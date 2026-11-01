"""Crypto reconciliation: blockchain balance  VS  Vaulta wallet records  VS  customer liabilities  VS  ledger.

For every asset-network the run compares
  chain holdings      sum of get_balance() over EVERY company wallet of that asset-network (deposit, hot, cold, treasury... and retired ones)
  ledger custody      GL CUSTODY_CRYPTO balance
  customer liability  sum of every customer's crypto GL balance (+ unattributed suspense + withdrawals in flight)
and raises an exception (never an auto-correction) for: ledger_mismatch, insolvency (holdings < liabilities), customer_balance_mismatch,
missing_transaction (on-chain transfer we never recorded), duplicate_credit, incorrect_customer_attribution, unmatched_transaction,
unexpected_asset, wrong_network, ledger_integrity and post_credit_reorg. Wallet-to-wallet movements never touch customer balances, and
because holdings are summed across all company wallets a sweep to cold storage does not create a false mismatch.
"""
import json
from . import util, accounting as acc, crypto_ledger as cl, crypto_wallets as cw, chain_addresses as ca, crypto_deposits as cd
from .util import ApiError
from .db import one, all_, tx
from .audit import audit

CRITICAL = ('insolvency', 'ledger_integrity', 'post_credit_reorg', 'duplicate_credit')

def raise_exception(db, dedupe_key, kind, severity, ledger_code, details, run_id=None):
    """Open an exception, or refresh the existing open one with the same key (so a persistent problem is one row, not one per run)."""
    with tx(db):
        ex = one(db, "SELECT id FROM reconciliation_exceptions WHERE dedupe_key=? AND status IN('open','investigating')", dedupe_key)
        if ex:
            db.execute('UPDATE reconciliation_exceptions SET last_seen_at=?,details=?,run_id=COALESCE(?,run_id) WHERE id=?', (util.now(), util.jdump(details), run_id, ex['id'])); return ex['id'], False
        eid = db.execute('INSERT INTO reconciliation_exceptions(run_id,dedupe_key,kind,severity,ledger_code,details,created_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?)',
                         (run_id, dedupe_key, kind, severity if kind not in CRITICAL else 'critical', ledger_code, util.jdump(details), util.now(), util.now())).lastrowid
        audit(db, None, 'crypto.reconciliation_exception', 'reconciliation_exception', eid, None, {'kind': kind, 'ledger_code': ledger_code}, None)
        return eid, True

def chain_holdings(db, providers, info):
    """Sum of on-chain balances (at the asset's own confirmation policy) across all company wallets for this asset-network."""
    seen, tot = set(), 0
    for w in all_(db, "SELECT * FROM company_crypto_wallets WHERE asset_network_id=? AND public_address<>'NOT_CONFIGURED'", info['an_id']):
        k = ca.address_key(info['address_format'], w['public_address'])
        if k in seen: continue
        seen.add(k)
        tot += providers.chain.get_balance(info['network_code'], w['public_address'], info['contract_address'], cw.effective(db, w)['confirmations'])
    return tot

def expected_customer_balances(db, ledger_code):
    """Independent re-derivation from the deposit and withdrawal tables (NOT from the ledger) of what each customer should hold."""
    an = one(db, 'SELECT id FROM asset_networks WHERE ledger_code=?', ledger_code); exp = {}
    for d in db.execute("SELECT user_id,amount FROM crypto_deposits WHERE asset_network_id=? AND status='credited'", (an['id'],)): exp[d['user_id']] = exp.get(d['user_id'], 0) + int(d['amount'])
    for w in db.execute("SELECT user_id,amount,fee FROM crypto_withdrawals WHERE asset_network_id=? AND status IN('pending_approval','approved','signing','broadcast','confirming','completed')", (an['id'],)): exp[w['user_id']] = exp.get(w['user_id'], 0) - int(w['amount']) - int(w['fee'])
    for c in db.execute('SELECT user_id,direction,units FROM crypto_conversions WHERE ledger_code=? AND reversed_at IS NULL', (ledger_code,)):     # crypto <-> Account Balance conversions (customers' crypto liability is transient)
        exp[c['user_id']] = exp.get(c['user_id'], 0) + (int(c['units']) if c['direction'] == 'withdrawal' else -int(c['units']))
    return exp

def reconcile_asset(db, providers, info, run_id=None):
    code = info['ledger_code']; found = []
    def X(key, kind, sev, details): found.append(raise_exception(db, f'{key}', kind, sev, code, details, run_id)[0])
    chain = chain_holdings(db, providers, info)
    custody = acc.balance(db, 'CUSTODY_CRYPTO', code); susp = acc.balance(db, 'SUSPENSE', code); clearing = acc.balance(db, 'CRYPTO_WD_CLEARING', code)
    liab = cl.total_customer_liability(db, code); inv = acc.balance(db, 'CRYPTO_CONVERSION', code)      # inv: crypto the company acquired by converting customer deposits to Account Balance; it must still be held (or swept with a ledger entry)
    owed = liab + susp + clearing + inv
    if chain != custody: X(f'ledger_mismatch:{code}', 'ledger_mismatch', 'high', {'chain_holdings': str(chain), 'ledger_custody': str(custody), 'difference': str(chain - custody)})
    if chain < owed: X(f'insolvency:{code}', 'insolvency', 'critical', {'chain_holdings': str(chain), 'customer_liabilities': str(liab), 'suspense': str(susp), 'withdrawals_in_flight': str(clearing), 'converted_inventory': str(inv), 'shortfall': str(owed - chain)})
    elif custody < owed: X(f'custody_below_liabilities:{code}', 'ledger_mismatch', 'critical', {'ledger_custody': str(custody), 'owed': str(owed)})
    exp = expected_customer_balances(db, code)
    users = set(exp) | {int(r['code'][len('CRYPTO-U'):]) for r in db.execute("SELECT g.code FROM gl_accounts g JOIN account_balances b ON b.gl_account_id=g.id WHERE g.code LIKE 'CRYPTO-U%' AND b.asset_code=?", (code,))}
    for uid in users:
        have = cl.balance(db, uid, code)
        if have != exp.get(uid, 0): X(f'customer_balance_mismatch:{code}:{uid}', 'customer_balance_mismatch', 'high', {'user_id': uid, 'ledger': str(have), 'expected_from_records': str(exp.get(uid, 0))})
    # on-chain transfers to our wallets that we never recorded
    net = dict(one(db, 'SELECT * FROM blockchain_networks WHERE id=?', info['network_id'])); seen = set()
    for w in all_(db, "SELECT * FROM company_crypto_wallets WHERE asset_network_id=? AND public_address<>'NOT_CONFIGURED' AND wallet_type IN('deposit','customer_deposit')", info['an_id']):
        k = ca.address_key(net['address_format'], w['public_address'])
        if k in seen: continue
        seen.add(k)
        for tr in providers.chain.list_incoming(net['network_code'], w['public_address'], 0):
            if (tr.contract or '').lower() != (info['contract_address'] or '').lower(): continue
            if tr.sender and cd._is_company_address(db, net, tr.sender): continue
            h = tr.tx_hash.lower() if net['address_format'] in ('evm', 'btc') else tr.tx_hash
            if not one(db, 'SELECT 1 FROM crypto_deposits WHERE network_id=? AND tx_hash=? AND event_index=?', net['id'], h, tr.event_index):
                X(f'missing_transaction:{net["id"]}:{h}:{tr.event_index}', 'missing_transaction', 'high', {'tx_hash': h, 'event_index': tr.event_index, 'amount': str(tr.amount), 'confirmations': tr.confirmations})
    for d in all_(db, "SELECT * FROM crypto_deposits WHERE asset_network_id=? AND status='credited'", info['an_id']):
        n = one(db, "SELECT COUNT(*) c FROM journal_entries WHERE source_type='crypto_deposit' AND source_ref=? AND entry_type='crypto.deposit_credit'", d['ref'])['c']
        if n != 1: X(f"duplicate_credit:{d['id']}", 'duplicate_credit', 'critical', {'deposit': d['ref'], 'credit_entries': n})
        gl = one(db, "SELECT gl_account_id FROM journal_lines WHERE entry_id=? AND direction='credit'", d['credit_entry_id'])
        if not gl or gl['gl_account_id'] != cl.customer_account(db, d['user_id']): X(f"incorrect_attribution:{d['id']}", 'incorrect_customer_attribution', 'critical', {'deposit': d['ref'], 'user_id': d['user_id']})
    for d in all_(db, "SELECT * FROM crypto_deposits WHERE asset_network_id=? AND status='manual_review' AND user_id IS NULL AND detected_at<?", info['an_id'], util.ts_after(hours=-24)):
        X(f"unmatched:{d['id']}", 'unmatched_transaction', 'medium', {'deposit': d['ref'], 'amount': d['amount'], 'reason': d['status_reason']})
    return {'ledger_code': code, 'chain_holdings': str(chain), 'ledger_custody': str(custody), 'customer_liabilities': str(liab), 'unattributed_suspense': str(susp), 'withdrawals_in_flight': str(clearing), 'converted_inventory': str(inv),
            'solvent': chain >= owed, 'ledger_matches_chain': chain == custody, 'exceptions': len(found)}

def reconcile(db, providers, actor_id=None, refresh=True):
    from . import crypto_withdrawals as cwd
    ref = util.new_ref('REC'); rid = db.execute('INSERT INTO reconciliation_runs(ref,started_at,started_by) VALUES(?,?,?)', (ref, util.now(), actor_id)).lastrowid
    summary = {'assets': [], 'global': []}; err = None
    try:
        if refresh:
            cd.run_once(db, providers); cwd.advance_all(db, providers)
        for r in all_(db, 'SELECT an.id FROM asset_networks an JOIN blockchain_networks n ON n.id=an.network_id WHERE n.address_format IS NOT NULL'):
            summary['assets'].append(reconcile_asset(db, providers, cw.an_info(db, r['id']), rid))
        v = acc.verify_integrity(db)
        if not v['ok']: raise_exception(db, 'ledger_integrity', 'ledger_integrity', 'critical', None, {'errors': v['errors'][:10]}, rid); summary['global'].append('ledger_integrity')
        for d in all_(db, "SELECT * FROM crypto_deposits WHERE status IN('unsupported_asset','wrong_network')"):
            raise_exception(db, f"{'unexpected_asset' if d['status']=='unsupported_asset' else 'wrong_network'}:{d['id']}", 'unexpected_asset' if d['status'] == 'unsupported_asset' else 'wrong_network', 'high', None,
                            {'deposit': d['ref'], 'tx_hash': d['tx_hash'], 'amount': d['amount'], 'contract': d['contract'], 'reason': d['status_reason'], 'note': 'funds are NOT credited; recovery is a manual custody procedure'}, rid)
    except ApiError as e: err = e.code; summary['error'] = e.code
    n = one(db, "SELECT COUNT(*) c FROM reconciliation_exceptions WHERE status IN('open','investigating')")['c']
    st = 'error' if err else 'exceptions' if n else 'clean'
    db.execute('UPDATE reconciliation_runs SET finished_at=?,status=?,summary=? WHERE id=?', (util.now(), st, util.jdump(summary), rid))
    return {'ref': ref, 'status': st, 'open_exceptions': n, 'summary': summary}

def check_solvency(db, providers, ledger_code):
    """Financial-integrity invariant: total held by Vaulta >= total owed to customers (+ unattributed + in flight)."""
    info = cw.an_info(db, ledger_code=ledger_code); held = chain_holdings(db, providers, info)
    owed = cl.total_customer_liability(db, ledger_code) + acc.balance(db, 'SUSPENSE', ledger_code) + acc.balance(db, 'CRYPTO_WD_CLEARING', ledger_code) + acc.balance(db, 'CRYPTO_CONVERSION', ledger_code)
    return held >= owed, held, owed

def list_runs(db, limit=20): return [dict(r, summary=json.loads(r['summary'])) for r in db.execute('SELECT * FROM reconciliation_runs ORDER BY id DESC LIMIT ?', (limit,))]
def list_exceptions(db, status=None, limit=200):
    return [dict(r, details=json.loads(r['details'])) for r in db.execute('SELECT * FROM reconciliation_exceptions' + (' WHERE status=?' if status else '') + ' ORDER BY id DESC LIMIT ?', (*([status] if status else []), limit))]

def resolve_exception(db, actor, exc_id, status, note):
    if status not in ('investigating', 'resolved', 'waived'): raise ApiError(422, 'invalid_status', 'status must be investigating, resolved or waived')
    note = cw._reason(note, 10)
    with tx(db):
        e = one(db, 'SELECT * FROM reconciliation_exceptions WHERE id=?', exc_id)
        if not e: raise ApiError(404, 'not_found', 'Exception not found')
        if e['status'] in ('resolved', 'waived'): raise ApiError(409, 'invalid_state', 'Already closed.')
        if status == 'waived' and e['severity'] == 'critical': raise ApiError(409, 'cannot_waive', 'Critical exceptions cannot be waived; fix the cause and resolve.')
        db.execute('UPDATE reconciliation_exceptions SET status=?,resolution_note=?,resolved_by=?,resolved_at=? WHERE id=?', (status, note, actor['id'], util.now() if status != 'investigating' else None, exc_id))
        audit(db, actor['id'], 'crypto.reconciliation_exception_' + status, 'reconciliation_exception', exc_id, {'status': e['status']}, {'status': status}, note)
