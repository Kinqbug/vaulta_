import os, re
from flask import Flask, g, request, jsonify, send_from_directory, abort
from .config import Config
from . import db as dbm, security, util, rbac, accounting, providers as prov, legal as legal_mod
from .util import ApiError
from .mailer import templates_db as mailer_templates

def legal_mod_hosts():
    from . import marketdata
    return marketdata.IMAGE_HOSTS

def create_app(overrides=None):
    Config.validate()
    app = Flask(__name__, static_folder=None)
    app.config.from_object(Config)
    if overrides: app.config.update(overrides)
    app.config['MAX_CONTENT_LENGTH'] = Config.MAX_CONTENT_LENGTH
    app.providers = prov.Providers(app.config)
    from .mailer.outbox import Mailer
    app.mailer = Mailer(app.config, app.providers.email)
    app.teardown_appcontext(dbm.close_db)

    with app.app_context():
        c = dbm.connect(app.config['DATABASE_PATH']); dbm.init_schema(c); rbac.ensure_rbac(c); legal_mod.ensure_seed(c); mailer_templates.ensure_seed(c); accounting.ensure_chart(c); accounting.backfill(c)
        try:
            from . import crypto_convert; r = crypto_convert.convert_legacy(c)         # one-off, idempotent: customers' pre-existing crypto balances become Account Balance
            if r['converted'] or r['skipped']: app.logger.warning('crypto legacy conversion: %s', r)
        except Exception: app.logger.exception('crypto legacy conversion failed (will retry on next start)')
        from . import crypto_setup
        if app.config.get('ENV') == 'production':
            n = c.execute('SELECT COUNT(*) FROM users WHERE is_demo=1').fetchone()[0]
            if n: raise RuntimeError(f'Refusing to start in production: the database contains {n} demo account(s). Run `python -m server.prodcheck --demo-report` and follow docs/PRODUCTION.md.')
        crypto_setup.startup_checks(c, app.config.get('ENV', 'development'), app.config.get('CRYPTO_PROVIDER_MODE', 'mock')); crypto_setup.sync_chain(c, app.providers.chain) if app.providers.crypto_mode == 'mock' else None
        c.close()

    @app.before_request
    def load_user():
        g.user = g.session = None
        if not request.path.startswith('/api/'): return
        s = security.load_session(dbm.get_db())
        if s: g.session, g.user = s
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and not request.path.startswith('/api/webhooks/') and request.path != '/api/legal/cookie-consent' and request.path not in ('/api/email/unsubscribe', '/api/email/unsubscribe/one-click'):
            if g.session:
                tok = request.headers.get('X-CSRF-Token', '')
                import hmac
                if not hmac.compare_digest(tok, g.session['csrf_token']): raise ApiError(403, 'csrf_failed', 'Security check failed. Refresh the page and try again.')
            elif request.mimetype != 'application/json':
                raise ApiError(415, 'json_required', 'Content-Type must be application/json.')

    from . import platform_gate; platform_gate.install(app)

    @app.after_request
    def headers(r):
        r.headers['X-Content-Type-Options'] = 'nosniff'; r.headers['X-Frame-Options'] = 'DENY'
        r.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        r.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        r.headers.setdefault('Content-Security-Policy', ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                                                "font-src https://fonts.gstatic.com; img-src 'self' data: " + ' '.join('https://' + h for h in legal_mod_hosts()) + "; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"))
        if app.config['COOKIE_SECURE']: r.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        if request.path.startswith('/api/') and not ((request.path == '/api/profile/avatar' or re.fullmatch(r'/api/admin/users/[^/]+/avatar', request.path)) and request.method == 'GET' and r.status_code in (200, 304)): r.headers['Cache-Control'] = 'no-store'      # the avatar alone is cacheable (private, ETag + ?v= versioned URL)
        return r

    @app.errorhandler(ApiError)
    def api_error(e):
        return jsonify({'error': {'code': e.code, 'message': e.message, 'details': e.details}}), e.status
    @app.errorhandler(404)
    def nf(e):
        if request.path.startswith('/api/'): return jsonify({'error': {'code': 'not_found', 'message': 'Not found'}}), 404
        return send_from_directory(app.config['WEB_ROOT'], 'index.html'), 404
    @app.errorhandler(405)
    def mna(e): return jsonify({'error': {'code': 'method_not_allowed', 'message': 'Method not allowed'}}), 405
    @app.errorhandler(413)
    def big(e): return jsonify({'error': {'code': 'file_too_large', 'message': 'Upload too large'}}), 413
    @app.errorhandler(Exception)
    def boom(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException): return e
        app.logger.exception(e)
        return jsonify({'error': {'code': 'server_error', 'message': 'Something went wrong. No data was changed if you see this message.'}}), 500

    from . import routes_auth, routes_user, routes_admin, routes_public, routes_crypto, routes_legal, routes_terminal, routes_settings
    from .mailer import routes as mailer_routes
    for m in (routes_auth, routes_user, routes_admin, routes_public, routes_settings): app.register_blueprint(m.bp)
    from . import support_chat
    for b in (support_chat.cust, support_chat.staff, routes_crypto.bp, routes_crypto.admin, routes_legal.bp, routes_legal.admin, routes_terminal.bp, routes_terminal.chat, mailer_routes.admin, mailer_routes.public): app.register_blueprint(b)

    if app.config.get('ENV') == 'development' and app.providers.crypto_mode == 'mock': app.register_blueprint(routes_crypto.dev)    # simulator routes do not exist outside development
    else:
        @app.route('/api/dev/<path:p>', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
        def no_dev_routes(p): raise ApiError(404, 'not_found', 'Not found')

    @app.get('/api/health')
    def health():
        """Liveness + database reachability. Deliberately reveals nothing about configuration outside development."""
        try: ok = dbm.get_db().execute('SELECT 1').fetchone()[0] == 1
        except Exception: ok = False
        body = {'ok': ok}
        if app.config.get('ENV') == 'development': body.update(mode=app.providers.mode, env='development', crypto_mode=app.providers.crypto_mode)
        return body, (200 if ok else 503)

    @app.route('/', defaults={'p': ''})
    @app.route('/<path:p>')
    def static_site(p):
        root = app.config['WEB_ROOT']; full = os.path.realpath(os.path.join(root, p))
        if not full.startswith(os.path.realpath(root)) or any(part.startswith('.') for part in p.split('/')): abort(404)
        if os.path.isdir(full):
            if p and not p.endswith('/'): return ('', 301, {'Location': '/' + p + '/'})
            return send_from_directory(root, os.path.join(p, 'index.html'))
        return send_from_directory(root, p)
    return app
