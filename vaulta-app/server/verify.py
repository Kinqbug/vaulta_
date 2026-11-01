"""python -m server.verify  -- crypto system verification.

Two kinds of check, both honest:
  * CONFIGURATION checks run against the real database/environment (wallet configured? address valid for its network? provider reachable?).
    A fresh install with NOT_CONFIGURED placeholders therefore FAILS wallet checks, by design: PASS means "genuinely configured".
  * BEHAVIOUR checks run the real deposit/ledger/reconciliation/withdrawal code on a throw-away database and the in-memory mock chain.
    They prove the logic works; they say nothing about your production chain connectivity (that is the provider checks above).
Only public information is printed. Exit status is 0 only if every check passes."""
import os, sys, tempfile, shutil, json
from . import util, db as dbm, rbac, accounting as acc, chain_addresses as ca, crypto_setup as cs, crypto_wallets as cw, crypto_deposits as cd, crypto_withdrawals as cwd
from . import crypto_recon as rc, crypto_ledger as cl, chain_providers as cp, providers as prov, security as sec
from .config import Config, crypto_env_problems
from .util import ApiError

REQUIRED = ('BTC', 'ETH', 'USDT', 'USDC', 'SOL')
SECRET_WORDS = ('private_key', 'seed', 'mnemonic', 'recovery_phrase', 'wallet_password', 'signing_key')
ENV_WORDS = ('PRIVATE_KEY', 'SEED_PHRASE', 'MNEMONIC', 'RECOVERY_PHRASE', 'WALLET_PASSWORD', 'SIGNING_KEY')

class Report:
    def __init__(self): self.rows = []; self.failed = 0; self.out = []
    def head(self, t): self.out.append(f'\n{t}')
    def check(self, label, ok, detail=''):
        self.failed += (not ok); self.out.append(f"  {label} {'.' * max(2, 38 - len(label))} {'PASS' if ok else 'FAIL'}" + (f'   {detail}' if detail and not ok else '')); return ok
    def line(self, t): self.out.append('  ' + t)
    def text(self, title):
        bar = '=' * 44
        return '\n'.join([bar, title, bar, *self.out, '', bar, 'RESULT: ' + ('PASS' if not self.failed else f'FAIL ({self.failed} check(s) failed)'), bar])

def short(a): return a if len(a) <= 16 else a[:8] + '…' + a[-6:]

# ------------------------------------------------------------------ configuration checks
def config_checks(r, db, providers, env, mode):
    r.head('ENVIRONMENT')
    probs = crypto_env_problems(env, mode)
    r.check(f'Environment/provider pairing ({env}/{mode})', not probs, '; '.join(probs))
    if mode == 'mock': r.line('note: mock provider. Provider checks below only prove the in-memory simulator responds, not that a real chain is reachable.')
    bad_env = [k for k in os.environ if any(w in k.upper() for w in ENV_WORDS)]
    r.check('No signing secrets in environment', not bad_env, ', '.join(sorted(bad_env)))
    secret_cols = [f'{t}.{c[1]}' for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() for c in db.execute(f'PRAGMA table_info({t})').fetchall() if any(w in c[1].lower() for w in SECRET_WORDS)]
    r.check('No private-key columns in database', not secret_cols, ', '.join(secret_cols))
    try: cs.startup_checks(db, env, mode); ok, d = True, ''
    except RuntimeError as e: ok, d = False, str(e)
    r.check('Networks match environment', ok, d)
    for sym in REQUIRED:
        ans = [x['id'] for x in db.execute('SELECT an.id FROM asset_networks an JOIN crypto_assets a ON a.id=an.asset_id WHERE a.symbol=? ORDER BY an.id', (sym,))]
        r.head(sym if ans else f'{sym}')
        if not ans: r.check('Asset/network mapping', False, 'asset is not registered (run python -m server.crypto_setup)'); continue
        for an_id in ans:
            info = cw.an_info(db, an_id); w = cw.live_wallet(db, an_id)
            r.line(f"{info['symbol']} on {info['network_name']}" + (f" (contract {short(info['contract_address'])})" if info['contract_address'] else ''))
            r.check('Asset/network mapping', info['an_status'] == 'active' and info['asset_status'] == 'active' and info['network_status'] == 'active' and bool(info['address_format']), 'asset, network or mapping is not active / has no address format')
            ok = bool(w) and cw.is_configured(w) and w['status'] == 'active' and bool(w['is_deposit_enabled'])
            anyw = db.execute("SELECT * FROM company_crypto_wallets WHERE asset_network_id=? AND wallet_type='deposit' AND status<>'retired' ORDER BY id DESC", (an_id,)).fetchone()
            why = 'NOT_CONFIGURED placeholder: the real address has not been supplied yet' if anyw and anyw['public_address'] == ca.PLACEHOLDER else 'wallet exists but is not active with deposits enabled' if anyw else 'no deposit wallet record'
            r.check('Wallet configured', ok, why)
            if not ok: continue
            try: cw.validate_wallet_address(info, w['public_address']); okv, d = True, ''
            except ApiError as e: okv, d = False, e.message
            r.check('Address validation', okv, d)
            if info['contract_address']:
                try: ca.validate(info['address_format'], info['contract_address'], testnet=bool(info['is_testnet']), strict=True); okc, d = True, ''
                except ca.InvalidAddress as e: okc, d = False, str(e)
                r.check('Contract', okc, d)
            try: cw.env_guard(env, info); oke, d = True, ''
            except ApiError as e: oke, d = False, e.message
            r.check('Network vs environment', oke, d)
            try: providers.chain.prepare_network(dict(db.execute('SELECT * FROM blockchain_networks WHERE id=?', (info['network_id'],)).fetchone())); providers.chain.latest_block(info['network_code']); okp, d = True, ''
            except ApiError as e: okp, d = False, e.message
            r.check('Blockchain provider reachable', okp, d)
            try: okw, d = bool(providers.wallets.controls_address(info['network_code'], w['public_address'])), 'custody does not confirm control'
            except ApiError as e: okw, d = False, e.message
            r.check('Custody confirms control of address', okw, d)
            env_addr = (os.environ.get(f'{sym}_WALLET_ADDRESS') or '').strip()
            if env_addr and info['asset_type'] == 'native' and env_addr != w['public_address']: r.line(f"note: {sym}_WALLET_ADDRESS differs from the database; the database wins")
            r.line(f"address {short(w['public_address'])}  min {w.get('minimum_deposit') or info['min_deposit']}  confirmations {cw.effective(db, w)['confirmations']}")

# ------------------------------------------------------------------ behaviour checks on a throw-away database
class _Lab:
    """Isolated database + mock chain used only for behaviour checks."""
    def __init__(self):
        self.tmp = tempfile.mkdtemp(); self.db = dbm.connect(os.path.join(self.tmp, 'verify.db')); dbm.init_schema(self.db); rbac.ensure_rbac(self.db); acc.ensure_chart(self.db)
        for sym, px in (('BTC', '64250'), ('ETH', '3100'), ('USDT', '1.0000'), ('USDC', '1.0000'), ('SOL', '148.20')):      # funding assets need a USD price: deposits convert to Account Balance
            self.db.execute("INSERT OR IGNORE INTO assets(symbol,name,asset_class,last_price,prev_close,created_at,price_updated_at) VALUES(?,?,'Crypto',?,?,?,?)", (sym, sym, px, px, util.now(), util.now()))
        cs.bootstrap(self.db, 'development', environ={}, activate=True)
        self.P = prov.Providers({'PROVIDER_MODE': 'internal', 'CRYPTO_PROVIDER_MODE': 'mock', 'ENV': 'development', 'UPLOAD_DIR': os.path.join(self.tmp, 'u')}); self.chain = self.P.chain.chain
        cs.sync_chain(self.db, self.P.chain); cs.install_dev_demo_wallets(self.db, self.P, 'development'); self.n = 0
    def close(self): self.db.close(); shutil.rmtree(self.tmp, ignore_errors=True)
    def cash(self, u): from . import ledger; return ledger.balance(self.db, ledger.account_for(self.db, u['id'], 'cash')['id'])['posted_cents']      # Account Balance in cents
    def user(self, kyc=True, role=None):
        self.n += 1; c = self.db.execute('INSERT INTO users(public_id,email,password_hash,email_verified,kyc_status,created_at) VALUES(?,?,?,1,?,?)', (f'USR-V{self.n}', f'v{self.n}@verify.test', 'x', 'verified' if kyc else 'unverified', util.now())).lastrowid
        self.db.execute('INSERT INTO user_profiles(user_id,first_name,last_name) VALUES(?,?,?)', (c, 'V', 'U')); self.db.execute('INSERT INTO kyc_profiles(user_id) VALUES(?)', (c,))
        if role: self.db.execute('INSERT INTO user_roles(user_id,role_id) SELECT ?,id FROM roles WHERE name=?', (c, role))
        return {'id': c}
    def src(self, u, code):
        i = cw.an_info(self.db, ledger_code=code); a = cs.demo_address(i['address_format'], f"verify-sender|{u['id']}|{code}", True)
        s = cd.register_source(self.db, u, i['network_code'], a); cd.verify_source_with_proof(self.db, self.P, u, s['id'], cp.mock_sign(a, s['challenge'])); return a
    def pay(self, code, amount, sender, confs, memo=None):
        i = cw.an_info(self.db, ledger_code=code); w = cw.live_wallet(self.db, i['an_id'])
        h = self.chain.fund(i['network_code'], w['public_address'], amount, sender=sender, contract=i['contract_address'], memo=memo); self.chain.mine(i['network_code'], confs); return h

def behaviour_checks(r):
    lab = _Lab(); db = lab.db; c = 'USDT.ETHEREUM'
    try:
        r.head('BEHAVIOUR (isolated database, mock chain)')
        a, b = lab.user(), lab.user(); sa, sb = lab.src(a, c), lab.src(b, c); cd.create_intent(db, lab.P, a, c); cd.create_intent(db, lab.P, b, c)
        lab.pay(c, 100_000_000, sa, 12); lab.pay(c, 100_000_000, sb, 12); stranger = lab.pay(c, 100_000_000, cs.demo_address('evm', 'stranger'), 12); cd.run_once(db, lab.P)
        r.check('Deposit attribution', lab.cash(a) == 10_000 and lab.cash(b) == 10_000 and
                db.execute("SELECT status FROM crypto_deposits WHERE user_id IS NULL").fetchone()['status'] == 'manual_review', 'shared-wallet deposits were not attributed/held correctly')
        for _ in range(3): cd.run_once(db, lab.P)
        r.check('Duplicate protection', lab.cash(a) == 10_000 and db.execute("SELECT COUNT(*) c FROM journal_entries WHERE entry_type='crypto.deposit_credit'").fetchone()['c'] == 2, 'a deposit was credited more than once')
        h = lab.pay('BTC', 1_000_000, lab.src(a, 'BTC'), 2); cd.create_intent(db, lab.P, a, 'BTC'); cd.run_once(db, lab.P); early = lab.cash(a) - 10_000
        lab.chain.mine('bitcoin', 1); cd.run_once(db, lab.P)
        r.check('Confirmation handling', early == 0 and lab.cash(a) - 10_000 == 64_250, 'credited before / not after required confirmations')
        i = cw.an_info(db, ledger_code='ETH'); lab.chain.register('badnet', 'evm')
        from . import assets as reg
        reg.create_network(db, None, 'badnet', 'Unsupported', 'account', 5, is_testnet=True, address_format='evm'); w = cw.live_wallet(db, i['an_id'])
        lab.chain.fund('badnet', w['public_address'], 10**18, sender='0x' + '8' * 40, mine=True); lab.chain.fund('ethereum', cw.live_wallet(db, cw.an_info(db, ledger_code=c)['an_id'])['public_address'], 5, sender='0x' + '8' * 40, contract='0x' + '9' * 40, mine=True)
        lab.chain.mine('ethereum', 20); cd.run_once(db, lab.P); st = {x['status'] for x in db.execute('SELECT status FROM crypto_deposits')}
        r.check('Wrong network / wrong asset protection', {'wrong_network', 'unsupported_asset'} <= st, f'statuses seen: {sorted(st)}')
        g = acc.verify_integrity(db); r.check('Ledger integration (double-entry integrity)', g['ok'] and acc.balance(db, 'CUSTODY_CRYPTO', c) == 300_000_000, 'ledger imbalance')
        run = rc.reconcile(db, lab.P)
        kinds = {e['kind'] for e in rc.list_exceptions(db)}
        r.check('Reconciliation', not ({'ledger_mismatch', 'insolvency', 'customer_balance_mismatch', 'duplicate_credit'} & kinds) and all(x['solvent'] for x in run['summary']['assets']), f'unexpected exceptions: {sorted(kinds)}')
        # withdrawal controls: 2FA, approval by someone else, reserve, completion
        s = sec.new_totp_secret(); db.execute('UPDATE users SET totp_secret=?,totp_enabled=1 WHERE id=?', (s, a['id'])); ctl = []
        try: cwd.request_withdrawal(db, lab.P, b, c, '1', cs.demo_address('evm', 'x'), '000000')
        except ApiError as e: ctl.append(e.code == 'two_factor_setup_required')
        w1 = cwd.request_withdrawal(db, lab.P, a, c, '40', cs.demo_address('evm', 'dest'), sec.totp_code(s))['withdrawal']; ctl.append(lab.cash(a) == 10_000 + 64_250 - 4_000)
        ops = lab.user(role='operations_admin')
        try: cwd.approve(db, a, w1['id']); ctl.append(False)
        except ApiError as e: ctl.append(e.code == 'separation_of_duties')
        cwd.approve(db, ops, w1['id']); cwd.process(db, lab.P, ops, w1['id']); lab.chain.mine('ethereum', 12); cwd.advance(db, lab.P, w1['id']); ctl.append(db.execute('SELECT status FROM crypto_withdrawals').fetchone()['status'] == 'completed')
        r.check('Withdrawal controls', all(ctl) and acc.verify_integrity(db)['ok'], f'control results: {ctl}')
    finally: lab.close()

def main(argv=None):
    env, mode = Config.ENV, Config.CRYPTO_PROVIDER_MODE; r = Report()
    path = Config.DATABASE_PATH
    if not os.path.exists(path): print(f'No database at {path}. Run: python -m server.seed (development) or your migration step, then python -m server.crypto_setup'); return 2
    db = dbm.connect(path); dbm.init_schema(db); rbac.ensure_rbac(db); acc.ensure_chart(db)
    P = prov.Providers({k: getattr(Config, k) for k in dir(Config) if k.isupper()})
    config_checks(r, db, P, env, mode); behaviour_checks(r)
    print(r.text(f'VAULTA CRYPTO SYSTEM VERIFICATION  [{env} / {mode}]'))
    if env != 'production' or mode != 'live': print('\nThis is a TEST configuration. Passing here does not make Vaulta ready for real customer funds.')
    return 1 if r.failed else 0

if __name__ == '__main__': sys.exit(main())
