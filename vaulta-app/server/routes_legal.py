"""Legal documents API.
Public:  GET /api/legal/documents, /api/legal/documents/<slug>[?version=N], /api/legal/current, POST /api/legal/cookie-consent
Users:   GET /api/legal/risk-status, POST /api/legal/risk-acknowledgement, GET /api/legal/my-acceptances
Admin:   /api/admin/legal/...   (legal.read / legal.manage / legal.publish enforced server-side on every route)"""
from flask import Blueprint, request, g, current_app
from . import util, security as sec, legal
from .util import ApiError
from .db import get_db, one, all_
from .routes_auth import body

bp = Blueprint('legal', __name__, url_prefix='/api/legal')
admin = Blueprint('legal_admin', __name__, url_prefix='/api/admin/legal')

# ---------------------------------------------------------------- public
@bp.get('/documents')
def documents(): return {'documents': legal.public_list(get_db())}

@bp.get('/documents/<slug>')
def document(slug):
    ver = request.args.get('version')
    if ver is not None:
        if not ver.isdigit() or int(ver) > 10**6: raise ApiError(422, 'invalid_version', 'version must be a positive integer')
        ver = int(ver)
    return {'document': legal.public_view(get_db(), slug, ver)}

@bp.get('/current')
def current():
    slugs = [s for s in (request.args.get('slugs') or 'terms,privacy').split(',') if s]
    if not slugs or len(slugs) > 6 or any(s not in legal.CORE_SLUGS for s in slugs): raise ApiError(422, 'invalid_slugs', 'Unknown document.')
    return {'documents': legal.current_for_acceptance(get_db(), tuple(slugs))}

@bp.post('/cookie-consent')
@sec.rate_limit('cookie_consent', 30, 3600)
def cookie_consent():
    """Anonymous, JSON-only, minimal record. Exempt from the session CSRF header because it carries no authority and no account data."""
    b = body(); db = get_db()
    return legal.record_cookie_consent(db, b.get('consent_id'), b.get('preferences') or {}, b.get('action'))

# ---------------------------------------------------------------- signed-in users
@bp.get('/risk-status')
@sec.login_required
def risk_status(): return legal.risk_status(get_db(), g.user['id'])

@bp.post('/risk-acknowledgement')
@sec.login_required
@sec.rate_limit('risk_ack', 20, 3600)
def risk_ack():
    b = body(); ver = b.get('version')
    if b.get('acknowledged') is not True: raise ApiError(422, 'acknowledgement_required', 'You must affirmatively acknowledge the disclosure.')
    try: ver = int(ver) if ver is not None else None
    except (TypeError, ValueError): raise ApiError(422, 'invalid_version', 'Invalid version.')
    db = get_db(); out = legal.acknowledge_risk(db, g.user['id'], ver)      # user id comes from the session, never from the request
    sec.log_event(db, g.user['id'], 'legal_risk_acknowledged', f"v{out['version']}")
    return out

@bp.get('/my-acceptances')
@sec.login_required
def my_acceptances(): return {'acceptances': legal.my_acceptances(get_db(), g.user['id'])}

# ---------------------------------------------------------------- administration
def _staff(db, uid):
    r = one(db, 'SELECT u.public_id,u.email FROM users u WHERE u.id=?', uid) if uid else None
    return {'id': r['public_id'], 'email': r['email']} if r else None

def _ver_row(db, v, full=False):
    out = {'id': v['id'], 'version': v['version'], 'status': v['status'], 'title': v['title'], 'material': bool(v['material']), 'effective_at': v['effective_at'], 'published_at': v['published_at'],
           'change_note': v['change_note'], 'content_hash': v['content_hash'], 'created_at': v['created_at'], 'updated_at': v['updated_at'],
           'created_by': _staff(db, v['created_by']), 'updated_by': _staff(db, v['updated_by']), 'published_by': _staff(db, v['published_by'])}
    if full: out['content'] = v['content']; out['slug'] = v['slug']
    return out

@admin.get('')
@sec.require_perm('legal.read')
def admin_list():
    db = get_db(); cfg = legal.get_config(db); out = []
    for d in all_(db, 'SELECT * FROM legal_documents ORDER BY sort_order,slug'):
        pub = legal.published(db, d['id']); dr = one(db, "SELECT * FROM legal_versions WHERE document_id=? AND status='DRAFT'", d['id'])
        out.append({'slug': d['slug'], 'title': d['title'], 'type': d['doc_type'], 'region': d['region'], 'required': bool(d['required']),
                    'published': _ver_row(db, pub) if pub else None, 'draft': _ver_row(db, dr) if dr else None,
                    'versions': db.execute('SELECT COUNT(*) FROM legal_versions WHERE document_id=?', (d['id'],)).fetchone()[0],
                    'unresolved': legal.unresolved_tokens(pub['content'], cfg, pub['effective_at'], pub['published_at']) if pub else []})
    return {'documents': out}

@admin.get('/status')
@sec.require_perm('legal.read')
def admin_status(): return legal.readiness(get_db(), current_app.config.get('ENV', 'development'))

@admin.get('/config')
@sec.require_perm('legal.read')
def admin_config_get():
    return {'config': legal.get_config(get_db()), 'fields': {k: v[0] for k, v in legal.CONFIG_FIELDS.items()}}

@admin.put('/config')
@sec.require_perm('legal.manage')
def admin_config_put():
    b = body(); return {'config': legal.set_config(get_db(), g.user['id'], b.get('config'))}

@admin.post('/documents')
@sec.require_perm('legal.manage')
def admin_create_supplement():
    b = body(); db = get_db()
    slug = legal.create_supplement(db, g.user['id'], str(b.get('slug', '')).strip().lower(), b.get('title'), b.get('region'), b.get('content'))
    return {'slug': slug}, 201

@admin.get('/documents/<slug>/versions')
@sec.require_perm('legal.read')
def admin_versions(slug):
    db = get_db(); d = legal.doc_by_slug(db, slug)
    return {'document': {'slug': d['slug'], 'title': d['title'], 'required': bool(d['required'])}, 'versions': [_ver_row(db, v) for v in db.execute('SELECT * FROM legal_versions WHERE document_id=? ORDER BY version DESC', (d['id'],))]}

@admin.get('/versions/<int:vid>')
@sec.require_perm('legal.read')
def admin_version(vid):
    db = get_db(); v = legal.get_version(db, vid); r = legal.render_version(db, v)
    return {'version': _ver_row(db, v, True), 'preview': r}

@admin.post('/documents/<slug>/drafts')
@sec.require_perm('legal.manage')
def admin_draft(slug):
    return {'id': legal.create_draft(get_db(), g.user['id'], slug)}, 201

@admin.put('/versions/<int:vid>')
@sec.require_perm('legal.manage')
def admin_update(vid):
    db = get_db(); v = legal.update_draft(db, g.user['id'], vid, body()); return {'version': _ver_row(db, v, True)}

@admin.post('/preview')
@sec.require_perm('legal.read')
def admin_preview():
    b = body(); db = get_db(); content = str(b.get('content') or '')
    if len(content) > 300_000: raise ApiError(422, 'too_long', 'Content is too large.')
    cfg = legal.get_config(db); eff = legal._clean_effective(b.get('effective_at')) if b.get('effective_at') else util.now()
    h, toc = legal.render_markdown(legal.substitute(content, cfg, eff, util.now()))
    return {'html': h, 'toc': toc, 'unresolved': legal.unresolved_tokens(content, cfg, eff, util.now())}

@admin.post('/versions/<int:vid>/publish')
@sec.require_perm('legal.publish')
@sec.rate_limit('legal_publish', 30, 3600)
def admin_publish(vid):
    b = body(); db = get_db()
    v = legal.publish(db, g.user['id'], vid, b.get('confirm'), b.get('effective_at'), current_app.config.get('ENV', 'development'))
    return {'version': _ver_row(db, v)}

@admin.post('/versions/<int:vid>/archive')
@sec.require_perm('legal.manage')
def admin_archive(vid):
    legal.archive_version(get_db(), g.user['id'], vid); return {'ok': True}

@admin.get('/acceptances')
@sec.require_perm('legal.read', 'users.read')
def admin_acceptances():
    db = get_db(); p, n = util.page_args(request.args, 25, 100); where, args = [], []
    if request.args.get('slug'): where.append('d.slug=?'); args.append(request.args['slug'])
    if request.args.get('version', '').isdigit(): where.append('a.document_version=?'); args.append(int(request.args['version']))
    if request.args.get('type') in ('TERMS_ACCEPTANCE', 'PRIVACY_NOTICE_ACKNOWLEDGEMENT', 'RISK_DISCLOSURE_ACKNOWLEDGEMENT'): where.append('a.acceptance_type=?'); args.append(request.args['type'])
    w = (' WHERE ' + ' AND '.join(where)) if where else ''
    total = db.execute('SELECT COUNT(*) FROM legal_acceptances a JOIN legal_documents d ON d.id=a.document_id' + w, args).fetchone()[0]
    rows = all_(db, 'SELECT a.id,u.public_id user,u.email,d.slug,a.document_version version,a.acceptance_type type,a.accepted_at,substr(a.policy_hash,1,12) hash FROM legal_acceptances a JOIN users u ON u.id=a.user_id JOIN legal_documents d ON d.id=a.document_id' + w + ' ORDER BY a.id DESC LIMIT ? OFFSET ?', *args, n, (p - 1) * n)
    return {'acceptances': rows, 'total': total, 'page': p, 'size': n}
