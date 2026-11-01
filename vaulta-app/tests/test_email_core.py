"""Email core: templates + rendering, transports (against a local fake provider), webhook verification, queue/retry/idempotency semantics."""
import os, sys, re, json, time, threading, unittest, base64
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from email_base import EmailBase, BASE, WH_SECRET, sign
from server import util, db as dbm
from server.mailer import catalog as cat, render as rnd, transports as tr, webhooks, outbox, prefs, templates_db as tdb, events
from server.mailer.transports import Result, MockTransport

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestCatalogAndRender(unittest.TestCase):
    def render(self, tid, ctx=None, **kw):
        t = cat.BY_ID[tid]; c = {**rnd.SAMPLE, **(ctx or {})}
        return rnd.render(t['subject'], t['spec'], c, secret={'action_url': BASE + '/verify-email/?token=T'}, optional=t['optional'], mail_class=t['mail_class'], base_url=BASE,
                          unsubscribe_url=BASE + '/unsubscribe/?t=x', **kw)

    def test_library_is_complete_and_unique(self):
        self.assertGreaterEqual(len(cat.CATALOG), 82)
        ids = [t['id'] for t in cat.CATALOG]; self.assertEqual(len(ids), len(set(ids)))
        per = {}
        for t in cat.CATALOG: per[t['category']] = per.get(t['category'], 0) + 1
        want = {'registration': 8, 'deposits': 7, 'withdrawals': 8, 'investments': 6, 'trading': 7, 'savings': 8, 'security': 10, 'account': 7, 'support': 5, 'announcements': 8, 'admin': 8}
        for k, n in want.items(): self.assertGreaterEqual(per[k], n, k)
        for must in ('WELCOME_001', 'EMAIL_VERIFY_001', 'EMAIL_VERIFIED_001', 'KYC_SUBMITTED_001', 'KYC_APPROVED_001', 'DEPOSIT_PENDING_001', 'DEPOSIT_CONFIRMED_001', 'WITHDRAWAL_REQUESTED_001', 'WITHDRAWAL_COMPLETED_001',
                     'INVESTMENT_CONFIRMED_001', 'TRADE_ORDER_PLACED_001', 'TRADE_ORDER_FILLED_001', 'SAVINGS_GOAL_001', 'SECURITY_NEW_LOGIN_001', 'SECURITY_PASSWORD_CHANGED_001', 'SECURITY_WALLET_CHANGED_001',
                     'SUPPORT_TICKET_CREATED_001', 'ANNOUNCEMENT_GENERAL_001'): self.assertIn(must, ids)

    def test_every_template_renders_html_and_plain_text_with_email_safe_markup(self):
        for t in cat.CATALOG:
            r = self.render(t['id'])
            self.assertTrue(r.text.strip(), t['id']); self.assertIn('VAULTA', r.text)
            h = r.html.lower()
            self.assertIn('<table', h); self.assertIn('max-width:600px', h.replace(' ', '')); self.assertIn('name="viewport"', h)
            for banned in ('<script', 'display:flex', 'display:grid', 'display: flex', 'display: grid', '<link ', '@import', 'javascript:'): self.assertNotIn(banned, h, (t['id'], banned))
            self.assertIn('@media', h)                                   # mobile enhancement exists...
            self.assertTrue(r.subject and '\n' not in r.subject)
            self.assertNotRegex(r.html, r'\{\{|\}\}'); self.assertNotRegex(r.text, r'\{\{|\}\}')            # no leaked placeholders

    def test_brand_rules_black_white_gray_only(self):
        for t in cat.CATALOG[:20]:
            h = self.render(t['id']).html
            for colour in re.findall(r'#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b', re.sub(r'&#\d+;', '', h)):
                c = colour.lstrip('#').lower(); c = ''.join(x * 2 for x in c) if len(c) == 3 else c
                self.assertEqual(c[0:2], c[2:4], (t['id'], colour)); self.assertEqual(c[2:4], c[4:6], (t['id'], colour))     # r==g==b: strictly grayscale; no orange/green/anything

    def test_logo_and_buttons_follow_reference(self):
        h = self.render('DEPOSIT_CONFIRMED_001').html
        self.assertIn(BASE + '/assets/brand/vaulta-logo-black.png', h)                 # official asset, absolute https URL
        self.assertTrue(os.path.exists(os.path.join(ROOT, 'web', 'assets', 'brand', 'vaulta-logo-black.png')))
        self.assertEqual(h.count('class="btn"'), 1)                                      # exactly one primary call to action
        self.assertIn('border-radius:999px', h); self.assertIn('#ffffff', h); self.assertIn('background-color:#1c1c1c', h)       # pill button, white text, dark outer background
        self.assertIn('background-color:#f5f5f5', h)                                     # light centred card

    def test_html_is_escaped_never_executed(self):
        evil = '<img src=x onerror=alert(1)><script>alert(2)</script>'
        r = self.render('ANNOUNCEMENT_GENERAL_001', {'first_name': evil, 'message_body': evil + '\n\n<b>second</b>'})
        self.assertNotIn('<script>alert', r.html); self.assertNotIn('<img src=x', r.html); self.assertNotIn('<b>second</b>', r.html)
        self.assertIn('&lt;script&gt;', r.html); self.assertEqual(r.html.count('<p style="margin:0 0 14px 0;">'), 3)           # greeting + two paragraphs
        r2 = self.render('SUPPORT_REPLY_001', {'ticket_reference': 'TKT-1\r\nBcc: attacker@evil.test'})
        self.assertNotIn('\n', r2.subject); self.assertNotIn('\r', r2.subject)

    def test_missing_required_value_blocks_render_and_nothing_is_invented(self):
        t = cat.BY_ID['DEPOSIT_CONFIRMED_001']
        for drop in ('amount', 'transaction_reference', 'asset_symbol', 'transaction_date'):
            ctx = {k: v for k, v in rnd.SAMPLE.items() if k != drop}
            with self.assertRaises(rnd.RenderError) as cm: rnd.render(t['subject'], t['spec'], ctx, base_url=BASE)
            self.assertIn(drop, cm.exception.variables)
        with self.assertRaises(rnd.RenderError): rnd.render(t['subject'], t['spec'], {**rnd.SAMPLE, 'amount': '   '}, base_url=BASE)      # blank is missing too
        with self.assertRaises(rnd.RenderError): rnd.render(t['subject'], t['spec'], dict(rnd.SAMPLE), base_url='')                       # no trusted base URL -> no links -> no email

    def test_optional_values_drop_their_rows_instead_of_inventing_text(self):
        no_reason = {k: v for k, v in rnd.SAMPLE.items() if k != 'reason'}
        t = cat.BY_ID['DEPOSIT_FAILED_001']
        r = rnd.render(t['subject'], t['spec'], no_reason, optional=t['optional'], base_url=BASE, mail_class='transactional')
        self.assertNotIn('Reason', r.html); self.assertNotIn('Sample reason', r.html)

    def test_unsafe_template_content_rejected(self):
        t = cat.BY_ID['WELCOME_001']; spec = json.loads(json.dumps(t['spec']))
        for bad in ('{% for x in y %}', '{{ config }}', '{{ first_name.__class__ }}', '<script>x</script>', '{{unknown_var}}', 'javascript:alert(1)'):
            s = json.loads(json.dumps(spec)); s['intro'] = [bad]
            self.assertTrue(cat.spec_problems(t['subject'], s), bad)
        s = json.loads(json.dumps(spec)); s['cta'] = {'label': 'Go', 'url_var': 'first_name'}; self.assertTrue(cat.spec_problems(t['subject'], s))      # button must be a link variable

    def test_valid_spec_is_accepted_whatever_the_key_order(self):
        # regression: a spec whose last key is the nested `cta` object serialises to JSON ending in "}}" and was wrongly rejected as malformed
        t = cat.BY_ID['KYC_APPROVED_001']; spec = {k: v for k, v in t['spec'].items() if k != 'cta'}; spec['cta'] = t['spec']['cta']
        self.assertTrue(json.dumps(spec).endswith('}}')); self.assertEqual(cat.spec_problems(t['subject'], spec), [])
        spec['intro'] = ['Hi {{first_name}}, broken {{ placeholder']; self.assertTrue(cat.spec_problems(t['subject'], spec))

    def test_link_policy_for_custom_buttons(self):
        ok = lambda u, extra=(): rnd.link_ok(u, BASE, extra)
        self.assertTrue(ok(BASE + '/app/')); self.assertTrue(ok('https://docs.partner.test/x', ('docs.partner.test',)))
        for bad in ('javascript:alert(1)', 'http://app.vaulta.example/', 'https://evil.test/', 'https://app.vaulta.example@evil.test/', '//evil.test', 'data:text/html,x', 'https://app.vaulta.example/a b'): self.assertFalse(ok(bad), bad)

    def test_footer_unsubscribe_only_for_marketing_and_nothing_invented(self):
        self.assertIn('/unsubscribe/?t=x', self.render('NEWSLETTER_001').html)
        for tid in ('DEPOSIT_CONFIRMED_001', 'SECURITY_NEW_LOGIN_001', 'ANNOUNCEMENT_MAINTENANCE_001', 'ADMIN_CUSTOM_001'): self.assertNotIn('Unsubscribe', self.render(tid).html, tid)
        h = self.render('SECURITY_NEW_LOGIN_001').html; self.assertIn('never ask for your password', h)
        # no legal config supplied -> no address/entity/support email in the footer (never fabricated)
        self.assertNotRegex(h, r'mailto:'); self.assertNotIn('Registered', h)
        legal = {'legal_entity_name': 'Acme Ltd', 'registered_address': '1 Road', 'support_email': 'help@acme.test', 'website_url': 'https://acme.test'}
        t = cat.BY_ID['WELCOME_001']; r = rnd.render(t['subject'], t['spec'], rnd.SAMPLE, base_url=BASE, legal=legal)
        self.assertIn('Acme Ltd · 1 Road', r.html); self.assertIn('mailto:help@acme.test', r.html); self.assertIn('Acme Ltd', r.text)

    def test_wording_describes_the_real_state(self):
        def txt(tid): return self.render(tid).text.lower()
        for tid in ('DEPOSIT_REQUESTED_001', 'DEPOSIT_PENDING_001', 'DEPOSIT_DETECTED_001', 'DEPOSIT_REVIEW_001'):
            self.assertNotRegex(txt(tid), r'has been (confirmed|credited)|is now (confirmed|credited)|successfully|credited to your', tid)
        self.assertIn('not been credited yet', txt('DEPOSIT_DETECTED_001')); self.assertIn('not been paid out', txt('WITHDRAWAL_REQUESTED_001'))
        self.assertIn('approval is not the payout', txt('WITHDRAWAL_APPROVED_001')); self.assertIn('not complete yet', txt('WITHDRAWAL_PROCESSING_001'))
        self.assertIn('not been executed', txt('TRADE_ORDER_PLACED_001')); self.assertNotIn('executed in full', txt('TRADE_ORDER_PLACED_001'))
        self.assertIn('not an approval', txt('KYC_SUBMITTED_001')); self.assertNotRegex(txt('KYC_SUBMITTED_001'), r'has been approved|is now verified|identity is verified|you are verified')
        self.assertIn('not yet confirmed', txt('INVESTMENT_RECEIVED_001'))
        for t in cat.CATALOG:                                                      # no promised or guaranteed returns anywhere
            low = self.render(t['id']).text.lower()
            self.assertNotRegex(low, r'guarantee(d)? (return|profit|gain)|risk[- ]free|will earn|double your', t['id'])
        for tid in ('INVESTMENT_CONFIRMED_001', 'TRADE_ORDER_FILLED_001'): self.assertIn('risk', txt(tid))

    def test_secret_link_only_in_secret_slot_and_fallback_shown(self):
        r = self.render('EMAIL_VERIFY_001'); self.assertEqual(r.html.count(BASE + '/verify-email/?token=T') >= 2, True); self.assertIn('copy and paste', r.html)
        self.assertEqual(self.render('WELCOME_001').html.count('token='), 0)


class FakeProvider:
    """A local HTTP server that records requests and replies as scripted: stands in for Resend / SES."""
    def __init__(self):
        self.requests, self.replies = [], []
        outer = self
        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get('Content-Length', 0)); outer.requests.append({'path': self.path, 'headers': {k.lower(): v for k, v in self.headers.items()}, 'body': json.loads(self.rfile.read(n) or b'{}')})
                status, body = outer.replies.pop(0) if outer.replies else (200, {'id': 'res_123', 'MessageId': 'ses_123'})
                raw = json.dumps(body).encode(); self.send_response(status); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
            def log_message(self, *a): pass
        self.srv = HTTPServer(('127.0.0.1', 0), H); self.url = f'http://127.0.0.1:{self.srv.server_port}'
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
    def close(self): self.srv.shutdown(); self.srv.server_close()


class TestTransports(unittest.TestCase):
    def setUp(self): self.fp = FakeProvider(); self.addCleanup(self.fp.close)
    def msg(self): return tr.Msg('cust@example.com', 'no-reply@mail.vaulta.example', 'VAULTA', 'Subj', '<p>hi</p>', 'hi', reply_to='help@vaulta.example', headers={'List-Unsubscribe': '<https://x/u>'}, idem_key='k-1', tags={'email_id': 7})

    def test_resend_request_shape_and_success(self):
        t = tr.ResendTransport('re_test_key', self.fp.url); r = t.send(self.msg())
        self.assertTrue(r.ok); self.assertEqual(r.message_id, 'res_123'); q = self.fp.requests[0]
        self.assertEqual(q['path'], '/emails'); self.assertEqual(q['headers']['authorization'], 'Bearer re_test_key'); self.assertEqual(q['headers']['idempotency-key'], 'k-1')
        b = q['body']; self.assertEqual(b['from'], 'VAULTA <no-reply@mail.vaulta.example>'); self.assertEqual(b['to'], ['cust@example.com']); self.assertEqual(b['html'], '<p>hi</p>'); self.assertEqual(b['text'], 'hi')
        self.assertEqual(b['headers']['List-Unsubscribe'], '<https://x/u>'); self.assertEqual(b['reply_to'], 'help@vaulta.example')

    def test_resend_error_classification(self):
        t = tr.ResendTransport('k', self.fp.url)
        for status, retry in ((429, True), (500, True), (503, True), (422, False), (400, False), (401, False), (403, False)):
            self.fp.replies.append((status, {'name': 'x', 'message': 'boom'})); r = t.send(self.msg()); self.assertFalse(r.ok); self.assertEqual(r.retryable, retry, status)
        self.fp.replies.append((200, {})); r = t.send(self.msg()); self.assertFalse(r.ok); self.assertTrue(r.retryable)               # success without a message id is not trusted
        r = tr.ResendTransport('k', 'http://127.0.0.1:1').send(self.msg()); self.assertFalse(r.ok); self.assertTrue(r.retryable); self.assertEqual(r.code, 'network')

    def test_header_injection_refused_before_any_request(self):
        with self.assertRaises(ValueError): tr.Msg('a@b.com\r\nBcc: x@y.com', 'a@b.com', 'V', 'S', '', '')
        with self.assertRaises(ValueError): tr.Msg('a@b.com', 'a@b.com', 'V', 'Subj\nBcc: x@y.com', '', '')
        self.assertEqual(self.fp.requests, [])

    def test_sigv4_matches_the_published_aws_test_vector(self):
        auth, _ = tr.sigv4_authorization('GET', 'example.amazonaws.com', '/', '', {'host': 'example.amazonaws.com', 'x-amz-date': '20150830T123600Z'}, b'', 'us-east-1', 'service', 'AKIDEXAMPLE',
                                         'wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY', '20150830T123600Z')
        self.assertTrue(auth.endswith('Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31'))

    def test_ses_request_signed_and_parsed(self):
        t = tr.SesTransport('eu-west-1', 'AKIDTEST', 'secret', 'cfgset', self.fp.url); r = t.send(self.msg())
        self.assertTrue(r.ok); self.assertEqual(r.message_id, 'ses_123'); q = self.fp.requests[0]
        self.assertEqual(q['path'], '/v2/email/outbound-emails'); self.assertRegex(q['headers']['authorization'], r'^AWS4-HMAC-SHA256 Credential=AKIDTEST/\d{8}/eu-west-1/ses/aws4_request, SignedHeaders=content-type;host;x-amz-date, Signature=[0-9a-f]{64}$')
        self.assertEqual(q['body']['Destination'], {'ToAddresses': ['cust@example.com']}); self.assertEqual(q['body']['ConfigurationSetName'], 'cfgset'); self.assertFalse(t.supports_idempotency)
        self.fp.replies.append((400, {'__type': 'com.amazon#MessageRejected', 'message': 'bad'})); self.assertFalse(t.send(self.msg()).retryable)
        self.fp.replies.append((429, {'__type': 'ThrottlingException'})); self.assertTrue(t.send(self.msg()).retryable)

    def test_transport_selection_and_isolation(self):
        self.assertEqual(tr.build_transport({'EMAIL_PROVIDER': 'dev'}).name, 'dev'); self.assertFalse(tr.build_transport({}).live)
        with self.assertRaises(ValueError): tr.build_transport({'EMAIL_PROVIDER': 'resend'})                   # no key -> refuses, never silently falls back to "sending"
        with self.assertRaises(ValueError): tr.build_transport({'EMAIL_PROVIDER': 'mock', 'ENV': 'production'})
        with self.assertRaises(ValueError): tr.build_transport({'EMAIL_PROVIDER': 'ses'})
        self.assertEqual(tr.build_transport({'EMAIL_PROVIDER': 'resend', 'EMAIL_PROVIDER_KEY': 'k'}).name, 'resend')

    def test_dev_transport_never_touches_the_network(self):
        import urllib.request
        orig = urllib.request.urlopen; urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network used'))
        try: r = tr.DevTransport().send(self.msg())
        finally: urllib.request.urlopen = orig
        self.assertTrue(r.ok and r.captured and r.message_id is None)


class TestConfigGuards(unittest.TestCase):
    def cfg(self, **kw):
        from server.config import Config
        return type('C', (Config,), kw)
    def test_production_requires_real_provider_and_trusted_url(self):
        from server.config import email_env_problems as p
        base = dict(ENV='production', EMAIL_FROM_ADDRESS='no-reply@x.com', PUBLIC_BASE_URL='https://x.com', EMAIL_PROVIDER_KEY='k', EMAIL_WEBHOOK_SECRET='whsec_x')
        self.assertEqual(p(self.cfg(**base, EMAIL_PROVIDER='resend')), [])
        self.assertTrue(p(self.cfg(**{**base, 'EMAIL_PROVIDER': 'dev'})));  self.assertTrue(p(self.cfg(**{**base, 'EMAIL_PROVIDER': 'mock'})))
        self.assertTrue(p(self.cfg(**{**base, 'EMAIL_PROVIDER': 'resend', 'PUBLIC_BASE_URL': 'http://x.com'}))); self.assertTrue(p(self.cfg(**{**base, 'EMAIL_PROVIDER': 'resend', 'EMAIL_WEBHOOK_SECRET': ''})))
        self.assertTrue(p(self.cfg(**{**base, 'EMAIL_PROVIDER': 'resend', 'EMAIL_FROM_ADDRESS': ''}))); self.assertTrue(p(self.cfg(**{**base, 'EMAIL_PROVIDER': 'ses'})))
    def test_staging_with_real_provider_needs_allowlist(self):
        from server.config import email_env_problems as p
        self.assertTrue(p(self.cfg(ENV='staging', EMAIL_PROVIDER='resend'))); self.assertEqual(p(self.cfg(ENV='staging', EMAIL_PROVIDER='resend', EMAIL_SANDBOX_ALLOWLIST='qa@x.com')), [])
        self.assertTrue(p(self.cfg(ENV='staging', EMAIL_PROVIDER='mock')))


class TestWebhooks(EmailBase):
    def ev(self, mid, typ='email.delivered', **data): return {'type': typ, 'created_at': '2026-10-21T10:00:00Z', 'data': {'email_id': mid, **data}}

    def test_signature_required_and_verified(self):
        eid, mid = self.accepted_id(); c = self.client(); body = json.dumps(self.ev(mid)).encode()
        self.assertEqual(c.post('/api/webhooks/email/resend', data=body).status_code, 400)                                        # no signature headers
        h = sign(body); h['svix-signature'] = 'v1,' + base64.b64encode(b'x' * 32).decode(); self.assertEqual(c.post('/api/webhooks/email/resend', data=body, headers=h).status_code, 401)
        self.assertEqual(c.post('/api/webhooks/email/resend', data=body, headers=sign(body, secret='whsec_' + base64.b64encode(b'wrong-key-wrong-key').decode())).status_code, 401)
        self.assertEqual(c.post('/api/webhooks/email/resend', data=body, headers=sign(body, ts=time.time() - 4000)).status_code, 400)  # replayed/stale
        self.assertEqual(c.post('/api/webhooks/email/resend', data=body + b' ', headers=sign(body)).status_code, 401)             # body tampered after signing
        self.assertEqual(self.db.execute('SELECT status FROM email_outbox WHERE id=?', (eid,)).fetchone()[0], 'accepted')          # nothing above changed state

    def test_unconfigured_secret_refuses_everything(self):
        eid, mid = self.accepted_id(); self.app.config['EMAIL_WEBHOOK_SECRET'] = ''
        r = self.webhook(self.client(), self.ev(mid)); self.assertEqual(r.status_code, 503); self.assertEqual(self.db.execute('SELECT status FROM email_outbox WHERE id=?', (eid,)).fetchone()[0], 'accepted')

    def test_accepted_is_not_delivered_until_a_verified_event(self):
        eid, mid = self.accepted_id(); row = self.db.execute('SELECT status,delivered_at FROM email_outbox WHERE id=?', (eid,)).fetchone()
        self.assertEqual((row['status'], row['delivered_at']), ('accepted', None))
        r = self.webhook(self.client(), self.ev(mid), msg_id='evt_1'); self.assertEqual(r.status_code, 200); self.assertEqual(r.json['result'], 'applied')
        row = self.db.execute('SELECT status,delivered_at FROM email_outbox WHERE id=?', (eid,)).fetchone(); self.assertEqual(row['status'], 'delivered'); self.assertTrue(row['delivered_at'])

    def test_replay_is_idempotent_and_never_downgrades(self):
        eid, mid = self.accepted_id(); c = self.client()
        self.assertEqual(self.webhook(c, self.ev(mid), msg_id='evt_1').json['result'], 'applied'); self.assertEqual(self.webhook(c, self.ev(mid), msg_id='evt_1').json['result'], 'duplicate')
        self.webhook(c, self.ev(mid, 'email.delivery_delayed'), msg_id='evt_2')                                                      # late "delayed" after "delivered"
        self.assertEqual(self.db.execute('SELECT status FROM email_outbox WHERE id=?', (eid,)).fetchone()[0], 'delivered')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM email_events WHERE email_id=?', (eid,)).fetchone()[0], 2)

    def test_permanent_bounce_marks_bounced_and_suppresses_future_mail(self):
        eid, mid = self.accepted_id(email='bounce@example.com'); self.webhook(self.client(), self.ev(mid, 'email.bounced', bounce={'type': 'Permanent', 'message': 'mailbox does not exist'}), msg_id='evt_b')
        r = self.db.execute('SELECT status,last_error FROM email_outbox WHERE id=?', (eid,)).fetchone(); self.assertEqual(r['status'], 'bounced'); self.assertIn('mailbox', r['last_error'])
        self.assertEqual(prefs.is_suppressed(self.db, 'bounce@example.com'), 'hard_bounce')
        self.mock.sent.clear()      # new mail to the now-suppressed address:
        uid = self.uid('bounce@example.com'); self.assertIsNotNone(self.emit('auth.email_verified', uid, 'again'))
        self.drain(); self.assertEqual(self.mock.sent, []); self.assertEqual(self.rows(event='auth.email_verified')[-1]['status'], 'suppressed')

    def test_transient_bounce_is_only_delayed(self):
        eid, mid = self.accepted_id(); self.webhook(self.client(), self.ev(mid, 'email.bounced', bounce={'type': 'Transient'}), msg_id='t1')
        self.assertEqual(self.db.execute('SELECT status FROM email_outbox WHERE id=?', (eid,)).fetchone()[0], 'delayed'); self.assertIsNone(prefs.is_suppressed(self.db, 'a@example.com'))

    def test_marketing_complaint_withdraws_consent(self):
        self.signup(); uid = self.uid('a@example.com'); prefs.set_marketing(self.db, uid, True, 'test')
        eid, _ = outbox.enqueue(self.db, user_id=uid, to_email='a@example.com', template_id='NEWSLETTER_001', version=1, subject='s', event_type='campaign.broadcast', entity_ref='x', idem='mk1', mail_class='marketing',
                                ctx={'first_name': 'T', 'message_body': 'Hello'}); self.drain()
        mid = self.db.execute('SELECT provider_message_id FROM email_outbox WHERE id=?', (eid,)).fetchone()[0]
        self.webhook(self.client(), self.ev(mid, 'email.complained'), msg_id='c1')
        self.assertEqual(self.db.execute('SELECT status FROM email_outbox WHERE id=?', (eid,)).fetchone()[0], 'complained'); self.assertFalse(prefs.marketing_opted_in(self.db, uid)); self.assertEqual(prefs.is_suppressed(self.db, 'a@example.com', 'marketing'), 'complaint')

    def test_event_arriving_before_acceptance_is_reconciled(self):
        self.signup(); self.mock.plan.append(Result(True, message_id='early_1'))
        # the provider's webhook races our own write of the provider id
        r = self.webhook(self.client(), self.ev('early_1'), msg_id='race'); self.assertEqual(r.json['result'], 'unmatched')
        self.drain(); row = self.rows(template='EMAIL_VERIFY_001') or self.rows(template='WELCOME_001'); first = [x for x in self.rows() if x['provider_message_id'] == 'early_1'][0]
        self.assertEqual(first['status'], 'delivered')

    def test_opens_clicks_ignored_unless_tracking_enabled(self):
        eid, mid = self.accepted_id(); r = self.webhook(self.client(), self.ev(mid, 'email.opened'), msg_id='o1'); self.assertEqual(r.json['result'], 'ignored_tracking_disabled')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM email_events WHERE provider_event_id=?', ('o1',)).fetchone()[0], 0)


class TestQueueSemantics(EmailBase):
    def queue_one(self, key='k1', **kw):
        if not self.db.execute('SELECT 1 FROM users').fetchone(): self.signup(); self.db.execute('DELETE FROM email_outbox')       # registration mail is not what these tests are about
        uid = self.uid('a@example.com')
        d = dict(user_id=uid, to_email='a@example.com', template_id='SUPPORT_CLOSED_001', version=1, subject='s', event_type='t', entity_ref='r', idem=key, mail_class='transactional',
                 ctx={'first_name': 'T', 'ticket_reference': 'TKT-1', 'ticket_subject': 'S'}); d.update(kw); return outbox.enqueue(self.db, **d)

    def test_idempotent_enqueue(self):
        a, created = self.queue_one(); b, again = self.queue_one(); self.assertEqual(a, b); self.assertTrue(created); self.assertFalse(again)

    def test_success_records_provider_acceptance_not_delivery(self):
        eid, _ = self.queue_one(); self.drain(); r = self.db.execute('SELECT * FROM email_outbox WHERE id=?', (eid,)).fetchone()
        self.assertEqual(r['status'], 'accepted'); self.assertTrue(r['provider_message_id'].startswith('mock_')); self.assertIsNone(r['delivered_at']); self.assertEqual(r['attempts'], 1)
        self.assertEqual(self.db.execute("SELECT outcome FROM email_attempts WHERE email_id=?", (eid,)).fetchone()[0], 'accepted')

    def test_retry_with_exponential_backoff_then_success(self):
        eid, _ = self.queue_one(); self.mock.plan += [Result(False, retryable=True, code='429', error='slow down'), Result(False, retryable=True, code='503', error='down')]
        self.drain(); r = self.db.execute('SELECT * FROM email_outbox WHERE id=?', (eid,)).fetchone(); self.assertEqual((r['status'], r['attempts']), ('retry', 1)); self.assertIn('429', r['last_error'])
        first_due = r['next_attempt_at']; self.assertGreater(first_due, util.now())
        self.drain(); self.assertEqual(self.db.execute('SELECT attempts FROM email_outbox WHERE id=?', (eid,)).fetchone()[0], 1)         # not due yet: untouched
        self.drain(now=util.ts_after(minutes=2)); r = self.db.execute('SELECT * FROM email_outbox WHERE id=?', (eid,)).fetchone(); self.assertEqual((r['status'], r['attempts']), ('retry', 2))
        gap1 = util.parse_ts(first_due) - util.utcnow(); gap2 = util.parse_ts(r['next_attempt_at']) - util.utcnow(); self.assertGreater(gap2, gap1 * 1.5)       # backoff grows
        self.drain(now=util.ts_after(hours=1)); r = self.db.execute('SELECT * FROM email_outbox WHERE id=?', (eid,)).fetchone(); self.assertEqual((r['status'], r['attempts']), ('accepted', 3))
        self.assertEqual([x[0] for x in self.db.execute('SELECT outcome FROM email_attempts WHERE email_id=? ORDER BY id', (eid,))], ['retryable_error', 'retryable_error', 'accepted'])

    def test_retries_are_bounded(self):
        eid, _ = self.queue_one(max_attempts=3); self.mock.plan += [Result(False, retryable=True, code='503', error='down')] * 5
        for i in range(6): self.drain(now=util.ts_after(days=i + 1))
        r = self.db.execute('SELECT * FROM email_outbox WHERE id=?', (eid,)).fetchone(); self.assertEqual((r['status'], r['attempts']), ('failed', 3)); self.assertIn('retries exhausted', r['last_error']); self.assertEqual(len(self.mock.sent), 3)

    def test_permanent_failure_is_not_retried(self):
        eid, _ = self.queue_one(); self.mock.plan.append(Result(False, retryable=False, code='422', error='invalid recipient'))
        self.drain(); self.drain(now=util.ts_after(days=3)); r = self.db.execute('SELECT * FROM email_outbox WHERE id=?', (eid,)).fetchone()
        self.assertEqual((r['status'], r['attempts']), ('failed', 1)); self.assertIn('permanent', r['last_error']); self.assertEqual(len(self.mock.sent), 1)

    def test_two_workers_cannot_send_one_message_twice(self):
        eid, _ = self.queue_one(); self.assertTrue(self.m.deliver_one(self.db, eid)); self.assertFalse(self.m.deliver_one(self.db, eid)); self.assertEqual(len(self.mock.sent), 1)
        eid2, _ = self.queue_one('k2'); self.db.execute("UPDATE email_outbox SET status='sending', locked_until=? WHERE id=?", (util.ts_after(minutes=5), eid2))
        self.assertFalse(self.m.deliver_one(self.db, eid2))                                                    # someone else holds a live lease

    def test_interrupted_send_recovery_depends_on_provider_idempotency(self):
        eid, _ = self.queue_one(); self.db.execute("UPDATE email_outbox SET status='sending', attempts=1, locked_until=? WHERE id=?", (util.ts_after(minutes=-1), eid))
        self.drain(); self.assertEqual(self.db.execute('SELECT status FROM email_outbox WHERE id=?', (eid,)).fetchone()[0], 'accepted')          # idempotent provider: safe to re-send
        self.m.transport = type('NoIdem', (MockTransport,), {'supports_idempotency': False})(); eid2, _ = self.queue_one('k2')
        self.db.execute("UPDATE email_outbox SET status='sending', attempts=1, locked_until=? WHERE id=?", (util.ts_after(minutes=-1), eid2)); self.drain(); r = self.db.execute('SELECT status,last_error FROM email_outbox WHERE id=?', (eid2,)).fetchone()
        self.assertEqual(r['status'], 'failed'); self.assertIn('not re-sent', r['last_error']); self.assertEqual(self.m.transport.sent, [])        # never risks a duplicate

    def test_one_time_link_is_removed_from_the_database_once_sent(self):
        self.signup(); r = self.rows(template='EMAIL_VERIFY_001')[0]; self.assertIn('token', r['secret_json']); self.assertNotIn('token', r['context_json'] or ''); self.assertEqual(r['body'], '')
        self.drain(); r = self.db.execute('SELECT * FROM email_outbox WHERE id=?', (r['id'],)).fetchone(); self.assertIsNone(r['secret_json'])
        token = re.search(r'token=([\w-]+)', self.mock.sent[-1].text or self.mock.sent[0].text).group(1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM email_outbox WHERE context_json LIKE ? OR subject LIKE ? OR last_error LIKE ?', (f'%{token}%',) * 3).fetchone()[0], 0)

    def test_closed_account_and_bad_address_are_not_sent(self):
        eid, _ = self.queue_one(); self.db.execute("UPDATE users SET status='closed'"); self.drain(); self.assertEqual(self.db.execute('SELECT status FROM email_outbox WHERE id=?', (eid,)).fetchone()[0], 'suppressed')
        eid2, _ = self.queue_one('k2', to_email='not an address'); self.drain(); self.assertEqual(self.db.execute('SELECT status FROM email_outbox WHERE id=?', (eid2,)).fetchone()[0], 'failed'); self.assertEqual(self.mock.sent, [])

    def test_render_failure_is_recorded_and_never_sent(self):
        eid, _ = self.queue_one(ctx={'first_name': 'T'}); self.drain(); r = self.db.execute('SELECT * FROM email_outbox WHERE id=?', (eid,)).fetchone()
        self.assertEqual(r['status'], 'failed'); self.assertIn('ticket_reference', r['last_error']); self.assertEqual(self.mock.sent, [])

    def test_legacy_rows_can_never_be_delivered(self):
        self.db.execute("INSERT INTO email_outbox(user_id,to_email,subject,body,non_live,created_at) VALUES(NULL,'old@example.com','Verify','x',1,?)", (util.now(),))
        lid = self.db.execute("SELECT id FROM email_outbox WHERE to_email='old@example.com'").fetchone()[0]
        self.assertEqual(self.db.execute('SELECT status FROM email_outbox WHERE id=?', (lid,)).fetchone()[0], 'legacy_unsent'); self.drain(); self.assertEqual(self.mock.sent, [])
        with self.assertRaises(Exception): self.db.execute("UPDATE email_outbox SET status='queued' WHERE id=?", (lid,))
        with self.assertRaises(Exception): self.db.execute("UPDATE email_outbox SET status='bogus' WHERE id=?", (lid,))


class TestSandboxGuard(EmailBase):
    def setUp(self):
        super().setUp(); self.mock.enforce_sandbox = True                                  # behave like a real provider transport

    def test_non_production_real_provider_only_reaches_allowlist(self):
        self.app.config['EMAIL_SANDBOX_ALLOWLIST'] = 'qa@team.example, @vaulta-internal.test'
        self.signup('stranger@customer.com'); self.signup('qa@team.example'); self.signup('dev@vaulta-internal.test'); self.drain()
        sent_to = {m.to for m in self.mock.sent}; self.assertEqual(sent_to, {'qa@team.example', 'dev@vaulta-internal.test'})
        blocked = [r for r in self.rows(to='stranger@customer.com')]; self.assertTrue(blocked and all(r['status'] == 'blocked' for r in blocked))

    def test_no_allowlist_blocks_everything_outside_production(self):
        self.app.config['EMAIL_SANDBOX_ALLOWLIST'] = ''; self.signup(); self.drain(); self.assertEqual(self.mock.sent, []); self.assertTrue(all(r['status'] == 'blocked' for r in self.rows()))

    def test_simulated_money_never_leaves_even_in_production(self):
        self.app.config['ENV'] = 'production'; self.signup(); c = self.client(); uid = self.uid('a@example.com'); self.drain(); self.mock.sent.clear()
        self.db.execute("UPDATE users SET kyc_status='verified'"); outbox.enqueue(self.db, user_id=uid, to_email='a@example.com', template_id='SUPPORT_CLOSED_001', version=1, subject='s', event_type='t', entity_ref='r', idem='nl',
                                                                                  mail_class='transactional', ctx={'first_name': 'T', 'ticket_reference': 'X', 'ticket_subject': 'S'}, non_live=1)
        self.drain(); self.assertEqual(self.mock.sent, []); self.assertEqual(self.rows(event='t')[0]['status'], 'blocked')


class TestDevMode(EmailBase):
    def test_dev_transport_labels_and_captures_but_is_never_delivered(self):
        self.m.transport = tr.DevTransport(); self.signup(); self.drain(); r = self.rows(template='EMAIL_VERIFY_001')[0]
        self.assertEqual(r['status'], 'dev_captured'); self.assertNotEqual(r['status'], 'delivered'); self.assertIsNone(r['provider_message_id'])
        cap = self.db.execute('SELECT * FROM email_dev_capture WHERE email_id=?', (r['id'],)).fetchone(); self.assertIn('DEVELOPMENT EMAIL', cap['html']); self.assertIn('/verify-email/?token=', cap['text'])
        self.assertTrue(cap['subject'].startswith('[DEV]')); self.assertIsNone(self.db.execute('SELECT secret_json FROM email_outbox WHERE id=?', (r['id'],)).fetchone()[0])
        st = self.m.status(); self.assertFalse(st['live_delivery']); self.assertIn('NOT configured', st['warning'])

    def test_dev_capture_is_not_written_outside_development(self):
        self.m.transport = tr.DevTransport(); self.app.config['ENV'] = 'staging'; self.signup(); self.drain()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM email_dev_capture').fetchone()[0], 0)

    def test_dev_mailbox_endpoint_returns_followable_link(self):
        self.m.transport = tr.DevTransport(); c = self.signup(verify=False); self.drain(); r = c.get('/api/dev/last-email'); self.assertEqual(r.status_code, 200)
        self.assertRegex(r.json['email']['body'], r'/verify-email/\?token=[\w-]+')
        tok = re.search(r'token=([\w-]+)', r.json['email']['body']).group(1); self.assertEqual(self.req(c, 'post', '/api/auth/verify-email', {'token': tok}).status_code, 200)


if __name__ == '__main__':
    unittest.main()
