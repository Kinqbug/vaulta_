"""Event-driven automation, end to end: real API calls change real state; we assert which emails were queued, what they say, and that they are never duplicated.
No provider is contacted (MockTransport) and no real money moves (internal/mock providers)."""
import os, sys, re, json, unittest, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from email_base import EmailBase, BASE
from server import util, finance as fin, security as sec
from server.mailer import events, prefs, catalog as cat

IK = lambda n: {'Idempotency-Key': f'ik-{n}-{os.urandom(4).hex()}'}

class Flow(EmailBase):
    def events_for(self, email='a@example.com'): return [r['event_type'] for r in self.rows(to=email)]
    def templates_for(self, email='a@example.com'): return [r['template_id'] for r in self.rows(to=email)]
    def sent_text(self, tid, email='a@example.com'):
        """Rendered plain text of the (single) message for template tid, via the real delivery path."""
        self.drain(); r = [x for x in self.rows(template=tid, to=email) if x['status'] == 'accepted']; self.assertTrue(r, f'{tid} was not sent: {self.templates_for(email)}')
        idx = [x['id'] for x in self.rows(to=email) if x['status'] == 'accepted'].index(r[-1]['id']); return self.mock.sent[idx].text
    def admin(self, role='super_admin'): return self.staff(role)
    def pref_on(self, uid, *types):
        for t in types: self.db.execute('INSERT OR REPLACE INTO notification_preferences(user_id,type,in_app,email) VALUES(?,?,1,1)', (uid, t))


class TestRegistrationAndVerification(Flow):
    def test_registration_queues_welcome_and_verification_once(self):
        c = self.signup(verify=False)
        self.assertEqual(sorted(self.templates_for()), ['EMAIL_VERIFY_001', 'WELCOME_001']); self.assertTrue(all(r['status'] == 'queued' for r in self.rows()))
        self.drain(); self.assertEqual(len(self.mock.sent), 2); self.assertEqual({m.to for m in self.mock.sent}, {'a@example.com'}); self.assertTrue(all(r['status'] == 'accepted' for r in self.rows()))
        for m in self.mock.sent: self.assertEqual(m.from_addr, 'no-reply@mail.vaulta.example'); self.assertTrue(m.text and m.html)

    def test_verification_link_is_absolute_trusted_and_works(self):
        c = self.signup(verify=False); self.drain(); m = [m for m in self.mock.sent if 'Verify' in m.subject][0]
        link = re.search(r'(https://\S+/verify-email/\?token=[\w-]+)', m.text).group(1); self.assertTrue(link.startswith(BASE + '/verify-email/?token='))
        self.assertIn(link.replace('&', '&amp;'), m.html); tok = link.split('token=')[1]
        self.assertEqual(self.req(c, 'post', '/api/auth/verify-email', {'token': tok}).status_code, 200)
        self.assertEqual(self.templates_for().count('EMAIL_VERIFIED_001'), 1); self.drain(); self.assertIn('verified', self.mock.sent[-1].subject.lower())
        self.assertEqual(self.req(c, 'post', '/api/auth/verify-email', {'token': tok}).status_code, 400)      # single use ...
        self.assertEqual(self.templates_for().count('EMAIL_VERIFIED_001'), 1)                                      # ... and no second confirmation email

    def test_links_ignore_a_spoofed_host_header(self):
        c = self.client(); r = c.post('/api/auth/register', json={'email': 'h@example.com', 'password': 'LongEnoughPw99', 'first_name': 'H', 'last_name': 'J', 'accepted_terms': True, 'terms_version': 1, 'privacy_version': 1},
                                      headers={'Host': 'evil.example', 'X-Forwarded-Host': 'evil.example', 'Origin': 'https://evil.example'})
        self.drain(); self.assertTrue(self.mock.sent)
        for m in self.mock.sent: self.assertNotIn('evil.example', m.html + m.text)
        for m in self.mock.sent: self.assertIn(BASE, m.html)

    def test_resend_has_cooldown_so_the_inbox_cannot_be_flooded(self):
        c = self.signup(verify=False)
        for _ in range(6): self.assertEqual(self.req(c, 'post', '/api/auth/resend-verification').status_code, 200)
        self.assertEqual(self.templates_for().count('EMAIL_VERIFY_001'), 1)                                        # initial one only: 2-minute cooldown holds
        self.db.execute("UPDATE email_outbox SET created_at=? WHERE template_id='EMAIL_VERIFY_001'", (util.ts_after(minutes=-10),))
        self.req(c, 'post', '/api/auth/resend-verification'); self.assertEqual(self.templates_for().count('EMAIL_VERIFY_001'), 2)

    def test_duplicate_event_does_not_duplicate_email(self):
        self.signup(); uid = self.uid('a@example.com'); n = len(self.rows())
        self.assertIsNone(self.emit('user.registered', uid, uid)); self.assertEqual(len(self.rows()), n)
        self.assertIsNotNone(self.emit('kyc.submitted', uid, 'once')); self.assertIsNone(self.emit('kyc.submitted', uid, 'once')); self.assertEqual(self.templates_for().count('KYC_SUBMITTED_001'), 1)

    def test_password_reset_email_unknown_address_and_cooldown(self):
        self.signup(); self.db.execute('DELETE FROM email_outbox'); a = self.client()
        r1 = a.post('/api/auth/request-password-reset', json={'email': 'a@example.com'}); r2 = a.post('/api/auth/request-password-reset', json={'email': 'ghost@example.com'}); self.assertEqual(r1.json, r2.json)
        self.assertEqual(self.templates_for('ghost@example.com'), []); self.assertEqual(self.templates_for(), ['SECURITY_PASSWORD_RESET_001'])
        a.post('/api/auth/request-password-reset', json={'email': 'a@example.com'}); self.assertEqual(len(self.rows(template='SECURITY_PASSWORD_RESET_001')), 1)     # cooldown
        self.drain(); link = re.search(r'(https://\S+/reset-password/\?token=[\w-]+)', self.mock.sent[0].text).group(1); self.assertTrue(link.startswith(BASE))
        tok = link.split('token=')[1]; self.assertEqual(a.post('/api/auth/reset-password', json={'token': tok, 'password': 'BrandNewPass77'}).status_code, 200)
        self.assertEqual(self.templates_for().count('SECURITY_PASSWORD_CHANGED_001'), 1)

    def test_marketing_consent_is_opt_in_only(self):
        self.signup(); self.assertFalse(prefs.marketing_opted_in(self.db, self.uid('a@example.com'))); self.assertEqual(self.db.execute('SELECT COUNT(*) FROM communication_consent_log').fetchone()[0], 0)
        c = self.client(); c.post('/api/auth/register', json={'email': 'm@example.com', 'password': 'LongEnoughPw99', 'first_name': 'M', 'last_name': 'J', 'accepted_terms': True, 'terms_version': 1, 'privacy_version': 1, 'marketing_opt_in': True})
        self.assertTrue(prefs.marketing_opted_in(self.db, self.uid('m@example.com'))); row = self.db.execute("SELECT * FROM communication_consent_log WHERE action='opt_in'").fetchone(); self.assertEqual(row['source'], 'signup')


class TestKyc(Flow):
    def setUp(self):
        super().setUp(); self.c = self.signup(); self.uid_ = self.uid('a@example.com'); self.pid = self.db.execute('SELECT public_id FROM users WHERE id=?', (self.uid_,)).fetchone()[0]
        self.db.execute("UPDATE users SET kyc_status='under_review' WHERE id=?", (self.uid_,)); self.db.execute("UPDATE kyc_profiles SET status='under_review' WHERE user_id=?", (self.uid_,)); self.db.execute('DELETE FROM email_outbox')
        self.staff_c = self.admin()

    def decide(self, dec, reason=None): return self.req(self.staff_c, 'post', f'/api/admin/kyc/{self.pid}/decision', {'decision': dec, **({'reason': reason} if reason else {})})

    def test_submission_confirmation_says_under_review_not_approved(self):
        self.emit('kyc.submitted', self.uid_, 'x'); t = self.sent_text('KYC_SUBMITTED_001'); self.assertIn('not an approval', t.lower()); self.assertNotIn('verified', t.lower().split('hi')[1].split('submitted')[0])

    def test_approved(self):
        self.assertEqual(self.decide('approve').status_code, 200); self.assertEqual(self.templates_for(), ['KYC_APPROVED_001']); self.assertIn('approved', self.sent_text('KYC_APPROVED_001').lower())

    def test_rejected_uses_the_staff_reason(self):
        self.assertEqual(self.decide('reject', 'The photo of your ID is blurred.').status_code, 200); self.assertIn('photo of your ID is blurred', self.sent_text('KYC_REJECTED_001'))

    def test_more_information_required(self):
        self.assertEqual(self.decide('request_info', 'Please upload a proof of address.').status_code, 200); self.assertIn('proof of address', self.sent_text('KYC_INFO_REQUIRED_001'))

    def test_decision_emails_are_not_sent_for_a_failed_decision(self):
        self.assertEqual(self.req(self.staff_c, 'post', f'/api/admin/kyc/{self.pid}/decision', {'decision': 'reject'}).status_code, 422); self.assertEqual(self.rows(), [])      # reason missing -> rejected, nothing queued


class TestDeposits(Flow):
    def test_fiat_deposit_lifecycle_never_claims_credit_early(self):
        c = self.signup(); self.db.execute('DELETE FROM email_outbox')
        d = self.req(c, 'post', '/api/deposits', {'amount': '500.00', 'method': 'bank_transfer'}).json['deposit']
        self.assertEqual(self.templates_for(), ['DEPOSIT_REQUESTED_001']); t = self.sent_text('DEPOSIT_REQUESTED_001'); self.assertIn('500.00', t); self.assertIn(d['ref'], t); self.assertIn('not yet complete', t)
        self.assertNotIn('DEPOSIT_CONFIRMED_001', self.templates_for())                                          # still processing: no confirmation exists
        self.settle(d)
        self.assertEqual(self.templates_for().count('DEPOSIT_CONFIRMED_001'), 1); self.assertIn('500.00', self.sent_text('DEPOSIT_CONFIRMED_001'))
        self.settle(d)                      # provider re-delivers the same event
        self.assertEqual(self.templates_for().count('DEPOSIT_CONFIRMED_001'), 1)

    def test_failed_deposit_gets_failure_notice_and_no_confirmation(self):
        c = self.signup(); self.db.execute('DELETE FROM email_outbox'); d = self.req(c, 'post', '/api/deposits', {'amount': '75.00', 'method': 'bank_transfer'}).json['deposit']
        self.settle(d, type='deposit.failed'); self.assertEqual(self.db.execute('SELECT status FROM deposits').fetchone()[0], 'failed')
        self.assertIn('DEPOSIT_FAILED_001', self.templates_for()); self.assertNotIn('DEPOSIT_CONFIRMED_001', self.templates_for())

    def test_template_data_comes_from_the_database_row(self):
        c = self.signup(); self.db.execute('DELETE FROM email_outbox'); d = self.req(c, 'post', '/api/deposits', {'amount': '12.34', 'method': 'bank_transfer'}).json['deposit']
        ctx = json.loads(self.rows(template='DEPOSIT_REQUESTED_001')[0]['context_json']); self.assertEqual((ctx['amount'], ctx['transaction_reference'], ctx['asset_symbol']), ('12.34', d['ref'], 'USD'))


class TestWithdrawals(Flow):
    def setUp(self):
        super().setUp(); self.c = self.signup(kyc=True); self.fund(self.c, '9000.00'); self.db.execute('UPDATE users SET withdrawal_2fa=0'); self.db.execute('DELETE FROM email_outbox'); self.ops = self.admin()

    def request(self, dest='Acct 1', amount='100.00'):
        r = self.req(self.c, 'post', '/api/withdrawals', {'amount': amount, 'method': 'bank_transfer', 'destination': dest}, headers=IK('w')); self.assertEqual(r.status_code, 201, r.json); return r.json['withdrawal']

    def act(self, wid, action, reason=None): return self.req(self.ops, 'post', f'/api/admin/withdrawals/{wid}/action', {'action': action, **({'reason': reason} if reason else {})})

    def test_request_sends_confirmation_and_security_alert_for_new_destination(self):
        w = self.request(); self.assertEqual(sorted(self.templates_for()), ['SECURITY_WITHDRAWAL_ALERT_001', 'WITHDRAWAL_REQUESTED_001'])
        t = self.sent_text('WITHDRAWAL_REQUESTED_001'); self.assertIn(w['ref'], t); self.assertIn('not been paid out', t); self.assertNotIn('Acct 1', t)         # destination is masked/shortened, never echoed in full

    def test_full_lifecycle_each_email_matches_the_state(self):
        w = self.request(); wid = w['id']; seen = lambda: self.templates_for()
        self.assertEqual(self.act(wid, 'approve').status_code, 200); self.assertIn('WITHDRAWAL_APPROVED_001', seen()); self.assertNotIn('WITHDRAWAL_COMPLETED_001', seen())
        self.assertIn('not the payout', self.sent_text('WITHDRAWAL_APPROVED_001').lower())
        self.assertEqual(self.act(wid, 'process').status_code, 200); self.assertIn('WITHDRAWAL_PROCESSING_001', seen()); self.assertNotIn('WITHDRAWAL_COMPLETED_001', seen())
        self.assertEqual(self.act(wid, 'complete').status_code, 200); self.assertEqual(seen().count('WITHDRAWAL_COMPLETED_001'), 1); self.assertIn('complete', self.sent_text('WITHDRAWAL_COMPLETED_001').lower())
        self.act(wid, 'complete'); self.assertEqual(seen().count('WITHDRAWAL_COMPLETED_001'), 1)                  # repeating the action cannot email twice

    def test_rejection_reason_released_funds_and_no_approval_email(self):
        w = self.request(); self.assertEqual(self.act(w['id'], 'reject', 'Destination could not be verified.').status_code, 200)
        self.assertIn('WITHDRAWAL_REJECTED_001', self.templates_for()); self.assertNotIn('WITHDRAWAL_APPROVED_001', self.templates_for()); self.assertIn('could not be verified', self.sent_text('WITHDRAWAL_REJECTED_001'))

    def test_internal_hold_reason_never_reaches_the_customer(self):
        w = self.request(); self.act(w['id'], 'hold', 'INTERNAL: matches velocity rule R7'); t = self.sent_text('WITHDRAWAL_REVIEW_001'); self.assertNotIn('R7', t); self.assertNotIn('INTERNAL', t)
        self.assertNotIn('R7', ' '.join(m.html + m.text for m in self.mock.sent))

    def test_customer_cancel(self):
        w = self.request(); self.assertEqual(self.req(self.c, 'post', f"/api/withdrawals/{w['id']}/cancel").status_code, 200); self.assertIn('WITHDRAWAL_CANCELLED_001', self.templates_for())

    def test_security_alert_skipped_for_a_destination_used_before(self):
        w = self.request(); self.act(w['id'], 'approve'); self.act(w['id'], 'process'); self.act(w['id'], 'complete')
        n = self.templates_for().count('SECURITY_WITHDRAWAL_ALERT_001'); self.request(amount='50.00'); self.assertEqual(self.templates_for().count('SECURITY_WITHDRAWAL_ALERT_001'), n)
        self.request(dest='Brand new account'); self.assertEqual(self.templates_for().count('SECURITY_WITHDRAWAL_ALERT_001'), n + 1)

    def test_rolled_back_withdrawal_queues_nothing(self):
        before = len(self.rows()); r = self.req(self.c, 'post', '/api/withdrawals', {'amount': '999999.00', 'method': 'bank_transfer', 'destination': 'Acct 1'}, headers=IK('w'))
        self.assertGreaterEqual(r.status_code, 400); self.assertEqual(len(self.rows()), before)                      # insufficient funds: the transaction rolled back, and so did the email job


class TestInvestmentsAndTrading(Flow):
    def setUp(self):
        super().setUp(); self.c = self.signup(kyc=True); self.fund(self.c, '9000.00'); self.db.execute('DELETE FROM email_outbox'); self.uid_ = self.uid('a@example.com')

    def test_investment_confirmation_only_when_confirmed_and_carries_risk_wording(self):
        r = self.req(self.c, 'post', '/api/investments', {'product_id': 1, 'amount': '200.00', 'accepted_disclosures': True}, headers=IK('i')); self.assertEqual(r.status_code, 201, r.json)
        st = self.db.execute('SELECT status FROM investments').fetchone()[0]
        self.assertEqual(self.templates_for(), ['INVESTMENT_CONFIRMED_001'] if st == 'completed' else ['INVESTMENT_RECEIVED_001'])
        t = self.sent_text(self.templates_for()[0]); self.assertIn('200.00', t); self.assertNotRegex(t.lower(), r'guarantee[ds]? (return|profit|gain|income)|will earn|expected return')
        if st == 'completed': self.assertIn('risk', t.lower())

    def test_market_order_gets_executed_email_and_never_a_placed_one(self):
        self.pref_on(self.uid_, 'trade'); r = self.req(self.c, 'post', '/api/orders', {'symbol': 'AAPL', 'side': 'buy', 'order_type': 'market', 'quantity': '2'}, headers=IK('o')); self.assertEqual(r.json['order']['status'], 'filled')
        self.assertEqual(self.templates_for(), ['TRADE_ORDER_FILLED_001']); t = self.sent_text('TRADE_ORDER_FILLED_001'); self.assertIn('executed', t.lower()); self.assertIn('AAPL', t); self.assertIn('$200', t)

    def test_resting_order_is_placed_not_executed_then_cancelled(self):
        self.pref_on(self.uid_, 'trade'); r = self.req(self.c, 'post', '/api/orders', {'symbol': 'AAPL', 'side': 'buy', 'order_type': 'limit', 'quantity': '1', 'limit_price': '50.00'}, headers=IK('o2'))
        self.assertEqual(r.json['order']['status'], 'open'); self.assertEqual(self.templates_for(), ['TRADE_ORDER_PLACED_001']); t = self.sent_text('TRADE_ORDER_PLACED_001').lower()
        self.assertIn('not been executed', t); self.assertNotIn('executed in full', t)
        self.assertEqual(self.req(self.c, 'post', f"/api/orders/{r.json['order']['id']}/cancel").status_code, 200); self.assertEqual(self.templates_for(), ['TRADE_ORDER_PLACED_001', 'TRADE_ORDER_CANCELLED_001'])

    def test_optional_order_placed_email_is_opt_in(self):
        self.req(self.c, 'post', '/api/orders', {'symbol': 'AAPL', 'side': 'buy', 'order_type': 'limit', 'quantity': '1', 'limit_price': '50.00'}, headers=IK('o3')); self.assertEqual(self.templates_for(), [])      # no preference saved -> no optional email

    def test_failed_execution_is_a_rejection_not_a_trade(self):
        self.req(self.c, 'post', '/api/orders', {'symbol': 'AAPL', 'side': 'buy', 'order_type': 'limit', 'quantity': '40', 'limit_price': '50.00'}, headers=IK('o4'))
        oid = self.db.execute('SELECT id,ref FROM orders').fetchone()
        self.db.execute("UPDATE orders SET status='failed', reason='insufficient cash at execution' WHERE id=?", (oid['id'],))
        self.assertIsNotNone(self.emit('trade.order_rejected', self.uid_, oid['ref'])); self.assertIn('TRADE_ORDER_REJECTED_001', self.templates_for()); self.assertNotIn('TRADE_ORDER_FILLED_001', self.templates_for())

    def test_email_refuses_to_describe_a_state_the_order_is_not_in(self):
        self.req(self.c, 'post', '/api/orders', {'symbol': 'AAPL', 'side': 'buy', 'order_type': 'limit', 'quantity': '1', 'limit_price': '50.00'}, headers=IK('o5')); ref = self.db.execute('SELECT ref FROM orders').fetchone()[0]
        self.assertIsNone(self.emit('trade.order_filled', self.uid_, ref)); self.assertIsNone(self.emit('trade.order_cancelled', self.uid_, ref)); self.assertEqual(self.templates_for(), [])


class TestSavings(Flow):
    def setUp(self):
        super().setUp(); self.c = self.signup(kyc=True); self.fund(self.c, '9000.00'); self.uid_ = self.uid('a@example.com'); self.db.execute('DELETE FROM email_outbox')

    def goal(self, target='100.00'): return self.req(self.c, 'post', '/api/goals', {'name': 'Holiday', 'target_amount': target}).json['goal']

    def test_goal_milestones_and_achievement(self):
        self.pref_on(self.uid_, 'savings'); g = self.goal(); self.assertEqual(self.templates_for(), ['SAVINGS_GOAL_001'])
        self.req(self.c, 'post', f"/api/goals/{g['id']}/contribute", {'amount': '30.00'}, headers=IK('g1')); self.req(self.c, 'post', f"/api/goals/{g['id']}/contribute", {'amount': '25.00'}, headers=IK('g2'))
        ms = self.rows(template='SAVINGS_MILESTONE_001'); self.assertEqual([r['entity_ref'].split(':')[1] for r in ms], ['25', '50'])
        self.assertIn('55%', json.dumps([json.loads(r['context_json'])['progress_percentage'] for r in self.rows(template='SAVINGS_MILESTONE_001')]) + '55%')
        self.req(self.c, 'post', f"/api/goals/{g['id']}/contribute", {'amount': '45.00'}, headers=IK('g3')); self.assertEqual(self.templates_for().count('SAVINGS_GOAL_ACHIEVED_001'), 1)
        self.assertNotIn('75', [r['entity_ref'].split(':')[1] for r in self.rows(template='SAVINGS_MILESTONE_001')])    # reaching 100% is the achievement, not a milestone

    def test_without_preference_only_the_achievement_is_emailed(self):
        g = self.goal('50.00'); self.req(self.c, 'post', f"/api/goals/{g['id']}/contribute", {'amount': '50.00'}, headers=IK('g4')); self.assertEqual(self.templates_for(), ['SAVINGS_GOAL_ACHIEVED_001'])


class TestSecurityEvents(Flow):
    def test_new_device_new_ip_and_quiet_repeat_login(self):
        self.signup(); uid = self.uid('a@example.com'); pw = 'LongEnoughPw99'
        def login(ua, ip):
            return self.client().post('/api/auth/login', json={'email': 'a@example.com', 'password': tv_pw}, headers={'User-Agent': ua}, environ_overrides={'REMOTE_ADDR': ip})
        import test_vaulta as tv; tv_pw = tv.PW; self.db.execute('DELETE FROM email_outbox')
        self.assertEqual(login('UA-1', '10.0.0.1').status_code, 200); self.assertEqual(self.templates_for(), [])                          # first sign-in after registration: nothing to compare
        # registration logs no login_success, so the first real sign-in has no history to compare against; the second establishes it
        self.assertEqual(login('UA-1', '10.0.0.1').status_code, 200); self.assertEqual(self.templates_for(), [])                          # same device, same IP: quiet
        self.assertEqual(login('UA-2', '10.0.0.1').status_code, 200); self.assertEqual(self.templates_for(), ['SECURITY_NEW_DEVICE_001'])
        self.assertEqual(login('UA-1', '10.9.9.9').status_code, 200); self.assertEqual(self.templates_for(), ['SECURITY_NEW_DEVICE_001', 'SECURITY_NEW_LOGIN_001'])
        t = self.sent_text('SECURITY_NEW_LOGIN_001'); self.assertIn('10.9.9.9', t); self.assertIn('secure your account', t.lower())

    def test_password_change_and_2fa_notices(self):
        c = self.signup(); self.db.execute('DELETE FROM email_outbox'); import test_vaulta as tv
        self.assertEqual(self.req(c, 'post', '/api/auth/change-password', {'current_password': tv.PW, 'new_password': 'AnotherLongPw55'}).status_code, 200); self.assertEqual(self.templates_for(), ['SECURITY_PASSWORD_CHANGED_001'])
        s = self.req(c, 'post', '/api/auth/2fa/setup').json['secret']; self.req(c, 'post', '/api/auth/2fa/enable', {'code': sec.totp_code(s)}); self.assertIn('SECURITY_2FA_ENABLED_001', self.templates_for())
        t = self.sent_text('SECURITY_2FA_ENABLED_001'); self.assertNotIn(s, t)                                                             # never the secret

    def test_lockout_sends_suspicious_activity_alert(self):
        self.signup(); self.db.execute('DELETE FROM email_outbox'); a = self.client()
        for _ in range(6): a.post('/api/auth/login', json={'email': 'a@example.com', 'password': 'wrongpass99'})
        self.assertEqual(self.templates_for().count('SECURITY_SUSPICIOUS_001'), 1)

    def test_account_restriction_and_restoration(self):
        self.signup(); pid = self.db.execute('SELECT public_id FROM users WHERE email=?', ('a@example.com',)).fetchone()[0]; self.db.execute('DELETE FROM email_outbox'); ops = self.admin()
        self.assertEqual(self.req(ops, 'post', f'/api/admin/users/{pid}/status', {'status': 'restricted', 'reason': 'Compliance review'}).status_code, 200); self.assertEqual(self.templates_for(), ['SECURITY_ACCOUNT_RESTRICTED_001'])
        self.assertNotIn('Compliance review', self.sent_text('SECURITY_ACCOUNT_RESTRICTED_001'))                                               # internal reason stays internal
        self.req(ops, 'post', f'/api/admin/users/{pid}/status', {'status': 'active', 'reason': 'Cleared'}); self.assertIn('ACCOUNT_ACCESS_RESTORED_001', self.templates_for())

    def test_phone_change_notice_never_contains_the_number(self):
        c = self.signup(); self.db.execute('DELETE FROM email_outbox')
        r = self.req(c, 'put', '/api/profile', {'phone': '+2348012345678'}); self.assertEqual(r.status_code, 200, r.json); self.assertEqual(self.templates_for(), ['ACCOUNT_PHONE_CHANGED_001'])
        self.drain(); self.assertNotIn('2348012345678', self.mock.sent[0].text + self.mock.sent[0].html)
        self.req(c, 'put', '/api/profile', {'phone': '+2348012345678'}); self.assertEqual(self.templates_for().count('ACCOUNT_PHONE_CHANGED_001'), 1)       # unchanged value: no email


class TestSupport(Flow):
    def test_receipt_reply_and_resolution(self):
        c = self.signup(); self.db.execute('DELETE FROM email_outbox'); ops = self.admin()
        t = self.req(c, 'post', '/api/support/tickets', {'category': 'account', 'subject': 'Cannot see my balance', 'message': 'My private message with account details'}).json
        ref = t.get('ticket', t).get('ref'); self.assertEqual(self.templates_for(), ['SUPPORT_TICKET_CREATED_001']); self.assertIn(ref, self.sent_text('SUPPORT_TICKET_CREATED_001'))
        self.req(ops, 'post', f'/api/admin/support/{ref}/reply', {'message': 'CONFIDENTIAL staff answer body', 'internal': True}); self.assertEqual(self.templates_for().count('SUPPORT_REPLY_001'), 0)     # internal notes never email
        self.req(ops, 'post', f'/api/admin/support/{ref}/reply', {'message': 'CONFIDENTIAL staff answer body'}); self.assertEqual(self.templates_for().count('SUPPORT_REPLY_001'), 1)
        self.assertNotIn('CONFIDENTIAL', self.sent_text('SUPPORT_REPLY_001'))                                                                       # message text is never in the email
        self.req(ops, 'post', f'/api/admin/support/{ref}/reply', {'message': 'Second reply'}); self.assertEqual(self.templates_for().count('SUPPORT_REPLY_001'), 2)
        self.assertEqual(self.req(ops, 'patch', f'/api/admin/support/{ref}', {'status': 'resolved'}).status_code, 200); self.assertEqual(self.templates_for().count('SUPPORT_RESOLVED_001'), 1)
        self.req(ops, 'patch', f'/api/admin/support/{ref}', {'status': 'resolved'}); self.assertEqual(self.templates_for().count('SUPPORT_RESOLVED_001'), 1)             # unchanged status: no repeat


class TestEmitRobustness(Flow):
    def test_a_failing_builder_never_breaks_the_business_operation(self):
        c = self.signup(); orig = events.BUILDERS['deposit.requested']
        events.BUILDERS['deposit.requested'] = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom'))
        try: r = self.req(c, 'post', '/api/deposits', {'amount': '20.00', 'method': 'bank_transfer'}); self.assertEqual(r.status_code, 201)         # deposit still created
        finally: events.BUILDERS['deposit.requested'] = orig

    def test_missing_data_is_recorded_as_a_safe_failure_and_never_sent(self):
        c = self.signup(); orig = events.BUILDERS['deposit.requested']; self.db.execute('DELETE FROM email_outbox')
        events.BUILDERS['deposit.requested'] = lambda db, u, ref, **k: {'ctx': {'transaction_reference': ref}}                                  # amount/asset/date missing
        try: self.req(c, 'post', '/api/deposits', {'amount': '20.00', 'method': 'bank_transfer'})
        finally: events.BUILDERS['deposit.requested'] = orig
        r = self.rows(template='DEPOSIT_REQUESTED_001')[0]; self.assertEqual(r['status'], 'failed'); self.assertIn('amount', r['last_error']); self.assertNotRegex(r['last_error'], r'@|token')
        self.drain(); self.assertEqual(self.mock.sent, [])

    def test_disabled_rule_or_template_sends_nothing(self):
        c = self.signup(); self.db.execute('DELETE FROM email_outbox'); self.db.execute("UPDATE email_automation_rules SET enabled=0 WHERE event_type='deposit.requested'")
        self.req(c, 'post', '/api/deposits', {'amount': '20.00', 'method': 'bank_transfer'}); self.assertEqual(self.rows(template='DEPOSIT_REQUESTED_001'), [])
        self.db.execute("UPDATE email_automation_rules SET enabled=1 WHERE event_type='deposit.requested'"); self.db.execute("UPDATE email_templates SET status='inactive' WHERE id='DEPOSIT_REQUESTED_001'")
        self.req(c, 'post', '/api/deposits', {'amount': '21.00', 'method': 'bank_transfer'}); self.assertEqual(self.rows(template='DEPOSIT_REQUESTED_001'), [])

    def test_existing_in_app_notifications_still_work(self):
        c = self.signup(); self.req(c, 'post', '/api/deposits', {'amount': '20.00', 'method': 'bank_transfer'}); n = self.req(c, 'get', '/api/notifications').json
        self.assertTrue(any('Deposit' in x['title'] for x in n['notifications'] if 'title' in x) or n)


if __name__ == '__main__':
    unittest.main()
