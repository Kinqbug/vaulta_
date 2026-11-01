"""Platform-wide switches that change real behaviour (System Settings > General > Display Settings).

One source of truth: the existing `system_settings` table (edited through the audited admin settings API).
  maintenance_mode      1 = the API answers 503 to everyone except staff (accounts holding any admin permission).
  registration_enabled  0 = POST /api/auth/register answers 403 (enforced in the route, not in the UI).
  live_prices_enabled   0 = the market-data provider is never called; only the last cached data is served, marked stale (reason 'paused').
  require_2fa_all       1 = a signed-in account without 2FA can only reach the 2FA enrolment endpoints until it enables 2FA.
Emergency access: staff are never locked out by maintenance mode, login/logout/session endpoints always work, and
`python -m server.platform_cli maintenance off` (or any other switch) works on the server without signing in.
"""
import time, threading, collections
from flask import g, request, current_app
from . import security as sec, rbac
from .util import ApiError
from .db import get_db

_hits = collections.defaultdict(collections.deque); _lock = threading.Lock()      # per process, like security.rate_limit; use a shared store for multi-process deployments
FLAGS = ('maintenance_mode', 'registration_enabled', 'live_prices_enabled', 'require_2fa_all')

# reachable in every state: health, public status, sign-in/out, "who am I", cookie consent (needed on the maintenance screen), provider webhooks (signature-verified)
ALWAYS = {'/api/health', '/api/platform/status', '/api/auth/login', '/api/auth/logout', '/api/auth/me', '/api/legal/cookie-consent'}
TWOFA_ENROL = {'/api/auth/2fa/setup', '/api/auth/2fa/enable'}

def flag(db, key): return bool(rbac.setting(db, key))

def is_staff(db):
    if not getattr(g, 'user', None): return False
    if 'perms' not in g.__dict__: g.perms = sec.user_perms(db, g.user['id'])
    return bool(g.perms)

def status(db):
    return {'maintenance': flag(db, 'maintenance_mode'), 'registration_enabled': flag(db, 'registration_enabled'),
            'live_prices_enabled': flag(db, 'live_prices_enabled'), 'two_factor_required': flag(db, 'require_2fa_all'),
            'name': (db.execute("SELECT value FROM system_settings WHERE key='platform.name'").fetchone() or ['VAULTA'])[0],
            'support_email': (db.execute("SELECT value FROM system_settings WHERE key='platform.support_email'").fetchone() or [None])[0] or None}

def enforce():
    p = request.path
    if not p.startswith('/api/') or p in ALWAYS or p.startswith('/api/webhooks/'): return
    db = get_db()
    if flag(db, 'maintenance_mode') and not is_staff(db):
        raise ApiError(503, 'maintenance', 'VAULTA is undergoing maintenance. Please try again shortly.')
    u = getattr(g, 'user', None)
    if u and flag(db, 'require_2fa_all') and not u['totp_enabled'] and p not in TWOFA_ENROL:
        raise ApiError(403, 'two_factor_setup_required', 'Two-factor authentication is required for all accounts. Turn it on in Security settings to continue.')
    cap = rbac.setting(db, 'api_rate_limit_per_hour')
    if cap and current_app.config.get('RATE_LIMIT_ENABLED') and not is_staff(db):
        key = ('u', u['id']) if u else ('ip', sec.client_ip()); t = time.time()
        with _lock:
            q = _hits[key]
            while q and q[0] < t - 3600: q.popleft()
            if len(q) >= cap: raise ApiError(429, 'rate_limited', 'Too many requests. Please wait a while and try again.')
            q.append(t)

def install(app):
    from . import platform_settings; app.register_blueprint(platform_settings.bp)
    app.before_request(enforce)          # registered after load_user, so g.user is set
    @app.get('/api/platform/status')
    def platform_status(): return status(get_db())
