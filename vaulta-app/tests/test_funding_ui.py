"""Browser-level checks of the separate Deposit (#/deposit) and Withdraw (#/withdraw) pages, against the real app and database (mock chain only).
Uses Playwright + Chromium when they are installed and SKIPS otherwise, so the normal backend suite never depends on a browser.
Also contains static checks that the old combined page and its links are gone."""
import io, os, re, sys, threading, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_funding_pages import FP
from test_crypto_requests import png
from server import security as sec

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def read(p):
    with open(os.path.join(ROOT, p), encoding='utf-8') as f: return f.read()

try:
    from playwright.sync_api import sync_playwright
    HAVE_PW = True
except Exception:                                             # pragma: no cover
    HAVE_PW = False


class TestStaticWiring(unittest.TestCase):
    def test_old_combined_page_and_links_are_gone(self):
        app, mdash = read('web/js/app.js'), read('web/js/mdash.js')
        self.assertNotIn("R.deposit=()=>", app); self.assertNotIn('Deposit & withdraw', app); self.assertNotIn('deposit?w=1', app + mdash)
        self.assertNotIn('/api/withdrawals', read('web/js/crypto.js'))
    def test_separate_routes_and_nav_entries_exist_once(self):
        app = read('web/js/app.js'); self.assertEqual(app.count("['deposit','Deposit']"), 1); self.assertEqual(app.count("['withdraw','Withdraw']"), 1)
        js = read('web/js/crypto.js'); self.assertIn('R.deposit =', js); self.assertIn('R.withdraw =', js)
        self.assertIn("'#/withdraw'", read('server/crypto_withdrawals.py')); self.assertIn("['Withdraw','withdraw','#/withdraw']", read('web/js/mdash.js'))
    def test_stylesheet_is_linked_and_scoped(self):
        self.assertIn('../css/funding.css', read('web/app/index.html')); css = read('web/css/funding.css')
        self.assertNotRegex(css, r'(?m)^(body|html|h1|h2|button|input)\b')            # never restyles global elements
    def test_no_hardcoded_asset_lists_in_customer_pages(self):
        js = read('web/js/crypto.js')
        for forbidden in ('Bitcoin (BTC)', "'BTC'", '"BTC"', 'bc1q', '0x1111'): self.assertNotIn(forbidden, js, forbidden)
    def test_mobile_quick_actions_savings_replaces_transfer_and_goals_is_named_goals(self):
        m = read('web/js/mdash.js')
        self.assertIn("['Savings','piggy','#/savings']", m); self.assertIn("['Goals','goal','#/goals']", m); self.assertIn("piggy:", m)
        for gone in ("'Transfer'", 'Savings Goals', 'data-act="transfer"', "transfer:'", "Moving money between Vaulta accounts"): self.assertNotIn(gone, m, gone)
    def test_cash_is_labelled_account_balance_on_both_dashboards(self):
        app, m = read('web/js/app.js'), read('web/js/mdash.js')
        self.assertIn('<h2>Account Balance</h2>', app); self.assertIn('<small>Account Balance</small>', m)
        for gone in ('<h2>Available cash</h2>', 'Available Cash'): self.assertNotIn(gone, app + m, gone)
        self.assertIn('Savings → Account Balance', app); self.assertIn("'Savings'],ACOL", app)
    def test_no_browser_storage_or_secrets_in_customer_pages(self):
        js = read('web/js/crypto.js'); self.assertNotRegex(js, r'localStorage|sessionStorage'); self.assertNotRegex(js, r'private[_ ]?key|seed phrase|mnemonic')


@unittest.skipUnless(HAVE_PW, 'playwright is not installed')
class TestPagesInBrowser(FP):
    @classmethod
    def setUpClass(cls):
        cls._pw = sync_playwright().start()
        try: cls.browser = cls._pw.chromium.launch()
        except Exception as e: cls._pw.stop(); raise unittest.SkipTest(f'chromium is not available: {e}')
    @classmethod
    def tearDownClass(cls): cls.browser.close(); cls._pw.stop()

    def setUp(self):
        super().setUp()
        from werkzeug.serving import make_server
        self.srv = make_server('127.0.0.1', 0, self.app, threaded=True); self.port = self.srv.server_port
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.c, self.u = self.customer(); self.totp(self.u['id']); self.fund(self.u, 200_000_000)
        aid = self.one("SELECT id FROM crypto_assets WHERE symbol='USDT'")[0]
        r = self.cops.post(f'/api/admin/crypto/assets/{aid}', data={'logo': (io.BytesIO(png(64, 64, seed=5)), 'l.png')}, content_type='multipart/form-data', headers={'X-CSRF-Token': self.cops.csrf}); self.assertEqual(r.status_code, 200, r.json)
        self.cfg(min_withdrawal='10', fee_flat='1', fee_percent='0.5', estimated_arrival='5-10 minutes (estimate)')
    def tearDown(self): self.srv.shutdown(); super().tearDown()

    def page(self, route, w=1280, h=900, ready='.fp'):
        ctx = self.browser.new_context(viewport={'width': w, 'height': h})
        cookies = {k[2]: v.value for k, v in self.c._cookies.items()}
        ctx.add_cookies([{'name': k, 'value': v, 'domain': '127.0.0.1', 'path': '/'} for k, v in cookies.items()])
        pg = ctx.new_page(); self.errors = []; pg.on('pageerror', lambda e: self.errors.append(str(e)))
        pg.goto(f'http://127.0.0.1:{self.port}/app/#/{route}'); pg.wait_for_selector(ready, timeout=15000); pg.wait_for_timeout(600)
        try: pg.get_by_text('Reject optional cookies').click(timeout=1200)
        except Exception: pass
        return pg
    def overflow(self, pg): return pg.evaluate('document.documentElement.scrollWidth - document.documentElement.clientWidth')

    def test_deposit_and_withdraw_are_separate_authenticated_routes(self):
        d = self.page('deposit'); self.assertIn('Deposit Cryptocurrency', d.inner_text('h1')); self.assertEqual(d.locator('#w-flow').count(), 0)
        w = self.page('withdraw'); self.assertIn('Withdraw Funds', w.inner_text('h1')); self.assertEqual(w.locator('#d-top').count(), 0)
        self.assertEqual(w.locator('nav a[data-k=withdraw]').count(), 1); self.assertEqual(w.locator('nav a[data-k=deposit]').count(), 1)
        self.assertIn('on', w.get_attribute('nav a[data-k=withdraw]', 'class')); w.reload(); w.wait_for_selector('.fp'); self.assertIn('Withdraw Funds', w.inner_text('h1'))     # refresh keeps the route
        anon = self.browser.new_context().new_page(); anon.goto(f'http://127.0.0.1:{self.port}/app/#/withdraw'); anon.wait_for_timeout(1500); self.assertEqual(anon.locator('.fp').count(), 0)       # not signed in: no page
        self.assertEqual(self.errors, [])
    def test_deposit_address_and_qr_follow_the_selected_asset_and_network(self):
        pg = self.page('deposit'); pg.click('#d-asset-b'); pg.click('#d-asset-l li:has-text("USDT")'); pg.wait_for_timeout(1200)
        addr = pg.inner_text('#d-addr').strip(); self.assertTrue(addr.startswith('0x')); self.assertIn('5-10 minutes (estimate)', pg.inner_text('.facts'))     # admin-configured arrival text, not hard-coded
        qr = pg.get_attribute('.qr-box img', 'src'); ref = re.search(r'deposit-intents/([^/]+)/qr.svg', qr).group(1)
        row = self.one('SELECT w.public_address FROM deposit_intents i JOIN company_crypto_wallets w ON w.id=i.company_wallet_id WHERE i.ref=?', ref)
        self.assertEqual(row[0].lower(), addr.lower())                                                                 # the QR's intent points at the exact address on screen
        pg.click('#d-asset-b'); pg.click('#d-asset-l li:has-text("BTC")'); pg.wait_for_timeout(1200)
        self.assertNotEqual(pg.inner_text('#d-addr').strip(), addr); self.assertNotEqual(pg.get_attribute('.qr-box img', 'src'), qr); self.assertEqual(self.errors, [])
    def test_selectors_show_the_configured_logo_and_never_a_broken_image(self):
        pg = self.page('withdraw'); pg.click('#w-asset-b'); pg.wait_for_timeout(200)
        n = pg.evaluate("""()=>[...document.querySelectorAll('#w-asset-l li')].map(li=>{const i=li.querySelector('img.alogo'),f=li.querySelector('.alogo.fb');return !!(i||f)})""")
        self.assertTrue(all(n) and len(n) == 5, n)                                                                      # every option has an icon (image or the generated fallback)
        pg.click('#w-asset-l li:has-text("USDT")'); pg.wait_for_timeout(800)
        self.assertEqual(pg.locator('#w-asset-b img.alogo').count(), 1); self.assertTrue(pg.evaluate("document.querySelector('#w-asset-b img.alogo').naturalWidth>0"))     # the uploaded logo actually loads
        self.assertEqual(pg.locator('#w-ami img.alogo').count(), 1)                                                     # and appears in the amount control too
    def test_listbox_keyboard_support(self):
        pg = self.page('withdraw'); pg.focus('#w-asset-b'); pg.keyboard.press('ArrowDown'); self.assertEqual(pg.get_attribute('#w-asset-b', 'aria-expanded'), 'true')
        pg.keyboard.press('ArrowDown'); pg.keyboard.press('Enter'); pg.wait_for_timeout(700); self.assertEqual(pg.evaluate('document.activeElement.id'), 'w-asset-b')
        pg.click('#w-asset-b'); pg.keyboard.press('Escape'); self.assertEqual(pg.get_attribute('#w-asset-b', 'aria-expanded'), 'false')
    def test_withdraw_validation_withdraw_all_confirmation_and_pending_result(self):
        pg = self.page('withdraw'); pg.click('#w-asset-b'); pg.click('#w-asset-l li:has-text("USDT")'); pg.wait_for_timeout(900)
        self.assertIn('200', pg.inner_text('#w-bal'))                                                                  # real ledger balance
        pg.fill('#w-addr', 'nope'); pg.wait_for_timeout(1100); self.assertIn('Not a valid', pg.inner_text('#w-av'))
        pg.fill('#w-addr', self.dest()); pg.wait_for_timeout(1100); self.assertIn('format is valid', pg.inner_text('#w-av'))
        pg.fill('#w-amt', '5'); pg.wait_for_timeout(900); self.assertIn('minimum', pg.inner_text('#w-fees').lower())
        pg.click('[data-all]'); pg.wait_for_timeout(900); amt = pg.input_value('#w-amt'); self.assertEqual(self.c.get(f'/api/crypto/withdrawal-info?ledger_code={self.CODE}').json['max_amount'].rstrip('0').rstrip('.'), amt)
        self.assertIn('200', pg.inner_text('#w-fees'))                                                                 # total deducted equals the whole balance
        pg.click('#w-go'); pg.wait_for_selector('#wcf'); self.assertTrue(pg.is_disabled('#wok'))                          # cannot confirm without 2FA code and acknowledgement
        pg.fill('#wcode', sec.totp_code(self.secret[self.u['id']])); self.assertTrue(pg.is_disabled('#wok')); pg.check('#wck'); self.assertFalse(pg.is_disabled('#wok'))
        pg.click('#wok'); pg.wait_for_selector('#w-done .okpanel'); self.assertIn('pending review', pg.inner_text('#w-done')); self.assertIn('not', pg.inner_text('#w-done').lower())
        row = self.one("SELECT status,total FROM (SELECT status, amount+fee AS total FROM crypto_withdrawals ORDER BY id DESC LIMIT 1)"); self.assertEqual(row[0], 'pending_approval'); self.assertLess(self.cash(self.u), 5)
        self.assertIn('Pending review', pg.inner_text('#fp-hist')); self.assertNotIn('Completed', pg.inner_text('#fp-hist')); self.assertEqual(self.errors, [])
    def test_2fa_missing_blocks_the_withdraw_button(self):
        self.db.execute('UPDATE users SET totp_enabled=0 WHERE id=?', (self.u['id'],)); pg = self.page('withdraw'); self.assertTrue(pg.is_disabled('#w-go')); self.assertIn('two-factor', pg.inner_text('#w-s4').lower())
    def test_admin_changes_reach_the_customer_pages(self):
        pg = self.page('deposit'); pg.click('#d-asset-b'); pg.click('#d-asset-l li:has-text("USDT")'); pg.wait_for_timeout(1000); self.assertIn('5-10 minutes (estimate)', pg.inner_text('.facts'))
        self.cfg(estimated_arrival='about a minute', deposit_enabled=False); pg.reload(); pg.wait_for_selector('.fp'); pg.wait_for_timeout(800); pg.click('#d-asset-b')
        self.assertEqual(pg.locator('#d-asset-l li:has-text("USDT")').count(), 0)                                      # deposits disabled for the network: no longer offered here
        w = self.page('withdraw'); w.click('#w-asset-b'); self.assertEqual(w.locator('#w-asset-l li:has-text("USDT")').count(), 1)                                 # ...but withdrawals stay available
    def test_mobile_dashboard_tiles_open_the_right_pages(self):
        pg = self.page('dashboard', 390, 900, ready='.mqa'); names = pg.eval_on_selector_all('.mqa .ql', 'e=>e.map(x=>x.innerText.trim())')
        self.assertEqual(names, ['Invest', 'Deposit', 'Withdraw', 'Savings', 'Goals', 'Trade', 'Portfolio', 'Retirement', 'Statements', 'Activity', 'Security', 'Support'])
        self.assertIn('account balance', pg.inner_text('#view').lower()); self.assertEqual(pg.locator('.mqa .qi svg').count(), 12)            # every tile has an icon
        pg.context.close()
        for label, hashv in (('Goals', '#/goals'), ('Withdraw', '#/withdraw'), ('Deposit', '#/deposit')):
            p2 = self.page('dashboard', 390, 900, ready='.mqa'); p2.locator('.mqa .qa', has_text=label).first.click(); p2.wait_for_function(f"location.hash.startsWith('{hashv}')"); p2.context.close()
        pg = self.page('dashboard', 390, 900, ready='.mqa'); pg.locator('.mqa .qa', has_text='Savings').first.click(); pg.wait_for_function("location.hash==='#/savings'"); pg.wait_for_timeout(1500); body = pg.inner_text('#view'); self.assertIn('Account Balance → Savings', body, pg.url + ' | ' + body[:300])
        self.assertEqual(self.errors, [])
    def test_no_horizontal_overflow_on_phone_widths_and_long_content(self):
        for width in (320, 360, 390, 768):
            for route in ('deposit', 'withdraw'):
                pg = self.page(route, width, 800); pg.wait_for_timeout(500)
                if route == 'withdraw': pg.fill('#w-addr', 'x' * 120); pg.wait_for_timeout(300)
                self.assertLessEqual(self.overflow(pg), 0, f'{route} overflows at {width}px'); pg.context.close()


# ---------------------------------------------------------------- Mobile top header is white (<=860px); desktop and the floating nav are untouched
class TestMobileHeaderCss(unittest.TestCase):
    CSS = __import__('re').sub(r'/\*.*?\*/', '', read('web/css/dash.css'), flags=__import__('re').S)      # comments removed so a block's prelude is just its selector
    @classmethod
    def blocks(cls):
        out, depth, start, pre = [], 0, 0, ''
        for i, ch in enumerate(cls.CSS):
            if ch == '{':
                if depth == 0: pre = cls.CSS[start:i].strip(); bstart = i + 1
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0: out.append((pre, cls.CSS[bstart:i])); start = i + 1
        return out
    def test_white_header_rules_exist_only_inside_the_860px_media_query(self):
        marks = ['.dhero{background:#fff;color:var(--black)}', '.dhero .ibtn:hover{background:#f1f1ef}', '.dhero .dot{background:var(--black);box-shadow:0 0 0 2px #fff}', '.dhero .srch{background:#f5f5f3;border:1px solid #e4e4e0}']
        for m in marks:
            homes = [pre.replace(' ', '') for pre, body in self.blocks() if m in body]
            self.assertEqual(homes, ['@media(max-width:860px)'], m)
        outside = ''.join(body if not pre.startswith('@') else '' for pre, body in self.blocks()) + ''.join(pre + '{' + body + '}' for pre, body in self.blocks() if not pre.startswith('@'))
        for m in marks: self.assertNotIn(m, outside, 'a white-header rule leaked outside the mobile media query: ' + m)
    def test_the_black_desktop_header_rule_is_unchanged(self):
        self.assertIn('.dhero{background:var(--black);color:#fff;border-radius:28px;padding:16px 24px 24px;margin-bottom:16px}', self.CSS)
        self.assertIn('.dhero.compact{padding:12px 24px;margin-bottom:22px}', self.CSS)
        self.assertIn('.ibtn:hover{background:#1a1a1a}', self.CSS)
    def test_logo_markup_dark_in_the_mobile_header_and_white_on_the_black_sidebar(self):
        app = read('web/js/app.js'); hdr = app[app.index('const headerHTML'):app.index('</header>')]
        self.assertIn("class=\"m-logo\"", hdr); self.assertIn("BRAND.img('black','logo-full')", hdr); self.assertNotIn("BRAND.img('white'", hdr)
        self.assertIn("<aside class=\"side\"", app); self.assertIn("BRAND.img('white'", app[app.index('<aside class="side"'):app.index('<aside class="side"') + 400])
        self.assertIn('.m-only,.m-logo,.dav-m{display:none}', self.CSS)       # outside the media query: the mobile logo link is never rendered on desktop


@unittest.skipUnless(HAVE_PW, 'playwright is not installed')
class TestMobileHeader(TestPagesInBrowser):
    """Real browser, real pages. The inherited page tests run once, in TestPagesInBrowser."""
    ROUTES = ['dashboard', 'portfolio', 'invest', 'trade', 'savings', 'goals', 'retirement', 'watchlist', 'transactions', 'statements', 'notifications', 'support', 'deposit', 'withdraw',
              'profile', 'profile/personal', 'profile/security', 'profile/password', 'profile/two-factor', 'profile/activity', 'profile/devices', 'profile/notifications', 'profile/preferences',
              'profile/privacy', 'profile/data', 'profile/help', 'settings']
    PROBE = """(()=>{const q=s=>document.querySelector(s),cs=(e,p)=>getComputedStyle(e)[p],r=e=>{const b=e.getBoundingClientRect();return{x:b.x,y:b.y,w:b.width,h:b.height,cx:b.x+b.width/2,cy:b.y+b.height/2,r:b.right}};
      const hero=q('#hero'),logo=q('#hero .m-logo img'),sb=q('#sbtn'),bell=q('#hero .bell'),av=q('#hero .dav-m'),nav=q('nav.mnav');
      return{heroBg:cs(hero,'backgroundColor'),heroImg:cs(hero,'backgroundImage'),radius:cs(hero,'borderRadius'),display:cs(hero,'display'),logoSrc:logo.getAttribute('src'),logoLoaded:logo.complete&&logo.naturalWidth>0,logo:r(logo),
        sbColor:cs(sb,'color'),bellColor:cs(bell,'color'),sb:r(sb),bell:r(bell),av:r(av),avRadius:cs(av,'borderRadius'),avBg:cs(av,'backgroundColor'),
        nav:r(nav),navBg:cs(nav,'backgroundColor'),navPos:cs(nav,'position'),scrollW:document.documentElement.scrollWidth,clientW:document.documentElement.clientWidth}})()"""
    def probe(self, route, w):
        pg = self.page(route, w=w, h=844, ready='#view'); pg.wait_for_timeout(1100); return pg, pg.evaluate(self.PROBE)
    def check_header(self, route, w, p):
        t = f'{route} @{w}px'
        self.assertEqual((p['heroBg'], p['heroImg']), ('rgb(255, 255, 255)', 'none'), t)                       # white, no gradient or overlay
        self.assertIn('vaulta-logo-black.png', p['logoSrc'], t); self.assertTrue(p['logoLoaded'], t + ': the logo image did not load'); self.assertGreater(p['logo']['h'], 10, t)
        self.assertEqual((p['sbColor'], p['bellColor']), ('rgb(10, 10, 10)', 'rgb(10, 10, 10)'), t)
        self.assertEqual(p['avBg'], 'rgb(42, 42, 42)', t); self.assertEqual(p['avRadius'], '50%', t)                # the avatar is exactly as it was
        self.assertAlmostEqual(p['av']['w'], p['av']['h'], delta=0.5, msg=t)
        for k in ('sb', 'bell', 'av'): self.assertLess(abs(p[k]['cy'] - p['logo']['cy']), 3, f'{t}: {k} is not aligned with the logo')
        self.assertLess(p['logo']['r'], p['sb']['x'], t); self.assertLess(p['sb']['r'], p['bell']['x'] + 1, t); self.assertLess(p['bell']['r'], p['av']['x'] + 1, t)   # nothing overlaps
        self.assertLessEqual(p['av']['r'], w + 0.5, t); self.assertLessEqual(p['scrollW'], p['clientW'], f'{t}: horizontal overflow')
        self.assertEqual((p['navBg'], p['navPos']), ('rgb(10, 10, 10)', 'fixed'), f'{t}: the floating nav must stay black')

    def test_every_user_page_has_a_white_header_with_dark_controls_at_390(self):
        bad = []
        for route in self.ROUTES:
            pg, p = self.probe(route, 390)
            if route == 'dashboard':                                                                            # the dashboard keeps its own (already white) mobile header
                self.assertEqual(p['display'], 'none'); d = pg.evaluate("(()=>{const m=document.querySelector('.mdash .mh');let e=m,bg='rgba(0, 0, 0, 0)';while(e&&bg==='rgba(0, 0, 0, 0)'){bg=getComputedStyle(e).backgroundColor;e=e.parentElement}return{bg,logo:document.querySelector('.mdash .mh-logo img').getAttribute('src'),bell:getComputedStyle(document.querySelector('.mdash .mh-bell')).color}})()")
                self.assertEqual(d['bg'], 'rgb(255, 255, 255)'); self.assertIn('black', d['logo']); self.assertEqual(d['bell'], 'rgb(16, 16, 16)')
            else: self.check_header(route, 390, p); self.assertEqual((p['radius'], p['display']), ('0px 0px 28px 28px', 'block'), route)
            self.assertEqual(self.errors, [], route); pg.context.close()

    def test_white_header_holds_at_every_mobile_width_and_the_nav_keeps_its_box(self):
        for w in (320, 360, 375, 390, 430, 768, 860):
            for route in ('invest', 'portfolio', 'profile/security', 'deposit'):
                pg, p = self.probe(route, w); self.check_header(route, w, p)
                if w == 390: self.assertEqual((round(p['nav']['x']), round(p['nav']['w']), round(p['nav']['h'])), (16, 358, 66))
                pg.context.close()

    def test_search_hover_menu_and_notification_dot_stay_visible_and_working_on_white(self):
        pg, _ = self.probe('invest', 390)
        pg.hover('#sbtn'); self.assertEqual(pg.evaluate("getComputedStyle(document.getElementById('sbtn')).color"), 'rgb(10, 10, 10)')
        self.assertGreater(pg.evaluate("(()=>{const c=getComputedStyle(document.getElementById('sbtn')).backgroundColor.match(/\\d+/g).map(Number);return (c[0]+c[1]+c[2])/3})()"), 200)       # a hovered icon never lands on a dark circle
        pg.hover('#hero .bell'); self.assertGreater(pg.evaluate("(()=>{const c=getComputedStyle(document.querySelector('#hero .bell')).backgroundColor.match(/\\d+/g).map(Number);return (c[0]+c[1]+c[2])/3})()"), 200)
        pg.mouse.move(5, 400); pg.click('#sbtn'); pg.wait_for_selector('#srchf', state='visible'); self.assertEqual(pg.get_attribute('#sbtn', 'aria-expanded'), 'true')
        s = pg.evaluate("(()=>{const f=getComputedStyle(document.getElementById('srchf')),i=getComputedStyle(document.getElementById('q'));return{bg:f.backgroundColor,bw:f.borderTopWidth,bc:f.borderTopColor,ic:i.color}})()")
        self.assertEqual((s['bg'], s['bw']), ('rgb(245, 245, 243)', '1px')); self.assertEqual(s['bc'], 'rgb(228, 228, 224)')                         # the field is visible on white
        pg.fill('#q', 'bond'); self.assertEqual(pg.input_value('#q'), 'bond'); self.assertNotEqual(s['ic'], 'rgb(255, 255, 255)')
        pg.fill('#q', ''); pg.click('#sbtn'); pg.wait_for_timeout(150)
        pg.evaluate("document.getElementById('ndot').hidden=false")                                                                              # an unread notification
        d = pg.evaluate("(()=>{const e=getComputedStyle(document.getElementById('ndot')),b=document.getElementById('ndot').getBoundingClientRect();return{bg:e.backgroundColor,sh:e.boxShadow,w:b.width,h:b.height}})()")
        self.assertEqual(d['bg'], 'rgb(10, 10, 10)'); self.assertIn('rgb(255, 255, 255)', d['sh']); self.assertGreater(d['w'], 5)
        pg.click('#acctb'); pg.wait_for_selector('#acctm', state='visible'); self.assertEqual(pg.get_attribute('#acctb', 'aria-expanded'), 'true')   # the avatar still opens the account menu
        m = pg.evaluate("(()=>{const m=getComputedStyle(document.getElementById('acctm')),a=getComputedStyle(document.querySelector('#acctm a'));return{bg:m.backgroundColor,fg:a.color}})()")
        self.assertEqual((m['bg'], m['fg']), ('rgb(255, 255, 255)', 'rgb(17, 17, 17)')); self.assertEqual(self.errors, []); pg.context.close()

    def test_header_stays_white_while_navigating_inside_the_app_without_reloading(self):
        pg = self.page('invest', w=390, h=844, ready='#view'); pg.wait_for_timeout(900); seen = []
        for route in ['portfolio', 'deposit', 'watchlist', 'profile/security', 'notifications', 'support', 'trade', 'savings', 'retirement', 'goals', 'transactions', 'statements', 'withdraw', 'profile', 'invest']:
            pg.evaluate("r=>{location.hash='#/'+r}", route); pg.wait_for_selector('#hero', state='visible'); pg.wait_for_timeout(450)
            seen.append((route, pg.evaluate("document.getElementById('hero').getBoundingClientRect().height>0?getComputedStyle(document.getElementById('hero')).backgroundColor:'hidden'")))
        self.assertEqual([r for r, bg in seen if bg != 'rgb(255, 255, 255)'], [], seen)                                 # no page flips the header back to black
        pg.evaluate("location.hash='#/dashboard'"); pg.wait_for_selector('.mdash .mh', state='visible'); pg.wait_for_timeout(300)
        self.assertEqual(pg.evaluate("getComputedStyle(document.getElementById('hero')).display"), 'none'); self.assertEqual(self.errors, []); pg.context.close()

    def test_desktop_header_is_unchanged_at_and_above_861px(self):
        for w in (861, 1024, 1280, 1440):
            for route in ('invest', 'portfolio', 'dashboard', 'profile'):
                pg = self.page(route, w=w, h=900, ready='#view'); pg.wait_for_timeout(1000)
                d = pg.evaluate("""(()=>{const q=s=>document.querySelector(s),cs=(e,p)=>getComputedStyle(e)[p];const h=q('#hero'),b=q('#hero .bell');
                  return{bg:cs(h,'backgroundColor'),fg:cs(h,'color'),logo:cs(q('#hero .m-logo'),'display'),bell:b?cs(b,'color'):null,avBg:cs(q('#hero .dav-m'),'backgroundColor'),avDisp:cs(q('#hero .dav-m'),'display'),side:cs(q('.side'),'backgroundColor'),mnav:cs(q('nav.mnav'),'display')}})()""")
                light = route == 'portfolio'                                                                    # the desktop Portfolio page has always had its own transparent "light" header
                self.assertEqual((d['bg'], d['fg'], d['logo'], d['avBg'], d['avDisp']), (('rgba(0, 0, 0, 0)', 'rgb(10, 10, 10)') if light else ('rgb(10, 10, 10)', 'rgb(255, 255, 255)')) + ('none', 'rgb(42, 42, 42)', 'none'), f'{route} @{w}px')
                if d['bell'] and not light: self.assertEqual(d['bell'], 'rgb(255, 255, 255)')
                self.assertNotEqual(d['side'], 'rgb(255, 255, 255)'); self.assertEqual(d['mnav'], 'none'); pg.context.close()

    for _n in [n for n in dir(TestPagesInBrowser) if n.startswith('test_') and n not in dict(locals())]: locals()[_n] = None



if __name__ == '__main__': unittest.main()
