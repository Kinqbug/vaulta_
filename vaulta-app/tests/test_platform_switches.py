"""The four Display Settings switches must change real server behaviour (not only the UI)."""
import os, sys, unittest, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.test_vaulta import Base, PW
from server import marketdata as md, platform_cli, util

def put(t, c, changes, reason='test change'): return t.req(c, 'put', '/api/admin/settings', {'changes': changes, 'reason': reason})

class Switches(Base):
    def reg(self, c, email='n@example.com'):
        return c.post('/api/auth/register', json={'email': email, 'password': PW, 'first_name': 'N', 'last_name': 'U', 'accepted_terms': True, 'terms_version': 1, 'privacy_version': 1})
    def admin(self): return self.staff('super_admin')

    # ---- registration ----
    def test_registration_off_blocks_server_side_and_audits(self):
        a = self.admin(); self.assertEqual(put(self, a, {'registration_enabled': '0'}).status_code, 200)
        r = self.reg(self.client()); self.assertEqual(r.status_code, 403); self.assertEqual(r.json['error']['code'], 'registration_closed')
        self.assertIsNone(self.db.execute("SELECT 1 FROM users WHERE email='n@example.com'").fetchone())
        self.assertEqual(self.client().get('/api/platform/status').json['registration_enabled'], False)
        self.assertTrue(self.db.execute("SELECT 1 FROM audit_logs WHERE action='setting.changed' AND entity_id='registration_enabled'").fetchone())
        self.assertEqual(self.client().post('/api/auth/login', json={'email': 'super_admin@vaulta.test', 'password': PW}).status_code, 200)   # existing accounts unaffected
        put(self, a, {'registration_enabled': '1'}); self.assertEqual(self.reg(self.client()).status_code, 201)

    # ---- maintenance ----
    def test_maintenance_blocks_customers_not_staff_and_login_still_works(self):
        cust = self.signup('c@example.com'); a = self.admin()
        self.assertEqual(cust.get('/api/portfolio').status_code, 200)
        self.assertEqual(put(self, a, {'maintenance_mode': '1'}).status_code, 200)
        r = cust.get('/api/portfolio'); self.assertEqual((r.status_code, r.json['error']['code']), (503, 'maintenance'))
        self.assertEqual(self.client().get('/api/products').status_code, 503)
        self.assertEqual(cust.get('/api/auth/me').status_code, 200)                         # session check still works
        self.assertEqual(self.client().get('/api/health').status_code, 200)
        self.assertTrue(self.client().get('/api/platform/status').json['maintenance'])
        self.assertEqual(self.client().post('/api/auth/login', json={'email': 'c@example.com', 'password': PW}).status_code, 200)   # can sign in to see the notice
        self.assertEqual(self.req(a, 'get', '/api/admin/settings').status_code, 200)       # staff keep working: they can turn it off again
        self.assertEqual(put(self, a, {'maintenance_mode': '0'}).status_code, 200); self.assertEqual(cust.get('/api/portfolio').status_code, 200)
    def test_maintenance_still_blocks_money_movement_for_staff_accounts_as_customers(self):
        c = self.signup('c2@example.com', kyc=True); a = self.admin(); put(self, a, {'maintenance_mode': '1'})
        self.assertEqual(self.req(c, 'post', '/api/deposits', {'amount': '50.00', 'method': 'bank_transfer'}).status_code, 503)
    def test_cli_can_switch_maintenance_off_without_login(self):
        a = self.admin(); put(self, a, {'maintenance_mode': '1'}); self.db.commit()
        os.environ['DATABASE_PATH'] = self.app.config['DATABASE_PATH']
        from server.config import Config; old = Config.DATABASE_PATH; Config.DATABASE_PATH = self.app.config['DATABASE_PATH']
        try: self.assertEqual(platform_cli.main(['maintenance', 'off']), 0)
        finally: Config.DATABASE_PATH = old
        self.assertFalse(self.client().get('/api/platform/status').json['maintenance'])
        self.assertTrue(self.db.execute("SELECT 1 FROM audit_logs WHERE reason='cli'").fetchone())

    # ---- live prices ----
    def test_live_prices_off_never_calls_provider_and_serves_stale_cache(self):
        calls = []
        def fetch(): calls.append(1); return {'x': 1}
        data, meta = md.cached(self.db, 'k1', 60, fetch); self.assertEqual((len(calls), meta['stale']), (1, False))
        a = self.admin(); put(self, a, {'live_prices_enabled': '0'}); md._mem['k1'] = (data, time.time() - 999)   # expired: would normally refetch
        data2, meta2 = md.cached(self.db, 'k1', 60, fetch)
        self.assertEqual(len(calls), 1); self.assertTrue(meta2['stale']); self.assertEqual(meta2['stale_reason'], 'paused'); self.assertEqual(data2, {'x': 1})
        with self.assertRaises(Exception) as cm: md.cached(self.db, 'never-cached', 60, fetch)
        self.assertEqual(cm.exception.code, 'market_data_paused'); self.assertEqual(len(calls), 1)    # no price is invented
        put(self, a, {'live_prices_enabled': '1'}); md.cached(self.db, 'k1', 60, fetch); self.assertEqual(len(calls), 2)

    # ---- two-factor ----
    def test_require_2fa_all_limits_accounts_without_2fa_to_enrolment(self):
        a = self.admin(); cust = self.signup('c3@example.com')
        r = put(self, a, {'require_2fa_all': '1'}); self.assertEqual((r.status_code, r.json['error']['code']), (409, 'enable_own_2fa_first'))   # no self-lockout
        self.db.execute("UPDATE users SET totp_enabled=1 WHERE email='super_admin@vaulta.test'"); a2 = self.client()
        self.assertEqual(put(self, a, {'require_2fa_all': '1'}).status_code, 200)
        r = cust.get('/api/portfolio'); self.assertEqual((r.status_code, r.json['error']['code']), (403, 'two_factor_setup_required'))
        self.assertEqual(cust.get('/api/auth/me').status_code, 200)
        self.assertEqual(self.req(cust, 'post', '/api/auth/2fa/setup').status_code, 200)    # can still enrol
        self.assertEqual(a.get('/api/portfolio').status_code, 200)                            # accounts with 2FA are unaffected
        self.db.execute("UPDATE users SET totp_enabled=1 WHERE email='c3@example.com'"); self.assertEqual(cust.get('/api/portfolio').status_code, 200)

    # ---- validation / permission ----
    def test_switch_values_validated_and_permission_required(self):
        a = self.admin(); self.assertEqual(put(self, a, {'maintenance_mode': '7'}).status_code, 422)
        s = self.staff('support_admin'); self.assertEqual(put(self, s, {'maintenance_mode': '1'}).status_code, 403)
        self.assertFalse(self.client().get('/api/platform/status').json['maintenance'])

if __name__ == '__main__': unittest.main()
