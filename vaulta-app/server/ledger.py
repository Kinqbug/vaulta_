"""Double-entry-style ledger helpers. Balances are *derived* from completed ledger rows, never cached,
so they cannot drift. Money is integer cents."""
import json, hashlib
from . import util, accounting
from .util import ApiError
from .db import one, val, tx

TRANSITIONS = {'pending': {'processing', 'completed', 'failed', 'cancelled'}, 'processing': {'completed', 'failed', 'cancelled'},
               'completed': {'reversed'}, 'failed': set(), 'cancelled': set(), 'reversed': set()}
HOLDING = ('pending', 'processing')

def account_for(db, user_id, kind='cash'):
    a = one(db, 'SELECT * FROM accounts WHERE user_id=? AND kind=? AND status="active" ORDER BY id LIMIT 1', user_id, kind)
    if a: return dict(a)
    names = {'cash': 'Cash', 'savings': 'High-Yield Savings', 'retirement': 'Retirement'}
    from .rbac import setting
    apy = setting(db, 'savings_apy_bps') if kind == 'savings' else 0
    cur = db.execute('INSERT INTO accounts(user_id,kind,name,apy_bps,created_at) VALUES(?,?,?,?,?)', (user_id, kind, names[kind], apy, util.now()))
    return dict(one(db, 'SELECT * FROM accounts WHERE id=?', cur.lastrowid))

def balance(db, account_id):
    posted = val(db, "SELECT COALESCE(SUM(CASE direction WHEN 'credit' THEN amount_cents ELSE -amount_cents END),0) FROM transactions WHERE account_id=? AND status='completed'", account_id)
    held = val(db, "SELECT COALESCE(SUM(amount_cents),0) FROM transactions WHERE account_id=? AND direction='debit' AND status IN('pending','processing')", account_id)
    acct = one(db, 'SELECT user_id,kind FROM accounts WHERE id=?', account_id)
    reserved = 0
    if acct and acct['kind'] == 'cash':
        reserved = val(db, "SELECT COALESCE(SUM(reserved_cents),0) FROM orders WHERE user_id=? AND status IN('pending','open','partially_filled')", acct['user_id'])
    return {'posted_cents': posted, 'held_cents': held, 'reserved_cents': reserved, 'available_cents': posted - held - reserved}

def post(db, *, user_id, account_id, type, direction, amount_cents, status='pending', reference=None, metadata=None,
         idem=None, non_live=True, allow_overdraft=False, fees=()):
    if amount_cents <= 0: raise ApiError(422, 'invalid_amount', 'Amount must be positive')
    with tx(db):
        if direction == 'debit' and status in (*HOLDING, 'completed') and not allow_overdraft:
            if balance(db, account_id)['available_cents'] < amount_cents:
                raise ApiError(409, 'insufficient_funds', 'Insufficient available funds.')
        t = util.now()
        cur = db.execute('INSERT INTO transactions(ref,user_id,account_id,type,direction,amount_cents,currency,status,reference,idempotency_key,metadata,non_live,created_at,updated_at,completed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (util.new_ref('TXN'), user_id, account_id, type, direction, amount_cents, 'USD', status, reference, idem, util.jdump(metadata or {}), 1 if non_live else 0, t, t, t if status == 'completed' else None))
        for kind, c in fees:
            if c: db.execute('INSERT INTO transaction_fees(transaction_id,amount_cents,kind,created_at) VALUES(?,?,?,?)', (cur.lastrowid, c, kind, t))
        if status == 'completed': accounting.mirror_transaction(db, cur.lastrowid, 'completed')     # GL entry in the same DB transaction
        return cur.lastrowid

def transition(db, txn_id, new, reference=None):
    with tx(db):
        t = one(db, 'SELECT * FROM transactions WHERE id=?', txn_id)
        if not t: raise ApiError(404, 'not_found', 'Transaction not found')
        if new not in TRANSITIONS[t['status']]:
            raise ApiError(409, 'invalid_transition', f"Cannot move a {t['status']} transaction to {new}.")
        if new == 'reversed' and t['direction'] == 'credit':
            if balance(db, t['account_id'])['available_cents'] < t['amount_cents']:
                raise ApiError(409, 'cannot_reverse', 'Reversal would overdraw the account; resolve spent funds first.')
        now = util.now()
        db.execute('UPDATE transactions SET status=?,updated_at=?,completed_at=COALESCE(?,completed_at),reference=COALESCE(?,reference) WHERE id=?',
                   (new, now, now if new == 'completed' else None, reference, txn_id))
        if new in ('completed', 'reversed'): accounting.mirror_transaction(db, txn_id, new)       # GL entry / reversal, same DB transaction

def idempotent(db, user_id, key, scope, fingerprint_obj, fn):
    """Run fn() at most once per (user,key,scope); replays return the original response."""
    if not key: return fn()
    if len(key) > 100: raise ApiError(422, 'bad_idempotency_key', 'Idempotency-Key too long')
    fp = hashlib.sha256(util.jdump(fingerprint_obj).encode()).hexdigest()
    with tx(db):
        row = one(db, 'SELECT response FROM idempotency_keys WHERE user_id=? AND key=? AND scope=?', user_id, key, scope)
        if row:
            data = json.loads(row['response'])
            if data['fp'] != fp: raise ApiError(422, 'idempotency_key_reused', 'This Idempotency-Key was used with different parameters.')
            data['resp']['idempotent_replay'] = True
            return data['resp']
        resp = fn()
        db.execute('INSERT INTO idempotency_keys(user_id,key,scope,response,created_at) VALUES(?,?,?,?,?)', (user_id, key, scope, json.dumps({'fp': fp, 'resp': resp}), util.now()))
        return resp

def txn_view(r):
    r = dict(r); r['amount'] = util.cstr(r.pop('amount_cents'))
    r['metadata'] = json.loads(r.get('metadata') or '{}'); r['non_live'] = bool(r['non_live'])
    return r
