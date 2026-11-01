"""Backend tests (stdlib unittest; run: python -m unittest discover -s tests -v). Each test gets a fresh temp database."""
import os, sys, tempfile, shutil, unittest, json, re, time
from decimal import Decimal
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server.app import create_app
from server import security as sec, db as dbm, util, portfolio as pf, finance as fin, ledger, rbac
from server.audit import verify_chain

PW = 'CorrectHorse42'
class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); sec.reset_rate_limits()
        self.app = create_app({'DATABASE_PATH': os.path.join(self.tmp, 't.db'), 'UPLOAD_DIR': os.path.join(self.tmp, 'up'), 'RATE_LIMIT_ENABLED': False, 'TESTING': True})
        self.db = dbm.connect(self.app.config['DATABASE_PATH']); self.P = self.app.providers
        self.db.execute("UPDATE system_settings SET value='1' WHERE key='fiat_funding_enabled'")      # legacy fiat tests; production default is crypto-only
        now = util.now()
        for sym, cls, price in (('VGTF', 'ETFs', '100.0000'), ('AAPL', 'Stocks', '200.0000')):
            cur = self.db.execute('INSERT INTO assets(symbol,name,asset_class,last_price,prev_close,created_at,price_updated_at) VALUES(?,?,?,?,?,?,?)', (sym, sym, cls, price, price, now, now))
            self.db.execute('INSERT INTO asset_prices(asset_id,ts,price) VALUES(?,?,?)', (cur.lastrowid, now, price))
        self.db.execute("INSERT INTO investment_products(slug,name,asset_id,category,risk,min_investment_cents,fee_bps,created_at) VALUES('tech','Tech Fund',1,'ETFs','Moderate',10000,50,?)", (now,))
    def tearDown(self): self.db.close(); shutil.rmtree(self.tmp, ignore_errors=True)
    def client(self): return self.app.test_client()
    def req(self, c, method, url, json_=None, **kw):
        h = dict(kw.pop('headers', {})); h['X-CSRF-Token'] = getattr(c, 'csrf', '')
        return getattr(c, method)(url, json=json_ if json_ is not None else ({} if method != 'get' else None), headers=h, **kw)
    def signup(self, email='a@example.com', verify=True, kyc=False, risk=True):
        c = self.client(); r = c.post('/api/auth/register', json={'email': email, 'password': PW, 'first_name': 'T', 'last_name': 'U', 'accepted_terms': True, 'terms_version': 1, 'privacy_version': 1}); self.assertEqual(r.status_code, 201, r.json)
        c.csrf = r.json['csrf_token']
        if risk: self.assertEqual(self.req(c, 'post', '/api/legal/risk-acknowledgement', {'acknowledged': True, 'version': 1}).status_code, 200)
        if verify: self.db.execute('UPDATE users SET email_verified=1 WHERE email=?', (email,))
        if kyc: self.db.execute("UPDATE users SET kyc_status='verified' WHERE email=?", (email,)); self.db.execute("UPDATE kyc_profiles SET status='verified' WHERE user_id=(SELECT id FROM users WHERE email=?)", (email,))
        return c
    def uid(self, email): return self.db.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()[0]
    def fund(self, c, amount='1000.00', email='a@example.com'):
        r = self.req(c, 'post', '/api/deposits', {'amount': amount, 'method': 'bank_transfer'}); self.assertEqual(r.status_code, 201, r.json)
        d = r.json['deposit']; self.assertEqual(d['status'], 'processing')
        fin.handle_payment_event(self.db, self.P.payments.internal_event(d['external_ref'])); return d
    def staff(self, role, email=None):
        email = email or f'{role}@vaulta.test'; c = self.client()
        pw = sec.hash_password(PW); cur = self.db.execute("INSERT INTO users(public_id,email,password_hash,email_verified,created_at) VALUES(?,?,?,1,?)", ('USR-' + role[:5].upper() + str(abs(hash(email)) % 10**6), email, pw, util.now()))
        self.db.execute('INSERT INTO user_profiles(user_id,first_name,last_name) VALUES(?,?,?)', (cur.lastrowid, role, 'S')); self.db.execute('INSERT INTO kyc_profiles(user_id) VALUES(?)', (cur.lastrowid,))
        self.db.execute('INSERT INTO user_roles(user_id,role_id) SELECT ?,id FROM roles WHERE name=?', (cur.lastrowid, role))
        r = c.post('/api/auth/login', json={'email': email, 'password': PW}); self.assertEqual(r.status_code, 200, r.json); c.csrf = r.json['csrf_token']; return c

class TestAuth(Base):
    def test_register_login_logout(self):
        c = self.signup(); self.assertEqual(c.get('/api/auth/me').json['user']['email'], 'a@example.com')
        self.assertEqual(self.req(c, 'post', '/api/auth/logout').status_code, 200); self.assertEqual(c.get('/api/auth/me').status_code, 401)
        r = c.post('/api/auth/login', json={'email': 'A@Example.com', 'password': PW}); self.assertEqual(r.status_code, 200)
    def test_password_hashed_not_plain(self):
        self.signup(); h = self.db.execute('SELECT password_hash FROM users').fetchone()[0]; self.assertNotIn(PW, h); self.assertTrue(h.startswith('scrypt:'))
    def test_weak_password_and_duplicate_email(self):
        c = self.client(); b = {'email': 'x@example.com', 'password': 'short1', 'first_name': 'a', 'last_name': 'b', 'accepted_terms': True, 'terms_version': 1, 'privacy_version': 1}
        self.assertEqual(c.post('/api/auth/register', json=b).status_code, 422); b['password'] = PW; self.assertEqual(c.post('/api/auth/register', json=b).status_code, 201)
        self.assertEqual(self.client().post('/api/auth/register', json=b).status_code, 409)
    def test_wrong_password_generic_and_lockout(self):
        self.signup(); c = self.client()
        r = c.post('/api/auth/login', json={'email': 'nobody@example.com', 'password': 'x' * 12}); r2 = c.post('/api/auth/login', json={'email': 'a@example.com', 'password': 'wrongpass99'})
        self.assertEqual(r.json['error']['message'], r2.json['error']['message'])      # no account enumeration
        for _ in range(5): c.post('/api/auth/login', json={'email': 'a@example.com', 'password': 'wrongpass99'})
        self.assertEqual(c.post('/api/auth/login', json={'email': 'a@example.com', 'password': PW}).status_code, 423)   # locked even with right password
    def test_csrf_required_for_authenticated_writes(self):
        c = self.signup(); self.assertEqual(c.post('/api/goals', json={'name': 'x', 'target_amount': '10.00'}).status_code, 403)
    def test_session_cookie_flags_and_rotation(self):
        c = self.client(); r = c.post('/api/auth/register', json={'email': 'z@example.com', 'password': PW, 'first_name': 'a', 'last_name': 'b', 'accepted_terms': True, 'terms_version': 1, 'privacy_version': 1})
        ck = r.headers.get('Set-Cookie'); self.assertIn('HttpOnly', ck); self.assertIn('SameSite=Lax', ck)
        tok1 = c.get_cookie('vaulta_sid').value; c.csrf = r.json['csrf_token']; self.req(c, 'post', '/api/auth/logout')
        c.post('/api/auth/login', json={'email': 'z@example.com', 'password': PW}); self.assertNotEqual(tok1, c.get_cookie('vaulta_sid').value)
    def test_password_reset_flow_single_use_and_revokes_sessions(self):
        c = self.signup(); a = self.client(); self.assertEqual(a.post('/api/auth/request-password-reset', json={'email': 'a@example.com'}).status_code, 200)
        self.assertEqual(a.post('/api/auth/request-password-reset', json={'email': 'ghost@example.com'}).status_code, 200)   # same answer
        import hashlib, secrets
        tok = secrets.token_urlsafe(8); self.db.execute("UPDATE email_tokens SET token_hash=? WHERE kind='reset_password'", (hashlib.sha256(tok.encode()).hexdigest(),))
        self.assertEqual(a.post('/api/auth/reset-password', json={'token': tok, 'password': 'BrandNewPass77'}).status_code, 200)
        self.assertEqual(a.post('/api/auth/reset-password', json={'token': tok, 'password': 'AnotherPass88x'}).status_code, 400)   # single use
        self.assertEqual(c.get('/api/auth/me').status_code, 401)           # old session revoked
        self.assertEqual(self.client().post('/api/auth/login', json={'email': 'a@example.com', 'password': 'BrandNewPass77'}).status_code, 200)
    def test_email_verification_token(self):
        c = self.signup(verify=False); self.assertFalse(c.get('/api/auth/me').json['user']['email_verified'])
        self.assertEqual(self.req(c, 'post', '/api/deposits', {'amount': '50.00', 'method': 'card'}).status_code, 403)   # unverified cannot deposit
    def test_totp_2fa_enable_and_login_requires_code(self):
        c = self.signup(); s = self.req(c, 'post', '/api/auth/2fa/setup').json['secret']
        self.assertEqual(self.req(c, 'post', '/api/auth/2fa/enable', {'code': sec.totp_code(s)}).status_code, 200)
        self.req(c, 'post', '/api/auth/logout'); d = self.client()
        self.assertEqual(d.post('/api/auth/login', json={'email': 'a@example.com', 'password': PW}).json['error']['code'], 'two_factor_required')
        self.assertEqual(d.post('/api/auth/login', json={'email': 'a@example.com', 'password': PW, 'code': '000000'}).status_code, 401)
        # replay of the already-used step is rejected
        self.assertEqual(d.post('/api/auth/login', json={'email': 'a@example.com', 'password': PW, 'code': sec.totp_code(s)}).status_code, 401)

class TestMoney(Base):
    def test_floats_rejected_and_decimals_exact(self):
        c = self.signup()
        self.assertEqual(self.req(c, 'post', '/api/deposits', {'amount': 10.5, 'method': 'card'}).status_code, 422)         # JSON float refused
        self.assertEqual(self.req(c, 'post', '/api/deposits', {'amount': '10.005', 'method': 'card'}).status_code, 422)
        self.assertEqual(util.parse_amount('0.10') + util.parse_amount('0.20'), 30)   # 0.1+0.2 == 0.30 exactly in cents
    def test_deposit_not_credited_until_provider_confirms(self):
        c = self.signup(); r = self.req(c, 'post', '/api/deposits', {'amount': '500.00', 'method': 'bank_transfer'}); self.assertEqual(r.json['deposit']['status'], 'processing')
        self.assertEqual(self.req(c, 'get', '/api/portfolio').json['summary']['available_cash'], '0.00')
        fin.handle_payment_event(self.db, self.P.payments.internal_event(r.json['deposit']['external_ref']))
        self.assertEqual(self.req(c, 'get', '/api/portfolio').json['summary']['available_cash'], '500.00')
        self.assertTrue(fin.handle_payment_event(self.db, self.P.payments.internal_event(r.json['deposit']['external_ref']))['noop'])   # duplicate webhook is harmless
        self.assertEqual(self.req(c, 'get', '/api/portfolio').json['summary']['available_cash'], '500.00')
    def test_card_fee_deducted(self):
        c = self.signup(); r = self.req(c, 'post', '/api/deposits', {'amount': '1000.00', 'method': 'card'}); d = r.json['deposit']
        fin.handle_payment_event(self.db, self.P.payments.internal_event(d['external_ref'])); self.assertEqual(d['fee'], '15.00')
        self.assertEqual(self.req(c, 'get', '/api/portfolio').json['summary']['available_cash'], '985.00')
    def test_idempotent_deposit_creates_one_transaction(self):
        c = self.signup(); h = {'Idempotency-Key': 'k-1'}
        a = self.req(c, 'post', '/api/deposits', {'amount': '200.00', 'method': 'bank_transfer'}, headers=h); b = self.req(c, 'post', '/api/deposits', {'amount': '200.00', 'method': 'bank_transfer'}, headers=h)
        self.assertEqual(a.json['deposit']['ref'], b.json['deposit']['ref']); self.assertEqual(self.db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0], 1)
        self.assertEqual(self.req(c, 'post', '/api/deposits', {'amount': '300.00', 'method': 'bank_transfer'}, headers=h).status_code, 422)   # same key, different body
    def test_ledger_immutable_and_no_delete(self):
        c = self.signup(); self.fund(c)
        with self.assertRaises(Exception): self.db.execute('UPDATE transactions SET amount_cents=999999')
        with self.assertRaises(Exception): self.db.execute('DELETE FROM transactions')
    def test_transaction_state_machine(self):
        c = self.signup(); self.fund(c); tid = self.db.execute('SELECT id FROM transactions').fetchone()[0]
        with self.assertRaises(util.ApiError): ledger.transition(self.db, tid, 'pending')
        with self.assertRaises(util.ApiError): ledger.transition(self.db, tid, 'failed')
    def test_withdrawal_rules(self):
        c = self.signup(kyc=False); self.fund(c, '9000.00')
        body = {'amount': '100.00', 'method': 'bank_transfer', 'destination': 'Acct 1'}
        self.assertEqual(self.req(c, 'post', '/api/withdrawals', body).json['error']['code'], 'kyc_required')        # server enforces KYC
        self.db.execute("UPDATE users SET kyc_status='verified' WHERE id=1")
        self.assertEqual(self.req(c, 'post', '/api/withdrawals', body).json['error']['code'], 'two_factor_setup_required')   # server demands 2FA be enabled
        s = self.req(c, 'post', '/api/auth/2fa/setup').json['secret']; self.req(c, 'post', '/api/auth/2fa/enable', {'code': sec.totp_code(s)})
        self.assertEqual(self.req(c, 'post', '/api/withdrawals', body).json['error']['code'], 'two_factor_required')
        self.db.execute('UPDATE users SET totp_last_step=0'); body['code'] = sec.totp_code(s)
        r = self.req(c, 'post', '/api/withdrawals', body); self.assertEqual(r.status_code, 201, r.json); self.assertEqual(r.json['withdrawal']['status'], 'pending')
        self.assertEqual(self.req(c, 'get', '/api/accounts').json['accounts'][0]['available'], '8900.00')           # funds held immediately
        body['amount'] = '999999.00'; self.db.execute('UPDATE users SET totp_last_step=0'); body['code'] = sec.totp_code(s)
        self.assertEqual(self.req(c, 'post', '/api/withdrawals', body).json['error']['code'], 'daily_limit')
    def test_large_withdrawal_held_for_review_and_admin_flow(self):
        c = self.signup(kyc=True); self.fund(c, '9000.00'); self.db.execute('UPDATE users SET withdrawal_2fa=0')
        r = self.req(c, 'post', '/api/withdrawals', {'amount': '6000.00', 'method': 'bank_transfer', 'destination': 'A'}); w = r.json['withdrawal']; self.assertEqual(w['status'], 'held_for_review')
        wid = self.db.execute('SELECT id FROM withdrawals').fetchone()[0]; ops = self.staff('operations_admin')
        self.assertEqual(self.req(ops, 'post', f'/api/admin/withdrawals/{wid}/action', {'action': 'complete'}).status_code, 409)   # cannot skip steps
        for a in ('approve', 'process', 'complete'): self.assertEqual(self.req(ops, 'post', f'/api/admin/withdrawals/{wid}/action', {'action': a}).status_code, 200, a)
        self.assertEqual(self.req(c, 'get', '/api/accounts').json['accounts'][0]['posted'], '3000.00')
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM audit_logs WHERE action LIKE 'withdrawal.%'").fetchone()[0], 3)
    def test_reject_withdrawal_releases_funds(self):
        c = self.signup(kyc=True); self.fund(c, '9000.00'); self.db.execute('UPDATE users SET withdrawal_2fa=0')
        self.req(c, 'post', '/api/withdrawals', {'amount': '6000.00', 'method': 'bank_transfer', 'destination': 'A'}); ops = self.staff('operations_admin')
        self.assertEqual(self.req(ops, 'post', '/api/admin/withdrawals/1/action', {'action': 'reject'}).status_code, 422)         # reason required
        self.assertEqual(self.req(ops, 'post', '/api/admin/withdrawals/1/action', {'action': 'reject', 'reason': 'Name mismatch'}).status_code, 200)
        self.assertEqual(self.req(c, 'get', '/api/accounts').json['accounts'][0]['available'], '9000.00')
    def test_overdraft_impossible_under_double_submit(self):
        c = self.signup(kyc=True); self.fund(c, '100.00'); self.db.execute('UPDATE users SET withdrawal_2fa=0')
        a = self.req(c, 'post', '/api/withdrawals', {'amount': '80.00', 'method': 'bank_transfer', 'destination': 'A'}); b = self.req(c, 'post', '/api/withdrawals', {'amount': '80.00', 'method': 'bank_transfer', 'destination': 'A'})
        self.assertEqual((a.status_code, b.status_code), (201, 409))

class TestInvestAndPortfolio(Base):
    def invest(self, c, amount='1000.00'): return self.req(c, 'post', '/api/investments', {'product_id': 1, 'amount': amount, 'accepted_disclosures': True})
    def test_investment_creates_holding_fee_and_ledger(self):
        c = self.signup(); self.fund(c); r = self.invest(c); self.assertEqual(r.status_code, 201, r.json); i = r.json['investment']
        self.assertEqual((i['status'], i['fee'], i['quantity']), ('completed', '5.00', '9.95000000'))     # (1000-5)/100
        s = self.req(c, 'get', '/api/portfolio').json; self.assertEqual(s['summary']['available_cash'], '0.00'); self.assertEqual(s['holdings'][0]['quantity'], '9.95000000')
        self.assertEqual(s['summary']['total_invested'], '1000.00'); self.assertEqual(s['summary']['portfolio_value'], '995.00')   # fee is a real cost
        self.assertTrue(i['non_live'])
    def test_investment_rules(self):
        c = self.signup(); self.fund(c, '500.00')
        self.assertEqual(self.invest(c, '50.00').json['error']['code'], 'below_minimum'); self.assertEqual(self.invest(c, '900.00').json['error']['code'], 'insufficient_funds')
        self.assertEqual(self.req(c, 'post', '/api/investments', {'product_id': 1, 'amount': '200.00'}).json['error']['code'], 'disclosure_required')
        self.db.execute("UPDATE investment_products SET status='paused'"); self.assertEqual(self.invest(c, '200.00').json['error']['code'], 'product_unavailable')
    def test_gain_loss_and_allocation_follow_server_prices(self):
        c = self.signup(); self.fund(c, '2000.00'); self.invest(c, '1000.00')
        self.db.execute("UPDATE assets SET last_price='110.0000' WHERE symbol='VGTF'"); s = self.req(c, 'get', '/api/portfolio').json
        h = s['holdings'][0]; self.assertEqual(h['value'], '1094.50'); self.assertEqual(h['unrealized'], '94.50'); self.assertEqual(s['summary']['unrealized_pl'], '94.50')
        alloc = {a['label']: a['pct'] for a in s['allocation']}; self.assertEqual(alloc['ETFs'], '100.0'); self.assertEqual(alloc['Savings'], '0.0')       # cash is the Account Balance, not part of the portfolio
        sm = s['summary']; self.assertEqual((sm['portfolio_value'], sm['available_cash'], sm['net_worth']), ('1094.50', '1000.00', '2094.50'))
    def test_portfolio_page_payload_is_server_computed(self):
        c = self.signup(); self.fund(c, '2000.00'); self.invest(c, '1000.00')
        self.db.execute("UPDATE assets SET last_price='110.0000' WHERE symbol='VGTF'"); j = self.req(c, 'get', '/api/portfolio').json; s = j['summary']
        self.assertEqual((s['unrealized_pl_pct'], s['realized_pl_pct']), ('9.45', '0.00'))                       # 94.50 / 1000.00 cost basis
        self.assertEqual(j['holdings'][0]['unrealized_pct'], '9.45')
        self.assertEqual([a['label'] for a in j['allocation']], ['Stocks', 'ETFs', 'Bonds', 'Crypto', 'Real Estate', 'Commodities', 'Savings'])   # every class, zero rows included
        self.assertEqual(sum(1 for a in j['allocation'] if a['value'] == '0.00'), 6)
        self.assertEqual({'watchlist', 'recent_activity'} <= set(j), True); self.assertTrue(j['recent_activity'][0]['ref'])
        self.assertEqual({a['label']: a['pct'] for a in j['allocation']}['ETFs'], '100.0')
    def test_portfolio_export_requires_login_and_is_scoped(self):
        self.assertEqual(self.client().get('/api/portfolio/export').status_code, 401)
        c = self.signup(); self.fund(c, '1000.00'); self.invest(c, '1000.00'); r = c.get('/api/portfolio/export')
        self.assertEqual((r.status_code, r.mimetype), (200, 'text/csv')); self.assertIn('VGTF', r.get_data(as_text=True)); self.assertIn('attachment', r.headers['Content-Disposition'])
        b = self.signup('b@example.com'); self.assertNotIn('VGTF', b.get('/api/portfolio/export').get_data(as_text=True))   # another customer never sees these holdings
    def test_client_cannot_set_price_or_balance(self):
        c = self.signup(); self.assertIn(self.req(c, 'post', '/api/assets/1/price', {'price': '1'}).status_code, (404, 405))   # no customer route exists to set prices
        r = self.req(c, 'post', '/api/investments', {'product_id': 1, 'amount': '100.00', 'accepted_disclosures': True, 'balance': '999999'}); self.assertEqual(r.json['error']['code'], 'insufficient_funds')
    def test_market_buy_sell_and_realized_pl(self):
        c = self.signup(); self.fund(c, '1000.00')
        r = self.req(c, 'post', '/api/orders', {'symbol': 'AAPL', 'side': 'buy', 'order_type': 'market', 'quantity': '2'}); self.assertEqual(r.json['order']['status'], 'filled')   # 400 + 0.40 fee
        self.db.execute("UPDATE assets SET last_price='250.0000' WHERE symbol='AAPL'")
        r = self.req(c, 'post', '/api/orders', {'symbol': 'AAPL', 'side': 'sell', 'order_type': 'market', 'quantity': '1'}); self.assertEqual(r.json['order']['status'], 'filled')
        s = self.req(c, 'get', '/api/portfolio').json['summary']; self.assertEqual(s['realized_pl'], '49.55')    # proceeds 249.75 (after fee) minus half of cost basis 400.40 = 200.20
    def test_limit_order_stays_open_then_fills_when_price_crosses(self):
        c = self.signup(); self.fund(c, '1000.00')
        r = self.req(c, 'post', '/api/orders', {'symbol': 'AAPL', 'side': 'buy', 'order_type': 'limit', 'quantity': '1', 'limit_price': '150'}); self.assertEqual(r.json['order']['status'], 'open')
        self.assertEqual(self.req(c, 'get', '/api/accounts').json['accounts'][0]['available'], '849.85')    # 150.15 reserved
        self.assertEqual(fin.match_open_orders(self.db, self.P), 0); fin.set_price(self.db, self.P, 2, '149')
        self.assertEqual(self.req(c, 'get', '/api/orders').json['orders'][0]['status'], 'filled')
    def test_cannot_sell_unowned_or_oversell_with_open_orders(self):
        c = self.signup(); self.fund(c, '1000.00'); sell = lambda q: self.req(c, 'post', '/api/orders', {'symbol': 'AAPL', 'side': 'sell', 'order_type': 'limit', 'limit_price': '900', 'quantity': q})
        self.assertEqual(sell('1').json['error']['code'], 'insufficient_holdings'); self.req(c, 'post', '/api/orders', {'symbol': 'AAPL', 'side': 'buy', 'order_type': 'market', 'quantity': '2'})
        self.assertEqual(sell('2').status_code, 201); self.assertEqual(sell('1').json['error']['code'], 'insufficient_holdings')
    def test_live_mode_never_fakes_execution(self):
        from server import providers as prov; live = prov.LiveTradingProvider(); self.P.trading = live
        c = self.signup(); self.fund(c, '1000.00')
        r = self.req(c, 'post', '/api/orders', {'symbol': 'AAPL', 'side': 'buy', 'order_type': 'market', 'quantity': '1'}); self.assertEqual(r.status_code, 503)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM orders').fetchone()[0], 0)       # rolled back entirely
        self.assertEqual(self.req(c, 'get', '/api/portfolio').json['summary']['available_cash'], '1000.00')

class TestSavingsRetirement(Base):
    def test_transfer_goal_progress_and_completion(self):
        c = self.signup(); self.fund(c, '1000.00')
        g = self.req(c, 'post', '/api/goals', {'name': 'Trip', 'target_amount': '400.00', 'category': 'Vacation'}).json['goal']; self.assertEqual(g['progress_pct'], '0.0')
        r = self.req(c, 'post', f"/api/goals/{g['id']}/contribute", {'amount': '100.00'}); self.assertEqual(r.json['goal']['progress_pct'], '25.0')
        self.assertEqual(self.req(c, 'get', '/api/savings').json['account']['posted'], '100.00'); self.assertEqual(self.req(c, 'get', '/api/accounts').json['accounts'][0]['posted'], '900.00')
        self.assertEqual(self.req(c, 'post', f"/api/goals/{g['id']}/contribute", {'amount': '300.00'}).json['goal']['status'], 'completed')
        g2 = self.req(c, 'post', '/api/goals', {'name': 'Big', 'target_amount': '9000.00'}).json['goal']
        self.assertEqual(self.req(c, 'post', f"/api/goals/{g2['id']}/contribute", {'amount': '5000.00'}).json['error']['code'], 'insufficient_funds')
    def test_other_users_goal_not_accessible(self):
        a = self.signup(); self.fund(a); g = self.req(a, 'post', '/api/goals', {'name': 'x', 'target_amount': '100.00'}).json['goal']; b = self.signup('b@example.com')
        self.assertEqual(self.req(b, 'post', f"/api/goals/{g['id']}/contribute", {'amount': '10.00'}).status_code, 404)
    def test_automatic_contributions_and_skip_when_broke(self):
        c = self.signup(); self.fund(c, '60.00'); self.req(c, 'post', '/api/goals', {'name': 'Auto', 'target_amount': '500.00', 'frequency': 'weekly', 'contribution_amount': '25.00'})
        due = '2999-01-01'; self.assertEqual(fin.run_due_contributions(self.db, due)['ran'], 1)
        self.db.execute('UPDATE automatic_contributions SET next_run=?', ('2000-01-01',)); self.assertEqual(fin.run_due_contributions(self.db, due)['ran'], 1)
        self.db.execute('UPDATE automatic_contributions SET next_run=?', ('2000-01-01',)); r = fin.run_due_contributions(self.db, due); self.assertEqual((r['ran'], r['skipped']), (0, 1))   # only $10 left
    def test_interest_accrual_is_idempotent_per_day(self):
        c = self.signup(); self.fund(c, '10000.00'); self.req(c, 'post', '/api/savings/transfer', {'direction': 'to_savings', 'amount': '10000.00'})
        r1 = fin.accrue_interest(self.db, '2026-10-01'); r2 = fin.accrue_interest(self.db, '2026-10-01'); self.assertEqual(r1['interest'], '1.15'); self.assertEqual(r2['accounts'], 0)   # 10000*4.2%/365
    def test_retirement_projection_math_and_validation(self):
        p = {'current_age': 30, 'retirement_age': 31, 'current_savings_cents': 0, 'monthly_contribution_cents': 10000, 'employer_match_pct': '0', 'expected_return_pct': '0'}
        self.assertEqual(fin.project_retirement(p)['final'], '1200.00'); p['expected_return_pct'] = '12'; self.assertEqual(fin.project_retirement(p)['final'], '1268.25')   # FV of annuity at 1%/mo
        c = self.signup(); body = {'current_age': 30, 'retirement_age': 65, 'current_savings': '1000', 'monthly_contribution': '200', 'employer_match_pct': '50', 'expected_return_pct': '6', 'target': '500000'}
        r = self.req(c, 'put', '/api/retirement', body); self.assertEqual(r.status_code, 200, r.json); self.assertIn('not guarantees', r.json['retirement']['disclaimer'])
        self.assertEqual(self.req(c, 'get', '/api/retirement').json['retirement']['plan']['retirement_age'], 65)
        body['retirement_age'] = 25; self.assertEqual(self.req(c, 'put', '/api/retirement', body).status_code, 422)
        body.update(retirement_age=65, expected_return_pct='40'); self.assertEqual(self.req(c, 'put', '/api/retirement', body).status_code, 422)

class TestPermissions(Base):
    ADMIN_GETS = ['overview', 'users', 'kyc', 'accounts', 'investments', 'assets', 'orders', 'transactions', 'deposits', 'withdrawals', 'savings', 'retirement', 'reports/summary', 'support', 'content', 'audit-logs', 'roles', 'settings']
    def test_anonymous_and_customers_blocked_from_every_admin_route(self):
        cust = self.signup(); anon = self.client()
        for p in self.ADMIN_GETS:
            self.assertEqual(anon.get('/api/admin/' + p).status_code, 401, p); self.assertEqual(cust.get('/api/admin/' + p).status_code, 403, p)
        for url, m in (('/api/admin/users/x/status', 'post'), ('/api/admin/transactions/adjustments', 'post'), ('/api/admin/roles/assign', 'post'), ('/api/admin/settings', 'put'), ('/api/admin/jobs/market-tick', 'post')):
            self.assertEqual(self.req(cust, m, url, {}).status_code, 403, url)
    def test_every_admin_route_rejects_anonymous_and_customers(self):
        cust = self.signup(); anon = self.client(); n = 0
        for rule in self.app.url_map.iter_rules():
            if not rule.rule.startswith('/api/admin/'): continue
            url = re.sub(r'<[^>]+>', '1', rule.rule)
            for m in rule.methods - {'HEAD', 'OPTIONS'}:
                n += 1; self.assertEqual(getattr(anon, m.lower())(url, json={}).status_code, 401, (m, url))
                self.assertEqual(self.req(cust, m.lower(), url, {}).status_code, 403, (m, url))
        self.assertGreater(n, 45)
    def test_role_scoping(self):
        support = self.staff('support_admin'); comp = self.staff('compliance_admin'); content = self.staff('content_admin')
        self.assertEqual(support.get('/api/admin/users').status_code, 200); self.assertEqual(support.get('/api/admin/withdrawals').status_code, 403)
        self.assertEqual(support.get('/api/admin/overview').status_code, 200)                       # any staff may see overview
        self.assertEqual(self.req(support, 'post', '/api/admin/users/x/status', {'status': 'suspended', 'reason': 'x' * 6}).status_code, 403)
        self.assertEqual(comp.get('/api/admin/kyc').status_code, 200); self.assertEqual(comp.get('/api/admin/transactions').status_code, 403)
        self.assertEqual(content.get('/api/admin/content').status_code, 200); self.assertEqual(content.get('/api/admin/users').status_code, 403)
        self.assertEqual(self.req(comp, 'post', '/api/admin/transactions/adjustments', {}).status_code, 403)
    def test_denied_attempts_are_logged(self):
        c = self.signup(); c.get('/api/admin/users'); self.assertGreaterEqual(self.db.execute("SELECT COUNT(*) FROM security_events WHERE type='permission_denied'").fetchone()[0], 1)
    def test_staff_role_cannot_self_escalate(self):
        sup = self.staff('support_admin'); self.assertEqual(self.req(sup, 'post', '/api/admin/roles/assign', {'user': 'support_admin@vaulta.test', 'role': 'super_admin', 'action': 'grant', 'reason': 'please'}).status_code, 403)
    def test_super_admin_cannot_change_own_roles_or_remove_last_super_admin(self):
        sa = self.staff('super_admin'); self.assertEqual(self.req(sa, 'post', '/api/admin/roles/assign', {'user': 'super_admin@vaulta.test', 'role': 'super_admin', 'action': 'revoke', 'reason': 'oops!'}).status_code, 403)
    def test_suspend_user_revokes_session_and_audits(self):
        cust = self.signup(); sa = self.staff('super_admin'); pid = cust.get('/api/auth/me').json['user']['id']
        self.assertEqual(self.req(sa, 'post', f'/api/admin/users/{pid}/status', {'status': 'suspended'}).status_code, 422)     # reason required
        self.assertEqual(self.req(sa, 'post', f'/api/admin/users/{pid}/status', {'status': 'suspended', 'reason': 'Fraud review'}).status_code, 200)
        self.assertEqual(cust.get('/api/auth/me').status_code, 401); self.assertEqual(self.client().post('/api/auth/login', json={'email': 'a@example.com', 'password': PW}).status_code, 403)
        row = self.db.execute("SELECT * FROM audit_logs WHERE action='user.status_changed'").fetchone(); self.assertEqual((row['reason'], json.loads(row['new_value'])['status']), ('Fraud review', 'suspended'))
    def test_kyc_approval_flow_unlocks_withdrawals_and_is_audited(self):
        cust = self.signup(); self.req(cust, 'put', '/api/kyc', {'legal_name': 'T U', 'date_of_birth': '1990-01-01', 'country': 'US', 'address': '1 A St'})
        png = b'\x89PNG\r\n\x1a\n' + b'0' * 20; r = cust.post('/api/kyc/documents', data={'file': (__import__('io').BytesIO(png), 'id.png'), 'doc_type': 'government_id'}, headers={'X-CSRF-Token': cust.csrf}); self.assertEqual(r.status_code, 201, r.json)
        self.assertEqual(self.req(cust, 'post', '/api/kyc/submit').json['kyc']['status'], 'under_review')
        comp = self.staff('compliance_admin'); pid = cust.get('/api/auth/me').json['user']['id']
        self.assertEqual(self.req(comp, 'post', f'/api/admin/kyc/{pid}/decision', {'decision': 'reject'}).status_code, 422)      # reason required for rejection
        self.assertEqual(self.req(comp, 'post', f'/api/admin/kyc/{pid}/decision', {'decision': 'approve'}).status_code, 200)
        self.assertEqual(cust.get('/api/auth/me').json['user']['kyc_status'], 'verified'); self.assertEqual(self.db.execute("SELECT COUNT(*) FROM audit_logs WHERE action='kyc.approve'").fetchone()[0], 1)
    def test_kyc_upload_validates_real_content(self):
        c = self.signup(); r = c.post('/api/kyc/documents', data={'file': (__import__('io').BytesIO(b'<script>alert(1)</script>'), 'evil.png'), 'doc_type': 'government_id'}, headers={'X-CSRF-Token': c.csrf}); self.assertEqual(r.status_code, 415)
    def test_adjustment_requires_reason_and_posts_ledger_and_audit(self):
        cust = self.signup(); fin_ = self.staff('finance_admin'); b = {'user': 'a@example.com', 'direction': 'credit', 'amount': '25.00', 'reason': 'Goodwill credit'}
        self.assertEqual(self.req(fin_, 'post', '/api/admin/transactions/adjustments', {**b, 'reason': 'x'}).status_code, 422)
        self.assertEqual(self.req(fin_, 'post', '/api/admin/transactions/adjustments', b).status_code, 201); self.assertEqual(self.req(cust, 'get', '/api/portfolio').json['summary']['available_cash'], '25.00')
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM audit_logs WHERE action='ledger.adjustment'").fetchone()[0], 1)
    def test_admin_user_detail_masks_phone_and_excludes_secrets(self):
        c = self.signup(); self.req(c, 'put', '/api/profile', {'first_name': 'T', 'last_name': 'U', 'phone': '+15551234567'}); sup = self.staff('support_admin')
        j = sup.get('/api/admin/users/a@example.com').json; self.assertEqual(j['user']['phone'], '•••567'); self.assertNotIn('password', json.dumps(j).lower()); self.assertNotIn('totp_secret', json.dumps(j))
    def test_csv_export_neutralises_formula_injection(self):
        c = self.signup('b@example.com'); self.fund(c); self.db.execute("UPDATE users SET public_id='=CMD()' WHERE email='b@example.com'")
        fin_ = self.staff('finance_admin'); body = fin_.get('/api/admin/reports/transactions.csv').get_data(as_text=True); self.assertIn("'=CMD()", body); self.assertNotIn(',=CMD()', body)

class TestAudit(Base):
    def test_audit_is_append_only_and_hash_chained(self):
        sa = self.staff('super_admin'); self.req(sa, 'put', '/api/admin/settings', {'changes': {'trade_fee_bps': '20'}, 'reason': 'Fee review'}); self.req(sa, 'put', '/api/admin/settings', {'changes': {'trade_fee_bps': '10'}, 'reason': 'Revert fee'})
        self.assertTrue(verify_chain(self.db)['ok'])
        with self.assertRaises(Exception): self.db.execute("UPDATE audit_logs SET reason='tampered'")
        with self.assertRaises(Exception): self.db.execute('DELETE FROM audit_logs')
        r = self.db.execute("SELECT * FROM audit_logs WHERE action='setting.changed' ORDER BY id").fetchall(); self.assertEqual((json.loads(r[0]['prev_value'])['value'], json.loads(r[0]['new_value'])['value']), ('10', '20'))
    def test_tamper_detected_when_triggers_bypassed(self):
        sa = self.staff('super_admin'); self.req(sa, 'put', '/api/admin/settings', {'changes': {'trade_fee_bps': '20'}, 'reason': 'Fee review'})
        self.db.execute('DROP TRIGGER audit_no_update'); self.db.execute("UPDATE audit_logs SET new_value='{\"value\":\"0\"}' WHERE action='setting.changed'")
        self.assertFalse(verify_chain(self.db)['ok'])
    def test_maintenance_mode_blocks_money_movement(self):
        c = self.signup(); sa = self.staff('super_admin'); self.req(sa, 'put', '/api/admin/settings', {'changes': {'maintenance_mode': '1'}, 'reason': 'Incident'})
        self.assertEqual(self.req(c, 'post', '/api/deposits', {'amount': '50.00', 'method': 'card'}).json['error']['code'], 'maintenance')

class TestPublic(Base):
    def test_products_come_from_db_and_hidden_when_paused(self):
        c = self.client(); self.assertEqual(len(c.get('/api/products').json['products']), 1); self.db.execute("UPDATE investment_products SET status='paused'"); self.assertEqual(c.get('/api/products').json['products'], [])
    def test_security_headers_and_static_traversal(self):
        c = self.client(); r = c.get('/login/'); self.assertIn("default-src 'self'", r.headers['Content-Security-Policy']); self.assertEqual(r.headers['X-Frame-Options'], 'DENY')
        for p in ('/../server/config.py', '/%2e%2e/server/config.py', '/.env', '/instance/vaulta.db'): self.assertEqual(c.get(p).status_code, 404, p)
    def test_webhook_requires_valid_signature(self):
        c = self.client(); body = json.dumps({'type': 'deposit.succeeded', 'external_ref': 'x'}).encode()
        self.assertEqual(c.post('/api/webhooks/internal-payments', data=body, headers={'X-Vaulta-Signature': 'bad'}).status_code, 401)
        with self.app.app_context(): sig = sec.sign_payload(body)
        self.assertEqual(c.post('/api/webhooks/internal-payments', data=body, headers={'X-Vaulta-Signature': sig}).status_code, 404)  # valid signature, unknown reference

if __name__ == '__main__': unittest.main()
