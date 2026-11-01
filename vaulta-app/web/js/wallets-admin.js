/* VAULTA admin: "Wallets & Reconciliation" (Crypto Operations) and the helpers shared with the dedicated Asset Settings page (asset-settings.js).
   Everything is rendered from the real backend (/api/admin/crypto/...). Nothing here is a dialog: row details, address changes and reviews open INLINE.
   Hiding a button is cosmetic; every action is re-checked on the server (permissions, 2FA, separation of duties). No private key field exists anywhere. */
(window.VAULTA_ADMIN_EXT = window.VAULTA_ADMIN_EXT || []).push(({ R, load, V, UI, can }) => {
  const { $, $$, esc, dt, label, toast, logo } = UI;
  window.VAULTA_ADMIN_PAGES = Object.assign(window.VAULTA_ADMIN_PAGES || {}, {
    'asset-settings': { title: 'Asset Settings', parent: 'settings-crypto', perms: ['crypto.registry.read', 'crypto.wallets.read'] },
    'crypto-sources': { title: 'Sending-address verification', parent: 'settings-crypto', perms: ['crypto.deposits.read'] }
  });

  /* ------------------------------------------------------------ shared helpers (also used by asset-settings.js) ------------------------------------------------------------ */
  const short = (a, h = 8, t = 6) => (a && a.length > h + t + 3 ? a.slice(0, h) + '…' + a.slice(-t) : a || '');
  const ST = { active: 'completed', inactive: 'draft', maintenance: 'pending', manual_review: 'pending', retired: 'rejected', disabled: 'draft', suspended: 'pending' };
  const STX = { active: 'Active', inactive: 'Inactive', maintenance: 'Maintenance', manual_review: 'Manual review', retired: 'Retired', disabled: 'Inactive', suspended: 'Maintenance' };
  const chip = (s, text) => `<span class="st st-${ST[s] || 'pending'}">${esc(text || STX[s] || label(s))}</span>`;
  const RS = { reconciled: ['completed', 'Reconciled'], pending: ['pending', 'Pending'], confirming: ['pending', 'Confirming'], mismatch: ['failed', 'Mismatch'], failed: ['failed', 'Failed'] };
  const rchip = s => `<span class="st st-${(RS[s] || RS.pending)[0]}">${esc((RS[s] || [0, label(s)])[1])}</span>`;
  const mono = t => `<code class="wr-addr">${esc(t)}</code>`;
  const ico = (d, s = 20) => `<svg viewBox="0 0 24 24" width="${s}" height="${s}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${d}</svg>`;
  const I = {
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>', wallet: '<path d="M3 7a2 2 0 0 1 2-2h13v4"/><path d="M3 7v11a2 2 0 0 0 2 2h14a1 1 0 0 0 1-1v-9a1 1 0 0 0-1-1H5a2 2 0 0 1-2-2z"/><circle cx="16.5" cy="14.5" r="1"/>',
    trend: '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>', shield: '<path d="M12 3l8 3v6c0 4.5-3.2 8-8 9-4.8-1-8-4.5-8-9V6l8-3z"/>', clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    refresh: '<path d="M20 12a8 8 0 1 1-2.6-5.9"/><path d="M20 4v5h-5"/>', filter: '<path d="M4 6h16M7 12h10M10 18h4"/>', search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4-4"/>', more: '<circle cx="5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="19" cy="12" r="1.3"/>',
    chev: '<path d="m9 6 6 6-6 6"/>', info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>', ext: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>'
  };
  const ago = iso => { if (!iso) return null; const s = Math.max(0, (Date.now() - new Date(iso.endsWith('Z') || /[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z').getTime()) / 1000);
    if (s < 60) return 'just now'; const m = Math.floor(s / 60); if (m < 60) return m + (m === 1 ? ' minute ago' : ' minutes ago'); const h = Math.floor(m / 60); if (h < 48) return h + (h === 1 ? ' hour ago' : ' hours ago'); return Math.floor(h / 24) + ' days ago'; };
  const usd = n => (n == null ? null : '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  async function copyText(text, btn) {
    try { await navigator.clipboard.writeText(text); } catch (e) {
      const t = document.createElement('textarea'); t.value = text; t.setAttribute('readonly', ''); t.style.cssText = 'position:fixed;opacity:0'; document.body.append(t); t.select();
      let ok = false; try { ok = document.execCommand('copy'); } catch (er) { } t.remove(); if (!ok) { toast('Copy is not available in this browser. Select the address and copy it manually.'); return; }
    }
    if (btn) { btn.classList.add('ok'); const old = btn.getAttribute('aria-label'); btn.setAttribute('aria-label', 'Copied'); setTimeout(() => { btn.classList.remove('ok'); if (old) btn.setAttribute('aria-label', old); }, 1400); }
    toast('Address copied');
  }
  const copyBtn = (text, what = 'address') => `<button type="button" class="wr-ic" data-copy="${esc(text)}" aria-label="Copy ${what}" title="Copy ${what}">${ico(I.copy, 16)}</button>`;
  const postForm = (url, fd) => new Promise((res, rej) => {
    const x = new XMLHttpRequest(); x.open('POST', url); x.responseType = 'json'; if (V.state.csrf) x.setRequestHeader('X-CSRF-Token', V.state.csrf);
    x.onerror = () => rej(new Error('Cannot reach the server.'));
    x.onload = () => (x.status >= 200 && x.status < 300 ? res(x.response) : rej(Object.assign(new Error((x.response && x.response.error && x.response.error.message) || 'The request failed.'), { code: x.response && x.response.error && x.response.error.code })));
    x.send(fd);
  });
  /* One inline form (never a dialog). Fields: {n,l,type,v,req,code,mono,rows,max,o,hint}. `submit(values)` may throw: the message is shown in the form and the form stays open. */
  function inlineForm(host, { title, intro, fields, ok, submit, onCancel, danger }) {
    const old = host.querySelector(':scope > .wr-inline'); if (old) old.remove();
    const f = x => x.type === 'select' ? `<label class="f">${esc(x.l)}<select name="${x.n}">${x.o.map(o => `<option value="${esc(o[0])}" ${o[0] === x.v ? 'selected' : ''}>${esc(o[1])}</option>`).join('')}</select></label>`
      : x.type === 'checkbox' ? `<label class="f wr-check"><input type="checkbox" name="${x.n}" ${x.v ? 'checked' : ''}> <span>${esc(x.l)}</span></label>`
      : `<label class="f">${esc(x.l)}${x.type === 'textarea' ? `<textarea name="${x.n}" rows="${x.rows || 3}" maxlength="${x.max || 500}" ${x.req ? 'required' : ''}>${esc(x.v || '')}</textarea>`
        : `<input name="${x.n}" value="${esc(x.v || '')}" ${x.req ? 'required' : ''} ${x.code ? 'inputmode="numeric" autocomplete="one-time-code" maxlength="8"' : 'autocomplete="off"'} ${x.mono ? 'spellcheck="false" class="mono"' : ''}>`}${x.hint ? `<small class="hint">${esc(x.hint)}</small>` : ''}</label>`;
    const el = document.createElement('form'); el.className = 'wr-inline'; el.noValidate = false;
    el.innerHTML = `<h4>${esc(title)}</h4>${intro ? `<p class="note">${intro}</p>` : ''}<div class="wr-inline-grid">${fields.map(f).join('')}</div><p class="wr-err" role="alert" hidden></p><div class="wr-inline-act"><button class="btn ${danger ? 'danger' : ''}" type="submit">${esc(ok)}</button><button class="btn ghost" type="button" data-x>Cancel</button></div>`;
    host.append(el); el.querySelector('input,textarea,select').focus({ preventScroll: false });
    const err = el.querySelector('.wr-err'), b = el.querySelector('button[type=submit]');
    el.querySelector('[data-x]').onclick = () => { el.remove(); if (onCancel) onCancel(); };
    el.onsubmit = async ev => {
      ev.preventDefault(); if (b.disabled) return; err.hidden = true;
      const o = {}; for (const x of fields) { const i = el.elements[x.n]; o[x.n] = x.type === 'checkbox' ? i.checked : i.value.trim(); }
      b.disabled = true; try { await submit(o); el.remove(); } catch (e) { err.textContent = e.message || 'The request failed.'; err.hidden = false; b.disabled = false; }
    };
    return el;
  }
  const tabHash = k => '#/settings-crypto?tab=' + k;
  const prm = () => new URLSearchParams((location.hash.split('?')[1] || ''));

  document.addEventListener('click', ev => { $$('details.wr-more[open]').forEach(d => { if (!d.contains(ev.target)) d.open = false; }); }, true);   // one listener: closes any open row menu on an outside click
  window.VW = { short, chip, rchip, mono, ico, I, ago, usd, copyText, copyBtn, postForm, inlineForm, ST, STX, RS, esc, $, $$, dt, label, toast, logo, V, can, load, R, UI };

  /* ------------------------------------------------------------ Wallets & Reconciliation ------------------------------------------------------------ */
  const KEY = 'vaulta:wr';
  const S = (() => { try { return Object.assign({ q: '', network: '', status: '', sort: 'asset', dir: 1 }, JSON.parse(sessionStorage.getItem(KEY) || '{}')); } catch (e) { return { q: '', network: '', status: '', sort: 'asset', dir: 1 }; } })();
  const RQ = (() => { try { return Object.assign({ asset: '', network: '', type: '', status: '', from: '', to: '', q: '', page: 1 }, JSON.parse(sessionStorage.getItem(KEY + ':rec') || '{}')); } catch (e) { return { asset: '', network: '', type: '', status: '', from: '', to: '', q: '', page: 1 }; } })();
  const persist = () => { try { sessionStorage.setItem(KEY, JSON.stringify(S)); sessionStorage.setItem(KEY + ':rec', JSON.stringify(RQ)); } catch (e) { } };

  const head = tab => `<header class="wr-head"><p class="wr-eyebrow">Crypto Operations</p><h1>Wallets &amp; Reconciliation</h1><p class="a-sub">Manage company wallets, track balances and reconcile on-chain transactions.</p></header>
    <div class="wr-tabs" role="tablist" aria-label="Wallets and reconciliation">
      <a role="tab" class="${tab === 'wallets' ? 'on' : ''}" aria-selected="${tab === 'wallets'}" href="${tabHash('wallets')}" ${tab === 'wallets' ? 'aria-current="page"' : ''}>Company Wallets</a>
      <a role="tab" class="${tab === 'reconciliation' ? 'on' : ''}" aria-selected="${tab === 'reconciliation'}" href="${tabHash('reconciliation')}" ${tab === 'reconciliation' ? 'aria-current="page"' : ''}>Reconciliation</a>
      ${can('crypto.deposits.read') ? `<a class="wr-sidelink" href="#/crypto-sources">Sending addresses ${ico(I.chev, 14)}</a>` : ''}</div>`;

  function card(icon, l, v, sub, tone = '') { return `<div class="wr-card"><span class="wr-cic ${tone}">${ico(icon, 22)}</span><div><small>${esc(l)}</small><b>${v}</b><em>${sub}</em></div></div>`; }

  function summaryCards(o) {
    const s = o.summary, lr = s.last_reconciliation;
    const bal = s.estimated_total_usd == null ? `<span class="wr-na" title="No chain balance or fresh price is available">Unavailable</span>` : usd(s.estimated_total_usd);
    const balSub = s.estimated_total_usd == null ? 'No provider balance or price available' : `Estimated, not a verified balance${s.estimate_complete ? '' : ` · ${s.priced_wallets} of ${s.configured_wallets} wallets priced`}`;
    return `<div class="wr-cards">${card(I.wallet, 'Total Wallets', s.total_wallets, `${s.active_wallets} active · ${s.configured_wallets} configured`)}${card(I.trend, 'Total Balance', bal, balSub, 'up')}
      ${card(I.shield, 'Pending Confirmations', s.pending_confirmations, `${s.pending_deposits} deposit${s.pending_deposits === 1 ? '' : 's'} · ${s.pending_withdrawals} withdrawal${s.pending_withdrawals === 1 ? '' : 's'}`)}
      ${card(I.clock, 'Last Reconciliation', lr ? esc(ago(lr.finished_at) || '—') : 'Never run', lr ? (lr.status === 'clean' ? 'Completed with no exceptions' : lr.status === 'exceptions' ? `Completed · ${s.open_exceptions} open exception${s.open_exceptions === 1 ? '' : 's'}` : 'Run ended with an error') : 'No reconciliation has been run yet')}</div>`;
  }

  const sortVal = (w, k) => k === 'asset' ? w.asset : k === 'network' ? w.network_name : k === 'status' ? w.status : k === 'balance' ? (w.available == null ? -1 : Number(w.available)) : k === 'min' ? Number(w.minimum_deposit || 0) : w.asset;
  function filtered(o) {
    const q = S.q.trim().toLowerCase();
    let rows = o.wallets.filter(w => (!q || w.asset.toLowerCase().includes(q) || w.asset_name.toLowerCase().includes(q) || (w.public_address || '').toLowerCase().includes(q) || (w.label || '').toLowerCase().includes(q))
      && (!S.network || w.network === S.network) && (!S.status || w.status === S.status));
    rows.sort((a, b) => { const x = sortVal(a, S.sort), y = sortVal(b, S.sort); return (typeof x === 'number' ? x - y : String(x).localeCompare(String(y))) * S.dir; });
    return rows;
  }
  const th = (k, t, cls = '') => `<th class="${cls}" scope="col" ${S.sort === k ? `aria-sort="${S.dir === 1 ? 'ascending' : 'descending'}"` : ''}><button type="button" class="wr-sort" data-sort="${k}">${esc(t)}<i aria-hidden="true">${S.sort === k ? (S.dir === 1 ? '↑' : '↓') : ''}</i></button></th>`;

  function balCell(w) {
    if (!w.configured) return '<span class="wr-na">Not configured</span>';
    if (w.available == null) return `<span class="wr-na" title="${esc(w.balance_issue || '')}">Unavailable</span><small>${esc(w.balance_issue || 'No chain provider connected')}</small>`;
    const fiat = w.fiat_estimate != null ? `<small>≈ ${esc(usd(w.fiat_estimate))} <span class="wr-est">est.</span></small>` : '<small class="wr-na">No fiat price</small>';
    const pend = Number(w.pending) > 0 ? `<small>Pending ${esc(w.pending)} ${esc(w.asset)}</small>` : '';
    return `<b>${esc(w.available)} ${esc(w.asset)}</b>${pend}${fiat}${w.balance_source === 'test_chain' ? '<small class="wr-test">Test chain, no real funds</small>' : ''}`;
  }
  function row(w, open) {
    const manage = can('crypto.wallets.manage'), href = (t, extra = '') => `#/asset-settings/${w.asset_id}?tab=${t}&net=${encodeURIComponent(w.network)}&wallet=${w.id}${extra}`;
    const full = `<tr class="wr-detail" id="wd-${w.id}"><td colspan="7"><div class="wr-dgrid">
        <div><small>Full wallet address</small>${w.configured ? `<div class="wr-full">${mono(w.public_address)}${copyBtn(w.public_address)}</div>` : '<b>NOT_CONFIGURED</b>'}</div>
        <div><small>Label</small><b>${esc(w.label)}</b></div><div><small>Wallet type · provider</small><b>${esc(label(w.wallet_type))} · ${esc(label(w.provider))}</b></div>
        <div><small>Network</small><b>${esc(w.network_name)}</b> <small>${esc(w.network)}${w.is_testnet ? ' · testnet' : ''}</small></div>
        <div><small>Token contract</small>${w.contract_address ? mono(w.contract_address) : '<b>Native asset</b>'}</div>
        <div><small>Customer deposits</small><b>${w.is_deposit_enabled ? 'Accepting deposits' : 'Not accepting deposits'}</b></div>
        <div><small>Created</small><b>${dt(w.created_at)}</b></div>
        <div class="wr-dact"><a class="btn ghost sm" href="${href('wallet')}">Open wallet configuration</a>${w.explorer_url && w.configured ? `<a class="btn ghost sm" href="${esc(w.explorer_url)}/address/${encodeURIComponent(w.public_address)}" target="_blank" rel="noopener noreferrer">View on explorer ${ico(I.ext, 14)}</a>` : ''}</div></div></td></tr>`;
    return `<tr data-w="${w.id}" class="${open ? 'is-open' : ''}">
      <td data-l="Asset"><span class="wr-asset">${logo({ symbol: w.asset, logo: w.logo }, 40)}<span><b>${esc(w.asset)}</b><small>${esc(w.asset_name)}${w.contract_address ? ' · token' : ''}</small></span></span></td>
      <td data-l="Network"><b>${esc(w.network_name)}</b><small>${esc(w.network)}${w.is_testnet ? ' · testnet' : ''}</small></td>
      <td data-l="Wallet address">${w.configured ? `<span class="wr-addrline" title="${esc(w.public_address)}">${mono(short(w.public_address, 10, 6))}${copyBtn(w.public_address)}</span><button type="button" class="wr-link" data-view="${w.id}" aria-expanded="${!!open}" aria-controls="wd-${w.id}">${open ? 'Hide' : 'View full address'}</button>` : '<b>NOT_CONFIGURED</b><small>Set the address to accept deposits</small>'}</td>
      <td data-l="Balance" class="wr-bal">${balCell(w)}</td>
      <td data-l="Status">${chip(w.status)}${w.configured ? `<small>${w.is_deposit_enabled ? 'Deposits on' : 'Deposits off'}</small>` : ''}</td>
      <td data-l="Min / confirmations"><b>${esc(w.minimum_deposit)}</b><small>${esc(w.required_confirmations)} conf.</small></td>
      <td data-l="Actions" class="wr-act"><a class="btn ghost sm" href="${href('general')}">Settings</a>${manage ? `<a class="btn sm" href="${href('wallet', '&change=1')}">${w.configured ? 'Change address' : 'Set address'}</a>` : ''}
        <details class="wr-more"><summary aria-label="More actions for ${esc(w.asset)} ${esc(w.network)}">${ico(I.more, 18)}</summary><div class="wr-menu" role="menu">
          <button type="button" role="menuitem" data-view="${w.id}">View wallet details</button>${w.configured ? `<button type="button" role="menuitem" data-copy="${esc(w.public_address)}">Copy address</button>` : ''}
          <a role="menuitem" href="${href('network')}">Network settings</a><a role="menuitem" href="${href('wallet', '&section=history')}">Wallet history</a>
          ${w.explorer_url && w.configured ? `<a role="menuitem" href="${esc(w.explorer_url)}/address/${encodeURIComponent(w.public_address)}" target="_blank" rel="noopener noreferrer">View on explorer</a>` : ''}</div></details></td></tr>${open ? full : ''}`;
  }

  /* pending address changes, reviewed inline (approve needs a SECOND administrator + fresh 2FA; the server refuses self-approval) */
  function pendingBlock(list) {
    if (!list.length) return `<div class="wr-pend"><span>${ico(I.info, 18)}</span><div><b>Pending address changes</b><small>No wallet address changes are awaiting confirmation.</small></div></div>`;
    return `<div class="wr-pend has" id="wr-pend"><span>${ico(I.info, 18)}</span><div><b>Pending address changes</b><small>There ${list.length === 1 ? 'is 1 wallet address change' : `are ${list.length} wallet address changes`} awaiting confirmation.</small></div>
      <button type="button" class="btn ghost sm" id="wr-pend-t" aria-expanded="false" aria-controls="wr-pend-list">View pending changes ${ico(I.chev, 14)}</button></div><div id="wr-pend-list" class="wr-pend-list" hidden>${pendingTable(list)}</div>`;
  }
  function pendingTable(list) {
    return `<div class="wr-scroll"><table class="wr-tbl wr-ptbl"><thead><tr><th>Asset</th><th>Network</th><th>Current address</th><th>Proposed address</th><th>Requested by</th><th>Requested</th><th>Status</th><th>Review</th></tr></thead><tbody>${list.map(r => `<tr data-req="${esc(r.ref)}">
      <td data-l="Asset"><b>${esc(r.ledger_code.split('.')[0])}</b><small>${esc(r.ref)}</small></td><td data-l="Network">${esc(r.ledger_code.includes('.') ? r.ledger_code.split('.')[1] : (r.network_name || 'Native'))}</td>
      <td data-l="Current">${r.old_address ? `<span class="wr-addrline" title="${esc(r.old_address)}">${mono(short(r.old_address, 10, 6))}${copyBtn(r.old_address, 'current address')}</span>` : '<small>none</small>'}</td>
      <td data-l="Proposed"><span class="wr-addrline" title="${esc(r.new_address)}">${mono(short(r.new_address, 10, 6))}${copyBtn(r.new_address, 'proposed address')}</span><small>${esc(r.new_label)}</small></td>
      <td data-l="Requested by">${esc(r.requested_by_email || r.requested_by)}<small>${esc(r.reason)}</small></td><td data-l="Requested">${dt(r.requested_at)}<small>expires ${dt(r.expires_at)}</small></td>
      <td data-l="Status">${chip(r.status === 'pending' ? 'maintenance' : r.status === 'approved' ? 'active' : 'retired', label(r.status))}<small>Awaiting second administrator</small></td>
      <td data-l="Review" class="wr-act">${r.status === 'pending' ? `${can('crypto.wallets.approve') ? `<button class="btn sm" type="button" data-apv="${esc(r.ref)}">Approve</button>` : ''}${can('crypto.wallets.manage') ? `<button class="btn ghost sm" type="button" data-rej="${esc(r.ref)}">Reject</button>` : ''}` : ''}</td></tr>`).join('')}</tbody></table></div>`;
  }
  /* shared by this page and the Wallet Configuration tab */
  function bindPending(root, done) {
    root.addEventListener('click', e => {
      const a = e.target.closest('[data-apv]'), r = e.target.closest('[data-rej]'); if (!a && !r) return;
      const tr = e.target.closest('tr'), ref = (a || r).dataset.apv || (a || r).dataset.rej, host = tr.closest('.wr-scroll').parentElement;
      if (a) inlineForm(host, { title: `Approve change ${ref}`, intro: 'Compare the proposed address with your custody console before approving. You cannot approve your own request. The new wallet is created <b>inactive</b>; customers keep seeing the current address until you enable it in wallet settings.',
        fields: [{ n: 'note', l: 'Approval note', type: 'textarea' }, { n: 'code', l: 'Your 2FA code', req: 1, code: 1 }], ok: 'Approve change',
        submit: async o => { await V.post(`/api/admin/crypto/wallets/change-requests/${encodeURIComponent(ref)}/approve`, o); toast('Address changed. The new wallet is inactive until enabled.'); done(); } });
      else inlineForm(host, { title: `Reject change ${ref}`, intro: 'The pending change is cancelled and the current address stays active.', fields: [{ n: 'note', l: 'Reason', type: 'textarea', req: 1 }], ok: 'Reject request', danger: 1,
        submit: async o => { await V.post(`/api/admin/crypto/wallets/change-requests/${encodeURIComponent(ref)}/reject`, o); toast('Request rejected'); done(); } });
    });
  }
  Object.assign(window.VW, { pendingTable, bindPending });

  function aside(sm, tabLink) {
    const recent = sm.recent.map(r => `<li><span class="wr-asset">${logo({ symbol: r.asset, logo: LOGOS[r.asset] }, 34)}<span><b>${esc(r.asset || '?')}</b><small>${esc(r.network_name)}</small><small>${dt(r.timestamp)}</small></span></span>${rchip(r.status)}</li>`).join('');
    return `<aside class="wr-aside box" aria-label="Reconciliation summary"><h2>Reconciliation</h2><p class="note">Match on-chain transactions with internal records.</p>
      <div class="wr-big"><span class="wr-cic">${ico(I.refresh, 20)}</span><div><small>Total reconciliations</small><b>${sm.total_records}</b><em>${sm.reconciled} reconciled successfully</em></div></div>
      <div class="wr-mini"><div><b>${sm.reconciled}</b><small>Reconciled</small></div><div><b>${sm.pending}</b><small>Pending</small></div><div class="${sm.problems ? 'bad' : ''}"><b>${sm.problems}</b><small>Mismatch / failed</small></div></div>
      <h3>Recent activity</h3>${sm.recent.length ? `<ul class="wr-recent">${recent}</ul>` : '<p class="note">No deposits or withdrawals have been recorded yet.</p>'}
      <a class="wr-all" href="${tabLink}">View all reconciliations ${ico(I.chev, 16)}</a></aside>`;
  }
  let LOGOS = {};

  async function wallets(v) {
    const [o, sm] = await Promise.all([V.get('/api/admin/crypto/wallets/overview'), can('crypto.recon.read') ? V.get('/api/admin/crypto/reconciliation/summary') : Promise.resolve(null)]);
    LOGOS = Object.fromEntries(o.wallets.map(w => [w.asset, w.logo]));
    const nets = [...new Map(o.wallets.map(w => [w.network, w.network_name])).entries()], test = o.provider_mode !== 'live';
    const open = new Set();
    v.innerHTML = head('wallets') + `${test ? `<div class="banner"><b>${esc(o.environment.toUpperCase())} · ${esc(o.provider_mode)} providers.</b> Test infrastructure: balances come from the test chain and no real funds are involved.</div>` : ''}
      <div class="wr-grid"><div class="wr-main">${summaryCards(o)}
        <section class="box wr-list" aria-labelledby="wr-t"><div class="wr-bar"><h2 id="wr-t">Company Wallets</h2>
          <div class="wr-tools"><label class="wr-search">${ico(I.search, 16)}<input id="wr-q" type="search" placeholder="Search asset, symbol or address" aria-label="Search wallets by asset name, symbol or address" value="${esc(S.q)}"></label>
            <button type="button" class="wr-fbtn" id="wr-ft" aria-expanded="${!!(S.network || S.status)}" aria-controls="wr-f" aria-label="Filters">${ico(I.filter, 18)}</button></div></div>
          <div class="wr-filters" id="wr-f" ${S.network || S.status ? '' : 'hidden'}><label>Network<select id="wr-net"><option value="">All networks</option>${nets.map(([c, n]) => `<option value="${esc(c)}" ${S.network === c ? 'selected' : ''}>${esc(n)}</option>`).join('')}</select></label>
            <label>Status<select id="wr-st"><option value="">All statuses</option>${['active', 'inactive', 'maintenance', 'manual_review'].map(s => `<option value="${s}" ${S.status === s ? 'selected' : ''}>${STX[s]}</option>`).join('')}</select></label>
            <button type="button" class="btn ghost sm" id="wr-clr">Clear filters</button></div>
          <div id="wr-body"></div>${pendingBlock(o.pending_changes)}</section></div>
        ${sm ? aside(sm, tabHash('reconciliation')) : ''}</div>`;
    const paint = () => {
      const rows = filtered(o);
      $('#wr-body', v).innerHTML = rows.length ? `<div class="wr-scroll"><table class="wr-tbl"><thead><tr>${th('asset', 'Asset')}${th('network', 'Network')}<th scope="col">Wallet address</th>${th('balance', 'Balance')}${th('status', 'Status')}${th('min', 'Min / conf.')}<th scope="col" class="r">Actions</th></tr></thead><tbody>${rows.map(w => row(w, open.has(w.id))).join('')}</tbody></table></div><p class="wr-count" aria-live="polite">${rows.length} of ${o.wallets.length} wallet${o.wallets.length === 1 ? '' : 's'}</p>`
        : `<div class="empty wr-empty">${o.wallets.length ? 'No wallets match these filters.' : 'No wallets are configured yet. Run <code>python -m server.crypto_setup</code> to create the placeholders.'}</div>`;
    };
    paint();
    let tm; $('#wr-q', v).oninput = e => { clearTimeout(tm); tm = setTimeout(() => { S.q = e.target.value; persist(); paint(); }, 150); };
    $('#wr-net', v).onchange = e => { S.network = e.target.value; persist(); paint(); }; $('#wr-st', v).onchange = e => { S.status = e.target.value; persist(); paint(); };
    $('#wr-clr', v).onclick = () => { S.network = S.status = S.q = ''; persist(); $('#wr-q', v).value = ''; $('#wr-net', v).value = ''; $('#wr-st', v).value = ''; paint(); };
    $('#wr-ft', v).onclick = e => { const f = $('#wr-f', v); f.hidden = !f.hidden; e.currentTarget.setAttribute('aria-expanded', String(!f.hidden)); };
    const pt = $('#wr-pend-t', v); if (pt) pt.onclick = () => { const l = $('#wr-pend-list', v); l.hidden = !l.hidden; pt.setAttribute('aria-expanded', String(!l.hidden)); };
    v.addEventListener('click', e => {
      const cp = e.target.closest('[data-copy]'); if (cp) { copyText(cp.dataset.copy, cp); const d = cp.closest('details'); if (d) d.open = false; return; }
      const so = e.target.closest('[data-sort]'); if (so) { const k = so.dataset.sort; S.dir = S.sort === k ? -S.dir : 1; S.sort = k; persist(); paint(); return; }
      const vw = e.target.closest('[data-view]'); if (vw) { const id = +vw.dataset.view; open.has(id) ? open.delete(id) : open.add(id); paint(); const d = vw.closest('details'); if (d) d.open = false; }
    });
    bindPending(v, () => R['settings-crypto']());
  }

  /* ------------------------------------------------------------ Reconciliation tab ------------------------------------------------------------ */
  async function recon(v) {
    const qs = new URLSearchParams({ page: RQ.page, size: 20 }); for (const k of ['asset', 'network', 'type', 'status', 'from', 'to', 'q']) if (RQ[k]) qs.set(k, RQ[k]);
    const [j, run] = await Promise.all([V.get('/api/admin/crypto/reconciliation/records?' + qs), V.get('/api/admin/crypto/reconciliation')]);
    const last = run.runs[0], c = j.counts, tot = Object.values(c).reduce((a, b) => a + b, 0), manage = can('crypto.recon.manage');
    const chips = [['', 'All', tot], ['pending', 'Pending', c.pending], ['confirming', 'Confirming', c.confirming], ['reconciled', 'Reconciled', c.reconciled], ['mismatch', 'Mismatch', c.mismatch], ['failed', 'Failed', c.failed]];
    const pages = Math.max(1, Math.ceil(j.total / j.size));
    const openEx = run.exceptions.filter(e => ['open', 'investigating'].includes(e.status));
    const lastAssets = last ? last.summary.assets || [] : [];
    v.innerHTML = head('reconciliation') + `<div class="wr-runbar box"><div><b>Latest run</b> ${last ? `${chip(last.status === 'clean' ? 'active' : last.status === 'error' ? 'retired' : 'maintenance', label(last.status))} <small>${esc(last.ref)} · ${dt(last.started_at)}</small>` : '<small>No reconciliation has been run yet.</small>'}</div>
        <div class="wr-runact">${openEx.length ? `<a href="#" class="wr-link" id="wr-ex-j">${openEx.length} open exception${openEx.length === 1 ? '' : 's'}</a>` : '<small>No open exceptions</small>'}${manage ? '<button class="btn" id="wr-run" type="button">Run reconciliation now</button>' : ''}</div></div>
      <p class="note wr-note">Status is derived from the deposit, withdrawal, ledger and exception records. On-chain amounts are shown only where the blockchain provider reported them; otherwise they are marked unavailable. Nothing is corrected automatically and amounts cannot be edited here.</p>
      <section class="box wr-list"><div class="wr-filters open"><label class="grow">Search<input id="rq-q" type="search" placeholder="Transaction hash, address or reference" value="${esc(RQ.q)}"></label>
        <label>Asset<select id="rq-a"><option value="">All</option>${j.assets.map(a => `<option ${RQ.asset === a ? 'selected' : ''}>${esc(a)}</option>`).join('')}</select></label>
        <label>Network<select id="rq-n"><option value="">All</option>${j.networks.map(a => `<option ${RQ.network === a ? 'selected' : ''}>${esc(a)}</option>`).join('')}</select></label>
        <label>Type<select id="rq-t"><option value="">All</option><option value="deposit" ${RQ.type === 'deposit' ? 'selected' : ''}>Deposit</option><option value="withdrawal" ${RQ.type === 'withdrawal' ? 'selected' : ''}>Withdrawal</option></select></label>
        <label>From<input id="rq-f" type="date" value="${esc(RQ.from)}"></label><label>To<input id="rq-to" type="date" value="${esc(RQ.to)}"></label>
        <button class="btn ghost sm" id="rq-clr" type="button">Clear</button></div>
        <div class="wr-chips" role="group" aria-label="Status">${chips.map(([k, t, n]) => `<button type="button" class="chip ${RQ.status === k ? 'on' : ''}" data-rs="${k}" aria-pressed="${RQ.status === k}">${t}<b>${n}</b></button>`).join('')}</div>
        ${j.records.length ? `<div class="wr-scroll"><table class="wr-tbl wr-rtbl"><thead><tr><th>Transaction hash</th><th>Asset / network</th><th>Type</th><th>Wallet address</th><th class="r">Blockchain amount</th><th class="r">Ledger amount</th><th>Confirmations</th><th>Time</th><th>Status</th><th>Actions</th></tr></thead><tbody>${j.records.map(r => `<tr data-rid="${r.id}">
          <td data-l="Transaction hash">${r.tx_hash ? `<span class="wr-addrline" title="${esc(r.tx_hash)}">${mono(short(r.tx_hash, 10, 6))}${copyBtn(r.tx_hash, 'transaction hash')}</span>` : '<small>Not yet broadcast</small>'}<small>${esc(r.ref)}</small></td>
          <td data-l="Asset / network"><span class="wr-asset">${logo({ symbol: r.asset || '?', logo: LOGOS[r.asset] }, 28)}<span><b>${esc(r.asset || 'Unknown')}</b><small>${esc(r.network_name)}</small></span></span></td><td data-l="Type">${esc(label(r.type))}</td>
          <td data-l="Wallet address"><span class="wr-addrline" title="${esc(r.address)}">${mono(short(r.address, 8, 5))}</span></td>
          <td data-l="Blockchain amount" class="r">${r.chain_amount == null ? '<span class="wr-na" title="The chain provider did not report this transfer">Unavailable</span>' : `<b>${esc(r.chain_amount)}</b>`}</td>
          <td data-l="Ledger amount" class="r">${r.ledger_amount == null ? '<span class="wr-na">Not posted</span>' : `<b>${esc(r.ledger_amount)}</b>`}</td>
          <td data-l="Confirmations">${esc(r.confirmations)} / ${r.required_confirmations == null ? '—' : esc(r.required_confirmations)}</td><td data-l="Time">${dt(r.timestamp)}</td>
          <td data-l="Status">${rchip(r.status)}<small>${esc(label(r.source_status))}</small>${r.discrepancy ? `<small class="wr-bad">${esc(r.discrepancy)}</small>` : ''}</td>
          <td data-l="Actions" class="wr-act">${r.exceptions.length && manage ? `<button class="btn sm" type="button" data-ex="${r.exceptions[0].id}">Resolve exception</button>` : r.exceptions.length ? '<small>Exception open</small>' : '<small>No action needed</small>'}</td></tr>`).join('')}</tbody></table></div>
          <div class="pager"><span>${j.total} record${j.total === 1 ? '' : 's'}</span><div><button class="btn ghost sm" data-pg="${j.page - 1}" ${j.page <= 1 ? 'disabled' : ''}>Previous</button> <span>Page ${j.page} of ${pages}</span> <button class="btn ghost sm" data-pg="${j.page + 1}" ${j.page >= pages ? 'disabled' : ''}>Next</button></div></div>`
        : `<div class="empty wr-empty">${tot ? 'No records match these filters.' : 'No deposits or withdrawals have been recorded yet, so there is nothing to reconcile.'}</div>`}</section>
      ${lastAssets.length ? `<section class="box wr-list" style="margin-top:16px"><h2 class="wr-h2">Latest run by asset</h2><div class="wr-scroll"><table class="wr-tbl"><thead><tr><th>Asset</th><th class="r">On-chain holdings</th><th class="r">Ledger custody</th><th class="r">Customer liabilities</th><th class="r">Unattributed</th><th>Holdings ≥ owed</th><th>Matches ledger</th></tr></thead><tbody>${lastAssets.map(a => `<tr><td data-l="Asset"><b>${esc(a.ledger_code)}</b></td><td data-l="On-chain" class="r">${esc(a.chain_holdings)}</td><td data-l="Ledger" class="r">${esc(a.ledger_custody)}</td><td data-l="Liabilities" class="r">${esc(a.customer_liabilities)}</td><td data-l="Unattributed" class="r">${esc(a.unattributed_suspense)}</td><td data-l="Solvent">${a.solvent ? chip('active', 'Yes') : chip('retired', 'No')}</td><td data-l="Matches">${a.ledger_matches_chain ? 'Yes' : '<b>No</b>'}</td></tr>`).join('')}</tbody></table></div><small>Amounts in the asset's smallest unit.</small></section>` : ''}
      <section class="box wr-list" id="wr-exc" style="margin-top:16px"><h2 class="wr-h2">Exceptions</h2>${run.exceptions.length ? `<div class="wr-scroll"><table class="wr-tbl"><thead><tr><th>Exception</th><th>Severity</th><th>Details</th><th>Status</th><th></th></tr></thead><tbody>${run.exceptions.map(e => `<tr><td data-l="Exception"><b>${esc(label(e.kind))}</b><small>${esc(e.ledger_code || '')} · ${dt(e.created_at)}</small></td><td data-l="Severity">${chip(e.severity === 'critical' ? 'retired' : e.severity === 'high' ? 'maintenance' : 'inactive', label(e.severity))}</td><td data-l="Details"><small class="wr-json">${esc(JSON.stringify(e.details))}</small></td><td data-l="Status">${chip(e.status === 'resolved' ? 'active' : e.status === 'waived' ? 'inactive' : 'maintenance', label(e.status))}${e.resolution_note ? `<small>${esc(e.resolution_note)}</small>` : ''}</td><td class="wr-act">${manage && ['open', 'investigating'].includes(e.status) ? `<button class="btn ghost sm" data-ex="${e.id}">Update</button>` : ''}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty wr-empty">No exceptions.</div>'}</section>`;
    const go = () => { persist(); R['settings-crypto'](); }, set = (k, val) => { RQ[k] = val; RQ.page = 1; go(); };
    let tm; $('#rq-q', v).oninput = e => { clearTimeout(tm); tm = setTimeout(() => { RQ.q = e.target.value.trim(); RQ.page = 1; go(); }, 450); };
    [['rq-a', 'asset'], ['rq-n', 'network'], ['rq-t', 'type'], ['rq-f', 'from'], ['rq-to', 'to']].forEach(([id, k]) => { $('#' + id, v).onchange = e => set(k, e.target.value); });
    $('#rq-clr', v).onclick = () => { Object.assign(RQ, { asset: '', network: '', type: '', status: '', from: '', to: '', q: '', page: 1 }); go(); };
    const jx = $('#wr-ex-j', v); if (jx) jx.onclick = e => { e.preventDefault(); $('#wr-exc', v).scrollIntoView({ behavior: 'smooth' }); };
    const rr = $('#wr-run', v); if (rr) rr.onclick = () => UI.run(rr, async () => { const r = await V.post('/api/admin/crypto/reconciliation/run'); toast(`Reconciliation ${label(r.status)}: ${r.open_exceptions} open exception(s)`); R['settings-crypto'](); });
    v.addEventListener('click', e => {
      const cp = e.target.closest('[data-copy]'); if (cp) { copyText(cp.dataset.copy, cp); return; }
      const rs = e.target.closest('[data-rs]'); if (rs) { set('status', rs.dataset.rs); return; }
      const pg = e.target.closest('[data-pg]'); if (pg) { RQ.page = +pg.dataset.pg; go(); return; }
      const ex = e.target.closest('[data-ex]'); if (ex) { const tr = ex.closest('tr'), host = tr.closest('.wr-list');
        inlineForm(host, { title: 'Resolve reconciliation exception', intro: 'Critical exceptions cannot be waived. Describe the investigation or the fix: this is an audited manual reconciliation step, and no amount is changed.',
          fields: [{ n: 'status', l: 'Status', type: 'select', v: 'investigating', o: [['investigating', 'Investigating'], ['resolved', 'Resolved'], ['waived', 'Waived (non-critical only)']] }, { n: 'note', l: 'Note (min 10 characters)', type: 'textarea', req: 1 }], ok: 'Save update',
          submit: async o => { await V.post(`/api/admin/crypto/reconciliation/exceptions/${ex.dataset.ex}/resolve`, o); toast('Exception updated'); R['settings-crypto'](); } }); }
    });
  }

  R['settings-crypto'] = () => load(async v => {
    const tab = prm().get('tab') === 'reconciliation' ? 'reconciliation' : 'wallets';
    if (tab === 'reconciliation' && !can('crypto.recon.read')) { v.innerHTML = '<div class="box empty">You do not have permission to view reconciliation.</div>'; return; }
    if (tab === 'wallets' && !can('crypto.wallets.read')) { location.replace('#/settings-crypto?tab=reconciliation'); return; }
    return tab === 'wallets' ? wallets(v) : recon(v);
  });
});
