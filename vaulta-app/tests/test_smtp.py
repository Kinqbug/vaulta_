"""SMTP host configuration + real SMTP transport. A local fake SMTP server on 127.0.0.1 is used (no external network, no real email)."""
import os, sys, socketserver, threading, unittest, base64
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cryptography.fernet import Fernet
from email_base import EmailBase
from server import secretbox
from server.mailer import smtp as sm, outbox
from server.mailer.transports import Msg

A = '/api/admin/email/smtp'

class FakeSmtp(socketserver.ThreadingTCPServer):
    allow_reuse_address = True; daemon_threads = True
    def __init__(self, user='u', pw='p', drop_in_data=False, reject_rcpt=False):
        self.user, self.pw, self.drop, self.reject_rcpt, self.messages, self.logins = user, pw, drop_in_data, reject_rcpt, [], []
        super().__init__(('127.0.0.1', 0), _H)
        threading.Thread(target=self.serve_forever, daemon=True).start()
    @property
    def port(self): return self.server_address[1]

class _H(socketserver.StreamRequestHandler):
    def w(self, s): self.wfile.write((s + '\r\n').encode()); self.wfile.flush()
    def handle(self):
        S = self.server; self.w('220 fake ESMTP'); sender = None; rcpt = []
        while True:
            line = self.rfile.readline().decode(errors='replace').rstrip('\r\n')
            if not line: return
            c = line.upper()
            if c.startswith('EHLO'): self.wfile.write(b'250-fake\r\n250 AUTH PLAIN LOGIN\r\n'); self.wfile.flush()
            elif c.startswith('AUTH PLAIN'):
                parts = base64.b64decode(line.split()[2]).split(b'\0'); S.logins.append(parts[1:])
                self.w('235 ok' if parts[1:] == [S.user.encode(), S.pw.encode()] else '535 5.7.8 bad credentials')
            elif c.startswith('MAIL'): sender = line; self.w('250 ok')
            elif c.startswith('RCPT'):
                if S.reject_rcpt: self.w('550 no such user')
                else: rcpt.append(line); self.w('250 ok')
            elif c == 'DATA':
                self.w('354 go'); buf = b''
                while True:
                    l = self.rfile.readline()
                    if l in (b'.\r\n', b''): break
                    buf += l
                if S.drop: return          # connection dies before the final 250
                S.messages.append((sender, rcpt, buf.decode(errors='replace'))); self.w('250 2.0.0 queued as ABC123')
            elif c == 'NOOP': self.w('250 ok')
            elif c == 'QUIT': self.w('221 bye'); return
            else: self.w('502 unsupported')

def cfg(port=25, **kw):
    d = {'enabled': True, 'host': '127.0.0.1', 'port': port, 'security': 'none', 'auth_method': 'none', 'from_address': 'no-reply@mail.vaulta.example', 'from_name': 'VAULTA'}
    d.update(kw); return d

class SmtpBase(EmailBase):
    def setUp(self):
        super().setUp()
        self._env = {k: os.environ.get(k) for k in ('SETTINGS_ENCRYPTION_KEY', 'SMTP_ALLOW_PRIVATE_HOSTS', 'SMTP_EXTRA_PORTS')}
        os.environ['SETTINGS_ENCRYPTION_KEY'] = Fernet.generate_key().decode(); os.environ['SMTP_ALLOW_PRIVATE_HOSTS'] = '1'
        self.srv = FakeSmtp(); os.environ['SMTP_EXTRA_PORTS'] = str(self.srv.port)
        self.app.config.update(EMAIL_PROVIDER='smtp', EMAIL_SANDBOX_ALLOWLIST='@example.com')
        self.t = sm.SmtpTransport(self.app.config['DATABASE_PATH']); self.app.mailer = outbox.Mailer(self.app.config, self.t); self.m = self.app.mailer
        self.admin = self.staff('super_admin')
    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close()
        for k, v in self._env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
        super().tearDown()
    def put(self, **kw):
        kw.setdefault('port', self.srv.port); return self.req(self.admin, 'put', A, cfg(**kw))

class TestSmtpConfigApi(SmtpBase):
    def test_requires_permission_server_side(self):
        for role in ('support_admin', 'communications_admin', 'operations_admin'):
            c = self.staff(role)
            self.assertEqual(c.get(A).status_code, 403, role); self.assertEqual(self.req(c, 'put', A, cfg(25)).status_code, 403, role)
            self.assertEqual(self.req(c, 'post', A + '/test-connection').status_code, 403); self.assertEqual(self.req(c, 'post', A + '/send-test', {'to': 'a@example.com'}).status_code, 403)
        self.assertEqual(self.client().get(A).status_code, 401)
        self.assertEqual(self.admin.get(A).status_code, 200)

    def test_password_encrypted_never_returned_and_kept_when_blank(self):
        r = self.put(auth_method='login', username='mailer@example.com', password='S3cret-pass!', security='starttls'); self.assertEqual(r.status_code, 200, r.json)
        self.assertNotIn('S3cret', r.get_data(as_text=True)); self.assertTrue(r.json['settings']['password_configured']); self.assertEqual(r.json['settings']['username_masked'], 'ma***@example.com')
        raw = self.db.execute('SELECT * FROM email_smtp_settings').fetchone(); self.assertNotIn('S3cret', str(tuple(raw))); self.assertNotEqual(raw['password_enc'], 'S3cret-pass!')
        self.assertEqual(sm.runtime(self.db)['password'], 'S3cret-pass!')
        enc = raw['password_enc']
        r = self.put(auth_method='login', username='mailer@example.com', password='', security='starttls', from_name='Changed'); self.assertEqual(r.status_code, 200)
        self.assertEqual(self.db.execute('SELECT password_enc FROM email_smtp_settings').fetchone()[0], enc)         # unrelated save did not wipe the secret
        self.assertNotIn('S3cret', self.admin.get(A).get_data(as_text=True))
        r = self.put(auth_method='login', username='mailer@example.com', password='N3w-pass!', security='starttls'); self.assertNotEqual(self.db.execute('SELECT password_enc FROM email_smtp_settings').fetchone()[0], enc)
        audits = ' '.join(str(tuple(x)) for x in self.db.execute("SELECT * FROM audit_logs WHERE action LIKE 'email.smtp%'")); self.assertIn('email.smtp_updated', audits)
        self.assertNotIn('S3cret', audits); self.assertNotIn('N3w-pass', audits)

    def test_cannot_store_password_without_server_key(self):
        os.environ.pop('SETTINGS_ENCRYPTION_KEY')
        r = self.put(auth_method='login', username='u', password='x1y2z3', security='starttls'); self.assertEqual(r.status_code, 422); self.assertIn('SETTINGS_ENCRYPTION_KEY', r.json['error']['message'])
        self.assertIsNone(self.db.execute('SELECT 1 FROM email_smtp_settings').fetchone())

    def test_validation(self):
        bad = [dict(host='bad host!'), dict(host=''), dict(port=22), dict(port='abc'), dict(security='plain'), dict(from_address='not-an-email'), dict(from_address='a@b.com\r\nBcc: x@y.com'),
               dict(from_name='Evil\r\nBcc: x@y.com'), dict(reply_to='x'), dict(public_base_url='https://a.com/path'), dict(host='localhost'), dict(auth_method='login', security='none', username='u', password='p')]
        for kw in bad:
            r = self.put(**kw); self.assertEqual(r.status_code, 422, kw)
        os.environ['SMTP_ALLOW_PRIVATE_HOSTS'] = '0'
        for h in ('127.0.0.1', '10.0.0.5', '169.254.169.254', '192.168.1.1', '[::1]', '::ffff:127.0.0.1', 'localhost', 'db.internal'):
            self.assertEqual(self.put(host=h, port=587, security='starttls').status_code, 422, h)

    def test_unencrypted_refused_in_production(self):
        os.environ['APP_ENV'] = 'production'
        try:
            with self.assertRaises(sm.SmtpConfigError): sm.validate(cfg(587, security='none', host='mail.example.com'))
        finally: os.environ.pop('APP_ENV')

class TestSmtpTransport(SmtpBase):
    def test_connection_test_sends_no_email_and_records_outcome(self):
        self.put(); r = self.req(self.admin, 'post', A + '/test-connection'); self.assertTrue(r.json['ok'], r.json); self.assertEqual(self.srv.messages, [])
        self.assertTrue(self.admin.get(A).json['settings']['last_test']['ok'])
        self.srv.shutdown(); self.srv.server_close()
        self.put(); r = self.req(self.admin, 'post', A + '/test-connection'); self.assertFalse(r.json['ok']); self.assertFalse(self.admin.get(A).json['settings']['last_test']['ok'])
        self.srv = FakeSmtp()                                                                       # for tearDown

    def test_bad_credentials_reported_sanitised(self):
        srv = FakeSmtp(user='right', pw='right'); os.environ['SMTP_EXTRA_PORTS'] = str(srv.port)
        try:
            # plain AUTH needs a secure channel in cfg validation; exercise the transport layer directly with a runtime dict
            with self.assertRaises(sm.smtplib.SMTPAuthenticationError): sm._connect({'host': '127.0.0.1', 'port': srv.port, 'security': 'none', 'auth_method': 'login', 'username': 'wrong', 'password': 'TOPSECRET'})
            c, code, msg = sm._classify(sm.smtplib.SMTPAuthenticationError(535, b'5.7.8 bad TOPSECRET')); self.assertEqual(code, 'auth_failed'); self.assertNotIn('TOPSECRET', msg); self.assertFalse(c)
        finally: srv.shutdown(); srv.server_close()

    def test_send_through_outbox_marks_accepted_not_delivered(self):
        self.put(reply_to='help@mail.vaulta.example'); self.signup('cust@example.com', verify=False); st = self.drain()
        r = self.rows(template='EMAIL_VERIFY_001')[0]
        self.assertEqual(r['status'], 'accepted'); self.assertEqual(r['provider'], 'smtp'); self.assertTrue(r['provider_message_id'])       # NOT 'delivered'
        sender, rcpt, raw = self.srv.messages[0]
        self.assertIn('no-reply@mail.vaulta.example', sender); self.assertIn('cust@example.com', rcpt[0])
        self.assertIn('From: VAULTA <no-reply@mail.vaulta.example>', raw); self.assertIn('Reply-To: help@mail.vaulta.example', raw); self.assertIn('Message-ID: <', raw)
        self.assertIn('text/plain', raw); self.assertIn('text/html', raw)
        self.assertEqual(self.drain()['processed'], 0)                                              # not re-sent

    def test_disabled_keeps_queue_untouched(self):
        self.put(enabled=False); self.signup('cust@example.com', verify=False); st = self.drain()
        self.assertTrue(st.get('paused')); self.assertEqual(self.srv.messages, []); self.assertEqual(self.rows(template='EMAIL_VERIFY_001')[0]['status'], 'queued')
        self.assertFalse(self.m.status()['sending_enabled'])

    def test_sandbox_blocks_unlisted_recipient(self):
        self.put(); self.signup('cust@elsewhere.org', verify=False); self.drain()
        self.assertEqual(self.rows(template='EMAIL_VERIFY_001')[0]['status'], 'blocked'); self.assertEqual(self.srv.messages, [])

    def test_recipient_refused_is_permanent_failure(self):
        self.srv.reject_rcpt = True; self.put(); self.signup('cust@example.com', verify=False); self.drain()
        r = self.rows(template='EMAIL_VERIFY_001')[0]; self.assertEqual(r['status'], 'failed'); self.assertIn('recipient_refused', r['last_error'])

    def test_connection_lost_during_data_is_not_auto_retried(self):
        self.srv.drop = True; self.put(); self.signup('cust@example.com', verify=False); self.drain()
        r = self.rows(template='EMAIL_VERIFY_001')[0]; self.assertEqual(r['status'], 'failed'); self.assertIn('outcome_unknown', r['last_error']); self.assertEqual(r['attempts'], 1)

    def test_unreachable_server_is_retryable(self):
        self.put(); self.srv.shutdown(); self.srv.server_close(); self.signup('cust@example.com', verify=False); self.drain()
        r = self.rows(template='EMAIL_VERIFY_001')[0]; self.assertEqual(r['status'], 'retry'); self.srv = FakeSmtp()

    def test_header_injection_blocked(self):
        with self.assertRaises(ValueError): Msg('a@example.com', 'x@example.com', 'n\r\nBcc: z@z.com', 's', '', '')
        m = Msg('a@example.com', 'x@example.com', 'n', 's', '<p>x</p>', 'x', headers={'X-A': 'v\r\nBcc: e@e.com'})
        with self.assertRaises(ValueError): sm.build_message(m)

    def test_send_test_endpoint(self):
        self.put()
        self.assertEqual(self.req(self.admin, 'post', A + '/send-test', {'to': ''}).status_code, 422)
        self.assertEqual(self.req(self.admin, 'post', A + '/send-test', {'to': 'a@example.com, b@example.com'}).status_code, 422)     # exactly one recipient
        r = self.req(self.admin, 'post', A + '/send-test', {'to': 'qa@example.com'}); self.assertEqual(r.status_code, 200, r.json); self.assertTrue(r.json['ok']); self.assertEqual(len(self.srv.messages), 1)
        self.assertIn('not proof', r.json['message']); self.assertEqual(self.db.execute("SELECT status FROM email_outbox WHERE id=?", (r.json['email_id'],)).fetchone()[0], 'accepted')
        r = self.req(self.admin, 'post', A + '/send-test', {'to': 'qa@notallowed.org'}); self.assertFalse(r.json['ok']); self.assertEqual(r.json['status'], 'blocked'); self.assertEqual(len(self.srv.messages), 1)
        self.srv.reject_rcpt = True; r = self.req(self.admin, 'post', A + '/send-test', {'to': 'qa@example.com'}); self.assertFalse(r.json['ok'])        # failure is reported, never claimed as success

    def test_send_test_requires_smtp_transport(self):
        self.put(); self.app.mailer = outbox.Mailer(self.app.config, self.mock)
        r = self.req(self.admin, 'post', A + '/send-test', {'to': 'qa@example.com'}); self.assertEqual(r.status_code, 409)

class TestSecretBox(unittest.TestCase):
    def test_roundtrip_rotation_and_wrong_key(self):
        old = os.environ.get('SETTINGS_ENCRYPTION_KEY'); k1, k2 = Fernet.generate_key().decode(), Fernet.generate_key().decode()
        try:
            os.environ['SETTINGS_ENCRYPTION_KEY'] = k1; tok = secretbox.encrypt('pw'); self.assertNotIn('pw', tok)
            os.environ['SETTINGS_ENCRYPTION_KEY'] = k2 + ',' + k1; self.assertEqual(secretbox.decrypt(tok), 'pw')           # rotated: new key first, old still decrypts
            os.environ['SETTINGS_ENCRYPTION_KEY'] = k2
            with self.assertRaises(secretbox.SecretBoxError): secretbox.decrypt(tok)
            os.environ.pop('SETTINGS_ENCRYPTION_KEY')
            with self.assertRaises(secretbox.SecretBoxError): secretbox.encrypt('x')
        finally:
            if old is None: os.environ.pop('SETTINGS_ENCRYPTION_KEY', None)
            else: os.environ['SETTINGS_ENCRYPTION_KEY'] = old

if __name__ == '__main__': unittest.main()
