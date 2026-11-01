"""Crypto funding lands in the Account Balance (USD cash); the Total Portfolio Value is investing + trading + savings/goals + retirement and never includes idle cash.
Real app, real database, mock chain only."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_funding_pages import FP
from server import crypto_withdrawals as cwd, crypto_convert as conv, crypto_ledger as cl, accounting as acc, crypto_recon as rc, ledger
from server.util import ApiError


class AB(FP):
    def setting(self, key, value):
        self.db.execute("INSERT INTO system_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    def summary(self, c): return c.get('/api/dashboard').json['summary']
    def wd(self, c, u, amount, idem=None):
        self.totp(u['id']); return cwd.request_withdrawal(self.db, self.P, u, self.CODE, amount, self.dest(), self.code(u['id']), idem=idem)['withdrawal']
    def ops_actor(self): return self.staff_user('operations_admin', 'ops-ab@vaulta.test')
    def check_books(self):
        self.assertTrue(acc.verify_integrity(self.db)['ok']); self.assertEqual(acc.reconcile_subledger(self.db), []); self.assertEqual(rc.reconcile(self.db, self.P)['status'], 'clean')


class TestDepositLandsInAccountBalance(AB):
    def test_crypto_deposit_goes_to_account_balance_and_not_portfolio_value(self):
        c, u = self.customer(); self.fund(u, 200_000_000)
        s = self.summary(c); self.assertEqual((s['available_cash'], s['portfolio_value'], s['net_worth']), ('200.00', '0.00', '200.00'))
        self.assertEqual(c.get('/api/crypto/balances').json['balances'], []); self.assertEqual(self.liability(u, self.CODE), 0)       # no crypto holding is left on the account
        p = c.get('/api/portfolio').json; self.assertEqual((p['summary']['portfolio_value'], p['holdings']), ('0.00', []))
        self.assertEqual({a['label']: a['value'] for a in p['allocation']}['Crypto'], '0.00')
        rec = c.get('/api/dashboard').json['recent'][0]; self.assertEqual((rec['type'], rec['direction'], rec['amount']), ('deposit', 'credit', '200.00'))   # the dashboard activity shows the dollar credit
        self.check_books()
    def test_conversion_is_recorded_with_rate_and_both_ledger_references(self):
        c, u = self.customer(); self.fund(u, 100_000_000)
        r = self.db.execute('SELECT * FROM crypto_conversions').fetchone(); self.assertEqual((r['direction'], r['usd_cents'], r['price'], r['spread_bps'], r['reversed_at']), ('deposit', 10_000, '1.0000', 0, None))
        self.assertTrue(r['crypto_entry_id'] and r['cash_txn_id'])
        t = self.db.execute('SELECT * FROM transactions WHERE id=?', (r['cash_txn_id'],)).fetchone(); self.assertEqual((t['type'], t['direction'], t['status'], t['amount_cents']), ('deposit', 'credit', 'completed', 10_000))
        item = [i for i in c.get('/api/crypto/funding-history').json['items'] if i['kind'] == 'deposit'][0]; self.assertEqual((item['usd_credit'], item['rate']), ('100.00', '1.0000'))
    def test_unpriced_or_paused_asset_is_never_credited_and_never_guessed(self):
        c, u = self.customer(); self.db.execute("UPDATE assets SET status='halted' WHERE symbol='USDT'")
        src = self.verify_sender(u, self.CODE); self.intent(u, self.CODE); h = self.pay(self.CODE, 100_000_000, src); self.scan()
        self.assertNotEqual(self.dep(h)['status'], 'credited'); self.assertEqual(self.cash(u), 0); self.assertEqual(self.db.execute('SELECT COUNT(*) FROM crypto_conversions').fetchone()[0], 0); self.assertEqual(self.liability(u, self.CODE), 0)
        self.db.execute("DELETE FROM asset_prices WHERE asset_id IN (SELECT id FROM assets WHERE symbol='USDC')"); self.db.execute("UPDATE assets SET last_price='0' WHERE symbol='USDC'")
        with self.assertRaises(ApiError) as e: conv.price(self.db, 'USDC.ETHEREUM')
        self.assertEqual(e.exception.code, 'price_unavailable')
    def test_stale_price_is_refused_when_an_age_limit_is_set(self):
        self.setting('crypto_price_max_age_minutes', 10); self.db.execute("UPDATE assets SET price_updated_at='2020-01-01T00:00:00Z' WHERE symbol='USDT'")
        with self.assertRaises(ApiError) as e: conv.price(self.db, self.CODE)
        self.assertEqual(e.exception.code, 'price_unavailable')
    def test_spread_is_applied_in_the_house_favour_both_ways(self):
        self.setting('crypto_conversion_spread_bps', 100); c, u = self.customer(); self.fund(u, 200_000_000)
        self.assertEqual(self.cash(u), 19_800)                                               # $200 less a 1% spread
        q = c.get(f'/api/crypto/withdrawal-quote?ledger_code={self.CODE}&amount=10').json; self.assertEqual((q['usd_debit'], q['rate']), ('10.10', '1.0000'))   # and 1% more on the way out
    def test_deposit_notification_states_the_dollars_credited(self):
        c, u = self.customer(); self.fund(u, 100_000_000)
        msg = self.db.execute("SELECT body FROM notifications WHERE type='deposit' ORDER BY id DESC LIMIT 1").fetchone()[0]; self.assertIn('$100.00', msg); self.assertIn('Account Balance', msg)
    def test_deposit_is_credited_once_even_when_the_monitor_runs_repeatedly(self):
        c, u = self.customer(); self.fund(u, 100_000_000)
        for _ in range(3): self.scan()
        self.assertEqual((self.cash(u), self.db.execute('SELECT COUNT(*) FROM crypto_conversions').fetchone()[0]), (10_000, 1))


class TestPortfolioValueDefinition(AB):
    def test_total_portfolio_value_counts_savings_and_goals_but_not_the_account_balance(self):
        c, u = self.customer(); self.fund(u, 200_000_000)
        g = self.req(c, 'post', '/api/goals', {'name': 'Trip', 'target_amount': '400.00', 'category': 'Vacation'}).json['goal']
        r = self.req(c, 'post', f"/api/goals/{g['id']}/contribute", {'amount': '50.00'}); self.assertEqual(r.status_code, 200, r.json)
        s = self.summary(c); self.assertEqual((s['available_cash'], s['savings_balance'], s['portfolio_value'], s['net_worth']), ('150.00', '50.00', '50.00', '200.00'))       # goal money is savings money: counted once
    def test_funding_the_account_does_not_move_the_portfolio_or_its_history(self):
        c, u = self.customer(); before = c.get('/api/portfolio/history?range=ALL').json
        self.fund(u, 200_000_000); after = c.get('/api/portfolio/history?range=ALL').json
        self.assertTrue(all(float(p['value']) == 0 for p in before['points'] + after['points']))      # a deposit must never look like portfolio growth
    def test_allocation_shares_add_up_to_the_portfolio_not_the_cash(self):
        c, u = self.customer(); self.fund(u, 200_000_000); g = self.req(c, 'post', '/api/goals', {'name': 'x', 'target_amount': '400.00', 'category': 'Vacation'}).json['goal']
        self.req(c, 'post', f"/api/goals/{g['id']}/contribute", {'amount': '100.00'}); a = {x['label']: x for x in c.get('/api/portfolio').json['allocation']}
        self.assertEqual((a['Savings']['value'], a['Savings']['pct']), ('100.00', '100.0')); self.assertNotIn('Cash', a)
    def test_unfunded_retirement_planner_figures_are_not_counted_as_money(self):
        c, u = self.customer(); self.assertEqual(self.summary(c)['portfolio_value'], '0.00')
        ledger.account_for(self.db, u['id'], 'retirement')                          # a funded retirement ACCOUNT would be counted; the planner's typed-in figures never are
        self.assertEqual(self.summary(c)['retirement_balance'], '0.00')


class TestWithdrawalsPayFromAccountBalance(AB):
    def test_withdrawal_debits_dollars_converts_and_locks_the_rate(self):
        c, u = self.customer(); self.fund(u, 200_000_000); self.cfg(min_withdrawal='1', fee_flat='1', fee_percent='0.5')
        w = self.wd(c, u, '20'); self.assertEqual((w['usd_debit'], w['rate'], w['status']), ('21.10', '1.0000', 'pending_approval'))      # 20 + $1 fixed + 0.5%
        self.assertEqual((self.cash(u), self.liability(u, self.CODE)), (17_890, 0)); self.check_books()
        r = self.db.execute("SELECT * FROM crypto_conversions WHERE direction='withdrawal'").fetchone(); self.assertEqual((r['usd_cents'], r['reversed_at']), (2_110, None))
        s = self.summary(c); self.assertEqual((s['available_cash'], s['portfolio_value']), ('178.90', '0.00'))
    def test_rejection_returns_exactly_the_dollars_even_if_the_price_moved(self):
        c, u = self.customer(); self.fund(u, 200_000_000); w = self.wd(c, u, '50'); self.assertEqual(self.cash(u), 15_000)
        self.db.execute("UPDATE assets SET last_price='2.0000' WHERE symbol='USDT'"); cwd.reject(self.db, self.ops_actor(), w['id'], 'address looks wrong to compliance')
        self.assertEqual((self.cash(u), self.liability(u, self.CODE)), (20_000, 0)); self.assertIsNotNone(self.db.execute('SELECT reversed_at FROM crypto_conversions WHERE direction="withdrawal"').fetchone()[0]); self.check_books()
    def test_customer_cancel_returns_the_dollars_and_cannot_be_repeated(self):
        c, u = self.customer(); self.fund(u, 200_000_000); w = self.wd(c, u, '50'); cwd.cancel(self.db, u, w['ref']); self.assertEqual(self.cash(u), 20_000)
        with self.assertRaises(ApiError): cwd.cancel(self.db, u, w['ref'])
        self.assertEqual(self.cash(u), 20_000); self.check_books()
    def test_not_enough_account_balance_blocks_the_withdrawal_and_changes_nothing(self):
        c, u = self.customer(); self.fund(u, 200_000_000); self.totp(u['id'])
        with self.assertRaises(ApiError) as e: cwd.request_withdrawal(self.db, self.P, u, self.CODE, '300', self.dest(), self.code(u['id']))
        self.assertEqual(e.exception.code, 'insufficient_funds'); self.assertEqual((self.cash(u), self.db.execute('SELECT COUNT(*) FROM crypto_withdrawals').fetchone()[0], self.db.execute("SELECT COUNT(*) FROM crypto_conversions WHERE direction='withdrawal'").fetchone()[0]), (20_000, 0, 0))
    def test_withdrawing_needs_no_crypto_holding_only_account_balance(self):
        c, u = self.customer(); self.fund(u, 200_000_000)
        cash = self.db.execute("SELECT id FROM accounts WHERE user_id=? AND kind='cash'", (u['id'],)).fetchone()[0]; self.assertGreater(ledger.balance(self.db, cash)['available_cents'], 0)
        self.assertEqual(self.liability(u, self.CODE), 0); self.assertTrue(c.get(f'/api/crypto/withdrawal-info?ledger_code={self.CODE}').json['can_withdraw'])
    def test_completed_withdrawal_keeps_books_and_reconciliation_clean(self):
        c, u = self.customer(); self.fund(u, 200_000_000); w = self.wd(c, u, '40'); a = self.ops_actor(); cwd.approve(self.db, a, w['id']); cwd.process(self.db, self.P, a, w['id'])
        self.chain.mine(self.info[self.CODE]['network_code'], 12); cwd.advance(self.db, self.P, w['id'])
        self.assertEqual(self.db.execute('SELECT status FROM crypto_withdrawals').fetchone()[0], 'completed'); self.assertEqual(self.cash(u), 16_000); self.check_books()
    def test_withdraw_page_data_uses_dollars_and_an_unpriced_asset_cannot_withdraw(self):
        c, u = self.customer(); self.fund(u, 200_000_000); self.assertEqual(c.get(f'/api/crypto/withdrawal-info?ledger_code={self.CODE}').json['available_usd'], '200.00')
        self.db.execute("UPDATE assets SET status='halted' WHERE symbol='USDT'"); i = c.get(f'/api/crypto/withdrawal-info?ledger_code={self.CODE}').json
        self.assertEqual((i['price_available'], i['can_withdraw']), (False, False)); self.assertIn('price_unavailable', [p['code'] for p in c.get(f'/api/crypto/withdrawal-quote?ledger_code={self.CODE}&amount=5').json['problems']])
        self.assertEqual(self.req(c, 'post', '/api/crypto/withdrawals', {'ledger_code': self.CODE, 'amount': '5', 'destination': self.dest(), 'code': '000000'}).status_code in (409, 403, 422), True)


class TestLegacyBalancesAreConverted(AB):
    def test_existing_crypto_balances_become_account_balance_exactly_once(self):
        c, u = self.customer(); units = 5_000_000        # 5 USDT credited under the old model
        acc.post_entry(self.db, entry_type='test.legacy_credit', idem='legacy-1', source_type='test', source_ref='legacy', description='legacy crypto balance',
                       lines=[(acc.account_id(self.db, 'SUSPENSE'), 'debit', self.CODE, units), (cl.customer_account(self.db, u['id']), 'credit', self.CODE, units)])
        self.assertEqual(self.liability(u, self.CODE), units)
        first = conv.convert_legacy(self.db); self.assertEqual((first['converted'], first['skipped']), (1, [])); self.assertEqual((self.cash(u), self.liability(u, self.CODE)), (500, 0))
        second = conv.convert_legacy(self.db); self.assertEqual(second['converted'], 0); self.assertEqual(self.cash(u), 500)
        self.assertEqual(self.db.execute("SELECT direction FROM crypto_conversions").fetchone()[0], 'legacy'); self.assertTrue(acc.verify_integrity(self.db)['ok']); self.assertEqual(acc.reconcile_subledger(self.db), [])
    def test_an_unpriced_legacy_balance_is_left_untouched_and_reported(self):
        c, u = self.customer(); units = 5_000_000
        acc.post_entry(self.db, entry_type='test.legacy_credit', idem='legacy-2', source_type='test', source_ref='legacy', description='legacy crypto balance',
                       lines=[(acc.account_id(self.db, 'SUSPENSE'), 'debit', self.CODE, units), (cl.customer_account(self.db, u['id']), 'credit', self.CODE, units)])
        self.db.execute("UPDATE assets SET status='halted' WHERE symbol='USDT'"); r = conv.convert_legacy(self.db)
        self.assertEqual((r['converted'], r['skipped'][0]['reason'], self.liability(u, self.CODE), self.cash(u)), (0, 'price_unavailable', units, 0))


class TestCustomerCannotReachConversions(AB):
    def test_conversions_are_not_exposed_or_writable_by_customers(self):
        c, u = self.customer()
        for url in ('/api/admin/crypto/conversions', '/api/crypto/conversions'): self.assertIn(c.get(url).status_code, (403, 404))
        self.assertEqual(self.req(c, 'post', '/api/crypto/convert', {'amount': '5'}).status_code in (403, 404, 405), True)


if __name__ == '__main__': unittest.main()
