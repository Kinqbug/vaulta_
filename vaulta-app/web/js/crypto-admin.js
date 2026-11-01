/* VAULTA admin. Funding is cryptocurrency-only, so the existing Deposits and Withdrawals pages ARE the crypto pages (review, attribution, approval, signing).
   Wallet configuration, source-address verification and reconciliation live under Settings > Crypto. Registered through window.VAULTA_ADMIN_EXT.
   Hiding a tab is cosmetic: every action is re-checked server-side (require_perm, 2FA, separation of duties). No private key field exists anywhere. */
(window.VAULTA_ADMIN_EXT = window.VAULTA_ADMIN_EXT || []).push(({ R, load, V, UI, can, kpi, NAV }) => {
  const { $, $$, esc, dt, label, toast, table, modal, formData, logo } = UI;
  const nav = k => NAV.find(n => n[0] === k);
  if (nav('deposits')) nav('deposits')[3] = 'crypto.deposits.read';
  if (nav('withdrawals')) nav('withdrawals')[3] = 'crypto.withdrawals.read';
  // 'settings-crypto' is now a normal, permission-gated registry page (below), no longer a hidden route that any staff member could open
  NAV.push(['settings-crypto', 'Wallets & Reconciliation', 'crypto', ['crypto.wallets.read', 'crypto.recon.read', 'crypto.deposits.read']]);   // listed under Crypto Operations; same route as before
  (window.VAULTA_SETTINGS_LINKS = window.VAULTA_SETTINGS_LINKS || []).push({ k: 'settings-crypto', perms: ['crypto.wallets.read', 'crypto.recon.read', 'crypto.deposits.read'], title: 'Crypto', text: 'Company wallets, source-address verification and reconciliation' });
  const ST = { active: 'completed', inactive: 'draft', maintenance: 'pending', manual_review: 'under_review', retired: 'rejected', credited: 'completed', rejected: 'rejected', failed: 'failed', pending_approval: 'pending',
    approved: 'approved', broadcast: 'processing', signing: 'processing', completed: 'completed', open: 'under_review', resolved: 'completed', waived: 'draft', investigating: 'pending', clean: 'completed', exceptions: 'failed', pending: 'pending' };
  const sb = s => `<span class="st st-${ST[s] || 'pending'}">${esc(label(s))}</span>`;
  const mono = t => `<code class="addr-s" style="word-break:break-all">${esc(t)}</code>`;
  let TAB = 'wallets', CUR = 'settings-crypto';

  /* generic form dialog: the dialog stays open and shows the server's message if `submit` fails */
  function form(title, intro, fields, ok, submit) {
    const inp = x => x.type === 'select' ? `<label class="f">${esc(x.l)}<select name="${x.n}">${x.o.map(o => `<option value="${esc(o[0])}" ${o[0] === x.v ? 'selected' : ''}>${esc(o[1])}</option>`).join('')}</select></label>`
      : x.type === 'checkbox' ? `<label class="f" style="flex-direction:row;gap:8px"><input type="checkbox" name="${x.n}" ${x.v ? 'checked' : ''}> ${esc(x.l)}</label>`
      : `<label class="f">${esc(x.l)}${x.type === 'textarea' ? `<textarea name="${x.n}" rows="${x.rows || 3}" maxlength="${x.max || 500}" ${x.req ? 'required' : ''}>${esc(x.v || '')}</textarea>`
        : `<input name="${x.n}" value="${esc(x.v || '')}" ${x.req ? 'required' : ''} ${x.code ? 'inputmode="numeric" maxlength="6" autocomplete="one-time-code"' : 'autocomplete="off"'} ${x.mono ? 'spellcheck="false"' : ''}>`}</label>`;
    const d = modal(title, `<p class="note">${intro}</p><form id="askf">${fields.map(inp).join('')}<p class="neg" id="askm" role="alert"></p><div class="actions"><button type="button" class="btn ghost" data-x>Cancel</button><button class="btn">${esc(ok)}</button></div></form>`);
    $('#askf', d).onsubmit = async e => {
      e.preventDefault(); const o = {}; fields.forEach(x => { const el = e.target.elements[x.n]; o[x.n] = x.type === 'checkbox' ? el.checked : el.value.trim(); });
      const b = $('button:not([data-x])', e.target); b.disabled = true;
      try { await submit(o); d.close(); } catch (err) { $('#askm', d).textContent = err.message; b.disabled = false; }
    };
    return d;
  }
  const done = msg => { toast(msg); R[CUR](); };
  const tabs = () => [['wallets', 'Company wallets', can('crypto.wallets.read')], ['addresses', 'Source addresses', can('crypto.deposits.read')], ['recon', 'Reconciliation', can('crypto.recon.read')]].filter(t => t[2]);
  /* settings pages get a tab bar; the Deposits and Withdrawals pages are plain pages */
  const frame = (v, body, title, withTabs) => {
    if (!withTabs) { v.innerHTML = `<div class="top"><h1>${esc(title)}</h1></div><div id="ctab">${body}</div>`; v.onclick = null; return; }
    const ts = tabs(); if (!ts.some(t => t[0] === TAB)) TAB = ts[0][0];
    v.innerHTML = `<div class="top"><h1>Settings · Crypto</h1></div><div class="tabs" role="tablist">${ts.map(t => `<button role="tab" class="btn sm ${t[0] === TAB ? 'tab-on' : 'ghost'}" data-tab="${t[0]}" aria-selected="${t[0] === TAB}">${esc(t[1])}</button>`).join(' ')}</div><div id="ctab" style="margin-top:14px">${body}</div>`;
    v.onclick = e => { const t = e.target.closest('[data-tab]'); if (t) { const k = t.dataset.tab; location.hash = k === 'addresses' ? '#/crypto-sources' : '#/settings-crypto?tab=' + (k === 'recon' ? 'reconciliation' : 'wallets'); } };
  };

  /* ---------------- company wallets ---------------- */
  async function wallets(v) {
    const [w, rq, h] = await Promise.all([V.get('/api/admin/crypto/wallets'), V.get('/api/admin/crypto/wallets/change-requests?status=pending'), V.get('/api/admin/crypto/wallets/history')]);
    const test = w.provider_mode !== 'live';
    const body = `${test ? `<div class="banner"><b>${esc(w.environment.toUpperCase())} · ${esc(w.provider_mode)} providers.</b> Test infrastructure: no real funds.</div>` : ''}
      <p class="note">Company wallets hold <b>public deposit addresses only</b>. Private keys, seed phrases and signing credentials never enter Vaulta: signing is done by the external custody system. Changing an address needs two administrators, each with a fresh 2FA code.</p>
      <section class="box"><h3>Company wallets</h3>${table([
        { h: 'Asset', r: x => `<b>${esc(x.asset)}</b><small>${esc(x.asset_name)}${x.contract_address ? ' · token' : ''}</small>` },
        { h: 'Network', r: x => `${esc(x.network_name)}${x.contract_address ? `<small>${mono(x.contract_address)}</small>` : ''}` },
        { h: 'Wallet', r: x => `${esc(x.label)}<small>${x.configured ? mono(x.public_address) : '<b>NOT_CONFIGURED</b>'}</small>` },
        { h: 'Status', r: x => sb(x.status) + `<small>${x.is_deposit_enabled ? 'deposits on' : 'deposits off'}</small>` },
        { h: 'Min / confirmations', r: x => `${esc(x.minimum_deposit)} <small>${esc(x.required_confirmations)} conf.</small>` },
        { h: '', r: x => can('crypto.wallets.manage') ? `<button class="btn ghost sm" data-set="${x.id}" type="button">Settings</button> <button class="btn sm" data-chg="${x.id}" type="button">${x.configured ? 'Change address' : 'Set address'}</button>` : '' }], w.wallets, { empty: 'No wallets configured. Run python -m server.crypto_setup.' })}</section>
      <section class="box" style="margin-top:16px"><h3>Pending address changes</h3>${table([{ h: 'Request', r: r => `${esc(r.ref)}<small>${dt(r.requested_at)}</small>` }, { h: 'Asset', r: r => esc(r.ledger_code) },
        { h: 'Old → new', r: r => `${r.old_address ? mono(r.old_address) : '<small>none</small>'}<br>→ ${mono(r.new_address)}` }, { h: 'Reason', r: r => esc(r.reason) }, { h: 'By', r: r => esc(r.requested_by) },
        { h: '', r: r => (can('crypto.wallets.approve') ? `<button class="btn sm" data-apv="${esc(r.ref)}" type="button">Approve</button> ` : '') + (can('crypto.wallets.manage') ? `<button class="btn ghost sm" data-rej="${esc(r.ref)}" type="button">Reject</button>` : '') }], rq.requests, { empty: 'No pending changes.' })}</section>
      <section class="box" style="margin-top:16px"><h3>Wallet configuration history</h3>${table([{ h: 'When', r: e => dt(e.created_at) }, { h: 'Event', r: e => esc(label(e.event_type)) }, { h: 'Asset', r: e => esc(e.ledger_code || '') },
        { h: 'Old address', r: e => (e.old_address ? mono(e.old_address) : '—') }, { h: 'New address', r: e => (e.new_address ? mono(e.new_address) : '—') }, { h: 'Admin / approver', r: e => `${esc(e.actor_id ?? 'system')} / ${esc(e.approver_id ?? '—')}` },
        { h: 'Reason', r: e => esc(e.reason || '') }, { h: 'IP', r: e => esc(e.ip || '') }], h.events, { empty: 'No history.' })}</section>`;
    frame(v, body, 'Settings · Crypto', true);
    const by = id => w.wallets.find(x => String(x.id) === String(id));
    $('#ctab').onclick = async e => {
      const chg = e.target.closest('[data-chg]'), set = e.target.closest('[data-set]'), apv = e.target.closest('[data-apv]'), rej = e.target.closest('[data-rej]');
      if (chg) { const x = by(chg.dataset.chg); const an = w.asset_networks.find(a => a.ledger_code === x.ledger_code);
        form(`${x.configured ? 'Change' : 'Set'} ${x.asset} deposit address`, `Network: <b>${esc(x.network_name)}</b>. The address is validated for this network${x.contract_address ? ' and token' : ''} before it is saved, and the custody system must confirm it controls it. A second administrator must approve.`,
          [{ n: 'address', l: `New ${x.asset} address`, req: 1, mono: 1 }, { n: 'label', l: 'Label', v: x.label, req: 1 }, { n: 'reason', l: 'Reason (min 10 characters)', type: 'textarea', req: 1 }, { n: 'code', l: 'Your 2FA code', req: 1, code: 1 }], 'Submit for approval',
          async o => { await V.post('/api/admin/crypto/wallets/change-requests', { asset_network_id: an.id, address: o.address, label: o.label, reason: o.reason, code: o.code }); done('Change submitted for approval'); }); }
      if (set) { const x = by(set.dataset.set);
        form(`${x.asset} wallet settings`, 'Policy changes are audited and require your 2FA code.', [{ n: 'minimum_deposit', l: 'Minimum deposit', v: x.minimum_deposit }, { n: 'confirmation_requirement', l: 'Required confirmations', v: String(x.required_confirmations) },
          { n: 'risk_review_threshold', l: 'Second-approval threshold (blank = none)', v: x.risk_review_threshold || '' },
          { n: 'status', l: 'Status', type: 'select', v: x.status, o: ['active', 'inactive', 'maintenance', 'manual_review'].map(s => [s, label(s)]) }, { n: 'is_deposit_enabled', l: 'Accept customer deposits', type: 'checkbox', v: x.is_deposit_enabled },
          { n: 'reason', l: 'Reason', type: 'textarea', req: 1 }, { n: 'code', l: 'Your 2FA code', req: 1, code: 1 }], 'Save',
          async o => { const { code, reason, ...c } = o; c.confirmation_requirement = +c.confirmation_requirement; await V.post(`/api/admin/crypto/wallets/${x.id}/settings`, { ...c, code, reason }); done('Settings saved'); }); }
      if (apv) { form('Approve wallet address change', 'You are approving a change to a company deposit address. Compare the new address with your custody console before approving. You cannot approve your own request.', [{ n: 'note', l: 'Approval note', type: 'textarea' }, { n: 'code', l: 'Your 2FA code', req: 1, code: 1 }], 'Approve change',
          async o => { await V.post(`/api/admin/crypto/wallets/change-requests/${encodeURIComponent(apv.dataset.apv)}/approve`, o); done('Address changed. The new wallet is inactive until you enable it in Settings.'); }); }
      if (rej) { form('Reject request', 'The pending change will be cancelled.', [{ n: 'note', l: 'Reason', type: 'textarea', req: 1 }], 'Reject', async o => { await V.post(`/api/admin/crypto/wallets/change-requests/${encodeURIComponent(rej.dataset.rej)}/reject`, o); done('Request rejected'); }); }
    };
  }

  /* ---------------- deposits: screenshot review queue (+ on-chain detections) ---------------- */
  let DTAB = 'requests'; const RQ = { status: 'pending', q: '', asset: '', page: 1 };
  const RCHIP = { pending: 'pending', approved: 'completed', rejected: 'rejected', cancelled: 'rejected' };
  const rchip = s => `<span class="st st-${RCHIP[s] || 'pending'}">${esc(label(s))}</span>`;
  const sub = () => `<div class="tabs" role="tablist" style="margin-bottom:12px">${[['requests', 'Deposit requests'], ['detections', 'On-chain detections']].map(t => `<button class="btn sm ${t[0] === DTAB ? 'tab-on' : 'ghost'}" data-dtab="${t[0]}" role="tab" aria-selected="${t[0] === DTAB}">${t[1]}</button>`).join(' ')}</div>`;
  const assetCell = x => `<span style="display:inline-flex;gap:10px;align-items:center">${logo(x, 30)}<span><b>${esc(x.asset)}</b><small>${esc(x.network)}</small></span></span>`;
  const pager = (j, go) => { const pages = Math.max(1, Math.ceil(j.total / j.size)); return `<div class="pager"><span>${j.total} result${j.total === 1 ? '' : 's'}</span><div><button class="btn ghost sm" data-pgn="${j.page - 1}" ${j.page <= 1 ? 'disabled' : ''}>Previous</button> <span>Page ${j.page} of ${pages}</span> <button class="btn ghost sm" data-pgn="${j.page + 1}" ${j.page >= pages ? 'disabled' : ''}>Next</button></div></div>`; };

  async function deposits(v) { return DTAB === 'detections' ? detections(v) : requests(v); }

  async function requests(v) {
    const qs = new URLSearchParams({ page: RQ.page, size: 20 }); if (RQ.status) qs.set('status', RQ.status); if (RQ.q) qs.set('q', RQ.q); if (RQ.asset) qs.set('asset', RQ.asset);
    const j = await V.get('/api/admin/crypto/deposit-requests?' + qs); const c = j.counts, tot = Object.values(c).reduce((a, b) => a + b, 0);
    const chips = [['pending', 'Pending'], ['approved', 'Approved'], ['rejected', 'Rejected'], ['cancelled', 'Cancelled'], ['', 'All']];
    frame(v, `${sub()}<div class="rqbar"><input id="rqq" type="search" placeholder="Search reference, email, customer ID, transaction hash or address" value="${esc(RQ.q)}" aria-label="Search deposit requests">
      <select id="rqa" aria-label="Filter by asset"><option value="">All assets</option>${j.assets.map(a => `<option ${a === RQ.asset ? 'selected' : ''}>${esc(a)}</option>`).join('')}</select></div>
      <div class="chips" role="tablist" aria-label="Status">${chips.map(([k, t]) => `<button class="chip ${RQ.status === k ? 'on' : ''}" data-rs="${k}">${t}<b>${k ? (c[k] || 0) : tot}</b></button>`).join('')}</div>
      <section class="box" style="margin-top:12px">${table([{ h: 'Deposit', r: d => `<b>${esc(d.ref)}</b><small>${dt(d.submitted_at)}</small>` }, { h: 'Customer', r: d => `${esc(d.user.email)}<small>${esc(d.user.public_id)} · KYC ${esc(label(d.user.kyc_status))}</small>` }, { h: 'Asset', r: assetCell },
        { h: 'Amount', cls: 'r', r: d => `<b>${esc(d.amount)}</b> ${esc(d.asset)}` }, { h: 'Transaction hash', r: d => (d.tx_hash ? mono(d.tx_hash.length > 22 ? d.tx_hash.slice(0, 12) + '…' + d.tx_hash.slice(-8) : d.tx_hash) : '<small>not supplied</small>') },
        { h: 'Evidence', r: d => (d.has_screenshot ? `<img class="thumb" loading="lazy" alt="Screenshot of ${esc(d.ref)}" src="/api/admin/crypto/deposit-requests/${d.id}/evidence">` : '<small>none</small>') }, { h: 'Status', r: d => rchip(d.status) },
        { h: '', r: d => `<button class="btn ${d.status === 'pending' ? '' : 'ghost'} sm" data-rv="${d.id}">${d.status === 'pending' && can('crypto.deposits.manage') ? 'Review' : 'View'}</button>` }], j.requests, { empty: RQ.status === 'pending' ? 'No deposits are waiting for review.' : 'No deposit requests match.' })}${pager(j)}</section>`, 'Deposits', false);
    const ct = $('#ctab'); let tm;
    $('#rqq').oninput = e => { clearTimeout(tm); tm = setTimeout(() => { RQ.q = e.target.value.trim(); RQ.page = 1; R.deposits(); }, 400); }; $('#rqa').onchange = e => { RQ.asset = e.target.value; RQ.page = 1; R.deposits(); };
    ct.onclick = e => { const t = e.target.closest('[data-dtab]'), s = e.target.closest('[data-rs]'), r = e.target.closest('[data-rv]'), g = e.target.closest('[data-pgn]');
      if (t) { DTAB = t.dataset.dtab; R.deposits(); } if (s) { RQ.status = s.dataset.rs; RQ.page = 1; R.deposits(); } if (g) { RQ.page = +g.dataset.pgn; R.deposits(); } if (r) review(r.dataset.rv); };
  }

  const VTXT = { verified: ['ok', 'Verified on-chain', 'The transfer exists, pays the Vaulta address in this asset and network, and has the required confirmations.'], unconfirmed: ['warn', 'Not final yet', ''], not_found: ['warn', 'Not found on the network', ''],
    mismatch: ['bad', 'Does not match', ''], unavailable: ['warn', 'Cannot verify automatically', ''], no_txid: ['warn', 'No transaction hash supplied', ''], already_credited: ['bad', 'Already credited', ''] };

  async function review(id) {
    const d = await V.get(`/api/admin/crypto/deposit-requests/${id}`), q = d.request, c = d.chain_check, ev = `/api/admin/crypto/deposit-requests/${q.id}/evidence`;
    const open = q.status === 'pending' && can('crypto.deposits.manage'), manual = ['unavailable', 'no_txid'].includes(c.status), vt = VTXT[c.status] || ['warn', label(c.status), ''];
    const dlg = modal(`Deposit ${q.ref}`, `<div class="rv"><div>
      ${q.screenshot ? `<div class="shot"><img id="shotimg" src="${ev}" alt="Customer's transaction screenshot" title="Click to enlarge"></div>
        <div class="actions" style="justify-content:flex-start;margin-top:10px"><button type="button" class="btn ghost sm" id="zoom">Enlarge</button><a class="btn ghost sm" target="_blank" rel="noopener" href="${ev}">Open in new tab</a><a class="btn ghost sm" href="${ev}?download=1">Download</a></div>
        <small>${esc(q.screenshot.mime)} · ${Math.round(q.screenshot.size / 1024)} KB · SHA-256 ${esc((q.screenshot.sha256 || '').slice(0, 16))}…</small>` : '<div class="empty">No screenshot was attached.</div>'}
      <p class="note" style="margin-top:10px">A screenshot is supporting evidence only. It is not proof of a confirmed transfer.</p>
      ${q.evidence_duplicate_of ? `<p class="note warn"><b>Same image</b> was used on request ${esc(q.evidence_duplicate_of)}.</p>` : ''}${d.related_requests.length ? `<p class="note warn"><b>Related requests</b> share this screenshot or transaction hash: ${d.related_requests.map(esc).join(', ')}</p>` : ''}</div>
    <div><dl class="dl2"><dt>Status</dt><dd>${rchip(q.status)}</dd><dt>Customer</dt><dd>${esc(q.user.name || '—')}<br>${esc(q.user.email)}<br><small>${esc(q.user.public_id)} · KYC ${esc(label(q.user.kyc_status))} · account ${esc(q.user.status)}</small></dd>
      <dt>Asset</dt><dd style="display:flex;gap:8px;align-items:center">${logo(q, 22)} ${esc(q.asset_name)} (${esc(q.asset)})</dd><dt>Network</dt><dd>${esc(q.network)}</dd><dt>Claimed amount</dt><dd><b>${esc(q.amount)} ${esc(q.asset)}</b></dd>
      <dt>Transaction hash</dt><dd>${q.tx_hash ? mono(q.tx_hash) : '<small>not supplied</small>'}</dd><dt>Sender address</dt><dd>${q.sender_address ? mono(q.sender_address) : '—'}</dd>
      <dt>Expected receiving address</dt><dd>${mono(q.destination_address)}${c.current_wallet_address && c.current_wallet_address !== q.destination_address ? '<br><small class="neg">The configured wallet has since changed.</small>' : '<br><small>Matches the configured Vaulta wallet.</small>'}</dd>
      <dt>Customer note</dt><dd>${esc(q.user_note || '—')}</dd><dt>Submitted</dt><dd>${dt(q.submitted_at)}</dd></dl>
      <div class="vbox ${vt[0]}"><b>Blockchain verification: ${esc(vt[1])}</b><p>${esc(vt[2] || (c.reasons || []).join(' '))}</p>${c.confirmations !== undefined ? `<small>On-chain amount ${esc(c.amount_display || '—')} ${esc(q.asset)}${c.amount_matches_claim === false ? ' <b class="neg">(differs from the claim)</b>' : ''} · ${esc(c.confirmations)} / ${esc(c.required_confirmations)} confirmations${c.sender ? ' · from ' + esc(String(c.sender).slice(0, 14)) + '…' : ''}</small>` : ''}</div>
      ${open ? `<div id="dec"><label class="f">Admin notes<textarea id="anote" rows="3" maxlength="500" placeholder="What did you check? (required when approving a mismatch or without chain verification)"></textarea></label>
        ${manual ? `<div class="vbox warn"><label style="display:flex;gap:8px;align-items:flex-start"><input type="checkbox" id="att"> <span>I verified this transfer independently (for example on a block explorer or the sending platform's record). This is recorded in the audit log under my name.</span></label>
          <label class="f">Verified amount (${esc(q.asset)})<input id="vamt" value="${esc(q.amount)}" inputmode="decimal" autocomplete="off"></label></div>` : ''}
        <div id="rejbox" hidden><label class="f">Reason for rejection <small>(shown to the customer)</small><textarea id="rreason" rows="2" maxlength="500"></textarea></label></div>
        <p class="neg" id="rvm" role="alert"></p><div class="actions" style="justify-content:flex-start"><button type="button" class="btn ok" id="apv">Approve deposit</button><button type="button" class="btn danger" id="rej">Reject deposit</button></div>
        <small>Approval credits the customer's balance once. It cannot be undone, only corrected by an audited adjustment.</small></div>`
        : `<div class="vbox ${q.status === 'approved' ? 'ok' : 'warn'}"><b>Decision: ${esc(label(q.status))}</b><p>${q.reviewed_at ? 'By administrator ' + esc(q.reviewed_by) + ' · ' + dt(q.reviewed_at) : ''}</p>${q.rejection_reason ? `<p><b>Reason shown to customer:</b> ${esc(q.rejection_reason)}</p>` : ''}${q.admin_notes ? `<p><b>Notes:</b> ${esc(q.admin_notes)}</p>` : ''}${q.verified_amount ? `<p>Credited ${esc(q.verified_amount)} ${esc(q.asset)} (${esc(label(q.verification_method))})${q.deposit_ref ? ' · ' + esc(q.deposit_ref) : ''}</p>` : ''}</div>`}
      <details><summary><small>History</small></summary>${d.events.map(e => `<div><small>${dt(e.created_at)} · ${esc(label(e.event))}${e.note ? ' · ' + esc(e.note) : ''}</small></div>`).join('')}</details></div></div>`, { wide: true });
    const big = () => modal(`Screenshot · ${q.ref}`, `<img src="${ev}" alt="Customer's transaction screenshot (enlarged)" style="max-width:100%;display:block;margin:auto">`, { wide: true });
    const z = $('#zoom', dlg); if (z) { z.onclick = big; $('#shotimg', dlg).onclick = big; }
    if (!open) return;
    const msg = t => ($('#rvm', dlg).textContent = t), lock = on => $$('#dec button', dlg).forEach(b => (b.disabled = on));
    $('#apv', dlg).onclick = async () => { msg(''); lock(true); const body = { note: $('#anote', dlg).value.trim() };
      if (manual) { body.attest = $('#att', dlg).checked; body.verified_amount = $('#vamt', dlg).value.trim(); }
      try { await V.post(`/api/admin/crypto/deposit-requests/${q.id}/approve`, body); toast('Deposit approved and credited'); dlg.close(); R.deposits(); } catch (e) { msg(e.message); lock(false); } };
    $('#rej', dlg).onclick = async () => { const box = $('#rejbox', dlg); if (box.hidden) { box.hidden = false; $('#rej', dlg).textContent = 'Confirm rejection'; $('#rreason', dlg).focus(); return; }
      msg(''); lock(true); try { await V.post(`/api/admin/crypto/deposit-requests/${q.id}/reject`, { reason: $('#rreason', dlg).value.trim(), note: $('#anote', dlg).value.trim() }); toast('Deposit rejected'); dlg.close(); R.deposits(); } catch (e) { msg(e.message); lock(false); } };
  }

  /* on-chain detections found by the monitor (manual review, risk approval): same funding system, second tab */
  async function detections(v) {
    const st = sessionStorage.getItem('cdst') || ''; const j = await V.get('/api/admin/crypto/deposits' + (st ? '?status=' + st : ''));
    const stat = ['', 'manual_review', 'risk_review', 'confirming', 'credited', 'wrong_network', 'unsupported_asset', 'rejected', 'reorged', 'failed'];
    frame(v, `${sub()}<div class="top" style="margin-bottom:8px"><select id="cdf" aria-label="Filter by status">${stat.map(s => `<option value="${s}" ${s === st ? 'selected' : ''}>${s ? esc(label(s)) : 'All statuses'}</option>`).join('')}</select>
      ${can('crypto.deposits.manage') ? '<button class="btn sm" id="mon" type="button">Run monitor now</button>' : ''}</div>
      <p class="note">Transfers the blockchain monitor found on its own. Customer-submitted requests are on the other tab.</p>
      <section class="box">${table([{ h: 'Deposit', r: d => `${esc(d.ref)}<small>${dt(d.detected_at)}</small>` }, { h: 'Asset', r: d => `<b>${esc(d.asset)}</b> ${esc(d.amount ?? '')}<small>${esc(d.network)}</small>` },
        { h: 'Customer', r: d => (d.user_id ? esc(d.user_id) + `<small>${esc(label(d.attribution_method))}</small>` : '<b>unattributed</b>') }, { h: 'From / tx', r: d => `${d.sender ? mono(d.sender) : '—'}<small>${mono(d.tx_hash)}</small>` },
        { h: 'Status', r: d => sb(d.status) + `<small>${esc(d.status_reason || '')}</small><small>${esc(d.confirmations)}/${esc(d.required_confirmations ?? '?')} conf.${d.received_in_ledger ? ' · in custody ledger' : ''}</small>` },
        { h: '', r: d => !can('crypto.deposits.manage') ? '' : d.status === 'manual_review' ? `<button class="btn sm" data-a="attribute" data-id="${d.id}">Attribute</button> <button class="btn ghost sm" data-a="retry" data-id="${d.id}">Retry match</button> <button class="btn ghost sm" data-a="reject" data-id="${d.id}">Reject</button>${(d.status_reason || '').startsWith('below_minimum') ? ` <button class="btn ghost sm" data-a="accept_below_minimum" data-id="${d.id}">Accept below minimum</button>` : ''} <button class="btn ghost sm" data-a="mark_duplicate" data-id="${d.id}">Duplicate</button>`
          : d.status === 'risk_review' ? `<button class="btn sm" data-a="approve_risk" data-id="${d.id}">Approve</button> <button class="btn ghost sm" data-a="reject" data-id="${d.id}">Reject</button>` : '' }], j.deposits, { empty: 'No on-chain detections.' })}</section>`, 'Deposits', false);
    $('#cdf').onchange = e => { sessionStorage.setItem('cdst', e.target.value); R.deposits(); };
    const m = $('#mon'); if (m) m.onclick = () => UI.run(m, async () => { const r = await V.post('/api/admin/crypto/monitor/run'); done(`Monitor finished (${r.advanced} deposits advanced, ${r.errors.length} errors)`); });
    $('#ctab').onclick = e => { const t = e.target.closest('[data-dtab]'); if (t) { DTAB = t.dataset.dtab; return R.deposits(); } const b = e.target.closest('[data-a]'); if (!b) return; const a = b.dataset.a, id = b.dataset.id;
      const extra = a === 'attribute' ? [{ n: 'user', l: 'Customer public ID or email', req: 1 }] : a === 'mark_duplicate' ? [{ n: 'duplicate_of', l: 'Duplicate of deposit id', req: 1 }] : [];
      const warn = { attribute: 'Manual attribution still passes the normal pipeline, and a DIFFERENT administrator must approve before the customer is credited.', approve_risk: 'Approving releases the credit to the customer.', reject: 'The funds stay in custody (suspense) until recovered or returned.' }[a] || 'Recorded in the audit log.';
      form(label(a), warn, [...extra, { n: 'reason', l: 'Reason (evidence, min 10 characters)', type: 'textarea', req: 1 }], 'Confirm', async o => { await V.post(`/api/admin/crypto/deposits/${id}/resolve`, { action: a, reason: o.reason, user: o.user, duplicate_of: o.duplicate_of ? +o.duplicate_of : undefined }); done('Done'); }); };
  }

  async function addresses(v) {
    const j = await V.get('/api/admin/crypto/source-addresses?status=pending');
    frame(v, `<p class="note">Verify a customer's sending address only with real evidence it is their own wallet (not an exchange or shared address). A verified address is what lets deposits from it be credited to that customer automatically.</p>
      <section class="box">${table([{ h: 'Customer', r: a => `${esc(a.email)}<small>${esc(a.user_id)}</small>` }, { h: 'Network', r: a => esc(a.network_code) }, { h: 'Address', r: a => mono(a.address) }, { h: 'Added', r: a => dt(a.created_at) },
        { h: '', r: a => (can('crypto.deposits.manage') ? `<button class="btn sm" data-v="${a.id}">Verify</button>` : '') }], j.addresses, { empty: 'No addresses awaiting review.' })}</section>`, '', true);
    $('#ctab').onclick = e => { const b = e.target.closest('[data-v]'); if (b) form('Verify sending address', 'Record the evidence you reviewed.', [{ n: 'reason', l: 'Evidence / reason', type: 'textarea', req: 1 }], 'Verify', async o => { await V.post(`/api/admin/crypto/source-addresses/${b.dataset.v}/verify`, o); done('Address verified'); }); };
  }

  /* ---------------- withdrawals ---------------- */
  async function withdrawals(v) {
    const j = await V.get('/api/admin/crypto/withdrawals'); const manage = can('crypto.withdrawals.manage');
    const WS = { pending_approval: 'pending', approved: 'approved', signing: 'processing', broadcast: 'processing', confirming: 'processing', completed: 'completed', rejected: 'rejected', failed: 'failed', cancelled: 'rejected' };
    const wb = s => `<span class="st st-${WS[s] || 'pending'}">${esc(label(s))}</span>`;
    const acts = w => !manage ? '' : ({ pending_approval: `<button class="btn ok sm" data-w="approve" data-id="${w.id}">Approve</button> <button class="btn ghost sm" data-w="reject" data-id="${w.id}">Reject</button>`,
      approved: `<button class="btn sm" data-w="process" data-id="${w.id}">Send via custody</button> <button class="btn ghost sm" data-w="manual" data-id="${w.id}" data-sym="${esc(w.asset)}">Record manual send</button> <button class="btn ghost sm" data-w="reject" data-id="${w.id}">Reject</button>`,
      broadcast: `<button class="btn ghost sm" data-w="advance" data-id="${w.id}">Check chain</button>${w.processing_mode === 'manual' ? ` <button class="btn sm" data-w="complete" data-id="${w.id}">Mark completed</button>` : ''}`,
      confirming: `<button class="btn ghost sm" data-w="advance" data-id="${w.id}">Check chain</button>${w.processing_mode === 'manual' ? ` <button class="btn sm" data-w="complete" data-id="${w.id}">Mark completed</button>` : ''}`,
      signing: `<button class="btn ghost sm" data-w="fail" data-id="${w.id}">Mark never sent</button>` })[w.status] || '';
    frame(v, `<section class="box">${table([{ h: 'Withdrawal', r: w => `<b>${esc(w.ref)}</b><small>${dt(w.created_at)}</small>` }, { h: 'Customer', r: w => `${esc(w.user.email)}<small>${esc(w.user.public_id)} · KYC ${esc(label(w.user.kyc_status))}</small>` }, { h: 'Asset', r: assetCell },
      { h: 'Amount / fee', cls: 'r', r: w => `<b>${esc(w.amount)}</b> ${esc(w.asset)}<small>fee ${esc(w.fee)} · total ${esc(w.total_debit)}</small>` }, { h: 'Destination', r: w => mono(w.destination) },
      { h: 'Status', r: w => wb(w.status) + `<small>${esc(w.status_reason || '')}${w.processing_mode === 'manual' && w.tx_hash ? ' manual' : ''}</small>${w.tx_hash ? `<small>${mono(w.tx_hash.slice(0, 16) + '…')}</small>` : ''}${w.manual_attested ? '<small>completion attested by admin</small>' : ''}` }, { h: '', r: acts }], j.withdrawals, { empty: 'No withdrawals.' })}</section>`, 'Withdrawals', false);
    $('#ctab').onclick = async e => { const b = e.target.closest('[data-w]'); if (!b) return; const a = b.dataset.w, id = b.dataset.id, w = j.withdrawals.find(x => String(x.id) === id);
      if (a === 'reject' || a === 'fail') return form(label(a), a === 'fail' ? 'Only if you have verified on-chain that this transfer was NOT broadcast. Otherwise funds could be sent twice.' : 'The customer\'s reserved funds are released and they are notified.', [{ n: 'reason', l: 'Reason', type: 'textarea', req: 1 }], 'Confirm', async o => { await V.post(`/api/admin/crypto/withdrawals/${id}/${a}`, o); done('Done'); });
      if (a === 'manual') return form('Record a manual send', `Use this when you sent ${esc(w.amount)} ${esc(w.asset)} to <code>${esc(w.destination)}</code> outside the custody integration. Recording the transaction moves it to <b>Processing</b>; it is completed only after the transaction is confirmed.`,
        [{ n: 'tx_hash', l: 'Transaction hash (TXID)', req: 1, mono: 1 }, { n: 'network_fee', l: `Network fee paid (${w.asset}, optional)`, v: '0' }, { n: 'note', l: 'Note', type: 'textarea' }], 'Record transaction', async o => { await V.post(`/api/admin/crypto/withdrawals/${id}/manual_process`, o); done('Recorded. Awaiting confirmation.'); });
      if (a === 'complete') return form('Complete withdrawal', 'If a blockchain provider is connected, completion requires the required confirmations. Otherwise confirm you verified the transaction independently.',
        [{ n: 'attest', l: 'I verified this transaction independently (block explorer or platform record)', type: 'checkbox' }, { n: 'note', l: 'How did you verify it? (min 20 characters)', type: 'textarea' }], 'Mark completed', async o => { await V.post(`/api/admin/crypto/withdrawals/${id}/manual_complete`, o); done('Withdrawal completed'); });
      UI.run(b, async () => { await V.post(`/api/admin/crypto/withdrawals/${id}/${a}`, {}); done(a === 'process' ? 'Submitted to custody' : 'Updated'); }); };
  }

  /* ---------------- reconciliation ---------------- */
  async function recon(v) {
    const j = await V.get('/api/admin/crypto/reconciliation'); const last = j.runs[0]; const assets = last ? last.summary.assets || [] : [];
    frame(v, `<div class="top" style="margin-bottom:8px">${can('crypto.recon.manage') ? '<button class="btn" id="rr" type="button">Run reconciliation now</button>' : ''}</div>
      <p class="note">Compares blockchain holdings, custody records, customer liabilities and the double-entry ledger. A mismatch opens an exception; nothing is corrected automatically.</p>
      <section class="box"><h3>Latest run ${last ? `${sb(last.status)} <small>${esc(last.ref)} · ${dt(last.started_at)}</small>` : ''}</h3>${table([{ h: 'Asset', r: a => `<b>${esc(a.ledger_code)}</b>` }, { h: 'On-chain holdings', cls: 'r', r: a => esc(a.chain_holdings) }, { h: 'Ledger custody', cls: 'r', r: a => esc(a.ledger_custody) },
        { h: 'Customer liabilities', cls: 'r', r: a => esc(a.customer_liabilities) }, { h: 'Unattributed', cls: 'r', r: a => esc(a.unattributed_suspense) }, { h: 'Holdings ≥ owed', r: a => (a.solvent ? sb('completed').replace('Completed', 'Yes') : '<span class="st st-failed">NO</span>') }, { h: 'Matches ledger', r: a => (a.ledger_matches_chain ? 'Yes' : '<b>No</b>') }], assets, { empty: 'No run yet.' })}<small>Amounts are in the asset's smallest unit.</small></section>
      <section class="box" style="margin-top:16px"><h3>Exceptions</h3>${table([{ h: 'Exception', r: e => `<b>${esc(label(e.kind))}</b><small>${esc(e.ledger_code || '')} · ${dt(e.created_at)}</small>` }, { h: 'Severity', r: e => sb(e.severity === 'critical' ? 'failed' : e.severity === 'high' ? 'pending' : 'draft').replace(/>[^<]*</, '>' + esc(label(e.severity)) + '<') },
        { h: 'Details', r: e => `<small>${esc(JSON.stringify(e.details))}</small>` }, { h: 'Status', r: e => sb(e.status) + (e.resolution_note ? `<small>${esc(e.resolution_note)}</small>` : '') },
        { h: '', r: e => (can('crypto.recon.manage') && ['open', 'investigating'].includes(e.status) ? `<button class="btn ghost sm" data-x="${e.id}">Update</button>` : '') }], j.exceptions, { empty: 'No exceptions.' })}</section>`, '', true);
    const rr = $('#rr'); if (rr) rr.onclick = () => UI.run(rr, async () => { const r = await V.post('/api/admin/crypto/reconciliation/run'); done(`Reconciliation ${r.status}: ${r.open_exceptions} open exception(s)`); });
    $('#ctab').onclick = e => { const b = e.target.closest('[data-x]'); if (b) form('Update exception', 'Critical exceptions cannot be waived. Describe the investigation or the fix.', [{ n: 'status', l: 'Status', type: 'select', v: 'investigating', o: [['investigating', 'Investigating'], ['resolved', 'Resolved'], ['waived', 'Waived (non-critical only)']] }, { n: 'note', l: 'Note (min 10 characters)', type: 'textarea', req: 1 }], 'Save',
      async o => { await V.post(`/api/admin/crypto/reconciliation/exceptions/${b.dataset.x}/resolve`, o); done('Updated'); }); };
  }

  /* ---------------- crypto assets: tab inside the existing Assets page ---------------- */
  const postForm = (url, fd, onp) => new Promise((res, rej) => {
    const x = new XMLHttpRequest(); x.open('POST', url); x.responseType = 'json'; if (V.state.csrf) x.setRequestHeader('X-CSRF-Token', V.state.csrf);
    x.upload.onprogress = e => { if (e.lengthComputable && onp) onp(Math.round((e.loaded / e.total) * 100)); };
    x.onerror = () => rej(new Error('Cannot reach the server.')); x.onload = () => (x.status >= 200 && x.status < 300 ? res(x.response) : rej(new Error((x.response && x.response.error && x.response.error.message) || 'The request failed.')));
    x.send(fd);
  });
  const NETF = (c = {}) => [{ n: 'min_deposit', l: 'Minimum deposit', v: c.min_deposit || '' }, { n: 'max_deposit', l: 'Maximum deposit (blank = no cap)', v: c.max_deposit || '' }, { n: 'min_withdrawal', l: 'Minimum withdrawal', v: c.min_withdrawal || '' }, { n: 'max_withdrawal', l: 'Maximum withdrawal (blank = no cap)', v: c.max_withdrawal || '' },
    { n: 'fee_flat', l: 'Withdrawal fee, flat', v: c.fee_flat || '0' }, { n: 'fee_percent', l: 'Withdrawal fee, percent', v: c.fee_percent || '0' }, { n: 'confirmations', l: 'Deposit confirmations required (blank = network default)', v: c.confirmations_override ? String(c.confirmations_override) : '' }];
  /* text customers read on the Deposit and Withdraw pages. Blank = a safe generic default computed from the real configuration. */
  const COPYF = (c = {}) => [{ n: 'estimated_arrival', l: 'Estimated arrival (shown on both pages; an estimate, not a promise)', v: c.estimated_arrival || '', max: 120 }, { n: 'processing_time', l: 'Withdrawal processing time (describe, do not guarantee)', v: c.processing_time || '', max: 160 },
    { n: 'deposit_instructions', l: 'Deposit instructions (one point per line)', type: 'textarea', v: c.deposit_instructions || '', max: 600 }, { n: 'withdrawal_instructions', l: 'Withdrawal instructions (one point per line)', type: 'textarea', v: c.withdrawal_instructions || '', max: 600 },
    { n: 'network_notice', l: 'Network warning (shown on both pages)', type: 'textarea', v: c.network_notice || '', max: 400 }];
  const COPYK = ['estimated_arrival', 'processing_time', 'deposit_instructions', 'withdrawal_instructions', 'network_notice'];
  let ATAB = 'market', ASSET_DATA = null;
  const tabBar = () => `<div class="tabs" role="tablist" style="margin-bottom:14px"><button class="btn sm ${ATAB === 'market' ? 'tab-on' : 'ghost'}" data-atab="market">Market assets</button> <button class="btn sm ${ATAB === 'crypto' ? 'tab-on' : 'ghost'}" data-atab="crypto">Cryptocurrencies (funding)</button> <button class="btn sm ${ATAB === 'networks' ? 'tab-on' : 'ghost'}" data-atab="networks">Blockchain networks</button></div>`;
  const bindTabs = root => root.querySelectorAll('[data-atab]').forEach(b => (b.onclick = () => { ATAB = b.dataset.atab; R.assets(); }));

  async function cryptoAssets(v) {
    const j = await V.get('/api/admin/crypto/assets'); ASSET_DATA = j; const manage = can('crypto.registry.manage');
    const onoff = (x, t) => `<span class="st st-${x ? 'completed' : 'draft'}">${t} ${x ? 'on' : 'off'}</span>`;
    v.innerHTML = `<div class="top"><h1>Assets</h1>${manage ? '<button class="btn" id="addc">Add cryptocurrency</button>' : ''}</div>${tabBar()}
      <p class="note">These records drive everything customers see: deposit and withdrawal selection, logos, networks, limits and fees. Changing a logo updates it everywhere without creating a new asset.</p>
      ${j.assets.map(a => `<section class="box" style="margin-bottom:16px" data-aid="${a.id}"><div class="assethead">${logo(a, 52)}<div class="ah-t"><h3 style="margin:0">${esc(a.name)} <small>${esc(a.symbol)}</small></h3><small>${esc(a.description || 'No description')}</small></div>
          <div class="ah-s">${rchipA(a.status)} ${a.visible ? '<span class="st st-completed">Visible</span>' : '<span class="st st-draft">Hidden</span>'} ${a.logo ? '' : '<span class="st st-pending">No logo</span>'} ${a.usd_price ? `<span class="st st-completed" title="Deposits convert to Account Balance at this price">$${esc(a.usd_price)}</span>` : '<span class="st st-failed" title="' + esc(a.price_issue || '') + '">No USD price: deposits cannot be credited</span>'}</div>
          ${manage ? `<div class="ah-b"><button class="btn ghost sm" data-ea="${a.id}">Edit</button> <button class="btn ghost sm" data-an="${a.id}">Add network</button>${a.networks.length ? '' : ` <button class="btn ghost sm" data-da="${a.id}">Delete</button>`}</div>` : ''}</div>
        ${table([{ h: 'Network', r: n => `<b>${esc(n.network_name)}</b>${n.contract_address ? `<small>${mono(n.contract_address)}</small>` : ''}` }, { h: 'Switches', r: n => `${onoff(n.deposit_enabled, 'Deposit')} ${onoff(n.withdrawal_enabled, 'Withdraw')}<small>asset-network ${esc(label(n.status))} · network ${esc(label(n.network_status))}${n.has_deposit_wallet ? '' : ' · <b>no deposit wallet</b>'}</small>` },
          { h: 'Deposit limits', r: n => `${esc(n.min_deposit || '—')} – ${esc(n.max_deposit || 'no cap')}` }, { h: 'Withdrawal limits', r: n => `${esc(n.min_withdrawal || '—')} – ${esc(n.max_withdrawal || 'no cap')}` },
          { h: 'Fee', r: n => `${esc(n.fee_flat)} ${esc(a.symbol)}${Number(n.fee_percent) ? ' + ' + esc(n.fee_percent) + '%' : ''}` }, { h: 'Confirmations', r: n => esc(n.confirmations_effective) + (n.confirmations_override ? '' : ' <small>(default)</small>') },
          { h: '', r: n => (manage ? `<button class="btn ghost sm" data-cn="${n.id}" data-sym="${esc(a.symbol)}">Configure</button>` : '') }], a.networks, { empty: 'No networks registered for this asset yet.' })}</section>`).join('') || '<div class="box empty">No cryptocurrencies yet.</div>'}`;
    bindTabs(v);
    const find = id => j.assets.find(a => String(a.id) === String(id)), netOf = id => j.assets.flatMap(a => a.networks.map(n => ({ ...n, symbol: a.symbol }))).find(n => String(n.id) === String(id));
    const add = $('#addc'); if (add) add.onclick = () => assetModal(null, j);
    v.onclick = e => { const ea = e.target.closest('[data-ea]'), an = e.target.closest('[data-an]'), cn = e.target.closest('[data-cn]'), da = e.target.closest('[data-da]');
      if (ea) { location.hash = '#/asset-settings/' + ea.dataset.ea; return; }   // asset settings are a dedicated page, not a dialog
      if (an) { const a = find(an.dataset.an); form(`Add a network for ${a.symbol}`, 'Register this asset on another blockchain network. Contract address is required for tokens.',
        [{ n: 'network_code', l: 'Network', type: 'select', o: j.networks.map(n => [n.network_code, n.name]) }, { n: 'decimals', l: 'Decimals (smallest unit)', req: 1, v: '18' }, { n: 'contract_address', l: 'Token contract address (tokens only)', mono: 1 }, ...NETF(), ...COPYF()], 'Add network',
        async o => { const b = Object.fromEntries(Object.entries(o).filter(([, x]) => x !== '')); await V.post(`/api/admin/crypto/assets/${a.id}/networks`, b); done2('Network added'); }); }
      if (cn) { location.hash = '#/asset-settings/' + cn.closest('[data-aid]').dataset.aid + '?tab=network'; return; }
      if (da) form('Delete cryptocurrency', 'Only an asset with no networks and no history can be deleted. Anything else must be hidden or disabled instead.', [], 'Delete', async () => { await V.del(`/api/admin/crypto/assets/${da.dataset.da}`); done2('Deleted'); }); };
  }
  const rchipA = s => `<span class="st st-${s === 'active' ? 'completed' : s === 'suspended' ? 'pending' : 'draft'}">${esc(label(s))}</span>`;
  const done2 = msg => { toast(msg); R.assets(); };

  /* ---------------- blockchain networks ---------------- */
  async function networksTab(v) {
    const j = await V.get('/api/admin/crypto/blockchain-networks'), manage = can('crypto.registry.manage');
    v.innerHTML = `<div class="top"><h1>Assets</h1>${manage ? '<button class="btn" id="addn">Add blockchain network</button>' : ''}</div>${tabBar()}
      <p class="note">A network is where an asset lives (Bitcoin, Ethereum, …). Customers only see an asset on a network that is <b>active</b> here <i>and</i> enabled for that asset. A new network starts <b>disabled</b>: it cannot carry customer funds until a blockchain provider that can monitor and pay out on it is connected and an operator activates it. Disabling a network stops <i>new</i> transactions only; existing records keep their network.</p>
      ${table([{ h: 'Network', r: n => `<b>${esc(n.name)}</b><small>${esc(n.network_code)}${n.is_testnet ? ' · test network' : ''}</small>` }, { h: 'Type', r: n => esc(label(n.chain_type)) }, { h: 'Address format', r: n => (n.usable ? esc(n.address_format) : '<span class="st st-pending">not set</span>') },
        { h: 'Confirmations', cls: 'r', r: n => esc(n.confirmations_required) }, { h: 'Status', r: n => rchipA(n.status) }, { h: 'Assets', r: n => (n.assets.length ? esc(n.assets.join(', ')) : '—') },
        { h: '', r: n => (manage ? `<button class="btn ghost sm" data-en="${n.id}">Edit</button>` : '') }], j.networks, { empty: 'No networks registered.' })}`;
    bindTabs(v);
    const add = $('#addn'); if (add) add.onclick = () => form('Add blockchain network', 'Creates a DISABLED network. The address format decides how customer withdrawal addresses are validated.',
      [{ n: 'network_code', l: 'Identifier (lowercase letters, digits, dashes)', req: 1, v: '' }, { n: 'name', l: 'Display name', req: 1 }, { n: 'chain_type', l: 'Chain type', type: 'select', o: [['account', 'Account based'], ['utxo', 'UTXO based']], v: 'account' },
        { n: 'address_format', l: 'Address format', type: 'select', o: [['evm', 'EVM (0x…)'], ['btc', 'Bitcoin (bc1… / 1… / 3…)'], ['solana', 'Solana (base58)']], v: 'evm' }, { n: 'confirmations_required', l: 'Default confirmations required', req: 1, v: '12' }, { n: 'chain_id', l: 'Chain ID (EVM only, optional)' },
        { n: 'is_testnet', l: 'This is a test network', type: 'checkbox', v: false }], 'Create network', async o => { const b = Object.fromEntries(Object.entries(o).filter(([, x]) => x !== '')); await V.post('/api/admin/crypto/blockchain-networks', b); toast('Network created (disabled)'); R.assets(); });
    v.onclick = e => { const b = e.target.closest('[data-en]'); if (!b) return; const n = j.networks.find(x => String(x.id) === b.dataset.en);
      form(`Edit ${n.name}`, 'Changes are recorded in the audit log. Lowering confirmations or activating a network affects how deposits are credited, so give a clear reason.',
        [{ n: 'name', l: 'Display name', v: n.name, req: 1 }, { n: 'confirmations_required', l: 'Default confirmations required', v: String(n.confirmations_required) }, { n: 'status', l: 'Status', type: 'select', v: n.status, o: ['active', 'disabled', 'suspended'].map(s => [s, label(s)]) },
          { n: 'reason', l: 'Reason for the change (min 10 characters)', type: 'textarea', req: 1 }], 'Save', async o => { await V.post(`/api/admin/crypto/blockchain-networks/${n.id}`, o); toast('Network saved'); R.assets(); }); };
  }

  function assetModal(a, j) {
    const edit = !!a; let file = null, remove = false;
    const dlg = modal(edit ? `Edit ${a.symbol}` : 'Add cryptocurrency', `<form id="af" novalidate>
      <div class="f-row"><label class="f">Cryptocurrency name<input name="name" required maxlength="60" value="${esc(a ? a.name : '')}" autocomplete="off"></label><label class="f">Symbol<input name="symbol" ${edit ? 'readonly' : 'required'} maxlength="12" value="${esc(a ? a.symbol : '')}" autocomplete="off" style="text-transform:uppercase"></label></div>
      ${edit ? '' : '<label class="f">Type<select name="type"><option value="native">Native coin</option><option value="token">Token</option><option value="stablecoin">Stablecoin</option></select></label>'}
      <label class="f">Description<textarea name="description" rows="3" maxlength="600">${esc(a ? a.description : '')}</textarea></label>
      <div class="f-row"><label class="f">Logo alternative text<input name="logo_alt" maxlength="140" value="${esc(a ? a.logo_alt : '')}" placeholder="${esc(a ? a.name : 'Name')} logo" autocomplete="off"></label><label class="f">Asset notice (shown on both pages)<input name="notice" maxlength="400" value="${esc(a ? a.notice : '')}" autocomplete="off"></label></div>
      <div class="logoup"><div id="lprev">${logo(a || { symbol: 'NEW' }, 72)}</div><div><b>Logo</b><p class="muted-s" style="margin:4px 0 8px">PNG, JPEG, WebP or SVG (plain shapes only). Up to 1 MB (SVG 100 KB), at least 32×32 px. Content is validated before saving.</p>
        <input type="file" id="lfile" accept="image/png,image/jpeg,image/webp,image/svg+xml" hidden><button type="button" class="btn ghost sm" id="lpick">${edit && a.logo ? 'Replace logo' : 'Choose logo'}</button> <button type="button" class="btn ghost sm" id="lrm" ${edit && a.logo ? '' : 'hidden'}>Remove logo</button><div id="lmsg" class="neg" role="alert"></div></div></div>
      <label class="f" style="flex-direction:row;gap:8px"><input type="checkbox" name="visible" ${!a || a.visible ? 'checked' : ''}> Visible to customers</label>
      ${edit ? '' : `<label class="f" style="flex-direction:row;gap:8px"><input type="checkbox" name="activate"> Activate the asset now</label>
        <fieldset class="fs"><legend>First supported network</legend><label class="f">Network<select name="network_code"><option value="">— add later —</option>${j.networks.map(n => `<option value="${esc(n.network_code)}">${esc(n.name)}</option>`).join('')}</select></label>
        <div class="f-row"><label class="f">Decimals<input name="decimals" value="18" inputmode="numeric"></label><label class="f">Token contract (tokens only)<input name="contract_address" spellcheck="false" autocomplete="off"></label></div>
        <div class="f-row">${NETF().map(f => `<label class="f">${esc(f.l)}<input name="${f.n}" value="${esc(f.v)}" autocomplete="off"></label>`).join('')}</div></fieldset>`}
      <div id="aprog" hidden><div class="pbar2"><i id="afill"></i></div></div><p class="neg" id="am" role="alert"></p><div class="actions"><button type="button" class="btn ghost" data-x>Cancel</button><button class="btn" id="asv">${edit ? 'Save changes' : 'Create asset'}</button></div></form>`, { wide: true });
    const prev = html => ($('#lprev', dlg).innerHTML = html);
    $('#lpick', dlg).onclick = () => $('#lfile', dlg).click();
    $('#lfile', dlg).onchange = e => { const f = e.target.files[0]; e.target.value = ''; $('#lmsg', dlg).textContent = ''; if (!f) return;
      if (!['image/png', 'image/jpeg', 'image/webp', 'image/svg+xml'].includes(f.type)) { $('#lmsg', dlg).textContent = 'Choose a PNG, JPEG, WebP or SVG image.'; return; } if (f.size > (f.type === 'image/svg+xml' ? 100 * 1024 : 1024 * 1024)) { $('#lmsg', dlg).textContent = 'That file is too large.'; return; }
      file = f; remove = false; const r = new FileReader(); r.onload = () => { prev(`<img class="alogo" src="${r.result}" alt="Logo preview" width="72" height="72" style="width:72px;height:72px">`); $('#lpick', dlg).textContent = 'Replace logo'; $('#lrm', dlg).hidden = false; }; r.readAsDataURL(f); };
    $('#lrm', dlg).onclick = () => { file = null; remove = true; prev(logo({ symbol: ($('[name=symbol]', dlg).value || 'NEW') }, 72)); $('#lrm', dlg).hidden = true; $('#lpick', dlg).textContent = 'Choose logo'; };
    $('#af', dlg).onsubmit = async e => { e.preventDefault(); const f = e.target, m = $('#am', dlg), b = $('#asv', dlg); m.textContent = ''; b.disabled = true; const fd = new FormData();
      for (const el of f.elements) { if (!el.name || el.type === 'file' || el.disabled) continue; if (el.type === 'checkbox') fd.append(el.name, el.checked ? 'true' : 'false'); else if (el.value.trim() !== '' || ['name', 'description', 'symbol', 'logo_alt', 'notice'].includes(el.name)) fd.append(el.name, el.value.trim()); }
      if (file) fd.append('logo', file, file.name); if (remove) fd.append('remove_logo', 'true'); $('#aprog', dlg).hidden = !file;
      try { await postForm(edit ? `/api/admin/crypto/assets/${a.id}` : '/api/admin/crypto/assets', fd, p => { $('#afill', dlg).style.width = p + '%'; }); toast(edit ? 'Asset updated' : 'Asset created'); dlg.close(); R.assets(); } catch (er) { m.textContent = er.message; b.disabled = false; } };
  }

  /* the existing Assets page keeps its market tab; the crypto tab is added to it */
  const origAssets = R.assets;
  R.assets = async () => {
    const canC = can('crypto.registry.read'), canM = can('assets.manage');
    if (!canC) return origAssets(); if (!canM && ATAB !== 'networks') ATAB = 'crypto';
    if (ATAB === 'crypto') return load(v => cryptoAssets(v));
    if (ATAB === 'networks' && can('crypto.registry.read')) return load(v => networksTab(v));
    await origAssets(); const v = $('#view'); if (v && canC) { v.insertAdjacentHTML('afterbegin', tabBar()); bindTabs(v); }
  };
  if (nav('assets')) nav('assets')[3] = ['assets.manage', 'crypto.registry.read'];

  R.deposits = () => { CUR = 'deposits'; return load(v => deposits(v)); };
  R.withdrawals = () => { CUR = 'withdrawals'; return load(v => withdrawals(v)); };
  /* The Wallets & Reconciliation page itself is rendered by wallets-admin.js (dedicated pages, no dialogs). This module keeps the customer sending-address verification queue. */
  R['settings-crypto'] = () => { CUR = 'settings-crypto'; return load(v => ({ wallets, addresses, recon })[tabs().some(t => t[0] === TAB) ? TAB : tabs()[0][0]](v)); };
  R['crypto-sources'] = () => { CUR = 'crypto-sources'; TAB = 'addresses'; return load(v => addresses(v)); };
});
