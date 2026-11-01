"""Portfolio valuation. All numbers are computed on the server from holdings x asset prices."""
import bisect
from decimal import Decimal
from datetime import timedelta
from . import util
from .util import to_cents, cstr, qstr
from .db import all_, one, val
from .ledger import account_for, balance

CLASSES = ['Stocks', 'ETFs', 'Bonds', 'Crypto', 'Real Estate', 'Commodities']

def _crypto_rows(db, user_id):
    """Customer crypto from the GENERAL LEDGER (never a second balance store), valued with the same asset prices as every other holding.
    Transferred-in crypto carries no cost-basis tracking yet, so cost = current value (no invented gain or loss)."""
    from . import crypto_ledger as cl
    out = []
    for code, minor in cl.balances(db, user_id).items():
        dec = one(db, 'SELECT decimals FROM ledger_assets WHERE code=?', code)['decimals']; sym = code.split('.')[0]; a = one(db, 'SELECT * FROM assets WHERE symbol=?', sym)
        q = Decimal(minor).scaleb(-dec); price = Decimal(a['last_price']) if a else None; prev = Decimal(a['prev_close']) if a else None
        value = to_cents(q * price) if price is not None else 0
        out.append({'asset_id': a['id'] if a else None, 'symbol': sym, 'name': (a['name'] if a else sym) + (' (on-chain deposit)' if a else ''), 'asset_class': 'Crypto', 'quantity': format(q.normalize(), 'f') if q else '0',
                    'avg_cost': str(price.quantize(Decimal('0.0001'))) if price is not None else '0', 'price': a['last_price'] if a else '0', 'value_cents': value, 'cost_cents': value, 'unrealized_cents': 0, 'realized_cents': 0,
                    'day_change_cents': to_cents(q * (price - prev)) if price is not None else 0, 'acquired_at': None, 'source': 'crypto_ledger', 'ledger_code': code, 'priced': price is not None})
    return out

def holdings(db, user_id):
    out, total = [], 0
    for h in all_(db, 'SELECT h.*,a.symbol,a.name,a.asset_class,a.last_price,a.prev_close,a.status astatus FROM portfolio_holdings h JOIN assets a ON a.id=h.asset_id WHERE h.user_id=? AND CAST(h.quantity AS REAL)>0', user_id):
        q, p = Decimal(h['quantity']), Decimal(h['last_price'])
        value = to_cents(q * p); total += value
        out.append({'asset_id': h['asset_id'], 'symbol': h['symbol'], 'name': h['name'], 'asset_class': h['asset_class'], 'quantity': qstr(q),
                    'avg_cost': str((Decimal(h['cost_basis_cents']) / 100 / q).quantize(Decimal('0.0001'))) if q else '0', 'price': h['last_price'],
                    'value_cents': value, 'cost_cents': h['cost_basis_cents'], 'unrealized_cents': value - h['cost_basis_cents'],
                    'realized_cents': h['realized_pl_cents'], 'day_change_cents': to_cents(q * (p - Decimal(h['prev_close']))), 'acquired_at': h['acquired_at']})
    for r in _crypto_rows(db, user_id): out.append(r); total += r['value_cents']
    from . import crypto_catalog as cat
    logos = cat.logo_map(db, include_hidden=True)         # customers keep seeing the logo of an asset they hold even if the admin later hides it
    for o in out:
        o['logo'] = logos.get(o['symbol']) if o['asset_class'] == 'Crypto' else None
        o['unrealized_pct'] = str((Decimal(o['unrealized_cents']) * 100 / Decimal(o['cost_cents'])).quantize(Decimal('0.01'))) if o['cost_cents'] else '0.00'
        o['allocation_pct'] = str((Decimal(o['value_cents']) * 100 / total).quantize(Decimal('0.01'))) if total else '0.00'
        for k in ('value', 'cost', 'unrealized', 'realized', 'day_change'): o[k] = cstr(o.pop(k + '_cents'))
    return out

def _retirement_cents(db, user_id):
    """Posted balance of a funded retirement ACCOUNT, if the customer has one. (The retirement planner's typed-in figures are projections, not money, and are never counted.)"""
    r = one(db, "SELECT id FROM accounts WHERE user_id=? AND kind='retirement' AND status='active' ORDER BY id LIMIT 1", user_id)
    return balance(db, r['id'])['posted_cents'] if r else 0

def summary(db, user_id):
    """portfolio_value = money working for the customer: investing + trading holdings, savings (which includes goals) and retirement.
    The Account Balance (cash) is where funding lands and what everything is funded from; it is NOT part of the portfolio value. net_worth adds it back."""
    cash = balance(db, account_for(db, user_id, 'cash')['id'])
    sav = balance(db, account_for(db, user_id, 'savings')['id']); ret = _retirement_cents(db, user_id)
    hs = db.execute('SELECT h.*,a.last_price,a.prev_close FROM portfolio_holdings h JOIN assets a ON a.id=h.asset_id WHERE h.user_id=?', (user_id,)).fetchall()
    value = invested = day = realized = 0
    for h in hs:
        q = Decimal(h['quantity']); value += to_cents(q * Decimal(h['last_price'])); invested += h['cost_basis_cents']
        day += to_cents(q * (Decimal(h['last_price']) - Decimal(h['prev_close']))); realized += h['realized_pl_cents']
    for r in _crypto_rows(db, user_id): value += r['value_cents']; invested += r['cost_cents']; day += r['day_change_cents']
    unreal = value - invested; total_ret = unreal + realized
    pct = lambda n, d: str((Decimal(n) * 100 / Decimal(d)).quantize(Decimal('0.01'))) if d else '0.00'   # percentages are always computed server-side from integer cents
    pv = value + sav['posted_cents'] + ret
    return {'portfolio_value': cstr(pv), 'holdings_value': cstr(value), 'available_cash': cstr(cash['available_cents']), 'cash_balance': cstr(cash['posted_cents']),
            'cash_held': cstr(cash['held_cents'] + cash['reserved_cents']), 'total_invested': cstr(invested), 'unrealized_pl': cstr(unreal), 'realized_pl': cstr(realized),
            'total_returns': cstr(total_ret), 'total_returns_pct': str((Decimal(total_ret) * 100 / invested).quantize(Decimal('0.01'))) if invested else '0.00',
            'day_change': cstr(day), 'holdings_day_change_pct': pct(day, value - day),
            'unrealized_pl_pct': pct(unreal, invested), 'realized_pl_pct': pct(realized, invested), 'savings_balance': cstr(sav['posted_cents']), 'retirement_balance': cstr(ret), 'account_balance': cstr(cash['available_cents']), 'net_worth': cstr(pv + cash['posted_cents']), 'currency': 'USD'}

def allocation(db, user_id):
    """Share of the portfolio (holdings at current prices + savings & goals + retirement) per class. Every class is returned, including zero rows. Cash is not part of it."""
    tot = {c: 0 for c in CLASSES}
    for h in holdings(db, user_id): tot[h['asset_class']] += to_cents(Decimal(h['value']))
    cash = max(balance(db, account_for(db, user_id, 'savings')['id'])['posted_cents'], 0) + max(_retirement_cents(db, user_id), 0)      # the 'Savings' slice
    s = sum(tot.values()) + cash
    share = lambda v: str((Decimal(v) * 100 / s).quantize(Decimal('0.1'))) if s else '0.0'
    return [{'label': k, 'value': cstr(v), 'pct': share(v)} for k, v in [*tot.items(), ('Savings', cash)]]

def activity(db, user_id, limit=6):
    """Latest ledger entries for the Portfolio 'Recent activity' card, built from ledger.txn_view() plus the trade metadata the ledger already stores."""
    from .ledger import txn_view
    out = []
    for r in db.execute('SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT ?', (user_id, limit)):
        t = txn_view(r); m = t.get('metadata') or {}
        out.append({'ref': t['ref'], 'type': t['type'], 'direction': t['direction'], 'amount': t['amount'], 'status': t['status'], 'created_at': t['created_at'],
                    'symbol': m.get('asset'), 'side': m.get('side'), 'quantity': m.get('qty'), 'product': m.get('product')})
    return out

PORTFOLIO_KINDS = ('savings', 'retirement')
RANGES = {'1D': (timedelta(days=1), 24), '1W': (timedelta(days=7), 28), '1M': (timedelta(days=30), 30), '3M': (timedelta(days=91), 45),
          '6M': (timedelta(days=182), 52), '1Y': (timedelta(days=365), 52), '5Y': (timedelta(days=1826), 60), 'ALL': (None, 60)}

def _price_lookup(db, asset_ids):
    series = {}
    for a in asset_ids:
        rows = db.execute('SELECT ts,price FROM asset_prices WHERE asset_id=? ORDER BY ts', (a,)).fetchall()
        series[a] = ([r['ts'] for r in rows], [Decimal(r['price']) for r in rows])
    return series

def value_series(db, user_id, stamps, include_savings=False, kinds=None):
    """Value at each ISO timestamp (ascending): the chosen account kinds from the ledger + holdings from movements x as-of price.
    kinds=None keeps the legacy meaning (cash, or cash+savings); portfolio history passes ('savings','retirement') = the portfolio WITHOUT cash."""
    kinds = kinds or (('cash', 'savings') if include_savings else ('cash',))
    ev = db.execute(f"SELECT COALESCE(t.completed_at,t.created_at) ts, CASE direction WHEN 'credit' THEN amount_cents ELSE -amount_cents END amt FROM transactions t JOIN accounts a ON a.id=t.account_id WHERE t.user_id=? AND t.status='completed' AND a.kind IN ({','.join('?'*len(kinds))}) ORDER BY ts", (user_id, *kinds)).fetchall()
    mv = db.execute('SELECT asset_id,qty_delta,ts FROM holding_movements WHERE user_id=? ORDER BY ts', (user_id,)).fetchall()
    # crypto held on the ledger: quantity deltas from journal lines on the customer's crypto liability account (credit = customer gains)
    cmv = []
    for r in db.execute("""SELECT je.posted_at ts,jl.asset_code,jl.direction,jl.amount,la.decimals FROM journal_lines jl JOIN journal_entries je ON je.id=jl.entry_id
                            JOIN gl_accounts g ON g.id=jl.gl_account_id JOIN ledger_assets la ON la.code=jl.asset_code WHERE g.code=? ORDER BY je.posted_at,jl.id""", (f'CRYPTO-U{int(user_id)}',)):
        a = db.execute('SELECT id FROM assets WHERE symbol=?', (r['asset_code'].split('.')[0],)).fetchone()
        if a: cmv.append({'asset_id': -a['id'], 'real_id': a['id'], 'qty_delta': str(Decimal(r['amount']).scaleb(-r['decimals']) * (1 if r['direction'] == 'credit' else -1)), 'ts': r['ts']})
    mv = sorted([dict(m) for m in mv] + cmv, key=lambda m: m['ts'])
    prices = _price_lookup(db, {m.get('real_id', m['asset_id']) for m in mv})
    out, ei, mi, cash, qty = [], 0, 0, 0, {}
    for s in stamps:
        while ei < len(ev) and ev[ei]['ts'] <= s: cash += ev[ei]['amt']; ei += 1
        while mi < len(mv) and mv[mi]['ts'] <= s: qty[mv[mi]['asset_id']] = qty.get(mv[mi]['asset_id'], Decimal(0)) + Decimal(mv[mi]['qty_delta']); mi += 1
        v = cash
        for a, q in qty.items():
            if q > 0:
                ts, ps = prices[abs(a)]; i = bisect.bisect_right(ts, s) - 1
                if i >= 0: v += to_cents(q * ps[i])
        out.append(v)
    return out

def history(db, user_id, rng='1M'):
    if rng not in RANGES: rng = '1M'
    span, n = RANGES[rng]; end = util.utcnow()
    if span is None:
        first = db.execute('SELECT MIN(ts) m FROM (SELECT created_at ts FROM transactions WHERE user_id=? UNION ALL SELECT ts FROM holding_movements WHERE user_id=?)', (user_id, user_id)).fetchone()['m']
        start = util.parse_ts(first) if first else end - timedelta(days=30)
        if (end - start).days < 1: start = end - timedelta(days=1)
    else: start = end - span
    step = (end - start) / (n - 1)
    stamps = [(start + step * i).strftime('%Y-%m-%dT%H:%M:%SZ') for i in range(n)]
    vals = value_series(db, user_id, stamps, kinds=PORTFOLIO_KINDS)   # same definition as the headline: holdings + savings + retirement, no cash
    return {'range': rng, 'points': [{'t': s, 'value': cstr(v)} for s, v in zip(stamps, vals)]}

def snapshot_all(db, day=None):
    day = day or util.now()[:10]; stamp = day + 'T23:59:59Z'
    for u in db.execute("SELECT id FROM users WHERE status IN('active','restricted')").fetchall():
        v = value_series(db, u['id'], [stamp], kinds=PORTFOLIO_KINDS)[0]
        db.execute('INSERT OR REPLACE INTO portfolio_snapshots(user_id,day,value_cents) VALUES(?,?,?)', (u['id'], day, v))
