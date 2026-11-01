"""Create the FIRST administrator on a fresh production (or staging) database. No demo data, no default credentials.

    python -m server.bootstrap_admin --email you@company.com
The password is read from a hidden prompt (or BOOTSTRAP_ADMIN_PASSWORD for automation; it is never echoed or logged), must satisfy the
password policy, and the command refuses to run if a super administrator already exists. The account is created with email_verified=0 so the
owner must verify the address, and the action is written to the audit log. Enable 2FA from the dashboard immediately after the first sign-in.
"""
import sys, os, getpass, argparse, secrets
from .config import Config
from . import db as dbm, util, rbac, security as sec
from .audit import audit
from .mailer import prefs

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0]); ap.add_argument('--email', required=True); a = ap.parse_args(argv)
    email = a.email.strip().lower()
    if not prefs.valid_address(email): print('Invalid email address.'); return 2
    c = dbm.connect(Config.DATABASE_PATH); dbm.init_schema(c); rbac.ensure_rbac(c)
    if c.execute("SELECT 1 FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE r.name='super_admin'").fetchone():
        print('A super administrator already exists. Refusing: use the dashboard (Roles & Permissions) to add staff.'); return 1
    if c.execute('SELECT 1 FROM users WHERE email=?', (email,)).fetchone(): print('That email already has an account.'); return 1
    pw = os.getenv('BOOTSTRAP_ADMIN_PASSWORD') or getpass.getpass('Choose a password: ')
    if not os.getenv('BOOTSTRAP_ADMIN_PASSWORD') and getpass.getpass('Repeat password: ') != pw: print('Passwords do not match.'); return 2
    try: sec.check_password_policy(pw, email)
    except util.ApiError as e: print('Password rejected: ' + e.message); return 2
    t = util.now()
    with dbm.tx(c):
        uid = c.execute('INSERT INTO users(public_id,email,password_hash,email_verified,kyc_status,is_demo,created_at) VALUES(?,?,?,?,?,0,?)', ('USR-' + secrets.token_hex(5).upper(), email, sec.hash_password(pw), 0, 'none', t)).lastrowid
        c.execute('INSERT INTO user_profiles(user_id,first_name,last_name) VALUES(?,?,?)', (uid, 'Administrator', ''))
        c.execute('INSERT INTO portfolios(user_id,created_at) VALUES(?,?)', (uid, t)); c.execute('INSERT INTO kyc_profiles(user_id) VALUES(?)', (uid,))
        c.execute('INSERT INTO user_roles(user_id,role_id,granted_at) SELECT ?,id,? FROM roles WHERE name=?', (uid, t, 'super_admin'))
        audit(c, None, 'admin.bootstrap', 'user', uid, None, {'email_domain': email.split('@')[1]}, 'cli bootstrap')
    print(f'Administrator {email} created. Verify the email address, sign in, and enable two-factor authentication.'); return 0

if __name__ == '__main__': sys.exit(main())
