"""Granular permissions and the default role matrix. Enforced server-side by security.require_perm."""
from . import util

PERMS = {
 'users.read': 'View users and profiles', 'users.export': 'Export the filtered customer list as CSV (audited)', 'users.manage': 'Suspend/restrict/reactivate accounts',
 'kyc.review': 'Review KYC submissions and documents', 'accounts.read': 'View account balances',
 'investments.manage': 'Create/edit/pause/archive investment products', 'assets.manage': 'Manage assets and prices',
 'orders.read': 'View orders', 'orders.manage': 'Cancel orders / run matching',
 'transactions.read': 'View the ledger', 'transactions.adjust': 'Post manual adjustments (always audited)',
 'deposits.read': 'View deposits', 'deposits.manage': 'Settle/fail/reverse deposits',
 'withdrawals.read': 'View withdrawals', 'withdrawals.manage': 'Approve/reject/process withdrawals',
 'savings.read': 'View savings and goals', 'retirement.read': 'View retirement plans',
 'reports.read': 'View reports and exports', 'support.manage': 'Reply to and manage tickets',
 'notifications.send': 'Broadcast notifications', 'content.manage': 'Manage articles and announcements',
 'audit.read': 'Read the audit log', 'roles.manage': 'Assign roles', 'settings.manage': 'Change system settings',
 'jobs.run': 'Run scheduled jobs (interest, contributions, matching, snapshots)',
 'ledger.read': 'View the general ledger, trial balance and integrity checks', 'ledger.manage': 'Close accounting periods',
 'chat.moderate': 'Moderate the Trade community chat: remove messages, mute users, review reports, post announcements',
 'legal.read': 'View legal documents, versions, acceptance records and legal readiness', 'legal.manage': 'Create and edit legal drafts, supplements and legal configuration', 'legal.publish': 'Publish legal document versions (changes what users are shown and must accept)',
 'crypto.registry.read': 'View the crypto asset/network registry', 'crypto.registry.manage': 'Create assets/networks and change their status',
 'crypto.wallets.read': 'View company crypto wallets and change history', 'crypto.wallets.manage': 'Propose company wallet address changes and edit wallet settings (requires 2FA)',
 'crypto.wallets.approve': 'Approve or reject company wallet address changes (requires 2FA; never the requester)',
 'crypto.deposits.read': 'View crypto deposits, intents and source addresses', 'crypto.deposits.manage': 'Run the chain monitor, resolve manual-review deposits, verify source addresses',
 'crypto.withdrawals.read': 'View crypto withdrawals', 'crypto.withdrawals.manage': 'Approve/reject/process crypto withdrawals', 'crypto.recon.read': 'View crypto reconciliation runs and exceptions', 'crypto.recon.manage': 'Run reconciliation and resolve exceptions'}

ROLES = {
 'super_admin': ('Full system access', list(PERMS)),
 'operations_admin': ('Transactions, deposits, withdrawals', ['crypto.deposits.read', 'crypto.deposits.manage', 'crypto.withdrawals.read', 'crypto.withdrawals.manage', 'transactions.read', 'deposits.read', 'deposits.manage', 'withdrawals.read', 'withdrawals.manage', 'orders.read', 'orders.manage', 'accounts.read', 'users.read', 'jobs.run']),
 'compliance_admin': ('KYC and verification', ['legal.read', 'legal.manage', 'chat.moderate', 'users.read', 'kyc.review', 'accounts.read', 'withdrawals.read', 'audit.read', 'crypto.withdrawals.read', 'crypto.deposits.read']),
 'investment_admin': ('Investment products and portfolio operations', ['investments.manage', 'assets.manage', 'orders.read', 'orders.manage', 'accounts.read', 'jobs.run']),
 'finance_admin': ('Financial reporting and reconciliation', ['ledger.read', 'ledger.manage', 'transactions.read', 'transactions.adjust', 'deposits.read', 'withdrawals.read', 'reports.read', 'accounts.read', 'savings.read', 'retirement.read', 'audit.read', 'crypto.recon.read', 'crypto.recon.manage', 'crypto.deposits.read', 'crypto.withdrawals.read']),
 'support_admin': ('Users and support', ['chat.moderate', 'users.read', 'support.manage', 'accounts.read']),
 'content_admin': ('Resources and announcements', ['content.manage', 'notifications.send']),
 'crypto_operations': ('Crypto asset/network registry and operations', ['crypto.registry.read', 'crypto.registry.manage', 'crypto.wallets.read', 'crypto.wallets.manage', 'crypto.deposits.read', 'crypto.deposits.manage',
                                                                       'crypto.withdrawals.read', 'crypto.withdrawals.manage', 'crypto.recon.read', 'crypto.recon.manage', 'ledger.read', 'accounts.read']),
 'auditor': ('Read-only access for audit and oversight', ['legal.read', 'users.read', 'accounts.read', 'transactions.read', 'deposits.read', 'withdrawals.read', 'orders.read', 'savings.read', 'retirement.read', 'reports.read', 'audit.read', 'ledger.read', 'crypto.registry.read', 'crypto.wallets.read', 'crypto.deposits.read', 'crypto.withdrawals.read', 'crypto.recon.read'])}

SETTINGS = {
 'withdrawal_review_threshold_cents': ('500000', 'Withdrawals at or above this amount are held for compliance review'),
 'daily_withdrawal_limit_cents': ('2500000', 'Maximum withdrawal total per user per day'),
 'min_deposit_cents': ('1000', 'Minimum deposit'), 'max_deposit_cents': ('5000000', 'Maximum single deposit'),
 'twofa_required_above_cents': ('100000', 'Investments at/above this need a 2FA code when 2FA is enabled'),
 'trade_fee_bps': ('10', 'Trading fee in basis points'), 'card_fee_bps': ('150', 'Card deposit fee (bps)'), 'bank_fee_bps': ('0', 'Bank transfer deposit fee (bps)'),
 'savings_apy_bps': ('420', 'Default savings APY in basis points (variable, not guaranteed)'),
 'maintenance_mode': ('0', 'Set to 1 for maintenance: the whole API answers 503 to everyone except staff, and money movement is blocked'),
 'registration_enabled': ('1', 'Set to 0 to stop new customer registrations (enforced by the server)'),
 'api_rate_limit_per_hour': ('0', 'Maximum API requests per hour per signed-in customer (or per IP when signed out). 0 = no limit. Staff are exempt'),
 'live_prices_enabled': ('1', 'Set to 0 to stop calling the market-data provider; only cached data is served, marked stale'),
 'require_2fa_all': ('0', 'Set to 1 to require two-factor authentication for every account (accounts without it can only enrol)'),
 'crypto_screenshot_required': ('1', 'Require a transaction screenshot with every crypto deposit request'),
 'crypto_conversion_spread_bps': ('0', 'Spread (basis points) applied when crypto is converted to or from Account Balance: customers get price x (1 - spread) on deposits and pay price x (1 + spread) on withdrawals. 0 = market price'),
 'crypto_price_max_age_minutes': ('0', 'Refuse crypto/cash conversion when the asset price is older than this many minutes (0 = no age limit)'), 'crypto_max_pending_requests': ('5', 'Maximum deposit requests a customer may have awaiting review'),
 'fiat_funding_enabled': ('0', 'Funding is cryptocurrency-only. Set to 1 to re-enable legacy bank/card deposits and withdrawals'),
 'crypto_intent_ttl_hours': ('24', 'How long a crypto deposit intent stays open'), 'crypto_intent_grace_hours': ('24', 'Late-arrival window after an intent expires (still needs manual review)'),
 'risk_ack_required': ('1', 'Require users to acknowledge the Risk Disclosure before investing or trading'),
 'crypto_requires_kyc': ('1', 'Require verified KYC before crypto deposits are credited and for crypto withdrawals')}

# ---- Email Center (server/mailer). Sending, template changes, approval of large sends and provider settings are separate permissions. ----
PERMS.update({
 'email.send': 'Send an email to one or several selected customers', 'email.send_bulk': 'Create bulk email campaigns/broadcasts (large ones need a second person with email.bulk_approve)',
 'email.bulk_approve': 'Approve large email campaigns created by someone else', 'email.templates.read': 'View email templates, versions and previews',
 'email.templates.edit': 'Edit email template drafts and duplicate templates', 'email.templates.publish': 'Publish email template versions and enable/disable templates (changes what customers receive)',
 'email.automations.manage': 'Enable/disable and configure email automations and reminders', 'email.logs.read': 'View the email outbox, delivery attempts and provider events',
 'email.settings.manage': 'Change email sending limits and view provider status; retry/cancel messages', 'email.suppression.manage': 'Add or release suppressed email addresses',
 'email.smtp.manage': 'View and change the SMTP host configuration, test the SMTP connection and send test emails (handles a stored credential)'})
ROLES['super_admin'] = (ROLES['super_admin'][0], list(PERMS))
for _r, _p in {'support_admin': ['email.send', 'email.templates.read', 'email.logs.read'], 'compliance_admin': ['email.send', 'email.templates.read', 'email.logs.read'],
               'content_admin': ['email.templates.read', 'email.templates.edit', 'email.send_bulk', 'email.logs.read'], 'operations_admin': ['email.logs.read'],
               'auditor': ['email.logs.read', 'email.templates.read']}.items():
    ROLES[_r] = (ROLES[_r][0], ROLES[_r][1] + _p)
ROLES['communications_admin'] = ('Email templates, automations, campaigns and delivery logs (cannot approve its own large sends or change provider settings)',
    ['users.read', 'email.send', 'email.send_bulk', 'email.templates.read', 'email.templates.edit', 'email.templates.publish', 'email.automations.manage', 'email.logs.read', 'email.suppression.manage'])
SETTINGS.update({
 'email_bulk_approval_threshold': ('50', 'Email campaigns to more than this many recipients need approval by a second administrator'),
 'email_campaign_max_recipients': ('5000', 'Hard cap on recipients in one email campaign'),
 'email_quiet_start_hour': ('21', 'Reminders are not sent from this local hour (customer time zone)'), 'email_quiet_end_hour': ('8', 'Reminders resume at this local hour'),
 'email_tracking_enabled': ('0', 'Record open/click events from the provider (privacy-sensitive; off by default)'), 'email_max_attempts': ('6', 'Delivery attempts before a message is marked failed')})

def ensure_rbac(db):
    for code, d in PERMS.items(): db.execute('INSERT OR IGNORE INTO permissions(code,description) VALUES(?,?)', (code, d))
    for name, (d, perms) in ROLES.items():
        db.execute('INSERT OR IGNORE INTO roles(name,description) VALUES(?,?)', (name, d))
        rid = db.execute('SELECT id FROM roles WHERE name=?', (name,)).fetchone()[0]
        for p in perms:
            db.execute('INSERT OR IGNORE INTO role_permissions(role_id,permission_id) SELECT ?,id FROM permissions WHERE code=?', (rid, p))
    for k, (v, d) in SETTINGS.items(): db.execute('INSERT OR IGNORE INTO system_settings(key,value,description,updated_at) VALUES(?,?,?,?)', (k, v, d, util.now()))

def setting(db, key):
    r = db.execute('SELECT value FROM system_settings WHERE key=?', (key,)).fetchone()
    return int(r[0]) if r else int(SETTINGS[key][0])
