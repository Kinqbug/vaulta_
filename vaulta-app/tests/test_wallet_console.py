"""Backend tests for the Wallets & Reconciliation and Asset Settings pages: read models, QR, explorer URL, logo upload, record derivation,
authorisation, and that asset configuration really reaches the customer-facing deposit flow."""
import os, sys, io, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_crypto import CryptoBase
from test_crypto_api import decode_svg_text, cv2
from server import crypto_wallets as cw, wallet_console as wc, crypto_recon as rc, util
from server.util import ApiError

def png(color=(10, 10, 10), size=64):
    from PIL import Image
    b = io.BytesIO(); Image.new('RGB', (size, size), color).save(b, 'PNG'); return b.getvalue()

class TestWalletConsole(CryptoBase):
    def setUp(self):
        super().setUp(); self.ops = self.staff('super_admin', 'root@vaulta.test')
        self.btc_id = self.db.execute("SELECT id FROM crypto_assets WHERE symbol='BTC'").fetchone()[0]
    def A(self, path): return self.ops.get('/api/admin/crypto' + path)
    def upload(self, path, data, files=None):
        return self.ops.post('/api/admin/crypto' + path, data={**data, **({'logo': (io.BytesIO(files[0]), files[1])} if files else {})}, headers={'X-CSRF-Token': self.ops.csrf}, content_type='multipart/form-data')

    # ---- authorisation
    def test_every_new_endpoint_requires_authentication_and_a_permission(self):
        urls = ['/wallets/overview', f'/wallets/{self.wal["BTC"]["id"]}/qr.svg', '/reconciliation/records', '/reconciliation/summary', f'/assets/{self.btc_id}/settings']
        for u in urls: self.assertEqual(self.client().get('/api/admin/crypto' + u).status_code, 401, u)
        cust = self.signup('c@example.com', kyc=True); sup = self.staff('support', 'sup@vaulta.test')
        for u in urls:
            self.assertEqual(cust.get('/api/admin/crypto' + u).status_code, 403, u); self.assertEqual(sup.get('/api/admin/crypto' + u).status_code, 403, u)
        r = self.req(sup, 'post', f'/api/admin/crypto/blockchain-networks/1/explorer', {'explorer_url': 'https://x.example', 'reason': 'valid reason here'}); self.assertEqual(r.status_code, 403)

    # ---- overview
    def test_overview_comes_from_real_records_and_never_fakes_balances(self):
        j = self.A('/wallets/overview').json; s = j['summary']
        self.assertEqual((s['total_wallets'], s['configured_wallets']), (5, 5)); self.assertIsNone(s['last_reconciliation']); self.assertEqual(s['pending_confirmations'], 0)
        self.assertNotIn('private', str(j).lower())
        btc = [w for w in j['wallets'] if w['asset'] == 'BTC'][0]; self.assertEqual(btc['balance_source'], 'test_chain'); self.assertEqual(btc['available'], '0.00000000')
        # a provider that cannot answer is reported as unavailable, never as zero or as a made-up number
        orig = self.P.chain.get_balance
        def boom(*a, **k): raise ApiError(503, 'provider_unavailable', 'Chain provider not connected')
        self.P.chain.get_balance = boom
        try: j = self.A('/wallets/overview').json
        finally: self.P.chain.get_balance = orig
        btc = [w for w in j['wallets'] if w['asset'] == 'BTC'][0]; self.assertIsNone(btc['available']); self.assertEqual(btc['balance_source'], 'unavailable'); self.assertIn('not connected', btc['balance_issue'])
        self.assertIsNone(btc['fiat_estimate']); self.assertIsNone(j['summary']['estimated_total_usd'])
    def test_pending_confirmations_and_last_reconciliation_reflect_activity(self):
        u = self.U(); src = self.verify_sender(u, 'USDT.ETHEREUM'); self.intent(u, 'USDT.ETHEREUM')
        self.chain.fund('ethereum', self.wal['USDT.ETHEREUM']['public_address'], 100_000_000, sender=src, contract=self.info['USDT.ETHEREUM']['contract_address']); self.chain.mine('ethereum', 2); self.scan()
        self.assertEqual(self.A('/wallets/overview').json['summary']['pending_confirmations'], 1)
        rc.reconcile(self.db, self.P, None); lr = self.A('/wallets/overview').json['summary']['last_reconciliation']; self.assertIsNotNone(lr)

    # ---- QR
    def test_qr_is_generated_from_the_stored_address_and_decodes_to_it(self):
        w = self.wal['BTC']; r = self.A(f'/wallets/{w["id"]}/qr.svg'); self.assertEqual((r.status_code, r.mimetype), (200, 'image/svg+xml')); self.assertEqual(r.headers['Cache-Control'], 'no-store')
        if cv2 is not None: self.assertEqual(decode_svg_text(w['public_address']), w['public_address'])
        self.assertIn('<svg', r.get_data(as_text=True)); self.assertNotIn('attachment', r.headers.get('Content-Disposition', ''))
        d = self.A(f'/wallets/{w["id"]}/qr.svg?download=1'); self.assertIn('attachment', d.headers['Content-Disposition'])
    def test_qr_refused_for_unknown_wallets_and_unconfigured_placeholders(self):
        w = self.wal['BTC']; self.assertEqual(wc.wallet_qr_payload(self.db, self.P, w['id'])[0], w['public_address'])
        self.db.execute('DROP TRIGGER IF EXISTS ccw_identity_immutable'); self.db.execute("UPDATE company_crypto_wallets SET public_address='NOT_CONFIGURED' WHERE id=?", (w['id'],))
        self.assertEqual(self.A(f'/wallets/{w["id"]}/qr.svg').status_code, 409)
        self.assertEqual(self.A('/wallets/999999/qr.svg').status_code, 404)

    # ---- explorer URL + asset settings read model
    def test_asset_settings_returns_networks_usage_wallets_and_history(self):
        j = self.A(f'/assets/{self.btc_id}/settings').json; a = j['asset']; n = a['networks'][0]
        self.assertEqual((a['symbol'], n['network_code'], n['classification']), ('BTC', 'bitcoin', 'native')); self.assertIn('usage', n); self.assertFalse(n['usage']['deposits']); self.assertTrue(n['usage']['wallets'])
        self.assertTrue(any(w['qr_available'] for w in j['wallets'])); self.assertTrue(j['history']); self.assertEqual(self.A('/assets/99999/settings').status_code, 404)
    def test_explorer_url_is_validated_audited_and_persisted(self):
        nid = self.A(f'/assets/{self.btc_id}/settings').json['asset']['networks'][0]['network_id']; u = f'/api/admin/crypto/blockchain-networks/{nid}/explorer'
        for bad in ('http://insecure.example', 'javascript:alert(1)', 'https://user:pw@x.example', 'https://nodot', 'https://a b.example', 'ftp://x.example'):
            self.assertEqual(self.req(self.ops, 'post', u, {'explorer_url': bad, 'reason': 'valid reason here'}).status_code, 422, bad)
        self.assertEqual(self.req(self.ops, 'post', u, {'explorer_url': 'https://explorer.example/', 'reason': 'short'}).status_code, 422)
        r = self.req(self.ops, 'post', u, {'explorer_url': 'https://explorer.example/', 'reason': 'valid reason here'}); self.assertEqual(r.json['explorer_url'], 'https://explorer.example')
        self.assertEqual(self.A(f'/assets/{self.btc_id}/settings').json['asset']['networks'][0]['explorer_url'], 'https://explorer.example')

    # ---- asset update + logo upload, used everywhere
    def test_logo_upload_replace_remove_and_validation(self):
        r = self.upload(f'/assets/{self.btc_id}', {'name': 'Bitcoin'}, (png((200, 100, 0)), 'btc.png')); self.assertEqual(r.status_code, 200, r.json); l1 = r.json['asset']['logo']; self.assertIn('/api/crypto/logos/', l1)
        c = self.signup('c@example.com', kyc=True); self.assertEqual(c.get(l1.split('?')[0]).status_code, 200)           # customers see the configured image
        self.assertEqual([a['logo'] for a in c.get('/api/crypto/assets').json['assets'] if a['symbol'] == 'BTC'][0], l1)
        r = self.upload(f'/assets/{self.btc_id}', {}, (png((0, 100, 200)), 'new.png')); l2 = r.json['asset']['logo']; self.assertNotEqual(l1, l2)      # replaced: new cache-busting URL, same asset
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM crypto_assets WHERE symbol='BTC'").fetchone()[0], 1)
        for name, data in (('evil.png', b'<?php system($_GET[0]); ?>'), ('exe.png', b'MZ\x90\x00' + b'\x00' * 100), ('empty.png', b'')):
            self.assertIn(self.upload(f'/assets/{self.btc_id}', {}, (data, name)).status_code, (413, 415, 422), name)
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64"><script>alert(1)</script><circle cx="32" cy="32" r="30" onclick="alert(2)"/></svg>'
        r = self.upload(f'/assets/{self.btc_id}', {}, (svg, 'x.svg'))
        if r.status_code == 200:
            body = c.get(r.json['asset']['logo'].split('?')[0]).get_data(as_text=True).lower(); self.assertNotIn('<script', body); self.assertNotIn('onclick', body)
        r = self.req(self.ops, 'post', f'/api/admin/crypto/assets/{self.btc_id}', {'remove_logo': True}); self.assertIsNone(r.json['asset']['logo'])
    def test_asset_edit_validation_status_needs_reason_and_secrets_refused(self):
        p = f'/api/admin/crypto/assets/{self.btc_id}'
        self.assertEqual(self.req(self.ops, 'post', p, {'name': ''}).status_code, 422); self.assertEqual(self.req(self.ops, 'post', p, {'description': 'x' * 601}).status_code, 422)
        self.assertEqual(self.req(self.ops, 'post', p, {'status': 'suspended'}).status_code, 422)
        r = self.req(self.ops, 'post', p, {'status': 'suspended', 'reason': 'maintenance window today'}); self.assertEqual((r.status_code, r.json['asset']['status']), (200, 'suspended'))
        self.assertEqual(self.req(self.ops, 'post', p, {'name': 'Bitcoin', 'seed_phrase': 'a b c'}).status_code, 400)
        r = self.req(self.ops, 'post', p, {'name': 'Bitcoin Core', 'description': 'Digital currency.', 'status': 'active', 'reason': 'maintenance finished'}); self.assertEqual(self.A(f'/assets/{self.btc_id}/settings').json['asset']['description'], 'Digital currency.')

    # ---- configuration reaches the customer flow
    def test_minimum_deposit_and_switches_reach_the_customer_deposit_flow(self):
        c = self.signup('c@example.com', kyc=True); an = self.info['BTC']['an_id']
        r = self.req(self.ops, 'post', f'/api/admin/crypto/asset-networks/{an}/config', {'min_deposit': '0.5', 'confirmations': '7', 'reason': 'raise the floor for testing'}); self.assertEqual(r.status_code, 200, r.json)
        btc = [a for a in c.get('/api/crypto/assets').json['assets'] if a['symbol'] == 'BTC'][0]; self.assertEqual((btc['minimum_deposit'], btc['required_confirmations']), ('0.50000000', 7))
        self.assertEqual(self.req(c, 'post', '/api/crypto/deposit-intents', {'ledger_code': 'BTC', 'expected_amount': '0.1'}).json['error']['code'], 'below_minimum')
        r = self.req(self.ops, 'post', f'/api/admin/crypto/asset-networks/{an}/config', {'deposit_enabled': False, 'reason': 'pause deposits only'}); self.assertEqual(r.status_code, 200)
        self.assertNotEqual(self.req(c, 'post', '/api/crypto/deposit-intents', {'ledger_code': 'BTC'}).status_code, 201)                       # deposits off: no new deposit
        n = self.A(f'/assets/{self.btc_id}/settings').json['asset']['networks'][0]; self.assertFalse(n['deposit_enabled']); self.assertTrue(n['withdrawal_enabled'])    # withdrawals untouched
        self.assertEqual(self.req(self.ops, 'post', f'/api/admin/crypto/asset-networks/{an}/config', {'min_deposit': '5', 'max_deposit': '1', 'reason': 'inverted limits'}).status_code, 422)

    # ---- address change workflow is still two-person and never silent
    def test_address_change_needs_a_second_administrator(self):
        c1 = self.staff('super_admin', 'a1@vaulta.test'); a1 = {'id': self.uid('a1@vaulta.test')}; self.totp(a1['id']); an = self.info['BTC']['an_id']; old = self.wal['BTC']['public_address']
        new = __import__('server.crypto_setup', fromlist=['x']).demo_address('btc', 'newaddr|1', True)
        r = self.req(c1, 'post', '/api/admin/crypto/wallets/change-requests', {'asset_network_id': an, 'address': new, 'label': 'Rotated', 'reason': 'quarterly rotation', 'code': self.code(a1['id'])}); self.assertEqual(r.status_code, 201, r.json)
        self.assertEqual(cw.live_wallet(self.db, an)['public_address'], old)                                                                   # customers still see the old address
        j = self.A('/wallets/overview').json; self.assertEqual(len(j['pending_changes']), 1); self.assertEqual(j['pending_changes'][0]['requested_by_email'], 'a1@vaulta.test')
        ref = r.json['ref']; r2 = self.req(c1, 'post', f'/api/admin/crypto/wallets/change-requests/{ref}/approve', {'code': self.code(a1['id'])}); self.assertGreaterEqual(r2.status_code, 400)   # not the requester

    # ---- reconciliation records are derived, filterable and honest
    def test_records_derive_status_from_real_deposits(self):
        u = self.U(); code = 'USDT.ETHEREUM'; src = self.verify_sender(u, code); self.intent(u, code)
        h = self.pay(code, 100_000_000, src, confs=2); self.scan(); j = self.A('/reconciliation/records').json; self.assertEqual(j['total'], 1)
        r = j['records'][0]; self.assertEqual((r['type'], r['asset'], r['status'], r['tx_hash']), ('deposit', 'USDT', 'confirming', h.lower() if h.startswith('0x') else h)); self.assertEqual(r['chain_amount'], '100.000000'); self.assertIsNone(r['ledger_amount'])
        self.chain.mine('ethereum', 12); self.scan(); r = self.A('/reconciliation/records').json['records'][0]; self.assertEqual((r['status'], r['ledger_amount'], r['chain_amount']), ('reconciled', '100.000000', '100.000000'))
        self.assertEqual(self.A('/reconciliation/records?status=mismatch').json['total'], 0); self.assertEqual(self.A('/reconciliation/records?asset=ETH').json['total'], 0); self.assertEqual(self.A('/reconciliation/records?type=withdrawal').json['total'], 0)
        self.assertEqual(self.A('/reconciliation/records?network=ethereum&type=deposit&asset=USDT').json['total'], 1); self.assertEqual(self.A('/reconciliation/records?from=2999-01-01').json['total'], 0)
        self.assertEqual(self.A(f'/reconciliation/records?q={r["tx_hash"][:10]}').json['total'], 1); s = self.A('/reconciliation/summary').json; self.assertEqual((s['total_records'], s['reconciled'], s['problems']), (1, 1, 0))
    def test_a_credit_that_differs_from_the_chain_is_a_mismatch_never_forced_to_reconciled(self):
        u = self.U(); code = 'USDT.ETHEREUM'; src = self.verify_sender(u, code); self.intent(u, code); h = self.pay(code, 100_000_000, src); self.scan()
        d = self.dep(h); self.assertEqual(d['status'], 'credited')
        self.db.execute('DROP TRIGGER IF EXISTS cd_identity_immutable'); self.db.execute('UPDATE crypto_deposits SET amount=? WHERE id=?', ('90000000', d['id']))      # simulate a corrupted record
        r = self.A('/reconciliation/records').json['records'][0]; self.assertEqual(r['status'], 'mismatch'); self.assertIn('differs', r['discrepancy'])
        self.assertEqual(self.ops.put(f'/api/admin/crypto/reconciliation/records/1', json={'status': 'reconciled'}, headers={'X-CSRF-Token': self.ops.csrf}).status_code, 405)   # there is no endpoint to force a status

if __name__ == '__main__': unittest.main()
