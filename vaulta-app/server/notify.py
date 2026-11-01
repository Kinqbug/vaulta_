from . import util

TYPES = ['transaction', 'deposit', 'withdrawal', 'investment', 'trade', 'security', 'kyc', 'savings', 'retirement', 'system', 'promotional']

def notify(db, user_id, type_, title, body='', link=None):
    pref = db.execute('SELECT in_app,email FROM notification_preferences WHERE user_id=? AND type=?', (user_id, type_)).fetchone()
    in_app = True if (type_ == 'security' or not pref) else bool(pref['in_app'])
    if in_app:
        db.execute('INSERT INTO notifications(user_id,type,title,body,link,created_at) VALUES(?,?,?,?,?,?)', (user_id, type_, title, body, link, util.now()))
    # Email is no longer written here. Emails are queued by server/mailer/events.emit() at the real state transitions (they used to be unsent rows).
