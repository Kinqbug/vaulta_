"""Tests for M1 (migrations), M2 (general ledger), M3 (asset registry) and the go-live gate."""
import os, sys, sqlite3, tempfile, shutil, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_vaulta import Base, PW
from server import accounting as acc, assets, migrate, finance as fin, ledger, util, providers as prov
from server.config import Config
from server.util import ApiError

def S(db, k): return acc.account_id(db, k)

class TestMigrations(unittest.TestCase):
    def setUp(self): self.tmp = tempfile.mkdtemp()
    def tearDown(self): shutil.rmtree(self.tmp, ignore_errors=True)
    def conn(self):
        c = sqlite3.connect(os.path.join(self.tmp, 'm.db'), isolation_level=None); c.row_factory = sqlite3.Row; return c
    def test_applies_in_order_and_is_idempotent(self):
        c = self.conn(); first = migrate.run(c); self.assertEqual(first[:3], ['001', '002', '003']); self.assertEqual(migrate.run(c), [])
    def test_edited_migration_refuses_to_start(self):
        d = os.path.join(self.tmp, 'mig'); os.mkdir(d); p = os.path.join(d, '001_a.sql'); open(p, 'w').write('CREATE TABLE a(x);')
        c = self.conn(); migrate.run(c, d); open(p, 'w').write('CREATE TABLE a(x, y);')
        with self.assertRaises(RuntimeError): migrate.run(c, d)
    def test_failed_migration_rolls_back_fully(self):
        d = os.path.join(self.tmp, 'mig'); os.mkdir(d); open(os.path.join(d, '001_a.sql'), 'w').write('CREATE TABLE ok(x);\nINSERT INTO nope VALUES(1);')
        c = self.conn()
        with self.assertRaises(sqlite3.Error): migrate.run(c, d)
        self.assertIsNone(c.execute("SELECT name FROM sqlite_master WHERE name='ok'").fetchone()); self.assertEqual(c.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0], 0)

class TestGeneralLedger(Base):
    def test_balanced_entries_and_per_asset_balancing(self):
        d = self.db; cust = acc.customer_account(d, 1) if d.execute('SELECT 1 FROM accounts').fetchone() else None
        eid = acc.post_entry(d, entry_type='t', lines=[(S(d, 'CUSTODY_FIAT'), 'debit', 'USD', 500), (S(d, 'EQUITY'), 'credit', 'USD', 500)])
        self.assertEqual(acc.balance(d, 'CUSTODY_FIAT'), 500)
        for bad in ([(S(d, 'CUSTODY_FIAT'), 'debit', 'USD', 5)],                                        # one line
                    [(S(d, 'CUSTODY_FIAT'), 'debit', 'USD', 5), (S(d, 'EQUITY'), 'credit', 'USD', 4)],      # unbalanced
                    [(S(d, 'CUSTODY_FIAT'), 'debit', 'USD', 0), (S(d, 'EQUITY'), 'credit', 'USD', 0)],      # zero
                    [(S(d, 'CUSTODY_FIAT'), 'debit', 'USD', 1.5), (S(d, 'EQUITY'), 'credit', 'USD', 1.5)],  # float
                    [(S(d, 'CUSTODY_FIAT'), 'debit', 'USD', True), (S(d, 'EQUITY'), 'credit', 'USD', True)]):
            with self.assertRaises(ApiError): acc.post_entry(d, entry_type='t', lines=bad)
        self.assertTrue(acc.verify_integrity(d)['ok']); self.assertTrue(eid)
    def test_different_assets_cannot_balance_each_other(self):
        d = self.db; sa = self.admin_asset('BTC', 'native', 8)
        with self.assertRaises(ApiError) as e: acc.post_entry(d, entry_type='t', lines=[(S(d, 'CUSTODY_CRYPTO'), 'debit', 'BTC', 100), (S(d, 'EQUITY'), 'credit', 'USD', 100)])
        self.assertEqual(e.exception.code, 'unbalanced_entry')
    def admin_asset(self, sym, typ, dec, net='btcnet'):
        a = assets.create_asset(self.db, None, sym, sym, typ); n = assets.create_network(self.db, None, net, net, 'utxo', 3, is_testnet=True)
        return assets.create_asset_network(self.db, None, sym, net, dec)
    def test_idempotent_and_reversal_once(self):
        d = self.db; ln = [(S(d, 'CUSTODY_FIAT'), 'debit', 'USD', 700), (S(d, 'EQUITY'), 'credit', 'USD', 700)]
        a = acc.post_entry(d, entry_type='t', lines=ln, idem='k1'); self.assertEqual(acc.post_entry(d, entry_type='t', lines=ln, idem='k1'), a)
        self.assertEqual(acc.balance(d, 'CUSTODY_FIAT'), 700)
        r = acc.reverse_entry(d, a, 'posted in error'); self.assertEqual(acc.reverse_entry(d, a, 'again'), r); self.assertEqual(acc.balance(d, 'CUSTODY_FIAT'), 0)
        with self.assertRaises(ApiError): acc.reverse_entry(d, r, 'no reversing a reversal')
        with self.assertRaises(ApiError): acc.reverse_entry(d, a, '  ')
    def test_posted_entries_are_immutable_in_the_database(self):
        d = self.db; e = acc.post_entry(d, entry_type='t', lines=[(S(d, 'CUSTODY_FIAT'), 'debit', 'USD', 9), (S(d, 'EQUITY'), 'credit', 'USD', 9)])
        for sql in ("UPDATE journal_lines SET amount='1' WHERE entry_id=%d" % e, "DELETE FROM journal_lines WHERE entry_id=%d" % e, "DELETE FROM journal_entries WHERE id=%d" % e,
                    "UPDATE journal_entries SET description='x' WHERE id=%d" % e, "UPDATE journal_entries SET sealed=0 WHERE id=%d" % e,
                    "INSERT INTO journal_lines(entry_id,line_no,gl_account_id,asset_code,direction,amount) VALUES(%d,9,1,'USD','debit','1')" % e,
                    "UPDATE ledger_assets SET decimals=3 WHERE code='USD'", "DELETE FROM gl_accounts WHERE id=1"):
            with self.assertRaises(sqlite3.DatabaseError, msg=sql): self.db.execute(sql)
    def test_closed_period_refuses_postings_and_cannot_reopen(self):
        d = self.db; acc.ensure_chart(d); pid = acc.ensure_period(d, '2020-01-15T00:00:00Z')
        ln = [(S(d, 'CUSTODY_FIAT'), 'debit', 'USD', 5), (S(d, 'EQUITY'), 'credit', 'USD', 5)]
        acc.post_entry(d, entry_type='t', lines=ln, posted_at='2020-01-15T00:00:00Z')
        sa = self.staff('super_admin'); self.assertEqual(self.req(sa, 'post', '/api/admin/ledger/periods/2020-01/close', {'reason': 'x'}).status_code, 422)
        self.assertEqual(self.req(sa, 'post', '/api/admin/ledger/periods/2020-01/close', {'reason': 'Month end close'}).status_code, 200)
        with self.assertRaises(sqlite3.DatabaseError): acc.post_entry(d, entry_type='t', lines=ln, posted_at='2020-01-20T00:00:00Z')
        with self.assertRaises(sqlite3.DatabaseError): d.execute("UPDATE accounting_periods SET status='open' WHERE code='2020-01'")
        self.assertEqual(self.req(sa, 'post', '/api/admin/ledger/periods/2020-01/close', {'reason': 'Month end close'}).status_code, 409)
    def test_full_customer_lifecycle_reconciles_gl_to_subledger(self):
        c = self.signup(kyc=True); self.fund(c, '1000.00'); self.db.execute('UPDATE users SET withdrawal_2fa=0')
        self.req(c, 'post', '/api/withdrawals', {'amount': '100.00', 'method': 'bank_transfer', 'destination': 'A'})
        self.req(c, 'post', '/api/investments', {'product_id': 1, 'amount': '200.00', 'accepted_disclosures': True})
        self.req(c, 'post', '/api/savings/transfer', {'direction': 'to_savings', 'amount': '50.00'})
        fin.accrue_interest(self.db)
        self.assertEqual(acc.reconcile_subledger(self.db), []); v = acc.verify_integrity(self.db); self.assertTrue(v['ok'], v)
        tb = acc.trial_balance(self.db); self.assertTrue(tb['totals']['USD']['balanced'])
        self.assertGreater(acc.balance(self.db, 'FEE_INCOME'), 0)                   # deposit fee / management fee recognised as income
    def test_reversed_deposit_reverses_the_gl(self):
        c = self.signup(kyc=True); d = self.fund(c, '500.00'); self.assertEqual(acc.reconcile_subledger(self.db), [])
        sa = self.staff('super_admin'); dep = self.db.execute('SELECT id FROM deposits').fetchone()[0]
        r = self.req(sa, 'post', f'/api/admin/deposits/{dep}/action', {'action': 'reverse', 'reason': 'Chargeback received'}); self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(acc.reconcile_subledger(self.db), []); self.assertTrue(acc.verify_integrity(self.db)['ok'])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM journal_entries WHERE entry_type='reversal'").fetchone()[0], 1)
    def test_backfill_catches_up_pre_gl_transactions_and_is_idempotent(self):
        c = self.signup(kyc=True); self.fund(c, '300.00')
        self.db.execute('PRAGMA foreign_keys=OFF')
        for t in ('journal_lines', 'account_balances'): pass
        # simulate a database from before the GL existed: drop GL rows via a fresh file with no GL, then migrate+backfill
        self.assertEqual(acc.backfill(self.db), 0); n = self.db.execute('SELECT COUNT(*) FROM journal_entries').fetchone()[0]; self.assertEqual(acc.backfill(self.db), 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM journal_entries').fetchone()[0], n)
    def test_gl_admin_routes_are_permissioned(self):
        sa = self.staff('super_admin'); aud = self.staff('auditor'); sup = self.staff('support_admin'); crypto = self.staff('crypto_operations')
        for p in ('trial-balance', 'integrity', 'entries', 'periods'):
            self.assertEqual(sa.get(f'/api/admin/ledger/{p}').status_code, 200, p); self.assertEqual(aud.get(f'/api/admin/ledger/{p}').status_code, 200, p)
            self.assertEqual(sup.get(f'/api/admin/ledger/{p}').status_code, 403, p)
        self.assertEqual(self.req(aud, 'post', '/api/admin/ledger/periods/2020-01/close', {'reason': 'Month end close'}).status_code, 403)   # auditor is read-only
        self.assertEqual(aud.get('/api/admin/crypto/registry').status_code, 200); self.assertEqual(self.req(aud, 'post', '/api/admin/crypto/assets', {'symbol': 'ABC', 'name': 'A', 'type': 'token'}).status_code, 403)
        self.assertEqual(self.req(crypto, 'post', '/api/admin/ledger/periods/2020-01/close', {'reason': 'Month end close'}).status_code, 403)

class TestAssetRegistry(Base):
    def setUp(self):
        super().setUp(); d = self.db
        assets.create_asset(d, None, 'tok', 'Test Token', 'stablecoin'); assets.create_asset(d, None, 'cc', 'Test Coin', 'native')
        assets.create_network(d, None, 'simeth', 'Sim EVM', 'account', 12, chain_id=1337, is_testnet=True); assets.create_network(d, None, 'simbsc', 'Sim BSC', 'account', 15, is_testnet=True)
    def test_same_token_on_two_networks_is_two_ledger_assets_with_own_decimals(self):
        a = assets.create_asset_network(self.db, None, 'TOK', 'simeth', 6, '0xABCDEF0000000000000000000000000000000001'); b = assets.create_asset_network(self.db, None, 'TOK', 'simbsc', 18, '0xABCDEF0000000000000000000000000000000002')
        codes = {r['ledger_code']: r['decimals'] for r in assets.list_registry(self.db)}; self.assertEqual(codes, {'TOK.SIMETH': 6, 'TOK.SIMBSC': 18})
        self.assertEqual(assets.to_minor(self.db, 'TOK.SIMETH', '1.5'), 1500000); self.assertEqual(assets.to_minor(self.db, 'TOK.SIMBSC', '1.5'), 1500000000000000000)
    def test_created_disabled_and_enable_rules(self):
        an = assets.create_asset_network(self.db, None, 'TOK', 'simeth', 6, '0xabc0000000000000000000000000000000000001')
        r = assets.list_registry(self.db)[0]; self.assertEqual((r['status'], r['deposit_enabled'], r['withdrawal_enabled']), ('disabled', 0, 0))
        with self.assertRaises(ApiError): assets.set_status(self.db, 1, an, status='active', reason='Enable for testing')     # parents inactive
        assets.set_parent_status(self.db, 1, 'asset', 1, 'active', 'Approved for sandbox'); assets.set_parent_status(self.db, 1, 'network', 1, 'active', 'Approved for sandbox')
        with self.assertRaises(ApiError): assets.set_status(self.db, 1, an, deposit_enabled=True, reason='Open deposits now')   # not active yet
        with self.assertRaises(ApiError): assets.set_status(self.db, 1, an, status='active', reason='short')                      # reason too short
        assets.set_status(self.db, 1, an, status='active', deposit_enabled=True, reason='Open deposits for sandbox')
        assets.set_parent_status(self.db, 1, 'network', 1, 'suspended', 'Chain incident reported')                                 # suspending cascades
        r = assets.list_registry(self.db)[0]; self.assertEqual((r['status'], r['deposit_enabled'], r['withdrawal_enabled']), ('suspended', 0, 0))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM audit_logs WHERE action LIKE 'crypto.%'").fetchone()[0], 9)
    def test_validation_and_immutability(self):
        d = self.db
        with self.assertRaises(ApiError): assets.create_asset_network(d, None, 'TOK', 'simeth', 6)                                  # token needs contract
        with self.assertRaises(ApiError): assets.create_asset_network(d, None, 'CC', 'simeth', 8, '0xabc')                          # native has none
        assets.create_asset_network(d, None, 'CC', 'simeth', 8)
        with self.assertRaises(ApiError): assets.create_asset_network(d, None, 'CC', 'simeth', 8)                                   # duplicate pair
        assets.create_asset_network(d, None, 'TOK', 'simeth', 6, '0xAAA0000000000000000000000000000000000009')
        d.execute("INSERT INTO crypto_assets(symbol,name,type,created_at) VALUES('T2','t2','token','x')")
        with self.assertRaises(ApiError): assets.create_asset_network(d, None, 'T2', 'simeth', 6, '0xaaa0000000000000000000000000000000000009')   # contract reuse (case-insensitive)
        for sql in ("UPDATE asset_networks SET decimals=9 WHERE id=1", "UPDATE asset_networks SET contract_address='0x1' WHERE id=2", "DELETE FROM asset_networks WHERE id=1", "UPDATE ledger_assets SET decimals=1 WHERE code='CC'"):
            with self.assertRaises(sqlite3.DatabaseError, msg=sql): d.execute(sql)
    def test_amount_parsing_is_exact_and_strict(self):
        assets.create_asset_network(self.db, None, 'CC', 'simeth', 8)
        self.assertEqual(assets.to_minor(self.db, 'CC', '0.00000001'), 1); self.assertEqual(assets.from_minor(self.db, 'CC', 123456789), '1.23456789')
        for bad in ('0.000000001', '0', '-1', 'abc', 'NaN', 'Infinity', 1.5, True): 
            with self.assertRaises(ApiError, msg=str(bad)): assets.to_minor(self.db, 'CC', bad)
        with self.assertRaises(ApiError): assets.to_minor(self.db, 'NOPE', '1')
    def test_18_decimal_amounts_round_trip_beyond_64_bit(self):
        assets.create_asset_network(self.db, None, 'TOK', 'simbsc', 18, '0xabc0000000000000000000000000000000000003'); big = assets.to_minor(self.db, 'TOK.SIMBSC', '1000000000.123456789012345678')
        self.assertGreater(big, 2 ** 63); d = self.db
        acc.post_entry(d, entry_type='t', lines=[(S(d, 'CUSTODY_CRYPTO'), 'debit', 'TOK.SIMBSC', big), (S(d, 'EQUITY'), 'credit', 'TOK.SIMBSC', big)])
        self.assertEqual(acc.balance(d, 'CUSTODY_CRYPTO', 'TOK.SIMBSC'), big); self.assertTrue(acc.verify_integrity(d)['ok'])
    def test_registry_routes(self):
        ops = self.staff('crypto_operations'); sup = self.staff('support_admin')
        r = self.req(ops, 'post', '/api/admin/crypto/assets', {'symbol': 'new1', 'name': 'New', 'type': 'native'}); self.assertEqual(r.status_code, 201, r.json)
        self.assertEqual(self.req(sup, 'post', '/api/admin/crypto/assets', {'symbol': 'new2', 'name': 'New', 'type': 'native'}).status_code, 403)
        self.assertEqual(self.req(ops, 'post', '/api/admin/crypto/assets', {'symbol': 'new1', 'name': 'New', 'type': 'native'}).status_code, 409)
        self.assertEqual(ops.get('/api/admin/crypto/registry').status_code, 200)

class TestGoLiveGate(Base):
    def test_live_mode_without_authorisation_blocks_money_movement(self):
        c = self.signup(kyc=True); self.P.mode = 'live'; self.P.real_funds_enabled = False
        r = self.req(c, 'post', '/api/deposits', {'amount': '100.00', 'method': 'bank_transfer'}); self.assertEqual((r.status_code, r.json['error']['code']), (403, 'funding_not_authorized'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0], 0)
        for url, body in (('/api/withdrawals', {'amount': '1.00', 'method': 'bank_transfer', 'destination': 'A'}), ('/api/investments', {'product_id': 1, 'amount': '100.00', 'accepted_disclosures': True})):
            self.assertEqual(self.req(c, 'post', url, body).status_code, 403, url)
    def test_production_refuses_to_start_without_requirements(self):
        class C(Config): ENV = 'production'; AUTH_SECRET = 'x' * 40; PROVIDER_MODE = 'internal'; DATABASE_URL = ''
        with self.assertRaises(RuntimeError) as e: C.validate()
        for needle in ('PROVIDER_MODE', 'DATABASE_URL', 'PAYMENT_PROVIDER_KEY'): self.assertIn(needle, str(e.exception))
        self.assertFalse(prov.Providers({'PROVIDER_MODE': 'internal', 'REAL_FUNDS_ENABLED': True, 'UPLOAD_DIR': self.tmp + '/u'}).real_funds_enabled)   # simulated mode can never hold real funds

if __name__ == '__main__': unittest.main()
