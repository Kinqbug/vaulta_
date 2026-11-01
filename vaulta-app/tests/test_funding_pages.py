"""Separate Deposit / Withdraw pages: configurable page copy, fee breakdown, server-side 'withdraw all', address validation, network management, and the
admin -> customer propagation of everything the two pages display. Real app, real database, mock chain only."""
import io, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_crypto_requests import RB, png
from server import crypto_withdrawals as cwd, assets as reg


class FP(RB):
    def setUp(self):
        super().setUp(); self.cops = self.staff('crypto_operations', 'cops@vaulta.test'); self.an = self.info[self.CODE]['an_id']
    def cfg(self, **b): return self.req(self.cops, 'post', f'/api/admin/crypto/asset-networks/{self.an}/config', {'reason': 'configure page content for test', **b})
    def fund(self, u, amt=None):
        src = self.verify_sender(u, self.CODE); self.intent(u, self.CODE); self.pay(self.CODE, amt or 200_000_000, src); self.scan()
    def dest(self): return self.sender(self.CODE, 'dest')


class TestPageCopyIsConfigurable(FP):
    def test_defaults_are_generic_and_non_promissory(self):
        c, u = self.customer(); j = c.get('/api/crypto/assets').json
        d = [a for a in j['assets'] if a['ledger_code'] == self.CODE][0]; w = [a for a in j['withdrawal_assets'] if a['ledger_code'] == self.CODE][0]
        self.assertEqual(d['estimated_arrival'], 'About 12 network confirmations'); self.assertEqual(d['logo_alt'], 'Tether logo'); self.assertEqual(d['instructions'], '')
        self.assertIn('reviewed by our team', w['processing_time']); self.assertNotIn('guarantee', w['processing_time'].lower()); self.assertIn('0x', w['address_hint'])
    def test_admin_text_reaches_both_customer_pages_immediately(self):
        c, u = self.customer()
        r = self.cfg(estimated_arrival='5-10 minutes (estimate)', processing_time='Reviewed within 24 hours', deposit_instructions='Send USDT on Ethereum only.', withdrawal_instructions='Double-check the address.', network_notice='Network congestion possible.')
        self.assertEqual(r.status_code, 200, r.json); self.assertEqual(r.json['network']['processing_time'], 'Reviewed within 24 hours')
        j = c.get('/api/crypto/assets').json; d = [a for a in j['assets'] if a['ledger_code'] == self.CODE][0]; w = [a for a in j['withdrawal_assets'] if a['ledger_code'] == self.CODE][0]
        self.assertEqual((d['estimated_arrival'], d['instructions'], d['network_notice']), ('5-10 minutes (estimate)', 'Send USDT on Ethereum only.', 'Network congestion possible.'))
        self.assertEqual((w['processing_time'], w['instructions'], w['estimated_arrival']), ('Reviewed within 24 hours', 'Double-check the address.', '5-10 minutes (estimate)'))
        self.cfg(estimated_arrival=''); d = [a for a in c.get('/api/crypto/assets').json['assets'] if a['ledger_code'] == self.CODE][0]; self.assertEqual(d['estimated_arrival'], 'About 12 network confirmations')     # clearing restores the default
        self.assertTrue(self.one("SELECT 1 FROM audit_logs WHERE action='crypto.asset_network_config'"))
    def test_copy_is_length_limited_stripped_and_permission_checked(self):
        c, u = self.customer()
        self.assertEqual(self.cfg(deposit_instructions='x' * 601).status_code, 422); self.assertEqual(self.cfg(processing_time='y' * 161).json['error']['code'], 'too_long')
        self.cfg(network_notice='ok\x00\x07text'); self.assertEqual(self.one('SELECT network_notice FROM asset_networks WHERE id=?', self.an)[0], 'oktext')
        self.assertEqual(self.req(c, 'post', f'/api/admin/crypto/asset-networks/{self.an}/config', {'network_notice': 'hi', 'reason': 'customer must not do this'}).status_code, 403)
    def test_asset_notice_and_logo_alt_text(self):
        c, u = self.customer(); aid = self.one("SELECT id FROM crypto_assets WHERE symbol='USDT'")[0]
        r = self.cops.post(f'/api/admin/crypto/assets/{aid}', data={'logo_alt': 'Tether coin', 'notice': 'Only USDT.'}, content_type='multipart/form-data', headers={'X-CSRF-Token': self.cops.csrf}); self.assertEqual(r.status_code, 200, r.json)
        a = [x for x in c.get('/api/crypto/assets').json['assets'] if x['symbol'] == 'USDT'][0]; self.assertEqual((a['logo_alt'], a['notice']), ('Tether coin', 'Only USDT.'))
    def test_new_asset_with_copy_needs_no_code_change(self):
        c, u = self.customer(); d = {'symbol': 'DOGE', 'name': 'Dogecoin', 'type': 'native', 'visible': 'true', 'network_code': 'ethereum', 'decimals': '8', 'min_withdrawal': '1', 'activate': 'true', 'withdrawal_instructions': 'Doge guidance'}
        r = self.cops.post('/api/admin/crypto/assets', data={**d, 'logo': (io.BytesIO(png(64, 64, seed=3)), 'l.png')}, content_type='multipart/form-data', headers={'X-CSRF-Token': self.cops.csrf}); self.assertEqual(r.status_code, 201, r.json)
        self.assertEqual(r.json['asset']['networks'][0]['withdrawal_instructions'], 'Doge guidance'); self.assertTrue(r.json['asset']['logo'])


class TestWithdrawalInfoAndQuote(FP):
    def test_withdrawal_info_uses_real_balance_and_withdraw_all_is_exact(self):
        c, u = self.customer(); self.fund(u)
        self.cfg(min_withdrawal='1', fee_flat='1', fee_percent='2'); i = c.get(f'/api/crypto/withdrawal-info?ledger_code={self.CODE}').json
        self.assertEqual((i['available_usd'], i['rate'], i['fee_fixed'], i['fee_percent'], i['can_withdraw']), ('200.00', '1.0000', '1.000000', '2', True))       # the Account Balance, in dollars
        top = i['max_amount']; q = c.get(f'/api/crypto/withdrawal-quote?ledger_code={self.CODE}&amount={top}').json
        self.assertEqual(q['problems'], []); self.assertLessEqual(float(q['usd_debit']), 200.0); self.assertEqual(q['available_usd'], '200.00')
        over = c.get(f'/api/crypto/withdrawal-quote?ledger_code={self.CODE}&amount=%s' % (float(top) + 0.01)).json; self.assertIn('insufficient_funds', [p['code'] for p in over['problems']])    # one cent more would not fit
        r = self.req(c, 'post', '/api/crypto/withdrawals', {'ledger_code': self.CODE, 'amount': top, 'destination': self.dest(), 'code': (self.totp(u['id']), self.code(u['id']))[1]}); self.assertEqual(r.status_code, 201, r.json)
        self.assertLess(self.cash(u), 5)       # (almost) nothing left: under a nickel of dust at most
        self.assertEqual(self.one('SELECT usd_debit_cents FROM crypto_withdrawals ORDER BY id DESC LIMIT 1')[0], 20_000 - self.cash(u))      # exactly the dollars debited are recorded
    def test_max_amount_never_exceeds_balance_for_many_fee_models(self):
        info = {'withdrawal_fee_policy': '{}', 'min_withdrawal': None, 'max_withdrawal': None}
        import json
        for flat in (0, 1, 7, 1000):
            for bps in (0, 1, 25, 150, 2000):
                for bal in (0, 1, 999, 100_000, 123_456_789):
                    info['withdrawal_fee_policy'] = json.dumps({'flat_minor': flat, 'bps': bps}); a = cwd.max_amount(info, bal)
                    self.assertGreaterEqual(a, 0); self.assertTrue(a == 0 or a + cwd.fee_for(info, a) <= bal, (flat, bps, bal, a))
                    if a: self.assertGreater(a + 1 + cwd.fee_for(info, a + 1), bal, (flat, bps, bal, a))     # and it is the LARGEST such amount
    def test_max_amount_respects_configured_cap(self):
        c, u = self.customer(); self.fund(u); self.cfg(max_withdrawal='50', fee_flat='0'); self.assertEqual(c.get(f'/api/crypto/withdrawal-info?ledger_code={self.CODE}').json['max_amount'], '50.000000')
    def test_quote_breakdown_adds_up_and_flags_problems(self):
        c, u = self.customer(); self.fund(u); self.cfg(min_withdrawal='10', max_withdrawal='100', fee_flat='1', fee_percent='2')
        q = c.get(f'/api/crypto/withdrawal-quote?ledger_code={self.CODE}&amount=50').json
        self.assertEqual((q['fee_fixed'], q['fee_percent_amount'], q['fee'], q['total_debit'], q['receives'], q['problems']), ('1.000000', '1.000000', '2.000000', '52.000000', '50.000000', []))
        codes = lambda amt: [p['code'] for p in c.get(f'/api/crypto/withdrawal-quote?ledger_code={self.CODE}&amount={amt}').json['problems']]
        self.assertEqual(codes('5'), ['below_minimum']); self.assertEqual(codes('101'), ['above_maximum']); self.cfg(max_withdrawal=''); self.assertIn('insufficient_funds', codes('199'))
        for bad in ('0', '-1', 'abc'): self.assertEqual(c.get(f'/api/crypto/withdrawal-quote?ledger_code={self.CODE}&amount={bad}').status_code, 422)
    def test_disabled_network_reports_unavailable_everywhere(self):
        c, u = self.customer(); self.fund(u); self.cfg(withdrawal_enabled=False)
        self.assertIn('asset_unavailable', [p['code'] for p in c.get(f'/api/crypto/withdrawal-quote?ledger_code={self.CODE}&amount=5').json['problems']])
        i = c.get(f'/api/crypto/withdrawal-info?ledger_code={self.CODE}').json; self.assertEqual((i['available_for_withdrawal'], i['can_withdraw']), (False, False))
        self.assertEqual(self.wdreq(c, u, '5').json['error']['code'], 'asset_unavailable')
    def wdreq(self, c, u, amt): self.totp(u['id']); return self.req(c, 'post', '/api/crypto/withdrawals', {'ledger_code': self.CODE, 'amount': amt, 'destination': self.dest(), 'code': self.code(u['id'])})
    def test_requires_login(self):
        for url in (f'/api/crypto/withdrawal-info?ledger_code={self.CODE}', f'/api/crypto/validate-address?ledger_code={self.CODE}&address=x'): self.assertEqual(self.client().get(url).status_code, 401)


class TestAddressValidation(FP):
    def v(self, c, addr, code=None): return c.get(f'/api/crypto/validate-address?ledger_code={code or self.CODE}&address={addr}').json
    def test_format_per_network_and_wrong_family_hint(self):
        c, u = self.customer(); ok = self.v(c, self.dest()); self.assertTrue(ok['valid']); self.assertIn('cannot be reversed', ok['message'])
        bad = self.v(c, 'not-an-address'); self.assertEqual((bad['valid'], bad['code']), (False, 'invalid_address'))
        btc = self.v(c, self.sender('BTC', 'x') if 'BTC' in self.info else 'tb1q', self.CODE); self.assertFalse(btc['valid'])
        self.assertFalse(self.v(c, '')['valid'])
    def test_vaulta_own_address_is_refused(self):
        c, u = self.customer(); r = self.v(c, self.wal[self.CODE]['public_address']); self.assertEqual((r['valid'], r['code']), (False, 'company_address'))
    def test_withdrawal_request_uses_the_same_check(self):
        c, u = self.customer(); self.fund(u); self.totp(u['id'])
        r = self.req(c, 'post', '/api/crypto/withdrawals', {'ledger_code': self.CODE, 'amount': '5', 'destination': 'junk', 'code': self.code(u['id'])}); self.assertEqual((r.status_code, r.json['error']['code']), (422, 'invalid_address'))
        self.assertEqual(self.cash(u), 20_000)       # nothing reserved or converted


class TestNetworkManagement(FP):
    def test_create_list_update_and_audit(self):
        c, u = self.customer()
        r = self.req(self.cops, 'post', '/api/admin/crypto/blockchain-networks', {'network_code': 'polygon', 'name': 'Polygon', 'chain_type': 'account', 'confirmations_required': 30, 'address_format': 'evm', 'is_testnet': True}); self.assertEqual(r.status_code, 201, r.json)
        n = r.json['network']; self.assertEqual((n['status'], n['usable'], n['assets']), ('disabled', True, []))
        self.assertEqual(self.req(self.cops, 'post', '/api/admin/crypto/blockchain-networks', {'network_code': 'polygon', 'name': 'Dup', 'chain_type': 'account', 'confirmations_required': 5, 'address_format': 'evm'}).json['error']['code'], 'exists')
        self.assertEqual(self.req(self.cops, 'post', '/api/admin/crypto/blockchain-networks', {'network_code': 'weird', 'name': 'W', 'chain_type': 'account', 'confirmations_required': 5}).json['error']['code'], 'invalid_address_format')
        self.assertEqual(self.req(self.cops, 'post', f"/api/admin/crypto/blockchain-networks/{n['id']}", {'name': 'Polygon PoS', 'confirmations_required': 64}).json['error']['code'], 'reason_required')
        r = self.req(self.cops, 'post', f"/api/admin/crypto/blockchain-networks/{n['id']}", {'name': 'Polygon PoS', 'confirmations_required': 64, 'reason': 'rename and raise finality'}); self.assertEqual((r.json['network']['name'], r.json['network']['confirmations_required']), ('Polygon PoS', 64))
        self.assertIn('polygon', [x['network_code'] for x in self.cops.get('/api/admin/crypto/blockchain-networks').json['networks']]); self.assertTrue(self.one("SELECT 1 FROM audit_logs WHERE action='crypto.network_updated'"))
    def test_customers_cannot_manage_networks_and_nothing_is_offered_until_enabled(self):
        c, u = self.customer(); self.assertEqual(c.get('/api/admin/crypto/blockchain-networks').status_code, 403); self.assertEqual(self.req(c, 'post', '/api/admin/crypto/blockchain-networks', {}).status_code, 403)
        self.req(self.cops, 'post', '/api/admin/crypto/blockchain-networks', {'network_code': 'arb', 'name': 'Arb', 'chain_type': 'account', 'confirmations_required': 10, 'address_format': 'evm'})
        self.assertNotIn('arb', {a['network'] for a in c.get('/api/crypto/assets').json['assets'] + c.get('/api/crypto/assets').json['withdrawal_assets']})
    def test_suspending_a_network_removes_it_from_new_transactions_only(self):
        c, u = self.customer(); self.fund(u); nid = self.one("SELECT id FROM blockchain_networks WHERE network_code='ethereum'")[0]
        r = self.req(self.cops, 'post', f'/api/admin/crypto/blockchain-networks/{nid}', {'status': 'suspended', 'reason': 'provider incident, pausing new activity'}); self.assertEqual(r.status_code, 200, r.json)
        j = c.get('/api/crypto/assets').json; self.assertNotIn('ethereum', {a['network'] for a in j['assets'] + j['withdrawal_assets']})
        self.assertEqual(self.cash(u), 20_000)          # balances and history are untouched


class TestNavigationLinks(FP):
    def test_withdrawal_notifications_open_the_withdraw_page(self):
        c, u = self.customer(); self.fund(u); self.totp(u['id'])
        self.assertEqual(self.req(c, 'post', '/api/crypto/withdrawals', {'ledger_code': self.CODE, 'amount': '5', 'destination': self.dest(), 'code': self.code(u['id'])}).status_code, 201)
        self.assertEqual(self.one("SELECT link FROM notifications WHERE type='withdrawal' ORDER BY id DESC LIMIT 1")[0], '#/withdraw')


if __name__ == '__main__': unittest.main()
