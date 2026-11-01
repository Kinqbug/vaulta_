"""Production readiness check + demo-data audit.   python -m server.prodcheck [--json] [--demo-report] [--disable-demo-accounts --backup-dir DIR --confirm]

Exit code 0 = no blockers, 1 = blockers found. It only READS, except --disable-demo-accounts, which (after a verified database backup and an
explicit --confirm) closes accounts flagged is_demo=1. It never deletes: ledger entries, audit trails and customer records are retained.
"""
import os, sys, json, sqlite3, argparse, shutil
from .config import Config
from . import db as dbm, util, secretbox, migrate
from .audit import audit

def _count(c, sql, *a):
    try: return c.execute(sql, a).fetchone()[0]
    except Exception: return None

def demo_report(c):
    """Dry-run classification. Provenance: users.is_demo, rows flagged non_live, assets served by the internal provider, seeded address patterns."""
    r = {'demo_users': _count(c, 'SELECT COUNT(*) FROM users WHERE is_demo=1'),
         'seed_pattern_accounts': _count(c, "SELECT COUNT(*) FROM users WHERE email LIKE '%@vaulta.test' OR email LIKE '%@demo.vaulta.test'"),
         'internal_provider_assets': _count(c, "SELECT COUNT(*) FROM assets WHERE provider='internal'"),
         'demo_quote_assets': _count(c, "SELECT COUNT(*) FROM assets WHERE name LIKE '%(demo quote)%'")}
    for t in ('deposits', 'withdrawals', 'transactions', 'investments', 'orders'): r[f'non_live_{t}'] = _count(c, f'SELECT COUNT(*) FROM {t} WHERE non_live=1')
    r['demo_user_ids'] = [x[0] for x in c.execute('SELECT public_id FROM users WHERE is_demo=1 LIMIT 50')] if r['demo_users'] else []
    r['note'] = 'Dry run. Nothing was changed. Ledger entries, audit logs and customer records are never deleted by this tool.'
    return r

def checks():
    out = []; add = lambda name, status, detail='': out.append({'check': name, 'status': status, 'detail': detail})
    try: Config.validate(); add('Environment validation', 'pass', f'APP_ENV={Config.ENV}')
    except RuntimeError as e: add('Environment validation', 'blocker', str(e))
    prod = Config.ENV == 'production'
    if not os.path.exists(Config.DATABASE_PATH): add('Database', 'blocker' if prod else 'warn', f'no database at {Config.DATABASE_PATH}'); return out
    c = dbm.connect(Config.DATABASE_PATH)
    pend = [v for v, n, s, k in migrate.discover(migrate.DIR) if not c.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone() or not c.execute('SELECT 1 FROM schema_migrations WHERE version=?', (v,)).fetchone()]
    add('Migrations', 'blocker' if pend else 'pass', ('pending: ' + ', '.join(pend)) if pend else 'all applied')
    if pend: return out
    dr = demo_report(c)
    add('Demo accounts', 'blocker' if dr['demo_users'] else 'pass', f"{dr['demo_users']} flagged is_demo; {dr['seed_pattern_accounts']} with seed-pattern emails")
    add('Simulated activity', 'blocker' if any(dr[k] for k in dr if k.startswith('non_live_')) else 'pass', ', '.join(f'{k[9:]}={dr[k]}' for k in dr if k.startswith('non_live_') and dr[k]) or 'none flagged non_live')
    add('Internal-price assets', 'blocker' if dr['internal_provider_assets'] else 'pass', f"{dr['internal_provider_assets']} asset(s) priced by the internal simulator ({dr['demo_quote_assets']} labelled demo quote)")
    add('Administrator', 'pass' if _count(c, "SELECT COUNT(*) FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE r.name='super_admin'") else 'blocker', 'a super administrator exists' if _count(c, "SELECT COUNT(*) FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE r.name='super_admin'") else 'none: run python -m server.bootstrap_admin')
    add('Real-funds gate', 'warn' if Config.REAL_FUNDS_ENABLED else 'pass', 'REAL_FUNDS_ENABLED=1: confirm licensing, custody and compliance sign-off' if Config.REAL_FUNDS_ENABLED else 'REAL_FUNDS_ENABLED=0 (gate closed)')
    add('Settings encryption key', 'pass' if secretbox.available() else ('blocker' if Config.EMAIL_PROVIDER == 'smtp' else 'warn'), 'SETTINGS_ENCRYPTION_KEY set' if secretbox.available() else 'SETTINGS_ENCRYPTION_KEY missing: UI-entered secrets cannot be stored')
    prov = Config.EMAIL_PROVIDER
    add('Email transport', 'pass' if prov in ('smtp', 'resend', 'ses') else 'blocker' if prod else 'warn', f'EMAIL_PROVIDER={prov or "unset"}')
    if prov == 'smtp':
        s = c.execute('SELECT * FROM email_smtp_settings WHERE id=1').fetchone()
        add('SMTP settings', 'pass' if s and s['enabled'] and s['host'] and s['password_enc'] is not None or (s and s['enabled'] and s['auth_method'] == 'none') else 'blocker', 'configured and enabled' if s and s['enabled'] else 'not configured/enabled in Admin > Email Center > Email & SMTP')
        add('SMTP connection test', 'pass' if s and s['last_test_ok'] else 'blocker' if prod else 'warn', 'last test succeeded' if s and s['last_test_ok'] else 'no successful connection test recorded')
    if prov == 'resend': add('Delivery webhooks', 'pass' if Config.EMAIL_WEBHOOK_SECRET else 'warn', 'secret set' if Config.EMAIL_WEBHOOK_SECRET else 'delivery cannot be confirmed without EMAIL_WEBHOOK_SECRET')
    add('Market data key', 'pass' if os.getenv('COINGECKO_API_KEY') else 'warn', 'COINGECKO_API_KEY set' if os.getenv('COINGECKO_API_KEY') else 'keyless public tier only: heavily rate-limited')
    active = _count(c, "SELECT COUNT(*) FROM company_crypto_wallets WHERE status='active' AND public_address NOT LIKE 'NOT_CONFIGURED%'")
    add('Company deposit wallets', 'pass' if active else 'warn', f'{active} active wallet(s) with a configured address' if active else 'none configured: crypto deposits will show as unavailable')
    add('Upload directory', 'pass' if os.path.isdir(Config.UPLOAD_DIR) and not os.path.realpath(Config.UPLOAD_DIR).startswith(os.path.realpath(Config.WEB_ROOT)) else 'blocker', 'outside the web root' if os.path.isdir(Config.UPLOAD_DIR) else 'missing')
    add('Database engine', 'blocker' if prod else 'warn', 'SQLite in use. The app is SQLite-only in this build; PostgreSQL support is NOT implemented (see docs/PRODUCTION.md).')
    c.close(); return out

def disable_demo(backup_dir):
    os.makedirs(backup_dir, exist_ok=True); dst = os.path.join(backup_dir, 'vaulta-before-demo-cleanup-' + util.now().replace(':', '') + '.db')
    src = sqlite3.connect(Config.DATABASE_PATH); bk = sqlite3.connect(dst); src.backup(bk); ok = bk.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'; bk.close(); src.close()
    if not ok: print('Backup failed integrity check; aborting.'); return 1
    c = dbm.connect(Config.DATABASE_PATH)
    with dbm.tx(c):
        n = c.execute("UPDATE users SET status='closed' WHERE is_demo=1 AND status<>'closed'").rowcount
        c.execute('DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE is_demo=1)')
        audit(c, None, 'demo.accounts_closed', 'system', None, None, {'closed': n, 'backup': os.path.basename(dst)}, 'prodcheck cli')
    print(f'Backup written to {dst} (integrity ok). Closed {n} demo account(s); no records were deleted.'); return 0

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument('--json', action='store_true'); ap.add_argument('--demo-report', action='store_true')
    ap.add_argument('--disable-demo-accounts', action='store_true'); ap.add_argument('--backup-dir'); ap.add_argument('--confirm', action='store_true'); a = ap.parse_args(argv)
    if a.demo_report:
        c = dbm.connect(Config.DATABASE_PATH); print(json.dumps(demo_report(c), indent=2)); return 0
    if a.disable_demo_accounts:
        if not (a.confirm and a.backup_dir): print('Refusing: pass --backup-dir DIR and --confirm (review --demo-report first).'); return 2
        return disable_demo(a.backup_dir)
    res = checks()
    if a.json: print(json.dumps(res, indent=2))
    else:
        for r in res: print(f"[{r['status'].upper():7}] {r['check']}: {r['detail']}")
    b = [r for r in res if r['status'] == 'blocker']; print(f"\n{len(b)} blocker(s)." if not a.json else '', end='\n' if not a.json else '')
    return 1 if b else 0

if __name__ == '__main__': sys.exit(main())
