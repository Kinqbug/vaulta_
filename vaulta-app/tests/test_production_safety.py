"""Production-safety: the development seed, simulator routes and demo data cannot reach production; bootstrap + readiness tooling."""
import os, sys, tempfile, shutil, sqlite3, unittest, io, contextlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_vaulta as tv
from server import db as dbm, util, seed as seedmod, prodcheck, bootstrap_admin, config
from server.app import create_app

def mkdb(tmp):
    p = os.path.join(tmp, 'p.db'); c = dbm.connect(p); dbm.init_schema(c); return p, c

class TestSeedAndSimulator(unittest.TestCase):
    def test_seed_refuses_outside_development(self):
        old = seedmod.Config.ENV
        try:
            for env in ('production', 'staging'):
                seedmod.Config.ENV = env
                with self.assertRaises(SystemExit) as cm: seedmod.seed(None, None)
                self.assertIn('Refusing', str(cm.exception))
        finally: seedmod.Config.ENV = old

class TestRuntimeSurfaces(tv.Base):
    def test_dev_simulator_routes_exist_only_in_development(self):
        self.assertEqual(self.client().post('/api/dev/crypto/mine', json={}).status_code, 401)            # development: route exists, needs login
        self.assertTrue([r.rule for r in self.app.url_map.iter_rules() if r.rule.startswith('/api/dev/')])
        tmp = tempfile.mkdtemp()
        try:
            app = create_app({'DATABASE_PATH': os.path.join(tmp, 's.db'), 'UPLOAD_DIR': os.path.join(tmp, 'u'), 'ENV': 'staging', 'CRYPTO_PROVIDER_MODE': 'testnet', 'RATE_LIMIT_ENABLED': False})
            self.assertEqual({r.rule for r in app.url_map.iter_rules() if r.rule.startswith('/api/dev/')} - {'/api/dev/last-email', '/api/dev/<path:p>'}, set())   # simulator routes are not registered (last-email 404s itself outside development)
            self.assertEqual(app.test_client().post('/api/dev/crypto/mine', json={}).status_code, 404)
            h = app.test_client().get('/api/health'); self.assertEqual(h.json, {'ok': True})                 # nothing about configuration outside development
        finally: shutil.rmtree(tmp, ignore_errors=True)

    def test_user_cannot_self_settle_deposit_when_live(self):
        c = self.signup(); self.P.mode = 'live'
        r = self.req(c, 'post', '/api/deposits/DEP-X/settle'); self.assertEqual(r.status_code, 404)

class TestProductionRefusesDemoData(unittest.TestCase):
    def setUp(self): self.tmp = tempfile.mkdtemp(); self.path, self.c = mkdb(self.tmp)
    def tearDown(self): self.c.close(); shutil.rmtree(self.tmp, ignore_errors=True)
    def add_user(self, email, demo):
        self.c.execute('INSERT INTO users(public_id,email,password_hash,email_verified,is_demo,created_at) VALUES(?,?,?,?,?,?)', ('USR-' + email[:4].upper(), email, 'x', 1, demo, util.now()))
    def test_production_start_refused_with_demo_accounts(self):
        self.add_user('demo@vaulta.test', 1); self.c.close()
        with self.assertRaises(RuntimeError) as cm:
            create_app({'DATABASE_PATH': self.path, 'UPLOAD_DIR': os.path.join(self.tmp, 'u'), 'ENV': 'production', 'CRYPTO_PROVIDER_MODE': 'live'})
        self.assertIn('demo account', str(cm.exception)); self.c = dbm.connect(self.path)
    def test_report_flags_demo_and_cleanup_is_non_destructive_and_gated(self):
        self.add_user('demo@vaulta.test', 1); self.add_user('real@example.com', 0)
        rep = prodcheck.demo_report(self.c); self.assertEqual(rep['demo_users'], 1); self.assertEqual(rep['seed_pattern_accounts'], 1)
        old = prodcheck.Config.DATABASE_PATH; prodcheck.Config.DATABASE_PATH = self.path
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(prodcheck.main(['--disable-demo-accounts']), 2)                          # refused without backup dir + confirm
                self.assertEqual(self.c.execute("SELECT status FROM users WHERE email='demo@vaulta.test'").fetchone()[0], 'active')
                self.assertEqual(prodcheck.main(['--disable-demo-accounts', '--backup-dir', os.path.join(self.tmp, 'bk'), '--confirm']), 0)
            self.assertEqual(self.c.execute("SELECT status FROM users WHERE email='demo@vaulta.test'").fetchone()[0], 'closed')
            self.assertEqual(self.c.execute("SELECT status FROM users WHERE email='real@example.com'").fetchone()[0], 'active')   # real customer untouched
            self.assertEqual(self.c.execute('SELECT COUNT(*) FROM users').fetchone()[0], 2)                                     # nothing deleted
            bk = os.listdir(os.path.join(self.tmp, 'bk')); self.assertEqual(len(bk), 1); self.assertEqual(sqlite3.connect(os.path.join(self.tmp, 'bk', bk[0])).execute('SELECT COUNT(*) FROM users').fetchone()[0], 2)
        finally: prodcheck.Config.DATABASE_PATH = old

class TestBootstrapAdmin(unittest.TestCase):
    def setUp(self): self.tmp = tempfile.mkdtemp(); self.old = bootstrap_admin.Config.DATABASE_PATH; bootstrap_admin.Config.DATABASE_PATH = os.path.join(self.tmp, 'b.db'); os.environ.pop('BOOTSTRAP_ADMIN_PASSWORD', None)
    def tearDown(self): bootstrap_admin.Config.DATABASE_PATH = self.old; os.environ.pop('BOOTSTRAP_ADMIN_PASSWORD', None); shutil.rmtree(self.tmp, ignore_errors=True)
    def run_(self, email, pw):
        os.environ['BOOTSTRAP_ADMIN_PASSWORD'] = pw
        with contextlib.redirect_stdout(io.StringIO()): return bootstrap_admin.main(['--email', email])
    def test_creates_one_admin_no_demo_data_and_audits(self):
        self.assertEqual(self.run_('owner@company.com', 'short'), 2)
        self.assertEqual(self.run_('owner@company.com', 'Tr1cky-Unique-Pass77'), 0)
        c = dbm.connect(bootstrap_admin.Config.DATABASE_PATH)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM users').fetchone()[0], 1); self.assertEqual(c.execute('SELECT is_demo,email_verified FROM users').fetchone()[:], (0, 0))
        self.assertNotIn('Tr1cky', c.execute('SELECT password_hash FROM users').fetchone()[0]); self.assertEqual(c.execute("SELECT COUNT(*) FROM audit_logs WHERE action='admin.bootstrap'").fetchone()[0], 1)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM assets').fetchone()[0], 0)                                                        # no fictional assets
        self.assertEqual(self.run_('second@company.com', 'Tr1cky-Unique-Pass77'), 1)                                                       # only the first admin
        self.assertEqual(c.execute('SELECT COUNT(*) FROM users').fetchone()[0], 1)

class TestProductionConfig(unittest.TestCase):
    def test_smtp_needs_encryption_key_and_https_url_in_production(self):
        class C: ENV = 'production'; EMAIL_PROVIDER = 'smtp'; EMAIL_FROM_ADDRESS = ''; PUBLIC_BASE_URL = 'http://x.com'; EMAIL_PROVIDER_KEY = ''; EMAIL_WEBHOOK_SECRET = ''; EMAIL_SANDBOX_ALLOWLIST = ''; AWS_ACCESS_KEY_ID = AWS_SECRET_ACCESS_KEY = EMAIL_SES_REGION = ''
        old = os.environ.pop('SETTINGS_ENCRYPTION_KEY', None)
        try:
            p = ' '.join(config.email_env_problems(C)); self.assertIn('SETTINGS_ENCRYPTION_KEY', p); self.assertIn('PUBLIC_BASE_URL', p)
            for bad in ('dev', 'mock'):
                C.EMAIL_PROVIDER = bad; self.assertIn('never deliver', ' '.join(config.email_env_problems(C)))
        finally:
            if old: os.environ['SETTINGS_ENCRYPTION_KEY'] = old

if __name__ == '__main__': unittest.main()
