"""Financial domain services: deposits, withdrawals, investments, savings/goals, retirement, trading.
Every function takes `db` explicitly and runs money movement inside one serialised DB transaction."""
import json
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from datetime import date, timedelta
from . import util, ledger, security
from .util import ApiError, to_cents, cstr, qstr, parse_ts
from .db import one, val, all_, tx
from .rbac import setting
from .notify import notify
from .mailer import events as mail
from .audit import audit

def _bps(amount_cents, bps): return int((Decimal(amount_cents) * bps / 10000).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

def can_transact(db, user):
    u = one(db, 'SELECT * FROM users WHERE id=?', user['id'])
    if setting(db, 'maintenance_mode'): raise ApiError(503, 'maintenance', 'Money movement is temporarily disabled.')
    if u['status'] != 'active': raise ApiError(403, 'account_restricted', f"Your account is {u['status']}. Contact support.")
    if not u['email_verified']: raise ApiError(403, 'email_unverified', 'Verify your email address first.')
    return dict(u)

def launch_gate(providers):
    """Live providers move real money, which stays disabled until the organisation's authorisation is effective (REAL_FUNDS_ENABLED=1)."""
    if providers.mode == 'live' and not providers.real_funds_enabled:
        raise ApiError(403, 'funding_not_authorized', 'Deposits, withdrawals and investing are not yet available. No transaction was created.')

def require_2fa(db, user, code, why):
    u = dict(one(db, 'SELECT * FROM users WHERE id=?', user['id']))
    if u['totp_enabled'] and not security.verify_totp(db, u, code):
        security.log_event(db, u['id'], '2fa_challenge_failed', why)
        raise ApiError(401, 'two_factor_required', 'Enter a valid 2FA code to continue.')

# ================= DEPOSITS =================
def create_deposit(db, providers, user, amount_cents, method, idem=None):
    launch_gate(providers); u = can_transact(db, user)
    if method not in ('bank_transfer', 'card'): raise ApiError(422, 'invalid_method', 'Unsupported payment method')
    if amount_cents < setting(db, 'min_deposit_cents') or amount_cents > setting(db, 'max_deposit_cents'):
        raise ApiError(422, 'amount_out_of_range', f"Deposits must be between ${cstr(setting(db,'min_deposit_cents'))} and ${cstr(setting(db,'max_deposit_cents'))}.")
    def run():
        fee = _bps(amount_cents, setting(db, 'card_fee_bps' if method == 'card' else 'bank_fee_bps'))
        ext = providers.payments.create_deposit(u['id'], amount_cents, method)      # raises ProviderNotConfigured in live mode w/o keys
        cash = ledger.account_for(db, u['id'])
        tid = ledger.post(db, user_id=u['id'], account_id=cash['id'], type='deposit', direction='credit', amount_cents=amount_cents - fee,
                          status='processing', reference=ext['external_ref'], non_live=providers.payments.non_live, fees=[('deposit_fee', fee)],
                          metadata={'method': method, 'gross_cents': amount_cents})
        ref = util.new_ref('DEP')
        db.execute('INSERT INTO deposits(ref,user_id,txn_id,amount_cents,method,provider,external_ref,internal_ref,fee_cents,status,non_live,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   (ref, u['id'], tid, amount_cents, method, providers.payments.name, ext['external_ref'], ref, fee, ext['status'], 1 if providers.payments.non_live else 0, util.now()))
        notify(db, u['id'], 'deposit', 'Deposit initiated', f"${cstr(amount_cents)} deposit {ref} is processing.", '#/transactions')
        mail.emit(db, 'deposit.requested', u['id'], ref)
        return {'deposit': deposit_view(db, ref)}
    with tx(db): return ledger.idempotent(db, u['id'], idem, 'deposit', {'a': amount_cents, 'm': method}, run)

def deposit_view(db, ref):
    d = dict(one(db, 'SELECT * FROM deposits WHERE ref=?', ref))
    for k in ('amount_cents', 'fee_cents'): d[k.replace('_cents', '')] = cstr(d.pop(k))
    d['non_live'] = bool(d['non_live']); return d

def handle_payment_event(db, payload):
    """Single entry point for provider settlement events (signed webhook or manual settlement)."""
    with tx(db):
        d = one(db, 'SELECT * FROM deposits WHERE external_ref=?', payload.get('external_ref'))
        if not d: raise ApiError(404, 'unknown_reference', 'Unknown payment reference')
        if d['status'] in ('completed', 'failed', 'reversed'): return {'status': d['status'], 'noop': True}
        if payload.get('type') == 'deposit.succeeded':
            ledger.transition(db, d['txn_id'], 'completed')
            db.execute("UPDATE deposits SET status='completed',completed_at=? WHERE id=?", (util.now(), d['id']))
            notify(db, d['user_id'], 'deposit', 'Deposit completed', f"${cstr(d['amount_cents'])} deposit {d['ref']} has been credited.", '#/transactions')
            mail.emit(db, 'deposit.confirmed', d['user_id'], d['ref'])        # only after the ledger transition above has completed
            return {'status': 'completed'}
        if payload.get('type') == 'deposit.failed':
            ledger.transition(db, d['txn_id'], 'failed')
            db.execute("UPDATE deposits SET status='failed' WHERE id=?", (d['id'],))
            notify(db, d['user_id'], 'deposit', 'Deposit failed', f"Deposit {d['ref']} could not be completed.", '#/transactions')
            mail.emit(db, 'deposit.failed', d['user_id'], d['ref'])
            return {'status': 'failed'}
        raise ApiError(422, 'unknown_event', 'Unsupported event type')

def reverse_deposit(db, actor_id, deposit_id, reason):
    with tx(db):
        d = one(db, 'SELECT * FROM deposits WHERE id=?', deposit_id)
        if not d or d['status'] != 'completed': raise ApiError(409, 'invalid_state', 'Only completed deposits can be reversed.')
        ledger.transition(db, d['txn_id'], 'reversed')
        db.execute("UPDATE deposits SET status='reversed' WHERE id=?", (d['id'],))
        audit(db, actor_id, 'deposit.reversed', 'deposit', d['id'], {'status': 'completed'}, {'status': 'reversed'}, reason)
        notify(db, d['user_id'], 'deposit', 'Deposit reversed', f"Deposit {d['ref']} was reversed.", '#/transactions')
        mail.emit(db, 'deposit.reversed', d['user_id'], d['ref'])

# ================= WITHDRAWALS =================
W_ACTIVE = ('pending', 'held_for_review', 'approved', 'processing')
def request_withdrawal(db, providers, user, amount_cents, method, destination, code=None, idem=None):
    launch_gate(providers); u = can_transact(db, user)
    if u['kyc_status'] != 'verified': raise ApiError(403, 'kyc_required', 'Complete identity verification before withdrawing.')
    if method not in ('bank_transfer', 'card'): raise ApiError(422, 'invalid_method', 'Unsupported withdrawal method')
    destination = util.text(destination, 'destination', 120)
    if not u['totp_enabled'] and u['withdrawal_2fa']:
        raise ApiError(403, 'two_factor_setup_required', 'Enable two-factor authentication in Security before withdrawing.')
    if u['totp_enabled']: require_2fa(db, u, code, 'withdrawal')
    def run():
        today = util.now()[:10]
        used = val(db, "SELECT COALESCE(SUM(amount_cents),0) FROM withdrawals WHERE user_id=? AND status NOT IN('rejected','failed','cancelled') AND substr(created_at,1,10)=?", u['id'], today)
        if used + amount_cents > setting(db, 'daily_withdrawal_limit_cents'):
            raise ApiError(422, 'daily_limit', f"Daily withdrawal limit of ${cstr(setting(db,'daily_withdrawal_limit_cents'))} exceeded.")
        status = 'held_for_review' if amount_cents >= setting(db, 'withdrawal_review_threshold_cents') else 'pending'
        cash = ledger.account_for(db, u['id'])
        tid = ledger.post(db, user_id=u['id'], account_id=cash['id'], type='withdrawal', direction='debit', amount_cents=amount_cents, status='pending',
                          non_live=providers.payments.non_live, metadata={'method': method, 'destination': destination})   # funds are held immediately
        ref = util.new_ref('WDR')
        db.execute('INSERT INTO withdrawals(ref,user_id,txn_id,amount_cents,method,destination,status,non_live,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                   (ref, u['id'], tid, amount_cents, method, destination, status, 1 if providers.payments.non_live else 0, util.now()))
        security.log_event(db, u['id'], 'withdrawal_requested', f'{ref} ${cstr(amount_cents)}')
        notify(db, u['id'], 'withdrawal', 'Withdrawal requested', f"${cstr(amount_cents)} withdrawal {ref} is {status.replace('_',' ')}.", '#/transactions')
        mail.emit(db, 'withdrawal.requested', u['id'], ref)
        mail.emit(db, 'security.withdrawal_alert', u['id'], ref)
        return {'withdrawal': withdrawal_view(db, ref)}
    with tx(db): return ledger.idempotent(db, u['id'], idem, 'withdrawal', {'a': amount_cents, 'm': method, 'd': destination}, run)

def withdrawal_view(db, ref):
    w = dict(one(db, 'SELECT * FROM withdrawals WHERE ref=?', ref)); w['amount'] = cstr(w.pop('amount_cents')); w['non_live'] = bool(w['non_live']); return w

def cancel_withdrawal(db, user, wid):
    with tx(db):
        w = one(db, 'SELECT * FROM withdrawals WHERE id=? AND user_id=?', wid, user['id'])
        if not w: raise ApiError(404, 'not_found', 'Withdrawal not found')
        if w['status'] not in ('pending', 'held_for_review'): raise ApiError(409, 'invalid_state', 'This withdrawal can no longer be cancelled.')
        ledger.transition(db, w['txn_id'], 'cancelled'); db.execute("UPDATE withdrawals SET status='cancelled' WHERE id=?", (wid,))
        mail.emit(db, 'withdrawal.cancelled', w['user_id'], w['ref'])

def withdrawal_action(db, providers, actor_id, wid, action, reason=None):
    FLOW = {'approve': (('pending', 'held_for_review'), 'approved'), 'hold': (('pending',), 'held_for_review'), 'process': (('approved',), 'processing'),
            'complete': (('processing',), 'completed'), 'reject': (('pending', 'held_for_review', 'approved'), 'rejected'), 'fail': (('processing',), 'failed')}
    if action not in FLOW: raise ApiError(422, 'invalid_action', 'Unknown action')
    if action in ('reject', 'fail', 'hold') and not (reason or '').strip(): raise ApiError(422, 'reason_required', 'A reason is required.')
    with tx(db):
        w = one(db, 'SELECT * FROM withdrawals WHERE id=?', wid)
        if not w: raise ApiError(404, 'not_found', 'Withdrawal not found')
        allowed, new = FLOW[action]
        if w['status'] not in allowed: raise ApiError(409, 'invalid_state', f"Cannot {action} a withdrawal that is {w['status']}.")
        if action == 'approve' and w['user_id'] == actor_id: raise ApiError(403, 'separation_of_duties', 'You cannot approve your own withdrawal.')
        extra = {}
        if action == 'process':
            out = providers.payments.create_payout(w['user_id'], w['amount_cents'], w['destination']); extra['provider_ref'] = out['external_ref']
            db.execute('UPDATE withdrawals SET provider_ref=? WHERE id=?', (out['external_ref'], wid)); ledger.transition(db, w['txn_id'], 'processing')
        elif action == 'complete': ledger.transition(db, w['txn_id'], 'completed', w['provider_ref'])
        elif action == 'reject': ledger.transition(db, w['txn_id'], 'cancelled')       # releases the hold
        elif action == 'fail': ledger.transition(db, w['txn_id'], 'failed')
        db.execute('UPDATE withdrawals SET status=?,review_reason=COALESCE(?,review_reason),reviewed_by=?,reviewed_at=?,completed_at=? WHERE id=?',
                   (new, reason, actor_id, util.now(), util.now() if new == 'completed' else None, wid))
        audit(db, actor_id, f'withdrawal.{action}', 'withdrawal', wid, {'status': w['status']}, {'status': new, **extra}, reason)
        notify(db, w['user_id'], 'withdrawal', f"Withdrawal {new.replace('_',' ')}", f"Withdrawal {w['ref']} is now {new.replace('_',' ')}." + (f' Reason: {reason}' if reason and action in ('reject', 'fail') else ''), '#/transactions')
        ev = {'approve': 'withdrawal.approved', 'hold': 'withdrawal.review', 'process': 'withdrawal.processing', 'complete': 'withdrawal.completed', 'reject': 'withdrawal.rejected', 'fail': 'withdrawal.failed'}[action]
        mail.emit(db, ev, w['user_id'], w['ref'], reason=(reason if action in ('reject', 'fail') else ''))      # an internal 'hold' reason is never sent to the customer
        return {'status': new}

# ================= HOLDINGS =================
def apply_fill(db, user_id, asset_id, side, qty, cash_cents, price, ref, source):
    """buy: cash_cents = total cost incl. fee. sell: cash_cents = net proceeds. Returns realized P/L (cents)."""
    t = util.now()
    h = one(db, 'SELECT * FROM portfolio_holdings WHERE user_id=? AND asset_id=?', user_id, asset_id)
    if side == 'buy':
        if h:
            q0 = Decimal(h['quantity'])
            db.execute('UPDATE portfolio_holdings SET quantity=?,cost_basis_cents=cost_basis_cents+?,acquired_at=CASE WHEN CAST(quantity AS REAL)<=0 THEN ? ELSE acquired_at END,updated_at=? WHERE id=?', (qstr(q0 + qty), cash_cents, t, t, h['id']))
        else:
            db.execute('INSERT INTO portfolio_holdings(user_id,asset_id,quantity,cost_basis_cents,acquired_at,updated_at) VALUES(?,?,?,?,?,?)', (user_id, asset_id, qstr(qty), cash_cents, t, t))
        realized = 0; delta = qty
    else:
        q0 = Decimal(h['quantity']) if h else Decimal(0)
        if qty > q0: raise ApiError(409, 'insufficient_holdings', 'Not enough units to sell.')
        removed = h['cost_basis_cents'] if qty == q0 else int((Decimal(h['cost_basis_cents']) * qty / q0).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        realized = cash_cents - removed
        db.execute('UPDATE portfolio_holdings SET quantity=?,cost_basis_cents=cost_basis_cents-?,realized_pl_cents=realized_pl_cents+?,updated_at=? WHERE id=?', (qstr(q0 - qty), removed, realized, t, h['id'])); delta = -qty
    db.execute('INSERT INTO holding_movements(user_id,asset_id,qty_delta,price,ts,source,ref) VALUES(?,?,?,?,?,?,?)', (user_id, asset_id, qstr(delta), str(price), t, source, ref))
    return realized

# ================= INVESTMENTS =================
def create_investment(db, providers, user, product_id, amount_cents, accepted_disclosures, code=None, idem=None):
    launch_gate(providers); u = can_transact(db, user)
    p = one(db, 'SELECT p.*,a.last_price,a.status astatus,a.symbol FROM investment_products p JOIN assets a ON a.id=p.asset_id WHERE p.id=?', product_id)
    if not p: raise ApiError(404, 'not_found', 'Product not found')
    if p['status'] != 'active' or p['astatus'] != 'active': raise ApiError(409, 'product_unavailable', 'This product is not currently available.')
    if amount_cents < p['min_investment_cents']: raise ApiError(422, 'below_minimum', f"Minimum investment is ${cstr(p['min_investment_cents'])}.")
    if accepted_disclosures is not True: raise ApiError(422, 'disclosure_required', 'You must acknowledge the risk disclosure to continue.')
    if amount_cents >= setting(db, 'twofa_required_above_cents'): require_2fa(db, u, code, 'investment')
    def run():
        fee = _bps(amount_cents, p['fee_bps']); net = amount_cents - fee
        price = Decimal(p['last_price']); qty = (Decimal(net) / 100 / price).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
        if qty <= 0: raise ApiError(422, 'amount_too_small', 'Amount too small for this product.')
        cash = ledger.account_for(db, u['id']); ref = util.new_ref('INV')
        tid = ledger.post(db, user_id=u['id'], account_id=cash['id'], type='investment', direction='debit', amount_cents=amount_cents, status='pending',
                          reference=ref, non_live=providers.trading.non_live, fees=[('management_fee', fee)], metadata={'product': p['name'], 'asset': p['symbol']})
        cur = db.execute('INSERT INTO investments(ref,user_id,product_id,asset_id,txn_id,amount_cents,fee_cents,status,non_live,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (ref, u['id'], p['id'], p['asset_id'], tid, amount_cents, fee, 'pending', 1 if providers.trading.non_live else 0, util.now()))
        res = providers.trading.execute({'side': 'buy', 'symbol': p['symbol'], 'qty': qty}, price)   # live adapter raises -> whole tx rolls back
        if res['status'] == 'filled':
            ledger.transition(db, tid, 'completed')
            apply_fill(db, u['id'], p['asset_id'], 'buy', qty, amount_cents, res['price'], ref, 'investment')
            db.execute("UPDATE investments SET status='completed',quantity=?,unit_price=?,completed_at=? WHERE id=?", (qstr(qty), str(res['price']), util.now(), cur.lastrowid))
            notify(db, u['id'], 'investment', 'Investment confirmed', f"{p['name']}: ${cstr(amount_cents)} invested ({ref}).", '#/portfolio')
            mail.emit(db, 'investment.confirmed', u['id'], ref)
        else:
            ledger.transition(db, tid, 'processing'); db.execute("UPDATE investments SET status='processing' WHERE id=?", (cur.lastrowid,))
            mail.emit(db, 'investment.received', u['id'], ref)         # recorded, NOT confirmed
        r = dict(one(db, 'SELECT * FROM investments WHERE id=?', cur.lastrowid))
        return {'investment': {**{k: r[k] for k in ('ref', 'status', 'quantity', 'unit_price', 'created_at', 'completed_at')}, 'amount': cstr(r['amount_cents']), 'fee': cstr(r['fee_cents']), 'product': p['name'], 'transaction_ref': val(db, 'SELECT ref FROM transactions WHERE id=?', tid), 'non_live': bool(r['non_live'])}}
    with tx(db): return ledger.idempotent(db, u['id'], idem, 'investment', {'p': product_id, 'a': amount_cents}, run)

# ================= SAVINGS & GOALS =================
def _transfer(db, user_id, src, dst, amount_cents, label, idem=None):
    grp = util.new_ref('TRF')
    out = ledger.post(db, user_id=user_id, account_id=src['id'], type='transfer', direction='debit', amount_cents=amount_cents, status='completed', reference=grp, non_live=False, metadata={'label': label})
    ledger.post(db, user_id=user_id, account_id=dst['id'], type='transfer', direction='credit', amount_cents=amount_cents, status='completed', reference=grp, non_live=False, metadata={'label': label})
    return grp

def savings_transfer(db, user, direction, amount_cents, idem=None):
    u = can_transact(db, user)
    if direction not in ('to_savings', 'to_cash'): raise ApiError(422, 'invalid_direction', 'direction must be to_savings or to_cash')
    def run():
        cash, sav = ledger.account_for(db, u['id'], 'cash'), ledger.account_for(db, u['id'], 'savings')
        src, dst = (cash, sav) if direction == 'to_savings' else (sav, cash)
        grp = _transfer(db, u['id'], src, dst, amount_cents, direction)
        notify(db, u['id'], 'savings', 'Transfer complete', f"${cstr(amount_cents)} moved {'to savings' if direction=='to_savings' else 'to cash'}.", '#/savings')
        if direction == 'to_savings': mail.emit(db, 'savings.contribution_recorded', u['id'], grp, amount_cents=amount_cents, at=util.now())
        return {'transfer': grp, 'amount': cstr(amount_cents)}
    with tx(db): return ledger.idempotent(db, u['id'], idem, 'transfer', {'d': direction, 'a': amount_cents}, run)

FREQ_DAYS = {'weekly': 7, 'biweekly': 14, 'monthly': 30}
def goal_view(g):
    g = dict(g); tgt = g['target_cents']
    g['progress_pct'] = str(min(Decimal(100), (Decimal(g['current_cents']) * 100 / tgt)).quantize(Decimal('0.1'))) if tgt else '0.0'
    g['target'] = cstr(g.pop('target_cents')); g['current'] = cstr(g.pop('current_cents'))
    g['contribution'] = cstr(g['contribution_cents']) if g.get('contribution_cents') else None; g.pop('contribution_cents', None); return g

def create_goal(db, user, name, category, target_cents, target_date=None, frequency=None, contribution_cents=None):
    u = dict(user)
    if frequency and (frequency not in FREQ_DAYS or not contribution_cents): raise ApiError(422, 'invalid_schedule', 'Choose a frequency and contribution amount.')
    if target_date:
        try: d = date.fromisoformat(target_date)
        except ValueError: raise ApiError(422, 'invalid_date', 'Use YYYY-MM-DD')
        if d <= date.fromisoformat(util.now()[:10]): raise ApiError(422, 'invalid_date', 'Target date must be in the future')
    with tx(db):
        sav = ledger.account_for(db, u['id'], 'savings')
        cur = db.execute('INSERT INTO savings_goals(user_id,account_id,name,category,target_cents,target_date,frequency,contribution_cents,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                         (u['id'], sav['id'], name, category, target_cents, target_date, frequency, contribution_cents, util.now()))
        if frequency:
            nxt = (date.fromisoformat(util.now()[:10]) + timedelta(days=FREQ_DAYS[frequency])).isoformat()
            db.execute('INSERT INTO automatic_contributions(goal_id,user_id,frequency,amount_cents,next_run) VALUES(?,?,?,?,?)', (cur.lastrowid, u['id'], frequency, contribution_cents, nxt))
        mail.emit(db, 'savings.goal_created', u['id'], cur.lastrowid, goal_id=cur.lastrowid, at=util.now())
        return goal_view(one(db, 'SELECT * FROM savings_goals WHERE id=?', cur.lastrowid))

def contribute_goal(db, user, goal_id, amount_cents, idem=None, _auto=False):
    u = can_transact(db, user)
    def run():
        g = one(db, 'SELECT * FROM savings_goals WHERE id=? AND user_id=?', goal_id, u['id'])
        if not g: raise ApiError(404, 'not_found', 'Goal not found')
        if g['status'] not in ('active', 'paused') and not _auto: raise ApiError(409, 'invalid_state', 'This goal is not accepting contributions.')
        cash, sav = ledger.account_for(db, u['id'], 'cash'), ledger.account_for(db, u['id'], 'savings')
        grp = _transfer(db, u['id'], cash, sav, amount_cents, f"goal:{g['name']}")
        cur = g['current_cents'] + amount_cents; status = 'completed' if cur >= g['target_cents'] else g['status']
        db.execute('UPDATE savings_goals SET current_cents=?,status=? WHERE id=?', (cur, status, goal_id))
        mail.emit(db, 'savings.contribution_recorded', u['id'], grp, goal_id=goal_id, amount_cents=amount_cents, at=util.now())
        if status == 'completed' and g['status'] != 'completed':
            notify(db, u['id'], 'savings', 'Goal reached', f"You reached your goal “{g['name']}”.", '#/goals')
            mail.emit(db, 'savings.goal_achieved', u['id'], goal_id, goal_id=goal_id, at=util.now())
        elif g['target_cents']:
            before, after = g['current_cents'] * 100 // g['target_cents'], cur * 100 // g['target_cents']
            hit = [m for m in (25, 50, 75) if before < m <= after]
            if hit: mail.emit(db, 'savings.milestone', u['id'], f'{goal_id}:{hit[-1]}', goal_id=goal_id, at=util.now())    # recorded progress crossed it; one email per milestone ever
        return {'goal': goal_view(one(db, 'SELECT * FROM savings_goals WHERE id=?', goal_id))}
    with tx(db): return ledger.idempotent(db, u['id'], idem, f'goal{goal_id}', {'a': amount_cents}, run)

def run_due_contributions(db, today=None):
    today = today or util.now()[:10]; done = failed = 0
    for r in all_(db, 'SELECT * FROM automatic_contributions WHERE active=1 AND next_run<=?', today):
        u = one(db, 'SELECT * FROM users WHERE id=?', r['user_id'])
        with tx(db):
            nxt = (date.fromisoformat(r['next_run']) + timedelta(days=FREQ_DAYS[r['frequency']])).isoformat()
            try:
                contribute_goal(db, dict(u), r['goal_id'], r['amount_cents'], _auto=True); res = 'ok'; done += 1
            except ApiError as e:
                res = e.code; failed += 1
                notify(db, r['user_id'], 'savings', 'Automatic contribution skipped', f"Your scheduled contribution could not run ({e.message}).", '#/goals')
                mail.emit(db, 'savings.contribution_failed', r['user_id'], f"{r['id']}:{r['next_run']}", goal_id=r['goal_id'], reason=e.message, at=util.now())
            db.execute('UPDATE automatic_contributions SET next_run=?,last_result=? WHERE id=?', (nxt, res, r['id']))
    return {'ran': done, 'skipped': failed}

def accrue_interest(db, day=None):
    day = day or util.now()[:10]; n = total = 0
    for a in all_(db, "SELECT * FROM accounts WHERE kind='savings' AND status='active'"):
        bal = ledger.balance(db, a['id'])['posted_cents']
        if bal <= 0: continue
        cents = int((Decimal(bal) * a['apy_bps'] / 10000 / 365).quantize(Decimal('1'), rounding=ROUND_DOWN))
        key = f"interest:{a['id']}:{day}"
        if cents < 1 or one(db, 'SELECT 1 FROM transactions WHERE user_id=? AND idempotency_key=?', a['user_id'], key): continue
        ledger.post(db, user_id=a['user_id'], account_id=a['id'], type='interest', direction='credit', amount_cents=cents, status='completed', idem=key, non_live=False, metadata={'apy_bps': a['apy_bps'], 'day': day})
        n += 1; total += cents
    return {'accounts': n, 'interest': cstr(total)}

# ================= RETIREMENT =================
def project_retirement(p, rate_override=None):
    rate = Decimal(str(rate_override if rate_override is not None else p['expected_return_pct'])) / 100 / 12
    c = Decimal(p['monthly_contribution_cents']) / 100; match = c * Decimal(str(p['employer_match_pct'])) / 100
    bal = Decimal(p['current_savings_cents']) / 100; months = (p['retirement_age'] - p['current_age']) * 12
    series, contrib = [{'age': p['current_age'], 'balance': str(bal.quantize(Decimal('0.01')))}], bal
    for m in range(1, months + 1):
        bal = bal * (1 + rate) + c + match; contrib += c + match
        if m % 12 == 0: series.append({'age': p['current_age'] + m // 12, 'balance': str(bal.quantize(Decimal('0.01')))})
    return {'final': str(bal.quantize(Decimal('0.01'))), 'contributed': str(contrib.quantize(Decimal('0.01'))), 'growth': str((bal - contrib).quantize(Decimal('0.01'))), 'series': series}

def save_retirement(db, user_id, body):
    def num(k, lo, hi, cast=int):
        try: v = cast(util.dec(body.get(k), k)) if cast is not int else int(util.dec(body.get(k), k))
        except ApiError: raise
        if not (lo <= v <= hi): raise ApiError(422, 'out_of_range', f'{k} must be between {lo} and {hi}')
        return v
    age, rage = num('current_age', 16, 100), num('retirement_age', 17, 100)
    if rage <= age: raise ApiError(422, 'invalid_ages', 'Retirement age must be after your current age.')
    rate = util.dec(body.get('expected_return_pct'), 'expected_return_pct'); match = util.dec(body.get('employer_match_pct', 0), 'employer_match_pct')
    if not (0 <= rate <= 15): raise ApiError(422, 'out_of_range', 'Expected return must be between 0 and 15%')
    if not (0 <= match <= 100): raise ApiError(422, 'out_of_range', 'Employer match must be between 0 and 100%')
    cents = lambda k, req=True: (to_cents(util.dec(body.get(k, 0), k)) if body.get(k) not in (None, '') else 0)
    sav, mon, tgt = cents('current_savings'), cents('monthly_contribution'), cents('target')
    if min(sav, mon, tgt) < 0: raise ApiError(422, 'out_of_range', 'Amounts cannot be negative')
    with tx(db):
        db.execute('INSERT INTO retirement_plans(user_id,current_age,retirement_age,current_savings_cents,monthly_contribution_cents,employer_match_pct,expected_return_pct,target_cents,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET current_age=excluded.current_age,retirement_age=excluded.retirement_age,current_savings_cents=excluded.current_savings_cents,monthly_contribution_cents=excluded.monthly_contribution_cents,employer_match_pct=excluded.employer_match_pct,expected_return_pct=excluded.expected_return_pct,target_cents=excluded.target_cents,updated_at=excluded.updated_at',
                   (user_id, age, rage, sav, mon, str(match), str(rate), tgt, util.now()))
        mail.emit(db, 'retirement.updated', user_id, f'{user_id}:{util.now()}')
    return get_retirement(db, user_id)

def get_retirement(db, user_id):
    p = one(db, 'SELECT * FROM retirement_plans WHERE user_id=?', user_id)
    if not p: return None
    p = dict(p); proj = project_retirement(p)
    sc = {'conservative': project_retirement(p, max(0, float(p['expected_return_pct']) - 2))['final'], 'expected': proj['final'], 'optimistic': project_retirement(p, float(p['expected_return_pct']) + 2)['final']}
    tgt = Decimal(p['target_cents']) / 100
    return {'plan': {'current_age': p['current_age'], 'retirement_age': p['retirement_age'], 'current_savings': cstr(p['current_savings_cents']), 'monthly_contribution': cstr(p['monthly_contribution_cents']),
                     'employer_match_pct': p['employer_match_pct'], 'expected_return_pct': p['expected_return_pct'], 'target': cstr(p['target_cents'])},
            'projection': proj, 'scenarios': sc, 'goal_progress_pct': str(min(Decimal(999), Decimal(proj['final']) * 100 / tgt).quantize(Decimal('0.1'))) if tgt else None,
            'disclaimer': 'Projections use the assumptions you entered. They are illustrations, not guarantees or forecasts; real returns vary and can be negative.'}

# ================= TRADING =================
def _asset(db, symbol):
    a = one(db, 'SELECT * FROM assets WHERE symbol=?', (symbol or '').upper())
    if not a: raise ApiError(404, 'unknown_asset', 'Unknown asset')
    return dict(a)

def order_view(db, o):
    o = dict(o); a = one(db, 'SELECT symbol,name FROM assets WHERE id=?', o['asset_id'])
    o['symbol'], o['asset_name'] = a['symbol'], a['name']; o['fee'] = cstr(o.pop('fee_cents')); o['reserved'] = cstr(o.pop('reserved_cents')); o['non_live'] = bool(o['non_live']); return o

def place_order(db, providers, user, symbol, side, otype, qty, limit=None, stop=None, code=None, idem=None):
    launch_gate(providers); u = can_transact(db, user)
    if side not in ('buy', 'sell'): raise ApiError(422, 'invalid_side', 'side must be buy or sell')
    if otype not in ('market', 'limit', 'stop', 'stop_limit'): raise ApiError(422, 'invalid_type', 'Unsupported order type')
    a = _asset(db, symbol)
    if a['status'] != 'active' or not a['tradable']: raise ApiError(409, 'asset_not_tradable', 'This asset is not currently tradable.')
    lim = util.dec(limit, 'limit_price') if otype in ('limit', 'stop_limit') else None
    stp = util.dec(stop, 'stop_price') if otype in ('stop', 'stop_limit') else None
    for n, v in (('limit_price', lim), ('stop_price', stp)):
        if v is not None and v <= 0: raise ApiError(422, 'invalid_price', f'{n} must be positive')
    last = Decimal(a['last_price']); fee_bps = setting(db, 'trade_fee_bps')
    if side == 'buy':
        ref_price = last if otype == 'market' else lim if otype in ('limit', 'stop_limit') else max(stp, last) * Decimal('1.10')
        notional = to_cents(qty * ref_price); reserve = notional + _bps(notional, fee_bps)
    else: reserve = 0
    if to_cents(qty * last) >= setting(db, 'twofa_required_above_cents'): require_2fa(db, u, code, 'order')
    def run():
        if side == 'sell':
            h = one(db, 'SELECT quantity FROM portfolio_holdings WHERE user_id=? AND asset_id=?', u['id'], a['id'])
            open_sell = sum((Decimal(r[0]) - Decimal(r[1])) for r in db.execute("SELECT quantity,filled_qty FROM orders WHERE user_id=? AND asset_id=? AND side='sell' AND status IN('pending','open','partially_filled')", (u['id'], a['id'])))
            if not h or Decimal(h['quantity']) - open_sell < qty: raise ApiError(409, 'insufficient_holdings', 'You do not hold enough units (open sell orders count).')
        else:
            if ledger.balance(db, ledger.account_for(db, u['id'])['id'])['available_cents'] < reserve: raise ApiError(409, 'insufficient_funds', 'Insufficient available cash for this order.')
        ref = util.new_ref('ORD')
        cur = db.execute('INSERT INTO orders(ref,user_id,asset_id,side,order_type,quantity,limit_price,stop_price,reserved_cents,status,idempotency_key,non_live,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (ref, u['id'], a['id'], side, otype, qstr(qty), str(lim) if lim else None, str(stp) if stp else None, reserve, 'pending', None, 1 if providers.trading.non_live else 0, util.now()))
        if otype != 'market': db.execute("UPDATE orders SET status='open' WHERE id=?", (cur.lastrowid,))
        _try_match(db, providers, cur.lastrowid, market_now=(otype == 'market'))
        o = one(db, 'SELECT * FROM orders WHERE id=?', cur.lastrowid)
        if o['status'] == 'pending': db.execute("UPDATE orders SET status='open' WHERE id=?", (o['id'],)); o = one(db, 'SELECT * FROM orders WHERE id=?', o['id'])
        if o['status'] == 'open': mail.emit(db, 'trade.order_placed', u['id'], o['ref'])      # only orders that are really resting; a filled order gets the executed email instead
        return {'order': order_view(db, o)}
    with tx(db):
        out = ledger.idempotent(db, u['id'], idem, 'order', {'s': symbol, 'sd': side, 't': otype, 'q': str(qty), 'l': str(lim), 'st': str(stp)}, run)
        return out

def _try_match(db, providers, oid, market_now=False):
    o = dict(one(db, 'SELECT * FROM orders WHERE id=?', oid))
    if o['status'] not in ('pending', 'open', 'partially_filled'): return False
    a = dict(one(db, 'SELECT * FROM assets WHERE id=?', o['asset_id']))
    if a['status'] != 'active': return False
    p = Decimal(a['last_price']); t, side = o['order_type'], o['side']
    if t in ('stop', 'stop_limit') and not o['triggered']:
        stp = Decimal(o['stop_price'])
        if (side == 'buy' and p >= stp) or (side == 'sell' and p <= stp): db.execute('UPDATE orders SET triggered=1 WHERE id=?', (oid,))
        else: return False
    fill = t == 'market' or t == 'stop'
    if t in ('limit', 'stop_limit'):
        lim = Decimal(o['limit_price']); fill = (side == 'buy' and p <= lim) or (side == 'sell' and p >= lim)
    if not fill: return False
    res = providers.trading.execute(o, p)              # live adapter raises ProviderNotConfigured: order stays open/unfilled
    if res['status'] != 'filled': return False
    _fill(db, o, res['price'], res['provider_ref'], providers); return True

def _fill(db, o, price, pref, providers):
    qty = Decimal(o['quantity']) - Decimal(o['filled_qty']); notional = to_cents(qty * price); fee = _bps(notional, setting(db, 'trade_fee_bps')); t = util.now()
    cash = ledger.account_for(db, o['user_id'])
    if o['side'] == 'buy':
        bal = ledger.balance(db, cash['id'])
        if bal['available_cents'] + o['reserved_cents'] < notional + fee:
            db.execute("UPDATE orders SET status='failed',reason='insufficient funds at fill',reserved_cents=0 WHERE id=?", (o['id'],))
            notify(db, o['user_id'], 'trade', 'Order failed', f"Order {o['ref']} could not fill: insufficient cash at execution price.", '#/trade')
            mail.emit(db, 'trade.order_rejected', o['user_id'], o['ref']); return
    db.execute("UPDATE orders SET status='filled',filled_qty=quantity,execution_price=?,fee_cents=?,reserved_cents=0,executed_at=? WHERE id=?", (str(price), fee, t, o['id']))
    db.execute('INSERT INTO order_executions(order_id,quantity,price,fee_cents,ts,provider,provider_ref) VALUES(?,?,?,?,?,?,?)', (o['id'], qstr(qty), str(price), fee, t, providers.trading.name, pref))
    a = one(db, 'SELECT symbol FROM assets WHERE id=?', o['asset_id'])
    meta = {'asset': a['symbol'], 'side': o['side'], 'qty': qstr(qty), 'price': str(price), 'order': o['ref']}
    if o['side'] == 'buy':
        ledger.post(db, user_id=o['user_id'], account_id=cash['id'], type='trade', direction='debit', amount_cents=notional + fee, status='completed', reference=o['ref'], non_live=bool(o['non_live']), fees=[('trading_fee', fee)], metadata=meta)
        apply_fill(db, o['user_id'], o['asset_id'], 'buy', qty, notional + fee, price, o['ref'], 'order')
    else:
        apply_fill(db, o['user_id'], o['asset_id'], 'sell', qty, notional - fee, price, o['ref'], 'order')
        ledger.post(db, user_id=o['user_id'], account_id=cash['id'], type='trade', direction='credit', amount_cents=max(notional - fee, 1), status='completed', reference=o['ref'], non_live=bool(o['non_live']), fees=[('trading_fee', fee)], metadata=meta)
    notify(db, o['user_id'], 'trade', 'Order filled', f"{o['side'].title()} {qstr(qty)} {a['symbol']} @ ${price} ({o['ref']}).", '#/trade')
    mail.emit(db, 'trade.order_filled', o['user_id'], o['ref'])

def match_open_orders(db, providers, asset_id=None):
    n = 0
    with tx(db):
        for o in all_(db, "SELECT id FROM orders WHERE status IN('open','partially_filled')" + (' AND asset_id=?' if asset_id else ''), *( [asset_id] if asset_id else [])):
            try:
                if _try_match(db, providers, o['id']): n += 1
            except ApiError: pass
    return n

def cancel_order(db, user_id, oid, actor_id=None, reason=None):
    with tx(db):
        o = one(db, 'SELECT * FROM orders WHERE id=?' + ('' if actor_id else ' AND user_id=?'), *([oid] if actor_id else [oid, user_id]))
        if not o: raise ApiError(404, 'not_found', 'Order not found')
        if o['status'] not in ('pending', 'open', 'partially_filled'): raise ApiError(409, 'invalid_state', 'This order can no longer be cancelled.')
        db.execute("UPDATE orders SET status='cancelled',cancelled_at=?,reserved_cents=0,reason=? WHERE id=?", (util.now(), reason, oid))
        if actor_id: audit(db, actor_id, 'order.cancelled', 'order', oid, {'status': o['status']}, {'status': 'cancelled'}, reason); notify(db, o['user_id'], 'trade', 'Order cancelled', f"Order {o['ref']} was cancelled by VAULTA operations.", '#/trade')
        mail.emit(db, 'trade.order_cancelled', o['user_id'], o['ref'])

# ================= MARKET PRICES =================
def set_price(db, providers, asset_id, price, actor_id=None, reason=None):
    price = Decimal(price)
    if price <= 0: raise ApiError(422, 'invalid_price', 'Price must be positive')
    with tx(db):
        a = one(db, 'SELECT * FROM assets WHERE id=?', asset_id)
        if not a: raise ApiError(404, 'not_found', 'Asset not found')
        t = util.now()
        db.execute('UPDATE assets SET last_price=?,price_updated_at=? WHERE id=?', (str(price), t, asset_id))
        db.execute('INSERT OR REPLACE INTO asset_prices(asset_id,ts,price) VALUES(?,?,?)', (asset_id, t, str(price)))
        if actor_id: audit(db, actor_id, 'asset.price_set', 'asset', asset_id, {'price': a['last_price']}, {'price': str(price)}, reason)
        return match_open_orders(db, providers, asset_id)

def market_tick(db, providers):
    n = 0
    with tx(db):
        for a in all_(db, "SELECT * FROM assets WHERE status='active'"):
            set_price(db, providers, a['id'], providers.market.next_price(a)); n += 1
    return n
