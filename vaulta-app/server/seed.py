"""Development seed. Creates clearly fictional demo data by driving the REAL services (with a back-dated clock),
so seeded balances, holdings and statements are produced by the same ledger code as production.
Credentials are generated randomly (or taken from env) and written to instance/dev-credentials.txt (chmod 600)."""
import os, sys, math, random, secrets, base64
from datetime import timedelta, datetime, timezone
from . import util, db as dbm, rbac, security as sec, ledger, finance as fin, portfolio as pf
from .audit import audit
from .config import Config

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')
ASSETS = [  # symbol,name,class,price,daily_vol,drift_per_day
 ('VGTF', 'Global Tech Fund', 'ETFs', '118.42', .011, .0006), ('VSGP', 'Sustainable Growth Portfolio', 'ETFs', '104.77', .008, .0004),
 ('VDAF', 'Digital Assets Fund', 'Crypto', '71.30', .028, .0008), ('VBCL', 'Blue Chip Leaders', 'Stocks', '96.15', .009, .0004),
 ('VGRI', 'Growth Innovators', 'Stocks', '88.60', .014, .0007), ('VTRL', 'Treasury Ladder', 'Bonds', '99.42', .0012, .00012),
 ('VCIB', 'Corporate Income Bonds', 'Bonds', '101.18', .002, .00016), ('VPPT', 'Prime Property Trust', 'Real Estate', '52.64', .005, .0003),
 ('VGLD', 'Gold Reserve', 'Commodities', '64.90', .008, .0003),
 ('AAPL', 'Apple Inc. (demo quote)', 'Stocks', '228.40', .014, .0005), ('NVDA', 'NVIDIA Corp. (demo quote)', 'Stocks', '138.20', .024, .0011),
 ('MSFT', 'Microsoft Corp. (demo quote)', 'Stocks', '421.10', .012, .0005), ('BTC', 'Bitcoin (demo quote)', 'Crypto', '64250', .028, .0007), ('ETH', 'Ethereum (demo quote)', 'Crypto', '3180', .033, .0006),
 ('USDT', 'Tether (demo quote)', 'Crypto', '1.0000', .0004, 0), ('USDC', 'USD Coin (demo quote)', 'Crypto', '1.0000', .0004, 0), ('SOL', 'Solana (demo quote)', 'Crypto', '148.20', .035, .0006)]
PRODUCTS = [  # slug,name,symbol,category,kind,issuer,risk,min,ret,horizon,fee,score,description
 ('global-tech-fund', 'Global Tech Fund', 'VGTF', 'ETFs', 'ETF', 'VAULTA Funds', 'Moderate', 100, 12.8, 'Long', .005, 9, 'Diversified exposure to leading global technology companies.'),
 ('sustainable-growth', 'Sustainable Growth Portfolio', 'VSGP', 'ETFs', 'Managed Portfolio', 'VAULTA Managed', 'Moderate', 250, 9.4, 'Long', .0075, 8, 'A managed mix of companies screened for sustainability practices.'),
 ('digital-assets-fund', 'Digital Assets Fund', 'VDAF', 'Crypto', 'Crypto', 'VAULTA Digital', 'High', 50, 27.6, 'Medium', .015, 7, 'A basket of selected digital assets. Prices can swing sharply.'),
 ('blue-chip-leaders', 'Blue Chip Leaders', 'VBCL', 'Stocks', 'Stock basket', 'Large-cap US companies', 'Moderate', 25, 10.2, 'Long', .003, 8, 'Established large companies with long operating histories.'),
 ('growth-innovators', 'Growth Innovators', 'VGRI', 'Stocks', 'Stock basket', 'Mid-cap growth companies', 'High', 50, 18.9, 'Medium', .004, 6, 'Faster-growing companies with larger potential price swings.'),
 ('treasury-ladder', 'Treasury Ladder', 'VTRL', 'Bonds', 'Bond', 'Government securities', 'Low', 500, 4.1, 'Short', .002, 7, 'Short-dated government bonds aimed at steadier income.'),
 ('corporate-income', 'Corporate Income Bonds', 'VCIB', 'Bonds', 'Bond', 'Investment-grade issuers', 'Low', 250, 5.1, 'Medium', .003, 6, 'Investment-grade corporate bonds that pay regular interest.'),
 ('prime-property', 'Prime Property Trust', 'VPPT', 'Real Estate', 'Real estate', 'Commercial property portfolio', 'Moderate', 500, 7.3, 'Long', .01, 7, 'Property-backed exposure to office and logistics assets.'),
 ('gold-reserve', 'Gold Reserve', 'VGLD', 'Commodities', 'Commodity', 'Physical gold exposure', 'Moderate', 100, 6.2, 'Medium', .004, 6, 'Exposure to gold as a potential portfolio diversifier.')]
ARTICLES = [
 ('beginner-s-guide-to-investing', "Beginner's Guide to Investing", 'guide', 'Start with a goal, a time horizon and an emergency fund, then choose diversified, low-cost options.', 'Before investing, decide what the money is for and when you will need it. Build an emergency fund first, then favour diversified, low-cost options. All investing involves risk, including the possible loss of principal. This is general education, not personal advice.'),
 ('understanding-bonds', 'Understanding Bonds', 'guide', 'A bond is a loan to a government or company that pays interest and returns principal at maturity, if the issuer does not default.', 'Bond prices tend to fall when interest rates rise, and issuers can miss payments, so credit quality matters. Past performance does not guarantee future results.'),
 ('understanding-diversification', 'Understanding Diversification', 'guide', 'Spreading money across different assets can reduce the impact of any single holding.', 'Diversification does not remove risk or guarantee a profit, but it can reduce reliance on any one investment.'),
 ('retirement-planning-basics', 'Retirement Planning Basics', 'guide', 'Time, regular contributions and realistic assumptions matter more than perfect timing.', 'Projections are estimates based on the assumptions you enter. They are not guarantees and real returns can be lower, or negative.'),
 ('how-savings-accounts-work', 'How Savings Accounts Work', 'guide', 'Savings accounts pay variable interest on your balance.', 'Rates can change. VAULTA savings APY is variable and not guaranteed.'),
 ('building-an-emergency-fund', 'Building an Emergency Fund', 'guide', 'A cash cushion keeps you from selling investments at a bad time.', 'Many people aim for three to six months of essential expenses held in an accessible account.')]

def _walk(rnd, n, vol, drift):
    p, out = 100.0, []
    for _ in range(n): p *= math.exp(drift - vol * vol / 2 + vol * rnd.gauss(0, 1)); out.append(p)
    return out

def seed_market(db, rnd, now):
    daily_n, hourly_n = 1826, 72
    for sym, name, cls, price, vol, drift in ASSETS:
        series = _walk(rnd, daily_n + hourly_n, vol, drift); k = float(price) / series[-1]
        pts = []
        for i, v in enumerate(series):
            ts = (now - timedelta(days=(daily_n - i + hourly_n // 24))).replace(hour=16, minute=0, second=0) if i < daily_n else now - timedelta(hours=(daily_n + hourly_n - 1 - i))
            pts.append((ts.strftime('%Y-%m-%dT%H:00:00Z') if i >= daily_n else ts.strftime('%Y-%m-%dT16:00:00Z'), v * k))
        dec = 4 if float(price) < 1000 else 2
        cur = db.execute('INSERT INTO assets(symbol,name,asset_class,provider,external_id,last_price,prev_close,created_at,price_updated_at) VALUES(?,?,?,?,?,?,?,?,?)', (sym, name, cls, 'internal', f'int:{sym}', price, price, util.now(), util.now()))
        db.executemany('INSERT OR REPLACE INTO asset_prices(asset_id,ts,price) VALUES(?,?,?)', [(cur.lastrowid, t, f'{v:.{dec}f}') for t, v in pts])
        db.execute('UPDATE assets SET last_price=?,prev_close=? WHERE id=?', (f'{pts[-1][1]:.{dec}f}', f'{pts[-25][1]:.{dec}f}', cur.lastrowid))
    for slug, name, sym, cat, kind, issuer, risk, mn, ret, hor, fee, score, desc in PRODUCTS:
        aid = db.execute('SELECT id FROM assets WHERE symbol=?', (sym,)).fetchone()[0]
        added = (now - timedelta(days=rnd.randint(10, 90))).strftime('%Y-%m-%dT%H:%M:%SZ')
        db.execute('INSERT INTO investment_products(slug,name,asset_id,category,kind,issuer,risk,min_investment_cents,fee_bps,illustrative_return_pct,horizon,description,score,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (slug, name, aid, cat, kind, issuer, risk, mn * 100, int(round(fee * 10000)), str(ret), hor, desc, score, added, added))

def asof(db, t):
    db.execute('UPDATE assets SET last_price=COALESCE((SELECT price FROM asset_prices p WHERE p.asset_id=assets.id AND p.ts<=? ORDER BY ts DESC LIMIT 1),last_price)', (t.strftime('%Y-%m-%dT%H:%M:%SZ'),))

def mkuser(db, email, first, last, pwhash, *, days_ago, verified=True, kyc='verified', demo=1):
    t = util.now()
    cur = db.execute('INSERT INTO users(public_id,email,password_hash,email_verified,kyc_status,is_demo,created_at,last_login_at) VALUES(?,?,?,?,?,?,?,?)', ('USR-' + secrets.token_hex(5).upper(), email, pwhash, 1 if verified else 0, kyc, demo, t, t))
    uid = cur.lastrowid
    db.execute('INSERT INTO user_profiles(user_id,first_name,last_name,country,risk_tolerance,investment_goal,investment_horizon) VALUES(?,?,?,?,?,?,?)', (uid, first, last, 'United States', 'Moderate', 'Long-term growth', 'Long'))
    db.execute('INSERT INTO portfolios(user_id,created_at) VALUES(?,?)', (uid, t)); db.execute('INSERT INTO kyc_profiles(user_id,status,legal_name,date_of_birth,country,address,reviewed_at) VALUES(?,?,?,?,?,?,?)', (uid, kyc, f'{first} {last}', '1990-04-12', 'United States', '1 Example Street, Testville', t if kyc == 'verified' else None))
    ledger.account_for(db, uid, 'cash'); ledger.account_for(db, uid, 'savings')
    if kyc == 'verified': db.execute('INSERT INTO kyc_reviews(user_id,decision,reason,created_at) VALUES(?,?,?,?)', (uid, 'verified', 'Seed data', t))
    return dict(db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone())

def seed(db, providers, reset_creds=True):
    if Config.ENV != 'development': raise SystemExit(f'Refusing to run the development seed in APP_ENV={Config.ENV}: it creates fictional customers, balances and prices. Use `python -m server.bootstrap_admin` for the first administrator.')
    if db.execute('SELECT COUNT(*) FROM users').fetchone()[0]: print('Database already has users; skipping seed (delete instance/vaulta.db to reseed).'); return
    rnd = random.Random(20260930); base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    admin_pw = os.getenv('ADMIN_PASSWORD') or secrets.token_urlsafe(14) + '9a'; demo_pw = os.getenv('DEMO_USER_PASSWORD') or secrets.token_urlsafe(12) + '7b'
    admin_email = os.getenv('ADMIN_EMAIL', 'admin@vaulta.test')
    ah, dh = sec.hash_password(admin_pw), sec.hash_password(demo_pw)
    util.set_clock(lambda: base - timedelta(days=1830)); rbac.ensure_rbac(db); seed_market(db, rnd, base)
    cur = lambda d, h=10: (lambda: base - timedelta(days=d, hours=-h + 12))
    def at(days_ago, hour=10): util.set_clock(lambda: base - timedelta(days=days_ago) + timedelta(hours=hour - base.hour)); asof(db, util.utcnow())

    # ---- staff (development only) ----
    at(200); staff = {}
    for role, email in (('super_admin', admin_email), ('operations_admin', 'ops@vaulta.test'), ('compliance_admin', 'compliance@vaulta.test'), ('investment_admin', 'invest@vaulta.test'),
                        ('finance_admin', 'finance@vaulta.test'), ('support_admin', 'support@vaulta.test'), ('content_admin', 'content@vaulta.test')):
        u = mkuser(db, email, role.split('_')[0].title(), 'Staff', ah, days_ago=200, demo=0); staff[role] = u
        db.execute('INSERT INTO user_roles(user_id,role_id,granted_by,granted_at) SELECT ?,id,?,? FROM roles WHERE name=?', (u['id'], staff['super_admin']['id'], util.now(), role))
    audit(db, staff['super_admin']['id'], 'seed.staff_created', 'system', None, None, {'roles': list(staff)}, 'Development seed')

    def user_script(email, first, last, start, kyc='verified', verified=True):
        at(start); return mkuser(db, email, first, last, dh, days_ago=start, verified=verified, kyc=kyc)
    def dep(u, d, amt, method='bank_transfer'):
        at(d); r = fin.create_deposit(db, providers, u, util.parse_amount(amt), method); fin.handle_payment_event(db, providers.payments.internal_event(r['deposit']['external_ref']))
    def inv(u, d, slug, amt):
        at(d); pid = db.execute('SELECT id FROM investment_products WHERE slug=?', (slug,)).fetchone()[0]; fin.create_investment(db, providers, u, pid, util.parse_amount(amt), True)
    def order(u, d, sym, side, qty, typ='market', **kw):
        at(d); return fin.place_order(db, providers, u, sym, side, typ, util.parse_qty(qty), **kw)

    # ---- the main demo customer ----
    amara = user_script('demo@vaulta.test', 'Amara', 'Demo', 150)
    dep(amara, 148, '25000.00'); inv(amara, 140, 'global-tech-fund', '4000.00'); inv(amara, 128, 'treasury-ladder', '3000.00'); inv(amara, 110, 'blue-chip-leaders', '2500.00')
    order(amara, 96, 'AAPL', 'buy', '12'); order(amara, 80, 'BTC', 'buy', '0.05'); dep(amara, 75, '12000.00', 'card'); inv(amara, 60, 'sustainable-growth', '2000.00')
    inv(amara, 45, 'digital-assets-fund', '800.00'); inv(amara, 33, 'prime-property', '1500.00'); inv(amara, 21, 'gold-reserve', '1000.00'); order(amara, 14, 'NVDA', 'buy', '20')
    order(amara, 9, 'AAPL', 'sell', '4'); at(100); fin.savings_transfer(db, amara, 'to_savings', util.parse_amount('3000.00'))
    at(70); fin.create_goal(db, amara, 'Emergency Fund', 'Emergency Fund', 1000000, (base + timedelta(days=300)).strftime('%Y-%m-%d'), 'monthly', 25000)
    at(60); fin.create_goal(db, amara, 'New Home', 'New Home', 6000000, (base + timedelta(days=1400)).strftime('%Y-%m-%d'))
    at(50); fin.create_goal(db, amara, 'Vacation', 'Vacation', 400000, (base + timedelta(days=200)).strftime('%Y-%m-%d'), 'weekly', 5000)
    for gid, amt in ((1, '1200.00'), (2, '2500.00'), (3, '600.00')): at(30); fin.contribute_goal(db, amara, gid, util.parse_amount(amt))
    for d in range(60, 0, -1): at(d); fin.accrue_interest(db, util.now()[:10])
    at(40); fin.save_retirement(db, amara['id'], {'current_age': 34, 'retirement_age': 65, 'current_savings': '42000', 'monthly_contribution': '650', 'employer_match_pct': '50', 'expected_return_pct': '6.5', 'target': '1500000'})
    at(20); order(amara, 3, 'MSFT', 'buy', '2', 'limit', limit=str(round(float(db.execute("SELECT last_price FROM assets WHERE symbol='MSFT'").fetchone()[0]) * 0.9, 2)))   # stays open
    at(5)
    for s in ('AAPL', 'BTC', 'NVDA', 'ETH', 'VGTF'): db.execute('INSERT OR IGNORE INTO watchlist_items(user_id,asset_id,added_at) SELECT ?,id,? FROM assets WHERE symbol=?', (amara['id'], util.now(), s))
    db.execute('UPDATE users SET withdrawal_2fa=0 WHERE is_demo=1')   # demo customers have no authenticator; real users must enable 2FA to withdraw
    at(4); fin.request_withdrawal(db, providers, amara, util.parse_amount('1200.00'), 'bank_transfer', 'Checking •••• 4417 (demo)')
    at(2); r = fin.create_deposit(db, providers, amara, util.parse_amount('750.00'), 'card')    # left processing for the admin queue
    at(3); t = util.now(); cur_ = db.execute("INSERT INTO support_tickets(ref,user_id,category,subject,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", ('TKT-DEMO0001', amara['id'], 'withdrawal', 'When will my withdrawal arrive?', 'open', t, t))
    db.execute('INSERT INTO support_messages(ticket_id,author_id,body,created_at) VALUES(?,?,?,?)', (cur_.lastrowid, amara['id'], 'I requested a withdrawal on Monday and it is still pending. Is there anything I need to do?', t))

    # ---- other customers (admin has real queues to work) ----
    pend = user_script('liam@demo.vaulta.test', 'Liam', 'Sample', 12, kyc='under_review', verified=True)
    at(11); db.execute("UPDATE kyc_profiles SET status='under_review',submitted_at=?,legal_name='Liam Sample' WHERE user_id=?", (util.now(), pend['id']))
    name, mime, digest = providers.storage.save(PNG); db.execute('INSERT INTO kyc_documents(user_id,doc_type,stored_name,original_name,mime,size,sha256,created_at) VALUES(?,?,?,?,?,?,?,?)', (pend['id'], 'government_id', name, 'id-front.png', mime, len(PNG), digest, util.now()))
    priya = user_script('priya@demo.vaulta.test', 'Priya', 'Demo', 70); dep(priya, 68, '15000.00'); inv(priya, 60, 'corporate-income', '2500.00'); inv(priya, 50, 'blue-chip-leaders', '1500.00'); order(priya, 25, 'ETH', 'buy', '0.8')
    db.execute('UPDATE users SET withdrawal_2fa=0 WHERE is_demo=1')
    at(6); fin.request_withdrawal(db, providers, priya, util.parse_amount('6000.00'), 'bank_transfer', 'Savings •••• 9021 (demo)')    # large -> held for review
    carlos = user_script('carlos@demo.vaulta.test', 'Carlos', 'Example', 40); dep(carlos, 38, '3000.00', 'card'); inv(carlos, 30, 'treasury-ladder', '1000.00'); at(20); fin.savings_transfer(db, carlos, 'to_savings', util.parse_amount('800.00'))
    at(8); db.execute("INSERT INTO support_tickets(ref,user_id,category,subject,status,priority,created_at,updated_at) VALUES('TKT-DEMO0002',?,'kyc','How long does verification take?','open','normal',?,?)", (carlos['id'], util.now(), util.now()))
    db.execute("INSERT INTO support_messages(ticket_id,author_id,body,created_at) VALUES((SELECT id FROM support_tickets WHERE ref='TKT-DEMO0002'),?,?,?)", (carlos['id'], 'Hi, I uploaded my ID yesterday. When should I expect a decision?', util.now()))
    for i in range(1, 13):    # filler customers so admin charts show a realistic spread (all fictional)
        d = rnd.randint(2, 29); u = user_script(f'user{i:02d}@demo.vaulta.test', f'Test{i:02d}', 'Customer', d); dep(u, d - 1 if d > 1 else 1, f'{rnd.choice([1500, 2500, 4000])}.00')
        if rnd.random() < .7: inv(u, max(d - 2, 0.5), rnd.choice([p[0] for p in PRODUCTS]), f'{rnd.choice([500, 750])}.00')
        for k in range(rnd.randint(1, 6)): db.execute("INSERT INTO security_events(user_id,type,ip,created_at) VALUES(?,?,?,?)", (u['id'], 'login_success', '203.0.113.7', (base - timedelta(days=rnd.randint(0, d))).strftime('%Y-%m-%dT%H:%M:%SZ')))
    for u in (amara, priya, carlos):
        for k in range(14): db.execute("INSERT INTO security_events(user_id,type,ip,created_at) VALUES(?,?,?,?)", (u['id'], 'login_success', '203.0.113.7', (base - timedelta(days=rnd.randint(0, 29))).strftime('%Y-%m-%dT%H:%M:%SZ')))

    # ---- content ----
    at(30)
    for slug, title, cat, summ, body in ARTICLES: db.execute("INSERT INTO articles(slug,title,category,summary,body,status,publish_at,author_id,created_at,updated_at) VALUES(?,?,?,?,?,'published',?,?,?,?)", (slug, title, cat, summ, body, util.now(), staff['content_admin']['id'], util.now(), util.now()))
    db.execute("INSERT INTO articles(slug,title,category,summary,body,status,author_id,created_at,updated_at) VALUES('q4-market-outlook-draft','Q4 market outlook (draft)','market_insight','Draft for review','Draft body.','draft',?,?,?)", (staff['content_admin']['id'], util.now(), util.now()))

    # ---- finalise: back to real time, price rollover, 90-day snapshots ----
    util.set_clock(None); asof(db, base); db.execute("UPDATE assets SET prev_close=COALESCE((SELECT price FROM asset_prices p WHERE p.asset_id=assets.id AND p.ts<=? ORDER BY ts DESC LIMIT 1),prev_close)", ((base - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ'),))
    for d in range(90, -1, -1): pf.snapshot_all(db, (base - timedelta(days=d)).strftime('%Y-%m-%d'))
    from . import crypto_setup      # registry for BTC/ETH/USDT/USDC/SOL with NOT_CONFIGURED wallets (nothing is invented; see README to attach dev mock wallets)
    crypto_setup.bootstrap(db, Config.ENV, activate=(Config.ENV == 'development'))
    path = os.path.join(os.path.dirname(Config.DATABASE_PATH), 'dev-credentials.txt')
    with open(path, 'w') as f:
        f.write(f"VAULTA development credentials (generated {util.now()}). DO NOT COMMIT. Development data only.\n\nSuper admin : {admin_email} / {admin_pw}\nOther staff (same password): ops@, compliance@, invest@, finance@, support@, content@ vaulta.test\nDemo customer: demo@vaulta.test / {demo_pw}\nOther demo customers (same password): liam@, priya@, carlos@, user01..user12@ demo.vaulta.test\n")
    os.chmod(path, 0o600)
    print(f'Seeded. Credentials written to {path}')

if __name__ == '__main__':
    from .app import create_app
    app = create_app()
    with app.app_context():
        c = dbm.connect(app.config['DATABASE_PATH']); seed(c, app.providers)
