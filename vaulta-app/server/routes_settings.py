"""Profile settings: avatar, display preferences, channel availability, personal-data export, sign-out-other-devices.

Everything is scoped to the authenticated user taken from the server-side session (g.user); no user id is ever read from the request.
Passwords, hashes, TOTP secrets, session tokens and other users' data are never included in any response here."""
import json
from flask import Blueprint, request, jsonify, g, current_app, Response
from . import util, security as sec, uploads, ledger
from .util import ApiError
from .db import get_db, one, all_, tx, val
from .routes_auth import body, public_user

bp = Blueprint('settings', __name__, url_prefix='/api')
U = sec.login_required

LANGUAGES = [{'value': 'en', 'label': 'English'}]                      # the UI is English-only; add a value here only when translations exist
CURRENCIES = [{'value': 'USD', 'label': 'US dollar (USD)'}]            # all balances are held in USD; no exchange-rate source is connected
THEMES = [{'value': 'light', 'label': 'Light'}]                         # dark/system exist in the schema but are NOT offered: dashboard charts, the trade terminal and crypto pages hard-code light colours
AVATAR_MIN, AVATAR_MAX_SIDE = 128, 2048
AVATAR_FOLDER = 'avatars'

def settings_row(db, uid):
    r = one(db, 'SELECT language,currency,theme FROM user_settings WHERE user_id=?', uid)
    return dict(r) if r else {'language': 'en', 'currency': 'USD', 'theme': 'light'}

def avatar_url(profile):
    return f"/api/profile/avatar?v={profile['avatar_version']}" if profile and profile['avatar_name'] else None

# ---------------- display preferences ----------------
@bp.get('/settings/preferences')
@U
def prefs_get():
    db = get_db(); cfg = current_app.config
    live_email = cfg.get('EMAIL_PROVIDER') in ('resend', 'ses')
    return {'preferences': settings_row(db, g.user['id']),
            'options': {'language': LANGUAGES, 'currency': CURRENCIES, 'theme': THEMES},
            'avatar': {'max_bytes': cfg['AVATAR_MAX_BYTES'], 'types': ['image/jpeg', 'image/png', 'image/webp'], 'min_side': AVATAR_MIN},
            'channels': {'email': {'available': live_email, 'note': None if live_email else 'Email delivery is not connected in this environment, so email preferences are saved but nothing is sent yet.'},
                         'push': {'available': False, 'note': 'Push notifications are not available yet.'},
                         'sms': {'available': False, 'note': 'SMS notifications are not available yet.'}}}

@bp.put('/settings/preferences')
@U
def prefs_put():
    b, db = body(), get_db(); cur = settings_row(db, g.user['id']); new = dict(cur)
    for key, opts in (('language', LANGUAGES), ('currency', CURRENCIES), ('theme', THEMES)):
        if key in b:
            if b[key] not in {o['value'] for o in opts}: raise ApiError(422, 'invalid_value', f'{key} is not a supported option')
            new[key] = b[key]
    with tx(db):
        db.execute('INSERT INTO user_settings(user_id,language,currency,theme,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET language=excluded.language,currency=excluded.currency,theme=excluded.theme,updated_at=excluded.updated_at',
                   (g.user['id'], new['language'], new['currency'], new['theme'], util.now()))
    return {'preferences': new}

# ---------------- profile photo ----------------
def _root(): return current_app.config['UPLOAD_DIR']

@bp.post('/profile/avatar')
@U
@sec.rate_limit('avatar', 20, 3600)
def avatar_put():
    f = request.files.get('file'); limit = current_app.config['AVATAR_MAX_BYTES']
    if not f: raise ApiError(422, 'missing_file', 'Choose a photo to upload.')
    data = f.read(limit + 1)
    if not data: raise ApiError(422, 'invalid_image', 'The file is empty.')
    if len(data) > limit: raise ApiError(413, 'file_too_large', f'The photo must be {limit // (1024 * 1024)} MB or smaller.')
    mime, w, h = uploads.detect_raster(data)                    # real content decides the type: the filename and Content-Type are ignored
    if w != h: raise ApiError(422, 'not_square', 'The photo must be square. Use the crop tool to frame it.')
    if w < AVATAR_MIN or w > AVATAR_MAX_SIDE: raise ApiError(422, 'invalid_dimensions', f'The photo must be between {AVATAR_MIN} and {AVATAR_MAX_SIDE} pixels wide.')
    db = get_db(); uid = g.user['id']; name = None
    try:
        name, sha = uploads.store(_root(), AVATAR_FOLDER, data, mime)
        with tx(db):
            old = one(db, 'SELECT avatar_name FROM user_profiles WHERE user_id=?', uid)['avatar_name']
            db.execute('UPDATE user_profiles SET avatar_name=?,avatar_sha256=?,avatar_version=avatar_version+1 WHERE user_id=?', (name, sha, uid))
            sec.log_event(db, uid, 'avatar_changed')
    except BaseException:
        if name: uploads.remove(_root(), AVATAR_FOLDER, name)   # the previous photo is untouched on any failure
        raise
    if old and not val(db, 'SELECT COUNT(*) FROM user_profiles WHERE avatar_name=?', old): uploads.remove(_root(), AVATAR_FOLDER, old)
    return {'user': public_user(db, dict(one(db, 'SELECT * FROM users WHERE id=?', uid)), False)}, 201

@bp.delete('/profile/avatar')
@U
def avatar_delete():
    db = get_db(); uid = g.user['id']
    with tx(db):
        old = one(db, 'SELECT avatar_name FROM user_profiles WHERE user_id=?', uid)['avatar_name']
        db.execute('UPDATE user_profiles SET avatar_name=NULL,avatar_sha256=NULL,avatar_version=avatar_version+1 WHERE user_id=?', (uid,))
        if old: sec.log_event(db, uid, 'avatar_removed')
    if old and not val(db, 'SELECT COUNT(*) FROM user_profiles WHERE avatar_name=?', old): uploads.remove(_root(), AVATAR_FOLDER, old)
    return {'user': public_user(db, dict(one(db, 'SELECT * FROM users WHERE id=?', uid)), False)}

@bp.get('/profile/avatar')
@U
def avatar_get():
    p = one(get_db(), 'SELECT avatar_name,avatar_sha256 FROM user_profiles WHERE user_id=?', g.user['id'])
    if not p or not p['avatar_name']: raise ApiError(404, 'not_found', 'No profile photo')
    data = uploads.load(_root(), AVATAR_FOLDER, p['avatar_name'])
    mime = {'png': 'image/png', 'jpg': 'image/jpeg', 'webp': 'image/webp'}[p['avatar_name'].rsplit('.', 1)[1]]
    return uploads.file_response(data, mime, etag=p['avatar_sha256']).make_conditional(request)

# ---------------- other devices ----------------
@bp.post('/auth/sessions/revoke-others')
@U
@sec.rate_limit('revoke_others', 10, 3600)
def revoke_others():
    db = get_db(); uid = g.user['id']
    with tx(db):
        n = val(db, 'SELECT COUNT(*) FROM sessions WHERE user_id=? AND revoked_at IS NULL AND id!=?', uid, g.session['id'])
        sec.revoke_sessions(db, uid, except_id=g.session['id']); sec.log_event(db, uid, 'other_sessions_revoked', str(n))
    return {'ok': True, 'revoked': n}

# ---------------- personal data export ----------------
@bp.get('/settings/export')
@U
@sec.rate_limit('export', 5, 3600)
def export_data():
    from . import crypto_deposits as cd, legal
    from .mailer import prefs as mailprefs
    from .routes_user import kyc_state
    db = get_db(); uid = g.user['id']
    prof = dict(one(db, 'SELECT * FROM user_profiles WHERE user_id=?', uid))
    for k in ('user_id', 'avatar_name', 'avatar_sha256'): prof.pop(k, None)
    kyc = kyc_state(db, uid); kyc.pop('required', None)
    tickets = []
    for t in all_(db, 'SELECT id,ref,category,subject,status,created_at,updated_at FROM support_tickets WHERE user_id=? ORDER BY id', uid):
        tid = t.pop('id'); t['messages'] = all_(db, 'SELECT is_staff,body,created_at FROM support_messages WHERE ticket_id=? AND internal=0 ORDER BY id', tid); tickets.append(t)
    doc = {'generated_at': util.now(), 'format_version': 1,
           'account': public_user(db, g.user, False), 'profile': prof, 'display_preferences': settings_row(db, uid),
           'notification_preferences': all_(db, 'SELECT type,in_app,email FROM notification_preferences WHERE user_id=?', uid),
           'marketing_email_opt_in': bool(mailprefs.marketing_opted_in(db, uid)),
           'identity_verification': {'status': kyc['kyc']['status'], 'submitted_at': kyc['kyc']['submitted_at'], 'reviewed_at': kyc['kyc']['reviewed_at'], 'documents': [{'type': d['doc_type'], 'uploaded_at': d['created_at']} for d in kyc['documents']]},
           'legal_acceptances': legal.my_acceptances(db, uid),
           'security_events': all_(db, 'SELECT type,ip,user_agent,detail,created_at FROM security_events WHERE user_id=? ORDER BY id', uid),
           'active_sessions': all_(db, 'SELECT created_at,last_seen_at,expires_at,ip,user_agent FROM sessions WHERE user_id=? AND revoked_at IS NULL AND expires_at>?', uid, util.now()),
           'support_tickets': tickets,
           'transactions': [ledger.txn_view(r) for r in db.execute('SELECT * FROM transactions WHERE user_id=? ORDER BY id LIMIT 10000', (uid,))],
           'crypto_funding': [cd.as_transaction(i) for i in cd.funding_items(db, uid)]}
    sec.log_event(db, uid, 'data_exported')
    day = util.now()[:10]
    return Response(json.dumps(doc, indent=2, default=str), mimetype='application/json',
                    headers={'Content-Disposition': f'attachment; filename="vaulta-data-export-{day}.json"', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
