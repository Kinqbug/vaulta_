"""Email Center: server-side permissions, template versioning, individual / selected / broadcast sends, recipient privacy, consent + unsubscribe,
suppression, reminders, logs and secret hygiene."""
import os, sys, re, json, unittest, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from email_base import EmailBase, BASE
import test_vaulta as tv
from server import util, rbac, audit as audit_mod
from server.mailer import prefs, reminders, events, catalog as cat, outbox, templates_db as tdb
from server.mailer.transports import Result

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A = '/api/admin/email'


class AdminBase(EmailBase):
    def customers(self, n, prefix='c', opt_in=False, verified=True):
        out = []
        for i in range(n):
            em = f'{prefix}{i}@example.com'; self.signup(em, verify=verified); uid = self.uid(em)
            if opt_in: prefs.set_marketing(self.db, uid, True, 'test')
            out.append(self.db.execute('SELECT public_id FROM users WHERE id=?', (uid,)).fetchone()[0])
        self.db.execute('DELETE FROM email_outbox'); return out

    def post(self, c, url, body=None, **kw): return self.req(c, 'post', A + url, body or {}, **kw)
    def send(self, c, mode, template, *, user_ids=None, segment=None, variables=None, overrides=None, schedule=None, confirm=None):
        r = self.post(c, '/campaigns', {'mode': mode, 'template_id': template, 'audience': {'user_ids': user_ids or [], 'segment': segment}, 'variables': variables or {}, 'overrides': overrides or {}, 'schedule_at': schedule})
        if r.status_code != 201: return r, None
        s = self.post(c, f"/campaigns/{r.json['id']}/submit", {'confirm_count': confirm if confirm is not None else r.json['estimated_count']}); return r, s
    def set_setting(self, k, v): self.db.execute('UPDATE system_settings SET value=? WHERE key=?', (str(v), k))


ENDPOINTS = [('get', '/overview', 'email.logs.read'), ('get', '/status', 'email.logs.read'), ('get', '/templates', 'email.templates.read'), ('get', '/templates/WELCOME_001', 'email.templates.read'),
             ('post', '/templates/WELCOME_001/preview', 'email.templates.read'), ('post', '/templates/WELCOME_001/draft', 'email.templates.edit'), ('post', '/templates/WELCOME_001/publish', 'email.templates.publish'),
             ('post', '/templates/WELCOME_001/status', 'email.templates.publish'), ('post', '/templates/WELCOME_001/duplicate', 'email.templates.edit'), ('post', '/templates/WELCOME_001/test-send', 'email.send'),
             ('get', '/customers/search?q=ab', 'email.send'), ('get', '/audiences', 'email.send'), ('post', '/compose/preview', 'email.send'), ('post', '/campaigns', 'email.send'), ('post', '/campaigns/1/submit', 'email.send'),
             ('post', '/campaigns/1/approve', 'email.bulk_approve'), ('post', '/campaigns/1/cancel', 'email.send'), ('get', '/campaigns', 'email.logs.read'), ('get', '/automations', 'email.logs.read'),
             ('patch', '/automations/1', 'email.automations.manage'), ('post', '/automations/run-reminders', 'email.automations.manage'), ('get', '/outbox', 'email.logs.read'), ('get', '/outbox/1', 'email.logs.read'),
             ('get', '/outbox/1/preview', 'email.logs.read'), ('post', '/outbox/1/retry', 'email.settings.manage'), ('post', '/outbox/1/cancel', 'email.settings.manage'), ('get', '/delivery-logs', 'email.logs.read'),
             ('post', '/queue/process', 'email.settings.manage'), ('get', '/suppressions', 'email.logs.read'), ('post', '/suppressions', 'email.suppression.manage'), ('post', '/suppressions/1/release', 'email.suppression.manage'),
             ('get', '/settings', 'email.settings.manage'), ('put', '/settings', 'email.settings.manage')]


class TestPermissions(AdminBase):
    def test_anonymous_and_ordinary_customers_are_refused_everywhere(self):
        anon = self.client(); cust = self.signup()
        for m, url, _ in ENDPOINTS:
            self.assertEqual(getattr(anon, m)(A + url, json={}).status_code, 401, url)
            self.assertEqual(self.req(cust, m, A + url, {}).status_code, 403, url)

    def test_every_endpoint_enforces_its_permission_on_the_server(self):
        for role in ('auditor', 'operations_admin', 'support_admin', 'content_admin', 'communications_admin', 'compliance_admin', 'super_admin'):
            c = self.staff(role); have = set(rbac.ROLES[role][1])
            for m, url, perm in ENDPOINTS:
                code = self.req(c, m, A + url, {}).status_code
                if perm in have: self.assertNotEqual(code, 403, f'{role} should reach {m} {url} ({code})')
                else: self.assertEqual(code, 403, f'{role} must NOT reach {m} {url} ({code})')

    def test_role_design_separates_sending_approving_and_provider_settings(self):
        p = lambda r: set(rbac.ROLES[r][1])
        self.assertNotIn('email.bulk_approve', p('communications_admin')); self.assertNotIn('email.settings.manage', p('communications_admin')); self.assertNotIn('email.send_bulk', p('support_admin'))
        self.assertNotIn('email.templates.publish', p('content_admin')); self.assertEqual({x for x in p('auditor') if x.startswith('email.')}, {'email.logs.read', 'email.templates.read'})
        self.assertTrue({x for x in rbac.PERMS if x.startswith('email.')} <= p('super_admin'))

    def test_support_agent_cannot_start_a_broadcast_even_by_calling_the_api_directly(self):
        sup = self.staff('support_admin'); r = self.post(sup, '/campaigns', {'mode': 'broadcast', 'template_id': 'ANNOUNCEMENT_GENERAL_001', 'audience': {'segment': 'all_active'}, 'variables': {'message_body': 'x'}})
        self.assertEqual(r.status_code, 403); self.assertEqual(self.db.execute('SELECT COUNT(*) FROM email_campaigns').fetchone()[0], 0)


class TestTemplates(AdminBase):
    def test_list_search_and_detail_expose_required_fields(self):
        c = self.staff('auditor'); items = c.get(A + '/templates').json['items']; self.assertGreaterEqual(len(items), 82)
        for k in ('id', 'name', 'category', 'description', 'subject', 'trigger', 'status', 'updated_at', 'updated_by', 'version'): self.assertIn(k, items[0])
        self.assertEqual([t['id'] for t in c.get(A + '/templates?q=WITHDRAWAL_COMPLETED').json['items']], ['WITHDRAWAL_COMPLETED_001'])
        d = c.get(A + '/templates/DEPOSIT_CONFIRMED_001').json; self.assertTrue(d['variables']); self.assertEqual(d['version'], 1); self.assertIn('Deposit credited', d['trigger'])

    def test_preview_renders_labelled_sample_html_and_never_sends(self):
        c = self.staff('auditor'); r = self.post(c, '/templates/DEPOSIT_CONFIRMED_001/preview'); self.assertEqual(r.status_code, 200); self.assertIn('PREVIEW', r.json['html']); self.assertIn('SAMPLE-0001', r.json['html']); self.assertTrue(r.json['text'])
        self.assertIn('data:image/png;base64', r.json['html']); self.assertEqual(self.mock.sent, [])

    def test_edit_publish_flow_and_versions_are_immutable(self):
        ed = self.staff('communications_admin'); t = ed.get(A + '/templates/ANNOUNCEMENT_GENERAL_001').json; spec = t['spec']; spec['headline'] = 'Edited headline for {{first_name}}.'
        r = self.post(ed, '/templates/ANNOUNCEMENT_GENERAL_001/draft', {'subject': 'Edited subject', 'spec': spec}); self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(ed.get(A + '/templates/ANNOUNCEMENT_GENERAL_001').json['version'], 1)                      # a draft is not live
        self.assertEqual(self.post(ed, '/templates/ANNOUNCEMENT_GENERAL_001/publish').status_code, 200)             # service template: author may publish
        d = ed.get(A + '/templates/ANNOUNCEMENT_GENERAL_001').json; self.assertEqual((d['version'], d['subject']), (2, 'Edited subject')); self.assertEqual([v['state'] for v in d['versions']], ['published', 'superseded'])
        with self.assertRaises(Exception): self.db.execute("UPDATE email_template_versions SET subject='tampered' WHERE template_id='ANNOUNCEMENT_GENERAL_001' AND version=1")
        with self.assertRaises(Exception): self.db.execute("DELETE FROM email_template_versions WHERE template_id='ANNOUNCEMENT_GENERAL_001' AND version=1")

    def test_security_and_transactional_templates_need_a_second_person_to_publish(self):
        ed = self.staff('communications_admin'); spec = ed.get(A + '/templates/SECURITY_NEW_LOGIN_001').json['spec']; spec['headline'] = 'Different headline.'
        self.assertEqual(self.post(ed, '/templates/SECURITY_NEW_LOGIN_001/draft', {'subject': 'New sign-in', 'spec': spec}).status_code, 200)
        r = self.post(ed, '/templates/SECURITY_NEW_LOGIN_001/publish'); self.assertEqual(r.status_code, 403); self.assertEqual(r.json['error']['code'], 'separation_of_duties')
        other = self.staff('super_admin'); self.assertEqual(self.post(other, '/templates/SECURITY_NEW_LOGIN_001/publish').status_code, 200)

    def test_unsafe_or_unsupported_content_is_rejected(self):
        ed = self.staff('communications_admin'); base = ed.get(A + '/templates/ANNOUNCEMENT_GENERAL_001').json['spec']
        for bad in ('{% for x in y %}x{% endfor %}', '{{ config }}', '<script>alert(1)</script>', '{{amount}}', '{{ first_name.__class__ }}'):
            s = json.loads(json.dumps(base)); s['intro'] = [bad]; self.assertEqual(self.post(ed, '/templates/ANNOUNCEMENT_GENERAL_001/draft', {'subject': 'ok', 'spec': s}).status_code, 422, bad)
        s = json.loads(json.dumps(base)); s['cta'] = {'label': 'Go', 'url_var': 'first_name'}; self.assertEqual(self.post(ed, '/templates/ANNOUNCEMENT_GENERAL_001/draft', {'subject': 'ok', 'spec': s}).status_code, 422)

    def test_queued_mail_keeps_the_version_it_was_queued_with(self):
        ed = self.staff('communications_admin'); [pid] = self.customers(1); uid = self.uid('c0@example.com')
        eid, _ = outbox.enqueue(self.db, user_id=uid, to_email='c0@example.com', template_id='ANNOUNCEMENT_GENERAL_001', version=1, subject='s', event_type='t', entity_ref='r', idem='pin', mail_class='service', ctx={'first_name': 'C', 'message_body': 'Body'})
        spec = ed.get(A + '/templates/ANNOUNCEMENT_GENERAL_001').json['spec']; spec['headline'] = 'NEW VERSION HEADLINE'
        self.post(ed, '/templates/ANNOUNCEMENT_GENERAL_001/draft', {'subject': 'v2', 'spec': spec}); self.post(ed, '/templates/ANNOUNCEMENT_GENERAL_001/publish')
        self.drain(); self.assertNotIn('NEW VERSION HEADLINE', self.mock.sent[0].html); self.assertIn('An update from VAULTA', self.mock.sent[0].html)

    def test_disable_duplicate_and_audit_trail(self):
        ed = self.staff('communications_admin'); self.assertEqual(self.post(ed, '/templates/ANNOUNCEMENT_GENERAL_001/status', {'status': 'inactive'}).status_code, 422)          # reason required
        self.assertEqual(self.post(ed, '/templates/ANNOUNCEMENT_GENERAL_001/status', {'status': 'inactive', 'reason': 'Paused'}).status_code, 200)
        self.assertEqual(self.post(ed, '/templates/ANNOUNCEMENT_GENERAL_001/duplicate', {'new_id': 'CUSTOM_PROMO_001', 'name': 'Custom promo'}).json['id'], 'CUSTOM_PROMO_001')
        c = ed.get(A + '/templates/CUSTOM_PROMO_001').json; self.assertEqual((c['status'], c['is_system']), ('inactive', False))
        self.assertEqual(self.post(ed, '/templates/ANNOUNCEMENT_GENERAL_001/duplicate', {'new_id': 'CUSTOM_PROMO_001', 'name': 'x'}).status_code, 422)             # unique ids
        acts = [r[0] for r in self.db.execute("SELECT action FROM audit_logs WHERE action LIKE 'email.%'")]; self.assertIn('email.template_status_changed', acts); self.assertIn('email.template_duplicated', acts)
        self.assertTrue(audit_mod.verify_chain(self.db)['ok'] if isinstance(audit_mod.verify_chain(self.db), dict) else audit_mod.verify_chain(self.db))

    def test_code_catalog_updates_never_overwrite_administrator_edits(self):
        ed = self.staff('communications_admin'); spec = ed.get(A + '/templates/NEWSLETTER_001').json['spec']; spec['headline'] = 'ADMIN EDIT'
        self.post(ed, '/templates/NEWSLETTER_001/draft', {'subject': 'Admin subject', 'spec': spec}); self.post(ed, '/templates/NEWSLETTER_001/publish')
        self.db.execute("UPDATE email_template_versions SET content_hash='stale' WHERE template_id='NEWSLETTER_001' AND version=1")
        tdb.ensure_seed(self.db); self.assertEqual(self.db.execute("SELECT current_version FROM email_templates WHERE id='NEWSLETTER_001'").fetchone()[0], 2)
        self.assertEqual(ed.get(A + '/templates/NEWSLETTER_001').json['spec']['headline'], 'ADMIN EDIT')


class TestCompose(AdminBase):
    def test_individual_send_reaches_only_the_chosen_customer(self):
        ids = self.customers(3); ed = self.staff('support_admin'); r, s = self.send(ed, 'individual', 'ADMIN_CUSTOM_001', user_ids=[ids[1]], variables={'message_body': 'Hello there.\n\nSecond paragraph.'})
        self.assertEqual(s.status_code, 200, s.json); self.assertEqual(s.json['status'], 'sending'); self.drain()
        self.assertEqual([m.to for m in self.mock.sent], ['c1@example.com']); self.assertIn('Second paragraph.', self.mock.sent[0].text)

    def test_selected_customers_get_separate_private_messages(self):
        ids = self.customers(4); ed = self.staff('support_admin'); r, s = self.send(ed, 'selected', 'ADMIN_CUSTOM_001', user_ids=ids[:3] + ['USR-DOESNOTEXIST'], variables={'message_body': 'Notice.'}, confirm=3)
        self.assertEqual(s.status_code, 200, s.json); self.drain(); self.assertEqual(sorted(m.to for m in self.mock.sent), ['c0@example.com', 'c1@example.com', 'c2@example.com'])
        for m in self.mock.sent:
            others = [e for e in ('c0@example.com', 'c1@example.com', 'c2@example.com', 'c3@example.com') if e != m.to]
            for o in others: self.assertNotIn(o, m.html + m.text + str(m.headers))
            self.assertFalse(hasattr(m, 'cc') or hasattr(m, 'bcc')); self.assertEqual(m.to.count('@'), 1)
        recs = self.staff('auditor').get(A + f"/campaigns/{r.json['id']}").json['recipients']; self.assertTrue(all('***' in x['email'] for x in recs))              # logs mask addresses

    def test_staff_accounts_and_closed_accounts_are_never_selected(self):
        ids = self.customers(2); self.staff('operations_admin'); sp = self.db.execute("SELECT public_id FROM users WHERE email='operations_admin@vaulta.test'").fetchone()[0]
        self.db.execute("UPDATE users SET status='closed' WHERE email='c1@example.com'"); ed = self.staff('support_admin')
        r, s = self.send(ed, 'selected', 'ADMIN_CUSTOM_001', user_ids=[ids[0], ids[1], sp], variables={'message_body': 'x'}, confirm=1); self.assertEqual(s.status_code, 200); self.drain(); self.assertEqual([m.to for m in self.mock.sent], ['c0@example.com'])

    def test_templates_describing_a_transaction_cannot_be_hand_sent(self):
        [pid] = self.customers(1); ed = self.staff('communications_admin')
        for tid in ('DEPOSIT_CONFIRMED_001', 'WITHDRAWAL_COMPLETED_001', 'KYC_APPROVED_001', 'SECURITY_NEW_LOGIN_001', 'TRADE_ORDER_FILLED_001'):
            r = self.post(ed, '/campaigns', {'mode': 'individual', 'template_id': tid, 'audience': {'user_ids': [pid]}}); self.assertEqual((r.status_code, r.json['error']['code']), (422, 'template_not_sendable'), tid)
        r = self.post(ed, '/campaigns', {'mode': 'broadcast', 'template_id': 'ADMIN_CUSTOM_001', 'audience': {'segment': 'all_active'}, 'variables': {'message_body': 'x'}}); self.assertEqual(r.json['error']['code'], 'template_not_for_broadcast')

    def test_variables_must_be_valid_and_complete(self):
        [pid] = self.customers(1); ed = self.staff('support_admin')
        self.assertEqual(self.post(ed, '/campaigns', {'mode': 'individual', 'template_id': 'ADMIN_CUSTOM_001', 'audience': {'user_ids': [pid]}}).status_code, 422)                                   # message missing
        self.assertEqual(self.post(ed, '/campaigns', {'mode': 'individual', 'template_id': 'ADMIN_CUSTOM_001', 'audience': {'user_ids': [pid]}, 'variables': {'message_body': 'x', 'amount': '1000000'}}).status_code, 422)   # cannot inject money values
        self.assertEqual(self.post(ed, '/campaigns', {'mode': 'individual', 'template_id': 'ADMIN_CUSTOM_001', 'audience': {'user_ids': [pid]}, 'variables': {'message_body': 'x'}, 'overrides': {'cta_url': 'https://evil.test/login'}}).status_code, 422)
        self.assertEqual(self.post(ed, '/campaigns', {'mode': 'individual', 'template_id': 'ADMIN_CUSTOM_001', 'audience': {'user_ids': [pid]}, 'variables': {'message_body': 'x'}, 'overrides': {'cta_url': 'javascript:alert(1)'}}).status_code, 422)

    def test_administrator_text_is_escaped(self):
        [pid] = self.customers(1); ed = self.staff('support_admin'); self.send(ed, 'individual', 'ADMIN_CUSTOM_001', user_ids=[pid], variables={'message_body': '<script>steal()</script> <a href="https://evil.test">click</a>'}); self.drain()
        h = self.mock.sent[0].html; self.assertNotIn('<script>steal', h); self.assertNotIn('<a href="https://evil.test"', h); self.assertIn('&lt;script&gt;', h)

    def test_transaction_reference_must_belong_to_the_customer_and_status_comes_from_our_records(self):
        ids = self.customers(2); c0 = self.signup('owner@example.com', kyc=True); self.fund(c0, '100.00', 'owner@example.com'); self.db.execute('DELETE FROM email_outbox')
        ref = self.db.execute('SELECT ref FROM deposits').fetchone()[0]; owner = self.db.execute("SELECT public_id FROM users WHERE email='owner@example.com'").fetchone()[0]; ed = self.staff('support_admin')
        r = self.post(ed, '/compose/preview', {'mode': 'individual', 'template_id': 'ADMIN_TRANSACTION_REVIEW_001', 'user_id': ids[0], 'variables': {'message_body': 'x', 'transaction_reference': ref}}); self.assertEqual(r.json['error']['code'], 'reference_mismatch')
        r, s = self.send(ed, 'individual', 'ADMIN_TRANSACTION_REVIEW_001', user_ids=[owner], variables={'message_body': 'Please wait.', 'transaction_reference': ref}); self.assertEqual(s.status_code, 200); self.drain()
        self.assertIn(ref, self.mock.sent[0].text); self.assertIn('Completed', self.mock.sent[0].text)

    def test_compose_preview_uses_the_real_customer_name_and_sends_nothing(self):
        [pid] = self.customers(1); ed = self.staff('support_admin'); r = self.post(ed, '/compose/preview', {'mode': 'individual', 'template_id': 'ADMIN_CUSTOM_001', 'user_id': pid, 'variables': {'message_body': 'Hi'}, 'overrides': {'headline': 'Custom headline'}})
        self.assertEqual(r.status_code, 200); self.assertIn('Custom headline', r.json['html']); self.assertIn('Hi T,', r.json['text']); self.assertEqual(self.mock.sent, [])

    def test_test_send_goes_only_to_the_administrators_own_address(self):
        ed = self.staff('support_admin'); r = self.post(ed, '/templates/DEPOSIT_CONFIRMED_001/test-send', {'to': 'someone.else@example.com'}); self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual([m.to for m in self.mock.sent], ['support_admin@vaulta.test']); m = self.mock.sent[0]; self.assertTrue(m.subject.startswith('[TEST]')); self.assertIn('TEST EMAIL', m.html); self.assertIn('SAMPLE-0001', m.text)
        self.assertEqual(r.json['status'], 'accepted')

    def test_failed_individual_send_is_reported_truthfully(self):
        [pid] = self.customers(1); ed = self.staff('support_admin'); self.send(ed, 'individual', 'ADMIN_CUSTOM_001', user_ids=[pid], variables={'message_body': 'x'})
        self.mock.plan.append(Result(False, retryable=False, code='422', error='rejected')); self.drain(); d = self.staff('auditor').get(A + '/outbox?status=failed').json; self.assertEqual(d['total'], 1); self.assertEqual([x['status'] for x in d['items']], ['failed']); self.assertIsNone(d['items'][0]['delivered_at'])


class TestCampaigns(AdminBase):
    def test_audience_filters_use_real_data(self):
        ids = self.customers(6, verified=True)
        self.db.execute("UPDATE users SET email_verified=0 WHERE email='c5@example.com'")
        self.db.execute("UPDATE users SET kyc_status='verified' WHERE email IN ('c0@example.com','c1@example.com')")
        self.db.execute("UPDATE users SET kyc_status='pending' WHERE email='c2@example.com'")
        prefs.set_marketing(self.db, self.uid('c0@example.com'), True, 't'); prefs.set_marketing(self.db, self.uid('c4@example.com'), True, 't')
        ed = self.staff('communications_admin'); got = {a['key']: a['eligible'] for a in ed.get(A + '/audiences?mail_class=service').json['items']}
        self.assertEqual((got['all_active'], got['email_verified'], got['kyc_verified'], got['kyc_incomplete'], got['marketing_opt_in']), (6, 5, 2, 4, 2))
        mk = {a['key']: a['eligible'] for a in ed.get(A + '/audiences?mail_class=marketing').json['items']}; self.assertEqual((mk['all_active'], mk['marketing_opt_in']), (2, 2))      # marketing: opted-in + verified only
        self.db.execute("INSERT INTO email_suppressions(email_key,scope,reason,created_at) VALUES('c4@example.com','marketing','unsubscribe',?)", (util.now(),))
        self.assertEqual({a['key']: a['eligible'] for a in ed.get(A + '/audiences?mail_class=marketing').json['items']}['all_active'], 1)

    def test_broadcast_needs_an_informed_confirmation_of_the_recipient_count(self):
        self.customers(3); ed = self.staff('communications_admin'); r, s = self.send(ed, 'broadcast', 'ANNOUNCEMENT_GENERAL_001', segment='all_active', variables={'message_body': 'Update.'}, confirm=99)
        self.assertEqual(r.json['estimated_count'], 3); self.assertEqual(s.status_code, 409); self.assertEqual(s.json['error']['code'], 'audience_changed'); self.drain(); self.assertEqual(self.mock.sent, [])
        s = self.post(ed, f"/campaigns/{r.json['id']}/submit", {'confirm_count': 3}); self.assertEqual(s.json['status'], 'sending'); self.drain(); self.assertEqual(len(self.mock.sent), 3)
        self.assertEqual(len({m.to for m in self.mock.sent}), 3); camp = self.db.execute('SELECT * FROM email_campaigns').fetchone(); self.assertEqual((camp['final_count'], camp['status']), (3, 'sending'))
        ed.get(A + '/campaigns'); self.assertEqual(self.db.execute('SELECT status FROM email_campaigns').fetchone()[0], 'completed')

    def test_large_campaign_needs_a_second_approver_and_respects_the_cap(self):
        self.customers(3); self.set_setting('email_bulk_approval_threshold', 2); ed = self.staff('communications_admin'); r, s = self.send(ed, 'broadcast', 'ANNOUNCEMENT_GENERAL_001', segment='all_active', variables={'message_body': 'Big update.'})
        self.assertEqual(s.json['status'], 'pending_approval'); self.drain(); self.assertEqual(self.mock.sent, [])
        self.assertEqual(self.post(ed, f"/campaigns/{r.json['id']}/approve").status_code, 403)                          # the sender lacks the permission
        boss = self.staff('super_admin', 'boss1@vaulta.test'); creator_super = self.staff('super_admin', 'boss2@vaulta.test')
        r2, s2 = self.send(creator_super, 'broadcast', 'ANNOUNCEMENT_GENERAL_001', segment='all_active', variables={'message_body': 'Another.'}); self.assertEqual(s2.json['status'], 'pending_approval')
        own = self.post(creator_super, f"/campaigns/{r2.json['id']}/approve"); self.assertEqual((own.status_code, own.json['error']['code']), (403, 'separation_of_duties'))        # even a super admin cannot approve their own
        self.assertEqual(self.post(boss, f"/campaigns/{r.json['id']}/approve").status_code, 200); self.drain(); self.assertEqual(len(self.mock.sent), 3)
        self.set_setting('email_campaign_max_recipients', 2); r3, s3 = self.send(boss, 'broadcast', 'ANNOUNCEMENT_GENERAL_001', segment='all_active', variables={'message_body': 'x'}); self.assertEqual((s3.status_code, s3.json['error']['code']), (422, 'too_many_recipients'))

    def test_scheduled_campaign_waits_and_can_be_cancelled(self):
        self.customers(2); ed = self.staff('communications_admin'); when = util.ts_after(days=2)
        r, s = self.send(ed, 'broadcast', 'ANNOUNCEMENT_GENERAL_001', segment='all_active', variables={'message_body': 'Later.'}, schedule=when); self.assertEqual(s.json['status'], 'scheduled')
        self.drain(); self.assertEqual(self.mock.sent, []); self.assertEqual({r['next_attempt_at'] for r in self.rows()}, {when})
        self.drain(now=util.ts_after(days=3)); self.assertEqual(len(self.mock.sent), 2)
        r2, s2 = self.send(ed, 'broadcast', 'ANNOUNCEMENT_MAINTENANCE_001', segment='all_active', variables={'maintenance_window': '2026-11-01 01:00-02:00 UTC'}, schedule=util.ts_after(days=5)); self.mock.sent.clear()
        self.assertEqual(self.post(ed, f"/campaigns/{r2.json['id']}/cancel").json['cancelled_messages'], 2); self.drain(now=util.ts_after(days=9)); self.assertEqual(self.mock.sent, [])
        self.assertEqual(self.post(ed, '/campaigns', {'mode': 'broadcast', 'template_id': 'ANNOUNCEMENT_GENERAL_001', 'audience': {'segment': 'all_active'}, 'variables': {'message_body': 'x'}, 'schedule_at': '2020-01-01T00:00:00Z'}).status_code, 422)

    def test_marketing_broadcast_respects_consent_and_unsubscribe_end_to_end(self):
        ids = self.customers(4); [prefs.set_marketing(self.db, self.uid(f'c{i}@example.com'), True, 'signup') for i in (0, 1, 2)]; ed = self.staff('communications_admin')
        r, s = self.send(ed, 'broadcast', 'NEWSLETTER_001', segment='all_active', variables={'message_body': 'Our news.'}); self.assertEqual(r.json['estimated_count'], 3); self.drain()
        self.assertEqual(sorted(m.to for m in self.mock.sent), ['c0@example.com', 'c1@example.com', 'c2@example.com'])
        m = self.mock.sent[0]; self.assertRegex(m.headers['List-Unsubscribe'], r'^<https://app\.vaulta\.example/api/email/unsubscribe/one-click\?t=[\w.-]+>$'); self.assertEqual(m.headers['List-Unsubscribe-Post'], 'List-Unsubscribe=One-Click'); self.assertIn('Unsubscribe', m.html)
        tok = re.search(r'one-click\?t=([\w.-]+)', m.headers['List-Unsubscribe']).group(1); anon = self.client()
        self.assertEqual(anon.post(f'/api/email/unsubscribe/one-click?t={tok}', data='List-Unsubscribe=One-Click', content_type='application/x-www-form-urlencoded').status_code, 200)         # no session, no CSRF: as mail clients do it
        uid = self.uid(m.to); self.assertFalse(prefs.marketing_opted_in(self.db, uid)); self.assertEqual(self.db.execute("SELECT action FROM communication_consent_log WHERE user_id=? ORDER BY id DESC", (uid,)).fetchone()[0], 'opt_out')
        self.mock.sent.clear(); r2, s2 = self.send(ed, 'broadcast', 'NEWSLETTER_001', segment='all_active', variables={'message_body': 'More news.'}); self.assertEqual(r2.json['estimated_count'], 2); self.drain(); self.assertNotIn(m.to, [x.to for x in self.mock.sent])
        # consent withdrawn AFTER queueing is still honoured at send time
        self.mock.sent.clear(); self.send(ed, 'broadcast', 'NEWSLETTER_001', segment='all_active', variables={'message_body': 'Third.'}); prefs.set_marketing(self.db, self.uid('c1@example.com'), False, 'test'); self.drain()
        self.assertNotIn('c1@example.com', [x.to for x in self.mock.sent]); self.assertEqual(self.rows(to='c1@example.com', status='suppressed')[-1]['last_error'], 'no marketing consent')
        # transactional mail is unaffected by unsubscribing from marketing
        self.mock.sent.clear(); self.emit('auth.email_verified', uid, 'after-unsub'); self.drain(); self.assertEqual([x.to for x in self.mock.sent], [m.to])

    def test_unsubscribe_tokens_cannot_be_forged(self):
        self.customers(2); anon = self.client(); good = prefs.make_unsub_token(self.app.config['AUTH_SECRET'], self.uid('c0@example.com')); prefs.set_marketing(self.db, self.uid('c0@example.com'), True, 't'); prefs.set_marketing(self.db, self.uid('c1@example.com'), True, 't')
        a, b = good.split('.'); forged = ('Mg' + '.' + b)
        for bad in ('', 'x', 'abc.def', forged, a + '.' + 'A' * len(b), good + 'x'): self.assertEqual(anon.post('/api/email/unsubscribe', json={'token': bad}).status_code, 400, bad)
        self.assertTrue(prefs.marketing_opted_in(self.db, self.uid('c1@example.com'))); info = anon.get(f'/api/email/unsubscribe/info?t={good}').json; self.assertIn('***', info['email']); self.assertTrue(info['subscribed'])
        self.assertEqual(anon.post('/api/email/unsubscribe', json={'token': good}).status_code, 200); self.assertFalse(prefs.marketing_opted_in(self.db, self.uid('c0@example.com')))

    def test_customer_can_manage_consent_in_settings_and_it_is_logged(self):
        c = self.signup(); self.assertFalse(c.get('/api/me/communication-preferences').json['marketing_opt_in'])
        self.assertTrue(self.req(c, 'put', '/api/me/communication-preferences', {'marketing_opt_in': True}).json['marketing_opt_in'])
        p = self.req(c, 'put', '/api/notification-preferences', {'preferences': [{'type': 'promotional', 'in_app': True, 'email': False}]}); self.assertEqual(p.status_code, 200, p.json)
        self.assertFalse(c.get('/api/me/communication-preferences').json['marketing_opt_in']); self.assertEqual([r[0] for r in self.db.execute('SELECT action FROM communication_consent_log ORDER BY id')], ['opt_in', 'opt_out'])
        self.assertEqual(self.req(c, 'put', '/api/me/communication-preferences', {'timezone': 'Mars/Phobos'}).status_code, 422); self.assertEqual(self.req(c, 'put', '/api/me/communication-preferences', {'timezone': 'Africa/Lagos'}).json['timezone'], 'Africa/Lagos')

    def test_manual_suppression_blocks_even_transactional_mail_and_release_needs_a_reason(self):
        ids = self.customers(1); ed = self.staff('communications_admin'); self.assertEqual(self.post(ed, '/suppressions', {'email': 'c0@example.com', 'scope': 'all', 'reason': 'Customer asked us to stop'}).status_code, 200)
        self.emit('auth.email_verified', self.uid('c0@example.com'), 'x'); self.drain(); self.assertEqual(self.mock.sent, []); sid = self.db.execute('SELECT id FROM email_suppressions').fetchone()[0]
        self.assertEqual(self.post(ed, f'/suppressions/{sid}/release', {}).status_code, 422); self.assertEqual(self.post(ed, f'/suppressions/{sid}/release', {'reason': 'Verified with the customer'}).status_code, 200)
        self.emit('auth.email_verified', self.uid('c0@example.com'), 'y'); self.drain(); self.assertEqual(len(self.mock.sent), 1)


class TestReminders(AdminBase):
    def run_reminders(self, now, only=None):
        with self.app.app_context(): return reminders.run(self.db, self.m, now=now, only=only)
    def setUp(self):
        super().setUp(); self.c = self.signup(verify=False); self.uid_ = self.uid('a@example.com'); self.db.execute('DELETE FROM email_outbox')
        self.db.execute("UPDATE users SET created_at='2026-10-18T10:00:00Z' WHERE id=?", (self.uid_,))

    def test_verification_reminders_after_24h_then_final_at_72h_with_fresh_links(self):
        self.assertEqual(self.run_reminders('2026-10-18T20:00:00Z')['evt:reminder.verify_email']['sent'], 0)                    # only 10h old
        self.run_reminders('2026-10-19T12:00:00Z'); self.assertEqual([r['template_id'] for r in self.rows()], ['EMAIL_VERIFY_REMINDER_001'])
        self.run_reminders('2026-10-19T13:00:00Z'); self.assertEqual(len(self.rows()), 1)                                           # no duplicate
        self.run_reminders('2026-10-21T12:00:00Z'); self.assertEqual([r['template_id'] for r in self.rows()], ['EMAIL_VERIFY_REMINDER_001', 'EMAIL_VERIFY_FINAL_001'])
        self.run_reminders('2026-10-30T12:00:00Z'); self.assertEqual(len(self.rows()), 2)                                           # attempts exhausted: no more
        self.drain(); tok = re.search(r'token=([\w-]+)', self.mock.sent[-1].text).group(1); self.assertEqual(self.req(self.c, 'post', '/api/auth/verify-email', {'token': tok}).status_code, 200)

    def test_reminders_stop_when_the_condition_is_resolved(self):
        self.run_reminders('2026-10-19T12:00:00Z'); self.db.execute('UPDATE users SET email_verified=1'); self.db.execute('DELETE FROM email_outbox')
        res = self.run_reminders('2026-10-21T12:00:00Z'); self.assertEqual([r for r in self.rows() if 'VERIFY' in r['template_id']], []); self.assertEqual(res['evt:reminder.verify_email_final']['sent'], 0)
        self.assertEqual(self.db.execute("SELECT stop_reason FROM email_reminder_state WHERE rule_code='evt:reminder.verify_email'").fetchone()[0], 'condition_resolved')

    def test_quiet_hours_follow_the_customers_time_zone_and_only_postpone(self):
        self.run_reminders('2026-10-19T23:30:00Z'); self.assertEqual(self.rows(), [])                                              # 23:30 UTC is quiet (21:00-08:00)
        self.run_reminders('2026-10-20T09:00:00Z'); self.assertEqual(len(self.rows()), 1)                                           # postponed, not cancelled
        self.db.execute('DELETE FROM email_outbox'); self.db.execute('DELETE FROM email_reminder_state'); prefs.set_timezone(self.db, self.uid_, 'Asia/Tokyo')
        self.run_reminders('2026-10-19T12:00:00Z'); self.assertEqual(self.rows(), [])                                              # 12:00 UTC = 21:00 in Tokyo: quiet for THIS customer
        prefs.set_timezone(self.db, self.uid_, 'America/New_York'); self.run_reminders('2026-10-19T12:00:00Z'); self.assertEqual(len(self.rows()), 1)       # 08:00 in New York: allowed

    def test_kyc_reminders_skip_customers_already_under_review(self):
        self.db.execute('UPDATE users SET email_verified=1'); self.db.execute("UPDATE users SET kyc_status='not_started'"); self.run_reminders('2026-10-21T12:00:00Z', only='reminder.kyc_incomplete'); self.assertEqual([r['template_id'] for r in self.rows()], ['KYC_COMPLETE_001'])
        self.db.execute('DELETE FROM email_outbox'); self.db.execute('DELETE FROM email_reminder_state'); self.db.execute("UPDATE users SET kyc_status='under_review'")
        self.run_reminders('2026-10-21T12:00:00Z', only='reminder.kyc_incomplete'); self.assertEqual(self.rows(), [])

    def test_attempt_limit_and_interval(self):
        self.db.execute('UPDATE users SET email_verified=1'); self.db.execute("UPDATE users SET kyc_status='not_started'")
        for day, want in (('2026-10-21T12:00:00Z', 1), ('2026-10-22T12:00:00Z', 1), ('2026-10-29T13:00:00Z', 2), ('2026-11-06T12:00:00Z', 3), ('2026-12-01T12:00:00Z', 3)):          # max 3, one week apart
            self.run_reminders(day, only='reminder.kyc_incomplete'); self.assertEqual(len(self.rows()), want, day)

    def test_info_outstanding_reminder_carries_the_staff_request(self):
        self.db.execute('UPDATE users SET email_verified=1'); self.db.execute("UPDATE users SET kyc_status='requires_info'"); self.db.execute("UPDATE kyc_profiles SET status='requires_info', info_request='Upload proof of address', reviewed_at='2026-10-10T10:00:00Z'")
        self.run_reminders('2026-10-21T12:00:00Z', only='reminder.kyc_info_outstanding'); self.drain(); self.assertIn('proof of address', self.mock.sent[0].text)

    def test_reengagement_is_off_by_default_and_needs_consent(self):
        self.db.execute('UPDATE users SET email_verified=1, last_login_at=?', ('2026-08-01T10:00:00Z',)); self.run_reminders('2026-10-21T12:00:00Z'); self.assertEqual(self.rows(template='REENGAGE_001'), [])        # rule disabled
        self.db.execute("UPDATE email_automation_rules SET enabled=1 WHERE event_type='reminder.reengagement'"); self.run_reminders('2026-10-21T12:00:00Z'); self.assertEqual(self.rows(template='REENGAGE_001'), [])    # no consent
        prefs.set_marketing(self.db, self.uid_, True, 't'); self.run_reminders('2026-10-21T12:00:00Z'); self.assertEqual(len(self.rows(template='REENGAGE_001')), 1); self.drain()
        self.assertIn('List-Unsubscribe', self.mock.sent[-1].headers)

    def test_disabled_reminder_sends_nothing_and_admin_can_run_and_configure_via_api(self):
        ed = self.staff('communications_admin'); rid = self.db.execute("SELECT id FROM email_automation_rules WHERE event_type='reminder.verify_email'").fetchone()[0]
        self.assertEqual(self.req(ed, 'patch', f'{A}/automations/{rid}', {'enabled': False}).status_code, 200); self.run_reminders('2026-10-19T12:00:00Z', only='reminder.verify_email'); self.assertEqual(self.rows(), [])
        self.assertEqual(self.req(ed, 'patch', f'{A}/automations/{rid}', {'params': {'max_attempts': 99}}).status_code, 422); self.assertEqual(self.req(ed, 'patch', f'{A}/automations/{rid}', {'enabled': True, 'delay_minutes': 600}).status_code, 200)
        wid = self.db.execute("SELECT id FROM email_automation_rules WHERE event_type='withdrawal.completed'").fetchone()[0]
        self.assertEqual(self.req(ed, 'patch', f'{A}/automations/{wid}', {'delay_minutes': 60}).status_code, 422)                              # money/security mail is never delayed
        self.assertEqual(self.req(ed, 'patch', f'{A}/automations/{wid}', {'enabled': False}).status_code, 422)                                 # and cannot be switched off without a recorded reason
        self.assertEqual(self.req(ed, 'patch', f'{A}/automations/{wid}', {'enabled': False, 'reason': 'Provider outage'}).status_code, 200)
        a = ed.get(A + '/automations').json; self.assertTrue(a['items']); self.assertTrue(any(g['template'] == 'INVESTMENT_MATURED_001' for g in a['no_backend_trigger']))           # gaps are listed, not faked


class TestLogsAndOverview(AdminBase):
    def test_overview_separates_provider_acceptance_from_confirmed_delivery(self):
        self.signup(); self.signup('b@example.com'); self.drain(); aud = self.staff('auditor'); o = aud.get(A + '/overview').json['counts']; self.assertEqual((o['accepted_by_provider'], o['confirmed_delivered']), (4, 0))
        mid = self.rows(to='a@example.com')[0]['provider_message_id']; self.webhook(self.client(), {'type': 'email.delivered', 'created_at': '2026-10-21T10:00:00Z', 'data': {'email_id': mid}}, msg_id='e1')
        o = aud.get(A + '/overview').json['counts']; self.assertEqual((o['accepted_by_provider'], o['confirmed_delivered']), (4, 1))

    def test_overview_says_plainly_when_real_delivery_is_not_configured(self):
        from server.mailer.transports import DevTransport
        self.m.transport = DevTransport(); self.signup(); self.drain(); o = self.staff('auditor').get(A + '/overview').json
        self.assertFalse(o['provider']['live_delivery']); self.assertIn('NOT configured', o['note']); self.assertEqual((o['counts']['dev_captured'], o['counts']['confirmed_delivered']), (2, 0))

    def test_outbox_masks_addresses_hides_secrets_and_audits_content_views(self):
        self.signup(); self.drain(); aud = self.staff('auditor'); lst = aud.get(A + '/outbox').json; blob = json.dumps(lst)
        self.assertNotIn('a@example.com', blob); self.assertIn('a***@example.com', blob); self.assertNotRegex(blob, r'token|secret_json|action_url')
        eid = self.rows(template='EMAIL_VERIFY_001')[0]['id']; d = aud.get(A + f'/outbox/{eid}').json; self.assertNotRegex(json.dumps(d), r'token=|a@example\.com'); self.assertEqual(d['status'], 'accepted'); self.assertIn('Inbox delivery is not confirmed', d['status_explained'])
        pv = aud.get(A + f'/outbox/{eid}/preview').json; self.assertNotIn('token=', pv['html'] + pv['text']); self.assertIn('ARCHIVE VIEW', pv['html'])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM audit_logs WHERE action='email.message_viewed'").fetchone()[0], 1)

    def test_outbox_filters_and_retry_cancel_controls(self):
        self.signup(); self.mock.plan.append(Result(False, retryable=False, code='422', error='no')); self.drain(); ops = self.staff('super_admin'); self.assertEqual(ops.get(A + '/outbox?status=failed').json['total'], 1)
        fid = self.rows(status='failed')[0]['id']; r = self.req(ops, 'post', f'{A}/outbox/{fid}/retry')
        self.assertIn(r.status_code, (200, 422))                                                                                     # welcome mail has no one-time link: may be retried
        self.assertEqual(self.req(ops, 'post', f'{A}/outbox/{fid}/cancel').status_code in (200, 409), True)
        vid = self.rows(template='EMAIL_VERIFY_001')[0]['id']; self.assertEqual(ops.get(A + f'/outbox?template=EMAIL_VERIFY_001').json['items'][0]['id'], vid)

    def test_queue_process_and_settings_validate_input(self):
        ops = self.staff('super_admin'); self.signup(); self.assertEqual(self.req(ops, 'post', A + '/queue/process', {}).json['processed'], 2)
        for bad in ({'email_max_attempts': 99}, {'nope': 1}, {'email_bulk_approval_threshold': 'abc'}): self.assertEqual(self.req(ops, 'put', A + '/settings', {'settings': bad}).status_code, 422)
        self.assertEqual(self.req(ops, 'put', A + '/settings', {'settings': {'email_bulk_approval_threshold': 10}}).status_code, 200); st = ops.get(A + '/settings').json
        self.assertNotRegex(json.dumps(st), r'whsec_|AKIA|re_[A-Za-z0-9]{10}'); self.assertEqual(st['settings']['email_bulk_approval_threshold']['value'], '10')

    def test_provider_status_never_reveals_credentials(self):
        self.app.config.update(EMAIL_PROVIDER_KEY='re_supersecretkey1234567890', AWS_SECRET_ACCESS_KEY='awssecret123'); blob = json.dumps(self.staff('super_admin').get(A + '/settings').json) + json.dumps(self.m.status())
        self.assertNotIn('supersecretkey', blob); self.assertNotIn('awssecret', blob); self.assertNotIn('whsec_', blob)


class TestHygiene(unittest.TestCase):
    def test_no_credentials_or_provider_keys_hardcoded(self):
        pats = [r're_[A-Za-z0-9]{20,}', r'AKIA[0-9A-Z]{16}', r'whsec_[A-Za-z0-9+/=]{16,}', r'sk_live_', r'-----BEGIN (RSA |EC )?PRIVATE KEY']
        for path in glob.glob(os.path.join(ROOT, 'server', '**', '*.py'), recursive=True) + glob.glob(os.path.join(ROOT, 'web', '**', '*.js'), recursive=True) + [os.path.join(ROOT, '.env.example')]:
            src = open(path, encoding='utf-8', errors='ignore').read()
            for p in pats: self.assertIsNone(re.search(p, src), f'{path} matches {p}')

    def test_email_code_reads_credentials_only_from_configuration(self):
        src = open(os.path.join(ROOT, 'server', 'mailer', 'transports.py')).read(); self.assertNotRegex(src, r"Bearer ['\"]re_"); self.assertIn("'Bearer ' + self.key", src)

    def test_never_emails_secrets_by_design(self):
        for t in cat.CATALOG:
            blob = json.dumps(t['spec']).lower() + t['subject'].lower()
            for word in ('seed phrase', 'recovery phrase', 'private key', 'your password is', 'one-time code is', 'totp secret'): self.assertNotIn(word, blob.replace('never ask for your password, one-time codes or recovery phrases', ''), t['id'])


class TestResponsiveRendering(unittest.TestCase):
    def test_no_horizontal_scroll_and_tappable_button_on_mobile_and_desktop(self):
        try: from playwright.sync_api import sync_playwright
        except ImportError: self.skipTest('playwright not installed')
        from server.mailer import render as rnd
        t = cat.BY_ID['WITHDRAWAL_COMPLETED_001']; ctx = {**rnd.SAMPLE, 'destination': '…VERYLONGDESTINATIONVALUE0123456789', 'transaction_reference': 'WDR-ABCDEF1234567890ABCDEF'}
        html = rnd.render(t['subject'], t['spec'], ctx, base_url=BASE, inline_logo_root=os.path.join(ROOT, 'web')).html; path = '/tmp/_email_responsive.html'; open(path, 'w').write(html)
        try:
            with sync_playwright() as p:
                b = p.chromium.launch()
                for w in (320, 375, 414, 768, 1280):
                    pg = b.new_page(viewport={'width': w, 'height': 800}); pg.goto('file://' + path)
                    self.assertLessEqual(pg.evaluate('document.documentElement.scrollWidth'), w, f'horizontal scroll at {w}px')
                    card = pg.evaluate("(()=>{const t=[...document.querySelectorAll('table')].find(x=>x.style.maxWidth==='600px'&&x.style.backgroundColor);return t.getBoundingClientRect().width})()"); self.assertLessEqual(card, 600.5)
                    if w <= 414: self.assertGreaterEqual(pg.evaluate("document.querySelector('a.btn').getBoundingClientRect().height"), 44)
                    pg.close()
                b.close()
        except Exception as e:
            if 'Executable' in str(e) or 'browserType.launch' in str(e): self.skipTest('chromium unavailable')
            raise


if __name__ == '__main__':
    unittest.main()
