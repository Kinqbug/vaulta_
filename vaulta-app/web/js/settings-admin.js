/* VAULTA admin: System Settings (six tabs). Registered through window.VAULTA_ADMIN_EXT; replaces the old flat settings page.
   Everything shown comes from the server: GET /api/admin/platform/settings (+ /system-info, + /api/admin/email/settings for the mail sending rules).
   Nothing is kept in the browser except the unsaved edits inside the form itself. A control whose backend does not exist is shown DISABLED with the reason,
   never as a working control. Every save goes through the shared confirmation dialog with an audited reason, runs inside that dialog, and the page is
   updated from the server's response (the authoritative value), never from what was typed. All dynamic text is escaped. */
(window.VAULTA_ADMIN_EXT = window.VAULTA_ADMIN_EXT || []).push(({ R, load, V, UI, can }) => {
  if (!can('settings.manage')) return;
  const { $, $$, esc, dt, toast } = UI, DLG = UI.dialog;
  const API = '/api/admin/platform/settings', MAIL = '/api/admin/email/settings';
  const TABS = [['general', 'General Settings'], ['security', 'Security Settings'], ['api', 'API Management'], ['notifications', 'Notification Settings'], ['payments', 'Payment Settings'], ['integrations', 'Integration Settings']];
  const IC = {
    gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z"/>',
    monitor: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
    coins: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
    mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
    plug: '<path d="M9 2v4M15 2v4M7 6h10v5a5 5 0 01-10 0V6zM12 16v6"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5h.01"/>',
    shield: '<path d="M12 3l8 3v6c0 4.5-3.2 8-8 9-4.8-1-8-4.5-8-9V6l8-3z"/><path d="M8.5 12l2.5 2.5L15.5 10"/>',
    key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M16 6l3 3"/>',
    bell: '<path d="M6 16v-5a6 6 0 1112 0v5l1.5 2h-15L6 16zM10 20a2 2 0 004 0"/>',
    wallet: '<rect x="3" y="6" width="18" height="14" rx="2"/><path d="M3 10h18M16 15h2"/>',
    save: '<path d="M5 3h11l3 3v15H5z"/><path d="M8 3v5h7V3M8 21v-7h8v7"/>',
    list: '<path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01"/>'
  };
  const svg = k => `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">${IC[k]}</svg>`;
  let D = null, ROOT = null, FOCUS_TAB = false;

  /* ---------- helpers ---------- */
  const F = n => { for (const g of ['general', 'switches', 'limits', 'financial', 'security', 'payments']) if (D[g] && D[g][n]) return D[g][n]; };
  const str = v => (typeof v === 'boolean' ? (v ? '1' : '0') : String(v ?? ''));
  const uptime = s => { const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); return d ? `${d}d ${h}h ${m}m` : h ? `${h}h ${m}m` : `${m}m ${s % 60}s`; };
  const STATE = { configured: 'Configured', not_configured: 'Not configured', unavailable: 'Unavailable' };
  const badge = (cls, t) => `<span class="st-badge ${cls}">${esc(t)}</span>`;
  const link = (href, text, ok = true) => ok ? `<a class="btn ghost sm" href="${href}">${esc(text)}</a>` : '';
  const unav = k => (D.unavailable || []).find(x => x.key === k) || { reason: 'Not available.' };

  /* ---------- building blocks ---------- */
  const card = (id, icon, title, desc, body) => `<section class="st-card" data-c="${id}" aria-labelledby="c-${id}-t"><header><span class="st-ico">${svg(icon)}</span><div><h3 id="c-${id}-t">${esc(title)}</h3><p>${esc(desc)}</p></div></header>${body}</section>`;
  const formCard = (id, icon, title, desc, rows, btn = 'Save Changes') => card(id, icon, title, desc,
    `<form data-card="${id}" novalidate>${rows}<p class="st-note" role="status" aria-live="polite"></p><div class="st-foot"><button class="btn" type="submit">${svg('save')}${esc(btn)}</button></div></form>`);
  const input = (n, f, attrs) => `<input id="st-${n}" name="${n}" value="${esc(str(f.value))}" data-orig="${esc(str(f.value))}" ${attrs} aria-describedby="h-${n} e-${n}">`;
  function field(n, { label, help = false } = {}) {
    const f = F(n); let ctl;
    if (n === 'language' || n === 'timezone') ctl = `<select id="st-${n}" name="${n}" data-orig="${esc(f.value)}" aria-describedby="e-${n}">${D.options[n].map(o => { const v = o.value ?? o, t = o.label ?? o; return `<option value="${esc(v)}" ${v === f.value ? 'selected' : ''}>${esc(t)}</option>`; }).join('')}</select>`;
    else if (f.editable === false) ctl = `<input id="st-${n}" value="${esc(f.value || 'Not set')}" readonly aria-readonly="true" aria-describedby="h-${n}">`;
    else if (f.kind === 'money') ctl = `<div class="st-in pre"><span class="st-aff" aria-hidden="true">$</span>${input(n, f, 'inputmode="decimal" autocomplete="off"')}</div>`;
    else if (f.kind === 'percent') ctl = `<div class="st-in suf"><span class="st-aff r" aria-hidden="true">%</span>${input(n, f, 'inputmode="decimal" autocomplete="off"')}</div>`;
    else if (f.kind === 'int') ctl = `<div class="st-in ${f.unit ? 'suf' : ''}">${f.unit ? `<span class="st-aff r" aria-hidden="true">${esc(f.unit)}</span>` : ''}${input(n, f, 'inputmode="numeric" autocomplete="off"')}</div>`;
    else ctl = input(n, f, `${n === 'support_email' ? 'inputmode="email" autocapitalize="none" spellcheck="false"' : ''} maxlength="254" autocomplete="off"`);
    return `<div class="st-row"><label for="st-${n}">${esc(label || f.label)}</label><div class="st-ctl">${ctl}${help || f.editable === false ? `<small class="st-help" id="h-${n}">${esc(f.used_by)}</small>` : ''}<small class="st-err" id="e-${n}" role="alert" hidden></small></div></div>`;
  }
  const SW = {  /* wording for the four platform switches: the dialog states what really happens */
    maintenance_mode: { label: 'Maintenance Mode', desc: 'Put the platform in maintenance mode.', danger: on => on,
      title: on => on ? 'Turn on maintenance mode?' : 'Turn off maintenance mode?', ok: on => on ? 'Turn on' : 'Turn off',
      msg: on => on ? 'Customers will not be able to use VAULTA until you turn this off, and money movement is blocked. Staff accounts keep working so you can turn it off again.' : 'Customers will be able to use VAULTA again.' },
    live_prices_enabled: { label: 'Show Live Prices', desc: 'Display real-time market prices.', danger: on => !on,
      title: on => on ? 'Turn live prices on?' : 'Turn live prices off?', ok: on => on ? 'Turn on' : 'Turn off',
      msg: on => on ? 'VAULTA will request live prices from the market-data provider again.' : 'VAULTA will stop requesting live prices. Customers will see the last saved prices marked as out of date, and prices that were never saved will show as unavailable.' },
    registration_enabled: { label: 'Enable Registration', desc: 'Allow new user registrations.', danger: on => !on,
      title: on => on ? 'Allow new registrations?' : 'Stop new registrations?', ok: on => on ? 'Allow' : 'Stop registrations',
      msg: on => on ? 'New people will be able to create accounts.' : 'New people will not be able to create accounts. Existing customers are not affected.' },
    require_2fa_all: { label: 'Enable Two-Factor Auth', desc: 'Require 2FA for all users.', danger: on => on,
      title: on => on ? 'Require two-factor authentication for everyone?' : 'Stop requiring two-factor authentication?', ok: on => on ? 'Require 2FA' : 'Stop requiring',
      msg: on => on ? 'Every customer and staff account without two-factor authentication will only be able to set it up until it is on. Your own account must already have it enabled.' : 'Accounts will no longer be forced to turn on two-factor authentication. Accounts that already use it keep it.' },
    fiat_funding_enabled: { label: 'Legacy bank and card funding', desc: 'Funding is cryptocurrency-only unless this is on.', danger: on => on,
      title: on => on ? 'Turn on legacy bank and card funding?' : 'Turn off legacy bank and card funding?', ok: on => on ? 'Turn on' : 'Turn off',
      msg: on => on ? 'Customers will be able to start bank and card deposits and withdrawals again, using the fees and limits on this page.' : 'Customers will only be able to fund and withdraw with cryptocurrency.' },
    risk_ack_required: { label: 'Require risk disclosure acknowledgement', desc: 'Customers must accept the Risk Disclosure before investing or trading.', danger: on => !on,
      title: on => on ? 'Require the risk acknowledgement?' : 'Stop requiring the risk acknowledgement?', ok: on => on ? 'Require' : 'Stop requiring',
      msg: on => on ? 'Customers must accept the Risk Disclosure before they can invest or trade.' : 'Customers will be able to invest and trade without accepting the Risk Disclosure. Check this with your legal adviser first.' },
    crypto_requires_kyc: { label: 'Require verified KYC for crypto', desc: 'Verified identity is needed for crypto deposits and withdrawals.', danger: on => !on,
      title: on => on ? 'Require verified KYC for crypto?' : 'Stop requiring verified KYC for crypto?', ok: on => on ? 'Require' : 'Stop requiring',
      msg: on => on ? 'Crypto deposits are only credited, and crypto withdrawals only allowed, for customers with verified identity.' : 'Customers without verified identity will be able to use crypto deposits and withdrawals. This weakens a compliance control.' },
    crypto_screenshot_required: { label: 'Require a screenshot on crypto deposits', desc: 'Every crypto deposit request must include a transaction screenshot.', danger: on => !on,
      title: on => on ? 'Require deposit screenshots?' : 'Stop requiring deposit screenshots?', ok: on => on ? 'Require' : 'Stop requiring',
      msg: on => on ? 'Customers must attach a transaction screenshot to every crypto deposit request.' : 'Customers will be able to submit crypto deposit requests without a screenshot, which gives reviewers less evidence.' }
  };
  const sw = (n, { off = false, note } = {}) => { const m = SW[n], f = F(n); return `<div class="st-sw"><div class="st-swt"><span id="sw-${n}-l">${esc(m.label)}</span><small id="sw-${n}-d">${esc(m.desc)}</small></div><button type="button" class="st-switch" role="switch" id="sw-${n}" data-sw="${n}" aria-checked="${f.value ? 'true' : 'false'}" aria-labelledby="sw-${n}-l" aria-describedby="sw-${n}-d"><i></i></button></div>`; };
  const offSwitch = (label, key, desc) => `<div class="st-sw off"><div class="st-swt"><span id="off-${key}-l">${esc(label)}</span><small><b>Not available:</b> ${esc(unav(key).reason)}</small></div><button type="button" class="st-switch" role="switch" aria-checked="false" aria-labelledby="off-${key}-l" disabled><i></i></button></div>`;
  const offField = (label, key, ctl) => `<div class="st-row off"><label for="off-${key}">${esc(label)}</label><div class="st-ctl">${ctl}<small class="st-help"><b>Not available:</b> ${esc(unav(key).reason)}</small></div></div>`;
  const kv = (k, v, note) => `<div class="st-kv"><span>${esc(k)}</span><div>${v}${note ? `<small class="st-help">${esc(note)}</small>` : ''}</div></div>`;
  const tile = (label, state, detail) => `<div class="st-tile"><div class="st-tl"><span>${esc(label)}</span>${badge(state, STATE[state] || state)}</div><small class="st-help">${esc(detail)}</small></div>`;
  const integ = key => (D.integrations || []).find(i => i.key === key) || { state: 'unavailable', detail: 'Not available.', label: key };

  /* ---------- tab content ---------- */
  function general(info) {
    const em = D.email || {}, mailOk = can('email.templates.read') || can('email.send') || can('email.logs.read') || can('email.settings.manage');
    const tiles = [['Payment Gateway', 'payments'], ['Analytics Provider', 'analytics'], ['Crypto Payments', 'crypto'], ['Google Analytics', 'google_analytics'], ['Chat Service', 'chat'], ['LiveChat', 'livechat']].map(([l, k]) => { const i = integ(k); return tile(l, i.state, i.detail); }).join('');
    return `<div class="st-grid">`
      + formCard('platform', 'gear', 'Platform Configuration', 'Configure basic platform information and preferences.', field('name', { label: 'Platform Name' }) + field('site_url', { label: 'Site URL' }) + field('support_email', { label: 'Support Email' }) + field('language', { label: 'Default Language' }) + field('timezone', { label: 'Time Zone' }))
      + card('display', 'monitor', 'Display Settings', 'Customize the platform display and behavior.', ['maintenance_mode', 'live_prices_enabled', 'registration_enabled', 'require_2fa_all'].map(n => sw(n)).join(''))
      + formCard('financial', 'coins', 'Financial Settings', 'Configure financial and transaction settings.',
        field('min_deposit', { label: 'Minimum Deposit', help: true })
        + offField('Minimum Withdrawal', 'min_withdrawal', '<div class="st-in pre"><span class="st-aff" aria-hidden="true">$</span><input id="off-min_withdrawal" value="Set per asset network" disabled></div>')
        + field('trade_fee_percent', { label: 'Trading Fee (%)', help: true })
        + offField('Withdrawal Processing Time', 'withdrawal_processing_time', '<select id="off-withdrawal_processing_time" disabled><option>Manual review</option></select>')
        + `<p class="st-help st-links">Per-network minimums, maximums and fees: ${link('#/assets', 'Assets & Markets', can('assets.manage'))}</p>`)
      + card('email', 'mail', 'Email & Notifications', 'Manage email templates and notification preferences.',
        offField('Admin Email', 'admin_email', '<input id="off-admin_email" value="" placeholder="Not available" disabled>')
        + offField('Notification Email', 'notification_email', '<input id="off-notification_email" value="" placeholder="Not available" disabled>')
        + offSwitch('Enable Email Alerts', 'email_alerts') + offSwitch('Send Daily Reports', 'daily_reports')
        + kv('Email delivery', em.live_delivery ? badge('configured', 'Live delivery') : badge('not_configured', 'Not delivering'), em.warning || `Provider: ${em.provider || 'unknown'}`)
        + `<div class="st-foot">${mailOk ? `<a class="btn" href="#/email">${svg('mail')}Manage Templates</a>` : ''}</div>`)
      + formCard('integration', 'plug', 'Integration Settings', 'Configure third-party integrations and services.', `<div class="st-tiles">${tiles}</div>` + field('api_rate_limit_per_hour', { label: 'API Rate Limit', help: true }))
      + card('sysinfo', 'info', 'System Information', 'View system details and version information.',
        kv('Platform Version', info.version ? esc(info.version) : '<span class="st-mute">Not set</span>', info.version ? '' : info.version_note)
        + kv('Last Updated', info.last_updated ? esc(dt(info.last_updated)) : '<span class="st-mute">Unknown</span>', info.last_updated_source === 'environment' ? 'From the deployment build time.' : 'Newest change to the server code (no build time is set).')
        + kv('Server Uptime', esc(uptime(info.uptime_seconds)), 'Since this server process started. Availability is not tracked.')
        + kv('Environment', badge(info.environment === 'production' ? 'prod' : 'env', info.environment.charAt(0).toUpperCase() + info.environment.slice(1)), `Payments: ${info.payment_mode}. Crypto: ${info.crypto_mode}.`))
      + `</div>`;
  }
  function security() {
    const pol = (D.policy || []).map(p => kv(p.label, `${esc(p.value)} ${esc(p.unit)}`, /^[A-Z_]+$/.test(p.source) ? `Set by ${p.source}` : p.source)).join('');
    return `<div class="st-grid">`
      + card('auth', 'shield', 'Two-factor authentication', 'Control who must use a second sign-in step.', sw('require_2fa_all') + `<p class="st-help">Accounts that already use two-factor authentication are asked for a code when they sign in. Turning this on makes it compulsory for everyone, including staff.</p>`)
      + formCard('access', 'key', 'Compliance and access', 'Thresholds and requirements that gate sensitive actions.',
        field('twofa_required_above', { help: true }) + field('withdrawal_review_threshold', { help: true }) + sw('risk_ack_required') + sw('crypto_requires_kyc'))
      + card('policy', 'list', 'Sign-in policy', 'Fixed by the server configuration, shown for reference.', pol + `<p class="st-help">These are set in the server's environment and need a restart to change, so they cannot be edited here.</p>`)
      + card('monitor', 'bell', 'Monitoring and access control', 'Where security activity is recorded and managed.',
        `<p class="st-help">Every change made on these pages is written to the tamper-evident audit log with the administrator and the reason.</p><div class="st-foot">${link('#/audit', 'Audit log', can('audit.read'))}${link('#/roles', 'Roles & permissions', can('roles.manage'))}</div>`)
      + `</div>`;
  }
  function api() {
    const creds = ['payments', 'market_data', 'email', 'kyc'].map(k => { const i = integ(k); return tile(i.label, i.state, i.detail); }).join('');
    return `<div class="st-grid">`
      + formCard('rate', 'gear', 'Rate limiting', 'Cap how many API requests a customer can make.', field('api_rate_limit_per_hour', { label: 'Requests per hour', help: true }))
      + card('creds', 'key', 'API credentials', 'Keys and tokens for external clients.',
        `<p class="st-help">VAULTA does not issue API keys or tokens to external clients, so there is nothing to create, rotate or revoke here.</p><h4 class="st-sub">Provider credentials</h4><div class="st-tiles">${creds}</div><p class="st-help">Provider secrets are environment variables on the server. They are never sent to this page.</p>`)
      + card('usage', 'list', 'Access records and usage', 'Who changed what, and how the API is used.',
        `<p class="st-help">Administrative changes are recorded in the audit log. API usage metrics are not collected, so none are shown.</p><div class="st-foot">${link('#/audit', 'Open audit log', can('audit.read'))}</div>`)
      + `</div>`;
  }
  const MAILF = [['email_quiet_start_hour', 'Quiet hours start', 'int', 0, 23, 'hour'], ['email_quiet_end_hour', 'Quiet hours end', 'int', 0, 23, 'hour'], ['email_max_attempts', 'Delivery attempts before failure', 'int', 1, 10, 'attempts'],
    ['email_bulk_approval_threshold', 'Campaign approval threshold', 'int', 1, 100000, 'recipients'], ['email_campaign_max_recipients', 'Maximum recipients per campaign', 'int', 1, 100000, 'recipients'], ['email_tracking_enabled', 'Track opens and clicks', 'flag', 0, 1]];
  function notifications(ms) {
    const em = D.email || {};
    const rules = !can('email.settings.manage') ? `<p class="st-help">You need the email settings permission to see or change these rules.</p>`
      : !ms ? `<p class="st-help">The email settings could not be loaded.</p>`
      : `<form data-card="mailrules" novalidate>${MAILF.map(([n, l, k, lo, hi, u]) => { const v = str(ms.settings[n] && ms.settings[n].value);
        const ctl = k === 'flag' ? `<select id="st-${n}" name="${n}" data-orig="${esc(v)}" aria-describedby="e-${n}"><option value="0" ${v === '0' ? 'selected' : ''}>Off</option><option value="1" ${v === '1' ? 'selected' : ''}>On</option></select>`
          : `<div class="st-in suf"><span class="st-aff r" aria-hidden="true">${esc(u)}</span><input id="st-${n}" name="${n}" value="${esc(v)}" data-orig="${esc(v)}" inputmode="numeric" autocomplete="off" aria-describedby="h-${n} e-${n}"></div>`;
        return `<div class="st-row"><label for="st-${n}">${esc(l)}</label><div class="st-ctl">${ctl}<small class="st-help" id="h-${n}">${esc(ms.settings[n] ? ms.settings[n].description : '')}${k === 'int' ? ` (${lo} to ${hi})` : ''}</small><small class="st-err" id="e-${n}" role="alert" hidden></small></div></div>`; }).join('')}
        <p class="st-note" role="status" aria-live="polite"></p><div class="st-foot"><button class="btn" type="submit">${svg('save')}Save Changes</button></div></form>`;
    const chan = [tile('Email', em.live_delivery ? 'configured' : 'not_configured', em.live_delivery ? `Live delivery through ${em.provider}.` : (em.warning || 'Email is not delivering.')),
      tile('In-app notifications', 'configured', 'Broadcast from Communications > Notifications.'), tile('SMS', 'unavailable', 'VAULTA has no SMS delivery.'), tile('Push notifications', 'unavailable', 'VAULTA has no push delivery.')].join('');
    return `<div class="st-grid">`
      + card('delivery', 'mail', 'Email delivery', 'Whether messages are actually leaving VAULTA.',
        kv('Provider', esc(em.provider || 'Unknown')) + kv('Delivery', em.live_delivery ? badge('configured', 'Live') : badge('not_configured', 'Captured locally, not sent'), em.warning || '')
        + kv('Sender address', esc(em.from_address || 'Not set')) + kv('Delivery confirmation', em.delivery_confirmation ? badge('configured', 'On') : badge('unavailable', 'Off'), em.delivery_confirmation ? '' : 'Needs a webhook signing secret on the server.')
        + `<div class="st-foot">${link('#/email', 'Templates and delivery logs', can('email.templates.read') || can('email.logs.read'))}</div>`)
      + card('mailrules', 'bell', 'Sending rules', 'Limits and quiet hours applied by the email system.', rules)
      + card('channels', 'list', 'Notification channels', 'What VAULTA can actually deliver.', `<div class="st-tiles">${chan}</div>`)
      + `</div>`;
  }
  function payments() {
    const crypto = can('crypto.wallets.read') || can('crypto.recon.read') || can('crypto.deposits.read');
    return `<div class="st-grid">`
      + formCard('funding', 'coins', 'Funding and limits', 'Legacy bank and card funding, and deposit and withdrawal limits.',
        sw('fiat_funding_enabled') + field('min_deposit', { label: 'Minimum deposit', help: true }) + field('max_deposit', { help: true }) + field('daily_withdrawal_limit', { help: true }))
      + formCard('fees', 'wallet', 'Fees and rates', 'Charges and rates applied by the ledger.',
        ['trade_fee_percent', 'card_fee_percent', 'bank_fee_percent', 'savings_apy_percent', 'crypto_spread_percent'].map(n => field(n, { help: true })).join(''))
      + formCard('cryptodep', 'shield', 'Crypto deposits', 'How crypto deposit requests are opened and reviewed.',
        sw('crypto_screenshot_required') + ['crypto_max_pending_requests', 'crypto_intent_ttl_hours', 'crypto_intent_grace_hours', 'crypto_price_max_age_minutes'].map(n => field(n, { help: true })).join(''))
      + card('networks', 'plug', 'Asset networks and wallets', 'Where per-network settings live.',
        `<p class="st-help">Minimums, maximums, fees and confirmations for each cryptocurrency network are set where that network is configured, so there is a single source of truth. Funding stays inside the existing review and approval workflow.</p><div class="st-foot">${link('#/assets', 'Configure asset networks', can('assets.manage'))}${link('#/settings-crypto', 'Wallets & Reconciliation', crypto)}${link('#/deposits', 'Crypto deposits', can('crypto.deposits.read'))}${link('#/withdrawals', 'Crypto withdrawals', can('crypto.withdrawals.read'))}</div>`)
      + `</div>`;
  }
  function integrations() {
    const items = ['payments', 'crypto', 'market_data', 'email', 'kyc', 'chat', 'analytics', 'google_analytics', 'livechat'].map(k => { const i = integ(k); return card('i-' + k, 'plug', i.label, STATE[i.state] || i.state, `<p class="st-help">${esc(i.detail)}</p><div>${badge(i.state, STATE[i.state] || i.state)}</div>`); }).join('');
    return `<p class="st-banner" role="note"><b>Configured is not the same as connected.</b> This page shows whether each provider's configuration is present on the server. VAULTA does not yet run live connection tests, so no integration is reported as connected. Credentials are server environment variables and are never shown.</p><div class="st-grid">${items}</div>`;
  }

  /* ---------- page ---------- */
  R.settings = sub => {
    const tab = TABS.some(t => t[0] === sub) ? sub : 'general';
    return load(async v => {
      const [d, info, ms] = await Promise.all([V.get(API), tab === 'general' ? V.get(API.replace('/settings', '/system-info')) : null, tab === 'notifications' && can('email.settings.manage') ? V.get(MAIL).catch(() => null) : null]);
      D = d; ROOT = v;
      const body = { general: () => general(info), security, api, notifications: () => notifications(ms), payments, integrations }[tab]();
      v.innerHTML = `<div class="a-ph"><div><h1>System Settings</h1><p>Manage your platform settings, security preferences, and integrations.</p></div></div>
        <div class="st-tabs" role="tablist" aria-label="System Settings sections">${TABS.map(([k, l]) => `<a role="tab" id="tab-${k}" href="#/settings/${k}" aria-selected="${k === tab}" aria-controls="st-panel" tabindex="${k === tab ? 0 : -1}">${esc(l)}</a>`).join('')}</div>
        <div id="st-panel" role="tabpanel" aria-labelledby="tab-${tab}">${body}</div>`;
      if (FOCUS_TAB) { FOCUS_TAB = false; $('#tab-' + tab, v).focus(); }
      wire(v, tab);
    });
  };

  /* ---------- behaviour (listeners are attached to this render's own node, so they can never stack) ---------- */
  const errEl = (form, n) => $(`#e-${n}`, form), inEl = (form, n) => form.elements[n];
  function clearErrs(form) { $$('.st-err', form).forEach(e => { e.hidden = true; e.textContent = ''; }); $$('[aria-invalid]', form).forEach(i => i.removeAttribute('aria-invalid')); $('.st-note', form).textContent = ''; }
  function showErrs(form, errs) { let first = null; Object.entries(errs).forEach(([n, m]) => { const e = errEl(form, n), i = inEl(form, n); if (e) { e.textContent = m; e.hidden = false; } if (i) { i.setAttribute('aria-invalid', 'true'); first = first || i; } }); if (first) first.focus(); }
  function check(form, ch) {
    const bad = {}, mail = form.dataset.card === 'mailrules';
    for (const [n, v] of Object.entries(ch)) {
      const m = mail ? MAILF.find(x => x[0] === n) : null, f = mail ? null : F(n), kind = mail ? m[2] : f.kind;
      if (kind === 'text') { if (n === 'name' ? !v || v.length > 60 || /[<>]/.test(v) : n === 'support_email' ? !/^[^@\s<>]{1,64}@[^@\s<>]+\.[^@\s<>]{2,}$/.test(v) : !v) bad[n] = n === 'name' ? 'Enter a platform name of 1 to 60 characters without < or >.' : n === 'support_email' ? 'Enter a valid email address.' : 'Choose a value.'; }
      else if (kind === 'money') { if (!/^\d{1,9}(\.\d{1,2})?$/.test(v)) bad[n] = 'Enter an amount such as 10.00 (at most 2 decimal places).'; }
      else if (kind === 'percent') { if (!/^\d{1,2}(\.\d{1,2})?$/.test(v)) bad[n] = 'Enter a percentage such as 0.10 (at most 2 decimal places).'; }
      else if (kind === 'int') { const lo = mail ? m[3] : f.min, hi = mail ? m[4] : f.max; if (!/^\d{1,7}$/.test(v) || (lo != null && +v < lo) || (hi != null && +v > hi)) bad[n] = lo != null ? `Enter a whole number from ${lo} to ${hi}.` : 'Enter a whole number.'; }
      else if (kind === 'flag' && !/^[01]$/.test(v)) bad[n] = 'Choose On or Off.';
    }
    return bad;
  }
  const labelOf = (form, n) => { const l = $(`label[for="st-${n}"]`, form); return l ? l.textContent : n; };
  function paint(v) {                                         /* show the server's authoritative values */
    $$('[data-sw]', v).forEach(b => b.setAttribute('aria-checked', F(b.dataset.sw).value ? 'true' : 'false'));
  }
  function save(form) {
    clearErrs(form); const ch = {};
    $$('[data-orig]', form).forEach(el => { if (!el.readOnly && !el.disabled && el.value.trim() !== el.dataset.orig) ch[el.name] = el.value.trim(); });
    if (!Object.keys(ch).length) { $('.st-note', form).textContent = 'No changes to save.'; return; }
    const bad = check(form, ch); if (Object.keys(bad).length) { showErrs(form, bad); return; }
    const mail = form.dataset.card === 'mailrules', names = Object.keys(ch).map(n => labelOf(form, n)).join(', ');
    DLG.confirm('Save changes?', `You are changing: ${names}.`, { ok: 'Save changes', reason: true, key: 'st-save:' + form.dataset.card, run: async reason => {
      try {
        const r = mail ? await V.put(MAIL, { settings: ch, reason }) : await V.put(API, { changes: ch, reason });
        if (!form.isConnected) return r;
        if (mail) Object.entries(ch).forEach(([n, x]) => { const el = inEl(form, n); el.value = el.dataset.orig = String(parseInt(x, 10)); });
        else { Object.assign(D, r); Object.keys(ch).forEach(n => { const el = inEl(form, n), val = str(F(n).value); el.value = val; el.dataset.orig = val; }); paint(ROOT); }
        return r;
      } catch (err) {
        if (err.code === 'validation_failed' && err.details && err.details.fields && form.isConnected) { showErrs(form, err.details.fields); return { invalid: true }; }   /* closes the dialog; the fields explain what to fix */
        throw err;
      }
    } }).then(res => { if (res && res.result && !res.result.invalid) toast('Settings saved'); });
  }
  function wire(v, tab) {
    v.addEventListener('submit', e => { const form = e.target.closest('form[data-card]'); if (form) { e.preventDefault(); save(form); } });
    v.addEventListener('click', e => {
      const b = e.target.closest('[data-sw]'); if (!b || b.disabled) return;
      const n = b.dataset.sw, to = b.getAttribute('aria-checked') !== 'true', m = SW[n];
      DLG.confirm(m.title(to), m.msg(to), { ok: m.ok(to), reason: true, danger: m.danger(to), key: 'st-sw:' + n, run: async reason => {
        const r = await V.put(API, { changes: { [n]: to }, reason }); Object.assign(D, r); if (v.isConnected) paint(v); return r; } })
        .then(res => { if (res && res.result) toast(`${m.label} ${F(n).value ? 'turned on' : 'turned off'}`); });
    });
    $('.st-tabs', v).addEventListener('keydown', e => {
      const keys = { ArrowRight: 1, ArrowLeft: -1 }; if (!(e.key in keys) && e.key !== 'Home' && e.key !== 'End') return;
      e.preventDefault(); const i = TABS.findIndex(t => t[0] === tab), j = e.key === 'Home' ? 0 : e.key === 'End' ? TABS.length - 1 : (i + keys[e.key] + TABS.length) % TABS.length;
      FOCUS_TAB = true; $('#tab-' + TABS[j][0], v).click();
    });
  }

  /* unsaved-change protection: a click on any in-app link, or a reload, asks first while a field differs from the saved value */
  const dirtyNow = () => !!ROOT && ROOT.isConnected && $$('[data-orig]', ROOT).some(el => !el.readOnly && !el.disabled && el.value.trim() !== el.dataset.orig);
  document.addEventListener('click', e => {
    const a = e.target.closest('a[href^="#/"]'); if (!a || !dirtyNow() || a.getAttribute('href') === location.hash) return;
    e.preventDefault(); e.stopPropagation(); const to = a.getAttribute('href'), keep = FOCUS_TAB;
    DLG.confirm('Discard unsaved changes?', 'Some fields on this page have not been saved.', { ok: 'Discard changes', danger: true, key: 'st-discard' }).then(ok => { if (ok) { ROOT = null; FOCUS_TAB = keep; location.hash = to; } else FOCUS_TAB = false; });
  }, true);
  addEventListener('beforeunload', e => { if (dirtyNow()) { e.preventDefault(); e.returnValue = ''; } });
});
