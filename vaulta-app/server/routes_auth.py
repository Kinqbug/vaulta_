import secrets
from flask import Blueprint, request, jsonify, g, current_app
from . import util, security as sec, ledger, legal, rbac
from .util import ApiError, need, text
from .db import get_db, one, all_, tx, val
from .audit import audit
from .notify import notify
from .mailer import events as mail, prefs as mailprefs

bp = Blueprint('auth', __name__, url_prefix='/api/auth')
def body():
    b = request.get_json(silent=True)
    if not isinstance(b, dict): raise ApiError(400, 'bad_request', 'JSON object expected')
    return b

def public_user(db, u, with_perms=True):
    p = one(db, 'SELECT * FROM user_profiles WHERE user_id=?', u['id'])
    out = {'id': u['public_id'], 'email': u['email'], 'phone': u['phone'], 'status': u['status'], 'email_verified': bool(u['email_verified']), 'kyc_status': u['kyc_status'],
           'two_factor_enabled': bool(u['totp_enabled']), 'first_name': p['first_name'] if p else None, 'last_name': p['last_name'] if p else None,
           'created_at': u['created_at'], 'last_login_at': u['last_login_at'], 'is_demo': bool(u['is_demo']),
           'avatar_url': (f"/api/profile/avatar?v={p['avatar_version']}" if p and p['avatar_name'] else None),
           'theme': (lambda r: r['theme'] if r else 'light')(one(db, 'SELECT theme FROM user_settings WHERE user_id=?', u['id']))}
    if with_perms:
        out['permissions'] = sorted(sec.user_perms(db, u['id'])); out['roles'] = [r['name'] for r in db.execute('SELECT r.name FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE ur.user_id=?', (u['id'],))]
    return out

def issue_session(db, u, status=200):
    old = g.session['id'] if getattr(g, 'session', None) else None
    if old: db.execute('UPDATE sessions SET revoked_at=? WHERE id=?', (util.now(), old))      # rotate: never reuse a pre-login session
    token, csrf = sec.create_session(db, u['id'])
    resp = jsonify({'user': public_user(db, u), 'csrf_token': csrf, 'mode': current_app.providers.mode}); resp.status_code = status
    sec.set_cookie(resp, token); return resp

def make_token(db, user_id, kind, hours):
    tok = secrets.token_urlsafe(32)
    db.execute('INSERT INTO email_tokens(user_id,kind,token_hash,expires_at) VALUES(?,?,?,?)', (user_id, kind, sec.sha(tok), util.ts_after(hours=hours))); return tok
def use_token(db, tok, kind):
    r = one(db, 'SELECT * FROM email_tokens WHERE token_hash=? AND kind=?', sec.sha(tok or ''), kind)
    if not r or r['used_at'] or util.parse_ts(r['expires_at']) < util.utcnow(): raise ApiError(400, 'invalid_token', 'This link is invalid or has expired.')
    db.execute('UPDATE email_tokens SET used_at=? WHERE id=?', (util.now(), r['id'])); return r['user_id']

@bp.post('/register')
@sec.rate_limit('register', 8, 3600)
def register():
    b, db = body(), get_db()
    if not rbac.setting(db, 'registration_enabled'): raise ApiError(403, 'registration_closed', 'New registrations are currently closed.')
    need(b, 'email', 'password', 'first_name', 'last_name')
    email = text(b['email'], 'email', 254).lower()
    if not util.EMAIL_RE.match(email): raise ApiError(422, 'invalid_email', 'Enter a valid email address.')
    if b.get('accepted_terms') is not True: raise ApiError(422, 'terms_required', 'You must accept the Terms and Privacy Policy.')
    pw = str(b['password']); sec.check_password_policy(pw, email)
    first, last = text(b['first_name'], 'first_name', 60), text(b['last_name'], 'last_name', 60)
    with tx(db):
        if one(db, 'SELECT 1 FROM users WHERE email=?', email): raise ApiError(409, 'email_taken', 'An account with this email already exists.')
        cur = db.execute('INSERT INTO users(public_id,email,phone,password_hash,created_at) VALUES(?,?,?,?,?)', ('USR-' + secrets.token_hex(5).upper(), email, text(b.get('phone'), 'phone', 30, False) or None, sec.hash_password(pw), util.now()))
        uid = cur.lastrowid
        db.execute('INSERT INTO user_profiles(user_id,first_name,last_name) VALUES(?,?,?)', (uid, first, last))
        legal.accept_signup(db, uid, b.get('terms_version'), b.get('privacy_version'))      # records the exact Terms + Privacy versions shown; rolls the whole signup back if they changed
        db.execute('INSERT INTO portfolios(user_id,created_at) VALUES(?,?)', (uid, util.now())); db.execute('INSERT INTO kyc_profiles(user_id) VALUES(?)', (uid,))
        ledger.account_for(db, uid, 'cash'); ledger.account_for(db, uid, 'savings')
        tok = make_token(db, uid, 'verify_email', 48)
        mail.emit(db, 'user.registered', uid, uid)                                                         # queued atomically with the new account
        mail.emit(db, 'auth.verification_requested', uid, sec.sha(tok)[:16], token=tok, expires_in='48 hours')
        if b.get('marketing_opt_in') is True: mailprefs.set_marketing(db, uid, True, 'signup', sec.client_ip())   # opt-in only; never pre-ticked
        sec.log_event(db, uid, 'account_created')
        notify(db, uid, 'system', 'Welcome to VAULTA', 'Verify your email and complete identity verification to start moving money.', '#/settings')
        u = dict(one(db, 'SELECT * FROM users WHERE id=?', uid))
        return issue_session(db, u, 201)

@bp.post('/login')
@sec.rate_limit('login', 15, 300)
def login():
    b, db = body(), get_db(); need(b, 'email', 'password')
    email = str(b['email']).strip().lower()[:254]; pw = str(b['password'])[:200]
    cfg = current_app.config
    u = one(db, 'SELECT * FROM users WHERE email=?', email)
    if not u:
        sec.burn_time(pw); sec.log_event(db, None, 'login_failed', 'unknown email'); raise ApiError(401, 'invalid_credentials', 'Incorrect email or password.')
    if u['locked_until'] and util.parse_ts(u['locked_until']) > util.utcnow():
        sec.log_event(db, u['id'], 'login_blocked_locked'); raise ApiError(423, 'account_locked', 'Too many failed attempts. Try again later or reset your password.')
    ok = sec.verify_password(u['password_hash'], pw)
    if ok and u['totp_enabled']:
        if not b.get('code'): raise ApiError(401, 'two_factor_required', 'Enter your 2FA code.')
        ok = sec.verify_totp(db, dict(u), b.get('code'))
        if not ok: sec.log_event(db, u['id'], '2fa_failed')
    if not ok:
        n = u['failed_logins'] + 1; lock = util.ts_after(minutes=cfg['LOCKOUT_MINUTES']) if n >= cfg['MAX_FAILED_LOGINS'] else None
        db.execute('UPDATE users SET failed_logins=?,locked_until=COALESCE(?,locked_until) WHERE id=?', (0 if lock else n, lock, u['id']))
        sec.log_event(db, u['id'], 'account_locked' if lock else 'login_failed', f'attempt {n}')
        if lock:
            notify(db, u['id'], 'security', 'Account temporarily locked', 'Several failed sign-in attempts were detected.')
            mail.emit(db, 'security.suspicious_activity', u['id'], f'lock:{u["id"]}:{util.now()}', at=util.now(), ip=sec.client_ip())
        raise ApiError(401, 'invalid_credentials', 'Incorrect email or password.')
    if u['status'] in ('suspended', 'closed'): raise ApiError(403, 'account_unavailable', 'This account is unavailable. Contact support.')
    with tx(db):
        prev = db.execute("SELECT ip,user_agent FROM security_events WHERE user_id=? AND type='login_success'", (u['id'],)).fetchall()
        db.execute('UPDATE users SET failed_logins=0,locked_until=NULL,last_login_at=? WHERE id=?', (util.now(), u['id']))
        eid = sec.log_event(db, u['id'], 'login_success')
        if prev:                                    # the very first sign-in is not "new": there is nothing to compare with
            ev = 'security.new_device' if sec.ua() not in {p['user_agent'] for p in prev} else ('security.new_login' if sec.client_ip() not in {p['ip'] for p in prev} else None)
            if ev: mail.emit(db, ev, u['id'], eid, at=util.now(), ip=sec.client_ip(), user_agent=sec.ua())
        return issue_session(db, dict(one(db, 'SELECT * FROM users WHERE id=?', u['id'])))

@bp.post('/logout')
def logout():
    db = get_db()
    if g.session: db.execute('UPDATE sessions SET revoked_at=? WHERE id=?', (util.now(), g.session['id'])); sec.log_event(db, g.user['id'], 'logout')
    r = jsonify({'ok': True}); sec.clear_cookie(r); return r

@bp.get('/me')
def me():
    if not g.user: raise ApiError(401, 'unauthenticated', 'Please log in.')
    return jsonify({'user': public_user(get_db(), g.user), 'csrf_token': g.session['csrf_token'], 'mode': current_app.providers.mode})

@bp.post('/verify-email')
@sec.rate_limit('verify', 20, 3600)
def verify_email():
    db = get_db()
    with tx(db):
        uid = use_token(db, body().get('token'), 'verify_email')
        was = one(db, 'SELECT email_verified FROM users WHERE id=?', uid)['email_verified']
        db.execute('UPDATE users SET email_verified=1 WHERE id=?', (uid,)); sec.log_event(db, uid, 'email_verified')
        if not was: mail.emit(db, 'auth.email_verified', uid, uid, at=util.now())
        notify(db, uid, 'system', 'Email verified', 'Your email address is verified.')
    return {'ok': True}

@bp.post('/resend-verification')
@sec.login_required
@sec.rate_limit('resend', 5, 3600)
def resend():
    db = get_db()
    if g.user['email_verified']: return {'ok': True}
    with tx(db):
        if mail.recent(db, g.user['id'], 'auth.verification_requested', 2) or mail.recent(db, g.user['id'], 'auth.verification_requested', 60) >= 5: return {'ok': True}   # cooldown: no inbox flooding
        tok = make_token(db, g.user['id'], 'verify_email', 48)
        mail.emit(db, 'auth.verification_requested', g.user['id'], sec.sha(tok)[:16], token=tok, expires_in='48 hours')
    return {'ok': True}

@bp.post('/request-password-reset')
@sec.rate_limit('pwreset', 6, 3600)
def request_reset():
    db = get_db(); email = str(body().get('email', '')).strip().lower()
    u = one(db, 'SELECT * FROM users WHERE email=?', email)
    if u:
        with tx(db):
            if mail.recent(db, u['id'], 'security.password_reset_requested', 2) or mail.recent(db, u['id'], 'security.password_reset_requested', 60) >= 3:
                return {'ok': True, 'message': 'If that email is registered, a reset link has been sent.'}      # cooldown, identical response
            tok = make_token(db, u['id'], 'reset_password', 1)
            eid = sec.log_event(db, u['id'], 'password_reset_requested')
            mail.emit(db, 'security.password_reset_requested', u['id'], sec.sha(tok)[:16], token=tok, purpose='reset', expires_in='1 hour')
    else: sec.burn_time('x')
    return {'ok': True, 'message': 'If that email is registered, a reset link has been sent.'}      # same response either way

@bp.post('/reset-password')
@sec.rate_limit('pwreset2', 10, 3600)
def reset_password():
    b, db = body(), get_db(); need(b, 'token', 'password')
    with tx(db):
        uid = use_token(db, b['token'], 'reset_password'); u = one(db, 'SELECT * FROM users WHERE id=?', uid); sec.check_password_policy(str(b['password']), u['email'])
        db.execute('UPDATE users SET password_hash=?,failed_logins=0,locked_until=NULL WHERE id=?', (sec.hash_password(str(b['password'])), uid))
        sec.revoke_sessions(db, uid); eid = sec.log_event(db, uid, 'password_reset_completed')
        mail.emit(db, 'security.password_changed', uid, eid, at=util.now())
        notify(db, uid, 'security', 'Password changed', 'Your password was reset. All sessions were signed out.')
    return {'ok': True}

@bp.post('/change-password')
@sec.login_required
@sec.rate_limit('chpw', 10, 3600)
def change_password():
    b, db = body(), get_db(); need(b, 'current_password', 'new_password')
    if not sec.verify_password(g.user['password_hash'], str(b['current_password'])): raise ApiError(403, 'invalid_credentials', 'Current password is incorrect.')
    sec.check_password_policy(str(b['new_password']), g.user['email'])
    with tx(db):
        db.execute('UPDATE users SET password_hash=? WHERE id=?', (sec.hash_password(str(b['new_password'])), g.user['id']))
        sec.revoke_sessions(db, g.user['id'], except_id=g.session['id']); eid = sec.log_event(db, g.user['id'], 'password_changed')
        mail.emit(db, 'security.password_changed', g.user['id'], eid, at=util.now())
        notify(db, g.user['id'], 'security', 'Password changed', 'Other devices were signed out.')
    return {'ok': True}

@bp.post('/2fa/setup')
@sec.login_required
def twofa_setup():
    db = get_db()
    if g.user['totp_enabled']: raise ApiError(409, 'already_enabled', '2FA is already enabled.')
    secret = sec.new_totp_secret(); db.execute('UPDATE users SET totp_secret=? WHERE id=?', (secret, g.user['id']))
    return {'secret': secret, 'otpauth_uri': f"otpauth://totp/VAULTA:{g.user['email']}?secret={secret}&issuer=VAULTA"}

@bp.post('/2fa/enable')
@sec.login_required
@sec.rate_limit('2fa', 10, 600)
def twofa_enable():
    db = get_db(); u = dict(one(db, 'SELECT * FROM users WHERE id=?', g.user['id']))
    if not u['totp_secret'] or not sec.verify_totp(db, u, body().get('code')): raise ApiError(422, 'invalid_code', 'That code is not valid.')
    with tx(db):
        db.execute('UPDATE users SET totp_enabled=1 WHERE id=?', (u['id'],)); sec.revoke_sessions(db, u['id'], except_id=g.session['id'])
        eid = sec.log_event(db, u['id'], '2fa_enabled'); mail.emit(db, 'security.2fa_enabled', u['id'], eid, at=util.now()); notify(db, u['id'], 'security', 'Two-factor authentication enabled', 'Other devices were signed out.')
    return {'ok': True}

@bp.post('/2fa/disable')
@sec.login_required
@sec.rate_limit('2fa', 10, 600)
def twofa_disable():
    b, db = body(), get_db(); u = dict(one(db, 'SELECT * FROM users WHERE id=?', g.user['id']))
    if not (sec.verify_password(u['password_hash'], str(b.get('password', ''))) and sec.verify_totp(db, u, b.get('code'))): raise ApiError(403, 'invalid_credentials', 'Password or code is incorrect.')
    with tx(db):
        db.execute('UPDATE users SET totp_enabled=0,totp_secret=NULL WHERE id=?', (u['id'],)); eid = sec.log_event(db, u['id'], '2fa_disabled'); mail.emit(db, 'security.2fa_disabled', u['id'], eid, at=util.now()); notify(db, u['id'], 'security', 'Two-factor authentication disabled', '')
    return {'ok': True}

@bp.get('/sessions')
@sec.login_required
def sessions():
    rows = all_(get_db(), 'SELECT id,created_at,last_seen_at,expires_at,ip,user_agent FROM sessions WHERE user_id=? AND revoked_at IS NULL AND expires_at>? ORDER BY last_seen_at DESC', g.user['id'], util.now())
    for r in rows: r['current'] = r['id'] == g.session['id']
    return {'sessions': rows}

@bp.delete('/sessions/<int:sid>')
@sec.login_required
def revoke(sid):
    db = get_db()
    if db.execute('UPDATE sessions SET revoked_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL', (util.now(), sid, g.user['id'])).rowcount == 0: raise ApiError(404, 'not_found', 'Session not found')
    sec.log_event(db, g.user['id'], 'session_revoked', str(sid)); return {'ok': True}

@bp.get('/security-events')
@sec.login_required
def events():
    return {'events': all_(get_db(), 'SELECT type,ip,user_agent,detail,created_at FROM security_events WHERE user_id=? ORDER BY id DESC LIMIT 50', g.user['id'])}
