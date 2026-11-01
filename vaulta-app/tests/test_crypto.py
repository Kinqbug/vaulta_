"""Crypto funding tests. Every test runs against the in-memory mock chain: no real funds, no real wallets, no keys."""
import os, sys, unittest, sqlite3
from decimal import Decimal
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_vaulta import Base, PW
from server import (ledger, crypto_setup as cs, crypto_deposits as cd, crypto_withdrawals as cwd, crypto_wallets as cw, crypto_recon as rc, crypto_ledger as cl, accounting as acc,
                    chain_addresses as ca, security as sec, util, assets as reg, chain_providers as cp)
from server.util import ApiError
from server.config import Config, crypto_env_problems

# ledger code, native?, amount to deposit (minor units), required confirmations
PRICES = [('BTC', 'Bitcoin', '64250'), ('ETH', 'Ethereum', '3100'), ('USDT', 'Tether', '1.0000'), ('USDC', 'USD Coin', '1.0000'), ('SOL', 'Solana', '148.20')]      # same demo quotes as server/seed.py
ASSETS = [('BTC', 1_000_000, 3), ('ETH', 10**17, 12), ('USDT.ETHEREUM', 100_000_000, 12), ('USDC.ETHEREUM', 100_000_000, 12), ('SOL', 10**9, 32)]

class CryptoBase(Base):
    def setUp(self):
        super().setUp()
        for sym, name, px in PRICES:             # funding assets must have a USD price: deposits convert to Account Balance and withdrawals convert back
            self.db.execute("INSERT OR IGNORE INTO assets(symbol,name,asset_class,last_price,prev_close,created_at,price_updated_at) VALUES(?,?,'Crypto',?,?,?,?)", (sym, name, px, px, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'))
        cs.bootstrap(self.db, 'development', environ={}, activate=True)
        self.chain = self.P.chain.chain; cs.sync_chain(self.db, self.P.chain)
        cs.install_dev_demo_wallets(self.db, self.P, 'development')
        self.info = {c: cw.an_info(self.db, ledger_code=c) for c, _, _ in ASSETS}
        self.wal = {c: cw.live_wallet(self.db, i['an_id']) for c, i in self.info.items()}
    def U(self, email='a@example.com'):
        self.signup(email, kyc=True); return {'id': self.uid(email)}
    def sender(self, code, tag): i = self.info[code]; return cs.demo_address(i['address_format'], f'sender|{code}|{tag}', True)
    def verify_sender(self, user, code, tag='s'):
        i = self.info[code]; a = self.sender(code, tag + str(user['id']))
        s = cd.register_source(self.db, user, i['network_code'], a); cd.verify_source_with_proof(self.db, self.P, user, s['id'], cp.mock_sign(a, s['challenge'])); return a
    def intent(self, user, code, amount=None): return cd.create_intent(self.db, self.P, user, code, amount)
    def pay(self, code, amount, sender, memo=None, confs=None, to=None, contract='default'):
        i = self.info[code]; c = i['contract_address'] if contract == 'default' else contract
        h = self.chain.fund(i['network_code'], to or self.wal[code]['public_address'], amount, sender=sender, contract=c, memo=memo)
        self.chain.mine(i['network_code'], dict((a, n) for a, _, n in ASSETS)[code] if confs is None else confs); return h
    def scan(self): return cd.run_once(self.db, self.P)
    def dep(self, h): return dict(self.db.execute('SELECT * FROM crypto_deposits WHERE tx_hash=?', (h.lower() if h.startswith('0x') else h,)).fetchone())
    def liability(self, user, code): return cl.balance(self.db, user['id'], code)      # customer CRYPTO liability: always 0 now (funding is converted to Account Balance at once)
    def bal(self, user, code): return self.worth(user, code)                           # 'how much of this asset is the customer's Account Balance worth'
    def cash(self, user): return ledger.balance(self.db, ledger.account_for(self.db, user['id'], 'cash')['id'])['posted_cents']
    def worth(self, user, code):
        """The customer's Account Balance expressed in this asset at the test price (exact for USDT/USDC at $1): lets tests say 'the customer is credited N units'."""
        px = Decimal(self.db.execute('SELECT last_price FROM assets WHERE symbol=?', (code.split('.')[0],)).fetchone()[0]); dec = self.db.execute('SELECT decimals FROM ledger_assets WHERE code=?', (code,)).fetchone()[0]
        return int((Decimal(self.cash(user)) / 100 / px).scaleb(dec).to_integral_value(rounding='ROUND_FLOOR'))
    def usd(self, code, units):
        """Independent expectation of what crediting `units` of this asset adds to the Account Balance, in cents (floor), from the asset table's price."""
        px = Decimal(self.db.execute('SELECT last_price FROM assets WHERE symbol=?', (code.split('.')[0],)).fetchone()[0]); dec = self.db.execute('SELECT decimals FROM ledger_assets WHERE code=?', (code,)).fetchone()[0]
        return int((Decimal(units).scaleb(-dec) * px * 100).to_integral_value(rounding='ROUND_FLOOR'))
    def totp(self, uid):
        s = sec.new_totp_secret(); self.db.execute('UPDATE users SET totp_secret=?,totp_enabled=1,totp_last_step=0 WHERE id=?', (s, uid)); self.secret = {**getattr(self, 'secret', {}), uid: s}
    def code(self, uid): self.db.execute('UPDATE users SET totp_last_step=0 WHERE id=?', (uid,)); return sec.totp_code(self.secret[uid])
    def staff_user(self, role, email):
        self.staff(role, email); uid = self.uid(email); self.totp(uid); return {'id': uid}
    def assert_gl(self): self.assertTrue(acc.verify_integrity(self.db)['ok'])

class TestFiveAssets(CryptoBase):
    def test_valid_deposit_credits_every_asset_exactly(self):
        u = self.U()
        for code, amt, _ in ASSETS:
            with self.subTest(code):
                before = self.cash(u); src = self.verify_sender(u, code); self.intent(u, code); h = self.pay(code, amt, src); self.scan()
                d = self.dep(h); self.assertEqual((d['status'], d['attribution_method']), ('credited', 'verified_source' if code != 'SOL' else 'verified_source'))
                self.assertEqual(self.cash(u) - before, self.usd(code, amt)); self.assertEqual(self.liability(u, code), 0); self.assertEqual(acc.balance(self.db, 'CUSTODY_CRYPTO', code), amt)        # lands in the Account Balance; the company holds the crypto
                self.assertEqual(acc.balance(self.db, 'SUSPENSE', code), 0)
        self.assert_gl()
    def test_confirmation_policy_is_per_asset_and_enforced(self):
        u = self.U()
        for code, amt, need in ASSETS:
            with self.subTest(code):
                before = self.cash(u); src = self.verify_sender(u, code); self.intent(u, code); h = self.pay(code, amt, src, confs=need - 1); self.scan()
                self.assertNotEqual(self.dep(h)['status'], 'credited'); self.assertEqual(self.cash(u), before)
                self.chain.mine(self.info[code]['network_code'], 1); self.scan(); self.assertEqual(self.dep(h)['status'], 'credited'); self.assertEqual(self.cash(u) - before, self.usd(code, amt))
    def test_confirmations_are_configurable_not_hardcoded(self):
        u = self.U(); a = self.staff_user('super_admin', 'adm@vaulta.test'); wid = self.wal['BTC']['id']
        cw.update_settings(self.db, self.P, a, wid, self.code(a['id']), 'lower BTC confirmations for test', {'confirmation_requirement': 1})
        src = self.verify_sender(u, 'BTC'); self.intent(u, 'BTC'); h = self.pay('BTC', 1_000_000, src, confs=1); self.scan(); self.assertEqual(self.dep(h)['status'], 'credited')
    def test_solana_uses_finality_commitment_and_memo(self):
        u = self.U(); i = self.intent(u, 'SOL'); self.assertTrue(i['memo'].startswith('VLT-'))
        h = self.pay('SOL', 10**9, 'AnyUnverifiedSender1111111111111111111111111', memo=i['memo'], confs=31); self.scan(); self.assertNotEqual(self.dep(h)['status'], 'credited')
        self.chain.mine('solana', 1); self.scan(); d = self.dep(h); self.assertEqual((d['status'], d['attribution_method']), ('credited', 'memo')); self.assertEqual(self.bal(u, 'SOL'), 10**9)

class TestSafety(CryptoBase):
    def test_duplicate_processing_never_double_credits(self):
        u = self.U(); src = self.verify_sender(u, 'USDT.ETHEREUM'); self.intent(u, 'USDT.ETHEREUM'); h = self.pay('USDT.ETHEREUM', 100_000_000, src)
        for _ in range(5): self.scan()
        d = self.dep(h); net = dict(self.db.execute('SELECT * FROM blockchain_networks WHERE network_code="ethereum"').fetchone())
        for tr in self.P.chain.get_transaction('ethereum', h): self.assertEqual(cd.ingest_transfer(self.db, self.P, net, tr), (d['id'], False))
        for _ in range(3): cd.advance(self.db, self.P, d['id'])
        self.assertEqual(self.bal(u, 'USDT.ETHEREUM'), 100_000_000)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM journal_entries WHERE entry_type='crypto.deposit_credit'").fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM crypto_deposits').fetchone()[0], 1)
    def test_database_rejects_a_second_row_for_the_same_event(self):
        u = self.U(); src = self.verify_sender(u, 'ETH'); self.intent(u, 'ETH'); self.pay('ETH', 10**17, src); self.scan()
        with self.assertRaises(sqlite3.IntegrityError): self.db.execute("INSERT INTO crypto_deposits(ref,network_id,tx_hash,event_index,recipient,amount,status,detected_at) SELECT 'X',network_id,tx_hash,event_index,recipient,amount,'detected',detected_at FROM crypto_deposits")
    def test_two_token_events_in_one_transaction_are_two_deposits(self):
        a, b = self.U('a@example.com'), self.U('b@example.com'); sa, sb = self.verify_sender(a, 'USDC.ETHEREUM'), self.verify_sender(b, 'USDC.ETHEREUM'); self.intent(a, 'USDC.ETHEREUM'); self.intent(b, 'USDC.ETHEREUM')
        i = self.info['USDC.ETHEREUM']; w = self.wal['USDC.ETHEREUM']['public_address']
        h = self.chain.send('ethereum', [dict(index=0, sender=sa, recipient=w, contract=i['contract_address'], amount=70_000_000), dict(index=1, sender=sb, recipient=w, contract=i['contract_address'], amount=30_000_000)]); self.chain.mine('ethereum', 12); self.scan()
        self.assertEqual((self.bal(a, 'USDC.ETHEREUM'), self.bal(b, 'USDC.ETHEREUM')), (70_000_000, 30_000_000)); self.assertEqual(self.db.execute('SELECT COUNT(*) FROM crypto_deposits').fetchone()[0], 2)
    def test_wrong_network_is_detected_and_never_credited(self):
        u = self.U(); self.intent(u, 'USDT.ETHEREUM'); self.chain.register('bsc', 'evm')
        from server import assets as r
        r.create_network(self.db, None, 'bsc', 'BNB Chain (unsupported)', 'account', 15, is_testnet=True, address_format='evm')   # registered, never activated
        src = self.verify_sender(u, 'USDT.ETHEREUM'); self.chain.fund('bsc', self.wal['USDT.ETHEREUM']['public_address'], 100_000_000, sender=src, contract=self.info['USDT.ETHEREUM']['contract_address'], mine=True); self.chain.mine('bsc', 40); self.scan()
        d = dict(self.db.execute('SELECT * FROM crypto_deposits').fetchone()); self.assertEqual(d['status'], 'wrong_network'); self.assertEqual(self.bal(u, 'USDT.ETHEREUM'), 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM journal_entries WHERE entry_type LIKE "crypto.deposit%"').fetchone()[0], 0)
        res = rc.reconcile(self.db, self.P); self.assertTrue(any(e['kind'] == 'wrong_network' for e in rc.list_exceptions(self.db)))
    def test_wrong_asset_unknown_token_and_native_at_token_address(self):
        u = self.U(); src = self.verify_sender(u, 'USDT.ETHEREUM'); self.intent(u, 'USDT.ETHEREUM'); w = self.wal['USDT.ETHEREUM']['public_address']
        h1 = self.chain.fund('ethereum', w, 5 * 10**8, sender=src, contract='0x' + '9' * 40, mine=True)       # unknown token contract
        h2 = self.chain.fund('ethereum', w, 10**18, sender=src, mine=True)                                       # native ETH to the USDT address
        self.chain.mine('ethereum', 40); self.scan()
        self.assertEqual({self.dep(h1)['status'], self.dep(h2)['status']}, {'unsupported_asset'}); self.assertEqual(self.bal(u, 'USDT.ETHEREUM'), 0)
        self.assertEqual(self.dep(h1)['status'], 'unsupported_asset')
    def test_customer_supplied_hash_is_independently_verified(self):
        u = self.U(); i = self.intent(u, 'ETH')
        with self.assertRaises(ApiError) as e: cd.submit_tx(self.db, self.P, u, i['ref'], '0x' + 'ab' * 32)
        self.assertEqual(e.exception.code, 'transaction_not_found')
        with self.assertRaises(ApiError) as e: cd.submit_tx(self.db, self.P, u, i['ref'], 'not-a-hash')
        self.assertEqual(e.exception.code, 'invalid_tx_hash')
        other = self.chain.fund('ethereum', cs.demo_address('evm', 'someone-else'), 10**18, sender='0x' + '3' * 40, mine=True)    # a real tx that did not pay Vaulta
        with self.assertRaises(ApiError) as e: cd.submit_tx(self.db, self.P, u, i['ref'], other)
        self.assertEqual(e.exception.code, 'not_a_deposit'); self.assertEqual(self.bal(u, 'ETH'), 0)
    def test_submitting_a_hash_does_not_bypass_attribution(self):
        u = self.U(); i = self.intent(u, 'ETH'); h = self.pay('ETH', 10**17, '0x' + '4' * 40)       # unverified sender
        out = cd.submit_tx(self.db, self.P, u, i['ref'], h); self.assertEqual(out[0]['status'], 'manual_review'); self.assertEqual(self.bal(u, 'ETH'), 0)
        cd.submit_tx(self.db, self.P, u, i['ref'], h); self.assertEqual(self.db.execute('SELECT COUNT(*) FROM crypto_deposits').fetchone()[0], 1)
    def test_below_minimum_is_not_silently_credited(self):
        u = self.U(); src = self.verify_sender(u, 'BTC'); self.intent(u, 'BTC'); h = self.pay('BTC', 5_000, src); self.scan()
        d = self.dep(h); self.assertEqual(d['status'], 'manual_review'); self.assertTrue(d['status_reason'].startswith('below_minimum')); self.assertEqual(self.bal(u, 'BTC'), 0)
        self.assertEqual(acc.balance(self.db, 'SUSPENSE', 'BTC'), 5_000)          # the funds are ours, held in suspense
    def test_failed_transaction_is_not_credited(self):
        u = self.U(); src = self.verify_sender(u, 'ETH'); i = self.info['ETH']
        h = self.chain.send('ethereum', [dict(index=0, sender=src, recipient=self.wal['ETH']['public_address'], contract=None, amount=10**17)], success=False); self.chain.mine('ethereum', 20)
        self.intent(u, 'ETH'); self.scan(); self.assertEqual(self.bal(u, 'ETH'), 0)
    def test_reorg_before_finality_reverses_and_recovers(self):
        u = self.U(); src = self.verify_sender(u, 'BTC'); self.intent(u, 'BTC'); h = self.pay('BTC', 1_000_000, src, confs=2); self.scan(); self.assertEqual(self.dep(h)['status'], 'confirming')
        self.chain.reorg('bitcoin', h, 'unconfirm'); self.scan(); self.assertEqual(self.dep(h)['status'], 'reorged'); self.assertEqual(self.bal(u, 'BTC'), 0)
        self.chain.mine('bitcoin', 3); self.scan(); self.assertEqual(self.dep(h)['status'], 'credited'); self.assertEqual(self.bal(u, 'BTC'), 1_000_000)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM crypto_deposits').fetchone()[0], 1)
    def test_reorg_after_confirmation_but_before_credit_reverses_custody_entry(self):
        u = self.U(); src = self.verify_sender(u, 'BTC'); self.intent(u, 'BTC'); self.db.execute("UPDATE system_settings SET value='0' WHERE key='crypto_requires_kyc'")
        self.db.execute("UPDATE users SET kyc_status='pending' WHERE id=?", (u['id'],)); self.db.execute("UPDATE system_settings SET value='1' WHERE key='crypto_requires_kyc'")
        h = self.pay('BTC', 1_000_000, src); self.scan(); d = self.dep(h); self.assertEqual(d['status'], 'risk_review'); self.assertEqual(acc.balance(self.db, 'SUSPENSE', 'BTC'), 1_000_000)
        self.chain.reorg('bitcoin', h, 'drop'); self.scan(); self.assertEqual(self.dep(h)['status'], 'reorged'); self.assertEqual(acc.balance(self.db, 'SUSPENSE', 'BTC'), 0); self.assertEqual(acc.balance(self.db, 'CUSTODY_CRYPTO', 'BTC'), 0)
        self.assert_gl()
    def test_credited_deposit_that_vanishes_raises_a_critical_exception_not_a_debit(self):
        u = self.U(); src = self.verify_sender(u, 'BTC'); self.intent(u, 'BTC'); h = self.pay('BTC', 1_000_000, src); self.scan(); self.assertEqual(self.dep(h)['status'], 'credited')
        self.chain.reorg('bitcoin', h, 'drop'); self.scan(); ex = [e for e in rc.list_exceptions(self.db) if e['kind'] == 'post_credit_reorg']; self.assertEqual(len(ex), 1); self.assertEqual(ex[0]['severity'], 'critical')
        self.assertEqual(self.bal(u, 'BTC'), 1_000_000)
    def test_invalid_state_transitions_are_refused(self):
        u = self.U(); src = self.verify_sender(u, 'BTC'); self.intent(u, 'BTC'); h = self.pay('BTC', 1_000_000, src); self.scan(); d = self.dep(h)
        with self.assertRaises(ApiError): cd._set(self.db, d, 'detected')
        with self.assertRaises(sqlite3.DatabaseError): self.db.execute('UPDATE crypto_deposits SET amount="1" WHERE id=?', (d['id'],))
        with self.assertRaises(sqlite3.DatabaseError): self.db.execute('DELETE FROM crypto_deposits WHERE id=?', (d['id'],))

class TestSharedWallet(CryptoBase):
    def test_same_amounts_from_two_customers_are_never_merged(self):
        a, b = self.U('a@example.com'), self.U('b@example.com'); c = 'USDT.ETHEREUM'; sa, sb = self.verify_sender(a, c), self.verify_sender(b, c); self.intent(a, c); self.intent(b, c)
        self.pay(c, 100_000_000, sa); self.pay(c, 100_000_000, sb); self.scan(); self.assertEqual((self.bal(a, c), self.bal(b, c)), (100_000_000, 100_000_000))
    def test_a_100_b_250_c_100(self):
        us = [self.U(f'u{i}@example.com') for i in range(3)]; c = 'USDT.ETHEREUM'; amts = [100_000_000, 250_000_000, 100_000_000]
        srcs = [self.verify_sender(u, c) for u in us]
        for u in us: self.intent(u, c)
        for s, a in zip(srcs, amts): self.pay(c, a, s)
        self.scan(); self.assertEqual([self.bal(u, c) for u in us], amts); self.assertEqual(acc.balance(self.db, 'CUSTODY_CRYPTO', c), sum(amts)); self.assert_gl()
    def test_amount_alone_never_attributes(self):
        a, b = self.U('a@example.com'), self.U('b@example.com'); c = 'USDT.ETHEREUM'; self.intent(a, c, '100'); self.intent(b, c, '100')
        h = self.pay(c, 100_000_000, self.sender(c, 'stranger')); self.scan(); d = self.dep(h)
        self.assertEqual(d['status'], 'manual_review'); self.assertIsNone(d['user_id']); self.assertEqual((self.bal(a, c), self.bal(b, c)), (0, 0))
    def test_unverified_sender_is_not_attributed_even_if_registered(self):
        a = self.U(); c = 'ETH'; adr = self.sender(c, 'x'); cd.register_source(self.db, a, 'ethereum', adr); self.intent(a, c)
        h = self.pay(c, 10**17, adr); self.scan(); self.assertEqual(self.dep(h)['status'], 'manual_review'); self.assertEqual(self.bal(a, c), 0)
    def test_one_verified_address_cannot_belong_to_two_customers(self):
        a, b = self.U('a@example.com'), self.U('b@example.com'); adr = self.verify_sender(a, 'ETH', 'shared')
        sb = cd.register_source(self.db, b, 'ethereum', adr)
        with self.assertRaises(ApiError) as e: cd.verify_source_with_proof(self.db, self.P, b, sb['id'], cp.mock_sign(adr, sb['challenge']))
        self.assertEqual(e.exception.code, 'address_in_use')
    def test_proof_must_be_valid(self):
        a = self.U(); adr = self.sender('ETH', 'p'); s = cd.register_source(self.db, a, 'ethereum', adr)
        with self.assertRaises(ApiError) as e: cd.verify_source_with_proof(self.db, self.P, a, s['id'], 'forged'); self.assertEqual(e.exception.code, 'proof_invalid')
    def test_conflicting_memo_and_sender_goes_to_manual_review(self):
        a, b = self.U('a@example.com'), self.U('b@example.com'); sa = self.verify_sender(a, 'SOL'); self.intent(a, 'SOL'); ib = self.intent(b, 'SOL')
        h = self.pay('SOL', 10**9, sa, memo=ib['memo']); self.scan(); self.assertEqual(self.dep(h)['status'], 'manual_review'); self.assertEqual((self.bal(a, 'SOL'), self.bal(b, 'SOL')), (0, 0))
    def test_deposit_after_intent_expiry_window_is_not_auto_credited(self):
        a = self.U(); src = self.verify_sender(a, 'ETH'); self.intent(a, 'ETH'); self.db.execute("UPDATE deposit_intents SET expires_at='2020-01-01T00:00:00Z'")
        h = self.pay('ETH', 10**17, src); self.scan(); self.assertEqual(self.dep(h)['status'], 'manual_review'); self.assertEqual(self.bal(a, 'ETH'), 0)

class TestManualReview(CryptoBase):
    def test_manual_attribution_needs_second_person_and_passes_pipeline(self):
        u = self.U(); c = 'ETH'; h = self.pay(c, 10**17, '0x' + '5' * 40); self.scan(); d = self.dep(h); self.assertEqual(d['status'], 'manual_review')
        a1, a2 = self.staff_user('operations_admin', 'ops1@vaulta.test'), self.staff_user('operations_admin', 'ops2@vaulta.test')
        pub = self.db.execute('SELECT public_id FROM users WHERE id=?', (u['id'],)).fetchone()[0]
        v = cd.resolve(self.db, self.P, a1, d['id'], 'attribute', 'customer emailed proof of sender wallet', user_ref=pub); self.assertEqual(v['status'], 'risk_review'); self.assertEqual(self.bal(u, c), 0)
        with self.assertRaises(ApiError) as e: cd.resolve(self.db, self.P, a1, d['id'], 'approve_risk', 'approving my own attribution')
        self.assertEqual(e.exception.code, 'separation_of_duties')
        v = cd.resolve(self.db, self.P, a2, d['id'], 'approve_risk', 'reviewed the evidence on file'); self.assertEqual(v['status'], 'credited'); self.assertEqual(self.bal(u, c), 10**17); self.assert_gl()
    def test_reject_and_duplicate_marking(self):
        h1 = self.pay('ETH', 10**17, '0x' + '6' * 40); h2 = self.pay('ETH', 10**17, '0x' + '7' * 40); self.scan(); a = self.staff_user('operations_admin', 'ops@vaulta.test')
        d1, d2 = self.dep(h1), self.dep(h2)
        self.assertEqual(cd.resolve(self.db, self.P, a, d1['id'], 'reject', 'sender is a mixer service')['status'], 'rejected')
        self.assertEqual(cd.resolve(self.db, self.P, a, d2['id'], 'mark_duplicate', 'same payment reported twice', duplicate_of=d1['id'])['status'], 'duplicate')
        with self.assertRaises(ApiError): cd.resolve(self.db, self.P, a, d1['id'], 'reject', 'x' * 12)
    def test_risk_threshold_requires_second_approval(self):
        u = self.U(); src = self.verify_sender(u, 'ETH'); self.intent(u, 'ETH'); a = self.staff_user('super_admin', 'adm@vaulta.test'); b = self.staff_user('operations_admin', 'ops@vaulta.test')
        cw.update_settings(self.db, self.P, a, self.wal['ETH']['id'], self.code(a['id']), 'review anything over 0.05 ETH', {'risk_review_threshold': '0.05'})
        h = self.pay('ETH', 10**17, src); self.scan(); self.assertEqual(self.dep(h)['status'], 'risk_review'); self.assertEqual(self.bal(u, 'ETH'), 0)
        cd.resolve(self.db, self.P, b, self.dep(h)['id'], 'approve_risk', 'verified source of funds'); self.assertEqual(self.bal(u, 'ETH'), 10**17)
    def test_unverified_kyc_holds_credit(self):
        u = self.U(); src = self.verify_sender(u, 'ETH'); self.intent(u, 'ETH'); self.db.execute("UPDATE users SET kyc_status='pending' WHERE id=?", (u['id'],))
        h = self.pay('ETH', 10**17, src); self.scan(); self.assertEqual(self.dep(h)['status'], 'risk_review'); self.assertEqual(self.bal(u, 'ETH'), 0)
        self.db.execute("UPDATE users SET kyc_status='verified' WHERE id=?", (u['id'],)); self.scan(); self.assertEqual(self.dep(h)['status'], 'credited')
    def test_blocklisted_sender_goes_to_manual_review(self):
        u = self.U(); src = self.verify_sender(u, 'ETH'); self.intent(u, 'ETH'); self.db.execute("INSERT INTO crypto_address_blocklist(address_format,address_key,reason,created_at) VALUES('evm',?,'test',?)", (src.lower(), util.now()))
        h = self.pay('ETH', 10**17, src); self.scan(); self.assertEqual(self.dep(h)['status'], 'manual_review'); self.assertEqual(self.bal(u, 'ETH'), 0)

class TestWithdrawals(CryptoBase):
    def fund(self, u, code='USDT.ETHEREUM', amt=100_000_000):
        src = self.verify_sender(u, code); self.intent(u, code); self.pay(code, amt, src); self.scan(); self.assertEqual(self.bal(u, code), amt)
    def wd(self, u, amount='40', code='USDT.ETHEREUM', dest=None):
        self.totp(u['id']); return cwd.request_withdrawal(self.db, self.P, u, code, amount, dest or self.sender(code, 'dest'), self.code(u['id']))['withdrawal']
    def test_full_lifecycle_reserves_signs_confirms_and_posts(self):
        u = self.U(); self.fund(u); c = 'USDT.ETHEREUM'; w = self.wd(u); self.assertEqual(w['status'], 'pending_approval'); self.assertEqual(self.bal(u, c), 60_000_000)
        ops = self.staff_user('operations_admin', 'ops@vaulta.test'); wid = w['id']; cwd.approve(self.db, ops, wid); cwd.process(self.db, self.P, ops, wid)
        self.assertEqual(self.db.execute('SELECT status FROM crypto_withdrawals').fetchone()[0], 'broadcast'); self.assertEqual(acc.balance(self.db, 'CRYPTO_WD_CLEARING', c), 40_000_000)
        cwd.advance(self.db, self.P, wid); self.assertEqual(self.db.execute('SELECT status FROM crypto_withdrawals').fetchone()[0], 'broadcast')
        self.chain.mine('ethereum', 12); cwd.advance(self.db, self.P, wid)
        self.assertEqual(self.db.execute('SELECT status FROM crypto_withdrawals').fetchone()[0], 'completed'); self.assertEqual(acc.balance(self.db, 'CRYPTO_WD_CLEARING', c), 0)
        self.assertEqual(acc.balance(self.db, 'CUSTODY_CRYPTO', c), 60_000_000); self.assertEqual(self.bal(u, c), 60_000_000); self.assert_gl()
        res = rc.reconcile(self.db, self.P); self.assertEqual(res['status'], 'clean', res)
    def test_withdrawals_require_2fa_balance_valid_address_and_kyc(self):
        u = self.U(); self.fund(u); c = 'USDT.ETHEREUM'
        with self.assertRaises(ApiError) as e: cwd.request_withdrawal(self.db, self.P, u, c, '1', self.sender(c, 'd'), '000000')
        self.assertEqual(e.exception.code, 'two_factor_setup_required'); self.totp(u['id'])
        with self.assertRaises(ApiError) as e: cwd.request_withdrawal(self.db, self.P, u, c, '1', self.sender(c, 'd'), '000000')
        self.assertEqual(e.exception.code, 'two_factor_required')
        with self.assertRaises(ApiError) as e: cwd.request_withdrawal(self.db, self.P, u, c, '1000', self.sender(c, 'd'), self.code(u['id'])); self.assertEqual(e.exception.code, 'insufficient_funds')
        with self.assertRaises(ApiError) as e: cwd.request_withdrawal(self.db, self.P, u, c, '1', self.sender('SOL', 'd'), self.code(u['id']))
        self.assertEqual(e.exception.code, 'invalid_address'); self.assertIn('Solana', e.exception.message)
        with self.assertRaises(ApiError) as e: cwd.request_withdrawal(self.db, self.P, u, c, '1', self.wal[c]['public_address'], self.code(u['id']))
        self.assertEqual(e.exception.code, 'company_address')
        self.db.execute("UPDATE users SET kyc_status='pending' WHERE id=?", (u['id'],))
        with self.assertRaises(ApiError) as e: cwd.request_withdrawal(self.db, self.P, u, c, '1', self.sender(c, 'd'), self.code(u['id']))
        self.assertEqual(e.exception.code, 'kyc_required')
    def test_two_requests_cannot_spend_the_same_balance(self):
        u = self.U(); self.fund(u); self.wd(u, '60')
        with self.assertRaises(ApiError) as e: self.wd(u, '60')
        self.assertEqual(e.exception.code, 'insufficient_funds')
    def test_failed_withdrawal_releases_reservation(self):
        u = self.U(); self.fund(u); c = 'USDT.ETHEREUM'; ops = self.staff_user('operations_admin', 'ops@vaulta.test'); w = self.wd(u); cwd.approve(self.db, ops, w['id'])
        self.P.wallets.fail_next = True
        with self.assertRaises(ApiError): cwd.process(self.db, self.P, ops, w['id'])
        self.assertEqual(self.db.execute('SELECT status FROM crypto_withdrawals').fetchone()[0], 'approved'); cwd.reject(self.db, ops, w['id'], 'custody refused the transfer')
        self.assertEqual(self.bal(u, c), 100_000_000); self.assertEqual(acc.balance(self.db, 'CRYPTO_WD_CLEARING', c), 0); self.assert_gl()
    def test_cannot_approve_own_withdrawal_and_cancel_releases(self):
        u = self.U(); self.fund(u); w = self.wd(u); self.db.execute("INSERT INTO user_roles(user_id,role_id) SELECT ?,id FROM roles WHERE name='operations_admin'", (u['id'],))
        with self.assertRaises(ApiError) as e: cwd.approve(self.db, u, w['id'])
        self.assertEqual(e.exception.code, 'separation_of_duties'); cwd.cancel(self.db, u, w['ref']); self.assertEqual(self.bal(u, 'USDT.ETHEREUM'), 100_000_000)
    def test_process_twice_cannot_double_send(self):
        u = self.U(); self.fund(u); ops = self.staff_user('operations_admin', 'ops@vaulta.test'); w = self.wd(u); cwd.approve(self.db, ops, w['id']); cwd.process(self.db, self.P, ops, w['id'])
        with self.assertRaises(ApiError): cwd.process(self.db, self.P, ops, w['id'])
        self.assertEqual(len([t for t in self.chain.nets['ethereum']['txs'].values() if t['events'][0]['sender'] == self.wal['USDT.ETHEREUM']['public_address']]), 1)
    def test_idempotent_request(self):
        u = self.U(); self.fund(u); self.totp(u['id']); d = self.sender('USDT.ETHEREUM', 'dest')
        a = cwd.request_withdrawal(self.db, self.P, u, 'USDT.ETHEREUM', '10', d, self.code(u['id']), idem='k1'); b = cwd.request_withdrawal(self.db, self.P, u, 'USDT.ETHEREUM', '10', d, self.code(u['id']), idem='k1')
        self.assertEqual(a['withdrawal']['ref'], b['withdrawal']['ref']); self.assertEqual(self.db.execute('SELECT COUNT(*) FROM crypto_withdrawals').fetchone()[0], 1)

class TestReconciliation(CryptoBase):
    def test_clean_after_deposits_and_wallet_to_wallet_movement(self):
        u = self.U(); c = 'ETH'; src = self.verify_sender(u, c); self.intent(u, c); self.pay(c, 10**17, src); self.scan()
        self.assertEqual(rc.reconcile(self.db, self.P)['status'], 'clean')
        ok, held, owed = rc.check_solvency(self.db, self.P, c); self.assertTrue(ok); self.assertEqual((held, owed), (10**17, 10**17))
    def test_sweep_to_cold_wallet_does_not_break_reconciliation(self):
        u = self.U(); c = 'ETH'; src = self.verify_sender(u, c); self.intent(u, c); self.pay(c, 10**17, src); self.scan(); a = self.staff_user('super_admin', 'adm@vaulta.test'); i = self.info[c]
        cold = cs.demo_address('evm', 'cold-eth'); self.db.execute("INSERT INTO company_crypto_wallets(asset_network_id,wallet_label,public_address,wallet_type,status,created_at) VALUES(?,?,?,?,?,?)", (i['an_id'], 'cold', cold, 'cold', 'active', util.now()))
        self.chain.send('ethereum', [dict(index=0, sender=self.wal[c]['public_address'], recipient=cold, contract=None, amount=10**17)]); self.chain.mine('ethereum', 12); self.scan()
        self.assertEqual(rc.reconcile(self.db, self.P)['status'], 'clean'); self.assertEqual(self.db.execute('SELECT COUNT(*) FROM crypto_deposits').fetchone()[0], 1)    # the sweep is not a customer deposit
    def test_unrecorded_on_chain_funds_and_missing_funds_are_flagged(self):
        u = self.U(); c = 'ETH'; src = self.verify_sender(u, c); self.intent(u, c); self.pay(c, 10**17, src); self.scan()
        self.chain.send('ethereum', [dict(index=0, sender=self.wal[c]['public_address'], recipient='0x' + 'c' * 40, contract=None, amount=3 * 10**16)]); self.chain.mine('ethereum', 12)    # funds leave with no ledger entry
        res = rc.reconcile(self.db, self.P); kinds = {e['kind'] for e in rc.list_exceptions(self.db)}; self.assertEqual(res['status'], 'exceptions'); self.assertIn('insolvency', kinds); self.assertIn('ledger_mismatch', kinds)
        self.assertFalse(rc.check_solvency(self.db, self.P, c)[0])
    def test_exceptions_dedupe_and_need_a_reason_to_close(self):
        u = self.U(); self.chain.send('ethereum', [dict(index=0, sender='0x' + 'd' * 40, recipient=self.wal['ETH']['public_address'], contract=None, amount=10**15)]); self.chain.mine('ethereum', 12)
        rc.reconcile(self.db, self.P, refresh=False); rc.reconcile(self.db, self.P, refresh=False); ex = [e for e in rc.list_exceptions(self.db) if e['kind'] == 'missing_transaction']; self.assertEqual(len(ex), 1)
        a = self.staff_user('finance_admin', 'fin@vaulta.test')
        with self.assertRaises(ApiError): rc.resolve_exception(self.db, a, ex[0]['id'], 'resolved', 'short')
        rc.resolve_exception(self.db, a, ex[0]['id'], 'resolved', 'recorded via the monitor after indexer delay')
    def test_tampered_customer_balance_is_detected(self):
        u = self.U(); c = 'ETH'; src = self.verify_sender(u, c); self.intent(u, c); self.pay(c, 10**17, src); self.scan()
        cust = cl.customer_account(self.db, u['id']); other = acc.account_id(self.db, 'EQUITY')
        acc.post_entry(self.db, entry_type='manual', lines=[(other, 'debit', c, 5), (cust, 'credit', c, 5)])        # a credit with no deposit behind it
        rc.reconcile(self.db, self.P, refresh=False); self.assertIn('customer_balance_mismatch', {e['kind'] for e in rc.list_exceptions(self.db)})
    def test_unmatched_deposit_funds_are_visible_not_lost(self):
        self.pay('ETH', 10**17, '0x' + 'e' * 40); self.scan(); self.assertEqual(acc.balance(self.db, 'SUSPENSE', 'ETH'), 10**17); self.assertEqual(rc.reconcile(self.db, self.P)['status'], 'clean')

class TestWalletControl(CryptoBase):
    def setUp(self):
        super().setUp(); self.a = self.staff_user('super_admin', 'adm1@vaulta.test'); self.b = self.staff_user('super_admin', 'adm2@vaulta.test'); self.ops = self.staff_user('crypto_operations', 'ops@vaulta.test')
        self.an = self.info['ETH']['an_id']; self.new = cs.demo_address('evm', 'new-eth-wallet')
    def propose(self, actor=None, address=None, reason='rotate ETH deposit wallet per treasury plan'):
        a = actor or self.a; return cw.propose_change(self.db, self.P, a, self.an, address or self.new, 'Vaulta ETH wallet', reason, self.code(a['id']))
    def test_address_must_match_network_and_contract(self):
        sol = self.sender('SOL', 'x'); btc = self.sender('BTC', 'x')
        for bad, hint in ((sol, 'Solana'), (btc, 'Bitcoin'), ('0x123', None), ('NOT_CONFIGURED', None), (self.new.lower(), None)):
            with self.assertRaises(ApiError) as e: self.propose(address=bad)
            self.assertEqual(e.exception.code, 'invalid_address'); hint and self.assertIn(hint, e.exception.message)
        with self.assertRaises(ApiError) as e: cw.propose_change(self.db, self.P, self.a, self.info['USDT.ETHEREUM']['an_id'], sol, 'x', 'wrong chain for USDT test', self.code(self.a['id']))
        self.assertEqual(e.exception.code, 'invalid_address')
        with self.assertRaises(ApiError) as e: cw.propose_change(self.db, self.P, self.a, self.info['USDT.ETHEREUM']['an_id'], self.info['USDT.ETHEREUM']['contract_address'], 'x', 'cannot be the contract itself', self.code(self.a['id']))
        self.assertEqual(e.exception.code, 'address_is_contract')
    def test_two_person_approval_with_2fa_retires_old_wallet_and_audits(self):
        old = self.wal['ETH']; ref = self.propose()
        with self.assertRaises(ApiError) as e: cw.approve_change(self.db, self.P, self.a, ref, self.code(self.a['id'])); self.assertEqual(e.exception.code, 'separation_of_duties')
        with self.assertRaises(ApiError) as e: cw.approve_change(self.db, self.P, self.b, ref, '000000'); self.assertEqual(e.exception.code, 'two_factor_required')
        self.assertEqual(cw.live_wallet(self.db, self.an)['public_address'], old['public_address'])
        wid = cw.approve_change(self.db, self.P, self.b, ref, self.code(self.b['id']), 'checked custody console')
        o = cw.get_wallet(self.db, old['id']); n = cw.get_wallet(self.db, wid); self.assertEqual((o['status'], o['replaced_by_wallet_id']), ('retired', wid)); self.assertEqual((n['public_address'], n['status'], n['is_deposit_enabled']), (self.new, 'inactive', 0))
        ev = [e for e in cw.history(self.db) if e['event_type'] == 'wallet_address_changed'][0]; self.assertEqual((ev['old_address'], ev['new_address'], ev['actor_id'], ev['approver_id']), (old['public_address'], self.new, self.a['id'], self.b['id']))
        self.assertTrue(ev['reason'] and ev['created_at'] and ev['request_ref']); self.assertTrue(self.db.execute("SELECT 1 FROM audit_logs WHERE action='crypto.wallet_address_changed'").fetchone())
        for sql in ('UPDATE company_crypto_wallets SET public_address="x" WHERE id=%d' % wid, 'DELETE FROM company_crypto_wallets WHERE id=%d' % old['id'], "UPDATE company_crypto_wallets SET status='active' WHERE id=%d" % old['id'], 'DELETE FROM wallet_change_events', 'UPDATE wallet_change_events SET reason="x"'):
            with self.assertRaises(sqlite3.DatabaseError, msg=sql): self.db.execute(sql)
    def test_change_requires_2fa_enabled_and_fresh_code(self):
        nofa = self.staff('super_admin', 'nofa@vaulta.test'); u = {'id': self.uid('nofa@vaulta.test')}
        with self.assertRaises(ApiError) as e: cw.propose_change(self.db, self.P, u, self.an, self.new, 'x', 'no two factor set up yet', '123456')
        self.assertEqual(e.exception.code, 'two_factor_setup_required')
        with self.assertRaises(ApiError) as e: cw.propose_change(self.db, self.P, self.a, self.an, self.new, 'x', 'bad code supplied here', '000000')
        self.assertEqual(e.exception.code, 'two_factor_required')
    def test_address_cannot_change_through_settings_and_secrets_are_refused(self):
        wid = self.wal['ETH']['id']
        with self.assertRaises(ApiError) as e: cw.update_settings(self.db, self.P, self.a, wid, self.code(self.a['id']), 'trying to swap address', {'public_address': self.new}); self.assertEqual(e.exception.code, 'use_change_workflow')
        with self.assertRaises(ApiError) as e: cw.update_settings(self.db, self.P, self.a, wid, self.code(self.a['id']), 'sending a key by mistake', {'private_key': 'abc'}); self.assertEqual(e.exception.code, 'secret_material_refused')
    def test_custody_must_confirm_control_of_new_address(self):
        ref = self.propose(); self.P.wallets.foreign.add(self.new)
        with self.assertRaises(ApiError) as e: cw.approve_change(self.db, self.P, self.b, ref, self.code(self.b['id']))
        self.assertEqual(e.exception.code, 'custody_does_not_control_address'); self.assertEqual(cw.live_wallet(self.db, self.an)['public_address'], self.wal['ETH']['public_address'])
    def test_deposit_to_retired_wallet_is_not_auto_credited_and_intents_are_cancelled(self):
        u = self.U(); src = self.verify_sender(u, 'ETH'); self.intent(u, 'ETH'); ref = self.propose(); cw.approve_change(self.db, self.P, self.b, ref, self.code(self.b['id']))
        self.assertEqual(self.db.execute("SELECT status FROM deposit_intents").fetchone()[0], 'cancelled')
        h = self.pay('ETH', 10**17, src, to=self.wal['ETH']['public_address']); self.scan(); self.assertEqual(self.dep(h)['status'], 'manual_review'); self.assertEqual(self.bal(u, 'ETH'), 0)
        with self.assertRaises(ApiError) as e: self.intent(u, 'ETH'); self.assertEqual(e.exception.code, 'asset_unavailable')    # new wallet not yet enabled
    def test_new_wallet_must_be_enabled_explicitly_then_accepts_deposits(self):
        ref = self.propose(); wid = cw.approve_change(self.db, self.P, self.b, ref, self.code(self.b['id'])); self.db.execute('UPDATE company_crypto_wallets SET minimum_deposit=NULL WHERE id=?', (wid,))
        cw.update_settings(self.db, self.P, self.a, wid, self.code(self.a['id']), 'enable after custody verification', {'status': 'active', 'is_deposit_enabled': True})
        u = self.U(); self.assertEqual(self.intent(u, 'ETH')['deposit_address'], self.new)
    def test_one_live_wallet_per_asset_network_and_support_cannot_change(self):
        with self.assertRaises(sqlite3.IntegrityError): self.db.execute("INSERT INTO company_crypto_wallets(asset_network_id,wallet_label,public_address,status,created_at) VALUES(?,?,?,?,?)", (self.an, 'dup', cs.demo_address('evm', 'dup'), 'active', util.now()))
        c = self.staff('support_admin', 'sup@vaulta.test'); r = self.req(c, 'post', '/api/admin/crypto/wallets/change-requests', {'asset_network_id': self.an, 'address': self.new, 'label': 'x', 'reason': 'support should not manage', 'code': '123456'})
        self.assertEqual(r.status_code, 403)
    def test_pending_request_expires_and_cannot_be_approved(self):
        ref = self.propose(); self.db.execute("UPDATE wallet_change_requests SET expires_at='2020-01-01T00:00:00Z'")
        with self.assertRaises(ApiError) as e: cw.approve_change(self.db, self.P, self.b, ref, self.code(self.b['id']))
        self.assertEqual(e.exception.code, 'invalid_state')

class TestPlumbing(CryptoBase):
    def test_no_secret_columns_anywhere_in_the_schema(self):
        bad = ('private_key', 'seed', 'mnemonic', 'recovery_phrase', 'wallet_password', 'signing_key')
        for (t,) in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            for col in self.db.execute(f'PRAGMA table_info({t})').fetchall(): self.assertFalse(any(b in col[1].lower() for b in bad), f'{t}.{col[1]}')
    def test_environment_and_provider_separation(self):
        self.assertTrue(crypto_env_problems('production', 'mock')); self.assertTrue(crypto_env_problems('production', 'testnet')); self.assertTrue(crypto_env_problems('development', 'live'))
        self.assertTrue(crypto_env_problems('staging', 'mock')); self.assertTrue(crypto_env_problems('staging', 'live')); self.assertFalse(crypto_env_problems('production', 'live')); self.assertFalse(crypto_env_problems('staging', 'testnet')); self.assertFalse(crypto_env_problems('development', 'mock'))
        class C(Config): ENV = 'production'; CRYPTO_PROVIDER_MODE = 'testnet'; AUTH_SECRET = 'x' * 40
        with self.assertRaises(RuntimeError) as e: C.validate()
        self.assertIn('testnet provider may never run in production', str(e.exception))
        class D(Config): ENV = 'development'; CRYPTO_PROVIDER_MODE = 'live'
        with self.assertRaises(RuntimeError): D.validate()
    def test_signing_secrets_in_environment_stop_startup(self):
        os.environ['WALLET_PRIVATE_KEY'] = 'x'
        try:
            with self.assertRaises(RuntimeError) as e: Config.validate()
            self.assertIn('WALLET_PRIVATE_KEY', str(e.exception))
        finally: del os.environ['WALLET_PRIVATE_KEY']
    def test_unconfigured_real_providers_refuse_instead_of_faking(self):
        for mode in ('testnet', 'live'):
            chain, wallets = cp.build(mode)
            with self.assertRaises(ApiError) as e: chain.get_transaction('ethereum', '0x1')
            self.assertEqual(e.exception.code, 'provider_not_configured')
            with self.assertRaises(ApiError): wallets.submit_transaction({})
    def test_live_mode_blocks_funding_until_real_funds_enabled(self):
        u = self.U(); self.P.crypto_mode = 'live'; self.P.crypto_real_funds = False
        with self.assertRaises(ApiError) as e: self.intent(u, 'ETH')
        self.assertEqual(e.exception.code, 'funding_not_authorized')
    def test_unconfigured_placeholder_wallet_is_never_offered(self):
        self.db.execute("UPDATE company_crypto_wallets SET status='retired',retired_at='x' WHERE asset_network_id=?", (self.info['SOL']['an_id'],))
        cw.install_wallet(self.db, an_id=self.info['SOL']['an_id'], address=ca.PLACEHOLDER, label='x', allow_placeholder=True)
        u = self.U()
        with self.assertRaises(ApiError) as e: self.intent(u, 'SOL')
        self.assertEqual(e.exception.code, 'wallet_not_configured'); self.assertNotIn('SOL', [a['symbol'] for a in cd.supported_assets(self.db)])
    def test_address_validators(self):
        self.assertEqual(ca.keccak256(b'').hex(), 'c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470')
        self.assertTrue(ca.is_valid('evm', '0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed', strict=True)); self.assertFalse(ca.is_valid('evm', '0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAEd'))
        self.assertTrue(ca.is_valid('btc', 'bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq')); self.assertFalse(ca.is_valid('btc', 'bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdQ'))
        self.assertFalse(ca.is_valid('btc', 'bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq', testnet=True)); self.assertTrue(ca.is_valid('btc', '1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2'))
        self.assertTrue(ca.is_valid('solana', 'So11111111111111111111111111111111111111112')); self.assertFalse(ca.is_valid('solana', '0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed'))

if __name__ == '__main__': unittest.main()
