"""Community chat service. Identity always comes from the validated session; the client never supplies a user id, role or name for the sender.
Messages are persisted in SQLite (immutable text; moderators can only remove). Delivery to other clients is by cursor polling (`after=<id>`),
presence is a heartbeat table. Authorisation (channel write policy, mutes, blocks) is enforced here, on the server."""
import re
from datetime import timedelta
from . import util, security as sec
from .util import ApiError
from .db import one, all_, val, tx
from .audit import audit

MAX_LEN = 500; BURST = (5, 10); DUP_SECONDS = 60; MAX_LINKS = 2; PRESENCE_SECONDS = 45
URL_RE = re.compile(r'(?:https?://|www\.)\S+', re.I)
CTRL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u202a-\u202e\u2066-\u2069]')     # control and bidi-override characters
REASONS = ('spam', 'abuse', 'scam', 'impersonation', 'other')

def _ago(**kw): return util.ts_after(**{k: -v for k, v in kw.items()})
def is_mod(db, user): return 'chat.moderate' in sec.user_perms(db, user['id'])
def is_staff(db, uid): return bool(val(db, 'SELECT 1 FROM user_roles WHERE user_id=?', uid))

def display(first, last, public_id):
    f = (first or '').strip(); l = (last or '').strip()
    name = (f + (' ' + l[:1].upper() + '.' if l else '')).strip() or 'Member ' + (public_id or '')[-4:]
    ini = ((f[:1] + l[:1]) or name[:1]).upper()
    return name, ini

def _user_card(db, uid):
    r = one(db, 'SELECT u.public_id,p.first_name,p.last_name FROM users u LEFT JOIN user_profiles p ON p.user_id=u.id WHERE u.id=?', uid)
    if not r: return {'id': None, 'name': 'Unknown', 'initials': '?', 'staff': False}
    n, i = display(r['first_name'], r['last_name'], r['public_id']); return {'id': r['public_id'], 'name': n, 'initials': i, 'staff': is_staff(db, uid)}

def channel(db, slug):
    c = one(db, 'SELECT * FROM chat_channels WHERE slug=? AND archived=0', slug or '')
    if not c: raise ApiError(404, 'not_found', 'Channel not found')
    return c

def active_mute(db, uid, channel_id):
    return one(db, 'SELECT * FROM chat_mutes WHERE user_id=? AND revoked_at IS NULL AND until>? AND (channel_id IS NULL OR channel_id=?) ORDER BY until DESC LIMIT 1', uid, util.now(), channel_id)

def can_write(db, user, c, perms=None):
    if c['write_policy'] == 'staff': return 'chat.moderate' in (perms if perms is not None else sec.user_perms(db, user['id']))
    return bool(user['email_verified']) and not active_mute(db, user['id'], c['id'])

def channels(db, user):
    perms = sec.user_perms(db, user['id']); since = _ago(seconds=PRESENCE_SECONDS); out = []
    for c in all_(db, 'SELECT * FROM chat_channels WHERE archived=0 ORDER BY sort_order,slug'):
        online = val(db, 'SELECT COUNT(*) FROM chat_presence WHERE channel_id=? AND last_seen>?', c['id'], since) or 0
        out.append({'slug': c['slug'], 'name': c['name'], 'description': c['description'], 'kind': c['kind'], 'coin_id': c['coin_id'], 'read_only': c['write_policy'] == 'staff' and 'chat.moderate' not in perms,
                    'can_write': can_write(db, user, c, perms), 'online': online, 'last_id': val(db, 'SELECT MAX(id) FROM chat_messages WHERE channel_id=?', c['id']) or 0})
    return out

def _msg(db, r, viewer_id, cache):
    if r['status'] == 'removed': return {'id': r['id'], 'removed': True, 'created_at': r['created_at']}
    u = cache.get(r['user_id']) or cache.setdefault(r['user_id'], _user_card(db, r['user_id']))
    rep = None
    if r['reply_to']:
        p = one(db, 'SELECT id,user_id,content,status FROM chat_messages WHERE id=?', r['reply_to'])
        if p and p['status'] == 'visible':
            pu = cache.get(p['user_id']) or cache.setdefault(p['user_id'], _user_card(db, p['user_id'])); rep = {'id': p['id'], 'name': pu['name'], 'snippet': p['content'][:80]}
    return {'id': r['id'], 'user': u, 'content': r['content'], 'created_at': r['created_at'], 'reply_to': rep, 'mine': r['user_id'] == viewer_id}

def history(db, user, slug, before=None, after=None, limit=50):
    c = channel(db, slug); limit = max(1, min(int(limit or 50), 100)); w, a = ['m.channel_id=?', 'm.user_id NOT IN (SELECT blocked_user_id FROM chat_blocks WHERE user_id=?)'], [c['id'], user['id']]
    if after is not None: w.append('m.id>?'); a.append(int(after))
    if before is not None: w.append('m.id<?'); a.append(int(before))
    order = 'ASC' if after is not None else 'DESC'
    rows = all_(db, f"SELECT m.* FROM chat_messages m WHERE {' AND '.join(w)} ORDER BY m.id {order} LIMIT ?", *a, limit)
    if order == 'DESC': rows.reverse()
    cache = {}; msgs = [_msg(db, r, user['id'], cache) for r in rows]
    return {'channel': c['slug'], 'messages': msgs, 'has_more': len(rows) == limit and after is None, 'can_write': can_write(db, user, c)}

def touch_presence(db, uid, channel_id):
    r = one(db, 'SELECT last_seen FROM chat_presence WHERE user_id=? AND channel_id=?', uid, channel_id)
    if r and r['last_seen'] > _ago(seconds=15): return
    db.execute('DELETE FROM chat_presence WHERE user_id=? AND channel_id!=?', (uid, channel_id))        # one active channel per user
    db.execute('INSERT INTO chat_presence(user_id,channel_id,last_seen) VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_seen=excluded.last_seen', (uid, channel_id, util.now()))

def presence(db, slug):
    c = channel(db, slug); rows = all_(db, 'SELECT user_id FROM chat_presence WHERE channel_id=? AND last_seen>? ORDER BY last_seen DESC LIMIT 30', c['id'], _ago(seconds=PRESENCE_SECONDS))
    cnt = val(db, 'SELECT COUNT(*) FROM chat_presence WHERE channel_id=? AND last_seen>?', c['id'], _ago(seconds=PRESENCE_SECONDS)) or 0
    return {'count': cnt, 'users': [{'name': _user_card(db, r['user_id'])['name']} for r in rows[:12]]}

def clean_content(text):
    if not isinstance(text, str): raise ApiError(422, 'invalid_content', 'Message must be text.')
    t = CTRL_RE.sub('', text).replace('\r\n', '\n').strip(); t = re.sub(r'\n{3,}', '\n\n', t)
    if not t: raise ApiError(422, 'empty_message', 'Message cannot be empty.')
    if len(t) > MAX_LEN: raise ApiError(422, 'message_too_long', f'Messages are limited to {MAX_LEN} characters.')
    if len(URL_RE.findall(t)) > MAX_LINKS: raise ApiError(422, 'too_many_links', f'Messages can contain at most {MAX_LINKS} links.')
    if re.search(r'(.)\1{24,}', t): raise ApiError(422, 'spam_detected', 'That message looks like spam.')
    return t

def send(db, user, slug, content, nonce=None, reply_to=None):
    c = channel(db, slug)
    if not user['email_verified']: raise ApiError(403, 'email_unverified', 'Verify your email address to post in the community.')
    perms = sec.user_perms(db, user['id'])
    if c['write_policy'] == 'staff' and 'chat.moderate' not in perms: raise ApiError(403, 'channel_read_only', 'Only Vaulta staff can post in this channel.')
    m = active_mute(db, user['id'], c['id'])
    if m: raise ApiError(403, 'muted', 'You are muted in this channel until ' + m['until'] + '.', {'until': m['until']})
    text = clean_content(content)
    if nonce is not None and not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', str(nonce)): raise ApiError(422, 'invalid_nonce', 'Invalid message token.')
    with tx(db):
        if nonce:
            ex = one(db, 'SELECT * FROM chat_messages WHERE user_id=? AND client_nonce=?', user['id'], nonce)
            if ex: return _msg(db, ex, user['id'], {}), True                  # retry of an already-stored message: no duplicate
        if (val(db, 'SELECT COUNT(*) FROM chat_messages WHERE user_id=? AND created_at>?', user['id'], _ago(seconds=BURST[1])) or 0) >= BURST[0]:
            raise ApiError(429, 'slow_down', 'You are sending messages too quickly. Please wait a few seconds.')
        if one(db, 'SELECT 1 FROM chat_messages WHERE user_id=? AND channel_id=? AND content=? AND created_at>? AND status=?', user['id'], c['id'], text, _ago(seconds=DUP_SECONDS), 'visible'):
            raise ApiError(409, 'duplicate_message', 'You just sent that message.')
        rid = None
        if reply_to is not None:
            p = one(db, "SELECT id FROM chat_messages WHERE id=? AND channel_id=? AND status='visible'", int(reply_to), c['id'])
            if not p: raise ApiError(422, 'invalid_reply', 'The message you are replying to is not available.')
            rid = p['id']
        cur = db.execute('INSERT INTO chat_messages(channel_id,user_id,content,client_nonce,reply_to,created_at) VALUES(?,?,?,?,?,?)', (c['id'], user['id'], text, nonce, rid, util.now()))
        touch_presence(db, user['id'], c['id'])
        row = one(db, 'SELECT * FROM chat_messages WHERE id=?', cur.lastrowid)
    return _msg(db, row, user['id'], {}), False

# ---------------------------------------------------------------- reports, blocks, moderation
def report(db, user, message_id, reason, details=None):
    if reason not in REASONS: raise ApiError(422, 'invalid_reason', 'Choose a report reason: ' + ', '.join(REASONS))
    m = one(db, 'SELECT * FROM chat_messages WHERE id=?', int(message_id))
    if not m or m['status'] != 'visible': raise ApiError(404, 'not_found', 'Message not found')
    if m['user_id'] == user['id']: raise ApiError(422, 'own_message', 'You cannot report your own message.')
    details = ' '.join(str(details or '').split())[:300]
    try:
        with tx(db): db.execute('INSERT INTO chat_reports(message_id,reporter_id,reason,details,created_at) VALUES(?,?,?,?,?)', (m['id'], user['id'], reason, details, util.now()))
    except Exception as e:
        if 'UNIQUE' in str(e): raise ApiError(409, 'already_reported', 'You have already reported this message.')
        raise
    return {'ok': True}

def remove_message(db, actor, message_id, reason=None):
    with tx(db):
        m = one(db, 'SELECT * FROM chat_messages WHERE id=?', int(message_id))
        if not m or m['status'] == 'removed': raise ApiError(404, 'not_found', 'Message not found')
        mod = is_mod(db, actor)
        if m['user_id'] != actor['id'] and not mod: raise ApiError(403, 'forbidden', 'You can only remove your own messages.')
        db.execute("UPDATE chat_messages SET status='removed',removed_by=?,removed_reason=?,removed_at=? WHERE id=?", (actor['id'], ' '.join(str(reason or '').split())[:200] or ('removed by author' if m['user_id'] == actor['id'] else 'removed by moderator'), util.now(), m['id']))
        if mod and m['user_id'] != actor['id']:
            audit(db, actor['id'], 'chat.message_removed', 'chat_message', m['id'], None, {'author_public_id': _user_card(db, m['user_id'])['id'], 'reason': reason})
            db.execute("UPDATE chat_reports SET status='actioned',resolved_by=?,resolved_at=? WHERE message_id=? AND status='open'", (actor['id'], util.now(), m['id']))

def _user_by_public(db, public_id):
    u = one(db, 'SELECT id,public_id FROM users WHERE public_id=?', str(public_id or ''))
    if not u: raise ApiError(404, 'not_found', 'User not found')
    return u

def mute(db, actor, public_id, minutes, slug=None, reason=None):
    if not is_mod(db, actor): raise ApiError(403, 'forbidden', 'Moderator permission required.')
    try: minutes = int(minutes)
    except (TypeError, ValueError): raise ApiError(422, 'invalid_duration', 'Duration must be a whole number of minutes.')
    if not 1 <= minutes <= 43200: raise ApiError(422, 'invalid_duration', 'Duration must be between 1 minute and 30 days.')
    u = _user_by_public(db, public_id)
    if u['id'] == actor['id']: raise ApiError(422, 'self_mute', 'You cannot mute yourself.')
    if is_staff(db, u['id']): raise ApiError(403, 'forbidden', 'Staff accounts cannot be muted.')
    cid = channel(db, slug)['id'] if slug else None; until = util.ts_after(minutes=minutes)
    with tx(db):
        db.execute('INSERT INTO chat_mutes(user_id,channel_id,until,reason,created_by,created_at) VALUES(?,?,?,?,?,?)', (u['id'], cid, until, ' '.join(str(reason or '').split())[:200], actor['id'], util.now()))
        audit(db, actor['id'], 'chat.user_muted', 'user', u['public_id'], None, {'until': until, 'channel': slug, 'reason': reason})
    return {'until': until}

def unmute(db, actor, public_id):
    if not is_mod(db, actor): raise ApiError(403, 'forbidden', 'Moderator permission required.')
    u = _user_by_public(db, public_id)
    with tx(db):
        n = db.execute('UPDATE chat_mutes SET revoked_at=?,revoked_by=? WHERE user_id=? AND revoked_at IS NULL AND until>?', (util.now(), actor['id'], u['id'], util.now())).rowcount
        audit(db, actor['id'], 'chat.user_unmuted', 'user', u['public_id'], None, {'revoked': n})
    return {'revoked': n}

def list_reports(db, actor, status='open', limit=50):
    if not is_mod(db, actor): raise ApiError(403, 'forbidden', 'Moderator permission required.')
    if status not in ('open', 'actioned', 'dismissed'): raise ApiError(422, 'invalid_status', 'Invalid status')
    rows = all_(db, 'SELECT r.id,r.reason,r.details,r.status,r.created_at,r.message_id,m.content,m.status mstatus,m.user_id author,c.slug FROM chat_reports r JOIN chat_messages m ON m.id=r.message_id JOIN chat_channels c ON c.id=m.channel_id WHERE r.status=? ORDER BY r.id DESC LIMIT ?', status, min(int(limit), 100))
    return [{'id': r['id'], 'reason': r['reason'], 'details': r['details'], 'status': r['status'], 'created_at': r['created_at'], 'message_id': r['message_id'], 'channel': r['slug'], 'content': r['content'], 'message_removed': r['mstatus'] == 'removed', 'author': _user_card(db, r['author'])} for r in rows]

def resolve_report(db, actor, rid, action):
    if not is_mod(db, actor): raise ApiError(403, 'forbidden', 'Moderator permission required.')
    if action not in ('actioned', 'dismissed'): raise ApiError(422, 'invalid_action', 'Action must be actioned or dismissed.')
    with tx(db):
        r = one(db, 'SELECT * FROM chat_reports WHERE id=?', int(rid))
        if not r: raise ApiError(404, 'not_found', 'Report not found')
        db.execute('UPDATE chat_reports SET status=?,resolved_by=?,resolved_at=? WHERE id=?', (action, actor['id'], util.now(), r['id']))
        audit(db, actor['id'], 'chat.report_resolved', 'chat_report', r['id'], {'status': r['status']}, {'status': action})

def block(db, user, public_id):
    u = _user_by_public(db, public_id)
    if u['id'] == user['id']: raise ApiError(422, 'self_block', 'You cannot block yourself.')
    db.execute('INSERT OR IGNORE INTO chat_blocks(user_id,blocked_user_id,created_at) VALUES(?,?,?)', (user['id'], u['id'], util.now()))
def unblock(db, user, public_id):
    u = _user_by_public(db, public_id); db.execute('DELETE FROM chat_blocks WHERE user_id=? AND blocked_user_id=?', (user['id'], u['id']))
def blocked(db, user):
    return [_user_card(db, r['blocked_user_id']) for r in db.execute('SELECT blocked_user_id FROM chat_blocks WHERE user_id=? ORDER BY created_at DESC', (user['id'],))]
