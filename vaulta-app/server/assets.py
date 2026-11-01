"""Asset / network registry (M3). Assets, networks and contract addresses are DATA, not code.
Everything is created disabled; enabling is an explicit, audited operator action. No asset list is hard-coded here."""
import re, json
from decimal import Decimal, InvalidOperation
from . import util
from .util import ApiError
from .db import one, all_, tx
from .audit import audit

_CODE = re.compile(r'^[A-Z0-9]{2,12}(\.[A-Z0-9_]{2,16})?$')
_SYM = re.compile(r'^[A-Z0-9]{2,12}$')
_NET = re.compile(r'^[a-z0-9_]{2,24}$')

def create_asset(db, actor_id, symbol, name, typ):
    symbol = (symbol or '').strip().upper()
    if not _SYM.match(symbol): raise ApiError(422, 'invalid_symbol', 'Symbol must be 2-12 letters/digits')
    if typ not in ('native', 'token', 'stablecoin'): raise ApiError(422, 'invalid_type', 'type must be native, token or stablecoin')
    with tx(db):
        if one(db, 'SELECT 1 FROM crypto_assets WHERE symbol=?', symbol): raise ApiError(409, 'exists', 'Asset already exists')
        aid = db.execute('INSERT INTO crypto_assets(symbol,name,type,status,created_at) VALUES(?,?,?,?,?)', (symbol, util.text(name, 'name', 80), typ, 'disabled', util.now())).lastrowid
        audit(db, actor_id, 'crypto.asset_created', 'crypto_asset', aid, None, {'symbol': symbol, 'type': typ}); return aid

def create_network(db, actor_id, network_code, name, chain_type, confirmations_required, chain_id=None, is_testnet=False, finality_policy=None, address_format=None, attribution_mode='source'):
    if not _NET.match(network_code or ''): raise ApiError(422, 'invalid_network', 'network_code must be lowercase letters/digits/underscore')
    if chain_type not in ('utxo', 'account'): raise ApiError(422, 'invalid_chain_type', 'chain_type must be utxo or account')
    if not isinstance(confirmations_required, int) or isinstance(confirmations_required, bool) or not 1 <= confirmations_required <= 10000:
        raise ApiError(422, 'invalid_confirmations', 'confirmations_required must be a positive integer')
    if address_format not in (None, 'btc', 'evm', 'solana'): raise ApiError(422, 'invalid_address_format', 'address_format must be btc, evm or solana')
    if attribution_mode not in ('memo_or_source', 'source', 'none'): raise ApiError(422, 'invalid_attribution_mode', 'attribution_mode must be memo_or_source, source or none')
    with tx(db):
        if one(db, 'SELECT 1 FROM blockchain_networks WHERE network_code=?', network_code): raise ApiError(409, 'exists', 'Network already exists')
        nid = db.execute('INSERT INTO blockchain_networks(network_code,name,chain_type,chain_id,is_testnet,confirmations_required,finality_policy,status,created_at,address_format,attribution_mode) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                         (network_code, util.text(name, 'name', 80), chain_type, None if chain_id is None else str(chain_id), 1 if is_testnet else 0, confirmations_required, util.jdump(finality_policy or {}), 'disabled', util.now(), address_format, attribution_mode)).lastrowid
        audit(db, actor_id, 'crypto.network_created', 'blockchain_network', nid, None, {'network_code': network_code, 'confirmations': confirmations_required, 'address_format': address_format}); return nid

def create_asset_network(db, actor_id, symbol, network_code, decimals, contract_address=None, min_deposit_minor='0', confirmations_override=None, withdrawal_fee_policy=None):
    """Registers the network-specific representation (ledger_code like USDT.ETH) and its ledger asset. Created disabled."""
    if not isinstance(decimals, int) or isinstance(decimals, bool) or not 0 <= decimals <= 36: raise ApiError(422, 'invalid_decimals', 'decimals must be an integer between 0 and 36')
    try: mn = int(str(min_deposit_minor))
    except ValueError: raise ApiError(422, 'invalid_min_deposit', 'min_deposit must be an integer in minor units')
    if mn < 0: raise ApiError(422, 'invalid_min_deposit', 'min_deposit cannot be negative')
    with tx(db):
        a = one(db, 'SELECT * FROM crypto_assets WHERE symbol=?', (symbol or '').upper()); n = one(db, 'SELECT * FROM blockchain_networks WHERE network_code=?', network_code)
        if not a or not n: raise ApiError(404, 'not_found', 'Unknown asset or network')
        if a['type'] in ('token', 'stablecoin') and not contract_address: raise ApiError(422, 'contract_required', 'Tokens require a verified contract address')
        if a['type'] == 'native' and contract_address: raise ApiError(422, 'contract_not_allowed', 'Native assets have no contract address')
        code = f"{a['symbol']}.{network_code.upper()}" if a['type'] != 'native' else f"{a['symbol']}"
        if not _CODE.match(code): raise ApiError(422, 'invalid_code', 'Derived ledger code is invalid')
        if one(db, 'SELECT 1 FROM asset_networks WHERE asset_id=? AND network_id=?', a['id'], n['id']) or one(db, 'SELECT 1 FROM ledger_assets WHERE code=?', code):
            raise ApiError(409, 'exists', 'This asset/network pair is already registered')
        db.execute('INSERT INTO ledger_assets(code,kind,decimals,created_at) VALUES(?,?,?,?)', (code, 'crypto', decimals, util.now()))
        try:
            anid = db.execute('INSERT INTO asset_networks(ledger_code,asset_id,network_id,contract_address,decimals,min_deposit,confirmations_override,withdrawal_fee_policy,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                              (code, a['id'], n['id'], (contract_address or None), decimals, str(mn), confirmations_override, util.jdump(withdrawal_fee_policy or {}), util.now())).lastrowid
        except Exception as e:
            if 'UNIQUE' in str(e): raise ApiError(409, 'contract_in_use', 'That contract address is already registered on this network')
            raise
        audit(db, actor_id, 'crypto.asset_network_created', 'asset_network', anid, None, {'ledger_code': code, 'decimals': decimals, 'contract': contract_address}); return anid

def set_status(db, actor_id, asset_network_id, *, status=None, deposit_enabled=None, withdrawal_enabled=None, reason=None):
    """Enabling requires: reason, asset + network active, and (for deposits) a configured confirmation policy. All changes audited."""
    if not (reason or '').strip() or len(reason.strip()) < 10: raise ApiError(422, 'reason_required', 'A reason of at least 10 characters is required.')
    with tx(db):
        r = one(db, 'SELECT an.*,a.status astatus,n.status nstatus FROM asset_networks an JOIN crypto_assets a ON a.id=an.asset_id JOIN blockchain_networks n ON n.id=an.network_id WHERE an.id=?', asset_network_id)
        if not r: raise ApiError(404, 'not_found', 'Asset network not found')
        new = {'status': status or r['status'], 'deposit_enabled': r['deposit_enabled'] if deposit_enabled is None else int(bool(deposit_enabled)),
               'withdrawal_enabled': r['withdrawal_enabled'] if withdrawal_enabled is None else int(bool(withdrawal_enabled))}
        if new['status'] not in ('disabled', 'active', 'suspended'): raise ApiError(422, 'invalid_status', 'Unknown status')
        if (new['deposit_enabled'] or new['withdrawal_enabled']) and new['status'] != 'active': raise ApiError(409, 'not_active', 'Deposits/withdrawals can only be enabled on an active asset network.')
        if new['status'] == 'active' and (r['astatus'] != 'active' or r['nstatus'] != 'active'): raise ApiError(409, 'parent_inactive', 'Activate the asset and the network first.')
        if new['status'] != 'active': new['deposit_enabled'] = new['withdrawal_enabled'] = 0     # suspending always closes both directions
        db.execute('UPDATE asset_networks SET status=?,deposit_enabled=?,withdrawal_enabled=?,updated_at=? WHERE id=?', (new['status'], new['deposit_enabled'], new['withdrawal_enabled'], util.now(), asset_network_id))
        audit(db, actor_id, 'crypto.asset_network_status', 'asset_network', asset_network_id, {k: r[k] for k in new}, new, reason); return new

def set_parent_status(db, actor_id, kind, ident, status, reason):
    if kind not in ('asset', 'network'): raise ApiError(422, 'invalid_kind', 'kind must be asset or network')
    if status not in ('disabled', 'active', 'suspended'): raise ApiError(422, 'invalid_status', 'Unknown status')
    if not (reason or '').strip() or len(reason.strip()) < 10: raise ApiError(422, 'reason_required', 'A reason of at least 10 characters is required.')
    tbl, col, fk = ('crypto_assets', 'id', 'asset_id') if kind == 'asset' else ('blockchain_networks', 'id', 'network_id')
    with tx(db):
        r = one(db, f'SELECT * FROM {tbl} WHERE {col}=?', ident)
        if not r: raise ApiError(404, 'not_found', f'{kind} not found')
        db.execute(f'UPDATE {tbl} SET status=? WHERE id=?', (status, ident))
        if status != 'active': db.execute(f"UPDATE asset_networks SET deposit_enabled=0,withdrawal_enabled=0,status=CASE WHEN status='active' THEN 'suspended' ELSE status END,updated_at=? WHERE {fk}=?", (util.now(), ident))
        audit(db, actor_id, f'crypto.{kind}_status', tbl, ident, {'status': r['status']}, {'status': status}, reason)

def list_registry(db):
    return all_(db, '''SELECT an.id,an.ledger_code,a.symbol,a.name,a.type,n.network_code,n.chain_type,n.is_testnet,an.contract_address,an.decimals,an.min_deposit,
        COALESCE(an.confirmations_override,n.confirmations_required) confirmations_required,an.deposit_enabled,an.withdrawal_enabled,an.status,a.status asset_status,n.status network_status
        FROM asset_networks an JOIN crypto_assets a ON a.id=an.asset_id JOIN blockchain_networks n ON n.id=an.network_id ORDER BY an.id''')

# ---- amounts: registry decimals decide what an integer means; nothing is ever a float ----
def to_minor(db, ledger_code, amount):
    """Decimal string -> integer minor units. Rejects floats, excess precision, non-positive values, unknown assets."""
    if isinstance(amount, (float, bool)): raise ApiError(422, 'invalid_number', 'Send amounts as strings')
    la = one(db, 'SELECT decimals FROM ledger_assets WHERE code=?', ledger_code)
    if not la: raise ApiError(422, 'unknown_asset', f'Unknown ledger asset {ledger_code}')
    try: d = Decimal(str(amount).strip())
    except InvalidOperation: raise ApiError(422, 'invalid_number', 'Not a valid number')
    if not d.is_finite() or d <= 0: raise ApiError(422, 'invalid_amount', 'Amount must be greater than zero')
    scaled = d.scaleb(la['decimals'])
    if scaled != scaled.to_integral_value(): raise ApiError(422, 'too_many_decimals', f"{ledger_code} supports at most {la['decimals']} decimal places")
    return int(scaled)

def from_minor(db, ledger_code, minor):
    la = one(db, 'SELECT decimals FROM ledger_assets WHERE code=?', ledger_code)
    if not la: raise ApiError(422, 'unknown_asset', f'Unknown ledger asset {ledger_code}')
    return format(Decimal(int(minor)).scaleb(-la['decimals']), 'f')
