"""Double-entry postings for crypto. Customer crypto balances ARE general-ledger balances; nothing here (or anywhere) keeps a second balance.

  received (deposit confirmed on-chain)   Dr CUSTODY_CRYPTO   Cr SUSPENSE                 funds are ours, owner not yet settled
  credit   (attributed, risk-cleared)     Dr SUSPENSE         Cr customer crypto liability
  withdrawal reserved                     Dr customer liab    Cr CRYPTO_WD_CLEARING (+ Cr FEE_INCOME)
  withdrawal completed                    Dr CRYPTO_WD_CLEARING  Cr CUSTODY_CRYPTO       (+ Dr NETWORK_FEE_EXPENSE  Cr CUSTODY_CRYPTO)
  withdrawal failed/rejected              reversal of the reserve entry

Every line carries the network-specific ledger asset code (USDT.ETHEREUM is a different asset from USDT on another chain), and debits equal
credits per asset. All entries are idempotent on a deterministic key, so replays and double processing cannot post twice.
"""
from . import accounting as acc, util
from .db import one, all_, tx
from .util import ApiError

def customer_account(db, user_id):
    code = f'CRYPTO-U{int(user_id)}'
    r = one(db, 'SELECT id FROM gl_accounts WHERE code=?', code)
    if r: return r['id']
    return db.execute('INSERT INTO gl_accounts(code,name,type,normal_side,owner_user_id,created_at) VALUES(?,?,?,?,?,?)',
                      (code, f'Customer crypto liability (user {user_id})', 'liability', 'credit', user_id, util.now())).lastrowid

def balance(db, user_id, ledger_code):
    """Minor units of `ledger_code` the company owes this customer, straight from the general ledger."""
    r = one(db, 'SELECT id FROM gl_accounts WHERE code=?', f'CRYPTO-U{int(user_id)}')
    return acc.balance(db, r['id'], ledger_code) if r else 0

def balances(db, user_id):
    r = one(db, 'SELECT id FROM gl_accounts WHERE code=?', f'CRYPTO-U{int(user_id)}')
    if not r: return {}
    out = {}
    for b in all_(db, 'SELECT asset_code,balance FROM account_balances WHERE gl_account_id=?', r['id']):
        v = -int(b['balance'])                                   # stored as debits-credits; a liability's credit balance is positive
        if v: out[b['asset_code']] = v
    return out

def total_customer_liability(db, ledger_code):
    tot = 0
    for r in db.execute("SELECT b.balance FROM account_balances b JOIN gl_accounts g ON g.id=b.gl_account_id WHERE g.code LIKE 'CRYPTO-U%' AND b.asset_code=?", (ledger_code,)): tot += -int(r['balance'])
    return tot

def _S(db, k): return acc.account_id(db, k)

def post_received(db, dep_id, ledger_code, amount, ref):
    return acc.post_entry(db, entry_type='crypto.deposit_received', idem=f'cdep:{dep_id}:received', source_type='crypto_deposit', source_ref=ref,
                          description=f'On-chain deposit {ref} received into custody',
                          lines=[(_S(db, 'CUSTODY_CRYPTO'), 'debit', ledger_code, int(amount)), (_S(db, 'SUSPENSE'), 'credit', ledger_code, int(amount))])

def post_credit(db, dep_id, user_id, ledger_code, amount, ref):
    return acc.post_entry(db, entry_type='crypto.deposit_credit', idem=f'cdep:{dep_id}:credit', source_type='crypto_deposit', source_ref=ref,
                          description=f'Credit verified deposit {ref} to customer',
                          lines=[(_S(db, 'SUSPENSE'), 'debit', ledger_code, int(amount)), (customer_account(db, user_id), 'credit', ledger_code, int(amount))])

def post_withdrawal_reserve(db, wid, user_id, ledger_code, amount, fee, ref):
    lines = [(customer_account(db, user_id), 'debit', ledger_code, int(amount) + int(fee)), (_S(db, 'CRYPTO_WD_CLEARING'), 'credit', ledger_code, int(amount))]
    if fee: lines.append((_S(db, 'FEE_INCOME'), 'credit', ledger_code, int(fee)))
    return acc.post_entry(db, entry_type='crypto.withdrawal_reserved', idem=f'cwd:{wid}:reserve', source_type='crypto_withdrawal', source_ref=ref, description=f'Reserve funds for withdrawal {ref}', lines=lines)

def post_withdrawal_completed(db, wid, ledger_code, amount, network_fee, ref):
    lines = [(_S(db, 'CRYPTO_WD_CLEARING'), 'debit', ledger_code, int(amount)), (_S(db, 'CUSTODY_CRYPTO'), 'credit', ledger_code, int(amount))]
    if int(network_fee) > 0: lines += [(_S(db, 'NETWORK_FEE_EXPENSE'), 'debit', ledger_code, int(network_fee)), (_S(db, 'CUSTODY_CRYPTO'), 'credit', ledger_code, int(network_fee))]
    return acc.post_entry(db, entry_type='crypto.withdrawal_completed', idem=f'cwd:{wid}:complete', source_type='crypto_withdrawal', source_ref=ref, description=f'Withdrawal {ref} settled on-chain', lines=lines)
