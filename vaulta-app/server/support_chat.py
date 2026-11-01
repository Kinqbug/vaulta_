"""Live customer-support conversations on top of the existing ticket tables.

Transport: authenticated short polling (GET .../poll?after=<message id>), which works behind any proxy and needs no extra process. Messages are ordered by
their server-assigned id, timestamps are server-generated, the sender role is derived from the session (never from the request), internal staff notes are
never returned on a customer endpoint, a retried send is de-duplicated by client_msg_id, and every mutation is rate-limited and CSRF-protected by the app hook.
Presence is only reported from a real staff heartbeat (an admin with the inbox open in the last 90 seconds); otherwise the customer is told support is offline.
"""
import re
from datetime import timedelta
from flask import Blueprint, request, jsonify, g, current_app, Response
from . import util, security as sec
from .util import ApiError, text
from .db import get_db, one, all_, tx
from .audit import audit
from .notify import notify
from .mailer import events as mail
from .routes_auth import body

cust = Blueprint('support_live', __name__, url_prefix='/api/support')
staff = Blueprint('support_live_admin', __name__, url_prefix='/api/admin/support')
P = sec.require_perm
STATUSES = ('open', 'in_progress', 'waiting_for_user', 'resolved', 'closed')
REOPEN_DAYS = 14
ONLINE_SECONDS = 90
MAX_BODY = 4000
_CID = re.compile(r'^[A-Za-z0-9_-]{8,64}$')
_CTRL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')

def clean(msg):
    m = _CTRL.sub('', (msg or '')).strip()
    if not m: raise ApiError(422, 'missing_fields', 'Write a message first.')
    if len(m) > MAX_BODY: raise ApiError(422, 'too_long', f'Messages can be at most {MAX_BODY} characters.')
    return m

def cid(b):
    c = b.get('client_msg_id')
    if c is None: return None
    if not isinstance(c, str) or not _CID.match(c): raise ApiError(422, 'invalid_client_id', 'client_msg_id must be 8-64 letters, digits, - or _.')
    return c

def hist(db, tid, actor, old, new):
    if old != new: db.execute('INSERT INTO support_status_history(ticket_id,actor_id,from_status,to_status,created_at) VALUES(?,?,?,?,?)', (tid, actor, old, new, util.now()))

def staff_online(db):
    since = util.ts_after(seconds=-ONLINE_SECONDS)
    return bool(db.execute('SELECT 1 FROM support_staff_presence WHERE last_seen_at>=?', (since,)).fetchone())

def staff_ids(db):
    return [r[0] for r in db.execute("SELECT DISTINCT ur.user_id FROM user_roles ur JOIN role_permissions rp ON rp.role_id=ur.role_id JOIN permissions p ON p.id=rp.permission_id JOIN users u ON u.id=ur.user_id WHERE p.code='support.manage' AND u.status='active'")]

def my_ticket(db, ref):
    t = one(db, 'SELECT * FROM support_tickets WHERE ref=? AND user_id=?', ref, g.user['id'])      # ownership enforced in the query: other customers' refs are simply 404
    if not t: raise ApiError(404, 'not_found', 'Conversation not found')
    return t

def msg_dict(db, m, staff_view=False):
    d = {'id': m['id'], 'is_staff': bool(m['is_staff']), 'body': m['body'], 'created_at': m['created_at'], 'client_msg_id': m['client_msg_id'],
         'attachments': [{'id': a['id'], 'name': a['original_name'], 'mime': a['mime'], 'size': a['size']} for a in db.execute('SELECT id,original_name,mime,size FROM support_attachments WHERE message_id=?', (m['id'],))]}
    if staff_view: d.update(internal=bool(m['internal']), author=m['author'] if 'author' in m.keys() else None, read_by_customer_at=m['read_by_customer_at'], read_by_staff_at=m['read_by_staff_at'])
    else: d['read'] = bool(m['read_by_staff_at']) if not m['is_staff'] else bool(m['read_by_customer_at'])        # customer sees whether support has read their message
    return d

def post_message(db, t, author, is_staff, message, client_id, internal=False):
    """Insert (idempotently) and return (message_row, created)."""
    cur = db.execute('INSERT OR IGNORE INTO support_messages(ticket_id,author_id,is_staff,internal,body,created_at,client_msg_id) VALUES(?,?,?,?,?,?,?)',
                     (t['id'], author, 1 if is_staff else 0, 1 if internal else 0, message, util.now(), client_id))
    if cur.rowcount: return db.execute('SELECT * FROM support_messages WHERE id=?', (cur.lastrowid,)).fetchone(), True
    return db.execute('SELECT * FROM support_messages WHERE ticket_id=? AND author_id=? AND client_msg_id=?', (t['id'], author, client_id)).fetchone(), False

def store_attachment(db, t, mid, uploader):
    f = request.files.get('file')
    if not f: raise ApiError(422, 'missing_fields', 'Choose a file to attach.')
    data = f.read(5 * 1024 * 1024 + 1)
    name, mime, digest = current_app.providers.storage.save(data)      # verifies real content (JPEG/PNG/PDF), 5 MB cap, random private name outside the web root
    orig = re.sub(r'[^A-Za-z0-9._ -]', '_', (f.filename or 'attachment'))[:100] or 'attachment'
    return db.execute('INSERT INTO support_attachments(ticket_id,message_id,uploaded_by,stored_name,original_name,mime,size,sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?)', (t['id'], mid, uploader, name, orig, mime, len(data), digest, util.now())).lastrowid

# ============================================================ customer
@cust.post('/conversations/<ref>/messages')
@sec.login_required
@sec.rate_limit('support_msg', 30, 600)
def send(ref):
    db, b = get_db(), body(); t = my_ticket(db, ref); message = clean(b.get('message')); client_id = cid(b)
    if t['status'] == 'closed': raise ApiError(409, 'closed', 'This conversation is closed. Reopen it to continue.')
    with tx(db):
        m, created = post_message(db, t, g.user['id'], False, message, client_id)
        if created:
            hist(db, t['id'], g.user['id'], t['status'], 'open')
            db.execute("UPDATE support_tickets SET status='open',updated_at=?,closed_at=NULL WHERE id=?", (util.now(), t['id']))
            if t['assigned_to']: notify(db, t['assigned_to'], 'system', 'New customer message', f"{t['ref']}: {t['subject']}", '#/support')
    return jsonify({'message': msg_dict(db, m), 'duplicate': not created}), (201 if created else 200)

@cust.post('/conversations/<ref>/attachments')
@sec.login_required
@sec.rate_limit('support_upload', 10, 3600)
def attach(ref):
    db = get_db(); t = my_ticket(db, ref)
    if t['status'] == 'closed': raise ApiError(409, 'closed', 'This conversation is closed. Reopen it to continue.')
    note = _CTRL.sub('', request.form.get('message', '')).strip()[:MAX_BODY] or '(attachment)'
    with tx(db):
        m, created = post_message(db, t, g.user['id'], False, note, cid({'client_msg_id': request.form.get('client_msg_id')}))
        if not created: return jsonify({'message': msg_dict(db, m), 'duplicate': True}), 200
        store_attachment(db, t, m['id'], g.user['id'])
        hist(db, t['id'], g.user['id'], t['status'], 'open'); db.execute("UPDATE support_tickets SET status='open',updated_at=?,closed_at=NULL WHERE id=?", (util.now(), t['id']))
    return jsonify({'message': msg_dict(db, m)}), 201

@cust.get('/conversations/<ref>/poll')
@sec.login_required
@sec.rate_limit('support_poll', 240, 600)
def poll(ref):
    db = get_db(); t = my_ticket(db, ref)
    try: after = max(0, int(request.args.get('after', 0)))
    except ValueError: raise ApiError(422, 'invalid_cursor', 'after must be a number')
    rows = db.execute('SELECT * FROM support_messages WHERE ticket_id=? AND internal=0 AND id>? ORDER BY id LIMIT 51', (t['id'], after)).fetchall()
    more = len(rows) > 50; rows = rows[:50]
    ids = [r['id'] for r in rows if r['is_staff'] and not r['read_by_customer_at']]
    if ids:
        db.execute(f"UPDATE support_messages SET read_by_customer_at=? WHERE id IN ({','.join('?' * len(ids))})", (util.now(), *ids))
    # read receipts for older customer messages can change after the cursor, so return the ids support has read so far
    read_ids = [r[0] for r in db.execute('SELECT id FROM support_messages WHERE ticket_id=? AND is_staff=0 AND read_by_staff_at IS NOT NULL', (t['id'],))]
    rows = [db.execute('SELECT * FROM support_messages WHERE id=?', (r['id'],)).fetchone() for r in rows]
    return {'messages': [msg_dict(db, r) for r in rows], 'has_more': more, 'status': t['status'], 'staff_online': staff_online(db), 'assigned': bool(t['assigned_to']), 'read_by_support': read_ids,
            'can_reopen': t['status'] in ('resolved', 'closed') and (t['status'] == 'resolved' or not t['closed_at'] or t['closed_at'] >= util.ts_after(days=-REOPEN_DAYS)),
            'server_time': util.now()}

@cust.get('/unread')
@sec.login_required
def unread():
    db = get_db()
    n = db.execute('SELECT COUNT(*) c, COUNT(DISTINCT m.ticket_id) t FROM support_messages m JOIN support_tickets k ON k.id=m.ticket_id WHERE k.user_id=? AND m.is_staff=1 AND m.internal=0 AND m.read_by_customer_at IS NULL', (g.user['id'],)).fetchone()
    return {'unread_messages': n['c'], 'unread_conversations': n['t'], 'staff_online': staff_online(db)}

@cust.post('/tickets/<ref>/reopen')
@sec.login_required
@sec.rate_limit('support_reopen', 10, 3600)
def reopen(ref):
    db = get_db(); t = my_ticket(db, ref)
    if t['status'] not in ('resolved', 'closed'): raise ApiError(409, 'not_closed', 'This conversation is already open.')
    if t['status'] == 'closed' and t['closed_at'] and t['closed_at'] < util.ts_after(days=-REOPEN_DAYS): raise ApiError(409, 'reopen_window_passed', f'Conversations can be reopened for {REOPEN_DAYS} days after closing. Please start a new one.')
    with tx(db):
        hist(db, t['id'], g.user['id'], t['status'], 'open'); db.execute("UPDATE support_tickets SET status='open',closed_at=NULL,updated_at=? WHERE id=?", (util.now(), t['id']))
        audit(db, g.user['id'], 'support.reopened', 'ticket', ref, {'status': t['status']}, {'status': 'open'})
    return {'ok': True, 'status': 'open'}

@cust.get('/attachments/<int:aid>')
@sec.login_required
def download(aid):
    db = get_db(); a = one(db, 'SELECT a.*, t.user_id owner, m.internal FROM support_attachments a JOIN support_tickets t ON t.id=a.ticket_id JOIN support_messages m ON m.id=a.message_id WHERE a.id=?', aid)
    is_staff = _has_support_perm(db)
    if not a or (a['owner'] != g.user['id'] and not is_staff) or (a['internal'] and not is_staff): raise ApiError(404, 'not_found', 'Attachment not found')
    with open(current_app.providers.storage.path(a['stored_name']), 'rb') as f: data = f.read()
    inline = a['mime'].startswith('image/')
    return Response(data, mimetype=a['mime'], headers={'Content-Disposition': ('inline' if inline else 'attachment') + '; filename="' + a['original_name'].replace('"', '') + '"', 'X-Content-Type-Options': 'nosniff',
                                                       'Content-Security-Policy': "default-src 'none'; sandbox", 'Cache-Control': 'private, no-store'})

def _has_support_perm(db):
    return bool(db.execute("SELECT 1 FROM user_roles ur JOIN role_permissions rp ON rp.role_id=ur.role_id JOIN permissions p ON p.id=rp.permission_id WHERE ur.user_id=? AND p.code='support.manage'", (g.user['id'],)).fetchone())

# ============================================================ staff
def _beat(db): db.execute('INSERT INTO support_staff_presence(user_id,last_seen_at) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET last_seen_at=excluded.last_seen_at', (g.user['id'], util.now()))

@staff.post('/heartbeat')
@P('support.manage')
def heartbeat(): _beat(get_db()); return {'ok': True}

@staff.get('/unread')
@P('support.manage')
def s_unread():
    db = get_db(); _beat(db)
    r = db.execute("SELECT COUNT(DISTINCT m.ticket_id) t, COUNT(*) c FROM support_messages m JOIN support_tickets k ON k.id=m.ticket_id WHERE m.is_staff=0 AND m.read_by_staff_at IS NULL AND k.status<>'closed'").fetchone()
    return {'unread_conversations': r['t'], 'unread_messages': r['c'], 'unassigned': db.execute("SELECT COUNT(*) FROM support_tickets WHERE assigned_to IS NULL AND status NOT IN('resolved','closed')").fetchone()[0]}

@staff.get('/<ref>/poll')
@P('support.manage')
@sec.rate_limit('support_poll_staff', 600, 600)
def s_poll(ref):
    db = get_db(); t = one(db, 'SELECT * FROM support_tickets WHERE ref=?', ref)
    if not t: raise ApiError(404, 'not_found', 'Conversation not found')
    _beat(db)
    try: after = max(0, int(request.args.get('after', 0)))
    except ValueError: raise ApiError(422, 'invalid_cursor', 'after must be a number')
    rows = db.execute('SELECT m.*, u.email author FROM support_messages m JOIN users u ON u.id=m.author_id WHERE m.ticket_id=? AND m.id>? ORDER BY m.id LIMIT 51', (t['id'], after)).fetchall()
    more = len(rows) > 50; rows = rows[:50]
    ids = [r['id'] for r in rows if not r['is_staff'] and not r['read_by_staff_at']]
    if ids: db.execute(f"UPDATE support_messages SET read_by_staff_at=? WHERE id IN ({','.join('?' * len(ids))})", (util.now(), *ids))
    return {'messages': [msg_dict(db, r, True) for r in rows], 'has_more': more, 'status': t['status'], 'assigned_to': t['assigned_to'], 'server_time': util.now()}

@staff.post('/<ref>/reply-live')
@P('support.manage')
@sec.rate_limit('support_reply', 120, 600)
def s_reply(ref):
    """Staff reply with idempotency (client_msg_id) and optional status change. Internal notes are never shown to customers nor emailed."""
    db, b = get_db(), body(); t = one(db, 'SELECT * FROM support_tickets WHERE ref=?', ref)
    if not t: raise ApiError(404, 'not_found', 'Conversation not found')
    message = clean(b.get('message')); internal = bool(b.get('internal')); client_id = cid(b); new_status = b.get('status')
    if new_status is not None and new_status not in STATUSES: raise ApiError(422, 'invalid_status', 'Invalid status')
    if t['status'] == 'closed' and not internal: raise ApiError(409, 'closed', 'Closed conversations cannot receive customer-visible replies. Reopen it first.')
    with tx(db):
        m, created = post_message(db, t, g.user['id'], True, message, client_id, internal)
        if created and not internal:
            st = new_status or ('waiting_for_user' if t['status'] in ('open', 'in_progress') else t['status'])
            hist(db, t['id'], g.user['id'], t['status'], st)
            db.execute('UPDATE support_tickets SET status=?,updated_at=?,assigned_to=COALESCE(assigned_to,?),closed_at=? WHERE id=?', (st, util.now(), g.user['id'], util.now() if st == 'closed' else None, t['id']))
            notify(db, t['user_id'], 'system', 'Support replied', f"New reply on {t['ref']}: {t['subject']}", '#/support')
            mail.emit(db, 'support.reply', t['user_id'], t['ref'], qualifier=str(m['id']))
            if st in ('resolved', 'closed') and st != t['status']: mail.emit(db, 'support.' + st, t['user_id'], t['ref'], qualifier=f"{st}:{m['id']}")
        if created: audit(db, g.user['id'], 'support.internal_note' if internal else 'support.reply', 'ticket', ref, None, {'message_id': m['id']})
    return jsonify({'message': msg_dict(db, m, True), 'duplicate': not created}), (201 if created else 200)

@staff.post('/<ref>/attachments')
@P('support.manage')
@sec.rate_limit('support_upload_staff', 30, 3600)
def s_attach(ref):
    db = get_db(); t = one(db, 'SELECT * FROM support_tickets WHERE ref=?', ref)
    if not t: raise ApiError(404, 'not_found', 'Conversation not found')
    internal = request.form.get('internal') in ('1', 'true')
    with tx(db):
        m, created = post_message(db, t, g.user['id'], True, _CTRL.sub('', request.form.get('message', '')).strip()[:MAX_BODY] or '(attachment)', cid({'client_msg_id': request.form.get('client_msg_id')}), internal)
        if not created: return jsonify({'message': msg_dict(db, m, True), 'duplicate': True}), 200
        store_attachment(db, t, m['id'], g.user['id'])
        if not internal:
            hist(db, t['id'], g.user['id'], t['status'], 'waiting_for_user'); db.execute("UPDATE support_tickets SET status='waiting_for_user',updated_at=?,assigned_to=COALESCE(assigned_to,?) WHERE id=?", (util.now(), g.user['id'], t['id']))
            notify(db, t['user_id'], 'system', 'Support replied', f"New reply on {t['ref']}: {t['subject']}", '#/support'); mail.emit(db, 'support.reply', t['user_id'], t['ref'], qualifier=str(m['id']))
        audit(db, g.user['id'], 'support.attachment_added', 'ticket', ref, None, {'message_id': m['id']})
    return jsonify({'message': msg_dict(db, m, True)}), 201

@staff.post('/<ref>/assign')
@P('support.manage')
def assign(ref):
    """Assign to any active staff member who holds support.manage (or unassign with admin_id=null)."""
    db, b = get_db(), body(); t = one(db, 'SELECT * FROM support_tickets WHERE ref=?', ref)
    if not t: raise ApiError(404, 'not_found', 'Conversation not found')
    target = b.get('admin_id')
    if target is not None:
        if not isinstance(target, int) or target not in staff_ids(db): raise ApiError(422, 'invalid_assignee', 'That person cannot be assigned support conversations.')
    with tx(db):
        db.execute('UPDATE support_tickets SET assigned_to=?,updated_at=? WHERE id=?', (target, util.now(), t['id']))
        audit(db, g.user['id'], 'support.assigned', 'ticket', ref, {'assigned_to': t['assigned_to']}, {'assigned_to': target}, text(b.get('reason'), 'reason', 300, False) or None)
        if target and target != g.user['id']: notify(db, target, 'system', 'Conversation assigned to you', f"{t['ref']}: {t['subject']}", '#/support')
    return {'ok': True, 'assigned_to': target}

@staff.get('/assignees')
@P('support.manage')
def assignees():
    db = get_db(); ids = staff_ids(db)
    return {'items': [{'id': r['id'], 'email': r['email']} for r in db.execute(f"SELECT id,email FROM users WHERE id IN ({','.join('?' * len(ids))}) ORDER BY email", ids)] if ids else []}

@staff.get('/<ref>/history')
@P('support.manage')
def history(ref):
    db = get_db(); t = one(db, 'SELECT id FROM support_tickets WHERE ref=?', ref)
    if not t: raise ApiError(404, 'not_found', 'Conversation not found')
    return {'status_history': all_(db, 'SELECT h.from_status,h.to_status,h.created_at,u.email actor FROM support_status_history h LEFT JOIN users u ON u.id=h.actor_id WHERE h.ticket_id=? ORDER BY h.id', t['id']),
            'audit': all_(db, "SELECT action,created_at,actor_id FROM audit_logs WHERE entity='ticket' AND entity_id=? ORDER BY id", ref)}
