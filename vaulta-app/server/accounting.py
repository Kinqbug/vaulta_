"""Double-entry general ledger (M2). Sits beside the customer subledger (ledger.py / transactions).

Rules enforced here and by DB triggers (migration 002):
  * every entry has >= 2 lines and debits == credits **per asset** (BTC can never balance against USD);
  * amounts are positive integers in the asset's minor units (bool/float/str rejected);
  * posted entries are immutable; corrections are reversal entries (unique per original);
  * entries are idempotent on idempotency_key; posting into a closed period is refused;
  * account_balances is a cache owned by this module and is re-derived from lines by verify_integrity().
Postings run inside db.tx(); callers must not call external services between the lines of one entry."""
import calendar
from datetime import date
from . import util
from .util import ApiError
from .db import one, all_, tx

SYSTEM = {   # key: (code, name, type)
    'CUSTODY_FIAT': ('1000', 'Custody: settlement funds', 'asset'),
    'CUSTODY_CRYPTO': ('1100', 'Custody: crypto assets', 'asset'),
    'CUSTODY_INVEST': ('1200', 'Custody: investment positions', 'asset'),
    'CUSTOMER_INVEST_LIAB': ('2200', 'Customer investment holdings (liability)', 'liability'),
    'TRANSFER_CLEARING': ('2900', 'Internal transfer clearing', 'liability'),
    'CRYPTO_WD_CLEARING': ('2910', 'Crypto withdrawals in flight (liability)', 'liability'),
    'CRYPTO_CONVERSION': ('2920', 'Crypto/cash conversion clearing', 'liability'),
    'SUSPENSE': ('2990', 'Suspense / reconciliation exceptions', 'liability'),
    'EQUITY': ('3000', 'Owner equity', 'equity'),
    'FEE_INCOME': ('4000', 'Fee income', 'income'),
    'INTEREST_EXPENSE': ('5000', 'Customer interest expense', 'expense'),
    'NETWORK_FEE_EXPENSE': ('5100', 'Blockchain network fees paid', 'expense'),
    'ADJUSTMENTS': ('5900', 'Manual adjustments', 'expense')}
NORMAL = {'asset': 'debit', 'expense': 'debit', 'liability': 'credit', 'equity': 'credit', 'income': 'credit'}

def ensure_chart(db):
    with tx(db):
        for code, name, typ in SYSTEM.values():
            db.execute('INSERT OR IGNORE INTO gl_accounts(code,name,type,normal_side,is_system,created_at) VALUES(?,?,?,?,1,?)', (code, name, typ, NORMAL[typ], util.now()))

def account_id(db, key_or_code):
    code = SYSTEM[key_or_code][0] if key_or_code in SYSTEM else key_or_code
    r = one(db, 'SELECT id FROM gl_accounts WHERE code=?', code)
    if not r: raise ApiError(500, 'gl_account_missing', f'GL account {code} does not exist')
    return r['id']

def customer_account(db, subledger_account_id):
    """One liability GL account per customer subledger account (cash / savings / retirement)."""
    r = one(db, 'SELECT id FROM gl_accounts WHERE owner_account_id=?', subledger_account_id)
    if r: return r['id']
    a = one(db, 'SELECT * FROM accounts WHERE id=?', subledger_account_id)
    if not a: raise ApiError(404, 'not_found', 'Subledger account not found')
    cur = db.execute('INSERT INTO gl_accounts(code,name,type,normal_side,owner_user_id,owner_account_id,created_at) VALUES(?,?,?,?,?,?,?)',
                     (f'CUST-{subledger_account_id}', f"Customer {a['kind']} liability (user {a['user_id']})", 'liability', 'credit', a['user_id'], subledger_account_id, util.now()))
    return cur.lastrowid

def ensure_period(db, ts):
    y, m = int(ts[:4]), int(ts[5:7]); code = f'{y:04d}-{m:02d}'
    r = one(db, 'SELECT id,status FROM accounting_periods WHERE code=?', code)
    if r: return r['id']
    last = calendar.monthrange(y, m)[1]
    return db.execute('INSERT INTO accounting_periods(code,starts_on,ends_on) VALUES(?,?,?)', (code, f'{y:04d}-{m:02d}-01', f'{y:04d}-{m:02d}-{last:02d}')).lastrowid

def _int_amount(v):
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0: raise ApiError(422, 'invalid_amount', 'GL amounts must be positive integers in minor units')
    return v

def post_entry(db, *, entry_type, lines, idem=None, description='', source_type=None, source_ref=None, created_by=None, posted_at=None, reverses=None):
    """lines: [(gl_account_id, 'debit'|'credit', asset_code, amount_minor_units)]. Returns the entry id (existing id on idempotent replay)."""
    if idem:
        e = one(db, 'SELECT id FROM journal_entries WHERE idempotency_key=?', idem)
        if e: return e['id']
    if len(lines) < 2: raise ApiError(422, 'unbalanced_entry', 'An entry needs at least two lines')
    tot = {}
    for _, d, asset, amt in lines:
        if d not in ('debit', 'credit'): raise ApiError(422, 'invalid_direction', 'direction must be debit or credit')
        _int_amount(amt); t = tot.setdefault(asset, [0, 0]); t[0 if d == 'debit' else 1] += amt
    bad = {a: {'debits': str(t[0]), 'credits': str(t[1])} for a, t in tot.items() if t[0] != t[1]}
    if bad: raise ApiError(422, 'unbalanced_entry', 'Debits must equal credits for every asset', bad)
    posted_at = posted_at or util.now()
    with tx(db):
        for asset in tot:
            if not one(db, 'SELECT 1 FROM ledger_assets WHERE code=?', asset): raise ApiError(422, 'unknown_asset', f'Unknown ledger asset {asset}')
        for acct, *_ in lines:
            a = one(db, 'SELECT status FROM gl_accounts WHERE id=?', acct)
            if not a or a['status'] != 'active': raise ApiError(422, 'invalid_gl_account', 'GL account missing or closed')
        period = ensure_period(db, posted_at)
        cur = db.execute('INSERT INTO journal_entries(ref,idempotency_key,entry_type,description,source_type,source_ref,period_id,reverses_entry_id,created_by,line_count,sealed,posted_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,0,?,?)',
                         (util.new_ref('JE'), idem, entry_type, description, source_type, source_ref, period, reverses, created_by, len(lines), posted_at, util.now()))
        eid = cur.lastrowid
        for n, (acct, d, asset, amt) in enumerate(lines, 1):
            lid = db.execute('INSERT INTO journal_lines(entry_id,line_no,gl_account_id,asset_code,direction,amount) VALUES(?,?,?,?,?,?)', (eid, n, acct, asset, d, str(amt))).lastrowid
            row = one(db, 'SELECT balance FROM account_balances WHERE gl_account_id=? AND asset_code=?', acct, asset)
            new = (int(row['balance']) if row else 0) + (amt if d == 'debit' else -amt)
            db.execute('INSERT INTO account_balances(gl_account_id,asset_code,balance,last_line_id,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(gl_account_id,asset_code) DO UPDATE SET balance=excluded.balance,last_line_id=excluded.last_line_id,updated_at=excluded.updated_at',
                       (acct, asset, str(new), lid, util.now()))
        db.execute('UPDATE journal_entries SET sealed=1 WHERE id=?', (eid,))
        return eid

def reverse_entry(db, entry_id, reason, actor_id=None):
    if not (reason or '').strip(): raise ApiError(422, 'reason_required', 'A reason is required.')
    with tx(db):
        e = one(db, 'SELECT * FROM journal_entries WHERE id=?', entry_id)
        if not e: raise ApiError(404, 'not_found', 'Entry not found')
        if e['reverses_entry_id']: raise ApiError(409, 'already_a_reversal', 'A reversal cannot itself be reversed; post a new entry.')
        existing = one(db, 'SELECT id FROM journal_entries WHERE reverses_entry_id=?', entry_id)
        if existing: return existing['id']
        ls = all_(db, 'SELECT * FROM journal_lines WHERE entry_id=? ORDER BY line_no', entry_id)
        flip = {'debit': 'credit', 'credit': 'debit'}
        return post_entry(db, entry_type='reversal', lines=[(l['gl_account_id'], flip[l['direction']], l['asset_code'], int(l['amount'])) for l in ls],
                          idem=f'reversal:{entry_id}', description=f"Reversal of {e['ref']}: {reason}", source_type=e['source_type'], source_ref=e['source_ref'], created_by=actor_id, reverses=entry_id)

# ---------------- reads ----------------
def balance(db, gl_account, asset_code='USD'):
    """Balance expressed on the account's normal side (a liability's credit balance is positive)."""
    aid = gl_account if isinstance(gl_account, int) else account_id(db, gl_account)
    a = one(db, 'SELECT normal_side FROM gl_accounts WHERE id=?', aid)
    r = one(db, 'SELECT balance FROM account_balances WHERE gl_account_id=? AND asset_code=?', aid, asset_code)
    raw = int(r['balance']) if r else 0
    return raw if a['normal_side'] == 'debit' else -raw

def _recompute(db):
    sums = {}
    for l in db.execute('SELECT gl_account_id,asset_code,direction,amount FROM journal_lines'):
        k = (l['gl_account_id'], l['asset_code']); sums[k] = sums.get(k, 0) + (int(l['amount']) if l['direction'] == 'debit' else -int(l['amount']))
    return sums

def trial_balance(db):
    rows = []; sums = _recompute(db); tot = {}
    for (aid, asset), raw in sorted(sums.items()):
        a = one(db, 'SELECT code,name,type FROM gl_accounts WHERE id=?', aid)
        d, c = (raw, 0) if raw > 0 else (0, -raw)
        rows.append({'account': a['code'], 'name': a['name'], 'type': a['type'], 'asset': asset, 'debit': str(d), 'credit': str(c)})
        t = tot.setdefault(asset, [0, 0]); t[0] += d; t[1] += c
    return {'rows': rows, 'totals': {a: {'debit': str(t[0]), 'credit': str(t[1]), 'balanced': t[0] == t[1]} for a, t in tot.items()}}

def verify_integrity(db):
    """Recompute everything from immutable lines. Any problem is reported, never auto-corrected."""
    errors = []
    per = {}
    for l in db.execute('SELECT entry_id,asset_code,direction,amount FROM journal_lines'):
        t = per.setdefault((l['entry_id'], l['asset_code']), [0, 0]); t[0 if l['direction'] == 'debit' else 1] += int(l['amount'])
    for (eid, asset), (d, c) in per.items():
        if d != c: errors.append({'type': 'unbalanced_entry', 'entry_id': eid, 'asset': asset, 'debits': str(d), 'credits': str(c)})
    for e in db.execute('SELECT id,line_count,sealed FROM journal_entries'):
        n = one(db, 'SELECT COUNT(*) c FROM journal_lines WHERE entry_id=?', e['id'])['c']
        if not e['sealed'] or n != e['line_count']: errors.append({'type': 'incomplete_entry', 'entry_id': e['id']})
    sums = _recompute(db); cached = {(r['gl_account_id'], r['asset_code']): int(r['balance']) for r in db.execute('SELECT * FROM account_balances')}
    for k in set(sums) | set(cached):
        if sums.get(k, 0) != cached.get(k, 0): errors.append({'type': 'balance_cache_mismatch', 'gl_account_id': k[0], 'asset': k[1], 'derived': str(sums.get(k, 0)), 'cached': str(cached.get(k, 0))})
    return {'ok': not errors, 'errors': errors}

def reconcile_subledger(db):
    """GL customer liability vs customer subledger posted balance, per account. Mismatches are reported as exceptions, never silently fixed."""
    from . import ledger
    out = []
    for a in all_(db, 'SELECT id FROM accounts'):
        posted = ledger.balance(db, a['id'])['posted_cents']
        gl = one(db, 'SELECT id FROM gl_accounts WHERE owner_account_id=?', a['id'])
        glbal = balance(db, gl['id'], 'USD') if gl else 0
        if posted != glbal: out.append({'account_id': a['id'], 'subledger_cents': posted, 'gl_cents': glbal, 'variance_cents': posted - glbal})
    return out

def close_period(db, code, actor_id, reason):
    from .audit import audit
    if not (reason or '').strip(): raise ApiError(422, 'reason_required', 'A reason is required.')
    with tx(db):
        p = one(db, 'SELECT * FROM accounting_periods WHERE code=?', code)
        if not p: raise ApiError(404, 'not_found', 'Period not found')
        if p['status'] == 'closed': raise ApiError(409, 'already_closed', 'Period is already closed')
        if p['ends_on'] >= util.now()[:10]: raise ApiError(409, 'period_not_ended', 'A period can only be closed after it has ended.')
        v = verify_integrity(db)
        if not v['ok']: raise ApiError(409, 'integrity_errors', 'Resolve ledger integrity errors before closing the period.', v['errors'][:20])
        db.execute("UPDATE accounting_periods SET status='closed',closed_by=?,closed_at=? WHERE id=?", (actor_id, util.now(), p['id']))
        audit(db, actor_id, 'ledger.period_closed', 'accounting_period', code, {'status': 'open'}, {'status': 'closed'}, reason)

# ---------------- subledger -> GL mirror ----------------
def _fees(db, txn_id): return one(db, 'SELECT COALESCE(SUM(amount_cents),0) f FROM transaction_fees WHERE transaction_id=?', txn_id)['f']

def mirror_transaction(db, txn_id, event='completed'):
    """Post (or reverse) the GL entry for a subledger transaction. Idempotent. Pending/processing rows are holds, not postings."""
    t = one(db, 'SELECT * FROM transactions WHERE id=?', txn_id)
    if not t or t['currency'] != 'USD': return None
    key = f'txn:{txn_id}:completed'
    if event == 'reversed':
        orig = one(db, 'SELECT id FROM journal_entries WHERE idempotency_key=?', key)
        if not orig: orig = {'id': mirror_transaction(db, txn_id, 'completed')}
        return reverse_entry(db, orig['id'], f"subledger transaction {t['ref']} reversed")
    if t['status'] not in ('completed', 'reversed'): return None
    cust = customer_account(db, t['account_id']); amt = t['amount_cents']; fee = _fees(db, txn_id); S = account_id
    typ, d = t['type'], t['direction']
    if typ in ('deposit', 'withdrawal') and '"crypto_conversion"' in (t['metadata'] or ''):       # cash side of a crypto<->cash conversion: the counter-entry is the conversion clearing account, not fiat custody
        clr = S(db, 'CRYPTO_CONVERSION')
        lines = [(clr, 'debit', 'USD', amt), (cust, 'credit', 'USD', amt)] if d == 'credit' else [(cust, 'debit', 'USD', amt), (clr, 'credit', 'USD', amt)]
        return post_entry(db, entry_type=f'subledger.crypto_{typ}', lines=lines, idem=key, description=f"crypto conversion {t['ref']}", source_type='transaction', source_ref=t['ref'], posted_at=t['completed_at'] or util.now())
    if typ == 'deposit' and d == 'credit':           # customer net + fee received in custody
        lines = [(S(db, 'CUSTODY_FIAT'), 'debit', 'USD', amt + fee), (cust, 'credit', 'USD', amt)] + ([(S(db, 'FEE_INCOME'), 'credit', 'USD', fee)] if fee else [])
    elif typ == 'withdrawal' and d == 'debit':
        lines = [(cust, 'debit', 'USD', amt), (S(db, 'CUSTODY_FIAT'), 'credit', 'USD', amt)]
    elif typ in ('investment', 'trade') and d == 'debit':   # amount includes the fee; the rest becomes an investment position
        n = amt - fee
        lines = [(cust, 'debit', 'USD', amt), (S(db, 'CUSTOMER_INVEST_LIAB'), 'credit', 'USD', n)] + ([(S(db, 'FEE_INCOME'), 'credit', 'USD', fee)] if fee else []) + \
                [(S(db, 'CUSTODY_INVEST'), 'debit', 'USD', n), (S(db, 'CUSTODY_FIAT'), 'credit', 'USD', n)]
    elif typ == 'trade' and d == 'credit':           # sale proceeds net of fee
        n = amt + fee
        lines = [(S(db, 'CUSTOMER_INVEST_LIAB'), 'debit', 'USD', n), (cust, 'credit', 'USD', amt)] + ([(S(db, 'FEE_INCOME'), 'credit', 'USD', fee)] if fee else []) + \
                [(S(db, 'CUSTODY_FIAT'), 'debit', 'USD', n), (S(db, 'CUSTODY_INVEST'), 'credit', 'USD', n)]
    elif typ == 'interest' and d == 'credit':
        lines = [(S(db, 'INTEREST_EXPENSE'), 'debit', 'USD', amt), (cust, 'credit', 'USD', amt)]
    elif typ == 'transfer':
        clr = S(db, 'TRANSFER_CLEARING')
        lines = [(cust, 'debit', 'USD', amt), (clr, 'credit', 'USD', amt)] if d == 'debit' else [(clr, 'debit', 'USD', amt), (cust, 'credit', 'USD', amt)]
    elif d == 'credit':                              # adjustment / refund / other credits
        lines = [(S(db, 'ADJUSTMENTS'), 'debit', 'USD', amt), (cust, 'credit', 'USD', amt)]
    else:                                            # fee and other debits
        lines = [(cust, 'debit', 'USD', amt), (S(db, 'FEE_INCOME' if typ == 'fee' else 'ADJUSTMENTS'), 'credit', 'USD', amt)]
    return post_entry(db, entry_type=f'subledger.{typ}', lines=lines, idem=key, description=f"{typ} {t['ref']}", source_type='transaction', source_ref=t['ref'],
                      posted_at=t['completed_at'] or util.now())

def backfill(db):
    """Mirror subledger rows that pre-date the GL (or were written by older code). Idempotent."""
    ensure_chart(db); n = 0
    for t in all_(db, "SELECT id,status FROM transactions WHERE status IN('completed','reversed') ORDER BY id"):
        with tx(db):
            key = f"txn:{t['id']}:completed"
            if not one(db, 'SELECT 1 FROM journal_entries WHERE idempotency_key=?', key): mirror_transaction(db, t['id'], 'completed'); n += 1
            if t['status'] == 'reversed':
                orig = one(db, 'SELECT id FROM journal_entries WHERE idempotency_key=?', key)
                if not one(db, 'SELECT 1 FROM journal_entries WHERE reverses_entry_id=?', orig['id']): mirror_transaction(db, t['id'], 'reversed')
    return n
