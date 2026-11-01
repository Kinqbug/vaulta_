"""Typed platform settings API: persistence, validation, permissions, audit, honest status, no secrets."""
import os, sys, json, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.test_vaulta import Base, PW
from server import rbac

URL = '/api/admin/platform/settings'
class PlatformSettings(Base):
    def put(self, c, changes, reason='test change'): return self.req(c, 'put', URL, {'changes': changes, 'reason': reason})
    def setUp(self): super().setUp(); self.a = self.staff('super_admin')

    def test_get_shape_and_readonly_site_url(self):
        j = self.a.get(URL).json
        for k in ('name', 'support_email', 'language', 'timezone', 'site_url'): self.assertIn(k, j['general'])
        self.assertEqual(sorted(j['switches']), ['live_prices_enabled', 'maintenance_mode', 'registration_enabled', 'require_2fa_all'])
        self.assertFalse(j['general']['site_url']['editable']); self.assertEqual(j['general']['name']['value'], 'VAULTA')
        self.assertEqual(j['switches']['registration_enabled']['value'], True); self.assertEqual(j['switches']['maintenance_mode']['value'], False)
        self.assertIn('UTC', j['options']['timezone']); self.assertEqual([o['value'] for o in j['options']['language']], ['en'])
        self.assertEqual({u['key'] for u in j['unavailable']}, {'admin_email', 'notification_email', 'email_alerts', 'daily_reports', 'min_withdrawal', 'withdrawal_processing_time'})

    def test_save_persists_audits_and_reaches_public_status(self):
        r = self.put(self.a, {'name': ' Vaulta Test ', 'support_email': 'Help@Example.com', 'timezone': 'Africa/Lagos', 'language': 'en'}); self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(sorted(r.json['changed']), ['name', 'support_email', 'timezone'])          # language unchanged: no write, no audit noise
        g = self.a.get(URL).json['general']; self.assertEqual((g['name']['value'], g['support_email']['value'], g['timezone']['value']), ('Vaulta Test', 'help@example.com', 'Africa/Lagos'))
        pub = self.client().get('/api/platform/status').json; self.assertEqual((pub['name'], pub['support_email']), ('Vaulta Test', 'help@example.com'))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM audit_logs WHERE action='setting.changed' AND entity_id LIKE 'platform.%'").fetchone()[0], 3)
        self.assertEqual(self.put(self.a, {'name': 'Vaulta Test'}).json['changed'], [])

    def test_validation_is_server_side_field_level_and_atomic(self):
        r = self.put(self.a, {'name': '<b>x</b>', 'support_email': 'nope', 'language': 'fr', 'timezone': 'Mars/Base', 'api_rate_limit_per_hour': '-1'})
        self.assertEqual(r.status_code, 422); self.assertEqual(set(r.json['error']['details']['fields']), {'name', 'support_email', 'language', 'timezone', 'api_rate_limit_per_hour'})
        r = self.put(self.a, {'name': 'Good Name', 'timezone': 'Mars/Base'}); self.assertEqual(r.status_code, 422)
        self.assertEqual(self.a.get(URL).json['general']['name']['value'], 'VAULTA')            # the valid half was NOT saved
        self.assertEqual(self.put(self.a, {'site_url': 'https://evil.example'}).status_code, 422)  # read-only
        self.assertEqual(self.put(self.a, {'name': 'X'}, reason='no').status_code, 422)
        self.assertEqual(self.put(self.a, {}).status_code, 422)

    def test_permissions(self):
        s = self.staff('support_admin'); self.assertEqual(s.get(URL).status_code, 403); self.assertEqual(self.put(s, {'name': 'Hacked'}).status_code, 403)
        self.assertEqual(self.client().get(URL).status_code, 401); self.assertEqual(self.signup('c@example.com').get(URL).status_code, 403)
        self.assertEqual(self.a.get(URL).json['general']['name']['value'], 'VAULTA')

    def test_financial_values_are_decimal_safe_and_read_by_the_real_code(self):
        for bad in ('10.005', '-1', 'abc', '1e3', 'NaN', '99999999999'): self.assertEqual(self.put(self.a, {'min_deposit': bad}).status_code, 422, bad)
        for bad in ('0.001', '10.01', '-0.5'): self.assertEqual(self.put(self.a, {'trade_fee_percent': bad}).status_code, 422, bad)
        self.assertEqual(self.put(self.a, {'min_deposit': '999999.00'}).status_code, 422)       # above the maximum deposit
        self.assertEqual(self.put(self.a, {'min_deposit': '25.50', 'trade_fee_percent': '0.15'}).status_code, 200)
        self.assertEqual((rbac.setting(self.db, 'min_deposit_cents'), rbac.setting(self.db, 'trade_fee_bps')), (2550, 15))
        f = self.a.get(URL).json['financial']; self.assertEqual((f['min_deposit']['value'], f['trade_fee_percent']['value']), ('25.50', '0.15'))
        c = self.signup('d@example.com', kyc=True); r = self.req(c, 'post', '/api/deposits', {'amount': '20.00', 'method': 'bank_transfer'})
        self.assertEqual((r.status_code, r.json['error']['code']), (422, 'amount_out_of_range'))   # the real deposit path enforces it

    def test_api_rate_limit_is_enforced_for_customers_not_staff(self):
        c = self.signup('r@example.com'); self.assertEqual(self.put(self.a, {'api_rate_limit_per_hour': '3'}).status_code, 200)
        self.app.config['RATE_LIMIT_ENABLED'] = True
        codes = [c.get('/api/portfolio').status_code for _ in range(4)]; self.assertEqual(codes, [200, 200, 200, 429])
        for _ in range(6): self.assertEqual(self.a.get(URL).status_code, 200)                  # staff exempt
        self.put(self.a, {'api_rate_limit_per_hour': '0'}); self.assertEqual(c.get('/api/portfolio').status_code, 200)

    def test_system_info_is_real_and_leaks_nothing(self):
        os.environ.pop('VAULTA_VERSION', None); j = self.a.get('/api/admin/platform/system-info').json
        self.assertIsNone(j['version']); self.assertTrue(j['version_note']); self.assertEqual(j['environment'], 'development'); self.assertGreaterEqual(j['uptime_seconds'], 0)
        self.assertIn(j['last_updated_source'], ('environment', 'code_modified')); self.assertNotIn('/home', json.dumps(j)); self.assertNotIn(self.tmp, json.dumps(j))
        os.environ['VAULTA_VERSION'] = '2.3.4'
        try: self.assertEqual(self.a.get('/api/admin/platform/system-info').json['version'], '2.3.4')
        finally: os.environ.pop('VAULTA_VERSION', None)

    def test_integration_status_is_truthful_and_secret_free(self):
        self.app.config.update({'PAYMENT_PROVIDER_KEY': 'sk_live_SECRET123', 'KYC_PROVIDER_KEY': 'kyc_SECRET456', 'EMAIL_PROVIDER_KEY': 'em_SECRET789'})
        j = self.a.get(URL).json; blob = json.dumps(j); st = {i['key']: i['state'] for i in j['integrations']}
        for s in ('SECRET123', 'SECRET456', 'SECRET789'): self.assertNotIn(s, blob)
        self.assertEqual((st['analytics'], st['google_analytics'], st['livechat']), ('unavailable',) * 3)
        self.assertEqual(st['payments'], 'not_configured'); self.assertEqual(st['kyc'], 'configured'); self.assertIn('manage_templates', j)

    def test_legacy_settings_list_hides_typed_keys_and_still_works(self):
        self.put(self.a, {'name': 'Zed'}); keys = [s['key'] for s in self.a.get('/api/admin/settings').json['settings']]
        self.assertNotIn('platform.name', keys); self.assertIn('registration_enabled', keys); self.assertIn('api_rate_limit_per_hour', keys)

    # ---- stage 4: every existing setting stays editable, with units, ranges and cross-checks ----
    def test_no_existing_setting_is_orphaned_by_the_new_settings_ui(self):
        from server import platform_settings as ps
        from server.mailer import routes as mr
        covered = {e['store'] for e in ps.REG.values()} | set(mr.KEYS)            # email_* keys are edited through the mailer's own endpoint
        self.assertEqual(sorted(set(rbac.SETTINGS) - covered), [])
        j = self.a.get(URL).json
        self.assertEqual(sorted(n for g in ('security', 'payments') for n in j[g]), sorted(n for n, e in ps.REG.items() if e['group'] in ('security', 'payments')))
        self.assertEqual(j['payments']['card_fee_percent']['value'], '1.50'); self.assertEqual(j['payments']['crypto_max_pending_requests']['value'], 5)
        self.assertEqual(j['payments']['fiat_funding_enabled']['value'], bool(rbac.setting(self.db, 'fiat_funding_enabled'))); self.assertEqual(j['security']['risk_ack_required']['kind'], 'flag')
        self.assertEqual((j['payments']['crypto_intent_ttl_hours']['min'], j['payments']['crypto_intent_ttl_hours']['max']), (1, 720))

    def test_security_and_payment_values_round_trip_into_the_numbers_the_code_reads(self):
        f0, s0 = rbac.setting(self.db, 'fiat_funding_enabled'), rbac.setting(self.db, 'crypto_screenshot_required')       # flip them whatever the fixture started with
        r = self.put(self.a, {'card_fee_percent': '1.75', 'savings_apy_percent': '4.20', 'daily_withdrawal_limit': '12345.67', 'twofa_required_above': '500', 'crypto_intent_ttl_hours': '48',
                              'fiat_funding_enabled': not f0, 'risk_ack_required': False, 'crypto_screenshot_required': not s0})
        self.assertEqual(r.status_code, 200, r.json)
        v = lambda k: rbac.setting(self.db, k)
        self.assertEqual((v('card_fee_bps'), v('savings_apy_bps'), v('daily_withdrawal_limit_cents'), v('twofa_required_above_cents'), v('crypto_intent_ttl_hours')), (175, 420, 1234567, 50000, 48))
        self.assertEqual((v('fiat_funding_enabled'), v('risk_ack_required'), v('crypto_screenshot_required')), (int(not f0), 0, int(not s0)))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM audit_logs WHERE action='setting.changed'").fetchone()[0], len(r.json['changed']))   # one audit row per real change
        self.assertGreaterEqual(len(r.json['changed']), 7)

    def test_security_policy_facts_are_read_only_and_come_from_live_config(self):
        pol = {x['key']: x for x in self.a.get(URL).json['policy']}
        self.assertEqual(pol['session_idle_minutes']['value'], self.app.config['SESSION_IDLE_MINUTES']); self.assertEqual(pol['password_min_length']['value'], 10)
        self.assertEqual(self.put(self.a, {'session_idle_minutes': 5}).status_code, 422)                          # not editable through this API
        weak = self.client().post('/api/auth/register', json={'email': 'w@example.com', 'password': 'short', 'first_name': 'W', 'last_name': 'W', 'accepted_terms': True, 'terms_version': 1, 'privacy_version': 1})
        self.assertEqual(weak.json['error']['code'], 'weak_password')

    def test_ranges_and_deposit_cross_checks(self):
        for k, bad in (('crypto_intent_ttl_hours', '0'), ('crypto_intent_ttl_hours', '721'), ('crypto_max_pending_requests', '0'), ('crypto_spread_percent', '10.01'), ('savings_apy_percent', '25.01'),
                       ('card_fee_percent', '-1'), ('daily_withdrawal_limit', '1e5'), ('fiat_funding_enabled', 'maybe'), ('crypto_price_max_age_minutes', '10081')):
            self.assertEqual(self.put(self.a, {k: bad}).status_code, 422, (k, bad))
        r = self.put(self.a, {'max_deposit': '5.00'}); self.assertEqual((r.status_code, 'max_deposit' in r.json['error']['details']['fields']), (422, True))     # below the 10.00 minimum
        self.assertEqual(self.put(self.a, {'min_deposit': '5.00', 'max_deposit': '500.00'}).status_code, 200)
        self.assertEqual(self.put(self.a, {'min_deposit': '600.00'}).status_code, 422)
        c = self.signup('m@example.com', kyc=True); r = self.req(c, 'post', '/api/deposits', {'amount': '501.00', 'method': 'bank_transfer'})
        self.assertEqual((r.status_code, r.json['error']['code']), (422, 'amount_out_of_range'))                       # the real deposit path reads max_deposit

if __name__ == '__main__': unittest.main()
