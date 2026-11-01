"""Creates the initial crypto configuration (BTC, ETH, USDT, USDC, SOL) as DATA in the registry. Run once per environment:

    python -m server.crypto_setup                 # registry + NOT_CONFIGURED placeholder wallets (nothing enabled)
    python -m server.crypto_setup --activate      # development only: also activate the assets/networks on the mock chain
    python -m server.crypto_setup --dev-demo-wallets   # development only: attach clearly-labelled MOCK wallets so the UI can be exercised

Nothing here invents a Vaulta wallet address. Real addresses arrive through secure configuration (BTC_WALLET_ADDRESS, ETH_WALLET_ADDRESS,
USDT_WALLET_ADDRESS, USDC_WALLET_ADDRESS, SOL_WALLET_ADDRESS) or the admin approval workflow, and are applied to a wallet ONLY while it is
still the NOT_CONFIGURED placeholder. Afterwards the database is the source of truth. Token contract addresses come from
USDT_ETHEREUM_CONTRACT / USDC_ETHEREUM_CONTRACT (verify them against the issuer's published list); none are hard-coded for staging/production.
"""
import os, sys, hashlib
from . import assets as reg, crypto_wallets as cw, chain_addresses as ca, util
from .db import one, all_
from .util import ApiError

DEV_CONTRACTS = {'USDT': '0x' + '1' * 40, 'USDC': '0x' + '2' * 40}     # synthetic, digits-only: usable ONLY against the mock chain
# symbol, name, type, network_code, network name, chain_type, address_format, attribution, decimals, confirmations, finality, min_deposit (minor units)
SPEC = [
 ('BTC', 'Bitcoin', 'native', 'bitcoin', 'Bitcoin', 'utxo', 'btc', 'source', 8, 3, {}, 10_000),
 ('ETH', 'Ethereum', 'native', 'ethereum', 'Ethereum', 'account', 'evm', 'source', 18, 12, {}, 5 * 10**15),
 ('USDT', 'Tether', 'stablecoin', 'ethereum', 'Ethereum', 'account', 'evm', 'source', 6, 12, {}, 10_000_000),
 ('USDC', 'USD Coin', 'stablecoin', 'ethereum', 'Ethereum', 'account', 'evm', 'source', 6, 12, {}, 10_000_000),
 ('SOL', 'Solana', 'native', 'solana', 'Solana', 'account', 'solana', 'memo_or_source', 9, 32, {'mode': 'commitment', 'commitment': 'finalized'}, 50_000_000)]

def demo_address(fmt, tag, testnet=True):
    """Format-valid address derived from a label. NOBODY holds a key for it. Mock chain / tests only."""
    h = hashlib.sha256(('vaulta-dev-demo|' + tag).encode()).digest()
    if fmt == 'btc': return ca.bech32_encode('tb' if testnet else 'bc', [0] + ca._convertbits(h[:20], 8, 5), 'bech32')
    if fmt == 'evm': return ca.eip55('0x' + ca.keccak256(h)[-20:].hex())
    return ca.b58encode(h)

def bootstrap(db, env='development', environ=None, activate=False, actor_id=None):
    """Idempotent. Returns a report; never raises for a missing optional input (reports it instead)."""
    environ = os.environ if environ is None else environ
    testnet = env != 'production'; report = {'created': [], 'skipped': [], 'warnings': []}
    for sym, name, typ, ncode, nname, chain_type, fmt, attr, dec, conf, fin, mindep in SPEC:
        a = one(db, 'SELECT id FROM crypto_assets WHERE symbol=?', sym)
        if not a: reg.create_asset(db, actor_id, sym, name, typ); report['created'].append(f'asset {sym}')
        n = one(db, 'SELECT id FROM blockchain_networks WHERE network_code=?', ncode)
        if not n:
            reg.create_network(db, actor_id, ncode, nname + (' (test network)' if testnet else ''), chain_type, conf, is_testnet=testnet, finality_policy=fin, address_format=fmt, attribution_mode=attr)
            report['created'].append(f'network {ncode}')
        contract = None
        if typ != 'native':
            contract = (environ.get(f'{sym}_{ncode.upper()}_CONTRACT') or '').strip() or (DEV_CONTRACTS[sym] if env == 'development' else None)
            if not contract: report['warnings'].append(f'{sym} on {ncode}: no contract configured (set {sym}_{ncode.upper()}_CONTRACT); asset network not created'); continue
            try: ca.validate(fmt, contract, testnet=testnet, strict=True)
            except ca.InvalidAddress as e: report['warnings'].append(f'{sym} contract invalid: {e}'); continue
        code = sym if typ == 'native' else f'{sym}.{ncode.upper()}'
        if not one(db, 'SELECT 1 FROM asset_networks WHERE ledger_code=?', code):
            reg.create_asset_network(db, actor_id, sym, ncode, dec, contract, str(mindep)); report['created'].append(f'asset-network {code}')
        an = one(db, 'SELECT id FROM asset_networks WHERE ledger_code=?', code)
        if not one(db, "SELECT 1 FROM company_crypto_wallets WHERE asset_network_id=? AND wallet_type='deposit' AND status<>'retired'", an['id']):
            cw.install_wallet(db, an_id=an['id'], address=ca.PLACEHOLDER, label=f'Vaulta {sym} wallet', actor_id=actor_id, reason='initial placeholder', event='wallet_placeholder_created', allow_placeholder=True)
            report['created'].append(f'wallet {code} (NOT_CONFIGURED)')
        w = cw.live_wallet(db, an['id']) or dict(one(db, "SELECT * FROM company_crypto_wallets WHERE asset_network_id=? AND wallet_type='deposit' AND status<>'retired'", an['id']))
        given = (environ.get(f'{sym}_WALLET_ADDRESS') or '').strip()
        if given and not cw.is_configured(w):
            try:
                info = cw.an_info(db, an['id']); cw.env_guard(env, info)
                cw.install_wallet(db, an_id=an['id'], address=given, label=f'Vaulta {sym} wallet', actor_id=actor_id, reason=f'applied from {sym}_WALLET_ADDRESS at first setup', event='wallet_address_set_from_environment')
                report['created'].append(f'wallet {code} address from environment (inactive until enabled)')
            except ApiError as e: report['warnings'].append(f'{sym}_WALLET_ADDRESS rejected: {e.message}')
    if activate:
        if env != 'development': raise RuntimeError('--activate is for development only; enable assets in staging/production through the audited admin console')
        for kind, table in (('asset', 'crypto_assets'), ('network', 'blockchain_networks')):
            for r in all_(db, f"SELECT id FROM {table} WHERE status='disabled'"): reg.set_parent_status(db, actor_id, kind, r['id'], 'active', 'development bootstrap activation')
        for r in all_(db, "SELECT id FROM asset_networks WHERE status='disabled'"):
            reg.set_status(db, actor_id, r['id'], status='active', deposit_enabled=True, withdrawal_enabled=True, reason='development bootstrap activation')
    return report

def sync_chain(db, chain):
    """Tell the blockchain provider about every registered network (the mock chain needs this; real providers may no-op)."""
    for n in all_(db, 'SELECT * FROM blockchain_networks'): chain.prepare_network(n)

def startup_checks(db, env, mode):
    """Refuse to start on an unsafe combination found in the DATA (the config checks in config.py cover the settings)."""
    bad = []
    if env == 'production':
        t = all_(db, "SELECT DISTINCT n.network_code FROM blockchain_networks n JOIN asset_networks an ON an.network_id=n.id WHERE n.is_testnet=1 AND (an.status='active' OR an.deposit_enabled=1 OR an.withdrawal_enabled=1)")
        if t: bad.append('production database has test networks enabled: ' + ', '.join(r['network_code'] for r in t))
        m = all_(db, "SELECT id FROM company_crypto_wallets WHERE wallet_label LIKE 'DEV MOCK WALLET%' AND status<>'retired'")
        if m: bad.append('production database contains development mock wallets')
    elif env == 'staging':
        t = all_(db, "SELECT DISTINCT n.network_code FROM blockchain_networks n JOIN asset_networks an ON an.network_id=n.id WHERE n.is_testnet=0 AND (an.status='active' OR an.deposit_enabled=1)")
        if t: bad.append('staging has non-test networks enabled: ' + ', '.join(r['network_code'] for r in t))
    if bad: raise RuntimeError('Refusing to start: ' + '; '.join(bad))

def install_dev_demo_wallets(db, providers, env):
    """Attach format-valid MOCK wallets and switch deposits on, through system-level calls. Refuses anywhere but development + mock chain."""
    if env != 'development' or providers.crypto_mode != 'mock': raise RuntimeError('demo wallets are only available in development with CRYPTO_PROVIDER_MODE=mock')
    out = []
    for r in all_(db, "SELECT w.id wid, w.public_address, an.id an_id, an.ledger_code FROM company_crypto_wallets w JOIN asset_networks an ON an.id=w.asset_network_id WHERE w.status<>'retired' AND w.wallet_type='deposit'"):
        info = cw.an_info(db, r['an_id'])
        if r['public_address'] == ca.PLACEHOLDER:
            wid = cw.install_wallet(db, an_id=r['an_id'], address=demo_address(info['address_format'], info['ledger_code'], bool(info['is_testnet'])), label='DEV MOCK WALLET (not a real address)',
                                    actor_id=None, reason='development demo wallet on the mock chain', event='wallet_dev_demo_installed')
        else: wid = r['wid']
        db.execute("UPDATE company_crypto_wallets SET status='active',is_deposit_enabled=1,updated_at=? WHERE id=? AND status<>'retired'", (util.now(), wid)); out.append(info['ledger_code'])
    return out

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    from .config import Config
    from . import db as dbm, accounting, rbac
    c = dbm.connect(Config.DATABASE_PATH); dbm.init_schema(c); rbac.ensure_rbac(c); accounting.ensure_chart(c)
    rep = bootstrap(c, Config.ENV, activate='--activate' in argv)
    if '--dev-demo-wallets' in argv:
        from . import providers as prov
        P = prov.Providers({**{k: getattr(Config, k) for k in dir(Config) if k.isupper()}}); rep['demo_wallets'] = install_dev_demo_wallets(c, P, Config.ENV)
    for k, v in rep.items(): print(k + ':', v)

if __name__ == '__main__': main()
