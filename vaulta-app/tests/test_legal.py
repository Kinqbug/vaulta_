"""Legal documents, versioning, acceptance evidence, RBAC, risk gate, cookie consent."""
import sqlite3, unittest
from test_vaulta import Base, PW
from server import legal, db as dbm
from server.audit import verify_chain

class LegalBase(Base):
    def admin(self, role='super_admin', email=None): return self.staff(role, email)
    def reg(self, email='n@example.com', **over):
        b = {'email': email, 'password': PW, 'first_name': 'T', 'last_name': 'U', 'accepted_terms': True, 'terms_version': 1, 'privacy_version': 1}; b.update(over)
        return self.client().post('/api/auth/register', json=b)
    def new_version(self, admin, slug, content, note='Update', material=False, publish=True):
        r = self.req(admin, 'post', f'/api/admin/legal/documents/{slug}/drafts'); self.assertEqual(r.status_code, 201, r.json); vid = r.json['id']
        r = self.req(admin, 'put', f'/api/admin/legal/versions/{vid}', {'content': content, 'change_note': note, 'material': material}); self.assertEqual(r.status_code, 200, r.json)
        if publish:
            r = self.req(admin, 'post', f'/api/admin/legal/versions/{vid}/publish', {'confirm': True}); self.assertEqual(r.status_code, 200, r.json)
        return vid

class TestPublicDocuments(LegalBase):
    def test_all_six_documents_published_and_complete(self):
        c = self.client(); docs = c.get('/api/legal/documents').json['documents']
        self.assertEqual([d['slug'] for d in docs], ['terms', 'privacy', 'disclosures', 'risk-disclosure', 'accessibility', 'cookies'])
        for d in docs:
            j = c.get(f"/api/legal/documents/{d['slug']}").json['document']
            self.assertTrue(j['html'] and j['toc'] and j['version'] == 1, d['slug'])
        t = c.get('/api/legal/documents/terms').json['document']
        self.assertIn('Cryptocurrency Withdrawals', t['html']); self.assertEqual(len(t['toc']), 23)
        self.assertIn('Smart Contract and Protocol Risk', c.get('/api/legal/documents/risk-disclosure').json['document']['html'])
    def test_placeholders_flagged_until_configured(self):
        j = self.client().get('/api/legal/documents/privacy').json['document']
        self.assertTrue(j['needs_completion']); self.assertIn('LEGAL ENTITY NAME', j['unresolved']); self.assertIn('lg-missing', j['html'])
        a = self.admin(); self.assertEqual(self.req(a, 'put', '/api/admin/legal/config', {'config': {'legal_entity_name': 'Acme Ltd', 'privacy_email': 'privacy@acme.example'}}).status_code, 200)
        j = self.client().get('/api/legal/documents/privacy').json['document']
        self.assertIn('Acme Ltd', j['html']); self.assertNotIn('LEGAL ENTITY NAME', j['unresolved']); self.assertIn('PRIVACY EMAIL' not in j['unresolved'] and 'privacy@acme.example', j['html'])
    def test_invalid_and_unknown_slugs(self):
        c = self.client()
        for s in ('nope', '..%2fetc', 'Terms', 'x'): self.assertEqual(c.get(f'/api/legal/documents/{s}').status_code, 404, s)
        self.assertEqual(c.get('/api/legal/documents/terms?version=abc').status_code, 422); self.assertEqual(c.get('/api/legal/documents/terms?version=99').status_code, 404)
    def test_drafts_never_public(self):
        a = self.admin(); vid = self.new_version(a, 'terms', '### Draft only\n\nSECRET DRAFT TEXT', publish=False)
        c = self.client(); self.assertNotIn('SECRET DRAFT', c.get('/api/legal/documents/terms').json['document']['html'])
        self.assertEqual(c.get('/api/legal/documents/terms?version=2').status_code, 404)
        self.assertEqual(c.get(f'/api/admin/legal/versions/{vid}').status_code, 401)
    def test_content_is_sanitised(self):
        a = self.admin(); self.new_version(a, 'cookies', '### Hi <script>alert(1)</script>\n\n[bad](javascript:alert(1)) <img src=x onerror=alert(1)> [ok](https://example.com)\n\n**fine**')
        h = self.client().get('/api/legal/documents/cookies').json['document']['html']
        self.assertNotIn('<script', h); self.assertNotIn('<img', h); self.assertNotIn('javascript:', h); self.assertIn('&lt;script&gt;', h); self.assertIn('href="https://example.com"', h)
    def test_seed_is_idempotent_and_never_overwrites(self):
        a = self.admin(); self.new_version(a, 'terms', '### Changed\n\nNew terms text that is long enough.', note='edit')
        self.assertEqual(legal.ensure_seed(self.db), []); self.assertIn('Changed', self.client().get('/api/legal/documents/terms').json['document']['html'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM legal_documents').fetchone()[0], 6)

class TestVersioningAndRbac(LegalBase):
    def test_publish_preserves_history_and_records_actor(self):
        a = self.admin(); old = self.db.execute("SELECT id FROM legal_versions WHERE status='PUBLISHED' AND document_id=(SELECT id FROM legal_documents WHERE slug='terms')").fetchone()[0]
        self.new_version(a, 'terms', '### New\n\nVersion two content, long enough.', note='Clarified wording')
        j = self.client().get('/api/legal/documents/terms').json['document']; self.assertEqual(j['version'], 2); self.assertTrue(j['is_current'])
        old_j = self.client().get('/api/legal/documents/terms?version=1').json['document']; self.assertEqual(old_j['status'], 'ARCHIVED'); self.assertIn('Cryptocurrency Deposits', old_j['html'])
        vs = self.req(a, 'get', '/api/admin/legal/documents/terms/versions').json['versions']; self.assertEqual([v['status'] for v in vs], ['PUBLISHED', 'ARCHIVED'])
        self.assertTrue(vs[0]['published_by']['email'].startswith('super_admin'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM legal_versions WHERE document_id=(SELECT document_id FROM legal_versions WHERE id=?) AND status=?', (old, 'PUBLISHED')).fetchone()[0], 1)
        self.assertTrue(verify_chain(self.db)['ok']); self.assertTrue(self.db.execute("SELECT 1 FROM audit_logs WHERE action='legal.published'").fetchone())
    def test_db_blocks_rewriting_published_versions(self):
        for sql in ("UPDATE legal_versions SET content='x' WHERE status='PUBLISHED'", "UPDATE legal_versions SET title='x' WHERE status='PUBLISHED'", 'DELETE FROM legal_versions', 'DELETE FROM legal_documents',
                    "UPDATE legal_versions SET status='DRAFT' WHERE status='PUBLISHED'"):
            with self.assertRaises(sqlite3.DatabaseError, msg=sql): self.db.execute(sql)
    def test_permissions_enforced_on_backend(self):
        user = self.signup(); comp = self.admin('compliance_admin'); anon = self.client()
        for c in (anon, user): self.assertIn(self.req(c, 'get', '/api/admin/legal').status_code, (401, 403))
        self.assertEqual(self.req(user, 'post', '/api/admin/legal/documents/terms/drafts').status_code, 403)
        vid = self.new_version(comp, 'terms', '### Mine\n\nCompliance can draft but not publish this.', publish=False)      # legal.manage
        self.assertEqual(self.req(comp, 'post', f'/api/admin/legal/versions/{vid}/publish', {'confirm': True}).status_code, 403)               # no legal.publish
        self.assertEqual(self.req(user, 'post', f'/api/admin/legal/versions/{vid}/publish', {'confirm': True}).status_code, 403)
        ops = self.admin('support_admin'); self.assertEqual(self.req(ops, 'get', '/api/admin/legal').status_code, 403)
        sa = self.admin(); self.assertEqual(self.req(sa, 'post', f'/api/admin/legal/versions/{vid}/publish', {'confirm': True}).status_code, 200)
    def test_publish_validation(self):
        a = self.admin(); r = self.req(a, 'post', '/api/admin/legal/documents/terms/drafts'); vid = r.json['id']
        self.assertEqual(self.req(a, 'post', f'/api/admin/legal/versions/{vid}/publish', {}).json['error']['code'], 'confirmation_required')
        self.assertEqual(self.req(a, 'post', f'/api/admin/legal/versions/{vid}/publish', {'confirm': True}).json['error']['code'], 'change_note_required')
        self.assertEqual(self.req(a, 'put', f'/api/admin/legal/versions/{vid}', {'content': ''}).json['error']['code'], 'empty_content')
        self.assertEqual(self.req(a, 'put', f'/api/admin/legal/versions/{vid}', {'effective_at': 'tomorrow'}).json['error']['code'], 'invalid_date')
        self.assertEqual(self.req(a, 'put', f'/api/admin/legal/versions/{vid}', {'title': ' '}).json['error']['code'], 'invalid_title')
        self.assertEqual(self.req(a, 'post', '/api/admin/legal/documents/terms/drafts').json['error']['code'], 'draft_exists')
        self.assertEqual(self.req(a, 'post', '/api/admin/legal/versions/99999/publish', {'confirm': True}).status_code, 404)
    def test_published_version_cannot_be_edited_or_archived_when_required(self):
        a = self.admin(); pid = self.db.execute("SELECT id FROM legal_versions WHERE status='PUBLISHED' LIMIT 1").fetchone()[0]
        self.assertEqual(self.req(a, 'put', f'/api/admin/legal/versions/{pid}', {'content': 'rewritten history ' * 5}).json['error']['code'], 'not_draft')
        self.assertEqual(self.req(a, 'post', f'/api/admin/legal/versions/{pid}/archive').json['error']['code'], 'required_document')
    def test_draft_can_be_archived_and_supplement_created(self):
        a = self.admin(); vid = self.new_version(a, 'cookies', '### x\n\nDraft that will be discarded by an admin.', publish=False)
        self.assertEqual(self.req(a, 'post', f'/api/admin/legal/versions/{vid}/archive').status_code, 200)
        self.assertEqual(self.req(a, 'post', '/api/admin/legal/documents', {'slug': 'terms-eu', 'title': 'EU Supplement', 'region': 'EU', 'content': '### EU\n\nRegional supplement text here.'}).status_code, 201)
        self.assertNotIn('terms-eu', [d['slug'] for d in self.client().get('/api/legal/documents').json['documents']])         # still a draft
        v = self.db.execute("SELECT id FROM legal_versions WHERE document_id=(SELECT id FROM legal_documents WHERE slug='terms-eu')").fetchone()[0]
        self.assertEqual(self.req(a, 'post', f'/api/admin/legal/versions/{v}/publish', {'confirm': True}).status_code, 200)
        self.assertIn('terms-eu', [d['slug'] for d in self.client().get('/api/legal/documents').json['documents']])
        self.assertEqual(self.req(a, 'post', '/api/admin/legal/documents', {'slug': 'terms', 'title': 'x', 'content': 'y' * 30}).status_code, 409)
        self.assertEqual(self.req(a, 'post', '/api/admin/legal/documents', {'slug': 'Bad Slug', 'title': 'x', 'content': 'y' * 30}).status_code, 422)
    def test_production_blocks_publish_until_config_complete(self):
        self.app.config['ENV'] = 'production'; a = self.admin()
        vid = self.new_version(a, 'terms', '### P\n\nProduction publish attempt content.', publish=False)
        r = self.req(a, 'post', f'/api/admin/legal/versions/{vid}/publish', {'confirm': True}); self.assertEqual(r.json['error']['code'], 'legal_config_incomplete'); self.assertIn('legal_entity_name', r.json['error']['details']['missing'])
        st = self.req(a, 'get', '/api/admin/legal/status').json; self.assertFalse(st['ready']); self.assertTrue(st['missing_config'])
    def test_config_validation(self):
        a = self.admin(); put = lambda cfg: self.req(a, 'put', '/api/admin/legal/config', {'config': cfg})
        self.assertEqual(put({'privacy_email': 'not-an-email'}).json['error']['code'], 'invalid_email'); self.assertEqual(put({'website_url': 'http://x.com'}).json['error']['code'], 'invalid_url')
        self.assertEqual(put({'bogus': 'x'}).json['error']['code'], 'unknown_field'); self.assertEqual(put({'legal_entity_name': 'x' * 500}).json['error']['code'], 'too_long')
        self.assertEqual(put({'website_url': 'https://vaulta.example'}).status_code, 200)

class TestAcceptance(LegalBase):
    def test_registration_records_exact_versions_and_hash(self):
        r = self.reg(); self.assertEqual(r.status_code, 201, r.json); uid = self.uid('n@example.com')
        rows = self.db.execute('SELECT acceptance_type,document_version,policy_hash,accepted_at FROM legal_acceptances WHERE user_id=? ORDER BY acceptance_type', (uid,)).fetchall()
        self.assertEqual([x['acceptance_type'] for x in rows], ['PRIVACY_NOTICE_ACKNOWLEDGEMENT', 'TERMS_ACCEPTANCE']); self.assertTrue(all(x['document_version'] == 1 and len(x['policy_hash']) == 64 and x['accepted_at'] for x in rows))
        self.assertEqual(rows[1]['policy_hash'], self.db.execute("SELECT content_hash FROM legal_versions v JOIN legal_documents d ON d.id=v.document_id WHERE d.slug='terms' AND v.version=1").fetchone()[0])
    def test_registration_requires_acceptance_and_versions(self):
        self.assertEqual(self.reg(accepted_terms=False).json['error']['code'], 'terms_required'); self.assertEqual(self.reg(accepted_terms='yes').status_code, 422)
        b = {'email': 'q@example.com', 'password': PW, 'first_name': 'a', 'last_name': 'b', 'accepted_terms': True}
        self.assertEqual(self.client().post('/api/auth/register', json=b).json['error']['code'], 'legal_versions_required')
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM users WHERE email IN ('q@example.com','n@example.com')").fetchone()[0], 0)
    def test_stale_version_rejected_and_nothing_created(self):
        a = self.admin(); self.new_version(a, 'terms', '### v2\n\nUpdated terms content for acceptance.', material=True)
        r = self.reg(); self.assertEqual(r.status_code, 409); self.assertEqual(r.json['error']['code'], 'legal_version_changed'); self.assertEqual(r.json['error']['details']['terms_version'], 2)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM users WHERE email='n@example.com'").fetchone()[0], 0)
        self.assertEqual(self.reg(terms_version=2).status_code, 201)
        self.assertEqual(self.db.execute("SELECT document_version FROM legal_acceptances a JOIN users u ON u.id=a.user_id WHERE u.email='n@example.com' AND acceptance_type='TERMS_ACCEPTANCE'").fetchone()[0], 2)
    def test_acceptance_records_are_append_only(self):
        self.reg(); 
        with self.assertRaises(sqlite3.DatabaseError): self.db.execute('UPDATE legal_acceptances SET document_version=9')
        with self.assertRaises(sqlite3.DatabaseError): self.db.execute('DELETE FROM legal_acceptances')
    def test_acceptance_cannot_be_forged_for_another_user(self):
        a = self.signup('a@example.com', risk=False); b = self.signup('b@example.com', risk=False)
        r = self.req(a, 'post', '/api/legal/risk-acknowledgement', {'acknowledged': True, 'version': 1, 'user_id': self.uid('b@example.com'), 'accepted_at': '2001-01-01T00:00:00Z'}); self.assertEqual(r.status_code, 200)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM legal_acceptances WHERE user_id=? AND acceptance_type='RISK_DISCLOSURE_ACKNOWLEDGEMENT'", (self.uid('b@example.com'),)).fetchone()[0], 0)
        t = self.db.execute("SELECT accepted_at FROM legal_acceptances WHERE user_id=? AND acceptance_type='RISK_DISCLOSURE_ACKNOWLEDGEMENT'", (self.uid('a@example.com'),)).fetchone()[0]; self.assertNotEqual(t[:4], '2001')
        self.assertEqual(self.client().post('/api/legal/risk-acknowledgement', json={'acknowledged': True}).status_code, 401)
        self.assertEqual(self.req(a, 'post', '/api/legal/risk-acknowledgement', {'version': 1}).json['error']['code'], 'acknowledgement_required')

class TestRiskGate(LegalBase):
    def test_invest_and_trade_blocked_until_acknowledged(self):
        c = self.signup(kyc=True, risk=False); self.fund(c, '1000.00')
        r = self.req(c, 'post', '/api/investments', {'product_id': 1, 'amount': '100.00', 'accepted_disclosures': True}); self.assertEqual(r.status_code, 428); self.assertEqual(r.json['error']['code'], 'risk_ack_required')
        self.assertEqual(self.req(c, 'post', '/api/orders', {'symbol': 'AAPL', 'side': 'buy', 'order_type': 'market', 'quantity': '1'}).status_code, 428)
        self.assertEqual(self.req(c, 'get', '/api/legal/risk-status').json, {'required': True, 'acknowledged': False, 'version': 1, 'available': True})
        self.assertEqual(self.req(c, 'post', '/api/legal/risk-acknowledgement', {'acknowledged': True, 'version': 1}).json['acknowledged'], True)
        self.assertEqual(self.req(c, 'post', '/api/investments', {'product_id': 1, 'amount': '100.00', 'accepted_disclosures': True}).status_code, 201)
    def test_material_revision_requires_reacknowledgement_but_minor_does_not(self):
        c = self.signup(kyc=True); self.fund(c, '1000.00'); a = self.admin()
        self.new_version(a, 'risk-disclosure', '### Minor\n\nTypo fix only, wording unchanged in substance.', note='typo', material=False)
        self.assertTrue(self.req(c, 'get', '/api/legal/risk-status').json['acknowledged'])
        self.new_version(a, 'risk-disclosure', '### Material\n\nNew risk added that users must read again.', note='new risk', material=True)
        s = self.req(c, 'get', '/api/legal/risk-status').json; self.assertFalse(s['acknowledged']); self.assertEqual(s['version'], 3)
        self.assertEqual(self.req(c, 'post', '/api/investments', {'product_id': 1, 'amount': '100.00', 'accepted_disclosures': True}).status_code, 428)
        self.assertEqual(self.req(c, 'post', '/api/legal/risk-acknowledgement', {'acknowledged': True, 'version': 2}).json['error']['code'], 'legal_version_changed')
        self.assertEqual(self.req(c, 'post', '/api/legal/risk-acknowledgement', {'acknowledged': True, 'version': 3}).status_code, 200)
    def test_unrelated_endpoints_not_gated(self):
        c = self.signup(risk=False); self.assertEqual(self.req(c, 'get', '/api/investments').status_code, 200); self.assertEqual(self.req(c, 'get', '/api/legal/my-acceptances').status_code, 200)
        self.assertEqual({a['acceptance_type'] for a in self.req(c, 'get', '/api/legal/my-acceptances').json['acceptances']}, {'TERMS_ACCEPTANCE', 'PRIVACY_NOTICE_ACKNOWLEDGEMENT'})

class TestCookieConsent(LegalBase):
    def post(self, c, **b): return c.post('/api/legal/cookie-consent', json=b)
    def test_records_choice_minimally(self):
        c = self.client(); r = self.post(c, preferences={'analytics': True, 'marketing': False}, action='custom'); self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['preferences'], {'preferences': False, 'analytics': True, 'marketing': False}); self.assertEqual(r.json['policy_version'], 1); cid = r.json['consent_id']
        r = self.post(c, consent_id=cid, preferences={'analytics': True}, action='withdraw'); self.assertEqual(r.json['preferences'], {'preferences': False, 'analytics': False, 'marketing': False})
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM cookie_consents WHERE consent_id=?', (cid,)).fetchone()[0], 2)
        self.assertEqual({r[1] for r in self.db.execute('PRAGMA table_info(cookie_consents)')}, {'id', 'consent_id', 'preferences', 'action', 'policy_version', 'created_at'})      # no IP / user id
        self.assertEqual(self.post(c, preferences={}, action='accept_all').json['preferences'], {'preferences': True, 'analytics': True, 'marketing': True})
    def test_validation_and_json_only(self):
        c = self.client()
        self.assertEqual(self.post(c, preferences={}, action='bogus').status_code, 422); self.assertEqual(self.post(c, preferences='x', action='custom').status_code, 422)
        self.assertEqual(self.post(c, consent_id='short', preferences={}, action='custom').status_code, 422)
        self.assertEqual(c.post('/api/legal/cookie-consent', data='x=1').status_code, 400)

if __name__ == '__main__': unittest.main()
