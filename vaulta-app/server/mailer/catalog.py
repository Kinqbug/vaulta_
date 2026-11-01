"""Email template catalog. Code is the source of the *default* content; it is seeded into email_templates / email_template_versions
(see templates_db.ensure_seed). Administrators then edit through versioned drafts, so what is sent is always a stored, immutable version.

Rules every template follows:
  * wording describes the ACTUAL state of the operation (pending is never "completed", approval is never "paid out", a submitted order is never "executed")
  * no promise of returns; nothing invented: every {{variable}} must be supplied by real backend data or the email is not sent
  * content is plain text with {{variables}} only; HTML is produced by the shared layout and every value is escaped
"""
import re, json, hashlib

PLACEHOLDER = re.compile(r'\{\{\s*([a-z][a-z0-9_]*)\s*\}\}')

CATEGORIES = {
    'registration': 'Registration & verification', 'deposits': 'Deposits', 'withdrawals': 'Withdrawals', 'investments': 'Investments', 'trading': 'Trading',
    'savings': 'Savings & retirement', 'security': 'Security', 'account': 'Account administration', 'support': 'Support',
    'announcements': 'Announcements & marketing', 'admin': 'Administrative communication'}

# name -> (description, kind). kind: text | url | multiline. Only variables listed here can appear in a template.
VARS = {
    'first_name': ('Customer first name', 'text'), 'email': ('Customer email address', 'text'), 'customer_id': ('Customer ID', 'text'),
    'transaction_reference': ('Reference of the transaction', 'text'), 'transaction_status': ('Current recorded status of the transaction', 'text'),
    'amount': ('Transaction amount', 'text'), 'asset_symbol': ('Asset or currency symbol', 'text'), 'network': ('Blockchain network', 'text'),
    'transaction_date': ('Date the transaction was recorded', 'text'), 'destination': ('Destination, partly masked', 'text'),
    'investment_name': ('Investment product name', 'text'), 'investment_amount': ('Amount invested', 'text'), 'investment_reference': ('Investment reference', 'text'),
    'order_type': ('Order type (market, limit, ...)', 'text'), 'trade_side': ('Buy or sell', 'text'), 'order_quantity': ('Quantity ordered', 'text'),
    'executed_quantity': ('Quantity executed', 'text'), 'execution_price': ('Execution price', 'text'), 'limit_price': ('Limit price', 'text'),
    'trading_fee': ('Trading fee', 'text'), 'order_reference': ('Order reference', 'text'),
    'goal_name': ('Savings goal name', 'text'), 'target_amount': ('Goal or plan target', 'text'), 'current_amount': ('Amount saved so far', 'text'),
    'progress_percentage': ('Progress towards the goal', 'text'), 'contribution_amount': ('Contribution amount', 'text'), 'retirement_age': ('Planned retirement age', 'text'),
    'event_timestamp': ('When the event was recorded (UTC)', 'text'), 'ip_address': ('IP address recorded for the event', 'text'), 'device': ('Browser / device recorded for the event', 'text'),
    'reason': ('Reason recorded by VAULTA staff', 'text'), 'account_status': ('Account status', 'text'), 'changed_fields': ('Profile fields that changed', 'text'),
    'wallet_action': ('What happened to the wallet address', 'text'), 'wallet_address': ('Wallet address, partly masked', 'text'),
    'ticket_reference': ('Support ticket reference', 'text'), 'ticket_subject': ('Support ticket subject', 'text'), 'expires_in': ('How long a link stays valid', 'text'),
    'document_name': ('Name of a legal document', 'text'), 'effective_date': ('Date a change takes effect', 'text'), 'maintenance_window': ('Maintenance start and end', 'text'),
    'message_body': ('Message written by the sender (plain text)', 'multiline'),
    'dashboard_url': ('Link to the dashboard', 'url'), 'transaction_url': ('Link to transactions', 'url'), 'portfolio_url': ('Link to the portfolio', 'url'),
    'trade_url': ('Link to trading', 'url'), 'goal_url': ('Link to savings goals', 'url'), 'support_url': ('Link to support', 'url'),
    'security_url': ('Link to security settings', 'url'), 'kyc_url': ('Link to identity verification', 'url'), 'document_url': ('Link to a legal document', 'url'),
    'action_url': ('One-time secure link (never stored after sending)', 'url'),
}
SECRET_VARS = {'action_url'}                 # kept only in secret_json until the message is sent
LINK_VARS = {k for k, (_, kind) in VARS.items() if kind == 'url'}

# automation event -> label. The backend calls events.emit(db, <event>, ...) at the real state transition.
EVENTS = {
    'user.registered': 'Customer registers', 'auth.verification_requested': 'Verification link issued (sign-up or resend)', 'auth.email_verified': 'Verification token validated',
    'reminder.verify_email': 'Reminder: email still unverified', 'reminder.verify_email_final': 'Final reminder: email still unverified', 'reminder.kyc_incomplete': 'Reminder: identity verification not started/completed',
    'reminder.kyc_info_outstanding': 'Reminder: requested verification information still outstanding', 'reminder.reengagement': 'Re-engagement (marketing consent required)', 'reminder.retirement_review': 'Retirement plan not reviewed for a year',
    'kyc.submitted': 'KYC submitted', 'kyc.approved': 'KYC approved', 'kyc.rejected': 'KYC rejected', 'kyc.info_required': 'KYC: more information requested',
    'deposit.requested': 'Deposit created (fiat)', 'deposit.pending': 'Crypto deposit request submitted for review', 'deposit.detected': 'Incoming crypto transaction detected and attributed',
    'deposit.confirmed': 'Deposit credited', 'deposit.failed': 'Deposit failed / request not approved', 'deposit.review': 'Crypto deposit held for review', 'deposit.reversed': 'Deposit reversed',
    'withdrawal.requested': 'Withdrawal requested', 'withdrawal.review': 'Withdrawal held for review', 'withdrawal.approved': 'Withdrawal approved', 'withdrawal.processing': 'Withdrawal processing / broadcast',
    'withdrawal.completed': 'Withdrawal completed', 'withdrawal.failed': 'Withdrawal failed', 'withdrawal.rejected': 'Withdrawal rejected', 'withdrawal.cancelled': 'Withdrawal cancelled by customer',
    'investment.received': 'Investment request recorded, not yet confirmed', 'investment.confirmed': 'Investment confirmed (record completed)',
    'trade.order_placed': 'Order accepted and resting on the book', 'trade.order_filled': 'Order executed', 'trade.order_cancelled': 'Order cancelled', 'trade.order_rejected': 'Order could not be executed',
    'savings.contribution_recorded': 'Savings contribution recorded', 'savings.goal_created': 'Savings goal created', 'savings.milestone': 'Savings goal milestone crossed', 'savings.contribution_failed': 'Scheduled contribution skipped',
    'savings.goal_achieved': 'Savings goal reached', 'retirement.updated': 'Retirement plan saved',
    'security.new_login': 'Sign-in from a new IP address', 'security.new_device': 'Sign-in from a new browser/device', 'security.password_changed': 'Password changed or reset', 'security.password_reset_requested': 'Password reset link issued',
    'security.2fa_enabled': 'Two-factor authentication enabled', 'security.2fa_disabled': 'Two-factor authentication disabled', 'security.wallet_changed': 'Customer source wallet address registered/verified/removed',
    'security.withdrawal_alert': 'Withdrawal to a new destination or held for review', 'security.account_restricted': 'Account restricted or suspended by staff', 'security.suspicious_activity': 'Account locked after repeated failed sign-ins',
    'account.profile_updated': 'Profile updated', 'account.phone_changed': 'Phone number changed', 'account.access_restored': 'Account reactivated by staff',
    'support.ticket_created': 'Support ticket created', 'support.reply': 'Staff reply recorded', 'support.info_required': 'Ticket moved to waiting for customer', 'support.resolved': 'Ticket resolved', 'support.closed': 'Ticket closed',
}

HI = 'Hi {{first_name}},'
RISK = 'Investing involves risk, including possible loss of principal. Past performance does not guarantee future results.'
NOT_YOU = 'If this was not you, secure your account now: reset your password and contact support.'


def T(id, cat, name, cls, event, subject, headline, intro, *, details=(), summary=(), summary_title='Details', cta=None, badge=None, after=(), notice=None,
      optional=(), manual=False, gated=False, admin_vars=(), desc='', preheader=None, hi=True):
    spec = {'preheader': preheader or '', 'headline': headline, 'intro': ([HI] if hi else []) + list(intro), 'badge': badge or '', 'details': [list(d) for d in details],
            'summary_title': summary_title, 'summary': [list(r) for r in summary], 'cta': {'label': cta[0], 'url_var': cta[1]} if cta else None, 'after': list(after), 'notice': notice or ''}
    return {'id': id, 'category': cat, 'name': name, 'description': desc or name, 'mail_class': cls, 'trigger_event': event, 'manual_send': manual, 'pref_gated': gated,
            'admin_vars': list(admin_vars), 'optional': list(optional), 'subject': subject, 'spec': spec}

R, D, W, I, TR, S, SEC, A, SUP, ANN, ADM = ('registration', 'deposits', 'withdrawals', 'investments', 'trading', 'savings', 'security', 'account', 'support', 'announcements', 'admin')
TX = [('Reference', '{{transaction_reference}}'), ('Amount', '{{amount}} {{asset_symbol}}'), ('Recorded', '{{transaction_date}}')]
WD = [('Reference', '{{transaction_reference}}'), ('Amount', '{{amount}} {{asset_symbol}}'), ('Destination', '{{destination}}'), ('Recorded', '{{transaction_date}}')]

CATALOG = [
 # ---------------------------------------------------------------- A. registration & verification
 T('WELCOME_001', R, 'Welcome to VAULTA', 'transactional', 'user.registered', 'Welcome to VAULTA', 'Welcome to VAULTA, {{first_name}}.',
   ['Your VAULTA account has been created. To protect it, please confirm your email address using the separate verification email we have sent you.',
    'Once your email is confirmed, complete identity verification to unlock deposits, withdrawals and trading.'], cta=('View Dashboard', 'dashboard_url'), preheader='Your account has been created.'),
 T('EMAIL_VERIFY_001', R, 'Verify email address', 'transactional', 'auth.verification_requested', 'Verify your VAULTA email address', 'Verify your email address.',
   ['Please confirm that {{email}} is the address you want to use for your VAULTA account.', 'This link can be used once and expires in {{expires_in}}.'],
   cta=('Verify Email Address', 'action_url'), notice='If you did not create a VAULTA account, you can ignore this email.', preheader='Confirm your email address to continue.'),
 T('EMAIL_VERIFY_REMINDER_001', R, 'Reminder: verify email address', 'transactional', 'reminder.verify_email', 'Reminder: verify your VAULTA email address', 'Your email address is still unverified.',
   ['You created a VAULTA account but have not yet confirmed {{email}}. Verification protects your account and is required before you can move money.', 'This new link can be used once and expires in {{expires_in}}.'],
   cta=('Verify Email Address', 'action_url'), notice='If you no longer want a VAULTA account, you can ignore this email.'),
 T('EMAIL_VERIFY_FINAL_001', R, 'Final reminder: verify email address', 'transactional', 'reminder.verify_email_final', 'Final reminder: verify your VAULTA email address', 'A final reminder to verify your email.',
   ['This is the last reminder we will send. Your VAULTA email address {{email}} is still unverified.', 'This new link can be used once and expires in {{expires_in}}.'],
   cta=('Verify Email Address', 'action_url'), notice='We will not send further verification reminders. You can request a new link from your dashboard at any time.'),
 T('EMAIL_VERIFIED_001', R, 'Email successfully verified', 'transactional', 'auth.email_verified', 'Your email address is verified', 'Your email address is verified.',
   ['Thank you, {{first_name}}. {{email}} is now confirmed for your VAULTA account.', 'The next step is identity verification, which is required before deposits, withdrawals and trading.'],
   badge='Verified', cta=('Complete Identity Verification', 'kyc_url')),
 T('KYC_COMPLETE_001', R, 'Complete identity verification', 'service', 'reminder.kyc_incomplete', 'Complete your identity verification', 'Complete your identity verification.',
   ['Identity verification is required before you can deposit, withdraw or trade on VAULTA.', 'It takes a few minutes: confirm your details and upload a government-issued ID.'],
   cta=('Complete Identity Verification', 'kyc_url'), desc='Sent when identity verification has not been started or completed'),
 T('KYC_SUBMITTED_001', R, 'Identity verification submitted', 'transactional', 'kyc.submitted', 'We have received your identity verification', 'We have received your verification.',
   ['Thank you for submitting your identity verification. Our compliance team is reviewing it and we will email you when there is an update.'],
   badge='Under review', details=[('Submitted', '{{event_timestamp}}')], notice='A submission is not an approval. Some features stay unavailable until verification is approved.', cta=('View Dashboard', 'dashboard_url')),
 T('KYC_APPROVED_001', R, 'Identity verification approved', 'transactional', 'kyc.approved', 'Your identity is verified', 'Your identity is verified.',
   ['Your identity verification has been approved. You can now use the features that require a verified account.'], badge='Verified', details=[('Approved', '{{event_timestamp}}')], cta=('View Dashboard', 'dashboard_url')),
 T('KYC_REJECTED_001', R, 'Identity verification unsuccessful', 'transactional', 'kyc.rejected', 'Your identity verification was unsuccessful', 'We could not verify your identity.',
   ['We were unable to approve your identity verification.'], badge='Unsuccessful', details=[('Reason', '{{reason}}')], after=['You can review your details and submit again.'], cta=('Review Verification', 'kyc_url')),
 T('KYC_INFO_REQUIRED_001', R, 'Additional verification information required', 'transactional', 'kyc.info_required', 'We need more information to verify your identity', 'We need a little more information.',
   ['To continue reviewing your identity verification, our compliance team needs the following.'], badge='Action required', details=[('What we need', '{{reason}}')], cta=('Provide Information', 'kyc_url')),
 T('KYC_INFO_REMINDER_001', R, 'Reminder: verification information outstanding', 'transactional', 'reminder.kyc_info_outstanding', 'Reminder: we still need information to verify your identity', 'We are still waiting for your information.',
   ['Your identity verification cannot be completed until we receive the information we asked for.'], badge='Action required', details=[('What we need', '{{reason}}')], cta=('Provide Information', 'kyc_url')),

 # ---------------------------------------------------------------- B. deposits
 T('DEPOSIT_REQUESTED_001', D, 'Deposit request created', 'transactional', 'deposit.requested', 'Deposit request {{transaction_reference}} received', 'We have received your deposit request.',
   ['Your deposit request has been created and is not yet complete. Funds are not available until the deposit is confirmed, and we will email you when that happens.'],
   badge='{{transaction_status}}', summary=TX, summary_title='Deposit details', cta=('View Transaction', 'transaction_url'), gated=False),
 T('DEPOSIT_PENDING_001', D, 'Deposit pending', 'transactional', 'deposit.pending', 'Your deposit {{transaction_reference}} is being verified', 'Your deposit is being verified.',
   ['We have received your deposit details and are verifying the transfer. Your balance will not change until the deposit is confirmed.'],
   badge='Pending', summary=[('Reference', '{{transaction_reference}}'), ('Amount submitted', '{{amount}} {{asset_symbol}}'), ('Network', '{{network}}'), ('Submitted', '{{transaction_date}}')], summary_title='Deposit details', cta=('View Transaction', 'transaction_url')),
 T('DEPOSIT_DETECTED_001', D, 'Cryptocurrency deposit detected', 'transactional', 'deposit.detected', 'We detected your {{asset_symbol}} deposit', 'We have detected your deposit.',
   ['An incoming transaction to your VAULTA deposit address has been detected. It has not been credited yet: crediting happens after the required network confirmations and our checks.'],
   badge='Awaiting confirmations', summary=[('Reference', '{{transaction_reference}}'), ('Amount', '{{amount}} {{asset_symbol}}'), ('Network', '{{network}}'), ('Detected', '{{transaction_date}}')], summary_title='Deposit details', cta=('View Transaction', 'transaction_url')),
 T('DEPOSIT_CONFIRMED_001', D, 'Deposit confirmed', 'transactional', 'deposit.confirmed', 'Your deposit {{transaction_reference}} is confirmed', 'Your deposit is confirmed.',
   ['Your deposit has been confirmed and credited to your VAULTA account.'], badge='Credited', summary=TX, summary_title='Deposit details', optional=(), cta=('View Transaction', 'transaction_url')),
 T('DEPOSIT_FAILED_001', D, 'Deposit unsuccessful', 'transactional', 'deposit.failed', 'Your deposit {{transaction_reference}} was unsuccessful', 'Your deposit was unsuccessful.',
   ['We could not complete this deposit. No funds were credited to your account.'], badge='Unsuccessful', summary=[('Reference', '{{transaction_reference}}'), ('Amount', '{{amount}} {{asset_symbol}}')], summary_title='Deposit details',
   details=[('Reason', '{{reason}}')], optional=('reason',), after=['If the details were wrong you can submit a new request. Contact support if you need help.'], cta=('Contact Support', 'support_url')),
 T('DEPOSIT_REVIEW_001', D, 'Deposit under review', 'transactional', 'deposit.review', 'Your deposit {{transaction_reference}} is under review', 'Your deposit is under review.',
   ['Some deposits need an additional check before they are credited. Your deposit has not been credited yet. You do not need to do anything unless we contact you.'],
   badge='Under review', summary=[('Reference', '{{transaction_reference}}'), ('Amount', '{{amount}} {{asset_symbol}}'), ('Network', '{{network}}')], summary_title='Deposit details', cta=('View Transaction', 'transaction_url')),
 T('DEPOSIT_REVERSED_001', D, 'Deposit reversed', 'transactional', 'deposit.reversed', 'Your deposit {{transaction_reference}} was reversed', 'A deposit was reversed.',
   ['A previously credited deposit has been reversed and the amount was removed from your account.'], badge='Reversed', summary=TX, summary_title='Deposit details', details=[('Reason', '{{reason}}')], optional=('reason',), cta=('Contact Support', 'support_url')),

 # ---------------------------------------------------------------- C. withdrawals
 T('WITHDRAWAL_REQUESTED_001', W, 'Withdrawal requested', 'transactional', 'withdrawal.requested', 'Withdrawal {{transaction_reference}} requested', 'We have received your withdrawal request.',
   ['Your withdrawal request has been recorded and the amount is on hold while it is processed. It has not been paid out.'], badge='{{transaction_status}}', summary=WD, summary_title='Withdrawal details',
   after=['If you did not request this withdrawal, contact support immediately.'], cta=('View Transaction', 'transaction_url')),
 T('WITHDRAWAL_REVIEW_001', W, 'Withdrawal under review', 'transactional', 'withdrawal.review', 'Your withdrawal {{transaction_reference}} is under review', 'Your withdrawal is under review.',
   ['Some withdrawals need an additional review before they are approved. Your withdrawal has not been paid out and the amount remains on hold.'], badge='Under review', summary=WD, summary_title='Withdrawal details', cta=('View Transaction', 'transaction_url')),
 T('WITHDRAWAL_APPROVED_001', W, 'Withdrawal approved', 'transactional', 'withdrawal.approved', 'Your withdrawal {{transaction_reference}} was approved', 'Your withdrawal has been approved.',
   ['Your withdrawal has been approved and will move to processing. Approval is not the payout: we will email you again when the withdrawal is completed.'], badge='Approved', summary=WD, summary_title='Withdrawal details', cta=('View Transaction', 'transaction_url')),
 T('WITHDRAWAL_PROCESSING_001', W, 'Withdrawal processing', 'transactional', 'withdrawal.processing', 'Your withdrawal {{transaction_reference}} is being processed', 'Your withdrawal is being processed.',
   ['Your withdrawal is now being processed. It is not complete yet; we will email you when it is.'], badge='Processing', summary=WD, summary_title='Withdrawal details', cta=('View Transaction', 'transaction_url')),
 T('WITHDRAWAL_COMPLETED_001', W, 'Withdrawal completed', 'transactional', 'withdrawal.completed', 'Your withdrawal {{transaction_reference}} is complete', 'Your withdrawal is complete.',
   ['Your withdrawal has been completed.'], badge='Completed', summary=WD, summary_title='Withdrawal details', cta=('View Transaction', 'transaction_url')),
 T('WITHDRAWAL_FAILED_001', W, 'Withdrawal unsuccessful', 'transactional', 'withdrawal.failed', 'Your withdrawal {{transaction_reference}} was unsuccessful', 'Your withdrawal was unsuccessful.',
   ['We were unable to complete this withdrawal.'], badge='Unsuccessful', summary=WD, summary_title='Withdrawal details', details=[('Reason', '{{reason}}')], optional=('reason',),
   after=['Contact support if you need help.'], cta=('Contact Support', 'support_url')),
 T('WITHDRAWAL_REJECTED_001', W, 'Withdrawal rejected', 'transactional', 'withdrawal.rejected', 'Your withdrawal {{transaction_reference}} was rejected', 'Your withdrawal was rejected.',
   ['We could not approve this withdrawal. It was not paid out and the held amount has been released back to your account.'], badge='Rejected', summary=WD, summary_title='Withdrawal details',
   details=[('Reason', '{{reason}}')], optional=('reason',), cta=('Contact Support', 'support_url')),
 T('WITHDRAWAL_CANCELLED_001', W, 'Withdrawal cancelled', 'transactional', 'withdrawal.cancelled', 'Your withdrawal {{transaction_reference}} was cancelled', 'Your withdrawal was cancelled.',
   ['This withdrawal was cancelled and the held amount has been released back to your account.'], badge='Cancelled', summary=WD, summary_title='Withdrawal details', after=['If you did not cancel this withdrawal, contact support immediately.'], cta=('View Transaction', 'transaction_url')),

 # ---------------------------------------------------------------- D. investments
 T('INVESTMENT_RECEIVED_001', I, 'Investment request received', 'transactional', 'investment.received', 'We received your investment request {{investment_reference}}', 'We have received your investment request.',
   ['Your investment request is recorded but not yet confirmed. We will email you when it is confirmed.'], badge='Processing', summary=[('Reference', '{{investment_reference}}'), ('Product', '{{investment_name}}'), ('Amount', '{{investment_amount}}'), ('Recorded', '{{transaction_date}}')],
   summary_title='Investment details', notice=RISK, cta=('View Investment', 'portfolio_url')),
 T('INVESTMENT_CONFIRMED_001', I, 'Investment confirmed', 'transactional', 'investment.confirmed', 'Your investment {{investment_reference}} is confirmed', 'Your investment is confirmed.',
   ['Your investment has been completed and recorded in your portfolio.'], badge='Confirmed', summary=[('Reference', '{{investment_reference}}'), ('Product', '{{investment_name}}'), ('Amount', '{{investment_amount}}'), ('Recorded', '{{transaction_date}}')],
   summary_title='Investment details', notice=RISK, cta=('View Investment', 'portfolio_url')),
 T('INVESTMENT_CANCELLED_001', I, 'Investment cancelled', 'transactional', None, 'Your investment {{investment_reference}} was cancelled', 'Your investment was cancelled.',
   ['This investment request was cancelled and was not completed.'], badge='Cancelled', summary=[('Reference', '{{investment_reference}}'), ('Product', '{{investment_name}}'), ('Amount', '{{investment_amount}}')], summary_title='Investment details', cta=('View Portfolio', 'portfolio_url'),
   desc='No automated trigger: the backend has no investment-cancellation workflow'),
 T('INVESTMENT_MATURED_001', I, 'Investment matured', 'transactional', None, 'Your investment {{investment_reference}} has matured', 'Your investment has matured.',
   ['This investment has reached its maturity date.'], badge='Matured', summary=[('Reference', '{{investment_reference}}'), ('Product', '{{investment_name}}')], summary_title='Investment details', notice=RISK, cta=('View Investment', 'portfolio_url'),
   desc='No automated trigger: the backend has no maturity workflow'),
 T('INVESTMENT_STATEMENT_001', I, 'Investment statement available', 'service', None, 'Your investment statement is available', 'Your investment statement is ready.',
   ['A new statement for your investments is available in your VAULTA documents.'], cta=('View Statements', 'portfolio_url'), desc='No automated trigger: statements are generated on request, not on a schedule'),
 T('INVESTMENT_UPDATE_001', I, 'Investment product update', 'service', None, 'An update about your investment', 'An update about your investment.',
   ['{{message_body}}'], notice=RISK, cta=('View Portfolio', 'portfolio_url'), manual=True, admin_vars=('message_body',), desc='Sent by an administrator to customers who hold the product'),

 # ---------------------------------------------------------------- E. trading
 T('TRADE_ORDER_PLACED_001', TR, 'Trade order placed', 'transactional', 'trade.order_placed', 'Your {{asset_symbol}} order {{order_reference}} was placed', 'Your order has been placed.',
   ['Your order was accepted and is waiting to be executed. It has not been executed yet; we will email you if it executes.'], badge='Open',
   summary=[('Reference', '{{order_reference}}'), ('Order', '{{trade_side}} · {{order_type}}'), ('Asset', '{{asset_symbol}}'), ('Quantity', '{{order_quantity}}'), ('Limit price', '{{limit_price}}')], optional=('limit_price',),
   summary_title='Order details', cta=('View Trade', 'trade_url'), gated=True),
 T('TRADE_ORDER_FILLED_001', TR, 'Trade order executed', 'transactional', 'trade.order_filled', 'Your {{asset_symbol}} order {{order_reference}} was executed', 'Your order was executed.',
   ['Your order has been executed in full.'], badge='Executed',
   summary=[('Reference', '{{order_reference}}'), ('Order', '{{trade_side}} · {{order_type}}'), ('Asset', '{{asset_symbol}}'), ('Quantity', '{{executed_quantity}}'), ('Price', '{{execution_price}}'), ('Fee', '{{trading_fee}}'), ('Executed', '{{transaction_date}}')],
   summary_title='Execution details', notice=RISK, cta=('View Trade', 'trade_url')),
 T('TRADE_ORDER_PARTIAL_001', TR, 'Trade partially filled', 'transactional', None, 'Your {{asset_symbol}} order {{order_reference}} was partially filled', 'Your order was partially filled.',
   ['Part of your order has been executed. The remainder is still open.'], badge='Partially filled', summary=[('Reference', '{{order_reference}}'), ('Asset', '{{asset_symbol}}'), ('Executed', '{{executed_quantity}}'), ('Ordered', '{{order_quantity}}')], summary_title='Order details', cta=('View Trade', 'trade_url'),
   desc='No automated trigger: the matching engine fills orders in full, so partial fills do not exist yet'),
 T('TRADE_ORDER_CANCELLED_001', TR, 'Trade order cancelled', 'transactional', 'trade.order_cancelled', 'Your {{asset_symbol}} order {{order_reference}} was cancelled', 'Your order was cancelled.',
   ['This order was cancelled and was not executed. Any cash held for it has been released.'], badge='Cancelled', summary=[('Reference', '{{order_reference}}'), ('Order', '{{trade_side}} · {{order_type}}'), ('Asset', '{{asset_symbol}}'), ('Quantity', '{{order_quantity}}')],
   summary_title='Order details', details=[('Reason', '{{reason}}')], optional=('reason',), cta=('View Trade', 'trade_url')),
 T('TRADE_ORDER_REJECTED_001', TR, 'Trade order rejected', 'transactional', 'trade.order_rejected', 'Your {{asset_symbol}} order {{order_reference}} could not be executed', 'Your order could not be executed.',
   ['This order was not executed. No trade took place.'], badge='Not executed', summary=[('Reference', '{{order_reference}}'), ('Order', '{{trade_side}} · {{order_type}}'), ('Asset', '{{asset_symbol}}'), ('Quantity', '{{order_quantity}}')],
   summary_title='Order details', details=[('Reason', '{{reason}}')], optional=('reason',), cta=('View Trade', 'trade_url')),
 T('TRADE_ORDER_EXPIRED_001', TR, 'Trade order expired', 'transactional', None, 'Your {{asset_symbol}} order {{order_reference}} expired', 'Your order expired.',
   ['This order reached its expiry and was not executed.'], badge='Expired', summary=[('Reference', '{{order_reference}}'), ('Asset', '{{asset_symbol}}'), ('Quantity', '{{order_quantity}}')], summary_title='Order details', cta=('View Trade', 'trade_url'),
   desc='No automated trigger: orders have no expiry in the backend'),
 T('TRADE_PRICE_ALERT_001', TR, 'Price alert', 'service', None, 'Price alert: {{asset_symbol}}', 'Your price alert was reached.',
   ['{{asset_symbol}} reached the price you asked to be alerted about.'], summary=[('Asset', '{{asset_symbol}}'), ('Price', '{{execution_price}}')], summary_title='Alert details', notice='Prices move quickly and this email may arrive after the price has changed.', cta=('View Trade', 'trade_url'), gated=True,
   desc='No automated trigger: the backend has no price-alert feature'),

 # ---------------------------------------------------------------- F. savings & retirement
 T('SAVINGS_ACCOUNT_001', S, 'Savings account created', 'service', None, 'Your savings account is ready', 'Your savings account is ready.',
   ['Your VAULTA savings account has been created.'], cta=('View Savings', 'goal_url'), desc='No automated trigger: savings accounts are created automatically at sign-up (covered by the welcome email)'),
 T('SAVINGS_CONTRIBUTION_001', S, 'Savings contribution recorded', 'service', 'savings.contribution_recorded', 'Savings contribution recorded', 'Your savings contribution was recorded.',
   ['A contribution to your savings was recorded.'], summary=[('Amount', '{{contribution_amount}}'), ('Goal', '{{goal_name}}'), ('Recorded', '{{transaction_date}}')], optional=('goal_name',), summary_title='Contribution', cta=('View Savings', 'goal_url'), gated=True),
 T('SAVINGS_GOAL_001', S, 'Savings goal created', 'service', 'savings.goal_created', 'Your savings goal “{{goal_name}}” was created', 'Your savings goal is set up.',
   ['Your savings goal has been created.'], summary=[('Goal', '{{goal_name}}'), ('Target', '{{target_amount}}')], summary_title='Goal details', cta=('View Goal', 'goal_url'), gated=True),
 T('SAVINGS_MILESTONE_001', S, 'Savings milestone reached', 'service', 'savings.milestone', 'You are {{progress_percentage}} of the way to “{{goal_name}}”', 'A savings milestone reached.',
   ['You have reached a milestone on your savings goal.'], summary=[('Goal', '{{goal_name}}'), ('Saved so far', '{{current_amount}}'), ('Target', '{{target_amount}}'), ('Progress', '{{progress_percentage}}')], summary_title='Progress', cta=('View Goal', 'goal_url'), gated=True),
 T('SAVINGS_CONTRIBUTION_FAILED_001', S, 'Savings contribution unsuccessful', 'service', 'savings.contribution_failed', 'Your scheduled savings contribution did not run', 'A scheduled contribution did not run.',
   ['Your scheduled contribution could not be made, so nothing was moved.'], badge='Not completed', details=[('Reason', '{{reason}}')], after=['Your next contribution will be attempted on its normal schedule.'], cta=('View Goal', 'goal_url')),
 T('SAVINGS_GOAL_ACHIEVED_001', S, 'Savings goal achieved', 'service', 'savings.goal_achieved', 'You reached your savings goal “{{goal_name}}”', 'You reached your savings goal.',
   ['Congratulations, your savings have reached the target for this goal.'], badge='Goal reached', summary=[('Goal', '{{goal_name}}'), ('Saved', '{{current_amount}}'), ('Target', '{{target_amount}}')], summary_title='Goal details', cta=('View Goal', 'goal_url')),
 T('RETIREMENT_UPDATED_001', S, 'Retirement plan updated', 'service', 'retirement.updated', 'Your retirement plan was updated', 'Your retirement plan was updated.',
   ['Your retirement plan settings were saved.'], summary=[('Retirement age', '{{retirement_age}}'), ('Monthly contribution', '{{contribution_amount}}'), ('Target', '{{target_amount}}')], optional=('target_amount',), summary_title='Plan',
   notice='Projections are illustrations based on your inputs, not forecasts or guarantees.', cta=('View Plan', 'dashboard_url'), gated=True),
 T('RETIREMENT_REVIEW_001', S, 'Retirement plan review reminder', 'service', 'reminder.retirement_review', 'Time to review your retirement plan', 'Time to review your retirement plan.',
   ['It has been a year since your retirement plan was last updated. A quick review keeps it in line with your goals.'], notice='Projections are illustrations, not forecasts or guarantees.', cta=('Review Plan', 'dashboard_url'), gated=True),

 # ---------------------------------------------------------------- G. security (always sent; cannot be switched off)
 T('SECURITY_NEW_LOGIN_001', SEC, 'New login detected', 'security', 'security.new_login', 'New sign-in to your VAULTA account', 'New sign-in detected.',
   ['Your account was signed in to from an IP address we have not seen before.'], summary=[('When', '{{event_timestamp}}'), ('IP address', '{{ip_address}}'), ('Browser', '{{device}}')], summary_title='Sign-in details', after=[NOT_YOU], cta=('Review Account Security', 'security_url')),
 T('SECURITY_NEW_DEVICE_001', SEC, 'New device detected', 'security', 'security.new_device', 'New device signed in to your VAULTA account', 'A new device signed in.',
   ['Your account was signed in to from a browser or device we have not seen before.'], summary=[('When', '{{event_timestamp}}'), ('IP address', '{{ip_address}}'), ('Browser', '{{device}}')], summary_title='Sign-in details', after=[NOT_YOU], cta=('Review Account Security', 'security_url')),
 T('SECURITY_PASSWORD_CHANGED_001', SEC, 'Password changed', 'security', 'security.password_changed', 'Your VAULTA password was changed', 'Your password was changed.',
   ['The password for your VAULTA account was changed. Other devices were signed out.'], details=[('When', '{{event_timestamp}}')], after=[NOT_YOU], cta=('Review Account Security', 'security_url')),
 T('SECURITY_PASSWORD_RESET_001', SEC, 'Password reset requested', 'security', 'security.password_reset_requested', 'Reset your VAULTA password', 'Reset your password.',
   ['We received a request to reset the password for your VAULTA account.', 'This link can be used once and expires in {{expires_in}}.'], cta=('Reset Password', 'action_url'),
   notice='If you did not request this, ignore this email: your password will not change.'),
 T('SECURITY_2FA_ENABLED_001', SEC, 'Two-factor authentication enabled', 'security', 'security.2fa_enabled', 'Two-factor authentication is on', 'Two-factor authentication is on.',
   ['Two-factor authentication was enabled for your account. Other devices were signed out.'], badge='Enabled', details=[('When', '{{event_timestamp}}')], after=[NOT_YOU], cta=('Review Account Security', 'security_url')),
 T('SECURITY_2FA_DISABLED_001', SEC, 'Two-factor authentication disabled', 'security', 'security.2fa_disabled', 'Two-factor authentication was turned off', 'Two-factor authentication was turned off.',
   ['Two-factor authentication was disabled for your account, which makes it easier for someone else to sign in.'], badge='Disabled', details=[('When', '{{event_timestamp}}')], after=[NOT_YOU], cta=('Review Account Security', 'security_url')),
 T('SECURITY_WALLET_CHANGED_001', SEC, 'Wallet configuration changed', 'security', 'security.wallet_changed', 'A wallet address on your account changed', 'A wallet address changed.',
   ['A wallet address linked to your account was changed.'], summary=[('Change', '{{wallet_action}}'), ('Network', '{{network}}'), ('Address', '{{wallet_address}}'), ('When', '{{event_timestamp}}')], summary_title='Wallet details', after=[NOT_YOU], cta=('Review Account Security', 'security_url')),
 T('SECURITY_WITHDRAWAL_ALERT_001', SEC, 'Withdrawal security alert', 'security', 'security.withdrawal_alert', 'Security alert: withdrawal request {{transaction_reference}}', 'Withdrawal security alert.',
   ['A withdrawal was requested to a destination you have not used before, or one that needs extra review.'], summary=WD, summary_title='Withdrawal details',
   after=['If you did not request this withdrawal, cancel it from your dashboard if you can, reset your password and contact support immediately.'], cta=('Review Account Security', 'security_url')),
 T('SECURITY_ACCOUNT_RESTRICTED_001', SEC, 'Account restriction notice', 'security', 'security.account_restricted', 'Your VAULTA account status changed', 'Your account status changed.',
   ['VAULTA changed the status of your account.'], badge='{{account_status}}', details=[('Effective', '{{event_timestamp}}')], after=['Some features may be unavailable. Contact support if you have questions.'], cta=('Contact Support', 'support_url')),
 T('SECURITY_SUSPICIOUS_001', SEC, 'Suspicious activity alert', 'security', 'security.suspicious_activity', 'Security alert: repeated failed sign-in attempts', 'Suspicious sign-in activity.',
   ['Several failed attempts to sign in to your account were detected, so we temporarily locked sign-in.'], details=[('When', '{{event_timestamp}}'), ('IP address', '{{ip_address}}')],
   after=['If this was you, wait for the lock to expire or reset your password. If not, reset your password now and enable two-factor authentication.'], cta=('Review Account Security', 'security_url')),

 # ---------------------------------------------------------------- H. account administration
 T('ACCOUNT_PROFILE_UPDATED_001', A, 'Profile updated', 'service', 'account.profile_updated', 'Your VAULTA profile was updated', 'Your profile was updated.',
   ['Your profile information was changed.'], details=[('Updated', '{{changed_fields}}'), ('When', '{{event_timestamp}}')], after=[NOT_YOU], cta=('Review Account Security', 'security_url'), gated=True),
 T('ACCOUNT_EMAIL_CHANGED_001', A, 'Email address changed', 'security', None, 'The email address on your VAULTA account changed', 'Your email address changed.',
   ['The email address on your account was changed.'], details=[('When', '{{event_timestamp}}')], after=[NOT_YOU], cta=('Contact Support', 'support_url'), desc='No automated trigger: the backend has no email-change workflow'),
 T('ACCOUNT_PHONE_CHANGED_001', A, 'Phone number changed', 'security', 'account.phone_changed', 'The phone number on your VAULTA account changed', 'Your phone number changed.',
   ['The phone number on your account was changed.'], details=[('When', '{{event_timestamp}}')], after=[NOT_YOU], cta=('Review Account Security', 'security_url')),
 T('ACCOUNT_ACCESS_RESTORED_001', A, 'Account access restored', 'service', 'account.access_restored', 'Your VAULTA account access was restored', 'Your account access was restored.',
   ['Your account has been reactivated and you can use it again.'], badge='Active', details=[('Effective', '{{event_timestamp}}')], cta=('View Dashboard', 'dashboard_url')),
 T('ACCOUNT_TERMS_UPDATED_001', A, 'Terms updated', 'service', None, 'We are updating our Terms', 'We are updating our Terms.',
   ['We have updated the {{document_name}}. The changes take effect on {{effective_date}}.', '{{message_body}}'], cta=('Read the Update', 'document_url'), manual=True, admin_vars=('document_name', 'effective_date', 'message_body'),
   desc='Sent by an administrator; the legal module does not announce new versions by email on its own'),
 T('ACCOUNT_PRIVACY_UPDATED_001', A, 'Privacy notice updated', 'service', None, 'We are updating our Privacy Notice', 'We are updating our Privacy Notice.',
   ['We have updated the {{document_name}}. The changes take effect on {{effective_date}}.', '{{message_body}}'], cta=('Read the Update', 'document_url'), manual=True, admin_vars=('document_name', 'effective_date', 'message_body'),
   desc='Sent by an administrator'),
 T('ACCOUNT_STATEMENT_001', A, 'Account statement available', 'service', None, 'Your account statement is available', 'Your account statement is ready.',
   ['A new statement for your account is available in your VAULTA documents.'], cta=('View Statements', 'dashboard_url'), desc='No automated trigger: statements are generated on request, not on a schedule'),

 # ---------------------------------------------------------------- I. support
 T('SUPPORT_TICKET_CREATED_001', SUP, 'Support request received', 'transactional', 'support.ticket_created', 'We received your request {{ticket_reference}}', 'We have received your request.',
   ['Thank you for contacting VAULTA support. Your request is logged and our team will reply in your support inbox.'], summary=[('Reference', '{{ticket_reference}}'), ('Subject', '{{ticket_subject}}'), ('Received', '{{event_timestamp}}')], summary_title='Request', cta=('View Request', 'support_url')),
 T('SUPPORT_REPLY_001', SUP, 'Support team replied', 'transactional', 'support.reply', 'New reply on your request {{ticket_reference}}', 'Our team replied to your request.',
   ['There is a new reply on your support request. For your security we do not include the message in this email.'], summary=[('Reference', '{{ticket_reference}}'), ('Subject', '{{ticket_subject}}')], summary_title='Request', cta=('Read Reply', 'support_url')),
 T('SUPPORT_INFO_REQUIRED_001', SUP, 'Additional information required', 'transactional', 'support.info_required', 'We need more information for {{ticket_reference}}', 'We need more information from you.',
   ['Our team needs more information to continue with your request. Please open it and reply.'], badge='Waiting for you', summary=[('Reference', '{{ticket_reference}}'), ('Subject', '{{ticket_subject}}')], summary_title='Request', cta=('Reply to Request', 'support_url')),
 T('SUPPORT_RESOLVED_001', SUP, 'Support request resolved', 'transactional', 'support.resolved', 'Your request {{ticket_reference}} was resolved', 'Your request was resolved.',
   ['We marked your support request as resolved. If you still need help, reply to it and we will pick it up again.'], badge='Resolved', summary=[('Reference', '{{ticket_reference}}'), ('Subject', '{{ticket_subject}}')], summary_title='Request', cta=('View Request', 'support_url')),
 T('SUPPORT_CLOSED_001', SUP, 'Support request closed', 'transactional', 'support.closed', 'Your request {{ticket_reference}} was closed', 'Your request was closed.',
   ['Your support request is now closed. You can open a new request at any time.'], badge='Closed', summary=[('Reference', '{{ticket_reference}}'), ('Subject', '{{ticket_subject}}')], summary_title='Request', cta=('Contact Support', 'support_url')),

 # ---------------------------------------------------------------- J. announcements & marketing (sent by administrators; marketing requires opt-in consent)
 T('ANNOUNCEMENT_GENERAL_001', ANN, 'General announcement', 'service', None, 'An update from VAULTA', 'An update from VAULTA.', ['{{message_body}}'], cta=('View Dashboard', 'dashboard_url'), manual=True, admin_vars=('message_body',),
   desc='Non-promotional service announcement'),
 T('ANNOUNCEMENT_MAINTENANCE_001', ANN, 'Scheduled maintenance', 'service', None, 'Scheduled maintenance: {{maintenance_window}}', 'Scheduled maintenance.',
   ['We will carry out scheduled maintenance. Some features may be unavailable during this time.'], details=[('Maintenance window', '{{maintenance_window}}')], after=['{{message_body}}'], optional=('message_body',), manual=True, admin_vars=('maintenance_window', 'message_body'), cta=('View Dashboard', 'dashboard_url')),
 T('ANNOUNCEMENT_FEATURE_001', ANN, 'New feature announcement', 'marketing', None, 'New on VAULTA', 'New on VAULTA.', ['{{message_body}}'], cta=('Explore VAULTA', 'dashboard_url'), manual=True, admin_vars=('message_body',),
   notice=RISK),
 T('ANNOUNCEMENT_INTERRUPTION_001', ANN, 'Service interruption', 'service', None, 'Service interruption', 'Service interruption.',
   ['We are experiencing a service interruption. Our team is working on it.'], after=['{{message_body}}'], optional=('message_body',), manual=True, admin_vars=('message_body',), cta=('View Dashboard', 'dashboard_url')),
 T('NEWSLETTER_001', ANN, 'Newsletter', 'marketing', None, 'Your VAULTA update', 'Your VAULTA update.', ['{{message_body}}'], cta=('Open VAULTA', 'dashboard_url'), manual=True, admin_vars=('message_body',), notice=RISK),
 T('EDUCATION_001', ANN, 'Educational content', 'marketing', None, 'Learn something new with VAULTA', 'Learn with VAULTA.', ['{{message_body}}'], cta=('Read More', 'dashboard_url'), manual=True, admin_vars=('message_body',),
   notice='Educational content is general information, not personal advice. ' + RISK),
 T('ANNOUNCEMENT_PRODUCT_001', ANN, 'Product announcement', 'marketing', None, 'A new product on VAULTA', 'A new product on VAULTA.', ['{{message_body}}'], cta=('View Product', 'dashboard_url'), manual=True, admin_vars=('message_body',), notice=RISK),
 T('REENGAGE_001', ANN, 'Customer re-engagement', 'marketing', 'reminder.reengagement', 'We have not seen you for a while', 'We have not seen you for a while.',
   ['It has been a while since you last signed in to VAULTA. Your account is just as you left it.'], cta=('Open VAULTA', 'dashboard_url'), notice='You are receiving this because you opted in to updates from VAULTA.'),

 # ---------------------------------------------------------------- K. administrative communication (one-to-one, written by staff)
 T('ADMIN_CUSTOM_001', ADM, 'Custom email', 'admin', None, 'A message from VAULTA', 'A message from VAULTA.', ['{{message_body}}'], cta=('View Dashboard', 'dashboard_url'), manual=True, admin_vars=('message_body',)),
 T('ADMIN_ACCOUNT_REVIEW_001', ADM, 'Account review notice', 'admin', None, 'Your VAULTA account is under review', 'Your account is under review.',
   ['Our team is reviewing your account.', '{{message_body}}'], badge='Under review', cta=('Contact Support', 'support_url'), manual=True, admin_vars=('message_body',)),
 T('ADMIN_DOCUMENT_REQUEST_001', ADM, 'Document request', 'admin', None, 'We need a document from you', 'We need a document from you.',
   ['Our team needs the following to continue.', '{{message_body}}'], badge='Action required', after=['Please upload documents only through your VAULTA dashboard. We will never ask you to send documents by email.'], cta=('Open Dashboard', 'dashboard_url'), manual=True, admin_vars=('message_body',)),
 T('ADMIN_TRANSACTION_REVIEW_001', ADM, 'Transaction review notice', 'admin', None, 'Your transaction {{transaction_reference}} is being reviewed', 'A transaction is being reviewed.',
   ['Our team is reviewing the transaction below.', '{{message_body}}'], badge='Under review', summary=[('Reference', '{{transaction_reference}}'), ('Status', '{{transaction_status}}')], summary_title='Transaction', cta=('View Transaction', 'transaction_url'), manual=True, admin_vars=('transaction_reference', 'message_body')),
 T('ADMIN_WITHDRAWAL_CLARIFY_001', ADM, 'Withdrawal clarification', 'admin', None, 'About your withdrawal {{transaction_reference}}', 'About your withdrawal.',
   ['We have a question about the withdrawal below.', '{{message_body}}'], badge='Action required', summary=[('Reference', '{{transaction_reference}}'), ('Status', '{{transaction_status}}')], summary_title='Withdrawal', cta=('Contact Support', 'support_url'), manual=True, admin_vars=('transaction_reference', 'message_body')),
 T('ADMIN_SECURITY_NOTICE_001', ADM, 'Security notice', 'admin', None, 'Security notice from VAULTA', 'Security notice.',
   ['{{message_body}}'], after=['VAULTA will never ask for your password, one-time codes or recovery phrases.'], cta=('Review Account Security', 'security_url'), manual=True, admin_vars=('message_body',)),
 T('ADMIN_SUPPORT_RESPONSE_001', ADM, 'Customer-service response', 'admin', None, 'A reply from VAULTA customer service', 'A reply from customer service.',
   ['{{message_body}}'], cta=('Contact Support', 'support_url'), manual=True, admin_vars=('message_body',)),
 T('ADMIN_SERVICE_UPDATE_001', ADM, 'Individual service update', 'admin', None, 'An update about your VAULTA service', 'An update about your service.',
   ['{{message_body}}'], cta=('View Dashboard', 'dashboard_url'), manual=True, admin_vars=('message_body',)),
]
BY_ID = {t['id']: t for t in CATALOG}


def spec_texts(subject, spec):
    """Every human-readable string in a subject + spec (the only places {{variables}} may appear)."""
    texts = [subject, spec.get('headline', ''), spec.get('preheader', ''), spec.get('badge', ''), spec.get('notice', ''), spec.get('summary_title', '')]
    texts += list(spec.get('intro', [])) + list(spec.get('after', []))
    texts += [x for row in spec.get('details', []) + spec.get('summary', []) for x in row]
    cta = spec.get('cta')
    if cta: texts.append(cta.get('label', ''))
    return [t for t in texts if isinstance(t, str)]

def referenced_vars(subject, spec):
    """Every {{variable}} used in a subject + spec, in first-use order."""
    out, texts = [], spec_texts(subject, spec)
    for t in texts:
        for m in PLACEHOLDER.findall(t or ''):
            if m not in out: out.append(m)
    cta = spec.get('cta')
    if cta and cta.get('url_var') and cta['url_var'] not in out: out.append(cta['url_var'])
    return out

def required_vars(subject, spec, optional=()):
    return [v for v in referenced_vars(subject, spec) if v not in optional]

def spec_problems(subject, spec):
    """Validation shared by the seed and by administrator edits. Returns a list of human-readable problems."""
    p = []
    if not (subject or '').strip(): p.append('Subject is required')
    if len(subject or '') > 200: p.append('Subject is too long')
    if not (spec.get('headline') or '').strip(): p.append('Headline is required')
    if not spec.get('intro'): p.append('At least one introductory paragraph is required')
    blob = json.dumps(spec) + (subject or '')
    for bad in ('{%', '{#', '<script', '<iframe', 'javascript:', 'onerror=', 'onload='):
        if bad in blob.lower(): p.append(f'Content may not contain "{bad}"')
    for t in spec_texts(subject or '', spec):          # per field: braces in the JSON structure itself ("}}" after a nested object) are not placeholders
        if '{{' in PLACEHOLDER.sub('', t) or '}}' in PLACEHOLDER.sub('', t): p.append('Malformed {{variable}} placeholder'); break
    unknown = [v for v in referenced_vars(subject or '', spec) if v not in VARS]
    if unknown: p.append('Unknown variables: ' + ', '.join(unknown))
    cta = spec.get('cta')
    if cta:
        if cta.get('url_var') not in LINK_VARS: p.append('Button link must be one of the supported link variables')
        if not (cta.get('label') or '').strip() or len(cta['label']) > 40: p.append('Button label is required (max 40 characters)')
    return p

def content_hash(subject, spec): return hashlib.sha256(json.dumps([subject, spec], sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def _selfcheck():
    ids = [t['id'] for t in CATALOG]
    assert len(ids) == len(set(ids)), 'duplicate template ids'
    for t in CATALOG:
        assert t['category'] in CATEGORIES, t['id']
        pr = spec_problems(t['subject'], t['spec']); assert not pr, (t['id'], pr)
        assert t['trigger_event'] is None or t['trigger_event'] in EVENTS, (t['id'], t['trigger_event'])
        for v in t['optional'] + t['admin_vars']: assert v in VARS, (t['id'], v)
        if t['mail_class'] in ('admin', 'marketing') or t['manual_send']: assert t['manual_send'] or t['trigger_event'], t['id']
_selfcheck()
