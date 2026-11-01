"""Browser tests (Playwright) for: Wallets & Reconciliation, the dedicated Asset Settings PAGE, and the global Back button on both dashboards.
Skipped, with a reason, when no browser is available. Runs against a live Flask server on a throwaway database."""
import os, sys, io, threading, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_admin_ui import SKIP, _browser, CONSENT
if not SKIP:
    from test_crypto import CryptoBase
    from test_vaulta import PW
    from werkzeug.serving import make_server

    def png_bytes(c=(30, 30, 30)):
        from PIL import Image
        b = io.BytesIO(); Image.new('RGB', (96, 96), c).save(b, 'PNG'); return b.getvalue()

    @unittest.skipIf(SKIP, SKIP)
    class UiBase(CryptoBase):
        def setUp(self):
            super().setUp()
            self.srv = make_server('127.0.0.1', 0, self.app, threaded=True); self.base = 'http://127.0.0.1:%d' % self.srv.server_port
            threading.Thread(target=self.srv.serve_forever, daemon=True).start()
            self.staff('super_admin', 'root@vaulta.test'); self.signup('cust@example.com', kyc=True)
            self.ctx = _browser.new_context(viewport={'width': 1440, 'height': 900}); self.ctx.add_cookies([dict(CONSENT, url=self.base)]); self.pg = self.ctx.new_page()
            self.errors = []; self.pg.on('pageerror', lambda e: self.errors.append(str(e)))
            self.btc = self.db.execute("SELECT id FROM crypto_assets WHERE symbol='BTC'").fetchone()[0]
        def tearDown(self):
            self.ctx.close(); self.srv.shutdown(); super().tearDown()
        def login(self, email='root@vaulta.test', w=1440, h=900):
            self.pg.set_viewport_size({'width': w, 'height': h}); self.pg.goto(self.base + '/login/'); self.pg.fill('input[type=email]', email); self.pg.fill('input[type=password]', PW); self.pg.click('form button'); self.pg.wait_for_timeout(1500)
        def hash(self): return self.pg.evaluate('location.hash')
        def admin(self, route, sel='#view .pg > *:not(.sk-wrap)'):
            self.pg.goto(self.base + '/admin/#/' + route); self.pg.wait_for_selector(sel, timeout=15000); self.pg.wait_for_timeout(300)
        def cust(self, route):
            self.pg.goto(self.base + '/app/#/' + route); self.pg.wait_for_selector('#view', timeout=15000); self.pg.wait_for_selector('#vbar', state='attached'); self.pg.wait_for_timeout(1200)
        def overflow(self): return self.pg.evaluate('document.documentElement.scrollWidth-document.documentElement.clientWidth')
        def back(self): self.pg.click('[data-vback]'); self.pg.wait_for_timeout(600)

    @unittest.skipIf(SKIP, SKIP)
    class TestWalletPages(UiBase):
        def test_company_wallets_lists_real_wallets_with_working_search_filter_sort_and_copy(self):
            self.login(); self.admin('settings-crypto'); self.pg.wait_for_selector('tr[data-w]')
            self.assertEqual(self.pg.locator('tr[data-w]').count(), 5); self.assertIn('Wallets & Reconciliation', self.pg.inner_text('h1')); self.assertEqual(self.pg.locator('.wr-card').count(), 4)
            self.pg.fill('#wr-q', 'usdc'); self.pg.wait_for_timeout(400); self.assertEqual(self.pg.locator('tr[data-w]').count(), 1)
            self.pg.fill('#wr-q', self.wal['SOL']['public_address'][:12]); self.pg.wait_for_timeout(400); self.assertEqual(self.pg.locator('tr[data-w]').count(), 1)
            self.pg.fill('#wr-q', ''); self.pg.wait_for_timeout(300); self.pg.click('#wr-ft'); self.pg.select_option('#wr-net', 'ethereum'); self.assertEqual(self.pg.locator('tr[data-w]').count(), 3)
            self.pg.select_option('#wr-st', 'maintenance'); self.assertEqual(self.pg.locator('tr[data-w]').count(), 0); self.assertIn('No wallets match', self.pg.inner_text('.wr-empty')); self.pg.click('#wr-clr'); self.assertEqual(self.pg.locator('tr[data-w]').count(), 5)
            self.pg.click('[data-sort=asset]'); self.assertEqual(self.pg.locator('tr[data-w] .wr-asset b').first.inner_text(), 'USDT')      # descending
            self.pg.click('tr[data-w] >> nth=0 >> [data-view]'); self.assertTrue(self.pg.locator('.wr-detail').count() == 1); self.assertIn('Full wallet address', self.pg.inner_text('.wr-detail'))
            self.ctx.grant_permissions(['clipboard-read', 'clipboard-write'], origin=self.base); self.pg.click('.wr-detail [data-copy]'); self.assertEqual(self.pg.evaluate('navigator.clipboard.readText()'), self.pg.locator('.wr-detail .wr-addr').first.inner_text())
            self.assertEqual(self.errors, [])
        def test_settings_opens_a_dedicated_page_never_a_dialog_and_back_restores_the_filtered_table(self):
            self.login(); self.admin('settings-crypto'); self.pg.wait_for_selector('tr[data-w]'); self.pg.fill('#wr-q', 'eth'); self.pg.wait_for_timeout(400)
            n = self.pg.locator('tr[data-w]').count(); self.assertGreaterEqual(n, 1)
            self.pg.locator('tr[data-w] >> nth=0 >> a.btn:has-text("Settings")').click(); self.pg.wait_for_selector('h1:has-text("Asset Settings")')
            self.assertRegex(self.hash(), r'^#/asset-settings/\d+'); self.assertEqual(self.pg.locator('dialog[open]').count(), 0); self.assertEqual(self.pg.locator('[role=dialog]').count(), 0)
            self.assertIn('Wallets & Reconciliation', self.pg.inner_text('#adm-crumb')); self.assertIn('Asset Settings', self.pg.inner_text('#adm-crumb'))
            self.back(); self.pg.wait_for_selector('tr[data-w]'); self.assertTrue(self.hash().startswith('#/settings-crypto')); self.assertEqual(self.pg.input_value('#wr-q'), 'eth'); self.assertEqual(self.pg.locator('tr[data-w]').count(), n)
        def test_change_address_and_settings_links_open_the_right_tab_in_place(self):
            self.login(); self.admin('settings-crypto'); self.pg.wait_for_selector('tr[data-w]')
            self.pg.locator('tr[data-w] >> nth=0 >> a.btn:has-text("Change address")').click(); self.pg.wait_for_selector('#f-pol'); self.assertIn('tab=wallet', self.hash()); self.pg.wait_for_selector('#chg-host form'); self.assertEqual(self.pg.locator('dialog[open]').count(), 0)
            self.pg.fill('#chg-host input[name=address]', 'not-an-address'); self.pg.fill('#chg-host input[name=code]', '123456'); self.pg.fill('#chg-host textarea[name=reason]', 'rotation for test'); self.pg.check('#chg-host input[name=confirm]'); self.pg.click('#chg-host button[type=submit]')
            self.pg.wait_for_selector('#chg-host .wr-err:not([hidden])'); self.assertIn('valid', self.pg.inner_text('#chg-host .wr-err'))                 # client-side format check: nothing was sent
        def test_asset_settings_tabs_keep_unsaved_values_and_a_guard_protects_them(self):
            self.login(); self.admin(f'asset-settings/{self.btc}'); self.pg.wait_for_selector('#f-gen')
            self.pg.fill('#f-gen [name=name]', 'Bitcoin (edited)'); self.pg.click('#tb-network'); self.assertTrue(self.pg.locator('#pn-general').is_hidden()); self.pg.click('#tb-wallet'); self.pg.click('#tb-general')
            self.assertEqual(self.pg.input_value('#f-gen [name=name]'), 'Bitcoin (edited)')
            msgs = []; self.pg.once('dialog', lambda d: (msgs.append(d.message), d.dismiss())); self.back(); self.assertTrue(msgs and 'unsaved' in msgs[0].lower()); self.assertRegex(self.hash(), r'^#/asset-settings/'); self.assertEqual(self.pg.input_value('#f-gen [name=name]'), 'Bitcoin (edited)')
            self.pg.click('#g-reset'); self.assertEqual(self.pg.input_value('#f-gen [name=name]'), 'Bitcoin')
            self.pg.fill('#f-gen [name=name]', 'X'); self.pg.once('dialog', lambda d: d.accept()); self.back(); self.assertNotRegex(self.hash(), r'^#/asset-settings/')
        def test_general_tab_validates_saves_persists_and_uploads_a_logo_used_by_customers(self):
            self.login(); self.admin(f'asset-settings/{self.btc}'); self.pg.wait_for_selector('#f-gen')
            self.pg.fill('#f-gen [name=name]', ''); self.pg.click('#g-save'); self.assertIn('required', self.pg.inner_text('#f-gen .as-ferr')); self.assertEqual(self.pg.get_attribute('#f-gen [name=name]', 'aria-invalid'), 'true')
            self.pg.fill('#f-gen [name=name]', 'Bitcoin'); self.pg.select_option('#f-gen [name=status]', 'suspended'); self.pg.click('#g-save'); self.assertIn('reason', self.pg.inner_text('#f-gen').lower())
            self.pg.fill('#f-gen [name=reason]', 'scheduled maintenance'); self.pg.fill('#f-gen [name=description]', 'A decentralised digital currency.'); self.assertIn('/600', self.pg.inner_text('#cc-d'))
            self.pg.set_input_files('#lg-file', files=[{'name': 'btc.png', 'mimeType': 'image/png', 'buffer': png_bytes()}]); self.assertIn('not saved yet', self.pg.inner_text('#lg-state'))
            self.pg.click('#g-save'); self.pg.wait_for_selector('#as-sum .st:has-text("Maintenance")')
            self.pg.reload(); self.pg.wait_for_selector('#f-gen'); self.assertEqual(self.pg.input_value('#f-gen [name=description]'), 'A decentralised digital currency.'); self.assertEqual(self.pg.input_value('#f-gen [name=status]'), 'suspended')
            self.assertEqual(self.pg.locator('#as-sum img.alogo').count(), 1)
            c = self.signup('c2@example.com', kyc=True); self.assertNotIn('BTC', [a['symbol'] for a in c.get('/api/crypto/assets').json['assets']])      # Maintenance status closes the asset to customers
            ops = self.staff('super_admin', 'root2@vaulta.test'); logo = [w['logo'] for w in ops.get('/api/admin/crypto/wallets/overview').json['wallets'] if w['asset'] == 'BTC'][0]; self.assertIn('/api/crypto/logos/', logo)   # the uploaded logo is what every wallet row uses
            self.pg.set_input_files('#lg-file', files=[{'name': 'bad.png', 'mimeType': 'image/png', 'buffer': b'x' * (1024 * 1024 + 10)}]); self.assertIn('1 MB', self.pg.inner_text('#f-gen .as-alert'))
            self.pg.set_input_files('#lg-file', files=[{'name': 'bad.gif', 'mimeType': 'image/gif', 'buffer': b'GIF89a'}]); self.assertIn('Unsupported', self.pg.inner_text('#f-gen .as-alert'))
        def test_network_tab_saves_minimum_deposit_that_customers_then_see(self):
            self.login(); self.admin(f'asset-settings/{self.btc}?tab=network'); self.pg.wait_for_selector('.as-nf[data-kind=an]')
            f = '.as-nf[data-kind=an]'; self.pg.fill(f + ' [name=min_deposit]', 'abc'); self.pg.fill(f + ' [name=reason]', 'testing the floor'); self.pg.click(f + ' button[type=submit]'); self.assertIn('number', self.pg.inner_text(f + ' .as-ferr'))
            self.pg.fill(f + ' [name=min_deposit]', '0.25'); self.pg.fill(f + ' [name=confirmations]', '9'); self.pg.click(f + ' button[type=submit]'); self.pg.wait_for_selector('#as-aside :text("0.25")')
            c = self.signup('c3@example.com', kyc=True); btc = [a for a in c.get('/api/crypto/assets').json['assets'] if a['symbol'] == 'BTC'][0]; self.assertEqual((btc['minimum_deposit'], btc['required_confirmations']), ('0.25000000', 9))
            self.pg.fill('.as-nf[data-kind=net] [name=n_explorer]', 'http://insecure.example'); self.pg.fill('.as-nf[data-kind=net] [name=n_reason]', 'explorer link update'); self.pg.click('.as-nf[data-kind=net] button[type=submit]'); self.assertIn('https', self.pg.inner_text('.as-nf[data-kind=net] .as-ferr'))
            self.pg.fill('.as-nf[data-kind=net] [name=n_explorer]', 'https://explorer.example'); self.pg.click('.as-nf[data-kind=net] button[type=submit]'); self.pg.wait_for_selector('#as-aside a[href^="https://explorer"]')
            self.assertIn('cannot be deleted', self.pg.inner_text('.as-use'))
        def test_wallet_tab_shows_qr_that_matches_the_address_and_no_upload_option(self):
            self.login(); self.admin(f'asset-settings/{self.btc}?tab=wallet'); self.pg.wait_for_selector('#qr-img'); addr = self.wal['BTC']['public_address']
            self.assertIn(addr, self.pg.get_attribute('#qr-img', 'alt')); self.assertIn(addr, self.pg.inner_text('#as-qr')); self.assertEqual(self.pg.locator('#as-qr input[type=file]').count(), 0)
            self.assertEqual(self.pg.evaluate("fetch(document.querySelector('#qr-img').src).then(r=>r.headers.get('content-type').split(';')[0])"), 'image/svg+xml')
            self.assertEqual(self.pg.locator('input[name*=key],input[name*=seed],input[name*=private]').count(), 0)
        def test_reconciliation_tab_filters_and_never_offers_amount_editing(self):
            u = self.U(); src = self.verify_sender(u, 'USDT.ETHEREUM'); self.intent(u, 'USDT.ETHEREUM'); self.pay('USDT.ETHEREUM', 100_000_000, src, confs=2); self.scan()
            self.login(); self.admin('settings-crypto?tab=reconciliation'); self.pg.wait_for_selector('.wr-rtbl tbody tr'); self.assertEqual(self.pg.locator('.wr-rtbl tbody tr').count(), 1); self.assertIn('Confirming', self.pg.inner_text('.wr-rtbl'))
            self.pg.click('[data-rs=mismatch]'); self.pg.wait_for_selector('.wr-empty'); self.pg.click('[data-rs=""]'); self.pg.wait_for_selector('.wr-rtbl'); self.pg.select_option('#rq-t', 'withdrawal'); self.pg.wait_for_selector('.wr-empty')
            self.assertEqual(self.pg.locator('.wr-rtbl input, .wr-list input[name*=amount]').count(), 0)

    @unittest.skipIf(SKIP, SKIP)
    class TestBackNavigation(UiBase):
        def test_admin_deep_link_falls_back_to_the_parent_and_never_leaves_the_app(self):
            self.login(); self.pg.goto('about:blank'); self.admin(f'asset-settings/{self.btc}'); self.assertTrue(self.pg.locator('[data-vback]').count() == 1)
            self.back(); self.assertEqual(self.hash(), '#/settings-crypto'); self.assertTrue(self.pg.url.startswith(self.base + '/admin/'))
            self.pg.wait_for_selector('tr[data-w]'); self.back() if self.pg.locator('[data-vback]').count() else None; self.assertTrue(self.pg.url.startswith(self.base + '/admin/'))     # parent -> dashboard, still inside the app
        def test_back_uses_the_real_previous_page_and_survives_refresh(self):
            self.login(); self.admin('users'); self.pg.evaluate("location.hash='#/audit'"); self.pg.wait_for_timeout(500); self.pg.evaluate("location.hash='#/reports'"); self.pg.wait_for_timeout(500)
            self.back(); self.assertEqual(self.hash(), '#/audit'); self.pg.reload(); self.pg.wait_for_selector('[data-vback]'); self.back(); self.assertEqual(self.hash(), '#/users')
        def test_overview_has_no_back_button_when_there_is_nowhere_to_go(self):
            self.login(); self.pg.goto('about:blank'); self.pg.goto(self.base + '/admin/#/overview'); self.pg.wait_for_selector('#view .pg > *:not(.sk-wrap)'); self.assertEqual(self.pg.locator('[data-vback]').count(), 0)
        def test_dashboards_never_show_back_even_with_history(self):
            self.login(); self.admin('users'); self.pg.evaluate("location.hash='#/overview'"); self.pg.wait_for_timeout(600); self.assertEqual(self.pg.locator('[data-vback]').count(), 0)
            self.login('cust@example.com'); self.cust('portfolio'); self.assertEqual(self.pg.locator('[data-vback]').count(), 1); self.pg.evaluate("location.hash='#/dashboard'"); self.pg.wait_for_timeout(800); self.assertEqual(self.pg.locator('[data-vback]').count(), 0)
        def test_every_admin_sidebar_page_has_a_working_top_left_back_button(self):
            self.login(); self.admin('overview'); hrefs = self.pg.eval_on_selector_all('#adm-side a[data-k]', 'els=>[...new Set(els.map(e=>e.getAttribute("href")))]'); self.assertGreater(len(hrefs), 12)
            for h in hrefs:
                if h in ('#/overview',): continue
                self.pg.goto('about:blank'); self.pg.goto(self.base + '/admin/' + h); self.pg.wait_for_selector('[data-vback]', timeout=15000)
                box = self.pg.locator('[data-vback]').bounding_box(); mt = self.pg.locator('#view').bounding_box(); self.assertLess(box['x'], 330, h); self.assertLess(box['y'], mt['y'], h)      # top-left, above the page content
                self.back(); self.assertEqual(self.hash(), '#/overview', h); self.assertTrue(self.pg.url.startswith(self.base + '/admin/'))
        def test_customer_pages_back_button_history_and_parent_fallback(self):
            self.login('cust@example.com'); self.cust('portfolio'); self.assertEqual(self.pg.locator('[data-vback]').count(), 1)
            self.pg.evaluate("location.hash='#/profile'"); self.pg.wait_for_timeout(600); self.pg.evaluate("location.hash='#/profile/security'"); self.pg.wait_for_timeout(600); self.back(); self.assertEqual(self.hash(), '#/profile'); self.back(); self.assertEqual(self.hash(), '#/portfolio')
            self.pg.goto('about:blank'); self.cust('profile/security'); self.back(); self.assertEqual(self.hash(), '#/profile'); self.assertTrue(self.pg.url.startswith(self.base + '/app/'))
            self.assertEqual(self.pg.locator('.ps-back').count(), 0)                    # no duplicate in-page back link
            self.pg.goto('about:blank'); self.cust('dashboard'); self.assertEqual(self.pg.locator('[data-vback]').count(), 0)
        def test_every_customer_page_has_a_back_button_that_goes_to_the_dashboard_or_previous(self):
            self.login('cust@example.com')
            for k in ('portfolio', 'deposit', 'withdraw', 'invest', 'trade', 'savings', 'retirement', 'goals', 'watchlist', 'transactions', 'statements', 'notifications', 'support', 'profile'):
                self.pg.goto('about:blank'); self.cust(k); self.assertEqual(self.pg.locator('[data-vback]').count(), 1, k); self.back(); self.assertEqual(self.hash(), '#/dashboard', k)
        def test_mobile_layouts_do_not_overflow_and_back_is_a_comfortable_touch_target(self):
            self.login(w=390, h=844)
            for route in ('settings-crypto', 'settings-crypto?tab=reconciliation', f'asset-settings/{self.btc}', f'asset-settings/{self.btc}?tab=network', f'asset-settings/{self.btc}?tab=wallet'):
                self.admin(route); self.pg.wait_for_timeout(500); self.assertLessEqual(self.overflow(), 1, route); b = self.pg.locator('[data-vback]').bounding_box(); self.assertGreaterEqual(b['height'], 44, route); self.assertTrue(self.pg.locator('[data-vback]').is_visible())
            self.admin('settings-crypto'); self.pg.wait_for_selector('tr[data-w]'); self.assertEqual(self.pg.evaluate("getComputedStyle(document.querySelector('.wr-tbl thead')).display"), 'none')       # cards, not a squeezed table
            self.assertGreaterEqual(self.pg.locator('tr[data-w] a.btn:has-text("Settings")').first.bounding_box()['height'], 44)
            self.login('cust@example.com', w=390, h=844); self.cust('portfolio'); self.assertLessEqual(self.overflow(), 1); self.assertTrue(self.pg.locator('[data-vback]').is_visible())

    @unittest.skipIf(SKIP, SKIP)
    class TestTabletAndDesktop(UiBase):
        def test_tablet_and_desktop_do_not_overflow_and_desktop_puts_the_panel_beside_the_table(self):
            for w, h in ((768, 1024), (1440, 900)):
                self.login(w=w, h=h)
                for route in ('settings-crypto', 'settings-crypto?tab=reconciliation', f'asset-settings/{self.btc}', f'asset-settings/{self.btc}?tab=wallet'):
                    self.admin(route); self.pg.wait_for_timeout(400); self.assertLessEqual(self.overflow(), 1, (w, route)); self.assertTrue(self.pg.locator('[data-vback]').is_visible(), (w, route))
            self.admin('settings-crypto'); self.pg.wait_for_selector('.wr-aside'); t = self.pg.locator('.wr-list').bounding_box(); a = self.pg.locator('.wr-aside').bounding_box(); self.assertGreater(a['x'], t['x'] + t['width'] - 5)   # beside, not below
            self.admin(f'asset-settings/{self.btc}'); self.pg.wait_for_selector('.as-aside'); m = self.pg.locator('.as-main').bounding_box(); a = self.pg.locator('.as-aside').bounding_box(); self.assertGreater(a['x'], m['x'] + m['width'] - 5)

if __name__ == '__main__': unittest.main()
