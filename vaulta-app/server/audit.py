"""Append-only, hash-chained audit log (tamper-evident). DB triggers also block UPDATE/DELETE."""
import hashlib
from . import util
from .security import client_ip, ua

def audit(db, actor_id, action, entity=None, entity_id=None, prev=None, new=None, reason=None):
    last = db.execute('SELECT hash FROM audit_logs ORDER BY id DESC LIMIT 1').fetchone()
    prev_hash = last['hash'] if last else '0' * 64
    t = util.now()
    pv = util.jdump(prev) if prev is not None else None
    nv = util.jdump(new) if new is not None else None
    h = hashlib.sha256('|'.join(str(x) for x in (prev_hash, actor_id, action, entity, entity_id, pv, nv, reason, t)).encode()).hexdigest()
    db.execute('INSERT INTO audit_logs(actor_id,action,entity,entity_id,prev_value,new_value,reason,ip,user_agent,created_at,prev_hash,hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
               (actor_id, action, entity, None if entity_id is None else str(entity_id), pv, nv, reason, client_ip(), ua(), t, prev_hash, h))

def verify_chain(db):
    prev = '0' * 64
    for r in db.execute('SELECT * FROM audit_logs ORDER BY id'):
        exp = hashlib.sha256('|'.join(str(x) for x in (prev, r['actor_id'], r['action'], r['entity'], r['entity_id'], r['prev_value'], r['new_value'], r['reason'], r['created_at'])).encode()).hexdigest()
        if r['prev_hash'] != prev or r['hash'] != exp: return {'ok': False, 'broken_at': r['id']}
        prev = r['hash']
    return {'ok': True}
