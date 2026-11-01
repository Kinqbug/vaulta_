"""Read models and small write helpers behind the admin 'Wallets & Reconciliation' and 'Asset Settings' pages.

Nothing here invents data: balances come from the configured BlockchainProvider (and are reported as unavailable when it cannot answer),
fiat values are labelled estimates and only produced when the asset has a valid, fresh price, and reconciliation status is DERIVED from the
deposit / withdrawal / ledger / exception records that already exist. Writing helpers audit every change."""
import json
from decimal import Decimal
from urllib.parse import urlparse
from . import util, assets as reg, crypto_wallets as cw, crypto_catalog as cat, crypto_convert as cv, crypto_ledger as cl
from .util import ApiError
from .db import one, all_, tx
from .audit import audit

PENDING_DEP = ('detected', 'validating', 'unconfirmed', 'confirming', 'confirmed', 'risk_review')
RECON_STATUSES = ('pending', 'confirming', 'reconciled', 'mismatch', 'failed')


# ---------------------------------------------------------------- explorer URL
def clean_explorer_url(v):
    v = (v or '').strip()
    if not v: return None
    if len(v) > 300: raise ApiError(422, 'invalid_url', 'Explorer URL is too long (max 300 characters).')
    if any(ord(c) < 33 or ord(c) == 127 for c in v): raise ApiError(422, 'invalid_url', 'Explorer URL cannot contain spaces or control characters.')
    u = urlparse(v)
    if u.scheme != 'https' or not u.hostname or '.' not in u.hostname: raise ApiError(422, 'invalid_url', 'Explorer URL must be a complete https:// address.')
    if u.username or u.password: raise ApiError(422, 'invalid_url', 'Explorer URL cannot contain credentials.')
    return v.rstrip('/')

def set_explorer(db, actor_id, network_id, url, reason):
    with tx(db):
        n = one(db, 'SELECT * FROM blockchain_networks WHERE id=?', network_id)
        if not n: raise ApiError(404, 'not_found', 'Network not found')
        new = clean_explorer_url(url)
        if new == n['explorer_url']: return new
        if len((reason or '').strip()) < 10: raise ApiError(422, 'reason_required', 'A reason of at least 10 characters is required.')
        db.execute('UPDATE blockchain_networks SET explorer_url=? WHERE id=?', (new, network_id))
        audit(db, actor_id, 'crypto.network_explorer_updated', 'blockchain_network', network_id, {'explorer_url': n['explorer_url']}, {'explorer_url': new}, reason)
        return new


def _who(db, rows):
    """Attach the requesting / deciding administrator's email to change requests (ids alone are not useful to a reviewer)."""
    ids = {r[k] for r in rows for k in ('requested_by', 'decided_by') if r.get(k)}
    em = {u['id']: u['email'] for u in db.execute('SELECT id,email FROM users WHERE id IN(%s)' % ','.join('?' * len(ids)), tuple(ids))} if ids else {}
    return [dict(r, requested_by_email=em.get(r['requested_by']), decided_by_email=em.get(r.get('decided_by'))) for r in rows]

# ---------------------------------------------------------------- wallets overview
def _wallet_balance(db, providers, w, info):
    """-> dict(available, pending, source, issue). Never guesses: if the chain provider cannot answer, available is None."""
    out = {'available': None, 'pending': None, 'balance_source': 'unavailable', 'balance_issue': None}
    if not cw.is_configured(w):
        out['balance_issue'] = 'Wallet address not configured'; return out
    try:
        minor = providers.chain.get_balance(info['network_code'], w['public_address'], info['contract_address'], cw.effective(db, w)['confirmations'])
        out['available'] = reg.from_minor(db, info['ledger_code'], minor)
        out['balance_source'] = 'chain' if providers.crypto_mode == 'live' else 'test_chain'
    except Exception as e:                                           # unimplemented live adapter, provider outage, unknown network...
        out['balance_issue'] = (getattr(e, 'message', None) or str(e) or e.__class__.__name__)[:160]
    pend = sum(int(r['amount']) for r in db.execute(f"SELECT amount FROM crypto_deposits WHERE company_wallet_id=? AND status IN({','.join('?' * len(PENDING_DEP))})", (w['id'], *PENDING_DEP)))
    out['pending'] = reg.from_minor(db, info['ledger_code'], pend) if pend else '0'
    return out

def _usd(db, ledger_code, amount):
    try: p = cv.price(db, ledger_code)
    except ApiError: return None
    return str((Decimal(amount) * p).quantize(Decimal('0.01')))

def overview(db, providers):
    rows = all_(db, "SELECT * FROM company_crypto_wallets WHERE status<>'retired' ORDER BY asset_network_id, wallet_type, id")
    wallets, total, complete, priced = [], Decimal(0), True, 0
    for w in rows:
        info = cw.an_info(db, w['asset_network_id']); v = cw.view(db, w)
        a = one(db, 'SELECT * FROM crypto_assets WHERE id=?', info['asset_id']); n = one(db, 'SELECT explorer_url FROM blockchain_networks WHERE id=?', info['network_id'])
        b = _wallet_balance(db, providers, w, info)
        v.update(b, asset_id=info['asset_id'], logo=cat.logo_url(a), asset_status=info['asset_status'], network_status=info['network_status'], an_status=info['an_status'],
                 deposit_enabled_network=bool(info['deposit_enabled']), withdrawal_enabled_network=bool(info['withdrawal_enabled']), explorer_url=n['explorer_url'], chain_type=info['chain_type'],
                 address_format=info['address_format'], asset_type=info['asset_type'])
        v['fiat_estimate'] = _usd(db, info['ledger_code'], b['available']) if b['available'] is not None else None
        if cw.is_configured(w):
            if v['fiat_estimate'] is None: complete = False
            else: total += Decimal(v['fiat_estimate']); priced += 1
        v['available_status'] = v['status']
        wallets.append(v)
    pend_dep = one(db, f"SELECT COUNT(*) c FROM crypto_deposits WHERE status IN('unconfirmed','confirming')")['c']
    pend_wd = one(db, "SELECT COUNT(*) c FROM crypto_withdrawals WHERE status IN('broadcast','confirming')")['c']
    run = one(db, "SELECT ref,started_at,finished_at,status FROM reconciliation_runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1")
    return {'wallets': wallets, 'environment': providers.env, 'provider_mode': providers.crypto_mode,
            'summary': {'total_wallets': len(wallets), 'configured_wallets': sum(1 for w in wallets if w['configured']), 'active_wallets': sum(1 for w in wallets if w['status'] == 'active' and w['configured']),
                        'estimated_total_usd': str(total.quantize(Decimal('0.01'))) if priced else None, 'estimate_complete': complete and priced > 0 or (priced == 0 and not any(w['configured'] for w in wallets)),
                        'priced_wallets': priced, 'pending_confirmations': pend_dep + pend_wd, 'pending_deposits': pend_dep, 'pending_withdrawals': pend_wd,
                        'last_reconciliation': dict(run) if run else None,
                        'open_exceptions': one(db, "SELECT COUNT(*) c FROM reconciliation_exceptions WHERE status IN('open','investigating')")['c']},
            'pending_changes': _who(db, cw.list_requests(db, 'pending')), 'networks': sorted({(w['network'], w['network_name']) for w in wallets})}


# ---------------------------------------------------------------- reconciliation records (derived, read-only)
def _open_exception_index(db):
    """deposit/withdrawal ref -> [exception rows], parsed from the details of exceptions that are still open."""
    idx = {}
    for e in db.execute("SELECT id,kind,severity,status,details FROM reconciliation_exceptions WHERE status IN('open','investigating')"):
        try: d = json.loads(e['details'] or '{}')
        except ValueError: continue
        ref = d.get('deposit') or d.get('withdrawal')
        if ref: idx.setdefault(ref, []).append({'id': e['id'], 'kind': e['kind'], 'severity': e['severity'], 'status': e['status']})
    return idx

def _credit_amount(db, d):
    if not d['credit_entry_id'] or not d['user_id']: return None
    try: acct = cl.customer_account(db, d['user_id'])
    except Exception: return None
    r = one(db, "SELECT amount FROM journal_lines WHERE entry_id=? AND direction='credit' AND gl_account_id=?", d['credit_entry_id'], acct)
    return int(r['amount']) if r else None

def records(db, *, asset=None, network=None, typ=None, status=None, date_from=None, date_to=None, q=None, page=1, size=20, limit=3000):
    exc = _open_exception_index(db); out = []
    base = '''SELECT d.*, a.symbol sym, an.ledger_code lc, n.network_code ncode, n.name nname FROM crypto_deposits d
              LEFT JOIN asset_networks an ON an.id=d.asset_network_id LEFT JOIN crypto_assets a ON a.id=an.asset_id JOIN blockchain_networks n ON n.id=d.network_id ORDER BY d.id DESC LIMIT ?'''
    for d in db.execute(base, (limit,)):
        d = dict(d); lc = d['lc']; conf, need = d['confirmations'], d['required_confirmations']; led = _credit_amount(db, d); amt = int(d['amount']); st = d['status']; why = None
        fm = (lambda m: None if m is None else reg.from_minor(db, lc, m)) if lc else (lambda m: None if m is None else str(m))
        if st == 'credited':
            if led == amt: rs = 'reconciled'
            else: rs = 'mismatch'; why = f"Ledger credit {fm(led) if led is not None else 'missing'} differs from the on-chain amount {fm(amt)}."
        elif st in ('detected', 'validating', 'unconfirmed'): rs = 'pending'
        elif st in ('confirming', 'confirmed', 'risk_review'): rs = 'confirming' if st != 'risk_review' else 'pending'; why = 'Awaiting second-person risk approval.' if st == 'risk_review' else None
        elif st in ('rejected', 'failed', 'reorged'): rs = 'failed'; why = d['status_reason']
        else: rs = 'mismatch'; why = d['status_reason'] or st.replace('_', ' ')          # duplicate / wrong_network / unsupported_asset / manual_review
        if d['ref'] in exc and rs != 'failed': rs = 'mismatch'; why = (why + ' ' if why else '') + 'Open reconciliation exception: ' + ', '.join(x['kind'].replace('_', ' ') for x in exc[d['ref']]) + '.'
        out.append({'id': 'D' + str(d['id']), 'ref': d['ref'], 'type': 'deposit', 'tx_hash': d['tx_hash'], 'asset': d['sym'], 'ledger_code': lc, 'network': d['ncode'], 'network_name': d['nname'], 'address': d['recipient'],
                    'chain_amount': fm(amt), 'ledger_amount': fm(led) if led is not None else None, 'chain_amount_source': 'chain_provider', 'confirmations': conf, 'required_confirmations': need,
                    'timestamp': d['credited_at'] or d['detected_at'], 'status': rs, 'source_status': st, 'discrepancy': why, 'exceptions': exc.get(d['ref'], [])})
    wbase = '''SELECT w.*, a.symbol sym, an.ledger_code lc, n.network_code ncode, n.name nname, n.confirmations_required nreq, an.confirmations_override ov FROM crypto_withdrawals w
               JOIN asset_networks an ON an.id=w.asset_network_id JOIN crypto_assets a ON a.id=an.asset_id JOIN blockchain_networks n ON n.id=an.network_id ORDER BY w.id DESC LIMIT ?'''
    for w in db.execute(wbase, (limit,)):
        w = dict(w); lc = w['lc']; st = w['status']; why = None; amt = reg.from_minor(db, lc, int(w['amount']))
        if st == 'completed': rs = 'reconciled' if (w['tx_hash'] and w['completion_entry_id']) else 'mismatch'; why = None if rs == 'reconciled' else 'Completed without a transaction hash or ledger completion entry.'
        elif st in ('broadcast', 'confirming'): rs = 'confirming'
        elif st in ('pending_approval', 'approved', 'signing'): rs = 'pending'
        else: rs = 'failed'; why = w['status_reason']
        if w['ref'] in exc and rs != 'failed': rs = 'mismatch'; why = (why + ' ' if why else '') + 'Open reconciliation exception.'
        out.append({'id': 'W' + str(w['id']), 'ref': w['ref'], 'type': 'withdrawal', 'tx_hash': w['tx_hash'], 'asset': w['sym'], 'ledger_code': lc, 'network': w['ncode'], 'network_name': w['nname'], 'address': w['destination'],
                    'chain_amount': None, 'ledger_amount': amt, 'chain_amount_source': 'unavailable', 'confirmations': w['confirmations'], 'required_confirmations': w['ov'] or w['nreq'],
                    'timestamp': w['updated_at'] or w['created_at'], 'status': rs, 'source_status': st, 'discrepancy': why, 'exceptions': exc.get(w['ref'], [])})
    def keep(r, with_status):
        if asset and r['asset'] != asset: return False
        if network and r['network'] != network: return False
        if typ and r['type'] != typ: return False
        if with_status and status and r['status'] != status: return False
        ts = (r['timestamp'] or '')[:10]
        if date_from and ts < date_from: return False
        if date_to and ts > date_to: return False
        if q:
            ql = q.strip().lower()
            if ql not in (r['tx_hash'] or '').lower() and ql not in (r['address'] or '').lower() and ql not in (r['ref'] or '').lower(): return False
        return True
    base_rows = [r for r in out if keep(r, False)]; rows = [r for r in base_rows if keep(r, True)]
    rows.sort(key=lambda r: r['timestamp'] or '', reverse=True)
    counts = {s: sum(1 for r in base_rows if r['status'] == s) for s in RECON_STATUSES}
    size = max(1, min(int(size), 100)); page = max(1, int(page))
    return {'records': rows[(page - 1) * size: page * size], 'total': len(rows), 'page': page, 'size': size, 'counts': counts, 'all_total': len(base_rows),
            'assets': sorted({r['asset'] for r in out if r['asset']}), 'networks': sorted({r['network'] for r in out})}

def reconciliation_summary(db):
    allr = records(db, size=5)
    runs = all_(db, 'SELECT ref,status,started_at,finished_at FROM reconciliation_runs ORDER BY id DESC LIMIT 5')
    c = allr['counts']
    return {'total_records': allr['all_total'], 'reconciled': c['reconciled'], 'pending': c['pending'] + c['confirming'], 'problems': c['mismatch'] + c['failed'], 'recent': allr['records'],
            'runs_total': one(db, 'SELECT COUNT(*) c FROM reconciliation_runs')['c'], 'runs': runs}


# ---------------------------------------------------------------- one asset, everything the settings page needs
def network_usage(db, an_id):
    code = one(db, 'SELECT ledger_code FROM asset_networks WHERE id=?', an_id)['ledger_code']
    u = {'wallets': one(db, "SELECT COUNT(*) c FROM company_crypto_wallets WHERE asset_network_id=? AND public_address<>'NOT_CONFIGURED'", an_id)['c'],
         'deposits': one(db, 'SELECT COUNT(*) c FROM crypto_deposits WHERE asset_network_id=?', an_id)['c'],
         'deposit_requests': one(db, 'SELECT COUNT(*) c FROM deposit_requests WHERE asset_network_id=?', an_id)['c'],
         'withdrawals': one(db, 'SELECT COUNT(*) c FROM crypto_withdrawals WHERE asset_network_id=?', an_id)['c'],
         'open_intents': one(db, "SELECT COUNT(*) c FROM deposit_intents WHERE asset_network_id=? AND status='open'", an_id)['c'],
         'ledger_lines': one(db, 'SELECT COUNT(*) c FROM journal_lines WHERE asset_code=?', code)['c']}
    u['has_history'] = any(u[k] for k in ('wallets', 'deposits', 'deposit_requests', 'withdrawals', 'ledger_lines'))
    return u

def asset_settings(db, providers, asset_id):
    a = one(db, 'SELECT * FROM crypto_assets WHERE id=?', asset_id)
    if not a: raise ApiError(404, 'not_found', 'Asset not found')
    asset = cat.asset_view(db, a)
    for n in asset['networks']:
        row = one(db, 'SELECT n.id nid, n.explorer_url, n.chain_id, n.chain_type, n.confirmations_required, n.attribution_mode FROM blockchain_networks n JOIN asset_networks an ON an.network_id=n.id WHERE an.id=?', n['id'])
        shared = [r['symbol'] for r in db.execute('SELECT DISTINCT a.symbol FROM asset_networks an JOIN crypto_assets a ON a.id=an.asset_id WHERE an.network_id=? ORDER BY a.symbol', (row['nid'],))]
        n.update(network_id=row['nid'], shared_with=shared, explorer_url=row['explorer_url'], chain_id=row['chain_id'], chain_type=row['chain_type'], network_default_confirmations=row['confirmations_required'], classification='token' if n['contract_address'] else 'native',
                 usage=network_usage(db, n['id']), address_hint=cat.ADDRESS_HINTS.get(n['address_format']))
    an_ids = [n['id'] for n in asset['networks']]; wallets = []
    for w in all_(db, 'SELECT * FROM company_crypto_wallets WHERE asset_network_id IN(%s) ORDER BY asset_network_id, (status=\'retired\'), id' % ','.join('?' * len(an_ids)) if an_ids else 'SELECT * FROM company_crypto_wallets WHERE 0', *an_ids):
        info = cw.an_info(db, w['asset_network_id']); v = cw.view(db, w); v['updated_at'] = w['updated_at']; v['asset_network_id'] = w['asset_network_id']
        v['qr_available'] = cw.is_configured(w) and w['status'] != 'retired'
        ver = False
        if cw.is_configured(w):
            try: ver = bool(providers.wallets.controls_address(info['network_code'], w['public_address']))
            except Exception: ver = None                       # provider cannot answer (unimplemented live adapter / outage): reported as unavailable, never as verified
        v['verified'] = ver
        wallets.append(v)
    reqs = _who(db, [r for r in cw.list_requests(db, None) if r['asset_network_id'] in an_ids][:50])
    wids = [w['id'] for w in wallets]
    events = [e for e in cw.history(db, None, 300) if e.get('wallet_id') in wids][:60]
    ids = {e[k] for e in events for k in ('actor_id', 'approver_id') if e.get(k)}
    em = {u['id']: u['email'] for u in db.execute('SELECT id,email FROM users WHERE id IN(%s)' % ','.join('?' * len(ids)), tuple(ids))} if ids else {}
    events = [dict(e, actor_email=em.get(e.get('actor_id')), approver_email=em.get(e.get('approver_id'))) for e in events]
    return {'asset': asset, 'wallets': wallets, 'change_requests': reqs, 'history': events, 'environment': providers.env, 'provider_mode': providers.crypto_mode}


def wallet_qr_payload(db, providers, wallet_id):
    """The text encoded in the admin QR preview: the validated public address, exactly as stored. Refused for unset or retired wallets."""
    w = cw.get_wallet(db, wallet_id)
    if not cw.is_configured(w): raise ApiError(409, 'not_configured', 'This wallet has no address yet, so there is nothing to encode.')
    if w['status'] == 'retired': raise ApiError(409, 'retired', 'Retired wallets do not have a QR code.')
    cw.validate_wallet_address(cw.an_info(db, w['asset_network_id']), w['public_address'])
    return w['public_address'], w
