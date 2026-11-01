"""Consent, suppression and unsubscribe.

Policy (documented in docs/EMAIL.md):
  * security / transactional / admin one-to-one mail is sent regardless of marketing consent
  * `service` mail (maintenance, account notices) is non-promotional: no consent needed, but hard-bounced/suppressed addresses are skipped
  * `marketing` mail needs an explicit opt-in (communication_preferences.marketing_opt_in=1), is never sent to a suppressed address, and always carries
    an unsubscribe link plus RFC 8058 one-click List-Unsubscribe headers
  * every consent change is appended to communication_consent_log (immutable)
"""
import hmac, hashlib, base64, re
from .. import util

def email_key(addr): return (addr or '').strip().lower()

def valid_address(addr):
    return bool(addr) and len(addr) <= 254 and not re.search(r'[\s<>,;\r\n]', addr) and re.fullmatch(r'[^@]{1,64}@[^@]+\.[^@]{2,}', addr) is not None

# ---------------------------------------------------------------- suppression
def is_suppressed(db, addr, scope='all'):
    """scope 'marketing' is also blocked by an 'all' suppression."""
    scopes = ('all',) if scope == 'all' else ('all', 'marketing')
    q = ','.join('?' * len(scopes))
    r = db.execute(f"SELECT reason FROM email_suppressions WHERE email_key=? AND released_at IS NULL AND scope IN ({q}) LIMIT 1", (email_key(addr), *scopes)).fetchone()
    return r['reason'] if r else None

def add_suppression(db, addr, scope, reason, source, email_id=None, admin_id=None):
    cur = db.execute('INSERT OR IGNORE INTO email_suppressions(email_key,scope,reason,source,email_id,created_at,created_by) VALUES(?,?,?,?,?,?,?)',
                     (email_key(addr), scope, reason, source, email_id, util.now(), admin_id))
    return cur.rowcount == 1

def release_suppression(db, sid, admin_id, why):
    return db.execute('UPDATE email_suppressions SET released_at=?, released_by=?, release_reason=? WHERE id=? AND released_at IS NULL', (util.now(), admin_id, why, sid)).rowcount == 1

# ---------------------------------------------------------------- marketing consent
def marketing_opted_in(db, user_id):
    r = db.execute('SELECT marketing_opt_in FROM communication_preferences WHERE user_id=?', (user_id,)).fetchone()
    return bool(r and r['marketing_opt_in'])

def set_marketing(db, user_id, opt_in, source, ip=None):
    """Idempotent: writes the consent log only when the value actually changes."""
    cur = marketing_opted_in(db, user_id)
    if cur == bool(opt_in) and db.execute('SELECT 1 FROM communication_preferences WHERE user_id=?', (user_id,)).fetchone(): return False
    n = util.now()
    db.execute('INSERT INTO communication_preferences(user_id,marketing_opt_in,marketing_changed_at,marketing_source,updated_at) VALUES(?,?,?,?,?) '
               'ON CONFLICT(user_id) DO UPDATE SET marketing_opt_in=excluded.marketing_opt_in, marketing_changed_at=excluded.marketing_changed_at, marketing_source=excluded.marketing_source, updated_at=excluded.updated_at',
               (user_id, 1 if opt_in else 0, n, source, n))
    db.execute('INSERT INTO communication_consent_log(user_id,channel,action,source,ip,created_at) VALUES(?,?,?,?,?,?)', (user_id, 'email_marketing', 'opt_in' if opt_in else 'opt_out', source, ip, n))
    return True

def get_timezone(db, user_id, default='UTC'):
    r = db.execute('SELECT timezone FROM communication_preferences WHERE user_id=?', (user_id,)).fetchone()
    return (r['timezone'] if r and r['timezone'] else None) or default

def set_timezone(db, user_id, tz):
    import zoneinfo
    try: zoneinfo.ZoneInfo(tz)
    except Exception: raise ValueError('unknown time zone')
    db.execute('INSERT INTO communication_preferences(user_id,timezone,updated_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET timezone=excluded.timezone, updated_at=excluded.updated_at', (user_id, tz, util.now()))

def notification_pref_on(db, user_id, ntype):
    """Optional (pref_gated) emails follow the customer's existing per-type notification preference. They are opt-in: no saved preference means no email."""
    r = db.execute('SELECT email FROM notification_preferences WHERE user_id=? AND type=?', (user_id, ntype)).fetchone()
    return bool(r and r['email'])

# ---------------------------------------------------------------- unsubscribe tokens (stateless, HMAC; do not expire so old emails keep working)
def _sig(secret, payload): return hmac.new(secret.encode(), b'vaulta-unsub:' + payload, hashlib.sha256).digest()[:16]
def make_unsub_token(secret, user_id):
    p = f'{int(user_id)}'.encode()
    return base64.urlsafe_b64encode(p).decode().rstrip('=') + '.' + base64.urlsafe_b64encode(_sig(secret, p)).decode().rstrip('=')

def parse_unsub_token(secret, token):
    try:
        a, b = (token or '').split('.', 1)
        pad = lambda s: s + '=' * (-len(s) % 4)
        p = base64.urlsafe_b64decode(pad(a)); sig = base64.urlsafe_b64decode(pad(b))
        if hmac.compare_digest(sig, _sig(secret, p)): return int(p.decode())
    except Exception: pass
    return None
