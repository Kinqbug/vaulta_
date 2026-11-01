"""Real SMTP delivery + the admin-managed SMTP configuration behind it.

Guarantees
  * `ok=True` means the SMTP server ACCEPTED the message (250 after DATA). It is never reported as delivered: SMTP gives no delivery proof.
  * The host is resolved and every address checked; the connection is pinned to the checked IP (no DNS-rebinding), private/loopback/link-local/
    metadata ranges are refused (SSRF), and only mail ports are allowed. Development may opt in to private hosts with SMTP_ALLOW_PRIVATE_HOSTS=1.
  * Certificates are always verified (default context). Plain-text transport is refused outside development, and credentials are never sent unencrypted.
  * If the connection dies while the message body is in flight the outcome is UNKNOWN: it is not retried automatically (SMTP has no idempotency key),
    so a customer is never mailed twice by a blind retry.
  * Passwords are decrypted only inside send/test, never logged, never returned.
"""
import os, re, ssl, socket, smtplib, ipaddress, email.utils
from email.message import EmailMessage
from email import policy
from .. import util, secretbox
from ..db import connect
from .transports import Result, Msg

ALLOWED_PORTS = {25, 465, 587, 2465, 2525, 2587}
SECURITY = ('starttls', 'ssl', 'none')
_HOST = re.compile(r'^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$')
_ADDR = re.compile(r'^[^@\s<>,;"\r\n]{1,64}@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')
_CTRL = re.compile(r'[\x00-\x1f\x7f]')
TIMEOUT = float(os.getenv('SMTP_TIMEOUT_SECONDS', '15'))

class SmtpConfigError(ValueError): pass

def _allow_private(): return os.getenv('SMTP_ALLOW_PRIVATE_HOSTS') == '1' and (os.getenv('APP_ENV') or os.getenv('VAULTA_ENV') or 'development') == 'development'
def _env(): return os.getenv('APP_ENV') or os.getenv('VAULTA_ENV') or 'development'

def _forbidden(ip):
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified or \
        (ip.version == 6 and ip.ipv4_mapped is not None and _forbidden(ip.ipv4_mapped))

def check_host_static(host):
    h = (host or '').strip().lower().rstrip('.')
    if not h: raise SmtpConfigError('SMTP host is required.')
    try: ip = ipaddress.ip_address(h.strip('[]'))
    except ValueError: ip = None
    if ip is not None:
        if _forbidden(ip) and not _allow_private(): raise SmtpConfigError('This SMTP host address is private, loopback or otherwise not allowed.')
        return h.strip('[]')
    if not _HOST.match(h): raise SmtpConfigError('SMTP host is not a valid hostname.')
    if h in ('localhost',) or h.endswith(('.localhost', '.local', '.internal', '.localdomain')) and not _allow_private():
        raise SmtpConfigError('This SMTP hostname is not allowed.')
    return h

def resolve_safe(host, port):
    """Resolve and return a vetted IP string. Raises SmtpConfigError if any resolved address is forbidden."""
    h = check_host_static(host)
    try: infos = socket.getaddrinfo(h, port, type=socket.SOCK_STREAM)
    except socket.gaierror: raise SmtpConfigError('SMTP host name could not be resolved.')
    ips = []
    for fam, _, _, _, sa in infos:
        ip = ipaddress.ip_address(sa[0].split('%')[0])
        if _forbidden(ip) and not _allow_private(): raise SmtpConfigError('SMTP host resolves to a private, loopback or link-local address, which is not allowed.')
        ips.append(str(ip))
    if not ips: raise SmtpConfigError('SMTP host name could not be resolved.')
    return ips[0]

def validate(d, *, existing=None):
    """Return a cleaned settings dict or raise SmtpConfigError. Does not touch the network."""
    out = {}
    out['enabled'] = 1 if d.get('enabled') else 0
    out['host'] = check_host_static(d.get('host', '')) if (d.get('host') or out['enabled']) else ''
    try: out['port'] = int(d.get('port') or 587)
    except (TypeError, ValueError): raise SmtpConfigError('SMTP port must be a number.')
    extra = {int(p) for p in os.getenv('SMTP_EXTRA_PORTS', '').split(',') if p.strip().isdigit()}
    if out['port'] not in ALLOWED_PORTS | extra: raise SmtpConfigError('SMTP port must be one of ' + ', '.join(map(str, sorted(ALLOWED_PORTS))) + '.')
    out['security'] = (d.get('security') or 'starttls').lower()
    if out['security'] not in SECURITY: raise SmtpConfigError('Security mode must be starttls, ssl or none.')
    if out['security'] == 'none' and _env() != 'development': raise SmtpConfigError('Unencrypted SMTP is only permitted in development.')
    out['auth_method'] = (d.get('auth_method') or 'login').lower()
    if out['auth_method'] not in ('login', 'none'): raise SmtpConfigError('Authentication method must be login or none.')
    out['username'] = (d.get('username') or '').strip()
    if _CTRL.search(out['username']) or len(out['username']) > 254: raise SmtpConfigError('SMTP username is invalid.')
    if out['auth_method'] == 'login' and out['enabled'] and not out['username']: raise SmtpConfigError('A username is required for login authentication.')
    if out['auth_method'] == 'login' and out['security'] == 'none': raise SmtpConfigError('Credentials may not be sent over an unencrypted connection.')
    fa = (d.get('from_address') or '').strip()
    if fa and not _ADDR.match(fa): raise SmtpConfigError('Sender email address is not valid.')
    if out['enabled'] and not fa: raise SmtpConfigError('A sender email address is required.')
    out['from_address'] = fa
    fn = (d.get('from_name') or '').strip()
    if _CTRL.search(fn) or len(fn) > 100 or re.search(r'[<>]', fn): raise SmtpConfigError('Sender name is invalid.')
    out['from_name'] = fn
    rt = (d.get('reply_to') or '').strip()
    if rt and not _ADDR.match(rt): raise SmtpConfigError('Reply-to address is not valid.')
    out['reply_to'] = rt
    pb = (d.get('public_base_url') or '').strip().rstrip('/')
    if pb and not re.match(r'^https?://[^\s/]+$', pb): raise SmtpConfigError('Public base URL must be an origin such as https://app.example.com (no path).')
    if pb and _env() == 'production' and not pb.startswith('https://'): raise SmtpConfigError('Public base URL must use https in production.')
    out['public_base_url'] = pb
    out['default_language'] = (d.get('default_language') or 'en').strip()[:10]
    tz = (d.get('default_timezone') or 'UTC').strip()[:64]
    if not re.fullmatch(r'[A-Za-z0-9_+\-/]+', tz): raise SmtpConfigError('Timezone is invalid.')
    out['default_timezone'] = tz
    return out

def mask_user(u):
    return '' if not u else (u[:2] + '***' + (('@' + u.split('@', 1)[1]) if '@' in u else ''))

def public_view(db):
    r = db.execute('SELECT * FROM email_smtp_settings WHERE id=1').fetchone()
    if not r: r = {'enabled': 0, 'host': '', 'port': 587, 'security': 'starttls', 'auth_method': 'login', 'username': '', 'password_enc': None, 'from_address': '', 'from_name': '', 'reply_to': '',
                   'public_base_url': '', 'default_language': 'en', 'default_timezone': 'UTC', 'updated_at': None, 'last_test_at': None, 'last_test_ok': None, 'last_test_error': None,
                   'last_send_ok_at': None, 'last_send_error_at': None, 'last_send_error': None}
    r = dict(r)
    return {'enabled': bool(r['enabled']), 'host': r['host'], 'port': r['port'], 'security': r['security'], 'auth_method': r['auth_method'], 'username_masked': mask_user(r['username']),
            'password_configured': bool(r['password_enc']), 'from_address': r['from_address'], 'from_name': r['from_name'], 'reply_to': r['reply_to'], 'public_base_url': r['public_base_url'],
            'default_language': r['default_language'], 'default_timezone': r['default_timezone'], 'updated_at': r['updated_at'],
            'last_test': {'at': r['last_test_at'], 'ok': None if r['last_test_ok'] is None else bool(r['last_test_ok']), 'error': r['last_test_error']},
            'last_send': {'ok_at': r['last_send_ok_at'], 'error_at': r['last_send_error_at'], 'error': r['last_send_error']},
            'encryption_key_configured': secretbox.available(), 'allowed_ports': sorted(ALLOWED_PORTS)}

def save(db, d, actor_id):
    """Persist validated settings. A blank/absent password keeps the stored one; clear_password=True removes it. Returns the non-secret before/after for auditing."""
    cur = db.execute('SELECT * FROM email_smtp_settings WHERE id=1').fetchone()
    c = validate(d)
    pw = d.get('password')
    enc = cur['password_enc'] if cur else None
    if d.get('clear_password'): enc = None
    if pw:
        if _CTRL.search(pw.replace('\t', '')) or len(pw) > 512: raise SmtpConfigError('SMTP password contains invalid characters.')
        try: enc = secretbox.encrypt(pw)
        except secretbox.SecretBoxError as e: raise SmtpConfigError(str(e))
    if c['auth_method'] == 'login' and c['enabled'] and not enc: raise SmtpConfigError('A password is required for login authentication.')
    if c['auth_method'] == 'none': enc = None
    before = {k: cur[k] for k in ('enabled', 'host', 'port', 'security', 'auth_method', 'from_address', 'reply_to')} if cur else None
    if not c['username'] and cur and c['auth_method'] == 'login': c['username'] = cur['username']
    cols = list(c) + ['password_enc', 'updated_by', 'updated_at']
    vals = [c[k] for k in c] + [enc, actor_id, util.now()]
    db.execute('INSERT INTO email_smtp_settings(id,' + ','.join(cols) + ') VALUES(1,' + ','.join('?' * len(cols)) + ') ON CONFLICT(id) DO UPDATE SET ' + ','.join(f'{k}=excluded.{k}' for k in cols), vals)
    after = {k: c[k] for k in ('enabled', 'host', 'port', 'security', 'auth_method', 'from_address', 'reply_to')}
    after['password_changed'] = bool(pw) or bool(d.get('clear_password'))
    return before, after

def runtime(db):
    """Settings including the decrypted password. Internal use only; never serialise this."""
    r = db.execute('SELECT * FROM email_smtp_settings WHERE id=1').fetchone()
    if not r: return None
    d = dict(r)
    d['password'] = secretbox.decrypt(r['password_enc']) if r['password_enc'] else ''
    return d

# ------------------------------------------------------------------ the wire protocol
def _smtp_class(security, ip, host):
    class _S(smtplib.SMTP_SSL if security == 'ssl' else smtplib.SMTP):
        def _get_socket(self, h, port, timeout):
            sock = socket.create_connection((ip, port), timeout)
            if security == 'ssl': return self.context.wrap_socket(sock, server_hostname=host)
            return sock
    return _S

def _connect(cfg):
    ip = resolve_safe(cfg['host'], cfg['port'])
    cls = _smtp_class(cfg['security'], ip, cfg['host'])
    ctx = ssl.create_default_context()
    kw = {'timeout': TIMEOUT}
    s = cls(**kw, context=ctx) if cfg['security'] == 'ssl' else cls(**kw)
    s._host = cfg['host']
    s.connect(cfg['host'], cfg['port'])
    s.ehlo()
    if cfg['security'] == 'starttls':
        if not s.has_extn('starttls'): s.close(); raise SmtpConfigError('The server does not offer STARTTLS; refusing to continue unencrypted.')
        s.starttls(context=ctx); s.ehlo()
    if cfg['auth_method'] == 'login' and cfg['username']:
        s.login(cfg['username'], cfg['password'])
    return s

def _classify(e):
    """-> (retryable, code, safe_message). Never includes credentials."""
    if isinstance(e, SmtpConfigError): return False, 'config', str(e)
    if isinstance(e, ssl.SSLCertVerificationError): return False, 'tls_verification_failed', 'TLS certificate could not be verified for this host.'
    if isinstance(e, ssl.SSLError): return True, 'tls_error', 'TLS handshake failed.'
    if isinstance(e, smtplib.SMTPAuthenticationError): return False, 'auth_failed', 'The SMTP server rejected the username/password.'
    if isinstance(e, smtplib.SMTPSenderRefused): return False, 'sender_refused', 'The server refused the sender address.'
    if isinstance(e, smtplib.SMTPRecipientsRefused): return False, 'recipient_refused', 'The server refused the recipient address.'
    if isinstance(e, smtplib.SMTPResponseException):
        txt = (e.smtp_error.decode('utf-8', 'replace') if isinstance(e.smtp_error, bytes) else str(e.smtp_error))[:160]
        return (400 <= e.smtp_code < 500), f'smtp_{e.smtp_code}', f'Server replied {e.smtp_code}: {txt}'
    if isinstance(e, (socket.timeout, TimeoutError)): return True, 'timeout', 'Connection to the SMTP server timed out.'
    if isinstance(e, (smtplib.SMTPServerDisconnected, ConnectionError, OSError)): return True, 'network', 'Could not reach the SMTP server (' + type(e).__name__ + ').'
    return True, 'smtp_error', type(e).__name__

def build_message(m, cfg_from=None):
    em = EmailMessage(policy=policy.SMTP)
    em['From'] = email.utils.formataddr((m.from_name, m.from_addr)) if m.from_name else m.from_addr
    em['To'] = m.to; em['Subject'] = m.subject
    if m.reply_to: em['Reply-To'] = m.reply_to
    em['Date'] = email.utils.formatdate(localtime=False)
    dom = m.from_addr.split('@', 1)[-1] or 'vaulta.invalid'
    em['Message-ID'] = email.utils.make_msgid(domain=dom)
    for k, v in (m.headers or {}).items():
        if re.search(r'[\r\n]', k + str(v)): raise ValueError('header injection attempt')
        em[k] = v
    em.set_content(m.text or ' ')
    if m.html: em.add_alternative(m.html, subtype='html')
    return em

class SmtpTransport:
    name, live, enforce_sandbox, supports_idempotency = 'smtp', True, True, False
    def __init__(self, db_path): self.db_path = db_path
    def _load(self):
        c = connect(self.db_path)
        try: return runtime(c)
        finally: c.close()
    def enabled(self):
        try: c = self._load()
        except Exception: return False
        return bool(c and c['enabled'] and c['host'])
    def identity(self):
        try: c = self._load()
        except Exception: c = None
        if not c: return {}
        return {'from_address': c['from_address'], 'from_name': c['from_name'], 'reply_to': c['reply_to'], 'public_base_url': c['public_base_url']}
    def _record(self, ok, err=None):
        try:
            c = connect(self.db_path)
            if ok: c.execute('UPDATE email_smtp_settings SET last_send_ok_at=? WHERE id=1', (util.now(),))
            else: c.execute('UPDATE email_smtp_settings SET last_send_error_at=?, last_send_error=? WHERE id=1', (util.now(), (err or '')[:300]))
            c.close()
        except Exception: pass
    def send(self, m):
        try: cfg = self._load()
        except secretbox.SecretBoxError as e: return Result(False, retryable=False, code='credential_unreadable', error=str(e))
        if not cfg or not cfg['enabled'] or not cfg['host']: return Result(False, retryable=True, code='not_configured', error='SMTP is not enabled/configured')
        phase = 'connect'; s = None
        try:
            em = build_message(m)
            s = _connect(cfg)
            phase = 'envelope'
            code, resp = s.mail(m.from_addr)
            if code >= 400: raise smtplib.SMTPSenderRefused(code, resp, m.from_addr)
            code, resp = s.rcpt(m.to)
            if code >= 400: raise smtplib.SMTPRecipientsRefused({m.to: (code, resp)})
            phase = 'data'
            code, resp = s.data(em.as_bytes())
            phase = 'done'
            if code != 250: raise smtplib.SMTPDataError(code, resp)
            try: s.quit()
            except Exception: pass
            self._record(True)
            return Result(True, message_id=em['Message-ID'].strip('<>'), http_status=code)
        except ValueError as e:
            return Result(False, retryable=False, code='invalid_message', error='message rejected before sending: ' + str(e)[:80])
        except Exception as e:
            retry, code, msg = _classify(e)
            if phase == 'data' and not isinstance(e, smtplib.SMTPResponseException):
                retry, code, msg = False, 'outcome_unknown', 'Connection was lost while the message was being sent; it may or may not have been accepted. Not retried automatically to avoid a duplicate. Check the mail server logs, then retry manually if needed.'
            self._record(False, f'{code}: {msg}')
            return Result(False, retryable=retry, code=code, error=msg)
        finally:
            if s is not None:
                try: s.close()
                except Exception: pass

def test_connection(db):
    """Real connect + TLS + auth, no message. Returns (ok, message, details). Records the outcome (without secrets)."""
    try: cfg = runtime(db)
    except secretbox.SecretBoxError as e: return False, str(e), {}
    if not cfg or not cfg['host']: return False, 'SMTP host is not configured.', {}
    steps = []
    try:
        ip = resolve_safe(cfg['host'], cfg['port']); steps.append('host resolved to an allowed address')
        s = _connect(cfg); steps.append('connected' + (' over implicit TLS' if cfg['security'] == 'ssl' else ' + STARTTLS' if cfg['security'] == 'starttls' else ' (unencrypted, development only)'))
        if cfg['auth_method'] == 'login' and cfg['username']: steps.append('authenticated')
        try: s.noop(); s.quit()
        except Exception: pass
        ok, msg = True, 'Connection, TLS and authentication succeeded. No email was sent.'
    except Exception as e:
        _, code, m = _classify(e); ok, msg = False, f'{m}'
        steps.append('failed: ' + code)
    db.execute('UPDATE email_smtp_settings SET last_test_at=?, last_test_ok=?, last_test_error=? WHERE id=1', (util.now(), 1 if ok else 0, None if ok else msg[:300]))
    return ok, msg, {'steps': steps}
