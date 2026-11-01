# Customer support chat: operations guide

Support runs on the existing `support_tickets` / `support_messages` tables (no parallel system). The public **Trade community chat is separate** and unrelated.

**Customer**: Dashboard > Support. Start a conversation (category, subject, message), then chat in the conversation window: messages arrive by authenticated polling (4 s while the tab is visible, 20 s when hidden, exponential back-off with a "Reconnecting" notice). A failed send shows *Not sent / Retry*; retries reuse a `client_msg_id`, so the server never stores a duplicate. Attachments: JPG/PNG/PDF up to 5 MB (content is verified, stored outside the web root). "Read by support" appears only once staff opened the message. "Support is online" appears only if a staff member has the inbox open (heartbeat in the last 90 s), otherwise the window says support is offline. Customers can close a conversation and reopen it within 14 days.

**Staff** (`support.manage`): Admin > Customer Support. Filters: status, priority, category, assigned (`none` = unassigned, `me`), unread. Open a row to chat live; tick *Internal note* for notes customers never see or receive by email; set status, priority and assignee in the same window. Staff are alerted (in-app) to new conversations; assignees to new customer messages; customers get an in-app notification and an email for each visible reply and for resolved/closed.

**Security**: sender identity comes from the session; customers can only reach their own `ref` (others get 404); internal notes and their attachments are never returned by customer endpoints; content is escaped on render; mutations need the CSRF token; limits: 30 messages/10 min, 10 uploads/h, 10 new tickets/h. Every reply, note, assignment, reopen and attachment is audited and status changes are recorded in `support_status_history`.

**Limits**: this is polling, not WebSockets; there is no typing indicator and no multi-agent presence list. Not tested in a real browser in this environment (see the test report).
