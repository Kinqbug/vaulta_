"""Admin management of the cryptocurrency catalog inside the existing asset registry (assets.py): logo, description, visibility, supported networks,
deposit/withdrawal switches, limits, fees and confirmation policy. The registry stays the single source of truth; the frontend renders everything from it.

Logos: validated by CONTENT (uploads.validate_logo), stored under a random server-generated name in a private directory, served by an authenticated endpoint.
Replacing a logo updates the same asset record (no duplicate asset) and changes its URL version so every screen refreshes."""
import json
from decimal import Decimal, InvalidOperation
from . import util, uploads, assets as reg
from .util import ApiError
from .db import one, all_, val, tx
from .audit import audit

LOGO_DIR = 'asset-logos'
MAX_FEE_BPS = 2000
# customer-facing copy configured per asset-network (column -> max length). Plain text only: the UI escapes it, control characters are stripped here.
TEXT_FIELDS = {'estimated_arrival': 120, 'processing_time': 160, 'deposit_instructions': 600, 'withdrawal_instructions': 600, 'network_notice': 400}
_CTRL = __import__('re').compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
def clean_copy(v, field, n):
    v = _CTRL.sub('', '' if v is None else str(v)).strip()
    if len(v) > n: raise ApiError(422, 'too_long', f'{field.replace("_", " ")} is too long (max {n} characters)')
    return v
ADDRESS_HINTS = {'btc': 'Starts with bc1, 1 or 3 (tb1 on a test network)', 'evm': 'Starts with 0x followed by 40 characters', 'solana': 'A base58 address of 32 to 44 characters'}

def logo_url(a):
    return f"/api/crypto/logos/{a['id']}?v={a['logo_sha256'][:12]}" if a['logo_path'] and a['logo_sha256'] else None

def logo_map(db, include_hidden=False):
    """symbol -> logo URL, used to decorate market lists, holdings and history without any hard-coded images."""
    return {r['symbol']: logo_url(r) for r in db.execute('SELECT * FROM crypto_assets WHERE logo_path IS NOT NULL' + ('' if include_hidden else ' AND visible=1'))}

def _dec(db, ledger_code, v, field):
    """Decimal string -> minor units; zero is allowed here (a zero fee or minimum), negatives and junk are not."""
    if v in (None, ''): return None
    try: d = Decimal(str(v).strip())
    except InvalidOperation: raise ApiError(422, 'invalid_amount', f'{field} must be a valid number')
    if not d.is_finite() or d < 0: raise ApiError(422, 'invalid_amount', f'{field} cannot be negative')
    if d == 0: return 0
    try: return reg.to_minor(db, ledger_code, str(v))
    except ApiError: raise ApiError(422, 'invalid_amount', f'{field} must be a valid amount for this asset')

def network_view(db, an):
    pol = json.loads(an['withdrawal_fee_policy'] or '{}'); c = an['ledger_code']; n = one(db, 'SELECT * FROM blockchain_networks WHERE id=?', an['network_id'])
    fm = lambda m: None if m in (None, '') else reg.from_minor(db, c, m)
    wallet = one(db, "SELECT 1 FROM company_crypto_wallets WHERE asset_network_id=? AND wallet_type='deposit' AND status='active' AND is_deposit_enabled=1 AND public_address<>'NOT_CONFIGURED'", an['id'])
    return {'id': an['id'], 'ledger_code': c, 'network_code': n['network_code'], 'network_name': n['name'], 'network_status': n['status'], 'is_testnet': bool(n['is_testnet']), 'contract_address': an['contract_address'], 'decimals': an['decimals'],
            'status': an['status'], 'deposit_enabled': bool(an['deposit_enabled']), 'withdrawal_enabled': bool(an['withdrawal_enabled']), 'min_deposit': fm(an['min_deposit']), 'max_deposit': fm(an['max_deposit']),
            'min_withdrawal': fm(an['min_withdrawal'] if an['min_withdrawal'] is not None else (str(pol['min_minor']) if pol.get('min_minor') else None)), 'max_withdrawal': fm(an['max_withdrawal']),
            'fee_flat': fm(str(pol.get('flat_minor', 0))) if int(pol.get('flat_minor', 0)) else '0', 'fee_percent': str(Decimal(int(pol.get('bps', 0))) / 100),
            'confirmations_override': an['confirmations_override'], 'confirmations_effective': an['confirmations_override'] or n['confirmations_required'], 'has_deposit_wallet': bool(wallet),
            'address_format': n['address_format'], **{k: an[k] or '' for k in TEXT_FIELDS}}

def asset_view(db, a, with_networks=True):
    out = {'id': a['id'], 'symbol': a['symbol'], 'name': a['name'], 'type': a['type'], 'status': a['status'], 'description': a['description'] or '', 'visible': bool(a['visible']), 'logo': logo_url(a), 'logo_alt': a['logo_alt'] or '', 'notice': a['notice'] or '', 'created_at': a['created_at']}
    from . import crypto_convert as _cv
    try: out['usd_price'] = str(_cv.price(db, a['symbol'])); out['price_issue'] = None            # deposits and withdrawals convert at this price
    except ApiError as e: out['usd_price'] = None; out['price_issue'] = e.message
    if with_networks: out['networks'] = [network_view(db, an) for an in db.execute('SELECT * FROM asset_networks WHERE asset_id=? ORDER BY id', (a['id'],))]
    return out

def list_assets(db, include_hidden=True):
    return [asset_view(db, a) for a in db.execute('SELECT * FROM crypto_assets' + ('' if include_hidden else ' WHERE visible=1') + ' ORDER BY id')]

def available_networks(db):
    return [{'network_code': n['network_code'], 'name': n['name'], 'status': n['status'], 'is_testnet': bool(n['is_testnet'])} for n in db.execute('SELECT * FROM blockchain_networks WHERE address_format IS NOT NULL ORDER BY id')]

# ---------------------------------------------------------------- create / update
def _text(v, field, n, req=True):
    v = (v or '').strip()
    if req and not v: raise ApiError(422, 'missing_field', f'{field} is required')
    return util.text(v, field, n) if v else ''

def _put_logo(root, data):
    mime, stored = uploads.validate_logo(data)
    name, sha = uploads.store(root, LOGO_DIR, stored, mime); return name, mime, sha

def create_asset(db, actor_id, root, *, symbol, name, typ='native', description='', visible=True, logo=None, activate=False, network=None, logo_alt=None, notice=None):
    symbol = (symbol or '').strip().upper(); name = _text(name, 'name', 60); description = _text(description, 'description', 600, req=False)
    new_logo = _put_logo(root, logo) if logo else None
    try:
        with tx(db):
            aid = reg.create_asset(db, actor_id, symbol, name, typ)
            db.execute('UPDATE crypto_assets SET description=?,visible=?,logo_path=?,logo_mime=?,logo_sha256=?,logo_updated_at=? WHERE id=?',
                       (description or None, 1 if visible else 0, *(new_logo or (None, None, None)), util.now() if new_logo else None, aid))
            if logo_alt or notice: db.execute('UPDATE crypto_assets SET logo_alt=?,notice=? WHERE id=?', (clean_copy(logo_alt, 'logo alt text', 140) or None, clean_copy(notice, 'notice', 400) or None, aid))
            if activate: reg.set_parent_status(db, actor_id, 'asset', aid, 'active', 'activated when the asset was created')
            if network: add_network(db, actor_id, aid, network)
            audit(db, actor_id, 'crypto.asset_catalog_created', 'crypto_asset', aid, None, {'symbol': symbol, 'name': name, 'visible': bool(visible), 'logo': bool(new_logo)}, None)
            return asset_view(db, one(db, 'SELECT * FROM crypto_assets WHERE id=?', aid))
    except BaseException:
        if new_logo: uploads.remove(root, LOGO_DIR, new_logo[0])
        raise

def update_asset(db, actor_id, root, asset_id, *, name=None, description=None, visible=None, logo=None, remove_logo=False, status=None, reason=None, logo_alt=None, notice=None):
    new_logo = _put_logo(root, logo) if logo else None; old = None
    try:
        with tx(db):
            a = one(db, 'SELECT * FROM crypto_assets WHERE id=?', asset_id)
            if not a: raise ApiError(404, 'not_found', 'Asset not found')
            sets = {}
            if name is not None: sets['name'] = _text(name, 'name', 60)
            if description is not None: sets['description'] = _text(description, 'description', 600, req=False) or None
            if visible is not None: sets['visible'] = 1 if visible else 0
            if logo_alt is not None: sets['logo_alt'] = clean_copy(logo_alt, 'logo alt text', 140) or None
            if notice is not None: sets['notice'] = clean_copy(notice, 'notice', 400) or None
            if new_logo: sets.update(logo_path=new_logo[0], logo_mime=new_logo[1], logo_sha256=new_logo[2], logo_updated_at=util.now()); old = a['logo_path']
            elif remove_logo and a['logo_path']: sets.update(logo_path=None, logo_mime=None, logo_sha256=None, logo_updated_at=util.now()); old = a['logo_path']
            if sets: db.execute('UPDATE crypto_assets SET ' + ','.join(f'{k}=?' for k in sets) + ' WHERE id=?', (*sets.values(), asset_id))
            if status and status != a['status']: reg.set_parent_status(db, actor_id, 'asset', asset_id, status, reason or '')
            if sets or status:
                audit(db, actor_id, 'crypto.asset_catalog_updated', 'crypto_asset', asset_id, {k: a[k] for k in sets if k not in ('logo_path',)} | ({'logo_sha256': a['logo_sha256']} if 'logo_sha256' in sets else {}),
                      {k: v for k, v in sets.items() if k != 'logo_path'} | ({'status': status} if status else {}), reason)
            res = asset_view(db, one(db, 'SELECT * FROM crypto_assets WHERE id=?', asset_id))
    except BaseException:
        if new_logo: uploads.remove(root, LOGO_DIR, new_logo[0])
        raise
    if old: uploads.remove(root, LOGO_DIR, old)          # only after the database commit succeeded
    return res

def delete_asset(db, actor_id, root, asset_id):
    """Only an asset that never reached the registry/ledger can be deleted. Anything with history must be archived (disabled + hidden) instead."""
    with tx(db):
        a = one(db, 'SELECT * FROM crypto_assets WHERE id=?', asset_id)
        if not a: raise ApiError(404, 'not_found', 'Asset not found')
        if val(db, 'SELECT COUNT(*) FROM asset_networks WHERE asset_id=?', asset_id): raise ApiError(409, 'has_history', 'This asset has network registrations and ledger history. Archive it instead (disable and hide it).')
        db.execute('DELETE FROM crypto_assets WHERE id=?', (asset_id,)); audit(db, actor_id, 'crypto.asset_catalog_deleted', 'crypto_asset', asset_id, {'symbol': a['symbol']}, None, None)
    uploads.remove(root, LOGO_DIR, a['logo_path'])

# ---------------------------------------------------------------- networks, limits, fees
def add_network(db, actor_id, asset_id, cfg):
    a = one(db, 'SELECT * FROM crypto_assets WHERE id=?', asset_id)
    if not a: raise ApiError(404, 'not_found', 'Asset not found')
    code = (cfg.get('network_code') or '').strip(); dec = cfg.get('decimals')
    if isinstance(dec, str) and dec.isdigit(): dec = int(dec)
    with tx(db):
        reg.create_asset_network(db, actor_id, a['symbol'], code, dec, (cfg.get('contract_address') or '').strip() or None, '0', None, None)
        an = one(db, 'SELECT * FROM asset_networks WHERE asset_id=? AND network_id=(SELECT id FROM blockchain_networks WHERE network_code=?)', asset_id, code)
        configure_network(db, actor_id, an['id'], {k: v for k, v in cfg.items() if k in ('min_deposit', 'max_deposit', 'min_withdrawal', 'max_withdrawal', 'fee_flat', 'fee_percent', 'confirmations', *TEXT_FIELDS)}, reason='initial configuration', toggles=False)
        return network_view(db, one(db, 'SELECT * FROM asset_networks WHERE id=?', an['id']))

def configure_network(db, actor_id, an_id, cfg, reason=None, toggles=True):
    """Limits, fees, confirmations and the deposit/withdrawal/asset-network switches. Every change is audited with its reason."""
    with tx(db):
        an = one(db, 'SELECT * FROM asset_networks WHERE id=?', an_id)
        if not an: raise ApiError(404, 'not_found', 'Asset network not found')
        c = an['ledger_code']; sets = {}; pol = json.loads(an['withdrawal_fee_policy'] or '{}')
        for k, col in (('min_deposit', 'min_deposit'), ('max_deposit', 'max_deposit'), ('min_withdrawal', 'min_withdrawal'), ('max_withdrawal', 'max_withdrawal')):
            if k in cfg:
                m = _dec(db, c, cfg[k], k.replace('_', ' '))
                if k.startswith('max_') and m == 0: m = None          # a maximum of 0 means 'no cap', never 'block everything'
                sets[col] = None if m is None else str(m)
        for lo, hi in (('min_deposit', 'max_deposit'), ('min_withdrawal', 'max_withdrawal')):
            l, h = sets.get(lo, an[lo]), sets.get(hi, an[hi])
            if l not in (None, '') and h not in (None, '') and int(h) < int(l): raise ApiError(422, 'invalid_limits', f'{hi.replace("_", " ")} cannot be below {lo.replace("_", " ")}')
        if 'fee_flat' in cfg:
            m = _dec(db, c, cfg['fee_flat'], 'fee'); pol['flat_minor'] = int(m or 0)
        if 'fee_percent' in cfg:
            try: bps = int((Decimal(str(cfg['fee_percent'] or '0')) * 100).to_integral_value())
            except InvalidOperation: raise ApiError(422, 'invalid_fee', 'Fee percentage must be a number')
            if not 0 <= bps <= MAX_FEE_BPS: raise ApiError(422, 'invalid_fee', f'Fee percentage must be between 0 and {MAX_FEE_BPS / 100}')
            pol['bps'] = bps
        if 'fee_flat' in cfg or 'fee_percent' in cfg: sets['withdrawal_fee_policy'] = json.dumps(pol)
        if 'confirmations' in cfg:
            v = cfg['confirmations']
            if v in (None, ''): sets['confirmations_override'] = None
            else:
                try: v = int(v)
                except (TypeError, ValueError): raise ApiError(422, 'invalid_confirmations', 'Confirmations must be a whole number')
                if not 1 <= v <= 100000: raise ApiError(422, 'invalid_confirmations', 'Confirmations must be at least 1')
                sets['confirmations_override'] = v
        for k, n_ in TEXT_FIELDS.items():
            if k in cfg: sets[k] = clean_copy(cfg[k], k, n_) or None
        before = {k: an[k] for k in sets}
        if sets:
            db.execute('UPDATE asset_networks SET ' + ','.join(f'{k}=?' for k in sets) + ',updated_at=? WHERE id=?', (*sets.values(), util.now(), an_id))
            audit(db, actor_id, 'crypto.asset_network_config', 'asset_network', an_id, before, sets, reason)
        if toggles and any(k in cfg for k in ('status', 'deposit_enabled', 'withdrawal_enabled')):
            reg.set_status(db, actor_id, an_id, status=cfg.get('status'), deposit_enabled=cfg.get('deposit_enabled'), withdrawal_enabled=cfg.get('withdrawal_enabled'), reason=reason)
        return network_view(db, one(db, 'SELECT * FROM asset_networks WHERE id=?', an_id))

def read_logo(db, root, asset_id):
    a = one(db, 'SELECT * FROM crypto_assets WHERE id=?', asset_id)
    if not a or not a['logo_path']: raise ApiError(404, 'not_found', 'No logo')
    return dict(a), uploads.load(root, LOGO_DIR, a['logo_path'])

# ---------------------------------------------------------------- blockchain networks (admin)
NETWORK_COLS = 'id,network_code,name,chain_type,chain_id,is_testnet,confirmations_required,status,address_format,attribution_mode,explorer_url,created_at'

def list_networks(db):
    """Every registered network with how many assets use it, so an administrator can see what disabling it would affect."""
    out = []
    for n in db.execute(f'SELECT {NETWORK_COLS} FROM blockchain_networks ORDER BY id'):
        d = dict(n); d['is_testnet'] = bool(d['is_testnet']); d['usable'] = d['address_format'] is not None
        d['assets'] = [r['symbol'] for r in db.execute('SELECT a.symbol FROM asset_networks an JOIN crypto_assets a ON a.id=an.asset_id WHERE an.network_id=? ORDER BY a.symbol', (n['id'],))]
        out.append(d)
    return out

def create_network(db, actor_id, cfg):
    """A network created here is DISABLED and cannot carry customer funds until a blockchain provider for it is connected and an operator activates it."""
    code = (cfg.get('network_code') or '').strip(); fmt = (cfg.get('address_format') or '').strip()
    if fmt not in ('btc', 'evm', 'solana'): raise ApiError(422, 'invalid_address_format', 'Choose the address format used by this network (btc, evm or solana) so addresses can be validated.')
    try: conf = int(cfg.get('confirmations_required'))
    except (TypeError, ValueError): raise ApiError(422, 'invalid_confirmations', 'Confirmations must be a whole number')
    chain_id = (str(cfg.get('chain_id')).strip() if cfg.get('chain_id') not in (None, '') else None)
    nid = reg.create_network(db, actor_id, code, _text(cfg.get('name'), 'name', 80), cfg.get('chain_type') or 'account', conf, chain_id=chain_id, is_testnet=_truthy(cfg.get('is_testnet')),
                             address_format=fmt, attribution_mode=cfg.get('attribution_mode') or 'source')
    return next(n for n in list_networks(db) if n['id'] == nid)

def _truthy(v): return v if isinstance(v, bool) else str(v).lower() in ('1', 'true', 'on', 'yes')

def update_network(db, actor_id, network_id, *, name=None, confirmations_required=None, status=None, reason=None):
    """Rename, change the default confirmation requirement, or activate/suspend/disable. Suspending closes every asset on the network for NEW transactions only;
    existing transactions keep the network they were created on."""
    with tx(db):
        n = one(db, 'SELECT * FROM blockchain_networks WHERE id=?', network_id)
        if not n: raise ApiError(404, 'not_found', 'Network not found')
        sets = {}
        if name is not None: sets['name'] = _text(name, 'name', 80)
        if confirmations_required not in (None, ''):
            try: c = int(confirmations_required)
            except (TypeError, ValueError): raise ApiError(422, 'invalid_confirmations', 'Confirmations must be a whole number')
            if not 1 <= c <= 10000: raise ApiError(422, 'invalid_confirmations', 'Confirmations must be between 1 and 10000')
            sets['confirmations_required'] = c
        if sets:
            if not (reason or '').strip() or len(reason.strip()) < 10: raise ApiError(422, 'reason_required', 'A reason of at least 10 characters is required.')
            db.execute('UPDATE blockchain_networks SET ' + ','.join(f'{k}=?' for k in sets) + ' WHERE id=?', (*sets.values(), network_id))
            audit(db, actor_id, 'crypto.network_updated', 'blockchain_network', network_id, {k: n[k] for k in sets}, sets, reason)
        if status and status != n['status']: reg.set_parent_status(db, actor_id, 'network', network_id, status, reason or '')
        return next(x for x in list_networks(db) if x['id'] == network_id)
