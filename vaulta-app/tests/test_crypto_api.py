"""HTTP-level crypto tests: customer flow, admin permissions, QR correctness, environment gating."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_crypto import CryptoBase, ASSETS
from server import qr, crypto_setup as cs, crypto_wallets as cw, crypto_recon as rc, util, verify, security as sec
from server.app import create_app
try:
    import cv2, numpy as np
except ImportError: cv2 = None

def decode_svg_text(text):
    M = qr.encode(text); n = len(M); s, q = 8, 4; img = np.full(((n + 2 * q) * s,) * 2, 255, np.uint8)
    for y in range(n):
        for x in range(n):
            if M[y][x]: img[(y + q) * s:(y + q + 1) * s, (x + q) * s:(x + q + 1) * s] = 0
    return cv2.QRCodeDetector().detectAndDecode(img)[0]

class TestCustomerApi(CryptoBase):
    def setUp(self):
        super().setUp()
        for sym, p in (('USDT', '1.0000'), ('ETH', '3000.0000'), ('BTC', '60000.0000'), ('SOL', '150.0000'), ('USDC', '1.0000')):
            self.db.execute('UPDATE assets SET last_price=?,prev_close=? WHERE symbol=?', (p, p, sym)); aid = self.db.execute('SELECT id FROM assets WHERE symbol=?', (sym,)).fetchone()[0]       # CryptoBase already created the asset rows
            self.db.execute('INSERT OR REPLACE INTO asset_prices(asset_id,ts,price) VALUES(?,?,?)', (aid, util.now(), p))
    def test_assets_listing_shows_five_assets_and_no_internal_fields(self):
        c = self.signup(kyc=True); r = c.get('/api/crypto/assets'); self.assertEqual(r.status_code, 200)
        self.assertEqual([a['symbol'] for a in r.json['assets']], ['BTC', 'ETH', 'USDT', 'USDC', 'SOL']); usdt = [a for a in r.json['assets'] if a['symbol'] == 'USDT'][0]
        self.assertEqual((usdt['network'], usdt['minimum_deposit'], usdt['required_confirmations']), ('ethereum', '10.000000', 12)); self.assertNotIn('public_address', usdt)
        self.assertEqual(self.client().get('/api/crypto/assets').status_code, 401)
    def test_end_to_end_customer_flow_portfolio_and_history(self):
        c = self.signup(kyc=True); code = 'USDT.ETHEREUM'; uid = self.uid('a@example.com')
        i = self.req(c, 'post', '/api/crypto/deposit-intents', {'ledger_code': code}).json['intent']
        self.assertEqual(i['deposit_address'], self.wal[code]['public_address']); self.assertEqual((i['network'], i['required_confirmations'], i['minimum_deposit']), ('ethereum', 12, '10.000000')); self.assertIn('permanent loss', i['warning'])
        self.assertFalse(i['attribution']['will_auto_credit'])
        adr = self.sender(code, 'api'); s = self.req(c, 'post', '/api/crypto/source-addresses', {'network': 'ethereum', 'address': adr}).json['address']
        sig = self.req(c, 'post', '/api/dev/crypto/sign-challenge', {'address': adr, 'challenge': s['challenge']}).json['signature']
        self.assertEqual(self.req(c, 'post', f"/api/crypto/source-addresses/{s['id']}/verify", {'signature': 'forged'}).status_code, 422)
        self.assertEqual(self.req(c, 'post', f"/api/crypto/source-addresses/{s['id']}/verify", {'signature': sig}).json['address']['status'], 'verified')
        r = self.req(c, 'post', '/api/dev/crypto/simulate-deposit', {'ledger_code': code, 'amount': '250.00', 'confirmations': 3}); self.assertEqual(r.status_code, 200, r.json)
        h = c.get('/api/crypto/deposits').json['deposits']; self.assertEqual((h[0]['asset'], h[0]['amount'], h[0]['network'], h[0]['status_label']), ('USDT', '250.000000', 'Ethereum (test network)', 'Confirming'))
        self.req(c, 'post', '/api/dev/crypto/mine', {'network': 'ethereum', 'blocks': 9}); h = c.get('/api/crypto/deposits').json['deposits']; self.assertEqual(h[0]['status'], 'credited')
        self.assertEqual(c.get('/api/crypto/balances').json['balances'], [])                      # converted at once: the customer holds dollars, not crypto
        pf = c.get('/api/portfolio').json; self.assertEqual((pf['holdings'], pf['summary']['holdings_value'], pf['summary']['portfolio_value']), ([], '0.00', '0.00'))
        self.assertEqual(c.get('/api/dashboard').json['summary']['available_cash'], '250.00')      # the deposit is in the Account Balance
        self.assertEqual([a for a in pf['allocation'] if a['label'] == 'Crypto'][0]['value'], '0.00')
        self.assertEqual(self.liability({'id': uid}, code), 0); self.assertEqual(self.cash({'id': uid}), 25_000); self.assert_gl()
    def test_unverified_simulated_payment_is_held_not_credited(self):
        c = self.signup(kyc=True); self.req(c, 'post', '/api/crypto/deposit-intents', {'ledger_code': 'ETH'})
        self.req(c, 'post', '/api/dev/crypto/simulate-deposit', {'ledger_code': 'ETH', 'amount': '0.1', 'confirmations': 12})
        self.assertEqual(c.get('/api/crypto/balances').json['balances'], []); self.assertEqual(c.get('/api/crypto/deposits').json['deposits'], [])     # not provably theirs, so not shown as theirs
        ops = self.staff('crypto_operations', 'ops@vaulta.test'); d = ops.get('/api/admin/crypto/deposits?status=manual_review').json['deposits']; self.assertEqual(len(d), 1); self.assertIsNone(d[0]['user_id'])
    def test_kyc_and_validation_errors(self):
        c = self.signup('n@example.com'); r = self.req(c, 'post', '/api/crypto/deposit-intents', {'ledger_code': 'ETH'}); self.assertEqual((r.status_code, r.json['error']['code']), (403, 'kyc_required'))
        k = self.signup(kyc=True); self.assertEqual(self.req(k, 'post', '/api/crypto/deposit-intents', {'ledger_code': 'DOGE'}).status_code, 404)
        self.assertEqual(self.req(k, 'post', '/api/crypto/deposit-intents', {'ledger_code': 'BTC', 'expected_amount': '0.00000001'}).json['error']['code'], 'below_minimum')
        self.assertEqual(self.req(k, 'post', '/api/crypto/source-addresses', {'network': 'ethereum', 'address': self.sender('SOL', 'x')}).status_code, 422)
        self.assertEqual(self.req(k, 'post', '/api/crypto/deposit-intents', {'ledger_code': 'ETH', 'expected_amount': 0.1}).status_code, 422)     # floats refused
    def test_secret_material_is_refused_everywhere(self):
        c = self.signup(kyc=True)
        for url in ('/api/crypto/deposit-intents', '/api/crypto/source-addresses'): self.assertEqual(self.req(c, 'post', url, {'ledger_code': 'ETH', 'private_key': 'abc'}).json['error']['code'], 'secret_material_refused')
    def test_customer_cannot_see_other_customers_intents_or_qr(self):
        a = self.signup('a@example.com', kyc=True); b = self.signup('b@example.com', kyc=True); ref = self.req(a, 'post', '/api/crypto/deposit-intents', {'ledger_code': 'ETH'}).json['intent']['ref']
        self.assertEqual(b.get(f'/api/crypto/deposit-intents/{ref}/qr.svg').status_code, 404); self.assertEqual(self.req(b, 'post', f'/api/crypto/deposit-intents/{ref}/cancel').status_code, 404)
        self.assertEqual(a.get(f'/api/crypto/deposit-intents/{ref}/qr.svg').status_code, 200); self.assertEqual(b.get('/api/crypto/deposit-intents').json['intents'], [])
    def test_withdrawal_over_http_requires_2fa_and_is_idempotent(self):
        c = self.signup(kyc=True); code = 'USDT.ETHEREUM'; u = {'id': self.uid('a@example.com')}
        src = self.verify_sender(u, code); self.intent(u, code); self.pay(code, 100_000_000, src); self.scan(); dest = self.sender(code, 'dest')
        r = self.req(c, 'post', '/api/crypto/withdrawals', {'ledger_code': code, 'amount': '10', 'destination': dest}); self.assertEqual(r.json['error']['code'], 'two_factor_setup_required')
        self.totp(u['id']); h = {'Idempotency-Key': 'w1'}; body = {'ledger_code': code, 'amount': '10', 'destination': dest, 'code': self.code(u['id'])}
        a = self.req(c, 'post', '/api/crypto/withdrawals', body, headers=h); self.assertEqual(a.status_code, 201, a.json); b = self.req(c, 'post', '/api/crypto/withdrawals', body, headers=h)
        self.assertEqual(a.json['withdrawal']['ref'], b.json['withdrawal']['ref']); self.assertEqual(self.bal(u, code), 90_000_000)

class TestQr(unittest.TestCase):
    @unittest.skipUnless(cv2, 'OpenCV not installed')
    def test_qr_decodes_to_exactly_the_address(self):
        for a in ('0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed', 'bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq', 'tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx', '1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2',
                  'So11111111111111111111111111111111111111112', 'bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0'): self.assertEqual(decode_svg_text(a), a)
    @unittest.skipUnless(cv2, 'OpenCV not installed')
    def test_qr_many_lengths(self):
        import random, string; rnd = random.Random(7)
        for n in range(1, 200, 9): t = ''.join(rnd.choice(string.ascii_letters + string.digits) for _ in range(n)); self.assertEqual(decode_svg_text(t), t, n)
    def test_svg_is_well_formed_and_script_free(self):
        s = qr.svg('0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed'); self.assertTrue(s.startswith('<svg')); self.assertNotIn('<script', s); self.assertNotIn('onload', s)
        with self.assertRaises(ValueError): qr.svg('x' * 300)

class TestAdminApi(CryptoBase):
    def test_permissions_matrix(self):
        sup = self.staff('support_admin', 'sup@vaulta.test'); fin = self.staff('finance_admin', 'fin@vaulta.test'); ops = self.staff('crypto_operations', 'ops@vaulta.test'); aud = self.staff('auditor', 'aud@vaulta.test'); cust = self.signup(kyc=True)
        g = lambda c, u: c.get(u).status_code
        for u in ('/api/admin/crypto/wallets', '/api/admin/crypto/deposits', '/api/admin/crypto/reconciliation', '/api/admin/crypto/withdrawals'): self.assertEqual(g(sup, u), 403, u); self.assertEqual(g(cust, u), 403, u)
        self.assertEqual(g(fin, '/api/admin/crypto/reconciliation'), 200); self.assertEqual(g(fin, '/api/admin/crypto/wallets'), 403); self.assertEqual(g(ops, '/api/admin/crypto/wallets'), 200)
        self.assertEqual(g(aud, '/api/admin/crypto/wallets'), 200); self.assertEqual(self.req(aud, 'post', '/api/admin/crypto/reconciliation/run').status_code, 403)
        self.assertEqual(self.req(ops, 'post', '/api/admin/crypto/wallets/change-requests/WCR-X/approve', {'code': '1'}).status_code, 403)     # ops can propose, never approve
    def test_wallet_change_over_http_two_people(self):
        a = self.staff('super_admin', 'adm1@vaulta.test'); b = self.staff('super_admin', 'adm2@vaulta.test'); ua, ub = self.uid('adm1@vaulta.test'), self.uid('adm2@vaulta.test'); self.totp(ua); self.totp(ub)
        an = self.info['SOL']['an_id']; new = cs.demo_address('solana', 'new-sol')
        r = self.req(a, 'post', '/api/admin/crypto/wallets/change-requests', {'asset_network_id': an, 'address': new, 'label': 'Vaulta SOL wallet', 'reason': 'annual rotation of the SOL deposit wallet', 'code': self.code(ua)}); self.assertEqual(r.status_code, 201, r.json)
        ref = r.json['ref']; self.assertEqual(self.req(a, 'post', f'/api/admin/crypto/wallets/change-requests/{ref}/approve', {'code': self.code(ua)}).json['error']['code'], 'separation_of_duties')
        self.assertEqual(self.req(b, 'post', f'/api/admin/crypto/wallets/change-requests/{ref}/approve', {'code': self.code(ub)}).status_code, 200)
        w = [x for x in a.get('/api/admin/crypto/wallets').json['wallets'] if x['asset'] == 'SOL']; self.assertEqual((w[0]['public_address'], w[0]['status']), (new, 'inactive'))
        self.assertTrue(any(e['event_type'] == 'wallet_address_changed' for e in a.get('/api/admin/crypto/wallets/history').json['events']))
        bad = self.req(a, 'post', '/api/admin/crypto/wallets/change-requests', {'asset_network_id': an, 'address': self.sender('BTC', 'x'), 'label': 'x', 'reason': 'wrong chain attempt on purpose', 'code': self.code(ua)}); self.assertEqual(bad.json['error']['code'], 'invalid_address')
    def test_deposit_review_and_recon_endpoints(self):
        h = self.pay('ETH', 10**17, '0x' + 'a' * 40); self.scan(); ops = self.staff('crypto_operations', 'ops@vaulta.test'); fin = self.staff('finance_admin', 'fin@vaulta.test')
        d = ops.get('/api/admin/crypto/deposits?status=manual_review').json['deposits']; self.assertEqual(len(d), 1); self.assertTrue(d[0]['status_reason'].startswith('unmatched'))
        det = ops.get(f"/api/admin/crypto/deposits/{d[0]['id']}").json; self.assertTrue(det['events']); self.assertEqual(self.req(ops, 'post', f"/api/admin/crypto/deposits/{d[0]['id']}/resolve", {'action': 'reject', 'reason': 'x'}).status_code, 422)
        r = self.req(fin, 'post', '/api/admin/crypto/reconciliation/run'); self.assertEqual(r.status_code, 200); self.assertEqual(r.json['status'], 'clean', r.json)
        self.assertEqual(self.req(ops, 'post', f"/api/admin/crypto/deposits/{d[0]['id']}/resolve", {'action': 'reject', 'reason': 'sender could not be identified'}).json['deposit']['status'], 'rejected')

class TestCryptoOnlyFunding(CryptoBase):
    def test_legacy_bank_and_card_funding_is_off_by_default(self):
        self.db.execute("UPDATE system_settings SET value='0' WHERE key='fiat_funding_enabled'"); c = self.signup(kyc=True)
        r = self.req(c, 'post', '/api/deposits', {'amount': '100', 'method': 'bank_transfer'}); self.assertEqual((r.status_code, r.json['error']['code']), (409, 'crypto_only'))
        r = self.req(c, 'post', '/api/withdrawals', {'amount': '10', 'method': 'bank_transfer', 'destination': 'x'}); self.assertEqual(r.json['error']['code'], 'crypto_only')
        self.assertEqual(c.get('/api/crypto/assets').status_code, 200)
    def test_admin_enabling_the_setting_restores_legacy_funding(self):
        self.db.execute("UPDATE system_settings SET value='1' WHERE key='fiat_funding_enabled'"); c = self.signup(kyc=True)
        self.assertNotEqual(self.req(c, 'post', '/api/deposits', {'amount': '100', 'method': 'bank_transfer'}).json.get('error', {}).get('code'), 'crypto_only')
    def test_standard_deposit_and_withdrawal_roles_manage_crypto(self):
        self.pay('ETH', 10**17, '0x' + 'a' * 40); self.scan()
        ops = self.staff('operations_admin', 'ops@vaulta.test'); comp = self.staff('compliance_admin', 'comp@vaulta.test'); sup = self.staff('support_admin', 'sup@vaulta.test')
        self.assertEqual(ops.get('/api/admin/crypto/deposits').status_code, 200); self.assertEqual(ops.get('/api/admin/crypto/withdrawals').status_code, 200)
        d = ops.get('/api/admin/crypto/deposits?status=manual_review').json['deposits'][0]
        self.assertEqual(self.req(ops, 'post', f"/api/admin/crypto/deposits/{d['id']}/resolve", {'action': 'reject', 'reason': 'sender could not be identified'}).status_code, 200)
        self.assertEqual(comp.get('/api/admin/crypto/withdrawals').status_code, 200); self.assertEqual(self.req(comp, 'post', '/api/admin/crypto/withdrawals/1/approve', {}).status_code, 403)
        self.assertEqual(sup.get('/api/admin/crypto/deposits').status_code, 403); self.assertEqual(sup.get('/api/admin/crypto/withdrawals').status_code, 403)
    def test_admin_overview_counts_crypto_work_queue(self):
        self.pay('ETH', 10**17, '0x' + 'b' * 40); self.scan(); ops = self.staff('operations_admin', 'ops@vaulta.test'); m = ops.get('/api/admin/overview').json['metrics']
        self.assertGreaterEqual(int(m['pending_deposits']), 1)

class TestEnvironmentGating(CryptoBase):
    def test_dev_endpoints_do_not_exist_outside_development_mock(self):
        for env, mode in (('staging', 'testnet'), ('production', 'live')):
            app = create_app({'DATABASE_PATH': os.path.join(self.tmp, f'{env}.db'), 'UPLOAD_DIR': os.path.join(self.tmp, 'u2'), 'RATE_LIMIT_ENABLED': False, 'ENV': env, 'CRYPTO_PROVIDER_MODE': mode, 'COOKIE_SECURE': False})
            c = app.test_client(); r = c.post('/api/auth/register', json={'email': 'z@example.com', 'password': 'CorrectHorse42', 'first_name': 'a', 'last_name': 'b', 'accepted_terms': True, 'terms_version': 1, 'privacy_version': 1}); c.csrf = r.json['csrf_token']
            self.assertEqual(self.req(c, 'post', '/api/dev/crypto/simulate-deposit', {'ledger_code': 'ETH', 'amount': '1'}).status_code, 404, env)
            self.assertTrue(c.get('/api/crypto/config').json['is_test_environment'] or env == 'production'); self.assertEqual(c.get('/api/crypto/config').json['provider_mode'], mode)
    def test_config_banner_in_development(self):
        c = self.signup(); j = c.get('/api/crypto/config').json; self.assertTrue(j['is_test_environment']); self.assertIn('TEST ENVIRONMENT', j['banner']); self.assertFalse(j['real_funds_enabled'])
    def test_production_database_with_test_networks_refuses_to_start(self):
        with self.assertRaises(RuntimeError) as e: cs.startup_checks(self.db, 'production', 'live')
        self.assertIn('test networks', str(e.exception))
    def test_verify_behaviour_checks_pass(self):
        r = verify.Report(); verify.behaviour_checks(r); self.assertEqual(r.failed, 0, '\n'.join(r.out))
    def test_verify_fails_honestly_on_placeholders(self):
        self.db.execute("UPDATE company_crypto_wallets SET status='retired',retired_at='x' WHERE asset_network_id=?", (self.info['BTC']['an_id'],))
        cw.install_wallet(self.db, an_id=self.info['BTC']['an_id'], address='NOT_CONFIGURED', label='x', allow_placeholder=True)
        r = verify.Report(); verify.config_checks(r, self.db, self.P, 'development', 'mock'); txt = '\n'.join(r.out); self.assertGreater(r.failed, 0); self.assertIn('NOT_CONFIGURED placeholder', txt)

if __name__ == '__main__': unittest.main()
