-- Live customer-support conversations: extends support_tickets / support_messages (no parallel system).
ALTER TABLE support_messages ADD COLUMN client_msg_id TEXT;            -- client-generated id: a retried send never duplicates a message
ALTER TABLE support_messages ADD COLUMN read_by_customer_at TEXT;      -- set when the customer's client fetched a staff message
ALTER TABLE support_messages ADD COLUMN read_by_staff_at TEXT;         -- set when staff opened a customer message
ALTER TABLE support_tickets ADD COLUMN closed_at TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_support_msg_client ON support_messages(ticket_id, author_id, client_msg_id) WHERE client_msg_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_support_msg_ticket ON support_messages(ticket_id, id);
CREATE INDEX IF NOT EXISTS idx_support_ticket_user ON support_tickets(user_id, updated_at);
CREATE TABLE IF NOT EXISTS support_attachments(
  id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES support_tickets(id), message_id INTEGER NOT NULL REFERENCES support_messages(id),
  uploaded_by INTEGER NOT NULL, stored_name TEXT NOT NULL, original_name TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_support_att_msg ON support_attachments(message_id);
CREATE TABLE IF NOT EXISTS support_status_history(
  id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES support_tickets(id), actor_id INTEGER, from_status TEXT, to_status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS support_staff_presence(user_id INTEGER PRIMARY KEY REFERENCES users(id), last_seen_at TEXT NOT NULL);
