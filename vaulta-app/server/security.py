"""Authentication, sessions, CSRF, TOTP 2FA, rate limiting and RBAC enforcement."""
import hashlib, hmac, secrets, base64, struct, time, threading, collections, re
from functools import wraps
from flask import request, g, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from . import util
from .util import ApiError
from .db import one, all_, val

COOKIE = 'vaulta_sid'
_dummy = None
def hash_password(pw): return generate_password_hash(pw, method='scrypt')
def verify_password(h, pw):
    return check_password_hash(h, pw)
def burn_time(pw):
    """Constant-ish work for unknown emails so response timing does not reveal account existence."""
    global _dummy
    if _dummy is None: _dummy = hash_password('timing-equaliser')
    check_password_hash(_dummy, pw)

COMMON = {'password123', 'passw0rd123', 'qwerty12345', '1234567890', 'letmein1234', 'welcome1234', 'password1234'}
MIN_PASSWORD = 10
def check_password_policy(pw, email=''):
    if len(pw) < MIN_PASSWORD: raise ApiError(422, 'weak_password', f'Password must be at least {MIN_PASSWORD} characters.')
    if len(pw) > 128: raise ApiError(422, 'weak_password', 'Password is too long.')
    if not (re.search(r'[A-Za-z]', pw) and re.search(r'\d', pw)): raise ApiError(422, 'weak_password', 'Use letters and at least one number.')
    if pw.lower() in COMMON or (email and email.split('@')[0].lower() in pw.lower() and len(email.split('@')[0]) > 3):
        raise ApiError(422, 'weak_password', 'That password is too easy to guess.')

def sha(s): return hashlib.sha256(s.encode()).hexdigest()
def client_ip(): return request.remote_addr if request else None
def ua(): return (request.headers.get('User-Agent', '')[:200]) if request else None

def log_event(db, user_id, type_, detail=None):
    return db.execute('INSERT INTO security_events(user_id,type,ip,user_agent,detail,created_at) VALUES(?,?,?,?,?,?)',
                      (user_id, type_, client_ip(), ua(), detail, util.now())).lastrowid

# ---------------- sessions ----------------
def create_session(db, user_id):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    t = util.now()
    db.execute('INSERT INTO sessions(user_id,token_hash,csrf_token,created_at,last_seen_at,expires_at,ip,user_agent) VALUES(?,?,?,?,?,?,?,?)',
               (user_id, sha(token), csrf, t, t, util.ts_after(hours=current_app.config['SESSION_ABSOLUTE_HOURS']), client_ip(), ua()))
    return token, csrf

def revoke_sessions(db, user_id, except_id=None):
    db.execute('UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL AND id IS NOT ?', (util.now(), user_id, except_id))

def load_session(db):
    tok = request.cookies.get(COOKIE)
    if not tok: return None
    s = one(db, 'SELECT * FROM sessions WHERE token_hash=?', sha(tok))
    if not s or s['revoked_at']: return None
    now = util.utcnow()
    if util.parse_ts(s['expires_at']) < now: return None
    if (now - util.parse_ts(s['last_seen_at'])).total_seconds() > current_app.config['SESSION_IDLE_MINUTES'] * 60: return None
    u = one(db, 'SELECT * FROM users WHERE id=?', s['user_id'])
    if not u or u['status'] in ('suspended', 'closed'): return None
    if (now - util.parse_ts(s['last_seen_at'])).total_seconds() > 60:
        db.execute('UPDATE sessions SET last_seen_at=? WHERE id=?', (util.now(), s['id']))
    return dict(s), dict(u)

def set_cookie(resp, token):
    c = current_app.config
    resp.set_cookie(COOKIE, token, httponly=True, samesite='Lax', secure=c['COOKIE_SECURE'], max_age=c['SESSION_ABSOLUTE_HOURS'] * 3600, path='/')
def clear_cookie(resp): resp.delete_cookie(COOKIE, path='/')

def user_perms(db, user_id):
    return {r['code'] for r in db.execute(
        'SELECT DISTINCT p.code FROM user_roles ur JOIN role_permissions rp ON rp.role_id=ur.role_id JOIN permissions p ON p.id=rp.permission_id WHERE ur.user_id=?', (user_id,))}

# ---------------- decorators ----------------
def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not getattr(g, 'user', None): raise ApiError(401, 'unauthenticated', 'Please log in.')
        return f(*a, **k)
    return w

def require_perm(*codes):
    """Server-side RBAC: every listed permission must be held. Hiding UI is never the control."""
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            if not getattr(g, 'user', None): raise ApiError(401, 'unauthenticated', 'Please log in.')
            from .db import get_db
            if 'perms' not in g.__dict__: g.perms = user_perms(get_db(), g.user['id'])
            if not all(c in g.perms for c in codes):
                log_event(get_db(), g.user['id'], 'permission_denied', ','.join(codes) + ' ' + request.path)
                raise ApiError(403, 'forbidden', 'You do not have permission for this action.')
            return f(*a, **k)
        return w
    return deco

def has_perm(db, code):
    if 'perms' not in g.__dict__: g.perms = user_perms(db, g.user['id'])
    return code in g.perms

def require_staff(f):
    @wraps(f)
    def w(*a, **k):
        from .db import get_db
        if not getattr(g, 'user', None): raise ApiError(401, 'unauthenticated', 'Please log in.')
        if not user_perms(get_db(), g.user['id']): raise ApiError(403, 'forbidden', 'Staff access only.')
        return f(*a, **k)
    return w

# ---------------- rate limiting (in-memory; use Redis for multi-process deployments) ----------------
_hits = collections.defaultdict(collections.deque); _lock = threading.Lock()
def rate_limit(name, limit, seconds, key_fn=None):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            if current_app.config.get('RATE_LIMIT_ENABLED'):
                key = (name, client_ip(), key_fn() if key_fn else '')
                t = time.time()
                with _lock:
                    q = _hits[key]
                    while q and q[0] < t - seconds: q.popleft()
                    if len(q) >= limit: raise ApiError(429, 'rate_limited', 'Too many attempts. Please wait and try again.')
                    q.append(t)
            return f(*a, **k)
        return w
    return deco
def reset_rate_limits():
    with _lock: _hits.clear()

# ---------------- TOTP (RFC 6238) ----------------
def new_totp_secret(): return base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')
def _hotp(secret, counter, digits=6):
    key = base64.b32decode(secret + '=' * (-len(secret) % 8))
    h = hmac.new(key, struct.pack('>Q', counter), 'sha1').digest()
    o = h[-1] & 15
    return str((struct.unpack('>I', h[o:o + 4])[0] & 0x7fffffff) % 10 ** digits).zfill(digits)
def totp_code(secret, at=None): return _hotp(secret, int((at or time.time()) // 30))
def verify_totp(db, user, code):
    """Accepts +/-1 step; a step can only be used once (replay protection)."""
    code = str(code or '').strip().replace(' ', '')
    if not (user['totp_secret'] and re.fullmatch(r'\d{6}', code)): return False
    cur = int(time.time() // 30)
    for step in (cur - 1, cur, cur + 1):
        if step > user['totp_last_step'] and hmac.compare_digest(_hotp(user['totp_secret'], step), code):
            db.execute('UPDATE users SET totp_last_step=? WHERE id=?', (step, user['id'])); return True
    return False

def webhook_secret(): return hashlib.sha256((current_app.config['AUTH_SECRET'] + ':webhook').encode()).digest()
def sign_payload(body: bytes): return hmac.new(webhook_secret(), body, 'sha256').hexdigest()
