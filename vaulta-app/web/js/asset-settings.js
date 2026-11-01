/* VAULTA admin: dedicated Asset Settings PAGE  (#/asset-settings/<asset-id>?tab=general|network|wallet[&net=<code>][&wallet=<id>][&change=1]).
   Not a dialog, drawer or overlay: it has its own route, breadcrumb, Back button and layout. All three tabs live in the DOM at once, so switching tabs keeps unsaved values;
   leaving the page with unsaved edits asks first (VNav.guard). Everything is saved through the existing audited admin APIs; the server re-validates every value. */
(window.VAULTA_ADMIN_EXT = window.VAULTA_ADMIN_EXT || []).push(({ R, load }) => {
  const W = () => window.VW, E = s => W().esc(s);
  const TYPES = { native: 'Native coin', token: 'Token', stablecoin: 'Stablecoin' };
  const ASTAT = [['active', 'Active'], ['disabled', 'Inactive'], ['suspended', 'Maintenance']];
  const ADDR = { evm: a => /^0x[0-9a-fA-F]{40}$/.test(a), solana: a => /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(a), btc: a => /^((bc1|tb1|bcrt1)[0-9a-z]{20,90}|[13mn2][1-9A-HJ-NP-Za-km-z]{25,40})$/.test(a) };
  const DEC = /^\d+(\.\d+)?$/;
  const COPY = [['estimated_arrival', 'Estimated arrival', 120], ['processing_time', 'Processing time', 160], ['deposit_instructions', 'Deposit instructions', 600], ['withdrawal_instructions', 'Withdrawal instructions', 600], ['network_notice', 'Network notice', 400]];

  /* a small form model: baseline snapshot, dirty check, reset, per-field errors */
  function model(form) {
    const read = () => { const o = {}; for (const el of form.elements) { if (!el.name || el.type === 'button' || el.type === 'submit' || el.dataset.skip) continue; o[el.name] = el.type === 'checkbox' ? el.checked : el.value; } return o; };
    let base = read(); const extra = { dirty: () => false, reset: () => { }, saved: () => { } };
    return {
      read, dirty: () => JSON.stringify(read()) !== JSON.stringify(base) || extra.dirty(), reset: () => { for (const el of form.elements) { if (!(el.name in base)) continue; if (el.type === 'checkbox') el.checked = base[el.name]; else el.value = base[el.name]; el.dispatchEvent(new Event('input', { bubbles: true })); } clear(form); extra.reset(); },
      rebase: names => { const r = read(); (names || Object.keys(r)).forEach(n => { base[n] = r[n]; }); extra.saved(); }, hook: h => Object.assign(extra, h), base: () => base
    };
  }
  function clear(form) { form.querySelectorAll('.as-ferr').forEach(x => x.remove()); form.querySelectorAll('[aria-invalid]').forEach(x => x.removeAttribute('aria-invalid')); const a = form.querySelector('.as-alert'); if (a) a.hidden = true; }
  function fail(form, errs, general) {
    clear(form); let first = null;
    for (const [n, msg] of Object.entries(errs || {})) { const el = form.elements[n]; if (!el) continue; el.setAttribute('aria-invalid', 'true'); const p = document.createElement('small'); p.className = 'as-ferr'; p.setAttribute('role', 'alert'); p.textContent = msg; (el.closest('label') || el.parentElement).append(p); first = first || el; }
    const a = form.querySelector('.as-alert'); if (a) { a.textContent = general || (first ? 'Please fix the highlighted fields.' : ''); a.hidden = !a.textContent; }
    if (first) first.focus(); else if (a && !a.hidden) a.scrollIntoView({ block: 'nearest' });
  }
  const field = (n, l, v, o = {}) => `<label class="f ${o.cls || ''}"><span>${E(l)}${o.req ? ' <i class="req" aria-hidden="true">*</i>' : ''}</span>${o.type === 'textarea' ? `<textarea name="${n}" rows="${o.rows || 3}" maxlength="${o.max || 600}" ${o.req ? 'required' : ''} ${o.ro ? 'readonly' : ''}>${E(v || '')}</textarea>`
    : o.type === 'select' ? `<select name="${n}" ${o.ro ? 'disabled' : ''}>${o.o.map(x => `<option value="${E(x[0])}" ${String(x[0]) === String(v) ? 'selected' : ''}>${E(x[1])}</option>`).join('')}</select>`
      : `<input name="${n}" value="${E(v == null ? '' : v)}" ${o.mono ? 'class="mono" spellcheck="false"' : ''} ${o.ro ? 'readonly aria-readonly="true"' : ''} ${o.max ? `maxlength="${o.max}"` : ''} ${o.req ? 'required' : ''} ${o.code ? 'inputmode="numeric" autocomplete="one-time-code" maxlength="8"' : 'autocomplete="off"'} ${o.im ? `inputmode="${o.im}"` : ''}>`}${o.hint ? `<small class="hint">${o.hint}</small>` : ''}</label>`;
  const sw = (n, l, on, hint) => `<label class="as-sw"><input type="checkbox" name="${n}" ${on ? 'checked' : ''}><span class="as-swt" aria-hidden="true"><i></i></span><span><b>${E(l)}</b>${hint ? `<small>${E(hint)}</small>` : ''}</span></label>`;

  async function page(v, id) {
    const w = W(), { $, $$, toast, V, can, ico, I, chip, mono, copyBtn, short, dt, label, logo, postForm, inlineForm, pendingTable, bindPending } = w;
    const j = await V.get(`/api/admin/crypto/assets/${encodeURIComponent(id)}/settings`);
    const P = new URLSearchParams(location.hash.split('?')[1] || '');
    const manage = can('crypto.registry.manage'), wmanage = can('crypto.wallets.manage'), wread = can('crypto.wallets.read');
    let st = { asset: j.asset, wallets: j.wallets, reqs: j.change_requests, hist: j.history, env: j.environment, mode: j.provider_mode };
    let tab = ['general', 'network', 'wallet'].includes(P.get('tab')) ? P.get('tab') : 'general';
    let selNet = (st.asset.networks.find(n => n.network_code === P.get('net')) || st.asset.networks[0] || {}).id || null;
    let selWallet = +P.get('wallet') || null; let qrStamp = Date.now(); const forms = []; let logoState = { file: null, remove: false, url: null };
    const netOf = i => st.asset.networks.find(n => n.id === i), curNet = () => netOf(selNet);
    const walletsOf = nid => st.wallets.filter(x => x.asset_network_id === nid);
    const anyDirty = () => forms.some(f => f.dirty());
    const asset = () => st.asset;
    const typeText = a => 'Cryptocurrency · ' + (TYPES[a.type] || label(a.type));
    const activeWallet = nid => walletsOf(nid).find(x => x.status === 'active' && x.is_deposit_enabled && x.configured);

    v.innerHTML = `<header class="as-head"><h1>Asset Settings</h1><p class="a-sub">Configure asset details, network information and wallet settings.</p></header>
      <section class="box as-sum" id="as-sum" aria-label="Asset summary"></section>
      <div class="as-grid"><div class="as-main box"><div class="wr-tabs as-tabs" role="tablist" aria-label="Asset configuration">${[['general', 'General Information'], ['network', 'Network Settings'], ['wallet', 'Wallet Configuration']].map(([k, t]) => `<button type="button" role="tab" id="tb-${k}" aria-controls="pn-${k}" data-tab="${k}" class="${k === tab ? 'on' : ''}" aria-selected="${k === tab}" tabindex="${k === tab ? 0 : -1}">${t}</button>`).join('')}</div>
        <div role="tabpanel" id="pn-general" aria-labelledby="tb-general" ${tab === 'general' ? '' : 'hidden'}></div><div role="tabpanel" id="pn-network" aria-labelledby="tb-network" ${tab === 'network' ? '' : 'hidden'}></div><div role="tabpanel" id="pn-wallet" aria-labelledby="tb-wallet" ${tab === 'wallet' ? '' : 'hidden'}></div></div>
        <aside class="as-aside box" id="as-aside" aria-label="Asset information"></aside></div>`;

    /* ---------- summary + sidebar (always rendered from the saved state) ---------- */
    const paintSummary = () => { const a = asset(); $('#as-sum', v).innerHTML = `<span class="as-logo-lg">${logo(a, 64)}</span><div><h2>${E(a.name)} <span class="as-sym">(${E(a.symbol)})</span> ${chip(a.status)}</h2><p>${E(typeText(a))}${a.visible ? '' : ' · hidden from customers'}</p></div>`; };
    const paintAside = () => {
      const a = asset(), n = curNet(), aw = n && activeWallet(n.id), ws = n ? walletsOf(n.id) : [], qr = ws.some(x => x.qr_available && x.status === 'active');
      const row = (k, val) => `<div class="as-kv"><span>${k}</span><b>${val}</b></div>`;
      $('#as-aside', v).innerHTML = `<div class="as-ah"><span class="as-logo-lg">${logo(a, 56)}</span><h3>${E(a.name)} <span class="as-sym">(${E(a.symbol)})</span> ${chip(a.status)}</h3></div>
        <div class="as-kvs">${row('Asset type', E(typeText(a)))}${row('Symbol', E(a.symbol))}${row('Status', chip(a.status))}</div>
        <h4>Network information</h4>${n ? `${st.asset.networks.length > 1 ? `<label class="f as-netpick"><span>Network</span><select id="as-ap">${st.asset.networks.map(x => `<option value="${x.id}" ${x.id === selNet ? 'selected' : ''}>${E(x.network_name)}</option>`).join('')}</select></label>` : ''}
        <div class="as-kvs">${row('Network name', E(n.network_name))}${row('Type', n.is_testnet ? 'Testnet' : 'Mainnet')}${row('Contract address', n.contract_address ? `<code class="wr-addr">${E(short(n.contract_address, 10, 6))}</code>` : 'Native asset')}
        ${row('Explorer', n.explorer_url ? `<a href="${E(n.explorer_url)}" target="_blank" rel="noopener noreferrer">${E(n.explorer_url.replace(/^https:\/\//, ''))} ${ico(I.ext, 13)}</a>` : '<span class="wr-na">Not configured</span>')}${row('Confirmations', E(n.confirmations_effective))}</div>
        <h4>Deposit requirements</h4><div class="as-kvs">${row('Minimum deposit', `${E(n.min_deposit || '0')} ${E(a.symbol)}`)}${row('Maximum deposit', n.max_deposit ? `${E(n.max_deposit)} ${E(a.symbol)}` : 'No cap')}
        ${row('Deposit status', n.deposit_enabled && n.status === 'active' && a.status === 'active' ? chip('active', 'Accepting deposits') : chip('inactive', 'Not accepting deposits'))}${row('QR code', qr ? 'Available (generated)' : '<span class="wr-na">Not available</span>')}
        ${row('Receiving address', aw ? chip('active', 'Active address set') : '<span class="wr-na">No active address</span>')}</div>` : '<p class="note">No networks are registered for this asset yet. Add one in Network Settings.</p>'}
        <div class="as-note">${ico(I.info, 18)}<div><b>Important note</b><p>Changes to asset configuration can affect deposits, withdrawals, wallet displays and other platform services. Historical transactions are never rewritten.</p></div></div>`;
      const ap = $('#as-ap', v); if (ap) ap.onchange = e => { selNet = +e.target.value; paintAside(); const wn = $('#wl-net', v); if (wn) { wn.value = selNet; paintWallet(); } };
    };

    /* ---------- General Information ---------- */
    const lg = (a, o) => (o.remove ? '' : o.url || a.logo);
    function paintGeneral() {
      const a = asset(), ro = !manage;
      $('#pn-general', v).innerHTML = `<form id="f-gen" novalidate><h3 class="as-h">Asset details</h3><p class="as-alert wr-err" role="alert" hidden></p>
        <div class="as-cols">${field('name', 'Asset name', a.name, { req: 1, max: 60, ro })}${field('symbol', 'Symbol', a.symbol, { ro: 1, hint: 'The symbol is the ledger key for this asset and cannot be changed.' })}
        ${field('type', 'Asset type', typeText(a), { ro: 1, hint: 'Set when the asset was created: it decides whether a token contract is required.' })}${field('status', 'Status', a.status, { type: 'select', o: ASTAT, ro, cls: 'as-statusf' })}</div>
        <div class="as-cols1">${sw('visible', 'Show this asset to customers', a.visible, 'When off, customers do not see it in deposit, withdrawal or portfolio lists.')}</div>
        <div id="as-reason-w" hidden>${field('reason', 'Reason for the status change', '', { type: 'textarea', rows: 2, max: 300, hint: 'Required when the status changes (at least 10 characters). Recorded in the audit log.' })}</div>
        <h3 class="as-h">Asset image</h3><div class="as-imgs"><div class="as-drop" id="as-drop"><span class="as-prev" id="lg-prev"></span>${manage ? '<button type="button" class="btn ghost sm" id="lg-pick">Change image</button>' : ''}<input type="file" id="lg-file" data-skip="1" accept="image/png,image/jpeg,image/svg+xml,.png,.jpg,.jpeg,.svg" hidden><small>PNG, JPG or SVG<br>(max 1 MB, SVG max 100 KB)</small></div>
        <div class="as-cur"><span class="as-prev" id="lg-cur"></span><div><b>Current image</b><small>Recommended size: 128 × 128 px</small><small>Supports PNG, JPG, SVG</small><span id="lg-state" class="as-lgs" role="status"></span>
        <span class="as-lgb">${manage ? '<button type="button" class="wr-link" id="lg-rm" hidden>Remove image</button><button type="button" class="wr-link" id="lg-undo" hidden>Undo image change</button>' : ''}</span></div></div></div>
        ${field('logo_alt', 'Image description (alt text)', a.logo_alt, { max: 140, ro, hint: 'Read by screen readers wherever the logo appears.' })}
        ${field('description', 'Description', a.description, { type: 'textarea', max: 600, rows: 4, ro, hint: '<span id="cc-d" aria-live="polite"></span>' })}
        ${field('notice', 'Customer notice (optional)', a.notice, { type: 'textarea', max: 400, rows: 2, ro, hint: 'Shown to customers on this asset in deposit and withdrawal screens. <span id="cc-n"></span>' })}
        ${manage ? `<div class="as-act"><button type="button" class="btn ghost" id="g-cancel">Cancel</button><button type="button" class="btn ghost" id="g-reset">Reset</button><button type="submit" class="btn" id="g-save">Save Changes</button></div>` : '<p class="note">You can view this asset but not change it.</p>'}</form>`;
      const f = $('#f-gen', v); const m = model(f); forms.push(m); logoState = { file: null, remove: false, url: null };
      m.hook({ dirty: () => !!(logoState.file || logoState.remove), reset: () => { logoState = { file: null, remove: false, url: null }; paintLogo(); } });
      const cc = (n, el, max) => { const u = () => { el.textContent = `${f.elements[n].value.length}/${max}`; }; f.elements[n].addEventListener('input', u); u(); };
      cc('description', $('#cc-d', f), 600); cc('notice', $('#cc-n', f), 400);
      const stl = () => { $('#as-reason-w', f).hidden = f.elements.status.value === asset().status; };
      f.elements.status.addEventListener('change', stl);
      function paintLogo() {
        const a = asset(), newer = logoState.file ? logoState.url : null, show = (el, url, sym) => { el.innerHTML = url ? `<img src="${E(url)}" alt="" width="72" height="72">` : logo({ symbol: sym }, 72); };
        show($('#lg-prev', f), logoState.remove ? null : (newer || a.logo), a.symbol); show($('#lg-cur', f), a.logo, a.symbol);
        $('#lg-state', f).textContent = logoState.file ? `New image selected: ${logoState.file.name} (not saved yet)` : logoState.remove ? 'The image will be removed when you save.' : a.logo ? '' : 'No image uploaded: a generated badge is shown instead.';
        const rm = $('#lg-rm', f), un = $('#lg-undo', f); if (rm) rm.hidden = !(a.logo && !logoState.remove && !logoState.file); if (un) un.hidden = !(logoState.file || logoState.remove);
        const dr = $('#lg-pick', f); if (dr) dr.textContent = a.logo || logoState.file ? 'Replace image' : 'Change image';
      }
      paintLogo();
      if (manage) {
        const file = $('#lg-file', f); $('#lg-pick', f).onclick = () => file.click();
        file.onchange = () => {
          const x = file.files[0]; if (!x) return; file.value = '';
          const okT = ['image/png', 'image/jpeg', 'image/svg+xml'].includes(x.type) && /\.(png|jpe?g|svg)$/i.test(x.name), svg = /svg/.test(x.type);
          if (!okT) return fail(f, { logo: '' }, 'Unsupported file type. Upload a PNG, JPG or SVG image.');
          if (x.size > (svg ? 100 * 1024 : 1024 * 1024)) return fail(f, {}, svg ? 'SVG logos must be 100 KB or smaller.' : 'Logos must be 1 MB or smaller.');
          if (!x.size) return fail(f, {}, 'The file is empty.');
          clear(f); if (logoState.url) URL.revokeObjectURL(logoState.url); logoState = { file: x, remove: false, url: URL.createObjectURL(x) }; paintLogo();
        };
        $('#lg-rm', f).onclick = () => { logoState = { file: null, remove: true, url: null }; paintLogo(); };
        $('#lg-undo', f).onclick = () => { if (logoState.url) URL.revokeObjectURL(logoState.url); logoState = { file: null, remove: false, url: null }; paintLogo(); };
        const back = () => { if (m.dirty() && !confirm('Discard your unsaved changes to this asset?')) return; if (m.dirty()) { m.reset(); stl(); } else VNav.back(); };
        $('#g-cancel', f).onclick = back; $('#g-reset', f).onclick = () => { m.reset(); stl(); };
        f.onsubmit = async e => {
          e.preventDefault(); const o = m.read(), errs = {}, a = asset();
          if (!o.name.trim()) errs.name = 'Asset name is required.'; else if (o.name.trim().length > 60) errs.name = 'Asset name must be 60 characters or fewer.';
          if (o.description.length > 600) errs.description = 'Description must be 600 characters or fewer.';
          if (o.notice.length > 400) errs.notice = 'Notice must be 400 characters or fewer.';
          if (o.status !== a.status && (o.reason || '').trim().length < 10) errs.reason = 'Give a reason of at least 10 characters for the status change.';
          if (Object.keys(errs).length) { if (errs.reason) $('#as-reason-w', f).hidden = false; return fail(f, errs); }
          if (!m.dirty()) { toast('No changes to save'); return; }
          const fd = new FormData(); fd.set('name', o.name.trim()); fd.set('description', o.description.trim()); fd.set('notice', o.notice.trim()); fd.set('logo_alt', o.logo_alt.trim()); fd.set('visible', o.visible ? '1' : '0');
          if (o.status !== a.status) { fd.set('status', o.status); fd.set('reason', o.reason.trim()); }
          if (logoState.file) fd.set('logo', logoState.file); else if (logoState.remove) fd.set('remove_logo', '1');
          const b = $('#g-save', f); b.disabled = true;
          try {
            const r = await postForm(`/api/admin/crypto/assets/${a.id}`, fd); st.asset = { ...st.asset, ...r.asset, networks: st.asset.networks.map(n => ({ ...n })) };
            if (logoState.url) URL.revokeObjectURL(logoState.url); logoState = { file: null, remove: false, url: null }; f.elements.reason.value = ''; m.rebase(); stl(); paintLogo(); paintSummary(); paintAside(); clear(f); toast('Asset settings saved');
          } catch (er) { const msg = er.message || 'The request failed.'; fail(f, /name/i.test(msg) && !/logo/i.test(msg) ? { name: msg } : /description/i.test(msg) ? { description: msg } : /reason/i.test(msg) ? { reason: msg } : {}, msg); if (/reason/i.test(msg)) $('#as-reason-w', f).hidden = false; }
          finally { b.disabled = false; }
        };
      } else f.onsubmit = e => e.preventDefault();
    }

    /* ---------- Network Settings ---------- */
    const stOpts = [['active', 'Active'], ['suspended', 'Maintenance'], ['disabled', 'Inactive']];
    function netCard(n) {
      const a = asset(), ro = !manage, u = n.usage, hint = n.address_hint;
      const nf = x => (x == null ? '' : x);
      return `<section class="as-net" data-an="${n.id}" aria-label="${E(n.network_name)}"><header><h3>${E(n.network_name)} ${n.is_testnet ? '<span class="st st-pending">Testnet</span>' : '<span class="st st-draft">Mainnet</span>'} <span class="st st-draft">${n.classification === 'token' ? 'Token' : 'Native'}</span></h3>${chip(n.status)}</header>
        <form class="as-nf" data-kind="net" data-id="${n.network_id}" novalidate><h4>Network record</h4><p class="note">Shared by ${n.shared_with.map(E).join(', ')}. Changes here affect every asset on this network.</p><p class="as-alert wr-err" role="alert" hidden></p>
          <div class="as-cols">${field('n_name', 'Network name', n.network_name, { req: 1, max: 80, ro })}${field('n_code', 'Network identifier', n.network_code, { ro: 1, mono: 1 })}
          ${field('n_type', 'Network type', (n.chain_type === 'utxo' ? 'UTXO' : 'Account-based') + (n.is_testnet ? ' · testnet' : ' · mainnet') + (n.chain_id ? ` · chain ${n.chain_id}` : ''), { ro: 1 })}${field('n_fmt', 'Address format', ({ btc: 'Bitcoin', evm: 'EVM (0x…)', solana: 'Solana' })[n.address_format] || n.address_format, { ro: 1, hint: E(hint || '') })}
          ${field('n_conf', 'Default required confirmations', n.network_default_confirmations, { im: 'numeric', req: 1, ro })}${field('n_status', 'Network status', n.network_status, { type: 'select', o: stOpts, ro })}
          ${field('n_explorer', 'Blockchain explorer URL', nf(n.explorer_url), { max: 300, ro, mono: 1, cls: 'span2', hint: 'Complete https:// address, for example the explorer’s home page. Used for “View on explorer” links.' })}</div>
          ${manage ? `${field('n_reason', 'Reason for the change', '', { type: 'textarea', rows: 2, max: 300, hint: 'At least 10 characters. Recorded in the audit log.' })}<div class="as-act"><button class="btn ghost" type="button" data-reset>Reset</button><button class="btn" type="submit">Save network record</button></div>` : ''}</form>
        <form class="as-nf" data-kind="an" data-id="${n.id}" novalidate><h4>${E(a.symbol)} on ${E(n.network_name)}</h4><p class="as-alert wr-err" role="alert" hidden></p>
          <div class="as-cols">${field('classification', 'Asset classification', n.classification === 'token' ? 'Token (contract based)' : 'Native asset', { ro: 1 })}${field('contract', 'Contract address', n.contract_address || 'Not applicable (native asset)', { ro: 1, mono: 1, hint: 'The contract is part of the ledger identity and cannot be edited.' })}
          ${field('min_deposit', 'Minimum deposit', n.min_deposit, { im: 'decimal', ro, hint: `In ${E(a.symbol)}. Customers cannot submit less.` })}${field('max_deposit', 'Maximum deposit', n.max_deposit, { im: 'decimal', ro, hint: 'Leave empty for no cap.' })}
          ${field('confirmations', 'Required deposit confirmations', n.confirmations_override == null ? '' : n.confirmations_override, { im: 'numeric', ro, hint: `Leave empty to use the network default (${E(n.network_default_confirmations)}).` })}${field('decimals', 'Decimals', n.decimals, { ro: 1 })}
          ${field('min_withdrawal', 'Minimum withdrawal', n.min_withdrawal, { im: 'decimal', ro })}${field('max_withdrawal', 'Maximum withdrawal', n.max_withdrawal, { im: 'decimal', ro, hint: 'Leave empty for no cap.' })}
          ${field('fee_flat', 'Withdrawal fee (flat)', n.fee_flat, { im: 'decimal', ro })}${field('fee_percent', 'Withdrawal fee (%)', n.fee_percent, { im: 'decimal', ro })}</div>
          <div class="as-sws">${sw('deposit_enabled', 'Deposit availability', n.deposit_enabled, 'Customers can start new deposits on this network.')}${sw('withdrawal_enabled', 'Withdrawal availability', n.withdrawal_enabled, 'Customers can request withdrawals on this network. Independent of deposits.')}</div>
          <div class="as-cols1">${field('status', `${a.symbol} status on this network`, n.status, { type: 'select', o: stOpts, ro })}</div>
          <details class="as-copy"><summary>Customer-facing copy (optional)</summary><div class="as-cols">${COPY.map(([k, l, mx]) => field(k, l, n[k], { type: k === 'estimated_arrival' || k === 'processing_time' ? 'text' : 'textarea', max: mx, rows: 2, ro, cls: k === 'estimated_arrival' || k === 'processing_time' ? '' : 'span2' })).join('')}</div></details>
          ${manage ? `${field('reason', 'Reason for the change', '', { type: 'textarea', rows: 2, max: 300, hint: 'At least 10 characters. Recorded in the audit log.' })}<div class="as-act"><button class="btn ghost" type="button" data-reset>Reset</button><button class="btn" type="submit">Save ${E(a.symbol)} settings</button></div>` : ''}</form>
        <div class="as-use"><h4>Usage and removal</h4><p class="note">${u.has_history ? `This network is referenced by ${u.deposits} deposit${u.deposits === 1 ? '' : 's'}, ${u.deposit_requests} deposit request${u.deposit_requests === 1 ? '' : 's'}, ${u.withdrawals} withdrawal${u.withdrawals === 1 ? '' : 's'} and ${u.ledger_lines} ledger line${u.ledger_lines === 1 ? '' : 's'}${u.wallets ? `, and has ${u.wallets} configured wallet${u.wallets === 1 ? '' : 's'}` : ''}. It <b>cannot be deleted</b>: that would corrupt financial history. Disabling stops all new deposits and withdrawals on it while keeping every record.`
          : 'No transactions or wallets use this network yet. Registered networks are never deleted (the ledger identity is permanent); disabling hides it from customers.'}${u.open_intents ? ` ${u.open_intents} open deposit intent${u.open_intents === 1 ? '' : 's'} will stop accepting deposits.` : ''}</p>
          ${manage && (n.status !== 'disabled' || n.deposit_enabled || n.withdrawal_enabled) ? `<button class="btn ghost sm" type="button" data-disable="${n.id}">Disable this network</button>` : n.status === 'disabled' ? '<small>This network is disabled.</small>' : ''}<div class="as-host"></div></div></section>`;
    }
    function paintNetwork() {
      const a = asset();
      $('#pn-network', v).innerHTML = `<h3 class="as-h">Network settings</h3><p class="note">${E(a.name)} can run on several networks. Each network has its own limits, confirmations and switches.</p>
        ${a.networks.length ? a.networks.map(netCard).join('') : '<div class="empty wr-empty">No networks are registered for this asset yet.</div>'}
        ${manage ? `<section class="as-net" id="as-add"><header><h3>Add a network</h3></header><div id="as-add-body"><button class="btn ghost" type="button" id="as-add-b">Add network for ${E(a.symbol)}</button> <a class="wr-link" href="#/assets">Register a new blockchain network</a></div></section>` : ''}`;
      forms.splice(0, forms.length, ...forms.filter(f => f.gen)); // keep the general model only
      const g = forms.find(f => f.gen);
      $$('form.as-nf', $('#pn-network', v)).forEach(f => bindNet(f));
      const add = $('#as-add-b', v); if (add) add.onclick = openAdd;
      $('#pn-network', v).onclick = e => {
        const d = e.target.closest('[data-disable]'); if (!d) return; const n = netOf(+d.dataset.disable), host = d.closest('.as-use').querySelector('.as-host');
        inlineForm(host, { title: `Disable ${n.network_name}`, intro: `Stops new deposits and withdrawals of ${E(a.symbol)} on this network. Existing transactions keep their records.`, fields: [{ n: 'reason', l: 'Reason (min 10 characters)', type: 'textarea', req: 1 }], ok: 'Disable network', danger: 1,
          submit: async o => { const r = await V.post(`/api/admin/crypto/asset-networks/${n.id}/config`, { reason: o.reason, deposit_enabled: false, withdrawal_enabled: false, status: 'disabled' }); Object.assign(n, r.network); toast('Network disabled'); paintNetwork(); paintAside(); paintWallet(); } });
      };
    }
    function bindNet(f) {
      const m = model(f); m.nets = true; forms.push(m); const n = f.dataset.kind === 'net' ? st.asset.networks.find(x => x.network_id === +f.dataset.id) : netOf(+f.dataset.id);
      const rs = f.querySelector('[data-reset]'); if (rs) rs.onclick = () => m.reset();
      f.onsubmit = async e => {
        e.preventDefault(); if (!manage) return; const o = m.read(), errs = {}, ch = {}, b = f.querySelector('button[type=submit]');
        const reason = (o.n_reason ?? o.reason ?? '').trim();
        if (f.dataset.kind === 'net') {
          if (!o.n_name.trim()) errs.n_name = 'Network name is required.'; if (!/^\d+$/.test(o.n_conf) || +o.n_conf < 1 || +o.n_conf > 10000) errs.n_conf = 'Enter a whole number between 1 and 10000.';
          const ex = o.n_explorer.trim(); if (ex) { let u; try { u = new URL(ex); } catch (er) { } if (!u || u.protocol !== 'https:' || !u.hostname.includes('.')) errs.n_explorer = 'Enter a complete https:// address.'; }
          if (Object.keys(errs).length) return fail(f, errs);
          const net = o.n_name.trim() !== n.network_name || +o.n_conf !== n.network_default_confirmations || o.n_status !== n.network_status, exp = ex !== (n.explorer_url || '');
          if (!net && !exp) return toast('No changes to save'); if (reason.length < 10) return fail(f, { n_reason: 'Give a reason of at least 10 characters.' });
          b.disabled = true; clear(f);
          try {
            if (net) { const body = { reason }; if (o.n_name.trim() !== n.network_name) body.name = o.n_name.trim(); if (+o.n_conf !== n.network_default_confirmations) body.confirmations_required = +o.n_conf; if (o.n_status !== n.network_status) body.status = o.n_status;
              const r = await V.post(`/api/admin/crypto/blockchain-networks/${n.network_id}`, body); st.asset.networks.filter(x => x.network_id === n.network_id).forEach(x => Object.assign(x, { network_name: r.network.name, network_default_confirmations: r.network.confirmations_required, network_status: r.network.status, confirmations_effective: x.confirmations_override || r.network.confirmations_required })); m.rebase(['n_name', 'n_conf', 'n_status']); }
            if (exp) { const r = await V.post(`/api/admin/crypto/blockchain-networks/${n.network_id}/explorer`, { explorer_url: ex, reason }); st.asset.networks.filter(x => x.network_id === n.network_id).forEach(x => { x.explorer_url = r.explorer_url; }); f.elements.n_explorer.value = r.explorer_url || ''; m.rebase(['n_explorer']); }
            f.elements.n_reason.value = ''; m.rebase(['n_reason']); toast('Network record saved'); paintAside();
          } catch (er) { fail(f, {}, (er.message || 'The request failed.') + (net && exp ? ' (Any part that already saved is kept.)' : '')); } finally { b.disabled = false; }
        } else {
          const money = ['min_deposit', 'max_deposit', 'min_withdrawal', 'max_withdrawal', 'fee_flat', 'fee_percent'];
          money.forEach(k => { const t = (o[k] || '').trim(); if (t && !DEC.test(t)) errs[k] = 'Enter a number such as 0.001.'; });
          if (!errs.min_deposit && !errs.max_deposit && o.max_deposit.trim() && +o.max_deposit > 0 && +o.max_deposit < +(o.min_deposit || 0)) errs.max_deposit = 'The maximum cannot be below the minimum.';
          if (!errs.min_withdrawal && !errs.max_withdrawal && o.max_withdrawal.trim() && +o.max_withdrawal > 0 && +o.max_withdrawal < +(o.min_withdrawal || 0)) errs.max_withdrawal = 'The maximum cannot be below the minimum.';
          if (o.confirmations.trim() && (!/^\d+$/.test(o.confirmations.trim()) || +o.confirmations < 1)) errs.confirmations = 'Enter a whole number of at least 1, or leave empty for the network default.';
          if (o.fee_percent.trim() && !errs.fee_percent && +o.fee_percent > 20) errs.fee_percent = 'The fee percentage cannot exceed 20.';
          if (Object.keys(errs).length) return fail(f, errs);
          const body = { reason }; for (const k of [...money, 'confirmations', ...COPY.map(c => c[0])]) body[k] = (o[k] ?? '').trim();
          if (o.deposit_enabled !== n.deposit_enabled) body.deposit_enabled = o.deposit_enabled; if (o.withdrawal_enabled !== n.withdrawal_enabled) body.withdrawal_enabled = o.withdrawal_enabled; if (o.status !== n.status) body.status = o.status;
          if (!m.dirty()) return toast('No changes to save'); if (reason.length < 10) return fail(f, { reason: 'Give a reason of at least 10 characters.' });
          b.disabled = true; clear(f);
          try { const r = await V.post(`/api/admin/crypto/asset-networks/${n.id}/config`, body); Object.assign(n, r.network); f.elements.reason.value = ''; m.rebase(); toast('Network settings saved'); paintAside(); paintWallet(); f.closest('section').querySelector('header .st:last-child').outerHTML = chip(n.status); }
          catch (er) { const msg = er.message || 'The request failed.'; const map = /minimum deposit|min deposit/i.test(msg) ? 'min_deposit' : /maximum deposit|max deposit/i.test(msg) ? 'max_deposit' : /confirm/i.test(msg) ? 'confirmations' : /fee/i.test(msg) ? 'fee_percent' : /reason/i.test(msg) ? 'reason' : null; fail(f, map ? { [map]: msg } : {}, msg); }
          finally { b.disabled = false; }
        }
      };
    }
    async function openAdd() {
      const a = asset(), host = $('#as-add-body', v); let nets = [];
      try { nets = (await V.get('/api/admin/crypto/blockchain-networks')).networks.filter(n => n.usable && !a.networks.some(x => x.network_code === n.network_code)); } catch (e) { host.insertAdjacentHTML('beforeend', `<p class="wr-err">${E(e.message)}</p>`); return; }
      if (!nets.length) { host.innerHTML = `<p class="note">Every registered network already carries ${E(a.symbol)}. <a class="wr-link" href="#/assets">Register a new blockchain network</a> first.</p>`; return; }
      inlineForm(host, { title: `Add a network for ${a.symbol}`, intro: `A new network starts <b>disabled</b>. ${a.type === 'native' ? 'Native assets have no contract address.' : 'Tokens need their verified contract address on that network.'}`,
        fields: [{ n: 'network_code', l: 'Network', type: 'select', o: nets.map(n => [n.network_code, `${n.name} (${n.network_code})`]) }, { n: 'decimals', l: 'Decimals (smallest unit)', req: 1, v: '18' }, ...(a.type === 'native' ? [] : [{ n: 'contract_address', l: 'Token contract address', req: 1, mono: 1 }]),
          { n: 'min_deposit', l: 'Minimum deposit' }, { n: 'max_deposit', l: 'Maximum deposit (empty = no cap)' }, { n: 'confirmations', l: 'Required confirmations (empty = network default)' }], ok: 'Add network',
        submit: async o => { const b = {}; for (const [k, x] of Object.entries(o)) if (x !== '') b[k] = x; if (b.decimals) b.decimals = +b.decimals; const r = await V.post(`/api/admin/crypto/assets/${a.id}/networks`, b);
          const fresh = await V.get(`/api/admin/crypto/assets/${a.id}/settings`); st.asset.networks = fresh.asset.networks; st.wallets = fresh.wallets; if (!selNet) selNet = r.network.id; toast('Network added (disabled)'); paintNetwork(); paintAside(); paintWallet(); } });
    }

    /* ---------- Wallet Configuration ---------- */
    let wlNet = null;
    async function refreshData(keep) { const fresh = await V.get(`/api/admin/crypto/assets/${asset().id}/settings`); st.wallets = fresh.wallets; st.reqs = fresh.change_requests; st.hist = fresh.history; st.asset.networks.forEach(n => { const f = fresh.asset.networks.find(x => x.id === n.id); if (f) Object.assign(n, f); }); paintAside(); if (!keep) paintWallet(); }
    function paintWallet() {
      const a = asset(), host = $('#pn-wallet', v); forms.splice(0, forms.length, ...forms.filter(f => !f.wl));
      if (!a.networks.length) { host.innerHTML = '<div class="empty wr-empty">Add a network in Network Settings before configuring a receiving wallet.</div>'; return; }
      if (!wlNet || !netOf(wlNet)) wlNet = selNet || a.networks[0].id; const n = netOf(wlNet), ws = walletsOf(wlNet), live = ws.filter(x => x.status !== 'retired');
      if (!selWallet || !ws.some(x => x.id === selWallet)) selWallet = (live.find(x => x.status === 'active' && x.is_deposit_enabled) || live[0] || ws[0] || {}).id || null;
      const sel = ws.find(x => x.id === selWallet), aw = activeWallet(wlNet), reqs = st.reqs.filter(r => r.asset_network_id === wlNet);
      const verTxt = x => !x.configured ? 'Not configured' : x.verified === true ? 'Custody provider confirms control' : x.verified === false ? 'Not confirmed by the custody provider' : 'Verification unavailable';
      host.innerHTML = `<h3 class="as-h">Wallet configuration</h3>
        ${a.networks.length > 1 ? `<label class="f as-netsel"><span>Network</span><select id="wl-net">${a.networks.map(x => `<option value="${x.id}" ${x.id === wlNet ? 'selected' : ''}>${E(x.network_name)}</option>`).join('')}</select></label>` : `<p class="note">Network: <b>${E(n.network_name)}</b></p>`}
        <section class="as-sec"><h4>Company receiving address</h4>${aw ? `<p class="as-ok">${ico(I.info, 16)} Customers are currently shown <b>${E(aw.label)}</b>.</p>` : '<p class="as-warnb">No active deposit address: customers cannot deposit on this network until a wallet is configured, active and accepting deposits.</p>'}
          ${ws.length ? `<div class="wr-scroll"><table class="wr-tbl as-wt"><thead><tr><th>Wallet</th><th>Address</th><th>Status</th><th>Verification</th><th>Created / updated</th></tr></thead><tbody>${ws.map(x => `<tr class="${x.id === selWallet ? 'is-open' : ''}" data-wsel="${x.id}" tabindex="0" aria-selected="${x.id === selWallet}">
            <td data-l="Wallet"><b>${E(x.label)}</b><small>${E(label(x.wallet_type))}</small>${x.id === (aw && aw.id) ? '<small class="as-live">Active for customer deposits</small>' : x.status === 'retired' ? '<small>Replaced (historical)</small>' : ''}</td>
            <td data-l="Address">${x.configured ? `<span class="wr-full">${mono(x.public_address)}${copyBtn(x.public_address)}</span>` : '<b>NOT_CONFIGURED</b>'}</td><td data-l="Status">${chip(x.status)}<small>${x.is_deposit_enabled ? 'Deposits on' : 'Deposits off'}</small></td>
            <td data-l="Verification">${E(verTxt(x))}</td><td data-l="Dates">${dt(x.created_at)}<small>${x.updated_at ? 'updated ' + dt(x.updated_at) : ''}</small></td></tr>`).join('')}</tbody></table></div>` : '<p class="note">No wallet record exists for this network.</p>'}</section>
        ${sel && sel.status !== 'retired' ? policySection(sel, n) + qrSection(sel, n) : sel ? '<p class="note">The selected wallet is retired: it is kept as a historical record and cannot be changed.</p>' : ''}
        ${sel ? changeSection(sel, n, reqs) : ''}${connSection(sel, n)}<section class="as-sec" id="as-hist"><h4>Wallet change history</h4>${histTable(ws)}</section>`;
      bindWallet(host, sel, n, reqs);
      if (P.get('section') === 'history') { P.delete('section'); setTimeout(() => $('#as-hist', host).scrollIntoView({ behavior: 'smooth' }), 50); }
    }
    const policySection = (x, n) => `<section class="as-sec"><h4>Wallet policy</h4><form id="f-pol" novalidate class="as-nf"><p class="as-alert wr-err" role="alert" hidden></p><div class="as-cols">
        ${field('wallet_label', 'Wallet label', x.label, { max: 80, ro: !wmanage })}${field('status', 'Wallet status', x.status, { type: 'select', o: [['active', 'Active'], ['inactive', 'Inactive'], ['maintenance', 'Maintenance'], ['manual_review', 'Manual review']], ro: !wmanage })}
        ${field('minimum_deposit', 'Minimum deposit (this wallet)', x.minimum_deposit, { im: 'decimal', ro: !wmanage, hint: `Default from the network settings: ${E(n.min_deposit || '0')}` })}${field('confirmation_requirement', 'Required confirmations (this wallet)', x.required_confirmations, { im: 'numeric', ro: !wmanage })}
        ${field('risk_review_threshold', 'Second-approval threshold', x.risk_review_threshold || '', { im: 'decimal', ro: !wmanage, hint: 'Deposits at or above this amount need a second person’s approval. Empty = none.' })}${field('created', 'Date created', dt(x.created_at), { ro: 1 })}</div>
        <div class="as-cols1">${sw('is_deposit_enabled', 'Accept customer deposits on this wallet', x.is_deposit_enabled, 'Requires an active wallet whose address the custody provider confirms it controls.')}</div>
        ${wmanage ? `<div class="as-cols">${field('reason', 'Reason for the change', '', { type: 'textarea', rows: 2, max: 300, hint: 'At least 10 characters.' })}${field('code', 'Your 2FA code', '', { code: 1, req: 1 })}</div><div class="as-act"><button class="btn ghost" type="button" data-reset>Cancel</button><button class="btn" type="submit">Save changes</button></div>` : ''}</form></section>`;
    const qrSection = (x, n) => `<section class="as-sec" id="as-qr"><h4>Deposit QR code</h4>${x.qr_available ? `<div class="as-qrw"><div class="as-qrbox"><img class="as-qr" id="qr-img" width="220" height="220" src="/api/admin/crypto/wallets/${x.id}/qr.svg?t=${qrStamp}" alt="QR code of the ${E(a_sym())} ${E(n.network_name)} deposit address ${E(x.public_address)}"></div>
        <div><span class="st st-completed">Generated by Vaulta</span><p class="note">Generated now from the validated address on file. It encodes exactly this text:</p><div class="wr-full">${mono(x.public_address)}${copyBtn(x.public_address)}</div>
        <p class="note">Network: ${E(n.network_name)}. The code carries the bare address (no amount or memo), the same payload customers receive. If this wallet is replaced, its QR code is replaced with it.</p>
        <div class="as-act left"><a class="btn ghost sm" href="/api/admin/crypto/wallets/${x.id}/qr.svg?download=1" download>Download QR (SVG)</a><button class="btn ghost sm" type="button" id="qr-re">Regenerate preview</button></div>
        <p class="note as-small">Uploading an external QR image is not supported. Vaulta only serves codes it generates from the stored address, so a QR code can never silently point to a different address.</p></div></div>` : '<p class="note">A QR code becomes available once this wallet has a configured address.</p>'}</section>`;
    const a_sym = () => asset().symbol;
    const changeSection = (x, n, reqs) => `<section class="as-sec" id="as-chg"><h4>Change wallet address</h4><p class="note">A change never replaces the live address immediately. It needs a <b>second administrator</b> to approve with a fresh 2FA code; the old address stays in the history and the new wallet starts inactive until enabled.</p>
        ${wmanage && x.status !== 'retired' ? '<div id="chg-host"><button class="btn" type="button" id="chg-open">' + (x.configured ? 'Change address' : 'Set address') + '</button></div>' : ''}
        <h5>Pending changes</h5>${reqs.filter(r => r.status === 'pending').length ? pendingTable(reqs.filter(r => r.status === 'pending')) : '<p class="note">No pending changes for this network.</p>'}</section>`;
    const connSection = (x, n) => `<section class="as-sec"><h4>Wallet connection and verification</h4><div class="as-kvs wide"><div class="as-kv"><span>Custody provider</span><b>${E(x ? label(x.provider) : 'External custody')}</b></div><div class="as-kv"><span>Environment</span><b>${E(st.env)} · ${E(st.mode)} providers</b></div>
        <div class="as-kv"><span>Custody confirms control</span><b>${!x || !x.configured ? 'Not configured' : x.verified === true ? 'Yes' : x.verified === false ? 'No' : 'Unavailable'}</b></div></div>
        <p class="note">${st.mode === 'live' ? 'Control of an address is confirmed by your custody provider, never by Vaulta.' : '<b>Test infrastructure:</b> confirmation comes from a development mock provider, so no real wallet is contacted and no real funds are involved.'} Private keys, seed phrases and signing credentials are never entered, stored or shown here: signing happens inside the external custody system.</p>
        <button class="btn ghost sm" type="button" id="conn-re">Re-check</button></section>`;
    const histTable = ws => { const ids = new Set(ws.map(x => x.id)), ev = st.hist.filter(e => ids.has(e.wallet_id)); return ev.length ? `<div class="wr-scroll"><table class="wr-tbl"><thead><tr><th>When</th><th>Event</th><th>Old address</th><th>New address</th><th>Admin / approver</th><th>Reason</th></tr></thead><tbody>${ev.map(e => `<tr><td data-l="When">${dt(e.created_at)}</td><td data-l="Event">${E(label(e.event_type))}</td><td data-l="Old">${e.old_address ? mono(short(e.old_address, 10, 6)) : '—'}</td><td data-l="New">${e.new_address ? mono(short(e.new_address, 10, 6)) : '—'}</td><td data-l="Admin">${E(e.actor_email || e.actor_id || 'system')} / ${E(e.approver_email || e.approver_id || '—')}</td><td data-l="Reason">${E(e.reason || '')}</td></tr>`).join('')}</tbody></table></div>` : '<p class="note">No wallet changes recorded for this network.</p>'; };
    function bindWallet(host, sel, n, reqs) {
      const wn = $('#wl-net', host); if (wn) wn.onchange = e => { if (forms.some(f => f.wl && f.dirty()) && !confirm('Discard your unsaved wallet changes?')) { e.target.value = wlNet; return; } wlNet = +e.target.value; selNet = wlNet; selWallet = null; paintAside(); paintWallet(); };
      $$('[data-wsel]', host).forEach(tr => { const go = e => { if (e.target.closest('[data-copy]')) return; if (forms.some(f => f.wl && f.dirty()) && !confirm('Discard your unsaved wallet changes?')) return; selWallet = +tr.dataset.wsel; paintWallet(); }; tr.onclick = go; tr.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(e); } }; });
      const pol = $('#f-pol', host);
      if (pol && sel) {
        const m = model(pol); m.wl = true; forms.push(m); const rs = pol.querySelector('[data-reset]'); if (rs) rs.onclick = () => m.reset();
        pol.onsubmit = async e => {
          e.preventDefault(); if (!wmanage) return; const o = m.read(), errs = {}; if (!o.wallet_label.trim()) errs.wallet_label = 'A label is required.';
          if (o.minimum_deposit.trim() && !DEC.test(o.minimum_deposit.trim())) errs.minimum_deposit = 'Enter a number such as 0.001.'; if (o.risk_review_threshold.trim() && !DEC.test(o.risk_review_threshold.trim())) errs.risk_review_threshold = 'Enter a number such as 5.';
          if (o.confirmation_requirement.trim() && (!/^\d+$/.test(o.confirmation_requirement.trim()) || +o.confirmation_requirement < 1)) errs.confirmation_requirement = 'Enter a whole number of at least 1.';
          if ((o.reason || '').trim().length < 10) errs.reason = 'Give a reason of at least 10 characters.'; if (!(o.code || '').trim()) errs.code = 'Enter your current 2FA code.';
          if (Object.keys(errs).length) return fail(pol, errs); if (!m.dirty()) return fail(pol, {}, 'No changes to save.');
          const b = pol.querySelector('button[type=submit]'); b.disabled = true; clear(pol);
          try { await V.post(`/api/admin/crypto/wallets/${sel.id}/settings`, { wallet_label: o.wallet_label.trim(), status: o.status, is_deposit_enabled: o.is_deposit_enabled, minimum_deposit: o.minimum_deposit.trim(), confirmation_requirement: o.confirmation_requirement.trim() ? +o.confirmation_requirement : null, risk_review_threshold: o.risk_review_threshold.trim(), reason: o.reason.trim(), code: o.code.trim() });
            toast('Wallet settings saved'); await refreshData(); } catch (er) { const msg = er.message || 'The request failed.'; fail(pol, /2fa|code|authenticat/i.test(msg) ? { code: msg } : /reason/i.test(msg) ? { reason: msg } : {}, msg); } finally { b.disabled = false; }
        };
      }
      const re = $('#qr-re', host); if (re) re.onclick = () => { qrStamp = Date.now(); $('#qr-img', host).src = `/api/admin/crypto/wallets/${sel.id}/qr.svg?t=${qrStamp}`; toast('QR code regenerated from the address on file'); };
      const rc = $('#conn-re', host); if (rc) rc.onclick = () => w.UI.run(rc, async () => { await refreshData(); toast('Custody status re-checked'); });
      const co = $('#chg-open', host); const openChange = () => {
        const hostEl = $('#chg-host', host); if (!hostEl) return; const cur = sel.configured ? sel.public_address : '';
        const el = inlineForm(hostEl, { title: sel.configured ? 'Propose a new address' : 'Set the receiving address', intro: `Network: <b>${E(n.network_name)}</b>. ${n.address_hint ? E(n.address_hint) + '. ' : ''}The address is validated for this network${n.contract_address ? ' and token' : ''} before it is saved.`,
          fields: [{ n: 'address', l: `New ${a_sym()} address`, req: 1, mono: 1 }, { n: 'label', l: 'Label', v: sel.label, req: 1 }, { n: 'reason', l: 'Reason (min 10 characters)', type: 'textarea', req: 1 }, { n: 'code', l: 'Your 2FA code', req: 1, code: 1 }, { n: 'confirm', l: 'I checked this address against the custody system and confirm it is controlled by the company.', type: 'checkbox' }], ok: 'Submit for approval',
          submit: async o => {
            if (!o.confirm) throw new Error('Confirm that you verified the address with the custody system.');
            const chk = ADDR[n.address_format]; if (chk && !chk(o.address)) throw new Error(`That does not look like a valid ${n.network_name} address. ${n.address_hint || ''}`);
            if (cur && o.address === cur) throw new Error('The proposed address is the same as the current address.');
            await V.post('/api/admin/crypto/wallets/change-requests', { asset_network_id: n.id, address: o.address, label: o.label, reason: o.reason, code: o.code, wallet_type: sel.wallet_type }); toast('Change submitted for approval'); await refreshData();
          } });
        const cmp = document.createElement('div'); cmp.className = 'as-cmp'; cmp.innerHTML = `<div><small>Current address</small>${cur ? `<code class="wr-addr">${E(cur)}</code>` : '<b>none</b>'}</div><div><small>Proposed address</small><code class="wr-addr" id="cmp-new">—</code></div>`; el.querySelector('.wr-inline-grid').before(cmp);
        el.elements.address.addEventListener('input', e => { $('#cmp-new', el).textContent = e.target.value.trim() || '—'; }); co && (co.hidden = true); el.querySelector('[data-x]').addEventListener('click', () => { if (co) co.hidden = false; });
      };
      if (co) co.onclick = openChange; if (P.get('change') === '1' && wmanage && co) { P.delete('change'); openChange(); setTimeout(() => $('#as-chg', host).scrollIntoView({ behavior: 'smooth', block: 'start' }), 50); }
      bindPending(host, () => refreshData());
    }

    /* ---------- tabs ---------- */
    const setTab = (t, focus) => {
      tab = t; $$('[data-tab]', v).forEach(b => { const on = b.dataset.tab === t; b.classList.toggle('on', on); b.setAttribute('aria-selected', String(on)); b.tabIndex = on ? 0 : -1; if (on && focus) b.focus(); });
      ['general', 'network', 'wallet'].forEach(k => { $('#pn-' + k, v).hidden = k !== t; });
      try { const p = new URLSearchParams(location.hash.split('?')[1] || ''); p.set('tab', t); history.replaceState(history.state, '', location.pathname + location.search + '#/asset-settings/' + asset().id + '?' + p); } catch (e) { }
    };
    $('.as-tabs', v).addEventListener('click', e => { const b = e.target.closest('[data-tab]'); if (b) setTab(b.dataset.tab); });
    $('.as-tabs', v).addEventListener('keydown', e => { const k = ['general', 'network', 'wallet'], i = k.indexOf(tab); if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { e.preventDefault(); setTab(k[(i + (e.key === 'ArrowRight' ? 1 : 2)) % 3], true); } });
    v.addEventListener('click', e => { const cp = e.target.closest('[data-copy]'); if (cp) w.copyText(cp.dataset.copy, cp); });

    paintSummary(); paintAside(); paintGeneral(); forms[0].gen = true; paintNetwork(); paintWallet();
    if (!wread) $('#tb-wallet', v).hidden = true;
    VNav.guard(() => anyDirty(), 'You have unsaved changes to this asset. Leave this page and discard them?');
    document.title = `VAULTA Admin — ${asset().symbol} Asset Settings`;
  }

  R['asset-settings'] = id => load(v => page(v, id));
});
