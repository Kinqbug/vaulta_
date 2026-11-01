"""Audience selection over the REAL schema (every clause below references columns/tables that exist in migrations 001-007).
Always excluded: staff accounts, demo accounts, closed accounts. Marketing sends additionally require verified email + opt-in consent + no suppression."""
from . import prefs

BASE = "u.status IN ('active','restricted') AND u.is_demo=0 AND u.id NOT IN (SELECT user_id FROM user_roles)"
MARKETING_OK = "u.email_verified=1 AND EXISTS(SELECT 1 FROM communication_preferences cp WHERE cp.user_id=u.id AND cp.marketing_opt_in=1)"
SUPPRESSED = "NOT EXISTS(SELECT 1 FROM email_suppressions s WHERE s.email_key=lower(u.email) AND s.released_at IS NULL AND s.scope IN %s)"

SEGMENTS = {
 'all_active': ('All active customers', "u.status='active'"),
 'email_verified': ('Email-verified customers', "u.status='active' AND u.email_verified=1"),
 'kyc_verified': ('KYC-approved customers', "u.status='active' AND u.kyc_status='verified'"),
 'kyc_incomplete': ('Customers with incomplete KYC', "u.status='active' AND u.kyc_status IN ('not_started','pending','requires_info','rejected')"),
 'pending_deposits': ('Customers with pending deposits',
    "(EXISTS(SELECT 1 FROM deposits d WHERE d.user_id=u.id AND d.status IN('pending','processing')) OR EXISTS(SELECT 1 FROM deposit_requests r WHERE r.user_id=u.id AND r.status='pending') "
    "OR EXISTS(SELECT 1 FROM crypto_deposits c WHERE c.user_id=u.id AND c.status IN('detected','validating','unconfirmed','confirming','confirmed','risk_review','manual_review')))"),
 'completed_deposits': ('Customers with completed deposits',
    "(EXISTS(SELECT 1 FROM deposits d WHERE d.user_id=u.id AND d.status='completed') OR EXISTS(SELECT 1 FROM crypto_deposits c WHERE c.user_id=u.id AND c.status='credited'))"),
 'with_investments': ('Customers with investments', "EXISTS(SELECT 1 FROM investments i WHERE i.user_id=u.id AND i.status='completed')"),
 'with_savings': ('Customers with savings activity (goals or savings-account transactions)',
    "(EXISTS(SELECT 1 FROM savings_goals g WHERE g.user_id=u.id AND g.status IN('active','paused','completed')) "
    "OR EXISTS(SELECT 1 FROM accounts a JOIN transactions t ON t.account_id=a.id WHERE a.user_id=u.id AND a.kind='savings' AND t.status='completed'))"),
 'marketing_opt_in': ('Customers who opted in to marketing', "EXISTS(SELECT 1 FROM communication_preferences cp WHERE cp.user_id=u.id AND cp.marketing_opt_in=1)"),
}

def _where(segment, mail_class):
    if segment not in SEGMENTS: raise ValueError('unknown audience')
    w = [BASE, SEGMENTS[segment][1]]
    if mail_class == 'marketing': w += [MARKETING_OK, SUPPRESSED % "('all','marketing')"]
    else: w.append(SUPPRESSED % "('all')")
    return ' AND '.join(f'({x})' for x in w)

def count(db, segment, mail_class): return db.execute(f'SELECT COUNT(*) FROM users u WHERE {_where(segment, mail_class)}').fetchone()[0]

def select(db, segment, mail_class, limit):
    return db.execute(f'SELECT u.id,u.public_id,u.email,p.first_name FROM users u LEFT JOIN user_profiles p ON p.user_id=u.id WHERE {_where(segment, mail_class)} ORDER BY u.id LIMIT ?', (limit,)).fetchall()

def resolve_ids(db, public_ids, mail_class):
    """Selected customers: every id is re-validated on the server. Returns (eligible rows, [(id, reason)])."""
    ok, skipped, seen = [], [], set()
    for pid in public_ids:
        if pid in seen: continue
        seen.add(pid)
        r = db.execute('SELECT u.id,u.public_id,u.email,u.status,u.email_verified,u.is_demo,p.first_name, EXISTS(SELECT 1 FROM user_roles x WHERE x.user_id=u.id) staff '
                       'FROM users u LEFT JOIN user_profiles p ON p.user_id=u.id WHERE u.public_id=?', (pid,)).fetchone()
        if not r: skipped.append((pid, 'customer not found')); continue
        if r['staff']: skipped.append((pid, 'staff account')); continue
        if r['status'] == 'closed': skipped.append((pid, 'account closed')); continue
        if prefs.is_suppressed(db, r['email'], 'marketing' if mail_class == 'marketing' else 'all'): skipped.append((pid, 'address suppressed')); continue
        if mail_class == 'marketing':
            if not prefs.marketing_opted_in(db, r['id']): skipped.append((pid, 'no marketing consent')); continue
            if not r['email_verified']: skipped.append((pid, 'email not verified')); continue
        ok.append(r)
    return ok, skipped
