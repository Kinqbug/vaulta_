"""Live support chat: ownership, internal-note privacy, ordering, idempotency, unread/read receipts, attachments, assignment, notifications, rate limits."""
import os, sys, io, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_vaulta as tv
from server import security as sec

PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 64
class SupportBase(tv.Base):
    def setUp(self):
        super().setUp(); self.cu = self.signup('cust@example.com'); self.cu2 = self.signup('other@example.com'); self.ag = self.staff('support_admin', 'agent@vaulta.test')
    def new(self, c=None, subject='Where is my deposit?'):
        r = self.req(c or self.cu, 'post', '/api/support/tickets', {'category': 'deposit', 'subject': subject, 'message': 'Hello, my deposit is pending.'}); self.assertEqual(r.status_code, 201, r.json); return r.json['ref']
    def send(self, ref, msg, cid=None, c=None):
        return self.req(c or self.cu, 'post', f'/api/support/conversations/{ref}/messages', {'message': msg, 'client_msg_id': cid} if cid else {'message': msg})
    def reply(self, ref, msg, **kw): return self.req(self.ag, 'post', f'/api/admin/support/{ref}/reply-live', {'message': msg, **kw})

class TestConversation(SupportBase):
    def test_send_poll_order_and_server_side_identity(self):
        ref = self.new(); r = self.send(ref, 'second message'); self.assertEqual(r.status_code, 201); self.assertFalse(r.json['message']['is_staff'])
        r = self.req(self.cu, 'post', f'/api/support/conversations/{ref}/messages', {'message': 'x spoof', 'is_staff': True, 'author_id': 1}); self.assertFalse(r.json['message']['is_staff'])
        self.assertEqual(self.reply(ref, 'Hi, checking now').status_code, 201)
        p = self.cu.get(f'/api/support/conversations/{ref}/poll?after=0').json; ids = [m['id'] for m in p['messages']]
        self.assertEqual(ids, sorted(ids)); self.assertEqual([m['is_staff'] for m in p['messages']], [False, False, False, True]); self.assertEqual(p['status'], 'waiting_for_user')
        last = ids[-1]; self.assertEqual(self.cu.get(f'/api/support/conversations/{ref}/poll?after={last}').json['messages'], [])      # cursor: nothing new
        self.send(ref, 'thanks'); self.assertEqual([m['body'] for m in self.cu.get(f'/api/support/conversations/{ref}/poll?after={last}').json['messages']], ['thanks'])
        self.assertEqual(self.cu.get(f'/api/support/conversations/{ref}/poll?after=abc').status_code, 422)

    def test_duplicate_send_with_same_client_id_creates_one_message(self):
        ref = self.new(); a = self.send(ref, 'retry me', 'client-id-0001'); b = self.send(ref, 'retry me', 'client-id-0001')
        self.assertEqual((a.status_code, b.status_code), (201, 200)); self.assertTrue(b.json['duplicate']); self.assertEqual(a.json['message']['id'], b.json['message']['id'])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM support_messages WHERE body='retry me'").fetchone()[0], 1)
        ra = self.reply(ref, 'answer', client_msg_id='agent-id-0001'); rb = self.reply(ref, 'answer', client_msg_id='agent-id-0001'); self.assertTrue(rb.json['duplicate'])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM email_outbox WHERE event_type='support.reply'").fetchone()[0], 1)      # one email, one notification, despite the retry
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM notifications WHERE title='Support replied'").fetchone()[0], 1)
        self.assertEqual(self.send(ref, 'x', 'bad id!').status_code, 422)

    def test_customers_cannot_touch_other_conversations(self):
        ref = self.new(); self.reply(ref, 'private to cust'); other = self.cu2
        for m, u, b in (('get', f'/api/support/conversations/{ref}/poll', None), ('post', f'/api/support/conversations/{ref}/messages', {'message': 'hi'}), ('post', f'/api/support/tickets/{ref}/reopen', {})):
            self.assertEqual((self.req(other, m, u, b) if m == 'post' else other.get(u)).status_code, 404, u)
        self.assertEqual(self.client().get(f'/api/support/conversations/{ref}/poll').status_code, 401)
        for u in (f'/api/admin/support/{ref}/poll', '/api/admin/support/unread', '/api/admin/support/assignees'): self.assertEqual(self.cu.get(u).status_code, 403, u)
        self.assertEqual(self.req(self.cu, 'post', f'/api/admin/support/{ref}/reply-live', {'message': 'I am staff now'}).status_code, 403)
        self.assertEqual(self.req(self.cu, 'post', f'/api/admin/support/{ref}/assign', {'admin_id': 1}).status_code, 403)

    def test_internal_notes_never_reach_customer_or_email(self):
        ref = self.new(); r = self.reply(ref, 'SECRET internal note', internal=True); self.assertEqual(r.status_code, 201); self.assertTrue(r.json['message']['internal'])
        p = self.cu.get(f'/api/support/conversations/{ref}/poll?after=0'); self.assertNotIn('SECRET', p.get_data(as_text=True))
        self.assertNotIn('SECRET', self.cu.get(f'/api/support/tickets/{ref}').get_data(as_text=True)); self.assertEqual(self.db.execute("SELECT COUNT(*) FROM email_outbox WHERE event_type='support.reply'").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT status FROM support_tickets WHERE ref=?", (ref,)).fetchone()[0], 'open')               # a note does not change the customer-visible state
        self.assertIn('SECRET', self.ag.get(f'/api/admin/support/{ref}/poll?after=0').get_data(as_text=True))

    def test_message_content_is_stored_verbatim_and_limited(self):
        ref = self.new(); xss = '<img src=x onerror=alert(1)>'; self.assertEqual(self.send(ref, xss).json['message']['body'], xss)     # escaping happens at render time (esc() in the UI)
        self.assertEqual(self.send(ref, '   ').status_code, 422); self.assertEqual(self.send(ref, 'a' * 4001).status_code, 422)
        self.assertEqual(self.send(ref, 'ok\x00hidden').json['message']['body'], 'okhidden')

class TestUnreadAndReceipts(SupportBase):
    def test_unread_counts_and_read_receipts(self):
        ref = self.new(); self.assertEqual(self.ag.get('/api/admin/support/unread').json['unread_conversations'], 1)
        self.assertEqual(self.ag.get('/api/admin/support').json['items'][0]['unread'], 1)
        self.reply(ref, 'a'); self.reply(ref, 'b'); self.assertEqual(self.cu.get('/api/support/unread').json['unread_messages'], 2); self.assertEqual(self.cu2.get('/api/support/unread').json['unread_messages'], 0)
        mine = self.cu.get(f'/api/support/conversations/{ref}/poll?after=0').json; self.assertEqual(self.cu.get('/api/support/unread').json['unread_messages'], 0)
        self.assertEqual(mine['read_by_support'], [])                                                                                      # staff have not opened it yet
        self.ag.get(f'/api/admin/support/{ref}/poll?after=0'); self.assertEqual(self.ag.get('/api/admin/support/unread').json['unread_conversations'], 0)
        self.assertEqual(len(self.cu.get(f'/api/support/conversations/{ref}/poll?after=999').json['read_by_support']), 1)

    def test_presence_is_real_not_assumed(self):
        ref = self.new(); self.assertFalse(self.cu.get(f'/api/support/conversations/{ref}/poll').json['staff_online'])
        self.req(self.ag, 'post', '/api/admin/support/heartbeat'); self.assertTrue(self.cu.get(f'/api/support/conversations/{ref}/poll').json['staff_online'])
        self.db.execute("UPDATE support_staff_presence SET last_seen_at='2020-01-01T00:00:00Z'"); self.assertFalse(self.cu.get('/api/support/unread').json['staff_online'])

class TestLifecycle(SupportBase):
    def test_status_flow_close_reopen_and_history(self):
        ref = self.new(); self.reply(ref, 'fixed', status='resolved'); self.assertEqual(self.db.execute("SELECT COUNT(*) FROM email_outbox WHERE event_type='support.resolved'").fetchone()[0], 1)
        p = self.cu.get(f'/api/support/conversations/{ref}/poll').json; self.assertTrue(p['can_reopen'])
        self.assertEqual(self.send(ref, 'still broken').status_code, 201); self.assertEqual(self.db.execute('SELECT status FROM support_tickets WHERE ref=?', (ref,)).fetchone()[0], 'open')     # customer reply reopens a resolved chat
        self.req(self.cu, 'post', f'/api/support/tickets/{ref}/close'); self.assertEqual(self.send(ref, 'late').status_code, 409)
        self.assertEqual(self.reply(ref, 'hello?').status_code, 409)
        self.assertEqual(self.req(self.cu, 'post', f'/api/support/tickets/{ref}/reopen').json['status'], 'open')
        self.req(self.cu, 'post', f'/api/support/tickets/{ref}/close'); self.db.execute("UPDATE support_tickets SET closed_at='2020-01-01T00:00:00Z' WHERE ref=?", (ref,))
        self.assertEqual(self.req(self.cu, 'post', f'/api/support/tickets/{ref}/reopen').status_code, 409)
        h = self.ag.get(f'/api/admin/support/{ref}/history').json; self.assertEqual([x['to_status'] for x in h['status_history']][:2], ['open', 'resolved'])
        self.assertEqual(self.req(self.ag, 'post', f'/api/admin/support/{ref}/reply-live', {'message': 'x', 'status': 'bogus'}).status_code, 422)

    def test_assignment_notifications_and_audit(self):
        ref = self.new(); self.assertEqual(self.db.execute("SELECT COUNT(*) FROM notifications WHERE title='New support conversation'").fetchone()[0], 1)      # staff were told
        ag2 = self.staff('support_admin', 'agent2@vaulta.test'); a2 = self.db.execute("SELECT id FROM users WHERE email='agent2@vaulta.test'").fetchone()[0]
        self.assertEqual(len(self.ag.get('/api/admin/support?assigned=none').json['items']), 1)
        r = self.req(self.ag, 'post', f'/api/admin/support/{ref}/assign', {'admin_id': a2}); self.assertEqual(r.status_code, 200)
        self.assertEqual(self.req(self.ag, 'post', f'/api/admin/support/{ref}/assign', {'admin_id': self.uid('cust@example.com')}).status_code, 422)     # a customer cannot be an assignee
        self.assertEqual(len(self.ag2_items(ag2)), 1); self.assertEqual(len(self.ag.get('/api/admin/support?assigned=none').json['items']), 0)
        self.send(ref, 'ping'); self.assertEqual(self.db.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND title='New customer message'", (a2,)).fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM audit_logs WHERE action='support.assigned'").fetchone()[0], 1)
    def ag2_items(self, c): return c.get('/api/admin/support?assigned=me').json['items']

class TestAttachments(SupportBase):
    def up(self, c, url, data, name='shot.png', **extra):
        h = {'X-CSRF-Token': getattr(c, 'csrf', '')}; return c.post(url, data={'file': (io.BytesIO(data), name), **extra}, headers=h, content_type='multipart/form-data')
    def test_upload_validation_and_authorization(self):
        ref = self.new(); u = f'/api/support/conversations/{ref}/attachments'
        r = self.up(self.cu, u, PNG, message='screenshot', client_msg_id='attach-0001'); self.assertEqual(r.status_code, 201, r.json); aid = r.json['message']['attachments'][0]['id']
        self.assertEqual(self.up(self.cu, u, PNG, message='screenshot', client_msg_id='attach-0001').status_code, 200)                     # retry does not store a second file
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM support_attachments').fetchone()[0], 1)
        self.assertEqual(self.up(self.cu, u, b'<script>alert(1)</script>', name='x.png').status_code, 415)                                  # content is checked, not the extension
        self.assertEqual(self.up(self.cu, u, b'MZ' + b'\0' * 100, name='evil.pdf').status_code, 415)
        self.assertEqual(self.up(self.cu, u, PNG + b'0' * (5 * 1024 * 1024 + 10)).status_code, 413)
        d = self.cu.get(f'/api/support/attachments/{aid}'); self.assertEqual(d.status_code, 200); self.assertEqual(d.headers['X-Content-Type-Options'], 'nosniff'); self.assertIn('sandbox', d.headers['Content-Security-Policy'])
        self.assertEqual(self.ag.get(f'/api/support/attachments/{aid}').status_code, 200)                                                  # staff can preview
        self.assertEqual(self.cu2.get(f'/api/support/attachments/{aid}').status_code, 404); self.assertEqual(self.client().get(f'/api/support/attachments/{aid}').status_code, 401)
        self.assertEqual(self.up(self.cu2, u, PNG).status_code, 404)
        stored = self.db.execute('SELECT stored_name FROM support_attachments').fetchone()[0]; self.assertTrue(os.path.exists(os.path.join(self.app.config['UPLOAD_DIR'], stored)))
        self.assertNotIn(os.path.realpath(self.app.config['WEB_ROOT']), os.path.realpath(self.app.config['UPLOAD_DIR']))
    def test_internal_staff_attachment_hidden_from_customer(self):
        ref = self.new(); r = self.up(self.ag, f'/api/admin/support/{ref}/attachments', PNG, internal='1'); self.assertEqual(r.status_code, 201); aid = r.json['message']['attachments'][0]['id']
        self.assertEqual(self.cu.get(f'/api/support/attachments/{aid}').status_code, 404); self.assertEqual(self.ag.get(f'/api/support/attachments/{aid}').status_code, 200)

class TestAbuseControls(SupportBase):
    def test_rate_limits_apply(self):
        self.app.config['RATE_LIMIT_ENABLED'] = True; sec.reset_rate_limits(); ref = self.new()
        codes = [self.send(ref, f'm{i}').status_code for i in range(32)]; self.assertEqual(codes.count(429), 2); self.assertTrue(all(c == 201 for c in codes[:30]))
    def test_csrf_required_on_mutations(self):
        ref = self.new(); r = self.cu.post(f'/api/support/conversations/{ref}/messages', json={'message': 'x'}); self.assertEqual(r.status_code, 403)

if __name__ == '__main__': unittest.main()
