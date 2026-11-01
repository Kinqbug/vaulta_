"""Browser tests for the admin console and the shared dialog manager (UI.dialog in web/js/ui.js).

Run:  python -m unittest tests.test_admin_ui -v      (needs `pip install playwright` and a Chromium: `playwright install chromium`;
the whole module is skipped, with a clear reason, when no browser can be launched.)

Part 1 exercises the dialog manager in an isolated page. Part 2 drives the real admin SPA against a live Flask server running on a
throwaway database: the KYC approve / reject / request-info workflows that used to open unexpected extra popups, navigation, permission-aware
navigation, failed requests and the mobile layout."""
import os, sys, tempfile, shutil, unittest, threading, re, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, 'web')
try:
    from playwright.sync_api import sync_playwright, Error as PWError
    _pw = sync_playwright().start(); _browser = _pw.chromium.launch()
    SKIP = None
except Exception as e:                                  # no playwright / no chromium in this environment
    _pw = _browser = None; SKIP = 'browser automation unavailable: %s' % str(e).splitlines()[0][:120]

def tearDownModule():
    if _browser: _browser.close()
    if _pw: _pw.stop()

CONSENT = {'name': 'vaulta_consent', 'value': '%7B%22v%22%3A1%2C%22p%22%3A%7B%22preferences%22%3Afalse%2C%22analytics%22%3Afalse%2C%22marketing%22%3Afalse%7D%7D'}

# ---------------------------------------------------------------- Part 1: dialog manager (isolated page)
@unittest.skipIf(SKIP, SKIP)
class TestDialogManager(unittest.TestCase):
    def setUp(self):
        self.ctx = _browser.new_context(viewport={'width': 1280, 'height': 800}); self.pg = self.ctx.new_page(); self.errors = []
        self.pg.on('pageerror', lambda e: self.errors.append(str(e)))
        self.pg.set_content('<!doctype html><html><body><button id="opener">open</button><div id="out"></div></body></html>')
        self.pg.add_style_tag(path=os.path.join(WEB, 'css', 'dash.css')); self.pg.add_script_tag(path=os.path.join(WEB, 'js', 'ui.js'))
    def tearDown(self):
        self.assertEqual(self.errors, []); self.ctx.close()
    def ev(self, js, arg=None): return self.pg.evaluate(js, arg)
    def stmt(self, js): self.pg.evaluate('()=>{' + js + '}')      # statements only: never hand a pending promise back to the test runner
    def count(self):
        self.pg.wait_for_timeout(40)      # native <dialog> 'close' events are queued tasks
        return self.ev("[document.querySelectorAll('dialog').length, document.querySelectorAll('dialog[open]').length]")

    def test_open_close_cleans_up_dom_and_stack(self):
        self.ev("UI.dialog.open({title:'Hello',body:'<p>x</p>'})"); self.assertEqual(self.count(), [1, 1]); self.assertEqual(self.ev('UI.dialog.depth'), 1)
        self.pg.click('dialog [data-x]'); self.assertEqual(self.count(), [0, 0]); self.assertEqual(self.ev('UI.dialog.depth'), 0)
        self.assertFalse(self.ev("document.body.classList.contains('dlg-open')"))

    def test_repeated_open_cycles_leave_nothing_behind(self):
        self.ev("for(let i=0;i<200;i++){const d=UI.dialog.open({title:'t'+i,body:'x'});d.close()}")
        self.assertEqual(self.count(), [0, 0]); self.assertEqual(self.ev('UI.dialog.depth'), 0)

    def test_keyed_dialog_is_singleton_so_repeated_clicks_cannot_duplicate_it(self):
        self.ev("document.getElementById('opener').onclick=()=>UI.dialog.open({title:'One',body:'x',key:'k'})")
        for _ in range(5): self.ev("document.getElementById('opener').click()")
        self.assertEqual(self.count(), [1, 1])

    def test_confirm_ok_cancel_escape_and_backdrop(self):
        self.stmt("window.R=null;UI.confirmBox('T','Msg').then(v=>window.R=v)"); self.pg.click('dialog [data-ok]'); self.pg.wait_for_function('window.R!==null'); self.assertIs(self.ev('window.R'), True)
        self.stmt("window.R='x';UI.confirmBox('T','Msg').then(v=>window.R=v)"); self.pg.click('dialog .btn.ghost'); self.pg.wait_for_function("window.R!=='x'"); self.assertIsNone(self.ev('window.R'))
        self.stmt("window.R='x';UI.confirmBox('T','Msg').then(v=>window.R=v)"); self.pg.keyboard.press('Escape'); self.pg.wait_for_function("window.R!=='x'"); self.assertIsNone(self.ev('window.R'))
        self.stmt("window.R='x';UI.confirmBox('T','Msg').then(v=>window.R=v)"); self.pg.wait_for_timeout(600); self.pg.mouse.click(4, 4); self.pg.wait_for_function("window.R!=='x'"); self.assertIsNone(self.ev('window.R'))
        self.assertEqual(self.count(), [0, 0])

    def test_confirm_with_reason_validates_before_resolving(self):
        self.stmt("window.R='pending';UI.confirmBox('T','Msg',{reason:true}).then(v=>window.R=v)")
        self.pg.click('dialog [data-ok]'); self.assertEqual(self.ev('window.R'), 'pending'); self.assertEqual(self.count(), [1, 1])
        self.assertIn('reason', self.pg.inner_text('dialog .dlg-err').lower())
        self.pg.fill('dialog .cr', 'abc'); self.pg.click('dialog [data-ok]'); self.assertEqual(self.ev('window.R'), 'pending')
        self.pg.fill('dialog .cr', 'A proper reason'); self.pg.click('dialog [data-ok]'); self.pg.wait_for_function("window.R!=='pending'"); self.assertEqual(self.ev('window.R'), 'A proper reason')

    def test_same_confirm_requested_twice_returns_the_same_pending_decision(self):
        self.assertTrue(self.ev("(()=>{const a=UI.confirmBox('T','Msg'),b=UI.confirmBox('T','Msg');return a===b&&document.querySelectorAll('dialog').length===1})()"))

    def test_confirm_run_success_closes_only_after_server_result(self):
        self.stmt("window.calls=0;window.R=null;UI.dialog.confirm('T','Msg',{run:async()=>{window.calls++;await new Promise(r=>setTimeout(r,250));return {id:7}}}).then(v=>window.R=v)")
        self.pg.dblclick('dialog [data-ok]'); self.assertEqual(self.count(), [1, 1])                       # still open while the request is in flight
        self.assertEqual(self.pg.inner_text('dialog [data-ok]'), 'Working…')
        self.pg.keyboard.press('Escape'); self.assertEqual(self.count(), [1, 1])                           # Esc is ignored while busy
        self.pg.wait_for_function('window.R!==null'); self.assertEqual(self.ev('window.calls'), 1)         # double click ran the request ONCE
        self.assertEqual(self.ev('window.R.result.id'), 7); self.assertEqual(self.count(), [0, 0])

    def test_confirm_run_failure_keeps_dialog_open_with_server_message_and_unlocks(self):
        self.stmt("window.R='pending';UI.dialog.confirm('T','Msg',{run:async()=>{throw new Error('Server said no')}}).then(v=>window.R=v)")
        self.pg.click('dialog [data-ok]'); self.pg.wait_for_selector('dialog .dlg-err:not([hidden])')
        self.assertEqual(self.pg.inner_text('dialog .dlg-err'), 'Server said no'); self.assertEqual(self.ev('window.R'), 'pending')
        self.assertFalse(self.ev("document.querySelector('dialog [data-ok]').disabled")); self.assertEqual(self.pg.inner_text('dialog [data-ok]'), 'Confirm')
        self.pg.keyboard.press('Escape'); self.pg.wait_for_function("window.R!=='pending'"); self.assertIsNone(self.ev('window.R'))

    def test_child_dialog_makes_parent_inert_and_restores_it(self):
        self.ev("window.parent_=UI.dialog.open({title:'Parent',body:'<button id=pb>go</button>',key:'p'});window.n=0;parent_.addEventListener('click',e=>{if(e.target.id==='pb')window.n++})")
        self.ev("UI.dialog.open({title:'Child',body:'c',key:'c'})")
        self.assertTrue(self.ev('parent_.inert')); self.ev("parent_.querySelector('#pb').click()"); self.assertEqual(self.ev('window.n'), 0)   # a stray/synthetic click can't reach the parent's handler
        self.pg.keyboard.press('Escape'); self.pg.wait_for_function("document.querySelectorAll('dialog').length===1"); self.assertFalse(self.ev('parent_.inert'))          # Esc closes ONLY the top dialog
        self.ev("parent_.querySelector('#pb').click()"); self.assertEqual(self.ev('window.n'), 1)

    def test_close_all_closes_every_layer(self):
        self.ev("UI.dialog.open({title:'a',body:'x',key:'a'});UI.dialog.open({title:'b',body:'x',key:'b'});UI.dialog.open({title:'c',body:'x',key:'c'})"); self.assertEqual(self.count(), [3, 3])
        self.ev('UI.dialog.closeAll()'); self.assertEqual(self.count(), [0, 0]); self.assertEqual(self.ev('UI.dialog.depth'), 0)

    def test_focus_returns_to_the_opening_control(self):
        self.pg.focus('#opener'); self.ev("UI.dialog.open({title:'F',body:'x'})"); self.pg.keyboard.press('Escape')
        self.assertEqual(self.ev('document.activeElement.id'), 'opener')

    def test_form_dialog_validates_locks_and_reports_errors(self):
        self.stmt("window.calls=0;window.R='pending';UI.dialog.form({title:'F',body:'<label class=f>Name<input name=name required></label>',onSubmit:async d=>{window.calls++;if(d.name==='bad')throw new Error('Name rejected');await new Promise(r=>setTimeout(r,150));return d.name}}).then(v=>window.R=v)")
        self.pg.click('dialog [type=submit]'); self.assertEqual(self.ev('window.calls'), 0); self.assertEqual(self.count(), [1, 1])    # native validation blocks an empty submit
        self.pg.fill('dialog input[name=name]', 'bad'); self.pg.click('dialog [type=submit]'); self.pg.wait_for_selector('dialog .dlg-err:not([hidden])')
        self.assertEqual(self.pg.inner_text('dialog .dlg-err'), 'Name rejected'); self.assertEqual(self.count(), [1, 1])
        self.pg.fill('dialog input[name=name]', 'good'); self.pg.dblclick('dialog [type=submit]'); self.pg.wait_for_function("window.R!=='pending'")
        self.assertEqual(self.ev('window.R'), 'good'); self.assertEqual(self.ev('window.calls'), 2); self.assertEqual(self.count(), [0, 0])      # 1 failed + 1 success: the double click did not submit twice

    def test_toast_deduplicates_identical_messages(self):
        self.ev("for(let i=0;i<4;i++)UI.toast('Saved')"); self.assertEqual(self.ev("document.querySelectorAll('.toast').length"), 1)
        self.ev("UI.toast('Failed','err')"); self.assertEqual(self.ev("document.querySelectorAll('.toast').length"), 2)

    def test_run_helper_ignores_reentrant_clicks(self):
        self.stmt("window.calls=0;window.b=document.getElementById('opener');b.onclick=()=>UI.run(b,async()=>{window.calls++;await new Promise(r=>setTimeout(r,200))})")
        self.pg.dblclick('#opener'); self.pg.wait_for_timeout(400); self.assertEqual(self.ev('window.calls'), 1); self.assertFalse(self.ev('b.disabled'))

# ---------------------------------------------------------------- Part 2: the real admin SPA against a live server
if not SKIP:
    from tests.test_vaulta import Base, PW
    from werkzeug.serving import make_server

    @unittest.skipIf(SKIP, SKIP)
    class TestAdminConsole(Base):
        def setUp(self):
            super().setUp()
            self.srv = make_server('127.0.0.1', 0, self.app, threaded=True); self.port = self.srv.server_port; self.base = 'http://127.0.0.1:%d' % self.port
            threading.Thread(target=self.srv.serve_forever, daemon=True).start()
            self.staff('super_admin', 'root@vaulta.test'); self.kyc_user = self._kyc_user('kay@example.com', 'Kay Tester')
            self.ctx = _browser.new_context(viewport={'width': 1440, 'height': 900}); self.ctx.add_cookies([dict(CONSENT, url=self.base)]); self.pg = self.ctx.new_page()
            self.errors = []; self.posts = []; self.pg.on('pageerror', lambda e: self.errors.append(str(e)))
            self.pg.on('request', lambda r: self.posts.append((r.method, r.url.replace(self.base, ''))) if r.method != 'GET' else None)
        def tearDown(self):
            self.ctx.close(); self.srv.shutdown(); super().tearDown()
        def _kyc_user(self, email, name):
            self.signup(email); uid = self.uid(email)
            self.db.execute("UPDATE kyc_profiles SET status='under_review',legal_name=?,country='US',submitted_at=? WHERE user_id=?", (name, util.now(), uid))
            self.db.execute("UPDATE users SET kyc_status='under_review' WHERE id=?", (uid,)); return uid
        def login(self, email='root@vaulta.test', w=1440, h=900):
            self.pg.set_viewport_size({'width': w, 'height': h}); self.pg.goto(self.base + '/login/'); self.pg.fill('input[type=email]', email); self.pg.fill('input[type=password]', PW)
            self.pg.click('form button'); self.pg.wait_for_url('**/admin/**', timeout=15000) if False else self.pg.wait_for_timeout(1500)
        def go(self, route): self.pg.goto(self.base + '/admin/#/' + route); self.pg.wait_for_selector('#view .pg > *:not(.sk-wrap)', timeout=15000); self.pg.wait_for_timeout(300)
        def dialogs(self):
            self.pg.wait_for_timeout(60)      # native <dialog> 'close' events are queued tasks
            return self.pg.evaluate("[document.querySelectorAll('dialog').length, document.querySelectorAll('dialog[open]').length]")
        def decisions(self): return [p for p in self.posts if '/decision' in p[1]]
        def open_kyc(self):
            self.go('kyc'); self.pg.click('#view tr[data-i]'); self.pg.wait_for_selector('dialog.dlg [data-d]')

        def test_shell_is_shared_and_navigation_is_permission_aware(self):
            self.login(); self.go('overview')
            self.assertTrue(self.pg.is_visible('.adm-side')); self.assertTrue(self.pg.is_visible('.adm-head'))
            for k in ('users', 'kyc', 'settings/general', 'roles'): self.assertEqual(self.pg.locator(f'#adm-side [data-k="{k}"]').count(), 1)     # sub-pages are links inside their (collapsed) group
            self.assertEqual(self.pg.locator('.adm-nv.on').count(), 1)
            self.assertEqual(self.pg.get_attribute('.adm-nv.on', 'data-k'), 'overview')
            self.go('kyc'); self.assertEqual(self.pg.get_attribute('.adm-nv.on', 'data-k'), 'kyc'); self.assertEqual(self.pg.locator('.adm-side').count(), 1)
            self.assertEqual(self.errors, [])
            # a staff member without kyc.review / settings.manage never sees those entries, and the API still refuses them server-side
            self.staff('support_admin', 'help@vaulta.test'); ctx2 = _browser.new_context(); ctx2.add_cookies([dict(CONSENT, url=self.base)]); p2 = ctx2.new_page()
            p2.goto(self.base + '/login/'); p2.fill('input[type=email]', 'help@vaulta.test'); p2.fill('input[type=password]', PW); p2.click('form button'); p2.wait_for_timeout(1500)
            p2.goto(self.base + '/admin/#/overview'); p2.wait_for_selector('.adm-nv[data-k=support]')
            self.assertEqual(p2.locator('#adm-side [data-k=kyc]').count(), 0); self.assertEqual(p2.locator('#adm-side [data-k^="settings"]').count(), 0)
            self.assertEqual(p2.evaluate("fetch('/api/admin/kyc').then(r=>r.status)"), 403); ctx2.close()

        def test_kyc_approve_uses_one_managed_confirmation_and_one_request(self):
            self.login(); self.open_kyc(); self.assertEqual(self.dialogs(), [1, 1])
            self.pg.click('[data-d=approve]'); self.assertEqual(self.dialogs(), [2, 2])                                  # review + exactly ONE confirmation
            self.assertTrue(self.pg.evaluate("document.querySelector('dialog.dlg:not(.dlg-confirm)').inert"))           # the review underneath cannot receive clicks
            self.pg.dblclick('dialog.dlg-confirm [data-ok]'); self.pg.wait_for_timeout(1500)
            self.assertEqual(len(self.decisions()), 1)                                                                   # no duplicate submission
            self.assertEqual(self.dialogs(), [0, 0])                                                                     # both layers closed after the server confirmed
            self.assertEqual(self.db.execute("SELECT status FROM kyc_profiles WHERE user_id=?", (self.kyc_user,)).fetchone()[0], 'verified')
            self.assertEqual(self.db.execute("SELECT COUNT(*) FROM audit_logs WHERE action='kyc.approve'").fetchone()[0], 1)      # exactly one audited decision
            self.assertEqual(self.errors, [])

        def test_kyc_cancel_returns_to_review_and_can_be_repeated(self):
            self.login(); self.open_kyc()
            for _ in range(3):
                self.pg.click('[data-d=approve]'); self.assertEqual(self.dialogs(), [2, 2]); self.pg.click('dialog.dlg-confirm .btn.ghost'); self.assertEqual(self.dialogs(), [1, 1])
                self.assertFalse(self.pg.evaluate("document.querySelector('dialog.dlg').inert"))
            self.assertEqual(self.decisions(), [])

        def test_kyc_reject_and_request_info_require_a_reason(self):
            self.login(); self.open_kyc()
            for dec in ('reject', 'request_info'):
                self.pg.click(f'[data-d={dec}]'); self.pg.click('dialog.dlg-confirm [data-ok]'); self.assertEqual(self.dialogs(), [2, 2]); self.assertEqual(self.decisions(), [])
                self.assertTrue(self.pg.is_visible('dialog.dlg-confirm .dlg-err')); self.pg.keyboard.press('Escape'); self.assertEqual(self.dialogs(), [1, 1])
            self.pg.click('[data-d=reject]'); self.pg.fill('dialog.dlg-confirm .cr', 'Document is unreadable'); self.pg.click('dialog.dlg-confirm [data-ok]'); self.pg.wait_for_timeout(1500)
            self.assertEqual(len(self.decisions()), 1); self.assertEqual(self.dialogs(), [0, 0])
            self.assertEqual(self.db.execute("SELECT status FROM kyc_profiles WHERE user_id=?", (self.kyc_user,)).fetchone()[0], 'rejected')

        def test_failed_decision_keeps_context_and_shows_the_server_error(self):
            self.login(); self.open_kyc()
            self.pg.route('**/api/admin/kyc/*/decision', lambda r: r.fulfill(status=422, content_type='application/json', body='{"error":{"code":"invalid","message":"Reviewer is not allowed to decide this case."}}'))
            self.pg.click('[data-d=approve]'); self.pg.click('dialog.dlg-confirm [data-ok]'); self.pg.wait_for_selector('dialog.dlg-confirm .dlg-err:not([hidden])')
            self.assertIn('not allowed', self.pg.inner_text('dialog.dlg-confirm .dlg-err')); self.assertEqual(self.dialogs(), [2, 2])
            self.assertEqual(self.pg.locator('.toast.ok').count(), 0)                                                    # no success message for a failed request
            self.assertFalse(self.pg.evaluate("document.querySelector('dialog.dlg-confirm [data-ok]').disabled"))     # not stuck loading
            self.assertEqual(self.db.execute("SELECT status FROM kyc_profiles WHERE user_id=?", (self.kyc_user,)).fetchone()[0], 'under_review')

        def test_navigation_closes_dialogs_and_stale_responses_cannot_open_them(self):
            self.login(); self.open_kyc(); self.pg.evaluate("location.hash='#/orders'"); self.pg.wait_for_timeout(600); self.assertEqual(self.dialogs(), [0, 0])
            self.go('kyc'); self.pg.route('**/api/admin/kyc/USR-*', lambda r: (time.sleep(0.8), r.continue_()))
            self.pg.click('#view tr[data-i]'); self.pg.evaluate("location.hash='#/users'"); self.pg.wait_for_timeout(1800)   # the slow review response arrives AFTER navigating away
            self.assertEqual(self.dialogs(), [0, 0]); self.assertEqual(self.errors, [])

        def test_rerendering_a_list_never_multiplies_row_handlers(self):
            self.login(); self.go('users'); fetches = []
            self.pg.on('request', lambda r: fetches.append(r.url) if re.search(r'/api/admin/users/[^/?]+$', r.url) and r.method == 'GET' else None)
            for _ in range(4):
                self.pg.select_option('[data-f=status]', 'active'); self.pg.wait_for_timeout(350); self.pg.select_option('[data-f=status]', ''); self.pg.wait_for_timeout(350)
            for k in ('deposits', 'withdrawals', 'orders', 'users'): self.go(k)
            fetches.clear(); self.pg.click('#view tr[data-i]'); self.pg.wait_for_timeout(1000)
            self.assertEqual(len(fetches), 1); self.assertEqual(self.dialogs(), [1, 1])

        def test_status_change_confirmation_runs_inside_the_dialog_once(self):
            self.login(); self.go('users'); self.pg.fill('#fq', 'kay@example.com'); self.pg.press('#fq', 'Enter'); self.pg.wait_for_timeout(800)
            self.pg.click('#view tr[data-i]'); self.pg.wait_for_selector('dialog [data-st=suspended]'); self.pg.dblclick('dialog [data-st=suspended]'); self.assertEqual(self.dialogs(), [2, 2])
            self.pg.fill('dialog.dlg-confirm .cr', 'Suspicious activity review'); self.pg.dblclick('dialog.dlg-confirm [data-ok]'); self.pg.wait_for_timeout(1500)
            self.assertEqual(len([p for p in self.posts if p[1].endswith('/status')]), 1)
            self.assertEqual(self.db.execute('SELECT status FROM users WHERE id=?', (self.kyc_user,)).fetchone()[0], 'suspended')

        def test_customers_page_shows_photos_kpis_and_a_single_row_menu(self):
            import io, struct, zlib
            raw = b''.join(b'\x00' + bytes((30, 120, 200)) * 200 for _ in range(200)); c = lambda t, d: struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
            img = b'\x89PNG\r\n\x1a\n' + c(b'IHDR', struct.pack('>IIBBBBB', 200, 200, 8, 2, 0, 0, 0)) + c(b'IDAT', zlib.compress(raw)) + c(b'IEND', b'')
            cl = self.signup('pic@example.com'); r = cl.post('/api/profile/avatar', data={'file': (io.BytesIO(img), 'a.png')}, headers={'X-CSRF-Token': cl.csrf}, content_type='multipart/form-data'); self.assertEqual(r.status_code, 201)
            self.login(); self.go('users'); self.pg.wait_for_selector('.kcard')
            self.assertEqual(self.pg.locator('.kcard').count(), 5)
            self.pg.fill('#fq', 'pic@example.com'); self.pg.press('#fq', 'Enter'); self.pg.wait_for_selector('.cav img'); self.pg.wait_for_timeout(500)
            self.assertTrue(self.pg.evaluate("document.querySelector('.cav img').naturalWidth>0"))                      # the customer's real uploaded photo is displayed
            self.pg.click('[data-menu]'); self.assertEqual(self.pg.locator('.cmenu').count(), 1)
            self.pg.click('[data-menu]'); self.assertEqual(self.pg.locator('.cmenu').count(), 0)                         # toggles closed
            self.pg.click('[data-menu]'); self.pg.click('.cmenu [data-it=restricted]'); self.assertEqual(self.dialogs(), [1, 1]); self.assertEqual(self.pg.locator('.cmenu').count(), 0)
            self.pg.fill('dialog.dlg-confirm .cr', 'Pending document check'); self.pg.dblclick('dialog.dlg-confirm [data-ok]'); self.pg.wait_for_timeout(1500)
            self.assertEqual(len([p for p in self.posts if p[1].endswith('/status')]), 1)
            self.assertEqual(self.db.execute("SELECT status FROM users WHERE email='pic@example.com'").fetchone()[0], 'restricted')
            self.pg.wait_for_selector('.badge, .st-restricted'); self.assertEqual(self.dialogs(), [0, 0])
            self.pg.click('[data-menu]'); self.pg.keyboard.press('Escape'); self.assertEqual(self.pg.locator('.cmenu').count(), 0)
            self.go('overview'); self.go('users'); self.assertEqual(self.errors, [])

        def test_overview_uses_live_numbers_not_placeholders(self):
            self.login(); self.go('overview')
            body = self.pg.inner_text('#view')
            n = self.db.execute('SELECT COUNT(*) FROM users WHERE id NOT IN(SELECT user_id FROM user_roles)').fetchone()[0]
            self.assertIn('Total customers', body); self.assertRegex(body, r'Total customers\s*\n?\s*%d\b' % n)
            self.assertEqual(self.errors, [])

        def test_mobile_layout_has_no_horizontal_overflow_and_dialogs_fit(self):
            self.login(w=375, h=800)
            for k in ('overview', 'users', 'kyc', 'settings'):
                self.go(k); self.assertFalse(self.pg.evaluate('document.documentElement.scrollWidth>document.documentElement.clientWidth'), k)
            onscreen = lambda: self.pg.evaluate("document.querySelector('.adm-side').getBoundingClientRect().right>0")
            self.assertFalse(onscreen()); self.pg.click('#adm-burger'); self.pg.wait_for_timeout(400); self.assertTrue(onscreen())
            self.pg.click('.adm-nv[data-k=kyc]'); self.pg.wait_for_timeout(700); self.assertFalse(onscreen())
            self.pg.wait_for_selector('#view tr[data-i]'); self.pg.click('#view tr[data-i]'); self.pg.wait_for_selector('dialog.dlg [data-d]')
            box = self.pg.evaluate("(()=>{const r=document.querySelector('dialog.dlg').getBoundingClientRect();return [r.left,r.right,innerWidth]})()")
            self.assertGreaterEqual(box[0], 0); self.assertLessEqual(box[1], box[2] + 1)

# ---------------------------------------------------------------- Part 3: redesigned sidebar (14 entries, dropdowns, routing, page preservation)
PRIMARY = ['Dashboard', 'Customers', 'Verification & Compliance', 'Crypto Operations', 'Finance & Accounting', 'Assets & Markets', 'Investments', 'Trading',
           'Customer Support', 'Reports', 'Communications', 'Content & Legal', 'Audit & Security', 'System Settings']
# every page of the original sidebar (snapshot of the pre-redesign registry, taken in a real browser as super admin) + the hidden crypto settings page
ORIGINAL = ['overview', 'users', 'kyc', 'support', 'deposits', 'withdrawals', 'transactions', 'accounts', 'investments', 'savings', 'retirement', 'assets', 'orders',
            'notifications', 'email', 'community', 'content', 'legal', 'reports', 'audit', 'roles', 'settings', 'settings-crypto']
GROUPS = {'Customers': ['users', 'accounts'], 'Crypto Operations': ['deposits', 'withdrawals', 'settings-crypto'], 'Investments': ['investments', 'savings', 'retirement'],
          'Communications': ['notifications', 'email', 'community'], 'Content & Legal': ['content', 'legal'], 'Audit & Security': ['audit', 'roles'],
          'System Settings': ['settings/general', 'settings/security', 'settings/api', 'settings/notifications', 'settings/payments', 'settings/integrations']}

if not SKIP:
    class TestAdminNav(TestAdminConsole):
        """Sidebar redesign. Subclasses the console fixture for its server/login helpers; the inherited console tests are switched off here (they run once, in TestAdminConsole)."""
        def prim(self): return self.pg.evaluate("[...document.querySelectorAll('.adm-tree > .adm-g .adm-nv')].map(a=>a.textContent.trim())")
        def expanded(self, g): return self.pg.get_attribute(f'.adm-g[data-g={g}] .adm-chev', 'aria-expanded')
        def crumb(self): return self.pg.inner_text('#adm-crumb').replace('\n', ' ')
        def noscroll(self): return self.pg.evaluate('document.documentElement.scrollWidth<=document.documentElement.clientWidth')

        def test_fourteen_primary_entries_in_order_with_chevrons_only_where_there_are_sub_pages(self):
            self.login(); self.go('overview'); self.assertEqual(self.prim(), PRIMARY)
            multi = {'customers', 'crypto', 'invest', 'comms', 'content', 'security', 'settings'}
            self.assertEqual(set(self.pg.evaluate("[...document.querySelectorAll('.adm-chev')].map(b=>b.closest('.adm-g').dataset.g)")), multi)
            for k in ('compliance', 'finance', 'markets', 'trading', 'support', 'reports'): self.assertEqual(self.pg.locator(f'.adm-g[data-g={k}] .adm-chev').count(), 0)

        def test_chevron_expands_without_navigating_and_label_navigates_and_expands(self):
            self.login(); self.go('overview'); h = self.pg.evaluate('location.hash')
            self.assertEqual(self.expanded('crypto'), 'false'); self.assertFalse(self.pg.is_visible('#adm-sub-crypto'))
            self.pg.click('.adm-g[data-g=crypto] .adm-chev'); self.assertEqual(self.expanded('crypto'), 'true'); self.assertTrue(self.pg.is_visible('#adm-sub-crypto'))
            self.assertEqual(self.pg.evaluate('location.hash'), h)                       # expanding never navigates
            self.pg.click('.adm-g[data-g=crypto] .adm-chev'); self.assertEqual(self.expanded('crypto'), 'false')
            self.pg.click('.adm-g[data-g=settings] .adm-nv'); self.pg.wait_for_timeout(500)
            self.assertEqual(self.pg.evaluate('location.hash'), '#/settings/general'); self.assertEqual(self.expanded('settings'), 'true')

        def test_active_parent_and_child_breadcrumb_refresh_and_history(self):
            self.login(); self.go('roles')
            self.assertEqual(self.expanded('security'), 'true'); self.assertEqual(self.pg.get_attribute('#adm-side .on', 'data-k'), 'roles')
            self.assertEqual(self.pg.locator('#adm-side .on').count(), 1); self.assertIn('has-on', self.pg.get_attribute('.adm-g[data-g=security]', 'class'))
            self.assertEqual(self.crumb(), 'Dashboard › Audit & Security › Roles & permissions')
            self.pg.reload(); self.pg.wait_for_selector('#view .pg > *:not(.sk-wrap)'); self.assertEqual(self.pg.get_attribute('#adm-side .on', 'data-k'), 'roles'); self.assertEqual(self.expanded('security'), 'true')
            self.go('audit'); self.assertEqual(self.expanded('security'), 'true'); self.assertEqual(self.pg.get_attribute('#adm-side .on', 'data-k'), 'audit')   # stays open between its children
            self.pg.go_back(); self.pg.wait_for_timeout(500); self.assertEqual(self.pg.get_attribute('#adm-side .on', 'data-k'), 'roles')
            self.pg.go_forward(); self.pg.wait_for_timeout(500); self.assertEqual(self.pg.get_attribute('#adm-side .on', 'data-k'), 'audit')
            self.go('overview'); self.assertEqual(self.crumb(), 'Dashboard'); self.go('transactions'); self.assertEqual(self.crumb(), 'Dashboard › Finance & Accounting › Transactions')
            self.go('support'); self.assertEqual(self.crumb(), 'Dashboard › Customer Support'); self.assertEqual(self.errors, [])

        def test_settings_subpages_open_directly_and_legacy_route_is_an_alias(self):
            self.login()
            for sub in GROUPS['System Settings']:
                self.go(sub); self.assertEqual(self.pg.get_attribute('#adm-side .on', 'data-k'), sub); self.assertEqual(self.expanded('settings'), 'true')
                self.assertEqual(self.pg.inner_text('#adm-crumb').replace('\n', ' ').startswith('Dashboard › System Settings ›'), True)
            self.go('settings'); self.assertEqual(self.pg.evaluate('location.hash'), '#/settings/general'); self.assertEqual(self.pg.get_attribute('#adm-side .on', 'data-k'), 'settings/general')
            self.assertEqual(self.errors, [])

        def test_every_original_page_is_still_reachable_exactly_once(self):
            self.login(); self.go('overview')
            keys = self.pg.evaluate("[...document.querySelectorAll('#adm-side [data-k]')].map(a=>a.dataset.k)")
            self.assertEqual(len(keys), len(set(keys)), 'a page appears twice in the sidebar')
            have = set(keys) | {'settings'} if 'settings/general' in keys else set(keys)
            self.assertEqual(sorted(set(ORIGINAL) - have), [])
            self.assertEqual(self.pg.locator('#adm-side [data-g=more]').count(), 0)          # nothing fell into the "More" safety bucket
            for g, kids in GROUPS.items():
                gid = self.pg.evaluate("(l)=>[...document.querySelectorAll('.adm-tree > .adm-g')].find(li=>li.querySelector('.adm-nv').textContent.trim()===l).dataset.g", g)
                self.assertEqual(self.pg.evaluate("(id)=>[...document.querySelectorAll(`.adm-g[data-g=${id}] .adm-sv`)].map(a=>a.dataset.k)", gid), kids, g)
            for k in ORIGINAL:                                                              # and each one actually renders
                self.go(k); self.assertGreater(self.pg.evaluate("document.querySelector('#view .pg').children.length"), 0, k)
                self.assertEqual(self.pg.evaluate("document.querySelectorAll('#adm-side .on').length"), 1, k)
            self.assertEqual(self.errors, [])

        def test_permissions_limit_the_sidebar_and_routes(self):
            self.staff('support_admin', 'help@vaulta.test'); ctx2 = _browser.new_context(viewport={'width': 1440, 'height': 900}); ctx2.add_cookies([dict(CONSENT, url=self.base)]); p2 = ctx2.new_page()
            p2.goto(self.base + '/login/'); p2.fill('input[type=email]', 'help@vaulta.test'); p2.fill('input[type=password]', PW); p2.click('form button'); p2.wait_for_timeout(1500)
            p2.goto(self.base + '/admin/#/overview'); p2.wait_for_selector('#adm-side [data-k=support]')
            shown = p2.evaluate("[...document.querySelectorAll('.adm-tree > .adm-g .adm-nv')].map(a=>a.textContent.trim())")
            self.assertIn('Customer Support', shown); self.assertNotIn('System Settings', shown); self.assertNotIn('Crypto Operations', shown); self.assertNotIn('Audit & Security', shown)
            for blocked in ('settings/security', 'roles', 'kyc', 'settings-crypto'):
                p2.goto(self.base + '/admin/#/' + blocked); p2.wait_for_timeout(700); self.assertEqual(p2.get_attribute('#adm-side .on', 'data-k'), 'overview', blocked)   # falls back, never renders it
            ctx2.close()

        def test_keyboard_navigation_and_aria(self):
            self.login(); self.go('overview')
            self.assertEqual(self.pg.get_attribute('.adm-g[data-g=settings] .adm-chev', 'aria-controls'), 'adm-sub-settings')
            self.pg.focus('.adm-g[data-g=settings] .adm-nv'); self.pg.keyboard.press('ArrowRight'); self.assertEqual(self.expanded('settings'), 'true')
            self.pg.keyboard.press('ArrowLeft'); self.assertEqual(self.expanded('settings'), 'false')
            self.pg.focus('.adm-g[data-g=customers] .adm-nv'); self.pg.keyboard.press('ArrowDown')
            self.assertEqual(self.pg.evaluate('document.activeElement.textContent.trim()'), 'Verification & Compliance')    # customers is collapsed: skips hidden children
            self.pg.focus('.adm-g[data-g=customers] .adm-chev'); self.pg.keyboard.press('Enter'); self.assertEqual(self.expanded('customers'), 'true')
            self.pg.focus('.adm-g[data-g=customers] .adm-nv'); self.pg.keyboard.press('ArrowDown'); self.assertEqual(self.pg.evaluate('document.activeElement.dataset.k'), 'users')
            self.pg.keyboard.press('Enter'); self.pg.wait_for_timeout(500); self.assertEqual(self.pg.get_attribute('#adm-side .on', 'data-k'), 'users')
            self.assertEqual(self.pg.get_attribute('#adm-side .on', 'aria-current'), 'page')

        def test_short_window_menu_scrolls_and_footer_is_reachable(self):
            self.login(w=1280, h=520); self.go('overview')
            for g in ('customers', 'crypto', 'invest', 'comms', 'content', 'security', 'settings'): self.pg.click(f'.adm-g[data-g={g}] .adm-chev')
            self.assertTrue(self.pg.evaluate("(()=>{const s=document.querySelector('.adm-side');return s.scrollHeight>s.clientHeight&&getComputedStyle(s).overflowY==='auto'})()"))
            self.pg.evaluate("document.querySelector('.adm-side').scrollTop=99999"); self.assertTrue(self.pg.is_visible('#logout'))
            self.assertTrue(self.pg.evaluate("(()=>{const r=document.querySelector('#logout').getBoundingClientRect();return r.bottom<=innerHeight+1&&r.top>=0})()"))

        def test_mobile_drawer_keeps_every_entry_expands_in_place_and_closes_on_navigation(self):
            self.login(w=375, h=800); self.go('overview')
            self.pg.click('#adm-burger'); self.pg.wait_for_timeout(400); self.assertEqual(self.prim(), PRIMARY); self.assertEqual(self.pg.get_attribute('#adm-burger', 'aria-expanded'), 'true')
            self.pg.click('.adm-g[data-g=settings] .adm-chev'); self.pg.wait_for_timeout(200)
            self.assertTrue(self.pg.evaluate("document.body.classList.contains('nav-open')"))            # expanding does not close the drawer
            self.assertEqual(self.pg.locator('#adm-sub-settings .adm-sv').count(), 6)
            self.pg.click('#adm-sub-settings [data-k="settings/payments"]'); self.pg.wait_for_timeout(700)
            self.assertFalse(self.pg.evaluate("document.body.classList.contains('nav-open')")); self.assertEqual(self.pg.evaluate('location.hash'), '#/settings/payments')
            self.pg.click('#adm-burger'); self.pg.wait_for_timeout(300); self.pg.keyboard.press('Escape'); self.pg.wait_for_timeout(300)
            self.assertFalse(self.pg.evaluate("document.body.classList.contains('nav-open')")); self.assertEqual(self.pg.evaluate('document.activeElement.id'), 'adm-burger')

        def test_no_horizontal_overflow_across_widths_with_menus_open(self):
            self.login()
            for w in (320, 360, 375, 390, 430, 768, 1024, 1280, 1440):
                self.pg.set_viewport_size({'width': w, 'height': 800}); self.go('settings/security'); self.assertTrue(self.noscroll(), f'page overflow at {w}px')
                if w <= 1024:
                    self.pg.click('#adm-burger'); self.pg.wait_for_timeout(300)
                    self.assertTrue(self.pg.evaluate("(()=>{const s=document.querySelector('.adm-side');return s.scrollWidth<=s.clientWidth+1})()"), f'drawer overflow at {w}px')
                    self.pg.keyboard.press('Escape'); self.pg.wait_for_timeout(200)
            self.assertEqual(self.errors, [])


    for _n in [n for n in dir(TestAdminConsole) if n.startswith('test_') and n not in TestAdminNav.__dict__]: setattr(TestAdminNav, _n, None)


# ---------------------------------------------------------------- Part 4: System Settings (six tabs, General card layout, real behaviour)
if not SKIP:
    class TestSystemSettings(TestAdminConsole):
        """Drives the real System Settings UI against a live server. Inherited console tests are switched off (they run once, in TestAdminConsole)."""
        TABS = [('general', 'General Settings'), ('security', 'Security Settings'), ('api', 'API Management'), ('notifications', 'Notification Settings'), ('payments', 'Payment Settings'), ('integrations', 'Integration Settings')]
        CARDS = ['Platform Configuration', 'Display Settings', 'Financial Settings', 'Email & Notifications', 'Integration Settings', 'System Information']
        API = '/api/admin/platform/settings'
        def j(self, path): return self.pg.evaluate("p=>fetch(p,{credentials:'same-origin'}).then(async r=>({s:r.status,j:await r.json().catch(()=>null)}))", path)
        def cfg(self): return self.j(self.API)['j']
        def status(self): return self.j('/api/platform/status')['j']
        def texts(self, sel): return self.pg.eval_on_selector_all(sel, 'e=>e.map(x=>x.textContent.trim())')
        def sw(self, n): return self.pg.get_attribute(f'#sw-{n}', 'aria-checked')
        def puts(self): return [p for p in self.posts if p[0] == 'PUT' and 'platform/settings' in p[1]]
        def confirm(self, reason='Scheduled change for testing'):
            self.pg.fill('dialog[open] .cr', reason); self.pg.click('dialog[open] [data-ok]'); self.pg.wait_for_selector('.toast.ok'); self.pg.wait_for_timeout(150)
        def anon_register(self, email):
            c = _browser.new_context(); c.add_cookies([dict(CONSENT, url=self.base)])
            try:
                r = c.request.post(self.base + '/api/auth/register', data={'email': email, 'password': PW, 'first_name': 'Q', 'last_name': 'A', 'accepted_terms': True, 'terms_version': 1, 'privacy_version': 1})
                return r.status, (r.json().get('error') or {}).get('code')
            finally: c.close()
        def boxes(self): return self.pg.eval_on_selector_all('.st-card', 'e=>e.map(x=>{const r=x.getBoundingClientRect();return{x:r.x,y:r.y+scrollY,w:r.width,h:r.height}})')

        def test_six_tabs_in_order_each_with_own_content_keyboard_and_refresh(self):
            self.login(); self.go('settings/general')
            self.assertEqual(self.texts('.st-tabs [role=tab]'), [t for _, t in self.TABS]); heads = {}
            for k, _ in self.TABS:
                self.go('settings/' + k); self.assertEqual(self.pg.get_attribute('.st-tabs [aria-selected=true]', 'id'), 'tab-' + k)
                self.assertEqual(self.pg.locator('.st-tabs [aria-selected=true]').count(), 1); heads[k] = tuple(self.texts('#st-panel h3')); self.assertTrue(heads[k], k)
            self.assertEqual(len(set(heads.values())), 6, 'two tabs show the same cards')
            self.go('settings/general'); self.pg.focus('#tab-general'); self.pg.keyboard.press('ArrowRight'); self.pg.wait_for_selector('#tab-security:focus')
            self.assertEqual(self.pg.evaluate('location.hash'), '#/settings/security')
            self.pg.keyboard.press('End'); self.pg.wait_for_selector('#tab-integrations:focus'); self.pg.keyboard.press('ArrowRight'); self.pg.wait_for_selector('#tab-general:focus')
            self.pg.keyboard.press('ArrowLeft'); self.pg.wait_for_selector('#tab-integrations:focus')
            self.pg.reload(); self.pg.wait_for_selector('#st-panel > *'); self.assertEqual(self.pg.get_attribute('.st-tabs [aria-selected=true]', 'id'), 'tab-integrations')
            self.assertEqual(self.errors, [])

        def test_general_tab_has_the_six_cards_fields_and_switches_in_the_reference_layout(self):
            self.login(); self.go('settings/general'); self.assertEqual(self.texts('.st-card h3'), self.CARDS)
            self.assertEqual(self.pg.inner_text('.a-ph h1'), 'System Settings'); self.assertEqual(self.pg.inner_text('.a-ph p'), 'Manage your platform settings, security preferences, and integrations.')
            self.assertEqual(self.texts('[data-c=platform] .st-row > label'), ['Platform Name', 'Site URL', 'Support Email', 'Default Language', 'Time Zone'])
            self.assertEqual(self.texts('[data-c=display] .st-swt > span'), ['Maintenance Mode', 'Show Live Prices', 'Enable Registration', 'Enable Two-Factor Auth'])
            self.assertEqual(self.texts('[data-c=financial] .st-row > label'), ['Minimum Deposit', 'Minimum Withdrawal', 'Trading Fee (%)', 'Withdrawal Processing Time'])
            self.assertEqual(self.texts('[data-c=email] .st-row > label, [data-c=email] .st-sw .st-swt > span'), ['Admin Email', 'Notification Email', 'Enable Email Alerts', 'Send Daily Reports'])
            self.assertIn('Manage Templates', self.pg.inner_text('[data-c=email]'))
            self.assertEqual(self.texts('[data-c=integration] .st-tl > span:first-child') + self.texts('[data-c=integration] .st-row > label'), ['Payment Gateway', 'Analytics Provider', 'Crypto Payments', 'Google Analytics', 'Chat Service', 'LiveChat', 'API Rate Limit'])
            self.assertEqual(self.texts('[data-c=sysinfo] .st-kv > span'), ['Platform Version', 'Last Updated', 'Server Uptime', 'Environment'])
            for n in ('language', 'timezone'): self.assertGreater(self.pg.locator(f'#st-{n} option').count(), 0)
            b = self.boxes()                                                                                         # two columns, three rows, equal height per row
            for a, c in ((0, 1), (2, 3), (4, 5)): self.assertLess(abs(b[a]['y'] - b[c]['y']), 2); self.assertLess(b[a]['x'], b[c]['x']); self.assertLess(abs(b[a]['h'] - b[c]['h']), 2)
            self.assertGreater(b[2]['y'], b[0]['y'] + b[0]['h'] - 1); self.assertGreater(b[4]['y'], b[2]['y'] + b[2]['h'] - 1)
            self.pg.set_viewport_size({'width': 375, 'height': 800}); self.pg.wait_for_timeout(300); m = self.boxes()                                  # one column, same order
            self.assertEqual(len({round(x['x']) for x in m}), 1); self.assertTrue(all(m[i]['y'] < m[i + 1]['y'] for i in range(5))); self.assertTrue(all(x['x'] + x['w'] <= 376 for x in m))
            self.assertTrue(self.pg.evaluate('document.documentElement.scrollWidth<=document.documentElement.clientWidth'))

        def test_platform_save_validates_confirms_persists_and_keeps_the_form_on_failure(self):
            self.login(); self.go('settings/general'); form = '[data-card=platform]'
            self.pg.click(form + ' button[type=submit]'); self.assertIn('No changes', self.pg.inner_text(form + ' .st-note')); self.assertEqual(self.dialogs()[1], 0)
            self.pg.fill('#st-name', 'Vaulta QA'); self.pg.fill('#st-support_email', 'not-an-email'); self.pg.click(form + ' button[type=submit]')
            self.assertEqual(self.dialogs()[1], 0); self.assertTrue(self.pg.is_visible('#e-support_email')); self.assertEqual(self.pg.get_attribute('#st-support_email', 'aria-invalid'), 'true')
            self.assertEqual(self.pg.input_value('#st-name'), 'Vaulta QA'); self.assertEqual(self.puts(), [])                                         # nothing sent, nothing cleared
            self.pg.fill('#st-support_email', 'Help@Example.com'); self.pg.click(form + ' button[type=submit]'); self.assertEqual(self.dialogs()[1], 1)
            self.pg.fill('dialog[open] .cr', 'no'); self.pg.click('dialog[open] [data-ok]'); self.assertEqual(self.dialogs()[1], 1); self.assertIn('reason', self.pg.inner_text('dialog[open] .dlg-err'))
            self.confirm(); self.assertEqual(len(self.puts()), 1)
            self.assertEqual(self.pg.input_value('#st-support_email'), 'help@example.com')                                                           # the server's normalised value, not what was typed
            g = self.cfg()['general']; self.assertEqual((g['name']['value'], g['support_email']['value']), ('Vaulta QA', 'help@example.com'))
            self.pg.reload(); self.pg.wait_for_selector('.st-grid'); self.assertEqual(self.pg.input_value('#st-name'), 'Vaulta QA')               # survives a refresh
            self.pg.evaluate("(()=>{const s=document.getElementById('st-language');s.add(new Option('Francais','fr'));s.value='fr'})()")       # tampered value passes the browser check, the server refuses it
            self.pg.click(form + ' button[type=submit]'); self.pg.fill('dialog[open] .cr', 'Testing the server rule'); self.pg.click('dialog[open] [data-ok]')
            self.pg.wait_for_selector('#e-language:not([hidden])'); self.assertEqual(self.dialogs()[1], 0); self.assertEqual(self.pg.input_value('#st-language'), 'fr')
            self.assertEqual(self.cfg()['general']['language']['value'], 'en'); self.assertEqual(len(self.puts()), 2)               # the rejected attempt was sent once and the stored value is untouched

        def test_switches_confirm_then_change_real_server_behaviour_and_stay_put_on_failure(self):
            self.login(); self.go('settings/general'); names = ('maintenance_mode', 'live_prices_enabled', 'registration_enabled', 'require_2fa_all')
            self.assertEqual([self.sw(n) for n in names], ['false', 'true', 'true', 'false'])
            for n in names: self.assertEqual(self.pg.get_attribute(f'#sw-{n}', 'role'), 'switch'); self.assertTrue(self.pg.get_attribute(f'#sw-{n}', 'aria-labelledby'))
            n0 = len(self.posts); self.pg.click('#sw-registration_enabled'); self.assertEqual(self.dialogs()[1], 1); self.pg.click('dialog[open] [data-x]')   # cancel: nothing changes, nothing sent
            self.assertEqual((self.dialogs()[1], self.sw('registration_enabled'), len(self.posts)), (0, 'true', n0))
            self.pg.evaluate("(()=>{const b=document.getElementById('sw-registration_enabled');b.click();b.click()})()"); self.assertEqual(self.dialogs()[1], 1)    # a double click opens one dialog
            self.pg.click('dialog[open] [data-x]'); self.assertEqual(self.anon_register('before@example.com')[0], 201)
            self.pg.click('#sw-registration_enabled'); self.confirm('Closing sign-ups during an audit')
            self.assertEqual((self.sw('registration_enabled'), self.status()['registration_enabled']), ('false', False)); self.assertEqual(len(self.puts()), 1)
            self.assertEqual(self.anon_register('after@example.com'), (403, 'registration_closed'))                                                       # the server refuses, not just the UI
            self.assertIsNone(self.db.execute("SELECT 1 FROM users WHERE email='after@example.com'").fetchone())
            self.pg.click('#sw-registration_enabled'); self.confirm('Re-opening sign-ups'); self.assertEqual(self.anon_register('again@example.com')[0], 201)
            self.pg.click('#sw-maintenance_mode'); self.assertIn('Staff accounts keep working', self.pg.inner_text('dialog[open]')); self.confirm('Planned maintenance window')
            self.assertTrue(self.status()['maintenance']); self.assertEqual(self.sw('maintenance_mode'), 'true')
            c = _browser.new_context(); anon = c.request.get(self.base + '/api/products').status; c.close(); self.assertEqual(anon, 503)                       # customers are blocked...
            self.assertEqual(self.j(self.API)['s'], 200)                                                                                                    # ...staff are not
            self.pg.click('#sw-maintenance_mode'); self.confirm('Maintenance finished'); self.assertFalse(self.status()['maintenance'])
            self.pg.click('#sw-live_prices_enabled'); self.confirm('Provider outage'); self.assertEqual((self.sw('live_prices_enabled'), self.status()['live_prices_enabled']), ('false', False))
            self.pg.click('#sw-require_2fa_all'); self.pg.fill('dialog[open] .cr', 'Hardening sign-in'); self.pg.click('dialog[open] [data-ok]')               # this admin has no 2FA: the server refuses
            self.pg.wait_for_selector('dialog[open] .dlg-err:not([hidden])'); self.assertIn('two-factor', self.pg.inner_text('dialog[open] .dlg-err').lower())
            self.assertEqual((self.dialogs()[1], self.sw('require_2fa_all'), self.status()['two_factor_required']), (1, 'false', False)); self.pg.click('dialog[open] [data-x]')
            self.assertEqual(self.sw('require_2fa_all'), 'false')
            logged = {r[0] for r in self.db.execute("SELECT entity_id FROM audit_logs WHERE action='setting.changed'").fetchall()}
            self.assertTrue({'registration_enabled', 'maintenance_mode', 'live_prices_enabled'} <= logged)

        def test_financial_card_validates_saves_and_marks_unbacked_controls_unavailable(self):
            self.login(); self.go('settings/general'); form = '[data-card=financial]'
            for i in ('off-min_withdrawal', 'off-withdrawal_processing_time'): self.assertTrue(self.pg.is_disabled('#' + i))
            self.assertGreaterEqual(self.pg.inner_text('[data-c=financial]').count('Not available'), 2)
            self.pg.fill('#st-min_deposit', '10.005'); self.pg.click(form + ' button[type=submit]'); self.assertTrue(self.pg.is_visible('#e-min_deposit')); self.assertEqual(self.dialogs()[1], 0)
            self.pg.fill('#st-min_deposit', '5.00'); self.pg.fill('#st-trade_fee_percent', '0.25'); self.pg.click(form + ' button[type=submit]'); self.confirm()
            f = self.cfg()['financial']; self.assertEqual((f['min_deposit']['value'], f['trade_fee_percent']['value']), ('5.00', '0.25'))
            self.pg.fill('#st-min_deposit', '999999.00'); self.pg.click(form + ' button[type=submit]'); self.pg.fill('dialog[open] .cr', 'Too high on purpose'); self.pg.click('dialog[open] [data-ok]')
            self.pg.wait_for_selector('#e-min_deposit:not([hidden])'); self.assertIn('maximum', self.pg.inner_text('#e-min_deposit')); self.assertEqual(self.cfg()['financial']['min_deposit']['value'], '5.00')

        def test_email_card_is_honest_and_manage_templates_opens_the_email_center(self):
            self.login(); self.go('settings/general')
            self.assertEqual(self.pg.locator('[data-c=email] input[disabled]').count(), 2); self.assertEqual(self.pg.locator('[data-c=email] .st-switch[disabled]').count(), 2)
            self.assertIn('NOT configured', self.pg.inner_text('[data-c=email]')); self.assertIn('Not delivering', self.pg.inner_text('[data-c=email]'))     # a saved address is not a delivered message
            self.pg.click('[data-c=email] a.btn'); self.pg.wait_for_selector('#adm-side [data-k=email].on'); self.assertEqual(self.pg.evaluate('location.hash'), '#/email')

        def test_integration_status_never_claims_connected_and_api_rate_limit_saves(self):
            self.app.config.update({'PAYMENT_PROVIDER_KEY': 'sk_live_SECRET123', 'KYC_PROVIDER_KEY': 'kyc_SECRET456'}); seen = []
            urls = []
            def grab(r):
                urls.append(r.url)
                try: seen.append(r.text())
                except Exception: pass
            self.pg.on('response', grab); self.login(); self.go('settings/general')
            self.assertEqual(self.pg.inner_text('[data-c=integration] .st-tile:has-text("Analytics Provider") .st-badge'), 'Unavailable')
            self.pg.fill('#st-api_rate_limit_per_hour', '50000'); self.pg.click('[data-card=integration] button[type=submit]'); self.confirm()
            self.assertEqual(self.cfg()['limits']['api_rate_limit_per_hour']['value'], 50000)
            self.go('settings/integrations'); self.assertEqual(self.pg.locator('.st-card').count(), 9); badges = self.texts('.st-badge')
            self.assertNotIn('Connected', badges); self.assertTrue({'Configured', 'Not configured', 'Unavailable'} >= set(badges)); self.assertIn('not the same as connected', self.pg.inner_text('.st-banner'))
            for t in ('security', 'api', 'notifications', 'payments'): self.go('settings/' + t)
            self.assertFalse([s for s in seen if 'sk_live_SECRET123' in s or 'kyc_SECRET456' in s], 'a credential reached the browser')
            self.assertTrue(any('/api/admin/platform/settings' in u for u in urls))

        def test_security_notifications_and_payments_tabs_persist_through_their_real_endpoints(self):
            self.login(); self.go('settings/security'); self.assertEqual(self.pg.locator('#sw-require_2fa_all').count(), 1); self.assertEqual(self.pg.locator('[data-c=policy] .st-kv').count(), 5)
            self.pg.fill('#st-twofa_required_above', '750.00'); self.pg.click('[data-card=access] button[type=submit]'); self.confirm()
            self.assertEqual(self.cfg()['security']['twofa_required_above']['value'], '750.00')
            self.pg.click('#sw-risk_ack_required'); self.confirm('Reviewed with legal'); self.assertEqual((self.sw('risk_ack_required'), self.cfg()['security']['risk_ack_required']['value']), ('false', False))
            self.go('settings/notifications'); self.pg.fill('#st-email_max_attempts', '11'); self.pg.click('[data-card=mailrules] button[type=submit]'); self.assertTrue(self.pg.is_visible('#e-email_max_attempts')); self.assertEqual(self.dialogs()[1], 0)
            self.pg.fill('#st-email_max_attempts', '4'); self.pg.click('[data-card=mailrules] button[type=submit]'); self.confirm()
            self.assertEqual(self.j('/api/admin/email/settings')['j']['settings']['email_max_attempts']['value'], '4')
            self.assertIn('SMS', self.pg.inner_text('[data-c=channels]')); self.assertEqual(self.pg.locator('[data-c=channels] .st-badge:text-is("Unavailable")').count(), 2)
            self.go('settings/payments'); self.pg.fill('#st-card_fee_percent', '2.00'); self.pg.click('[data-card=fees] button[type=submit]'); self.confirm()
            self.assertEqual(self.cfg()['payments']['card_fee_percent']['value'], '2.00'); self.assertEqual(self.db.execute("SELECT value FROM system_settings WHERE key='card_fee_bps'").fetchone()[0], '200')
            self.go('settings/api'); self.assertIn('does not issue API keys', self.pg.inner_text('[data-c=creds]'))

        def test_unsaved_changes_are_protected_when_navigating_away(self):
            self.login(); self.go('settings/general'); self.pg.fill('#st-name', 'Half typed')
            self.pg.click('#tab-security'); self.assertEqual(self.dialogs()[1], 1); self.assertIn('unsaved', self.pg.inner_text('dialog[open]').lower()); self.pg.click('dialog[open] [data-x]')
            self.assertEqual((self.pg.evaluate('location.hash'), self.pg.input_value('#st-name')), ('#/settings/general', 'Half typed'))
            self.pg.click('#adm-side [data-k=audit], #adm-side .adm-g[data-g=security] .adm-chev'); self.pg.wait_for_timeout(200)
            self.pg.click('#tab-api'); self.pg.click('dialog[open] [data-ok]'); self.pg.wait_for_selector('#tab-api[aria-selected=true]'); self.assertEqual(self.pg.evaluate('location.hash'), '#/settings/api')
            self.go('settings/general'); self.assertEqual(self.pg.input_value('#st-name'), self.cfg()['general']['name']['value'])

        def test_every_tab_fits_every_width_and_dialogs_stay_inside_the_viewport(self):
            self.login()
            for w in (320, 375, 768, 1024, 1440):
                self.pg.set_viewport_size({'width': w, 'height': 800})
                for k, _ in self.TABS:
                    self.go('settings/' + k); self.assertTrue(self.pg.evaluate('document.documentElement.scrollWidth<=document.documentElement.clientWidth'), f'{k} overflows at {w}px')
                    self.assertTrue(self.pg.evaluate("[...document.querySelectorAll('#st-panel .btn, #st-panel .st-switch, #st-panel input, #st-panel select')].every(e=>{const r=e.getBoundingClientRect();return r.width===0||(r.left>=-1&&r.right<=innerWidth+1)})"), f'a control leaves the screen on {k} at {w}px')
                self.go('settings/general'); self.pg.click('#sw-live_prices_enabled'); self.pg.wait_for_selector('dialog[open] .cr')
                self.assertTrue(self.pg.evaluate("(()=>{const r=document.querySelector('dialog[open]').getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight})()"), f'dialog leaves the screen at {w}px')
                self.pg.click('dialog[open] [data-x]')
            self.assertEqual(self.errors, [])

    for _n in [n for n in dir(TestAdminConsole) if n.startswith('test_') and n not in TestSystemSettings.__dict__]: setattr(TestSystemSettings, _n, None)


# ---------------------------------------------------------------- Part 5: popup lifecycle (adversarial: in-flight requests, double submits, Escape, focus, row clicks)
if not SKIP:
    class TestPopupLifecycle(TestAdminConsole):
        """Tries to break the shared dialog manager through the real KYC and Settings flows. Requests are held in the browser so the test controls timing."""
        DEC = '**/api/admin/kyc/*/decision'
        def hold(self, pattern, method=None):
            held = []; self.pg.route(pattern, lambda r: held.append(r) if method in (None, r.request.method) else r.continue_()); return held
        def approve_started(self, held):
            self.open_kyc(); self.pg.click('dialog.dlg [data-d=approve]'); self.pg.wait_for_selector('dialog.dlg-confirm [data-ok]'); self.pg.click('dialog.dlg-confirm [data-ok]')
            for _ in range(40):
                if held: break
                self.pg.wait_for_timeout(50)
            self.assertEqual(len(held), 1)
        def status(self): return self.db.execute("SELECT status FROM kyc_profiles WHERE user_id=?", (self.kyc_user,)).fetchone()[0]
        def toast_text(self): self.pg.wait_for_selector('.toast'); return self.pg.inner_text('#toasts')

        def test_navigating_away_mid_request_reports_the_outcome_and_never_touches_the_new_page(self):
            self.login(); held = self.hold(self.DEC); self.approve_started(held)
            self.pg.evaluate("location.hash='#/users'"); self.pg.wait_for_selector('#adm-side [data-k=users].on'); self.pg.wait_for_selector('#view .pg > *:not(.sk-wrap)')
            self.assertEqual(self.dialogs()[0], 0)                                                                   # navigation closed both dialogs
            self.pg.evaluate("document.querySelector('#view input').value='typed on the new page'")
            held[0].continue_(); self.pg.wait_for_timeout(1000)                                                      # the late response arrives now
            self.assertEqual(self.status(), 'verified')                                                              # the server did it exactly once
            self.assertEqual(self.dialogs()[0], 0); self.assertEqual(self.pg.evaluate('location.hash'), '#/users')
            self.assertEqual(self.pg.evaluate("document.querySelector('#view input').value"), 'typed on the new page')    # no stray re-render over the page the admin is on
            self.assertIn('Approve verification', self.toast_text())                                                 # ...but the admin is told what happened
            self.assertEqual([p for p in self.posts if 'decision' in p[1]].__len__(), 1)

        def test_a_failure_after_navigating_away_is_still_reported(self):
            self.login(); held = self.hold(self.DEC); self.approve_started(held)
            self.pg.evaluate("location.hash='#/overview'"); self.pg.wait_for_selector('#adm-side [data-k=overview].on'); self.pg.wait_for_timeout(300)
            held[0].fulfill(status=500, content_type='application/json', body='{"error":{"code":"boom","message":"Backend exploded"}}'); self.pg.wait_for_timeout(500)
            t = self.toast_text(); self.assertIn('Backend exploded', t); self.assertIn('Approve verification', t); self.assertTrue(self.pg.locator('.toast.err').count() >= 1)
            self.assertEqual((self.dialogs()[0], self.status()), (0, 'under_review'))

        def test_escape_cannot_close_a_dialog_while_its_request_is_running(self):
            self.login(); held = self.hold(self.DEC); self.approve_started(held)
            self.assertTrue(self.pg.is_disabled('dialog.dlg-confirm [data-ok]')); self.assertIn('Working', self.pg.inner_text('dialog.dlg-confirm [data-ok]'))
            for _ in range(4): self.pg.keyboard.press('Escape'); self.pg.wait_for_timeout(120)                      # Chromium lets a repeated Escape through a prevented 'cancel'
            self.assertEqual(self.dialogs(), [2, 2])
            held[0].continue_(); self.pg.wait_for_selector('.toast.ok'); self.pg.wait_for_timeout(400)
            self.assertEqual((self.dialogs()[0], self.status()), (0, 'verified'))

        def test_double_clicks_and_double_submits_send_one_request(self):
            self.login(); held = self.hold(self.DEC); self.open_kyc(); self.pg.click('dialog.dlg [data-d=approve]'); self.pg.wait_for_selector('dialog.dlg-confirm [data-ok]')
            self.pg.evaluate("(()=>{const b=document.querySelector('dialog.dlg-confirm [data-ok]');b.click();b.click();b.click()})()"); self.pg.wait_for_timeout(300)
            self.assertEqual(len(held), 1); held[0].continue_(); self.pg.wait_for_timeout(800); self.assertEqual(len([p for p in self.posts if 'decision' in p[1]]), 1)
            self.go('overview')
            self.pg.evaluate("""(()=>{window.__n=0;UI.dialog.form({title:'T',key:'t-form',body:'<label class="f">A<input name="a" value="x" required></label>',onSubmit:async()=>{window.__n++;await new Promise(r=>setTimeout(r,400))}})})()""")
            self.pg.wait_for_selector('dialog .dlg-form'); self.pg.evaluate("(()=>{const f=document.querySelector('dialog .dlg-form');for(let i=0;i<3;i++)f.requestSubmit()})()")
            self.pg.wait_for_timeout(900); self.assertEqual(self.pg.evaluate('window.__n'), 1)

        def test_focus_stays_in_the_top_dialog_and_returns_to_the_control_that_opened_it(self):
            self.login(); self.open_kyc(); self.pg.focus('dialog.dlg [data-d=approve]'); self.pg.keyboard.press('Enter'); self.pg.wait_for_selector('dialog.dlg-confirm [data-ok]')
            for _ in range(12):
                self.pg.keyboard.press('Tab')       # a native modal may hand focus to the browser's own UI (activeElement is then <body>) but never to the page behind it
                self.assertTrue(self.pg.evaluate("document.activeElement===document.body||!!document.activeElement.closest('dialog.dlg-confirm')"), 'focus reached the page behind the confirmation')
            self.assertTrue(self.pg.evaluate("document.querySelector('dialog.dlg:not(.dlg-confirm)').inert"))             # the review underneath cannot be reached
            self.pg.evaluate("document.querySelector('dialog.dlg:not(.dlg-confirm) [data-d=reject]').click()"); self.pg.wait_for_timeout(150); self.assertEqual(self.dialogs(), [2, 2])
            self.pg.keyboard.press('Escape'); self.pg.wait_for_timeout(150); self.assertEqual(self.dialogs(), [1, 1])
            self.assertEqual(self.pg.evaluate("document.activeElement.dataset.d"), 'approve')                          # focus is back on the button that opened the confirmation
            self.assertFalse(self.pg.evaluate("document.querySelector('dialog.dlg').inert"))

        def test_a_button_inside_a_clickable_row_never_also_opens_the_row_dialog(self):
            self.login(); self.go('kyc')
            self.pg.evaluate("document.querySelector('#view tr[data-i] td').insertAdjacentHTML('beforeend','<button type=\"button\" id=\"inrow\">inline</button><a href=\"#/kyc\" id=\"inlink\">x</a>')")
            self.pg.click('#inrow'); self.pg.wait_for_timeout(250); self.assertEqual(self.dialogs()[0], 0)
            self.pg.click('#inlink'); self.pg.wait_for_timeout(250); self.assertEqual(self.dialogs()[0], 0)
            self.pg.click('#view tr[data-i] td >> nth=1'); self.pg.wait_for_selector('dialog.dlg [data-d]'); self.assertEqual(self.dialogs(), [1, 1])    # a plain click on the row still opens it

        def test_settings_save_survives_navigation_with_one_notice_and_no_dialog(self):
            self.login(); held = self.hold('**/api/admin/platform/settings', 'PUT'); self.go('settings/general')
            self.pg.click('#sw-live_prices_enabled'); self.pg.fill('dialog[open] .cr', 'Provider outage drill'); self.pg.click('dialog[open] [data-ok]')
            for _ in range(40):
                if any(True for _ in held): break
                self.pg.wait_for_timeout(50)
            self.pg.evaluate("location.hash='#/overview'"); self.pg.wait_for_selector('#adm-side [data-k=overview].on'); self.pg.wait_for_timeout(300)
            held[-1].continue_(); self.pg.wait_for_timeout(1000)
            self.assertEqual(self.dialogs()[0], 0); self.assertFalse(self.pg.evaluate("fetch('/api/platform/status').then(r=>r.json())")['live_prices_enabled'])
            self.assertEqual(self.pg.locator('.toast').count(), 1)                                                     # exactly one notice, not two

    for _n in [n for n in dir(TestAdminConsole) if n.startswith('test_') and n not in TestPopupLifecycle.__dict__]: setattr(TestPopupLifecycle, _n, None)


# ---------------------------------------------------------------- Part 6: responsive sweep (every admin page and settings tab at the target widths)
if not SKIP:
    class TestResponsiveSweep(TestAdminConsole):
        """Signs in at desktop width, then shrinks the window (the order that exposed a render-time sizing bug) and checks every page for page-level horizontal
        overflow, content pushed past the screen edge outside a scroll container, and overlapping or off-screen header controls."""
        PAGES = ['overview', 'users', 'kyc', 'support', 'deposits', 'withdrawals', 'transactions', 'accounts', 'investments', 'savings', 'retirement', 'assets', 'orders', 'notifications',
                 'email', 'community', 'content', 'legal', 'reports', 'audit', 'roles', 'settings-crypto'] + ['settings/' + t for t in ('general', 'security', 'api', 'notifications', 'payments', 'integrations')]
        CHECK = """(()=>{const W=innerWidth,out=[];
 const scrollers=e=>{for(let p=e.parentElement;p&&p!==document.body;p=p.parentElement){const o=getComputedStyle(p).overflowX;if(o==='auto'||o==='scroll'||o==='hidden')return true}return false};
 if(document.documentElement.scrollWidth>W+1)out.push('PAGE-OVERFLOW '+document.documentElement.scrollWidth+'>'+W);
 document.querySelectorAll('#view *').forEach(e=>{const r=e.getBoundingClientRect();if(r.width&&r.right>W+1&&!scrollers(e)&&getComputedStyle(e).position!=='fixed')out.push('OFF '+e.tagName+'.'+(e.className||'').toString().slice(0,30)+' r='+Math.round(r.right)+' '+(e.textContent||'').trim().slice(0,24))});
 const hdr=[...document.querySelectorAll('.adm-top > *, .adm-top .adm-tools > *')].filter(e=>e.offsetWidth&&getComputedStyle(e).visibility!=='hidden');const bx=hdr.map(e=>[e,e.getBoundingClientRect()]);
 for(let i=0;i<bx.length;i++)for(let j=i+1;j<bx.length;j++){const a=bx[i][1],b=bx[j][1];if(bx[i][0].contains(bx[j][0])||bx[j][0].contains(bx[i][0]))continue;if(a.left<b.right-1&&b.left<a.right-1&&a.top<b.bottom-1&&b.top<a.bottom-1)out.push('HDR-OVERLAP '+(bx[i][0].className||bx[i][0].id||bx[i][0].tagName)+' x '+(bx[j][0].className||bx[j][0].id||bx[j][0].tagName))}
 hdr.forEach(e=>{const r=e.getBoundingClientRect();if(r.right>W+1||r.left<-1)out.push('HDR-OFF '+(e.className||e.id||e.tagName))});
 return out.slice(0,6)})()"""
        def test_no_page_overflows_or_overlaps_at_any_target_width(self):
            self.login(); bad = {}
            for w in (320, 360, 375, 390, 430, 768, 1024, 1280):
                self.pg.set_viewport_size({'width': w, 'height': 800})
                for p in self.PAGES:
                    self.go(p); o = self.pg.evaluate(self.CHECK)
                    if o: bad[f'{w}px {p}'] = o
            self.assertEqual(bad, {}); self.assertEqual(self.errors, [])

    for _n in [n for n in dir(TestAdminConsole) if n.startswith('test_') and n not in TestResponsiveSweep.__dict__]: setattr(TestResponsiveSweep, _n, None)

if __name__ == '__main__': unittest.main()
