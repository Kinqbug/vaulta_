/* VAULTA customer Deposit page (#/deposit) and Withdraw page (#/withdraw).
   Deposit and Withdraw used to be one combined page; they are now two independent routes that share the components below.
   Nothing on either page is hard-coded: assets, logos, networks, addresses, limits, fees, arrival/processing text, instructions and statuses are all read from the
   backend (the admin-managed asset registry and the customer's own ledger). A screenshot is evidence for staff review, never proof of payment, and a withdrawal request is
   shown as "pending review" until the backend itself reports a later state. */
(window.VAULTA_EXT = window.VAULTA_EXT || []).push(({ R, load, V, UI }) => {
  const { $, $$, esc, dt, label, toast, modal, logo, table } = UI;

  /* ================= small shared helpers ================= */
  const P = {
    chev: '<path d="m6 9 6 6 6-6"/>', chevR: '<path d="m9 6 6 6-6 6"/>', arrowL: '<path d="M19 12H5"/><path d="m12 19-7-7 7-7"/>', arrowR: '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>', check: '<path d="m5 12 5 5 9-10"/>',
    scan: '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/><path d="M8 12h8M12 8v8"/>',
    upload: '<path d="m16 16-4-4-4 4"/><path d="M12 12v9"/><path d="M20.4 18.4A5 5 0 0 0 18 9h-1.3A8 8 0 1 0 4 16.3"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>', lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    wallet: '<path d="M3 7a2 2 0 0 1 2-2h13v4"/><path d="M3 7v11a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-9a1 1 0 0 0-1-1H5a2 2 0 0 1-2-2z"/><circle cx="17" cy="14.5" r="1"/>',
    send: '<path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/>', refresh: '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>', snow: '<path d="M12 2v20M2 12h20M5 5l14 14M19 5 5 19"/>',
    alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>', paste: '<rect x="8" y="3" width="8" height="4" rx="1"/><path d="M16 5h2a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h2"/>',
    layers: '<path d="m12 2 10 5-10 5L2 7z"/><path d="m2 12 10 5 10-5M2 17l10 5 10-5"/>'
  };
  const svg = (n, s = 20) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${P[n] || ''}</svg>`;
  const short = (h, a = 10, b = 6) => (h && h.length > a + b + 3 ? h.slice(0, a) + '…' + h.slice(-b) : h || '—');
  const bytes = n => (n < 1048576 ? Math.max(1, Math.round(n / 1024)) + ' KB' : (n / 1048576).toFixed(1) + ' MB');
  const fmt = s => { s = String(s ?? ''); return s.includes('.') ? s.replace(/0+$/, '').replace(/\.$/, '') || '0' : s; };       // display only: money arithmetic always happens on the server
  const money = x => (x === null || x === undefined || x === '') ? '—' : '$' + Number(x).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const lines = t => String(t || '').split(/\n+/).map(x => x.trim()).filter(Boolean);
  const hist_ = window.history;
  let TOKEN = 0;                                                                // invalidates late async results after the customer leaves a page

  /* One asset-icon component. The URL comes from the admin-managed record (a.logo); UI.logo draws a generated badge when there is none or it fails to load. */
  const icon = (a, size = 40, withAlt = false) => {
    const h = logo(a, size);
    return withAlt && a && a.logo ? h.replace('alt=""', `alt="${esc(a.logo_alt || (a.name ? a.name + ' logo' : ''))}"`) : h;
  };
  const groups = list => {
    const m = new Map();
    list.forEach(a => { if (!m.has(a.symbol)) m.set(a.symbol, { symbol: a.symbol, name: a.name, logo: a.logo, logo_alt: a.logo_alt, nets: [] }); m.get(a.symbol).nets.push(a); });
    return [...m.values()];
  };
  const netSub = n => (n.contract_address ? `${n.symbol} token · ${short(n.contract_address, 8, 4)}` : `Native ${n.symbol} network`);

  /* Statuses, worded for customers. A withdrawal request is "Pending review" until the backend says otherwise; nothing is ever called Completed early. */
  const DEP_CHAIN = { detected: 'wait', validating: 'wait', unconfirmed: 'wait', confirming: 'wait', confirmed: 'wait', risk_review: 'wait', manual_review: 'wait', credited: 'ok', rejected: 'bad', failed: 'bad', wrong_network: 'bad', unsupported_asset: 'bad', duplicate: 'bad', reorged: 'bad' };
  function stat(it) {
    if (it.kind === 'withdrawal') {
      return ({ pending_approval: ['wait', 'Pending review'], approved: ['wait', 'Approved'], signing: ['wait', 'Processing'], broadcast: ['wait', 'Processing'], confirming: ['wait', 'Processing'], completed: ['ok', 'Completed'], rejected: ['bad', 'Rejected'], failed: ['bad', 'Failed'], cancelled: ['bad', 'Cancelled'] })[it.status] || ['wait', label(it.status)];
    }
    if (it.source === 'request') return ({ pending: ['wait', 'Pending review'], approved: ['ok', 'Completed'], rejected: ['bad', 'Rejected'], cancelled: ['bad', 'Cancelled'] })[it.status] || ['wait', label(it.status)];
    const t = DEP_CHAIN[it.status] || 'wait';
    return [t, t === 'ok' ? 'Completed' : t === 'bad' ? label(it.status) : ['detected', 'validating', 'unconfirmed', 'confirming'].includes(it.status) ? 'Pending confirmation' : 'Pending review'];
  }
  const badge = it => { const [t, x] = stat(it); return `<span class="fs ${t}"><i></i>${esc(x)}</span>`; };

  function steps(names, cur, bad) {
    return `<ol class="trk" aria-label="Progress">${names.map((n, i) => `<li class="${i < cur ? 'done' : i === cur ? (bad ? 'bad' : 'cur') : ''}"><span class="dot" aria-hidden="true">${i < cur ? '✓' : bad && i === cur ? '✕' : i + 1}</span><span>${esc(n)}</span></li>`).join('')}</ol>`;
  }
  function tracker(it) {
    if (it.kind === 'deposit' && it.source === 'request') {
      if (it.status === 'approved') return steps(['Submitted', 'In review', 'Approved', 'Credited'], 4, false);
      if (it.status === 'rejected') return steps(['Submitted', 'In review', 'Rejected', 'Credited'], 2, true);
      if (it.status === 'cancelled') return steps(['Submitted', 'Cancelled'], 1, true);
      return steps(['Submitted', 'In review', 'Decision', 'Credited'], 1, false);
    }
    if (it.kind === 'withdrawal') {
      const at = { pending_approval: 0, approved: 1, signing: 2, broadcast: 2, confirming: 2, completed: 4 }[it.status], N = ['Requested', 'Approved', 'Processing', 'Completed'];
      return at === undefined ? steps(N, it.status === 'cancelled' ? 0 : 1, true) : steps(N, at, false);
    }
    return '';
  }

  /* ---- copy to clipboard, with a fallback for browsers/contexts without the async clipboard API ---- */
  async function copyText(text, btn) {
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch (e) {
      const ta = document.createElement('textarea'); ta.value = text; ta.setAttribute('readonly', ''); ta.style.cssText = 'position:fixed;opacity:0'; document.body.append(ta); ta.select();
      try { ok = document.execCommand('copy'); } catch (e2) { ok = false; } ta.remove();
    }
    const live = $('#fp-live'); if (live) live.textContent = ok ? 'Copied to clipboard' : 'Copy failed. Select the text and copy it manually.';
    if (btn && ok) { const old = btn.innerHTML; btn.innerHTML = svg('check'); setTimeout(() => { btn.innerHTML = old; }, 1500); }
    if (!ok) toast('Select the text and copy it manually');
    return ok;
  }

  /* ================= accessible custom listbox (asset and network pickers; a native <select> cannot show logos) ================= */
  const ddOpt = i => `<span class="dd-o">${icon(i.logo, 40, true)}<span class="dd-t"><b>${esc(i.title)}</b>${i.sub ? `<small>${esc(i.sub)}</small>` : ''}</span></span>`;
  function dd(id, items, value, aria, ph) {
    const cur = items.find(i => i.value === value);
    return `<div class="dd" data-dd="${id}"><button type="button" class="dd-b" id="${id}-b" aria-haspopup="listbox" aria-expanded="false" aria-controls="${id}-l" aria-label="${esc(aria)}${cur ? ': ' + esc(cur.title) : ''}" ${items.length ? '' : 'disabled'}>${cur ? ddOpt(cur) : `<span class="dd-ph">${esc(ph || 'Select')}</span>`}${svg('chev', 18)}</button>
      <ul class="dd-l" id="${id}-l" role="listbox" aria-labelledby="${id}-b" hidden>${items.map(i => `<li role="option" tabindex="-1" data-v="${esc(i.value)}" aria-selected="${i.value === value}">${ddOpt(i)}${i.value === value ? svg('check', 18) : ''}</li>`).join('')}</ul></div>`;
  }
  const closeDD = except => $$('.dd-b[aria-expanded=true]').forEach(b => { if (!except || b.closest('.dd') !== except) { b.setAttribute('aria-expanded', 'false'); $('.dd-l', b.closest('.dd')).hidden = true; } });
  function openDD(box, focus) {
    closeDD(box); const b = $('.dd-b', box), l = $('.dd-l', box); b.setAttribute('aria-expanded', 'true'); l.hidden = false;
    if (focus) { const t = $('[aria-selected=true]', l) || $('li', l); if (t) t.focus(); }
  }
  /* returns true when the event was a listbox interaction; onPick(id, value) performs the selection */
  function ddClick(e, onPick) {
    const b = e.target.closest('.dd-b');
    if (b) { const box = b.closest('.dd'); if (b.getAttribute('aria-expanded') === 'true') closeDD(); else openDD(box, false); return true; }
    const li = e.target.closest('.dd-l li');
    if (li) { const box = li.closest('.dd'); closeDD(); onPick(box.dataset.dd, li.dataset.v); return true; }
    return false;
  }
  function ddKey(e, onPick) {
    const b = e.target.closest('.dd-b'), li = e.target.closest('.dd-l li');
    if (b && ['ArrowDown', 'ArrowUp'].includes(e.key)) { e.preventDefault(); openDD(b.closest('.dd'), true); return; }
    if (b && (e.key === 'Escape' || e.key === 'Tab')) { closeDD(); return; }
    if (!li) return;
    const all = $$('li', li.parentElement), i = all.indexOf(li), box = li.closest('.dd'), btn = $('.dd-b', box);
    if (e.key === 'ArrowDown') { e.preventDefault(); (all[i + 1] || all[0]).focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); (all[i - 1] || all[all.length - 1]).focus(); }
    else if (e.key === 'Home') { e.preventDefault(); all[0].focus(); } else if (e.key === 'End') { e.preventDefault(); all[all.length - 1].focus(); }
    else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); closeDD(); btn.focus(); onPick(box.dataset.dd, li.dataset.v); }
    else if (e.key === 'Escape') { e.preventDefault(); closeDD(); btn.focus(); } else if (e.key === 'Tab') closeDD();
  }
  if (!window.__vaultaDDBound) { window.__vaultaDDBound = true; document.addEventListener('click', e => { if (!e.target.closest('.dd')) closeDD(); }); }

  /* ================= shared page pieces ================= */
  const COINS = `<svg class="coins" viewBox="0 0 220 170" aria-hidden="true" focusable="false"><defs><radialGradient id="fpcg" cx="32%" cy="28%" r="85%"><stop offset="0" stop-color="#5a5a5a"/><stop offset="1" stop-color="#050505"/></radialGradient></defs>
    <ellipse cx="110" cy="92" rx="104" ry="38" fill="none" stroke="#cfcfcf" transform="rotate(-18 110 92)"/>
    <circle cx="128" cy="58" r="46" fill="url(#fpcg)" stroke="#9a9a9a"/><text x="128" y="77" text-anchor="middle" font-size="54" font-weight="800" fill="#fff" font-family="Manrope,Arial,sans-serif">₿</text>
    <circle cx="154" cy="118" r="42" fill="url(#fpcg)" stroke="#9a9a9a"/><path d="M154 94l-17 25 17 10 17-10zM154 124l-17-5 17 25 17-25z" fill="#fff" fill-opacity=".92"/>
    <circle cx="64" cy="120" r="30" fill="url(#fpcg)" stroke="#9a9a9a"/><text x="64" y="132" text-anchor="middle" font-size="34" font-weight="800" fill="#fff" font-family="Manrope,Arial,sans-serif">T</text></svg>`;
  const header = (title, back, text) => `<div class="fp-head"><div><h1>${esc(title)}</h1><p>${esc(text)}</p></div>${COINS}</div>`;
  const step = (n, title, desc, body, id, bare) => `<section class="${bare ? 'step' : 'fp-card step'}" ${id ? `id="${id}"` : ''}><div class="st-h"><span class="num" aria-hidden="true">${n}</span><div><h2>${esc(title)}</h2><p>${esc(desc)}</p></div></div><div class="st-b">${body}</div></section>`;
  const note = (html, kind, ic) => `<div class="note-i ${kind || ''}" ${kind === 'warn' || kind === 'err' ? 'role="alert"' : ''}>${svg(ic || (kind === 'warn' ? 'alert' : 'info'), 18)}<div>${html}</div></div>`;
  const testBanner = cfg => (cfg.banner ? `<div class="banner" role="status"><b>${esc(cfg.banner)}</b></div>` : '');
  /* admin-configured notices for the selected asset-network, shown identically on both pages */
  const notices = n => [n.notice, n.network_notice].filter(Boolean).map(t => note(esc(t), 'warn')).join('');

  /* ----- history (desktop table + mobile cards), built from real transaction records ----- */
  function history(items, kind, title, empty) {
    const rows = items.filter(i => i.kind === kind).slice(0, 5), isW = kind === 'withdrawal', when = it => dt(it.created_at);
    const tbl = `<table><thead><tr><th scope="col">Date</th><th scope="col">Asset</th><th scope="col">Amount</th><th scope="col">${isW ? 'Address' : 'Network'}</th><th scope="col">${isW ? 'Network' : 'Transaction'}</th><th scope="col">Status</th><th scope="col"><span class="sr">Details</span></th></tr></thead><tbody>
      ${rows.map(it => `<tr><td>${esc(when(it))}</td><td><span class="as">${icon(it, 28)}${esc(it.asset)}</span></td><td class="num2">${esc(fmt(it.amount))} ${esc(it.asset)}</td>
        <td ${isW ? `class="mono" title="${esc(it.destination)}"` : ''}>${esc(isW ? short(it.destination, 8, 6) : it.network)}</td>
        <td ${!isW ? `class="mono" title="${esc(it.tx_hash || '')}"` : ''}>${esc(isW ? it.network : it.tx_hash ? short(it.tx_hash, 8, 6) : '—')}</td><td>${badge(it)}</td>
        <td><button type="button" class="det" data-det="${esc(it.ref)}" aria-label="View details of ${esc(it.ref)}">${svg('chevR', 18)}</button></td></tr>`).join('')}</tbody></table>`;
    const cards = `<ul class="hist-c">${rows.map(it => `<li><button type="button" class="row" data-det="${esc(it.ref)}" aria-label="View details of ${esc(it.ref)}">${icon(it, 36)}<span class="t"><b>${esc(fmt(it.amount))} ${esc(it.asset)}</b><small>${esc(when(it))} · ${esc(isW ? short(it.destination, 8, 6) : it.network)}</small></span><span class="r">${badge(it)}</span></button></li>`).join('')}</ul>`;
    return `<div class="hist-h"><h2>${esc(title)}</h2><a href="#/transactions">View All ${svg('arrowR', 16)}</a></div><div class="hist" id="fp-hist">${rows.length ? tbl + cards : `<div class="empty">${esc(empty)}</div>`}</div>`;
  }

  function details(it) {
    const isW = it.kind === 'withdrawal', dl = [['Reference', esc(it.ref)], ['Asset', `<span style="display:inline-flex;gap:8px;align-items:center">${icon(it, 22)}${esc(it.asset_name || it.asset)} (${esc(it.asset)})</span>`], ['Network', esc(it.network)],
      ['Amount', `<b>${esc(fmt(it.amount))} ${esc(it.asset)}</b>`], ...(isW ? [['Fee', `${esc(fmt(it.fee))} ${esc(it.asset)}`], ['Total deducted', `${esc(fmt(it.total_debit))} ${esc(it.asset)}`], ['Destination', `<code>${esc(it.destination)}</code>`]] : []),
      ...(isW ? (it.usd_debit ? [['Deducted from Account Balance', `<b>${esc(money(it.usd_debit))}</b> at 1 ${esc(it.asset)} ≈ ${esc(money(it.rate))}`]] : []) : (it.usd_credit ? [['Credited to Account Balance', `<b>${esc(money(it.usd_credit))}</b> at 1 ${esc(it.asset)} ≈ ${esc(money(it.rate))}`]] : [])),
      ['Transaction hash', it.tx_hash ? `<code>${esc(it.tx_hash)}</code>` : 'Not available yet'], ['Date', esc(dt(it.created_at))], ['Status', badge(it)]];
    const d = modal(`${isW ? 'Withdrawal' : 'Deposit'} details`, `<div class="cf"><dl>${dl.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>${tracker(it)}
      ${it.rejection_reason ? `<p class="note-i err">${svg('alert', 18)}<span><b>Reason:</b> ${esc(it.rejection_reason)}</span></p>` : ''}
      ${isW && it.status === 'pending_approval' ? note('This request has been recorded and is waiting for review. <b>Nothing has been sent to the blockchain yet.</b>') : ''}
      <div class="actions" style="justify-content:flex-start;gap:8px;flex-wrap:wrap">${it.source === 'request' && it.has_screenshot ? '<button class="btn ghost sm" data-shot type="button">View screenshot</button>' : ''}
      ${it.source === 'request' && it.can_cancel ? '<button class="btn ghost sm" data-cx type="button">Cancel request</button>' : ''}${isW && it.status === 'pending_approval' ? '<button class="btn ghost sm" data-wc type="button">Cancel withdrawal</button>' : ''}<button class="btn ghost sm" data-x type="button">Close</button></div></div>`);
    d.addEventListener('click', async e => {
      if (e.target.closest('[data-shot]')) modal('Your transaction screenshot', `<img src="/api/crypto/deposit-requests/${encodeURIComponent(it.ref)}/evidence" alt="Your uploaded transaction screenshot" style="max-width:100%;border-radius:10px">`, { wide: true });
      if (e.target.closest('[data-cx]')) { try { await V.post(`/api/crypto/deposit-requests/${encodeURIComponent(it.ref)}/cancel`); d.close(); toast('Request cancelled'); R.deposit(); } catch (er) { toast(er.message); } }
      if (e.target.closest('[data-wc]')) { try { await V.post(`/api/crypto/withdrawals/${encodeURIComponent(it.ref)}/cancel`); d.close(); toast('Withdrawal cancelled'); R.withdraw(); } catch (er) { toast(er.message); } }
    });
  }
  const itemByRef = (items, ref) => items.find(i => i.ref === ref);

  /* ================= DEPOSIT PAGE ================= */
  const D = { cfg: {}, assets: [], sym: null, net: null, intent: null, ierr: null, busy: false, itok: 0, file: null, key: null, items: [], src: [], done: null };
  const dCur = () => D.assets.find(a => a.ledger_code === D.net);
  const dIntentOk = () => D.intent && D.intent.ledger_code === D.net;       // an address/QR is only ever shown for the CURRENT asset + network

  function dTop() {
    const gs = groups(D.assets), g = gs.find(x => x.symbol === D.sym);
    return step(1, 'Select Cryptocurrency', 'Choose the asset you want to deposit.', dd('d-asset', gs.map(x => ({ value: x.symbol, title: `${x.name} (${x.symbol})`, logo: x })), D.sym, 'Cryptocurrency', 'Select a cryptocurrency'), 'd-s1') +
      (g ? step(2, 'Select Network', 'Choose the blockchain network for your deposit.', dd('d-net', g.nets.map(n => ({ value: n.ledger_code, title: n.network_name, sub: netSub(n), logo: g })), D.net, 'Network', 'Select a network'), 'd-s2') : '');
  }

  function dAddress() {
    const c = dCur(); let body;
    if (!D.assets.length) body = '<div class="state"><b>Deposits are temporarily unavailable</b>No cryptocurrency is enabled for deposits right now. Please check back soon.</div>';
    else if (!c) body = '<div class="state">Select a cryptocurrency and network to see your deposit address.</div>';
    else if (D.busy) body = '<div class="state" role="status"><span class="spin" aria-hidden="true"></span>Preparing your deposit address…</div>';
    else if (D.ierr) {
      const kyc = D.ierr.code === 'kyc_required';
      body = `<div class="state" role="alert"><b>${kyc ? 'Verify your identity first' : 'Deposit address unavailable'}</b>${esc(D.ierr.message)}<br>${kyc ? '<a class="btn sm" href="#/settings">Open verification</a>' : '<button class="btn ghost sm" type="button" data-retry-d>Try again</button>'}</div>`;
    } else if (dIntentOk()) {
      const i = D.intent;
      body = `<div class="addr-panel"><div class="qr-box"><img src="/api/crypto/deposit-intents/${encodeURIComponent(i.ref)}/qr.svg" alt="QR code of your ${esc(i.asset)} deposit address on ${esc(i.network_name)}" width="160" height="160"></div>
        <div class="addr-main"><div><span class="lbl">Your ${esc(i.asset)} Deposit Address</span><div class="addr-row"><code id="d-addr" tabindex="0">${esc(i.deposit_address)}</code><button type="button" class="copy" data-copy="${esc(i.deposit_address)}" aria-label="Copy ${esc(i.asset)} deposit address">${svg('copy')}</button></div></div>
        ${i.memo ? `<div><span class="lbl">Memo / reference (include it so we can match your deposit)</span><div class="addr-row"><code>${esc(i.memo)}</code><button type="button" class="copy" data-copy="${esc(i.memo)}" aria-label="Copy memo">${svg('copy')}</button></div></div>` : ''}
        ${i.contract_address ? `<div><span class="lbl">${esc(i.asset)} token contract</span><div class="addr-row"><code>${esc(i.contract_address)}</code></div></div>` : ''}
        <div class="facts"><div>${svg('refresh', 22)}<span><small>Minimum Deposit</small><b>${esc(fmt(i.minimum_deposit))} ${esc(i.asset)}</b></span></div><div>${svg('clock', 22)}<span><small>Estimated Arrival</small><b>${esc(c.estimated_arrival)}</b></span></div></div></div></div>
        ${note(`<b>Warning:</b> ${esc(i.warning)}`, 'warn')}`;
    }
    return step(3, 'Deposit Address', c ? `Send only ${c.symbol} on the ${c.network_name} network to this address. Sending any other asset or using another network may result in permanent loss of funds.` : 'Your personal Vaulta deposit address appears here.', body, 'd-s3');
  }

  function dProof() {
    if (!dIntentOk()) return '';              // no valid address, so nothing to prove: the customer cannot proceed as though one existed
    const i = D.intent, c = dCur(), req = D.cfg.proof ? D.cfg.proof.required : true, max = D.cfg.proof ? D.cfg.proof.max_bytes : 5242880;
    if (D.done) return step(4, 'Upload Proof of Transaction', 'Your deposit was submitted.', `<div class="okpanel"><div class="tick">${svg('check', 26)}</div><b>Submitted for review</b><p>Reference <b>${esc(D.done.ref)}</b>. Our team will verify the transfer on the blockchain. Your Account Balance is credited (in US dollars, at the market rate) only after verification and approval, so it shows as <b>Pending review</b> until then.</p><button class="btn ghost sm" type="button" data-again>Submit another proof</button></div>`, 'd-s4');
    return step(4, 'Upload Proof of Transaction', `Please upload a screenshot of your crypto transaction (TXID, wallet transfer confirmation or exchange receipt).${req ? '' : ' This is optional.'}`,
      `<form id="pf" class="fp-form" novalidate><div class="drop" id="drop" tabindex="0" role="button" aria-label="Upload a screenshot of your transaction. Press Enter to choose a file.">
        <input type="file" id="shot" accept="image/png,image/jpeg,image/webp" hidden>
        <div id="dropmsg">${svg('upload', 30)}<b>Tap to upload screenshot</b><small>PNG, JPG or WebP (Max ${bytes(max)}) · must show the amount, network and transaction hash</small></div>
        <div id="prev" class="prev" hidden><img id="previmg" alt="Screenshot preview"><div class="pm"><span id="pname"></span><div><button type="button" class="btn ghost sm" id="repl">Replace</button><button type="button" class="btn ghost sm" id="rmv">Remove</button></div></div></div></div>
        <div class="f-row" style="margin-top:14px"><label class="f">Amount sent (${esc(i.asset)})<input name="amount" inputmode="decimal" autocomplete="off" required placeholder="Min ${esc(fmt(i.minimum_deposit))}"></label>
        <label class="f"><span>Transaction hash (TXID) <small>if available</small></span><input name="tx_hash" autocomplete="off" spellcheck="false" placeholder="Paste the transaction ID"></label></div>
        <div class="f-row"><label class="f"><span>Sending address <small>optional</small></span><input name="sender_address" autocomplete="off" spellcheck="false"></label><label class="f"><span>Note for our team <small>optional</small></span><input name="note" maxlength="500" autocomplete="off"></label></div>
        <div class="sumbox" aria-live="polite"><b>Deposit summary</b><dl><dt>Asset</dt><dd>${icon(c, 18)} ${esc(i.asset)}</dd><dt>Network</dt><dd>${esc(i.network_name)}</dd><dt>Amount</dt><dd id="sa">—</dd><dt>To</dt><dd><code>${esc(short(i.deposit_address))}</code></dd><dt>Screenshot</dt><dd id="ss">Not attached</dd><dt>Transaction hash</dt><dd id="sh">Not provided</dd></dl></div>
        <div id="prog" hidden><div class="pbar"><i id="pfill"></i></div><small id="ptxt">Uploading…</small></div>
        <p class="neg" id="pm" role="alert"></p><button class="btn" id="psub" type="submit">Submit deposit for review</button>
        <p class="limits" style="margin-top:10px">Your Account Balance is credited (in US dollars, at the market rate) only after our team verifies the transaction. A screenshot alone is not proof of payment.</p></form>`, 'd-s4');
  }

  function dInfo() {
    const c = dCur(), sym = c ? c.symbol : 'the selected asset';
    const li = [`Send only ${sym} to this address.`, c ? `Make sure you select the correct network (${c.network_name}).` : 'Make sure you select the correct network.',
      `Deposits are credited only after our team verifies the transfer${c ? ` and the required network confirmations (${c.required_confirmations})` : ''}.`,
      `Approved deposits are converted to US dollars at the market rate and added to your Account Balance. They do not appear as crypto holdings.`,
      c ? `Minimum deposit amount is ${fmt(c.minimum_deposit)} ${c.symbol}${c.max_deposit ? `; maximum ${fmt(c.max_deposit)} ${c.symbol}` : ''}.` : null, ...(c ? lines(c.instructions) : [])].filter(Boolean);
    return `<div class="fp-info one"><section class="icard" aria-labelledby="d-ii"><h3 id="d-ii">${svg('info', 22)} Important Information</h3><ul>${li.map(x => `<li>${esc(x)}</li>`).join('')}</ul>${c ? notices(c) : ''}</section></div>`;
  }

  /* optional extras from the old combined page are kept: verified sending addresses (automatic matching) and the development-only simulator */
  function advanced(src) {
    const nets = [...new Map(D.assets.map(a => [a.network, a.network_name])).entries()];
    return `<details class="box"><summary><b>Automatic crediting (optional)</b> — verified sending addresses</summary>
      <p class="note">Deposits from an address you have proven you control can be matched to you automatically, which speeds up review. This is optional: the screenshot flow above always works.</p>
      ${src.length ? src.map(s => `<div style="padding:10px 0;border-bottom:1px solid var(--border)"><div class="addr">${esc(s.address)}</div><small>${esc(label(s.network))} · ${esc(label(s.status))}</small>
        ${s.status === 'pending' ? `<form class="vf" data-id="${s.id}" style="margin-top:8px"><label class="f">Challenge to sign<input readonly value="${esc(s.challenge)}"></label><label class="f">Signature<input name="signature" required autocomplete="off"></label><button class="btn sm">Verify ownership</button>${D.cfg.provider_mode === 'mock' ? ` <button class="btn ghost sm" type="button" data-sign="${s.id}" data-addr="${esc(s.address)}" data-ch="${esc(s.challenge)}">Sign (test only)</button>` : ''}<span class="neg" role="alert"></span></form>` : ''}
        <button class="btn ghost sm" data-rev="${s.id}" type="button" style="margin-top:6px">Remove</button></div>`).join('') : '<div class="empty">No addresses yet.</div>'}
      <form id="srcf" style="margin-top:12px"><div class="f-row"><label class="f">Network<select name="network">${nets.map(([c, n]) => `<option value="${esc(c)}">${esc(n)}</option>`).join('')}</select></label><label class="f">Your wallet address<input name="address" required autocomplete="off" spellcheck="false"></label></div><p class="neg" id="srcm" role="alert"></p><button class="btn sm">Add address</button></form></details>`;
  }
  const devPanel = () => (D.cfg.provider_mode !== 'mock' || D.cfg.environment !== 'development') ? '' : `<details class="box"><summary><b>Test tools</b> (development only)</summary><p class="note">Simulates an on-chain payment on the in-memory test chain. No real funds exist.</p>
      <form id="devf"><div class="f-row"><label class="f">Amount<input name="amount" value="100" required></label><label class="f">Blocks to mine<input name="confirmations" value="12" inputmode="numeric"></label>
      <label class="f">Sender<select name="from"><option value="verified">My verified address</option><option value="unknown">An unknown address</option></select></label></div><p class="neg" id="devm" role="alert"></p><button class="btn sm">Simulate payment</button></form></details>`;

  const dPaint = which => {
    const set = (id, html) => { const e = $('#' + id); if (e) e.innerHTML = html; };
    if (!which || which.includes('top')) set('d-top', dTop());
    if (!which || which.includes('addr')) set('d-addr-wrap', dAddress());
    if (!which || which.includes('proof')) { set('d-proof', dProof()); bindProof(); }
    if (!which || which.includes('info')) set('d-info', dInfo());
    if (!which || which.includes('hist')) set('d-hist', history(D.items, 'deposit', 'Recent Deposits', 'No deposits yet. Your deposits will appear here.'));
  };

  async function dLoadIntent() {
    const code = D.net, tok = ++D.itok; D.intent = null; D.ierr = null; D.file = null; D.key = null; D.done = null;
    if (!code) { dPaint(['addr', 'proof', 'info']); return; }
    D.busy = true; dPaint(['addr', 'proof', 'info']);
    try { const r = await V.post('/api/crypto/deposit-intents', { ledger_code: code }); if (tok !== D.itok) return; D.intent = r.intent; }      // the server re-uses the customer's open intent: no duplicate addresses
    catch (e) { if (tok !== D.itok) return; D.ierr = e; }
    D.busy = false; dPaint(['addr', 'proof', 'info']);
  }

  function dPick(id, v) {
    if (id === 'd-asset') { D.sym = v; const g = groups(D.assets).find(x => x.symbol === v); D.net = g ? g.nets[0].ledger_code : null; } else if (id === 'd-net') D.net = v;
    dPaint(['top']); dLoadIntent(); const b = $('#' + id + '-b'); if (b) b.focus();
  }

  function bindProof() {
    const f = $('#pf'); if (!f) return;
    const drop = $('#drop'), input = $('#shot'), err = $('#pm'), i = D.intent, max = D.cfg.proof ? D.cfg.proof.max_bytes : 5242880, types = D.cfg.proof ? D.cfg.proof.types : ['image/jpeg', 'image/png', 'image/webp'];
    const sum = () => { const d = UI.formData(f); $('#sa').textContent = d.amount ? `${d.amount} ${i.asset}` : '—'; $('#sh').textContent = d.tx_hash ? short(d.tx_hash) : 'Not provided'; $('#ss').textContent = D.file ? `${D.file.name} (${bytes(D.file.size)})` : 'Not attached'; };
    const setFile = file => {                 // client checks are a convenience only: the server re-validates the real file content
      err.textContent = '';
      if (!file) { D.file = null; $('#prev').hidden = true; $('#dropmsg').hidden = false; sum(); return; }
      if (!types.includes(file.type)) { err.textContent = 'Please choose a PNG, JPG or WebP image.'; return; }
      if (file.size > max) { err.textContent = `That file is ${bytes(file.size)}. The maximum is ${bytes(max)}.`; return; }
      if (file.size < 1024) { err.textContent = 'That image is too small to be a readable screenshot.'; return; }
      D.file = file; D.key = null; const r = new FileReader();
      r.onload = () => { $('#previmg').src = r.result; $('#pname').textContent = `${file.name} · ${bytes(file.size)}`; $('#prev').hidden = false; $('#dropmsg').hidden = true; sum(); };
      r.onerror = () => { err.textContent = 'We could not read that file.'; }; r.readAsDataURL(file);
    };
    drop.onclick = e => { if (!e.target.closest('button')) input.click(); };
    drop.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } };
    input.onchange = () => { setFile(input.files[0]); input.value = ''; };
    ['dragenter', 'dragover'].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.remove('over'); }));
    drop.addEventListener('drop', e => setFile(e.dataTransfer.files[0]));
    $('#repl').onclick = e => { e.stopPropagation(); input.click(); }; $('#rmv').onclick = e => { e.stopPropagation(); setFile(null); };
    f.oninput = () => { D.key = null; sum(); };
    f.onsubmit = async e => {
      e.preventDefault(); err.textContent = ''; const d = UI.formData(f), req = D.cfg.proof ? D.cfg.proof.required : true;
      if (!D.file && req) { err.textContent = 'Upload a screenshot of your transaction.'; return; }
      if (!(d.amount || '').trim()) { err.textContent = 'Enter the amount you sent.'; return; }
      const fd = new FormData(); fd.append('intent_ref', i.ref); fd.append('network', i.network); fd.append('amount', d.amount.trim());
      ['tx_hash', 'sender_address', 'note'].forEach(k => { if ((d[k] || '').trim()) fd.append(k, d[k].trim()); }); if (D.file) fd.append('screenshot', D.file, D.file.name);
      D.key = D.key || V.uuid(); const b = $('#psub'); b.disabled = true; $('#prog').hidden = false; $('#pfill').style.width = '0%'; $('#ptxt').textContent = 'Uploading… 0%';
      try {
        const out = await send('/api/crypto/deposit-requests', fd, D.key, p => { $('#pfill').style.width = p + '%'; $('#ptxt').textContent = p < 100 ? `Uploading… ${p}%` : 'Processing…'; });
        D.file = null; D.key = null; D.done = { ref: (out && out.request && out.request.ref) || (out && out.ref) || 'received' }; toast('Deposit submitted for review');
        try { D.items = (await V.get('/api/crypto/funding-history')).items; } catch (e2) { /* the history refresh is best effort */ }
        dPaint(['proof', 'hist']);
      } catch (ex) { err.textContent = ex.message; b.disabled = false; $('#prog').hidden = true; if (!ex.network) D.key = null; }
    };
  }
  /* XHR so the browser reports real upload progress; the Idempotency-Key makes a retry after a dropped connection safe */
  function send(url, fd, key, onProgress) {
    return new Promise((res, rej) => {
      const x = new XMLHttpRequest(); x.open('POST', url); x.responseType = 'json'; x.setRequestHeader('Accept', 'application/json');
      if (V.state.csrf) x.setRequestHeader('X-CSRF-Token', V.state.csrf); if (key) x.setRequestHeader('Idempotency-Key', key);
      x.upload.onprogress = e => { if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100)); };
      x.onerror = () => rej(Object.assign(new Error('Cannot reach the server. Check your connection and try again.'), { network: true }));
      x.onload = () => (x.status >= 200 && x.status < 300 ? res(x.response) : rej(new Error((x.response && x.response.error && x.response.error.message) || 'The upload failed. Please try again.')));
      x.send(fd);
    });
  }

  async function paintDeposit(v) {
    const tok = ++TOKEN;
    const [cfg, as, hist, src] = await Promise.all([V.get('/api/crypto/config'), V.get('/api/crypto/assets'), V.get('/api/crypto/funding-history'), V.get('/api/crypto/source-addresses')]);
    if (tok !== TOKEN) return;
    D.cfg = cfg; D.assets = as.assets; D.items = hist.items; D.src = src.addresses;
    const gs = groups(D.assets), g = gs.find(x => x.symbol === D.sym) || gs[0];
    D.sym = g ? g.symbol : null; if (!g || !g.nets.some(n => n.ledger_code === D.net)) D.net = g ? g.nets[0].ledger_code : null;
    v.innerHTML = `<div class="fp" id="fp">${testBanner(cfg)}${header('Deposit Cryptocurrency', 'Deposit', 'Fund your Vaulta account with crypto. Select a supported asset, get your wallet address and send the funds from your external wallet.')}
      <div id="d-top"></div><div id="d-addr-wrap"></div><div id="d-proof"></div><div id="d-info"></div><div id="d-hist"></div>${advanced(D.src)}${devPanel()}<div class="sr" id="fp-live" aria-live="polite"></div></div>`;
    dPaint(); bindDeposit(v);
    if (D.net) dLoadIntent();
  }

  function bindDeposit(v) {
    v.onclick = async e => {
      if (ddClick(e, dPick)) return;
      const cp = e.target.closest('[data-copy]'); if (cp) { copyText(cp.dataset.copy, cp); return; }
      if (e.target.closest('[data-retry-d]')) { dLoadIntent(); return; }
      if (e.target.closest('[data-again]')) { D.done = null; D.key = null; dPaint(['proof']); return; }
      const dd_ = e.target.closest('[data-det]'); if (dd_) { const it = itemByRef(D.items, dd_.dataset.det); if (it) details(it); return; }
      const rv = e.target.closest('[data-rev]'); if (rv) { try { await V.post(`/api/crypto/source-addresses/${rv.dataset.rev}/revoke`); R.deposit(); } catch (er) { toast(er.message); } return; }
      const ds = e.target.closest('[data-sign]'); if (ds) { try { const j = await V.post('/api/dev/crypto/sign-challenge', { address: ds.dataset.addr, challenge: ds.dataset.ch }); $('[name=signature]', ds.closest('form')).value = j.signature; } catch (er) { toast(er.message); } }
    };
    v.onkeydown = e => ddKey(e, dPick);
    const sf = $('#srcf'); if (sf) sf.onsubmit = async e => { e.preventDefault(); try { await V.post('/api/crypto/source-addresses', UI.formData(e.target)); toast('Address added. Now prove you control it.'); R.deposit(); } catch (er) { $('#srcm').textContent = er.message; } };
    $$('.vf').forEach(f => (f.onsubmit = async e => { e.preventDefault(); try { await V.post(`/api/crypto/source-addresses/${f.dataset.id}/verify`, UI.formData(f)); toast('Address verified'); R.deposit(); } catch (er) { $('.neg', f).textContent = er.message; } }));
    const df = $('#devf'); if (df) df.onsubmit = async e => { e.preventDefault(); if (!D.net) { $('#devm').textContent = 'Choose an asset and network first.'; return; } const d = UI.formData(e.target);
      try { await V.post('/api/dev/crypto/simulate-deposit', { ledger_code: D.net, ...d, confirmations: +d.confirmations || 1 }); toast('Simulated payment sent'); R.deposit(); } catch (er) { $('#devm').textContent = er.message; } };
  }

  /* ================= WITHDRAW PAGE ================= */
  const W = { cfg: {}, assets: [], sym: null, net: null, info: null, amount: '', addr: '', av: null, q: null, qerr: null, items: [], bal: [], done: null, atok: 0, qtok: 0, itok: 0 };
  const wCur = () => W.assets.find(a => a.ledger_code === W.net);
  const gate = () => {
    const u = V.state.user || {}, m = [];
    if (!u.two_factor_enabled) m.push('Withdrawals require two-factor authentication. <a href="#/profile/security">Turn on 2FA in Security</a> first.');
    if (u.kyc_status !== 'verified') m.push('Identity verification may be required before you can withdraw. <a href="#/settings">Open verification</a>.');
    return { block: !u.two_factor_enabled, html: m.map(x => `<p class="gate">${x}</p>`).join('') };
  };
  const feeText = (i, n) => { const fx = Number(i.fee_fixed) > 0, pc = Number(i.fee_percent) > 0; return !fx && !pc ? 'No fee' : [fx ? `${fmt(i.fee_fixed)} ${n.symbol}` : '', pc ? `${fmt(i.fee_percent)}%` : ''].filter(Boolean).join(' + '); };

  function wFlow() {
    const gs = groups(W.assets), g = gs.find(x => x.symbol === W.sym), n = wCur();
    if (!gs.length || !g || !n) return '<div class="state"><b>Withdrawals are temporarily unavailable</b>No cryptocurrency is enabled for withdrawals right now. Please check back soon.</div>';
    const gt = gate();
    return `${step(1, 'Select Cryptocurrency', 'Choose the asset you want to withdraw.', dd('w-asset', gs.map(x => ({ value: x.symbol, title: `${x.name} (${x.symbol})`, logo: x })), W.sym, 'Cryptocurrency', 'Select a cryptocurrency'), 'w-s1', true)}
      ${step(2, 'Select Network', 'Choose the blockchain network for your withdrawal.', dd('w-net', g.nets.map(x => ({ value: x.ledger_code, title: x.network_name, sub: netSub(x), logo: g })), W.net, 'Network', 'Select a network'), 'w-s2', true)}
      ${step(3, 'Enter Withdrawal Address', 'Input the wallet address where you want to receive your funds.', `<div class="inp" id="w-ai"><input id="w-addr" value="${esc(W.addr)}" placeholder="Enter ${esc(n.symbol)} wallet address" autocomplete="off" autocapitalize="off" spellcheck="false" aria-label="Destination ${esc(n.symbol)} address on ${esc(n.network_name)}" aria-describedby="w-av">
        ${navigator.clipboard && navigator.clipboard.readText ? `<button type="button" class="ibt" data-paste aria-label="Paste address">${svg('paste', 18)}<span>Paste</span></button>` : ''}
        ${'BarcodeDetector' in window && navigator.mediaDevices ? `<button type="button" class="ibt" data-scan aria-label="Scan a wallet QR code">${svg('scan', 20)}</button>` : ''}</div>
        <p class="limits" style="margin-top:6px">${esc(n.address_hint)}</p><div id="w-av" aria-live="polite"></div>
        ${note('<b>Withdrawals may be irreversible.</b> Funds sent to a wrong or incompatible address cannot be recovered. A correctly formatted address does not prove it belongs to you.', 'warn')}`, 'w-s3', true)}
      ${step(4, 'Amount', 'Enter the amount you want to withdraw.', `<div class="inp big" id="w-ami"><input id="w-amt" value="${esc(W.amount)}" inputmode="decimal" autocomplete="off" placeholder="0.0000" aria-label="Amount to withdraw in ${esc(n.symbol)}" aria-describedby="w-bal w-err"><span class="unit">${icon(n, 28)}${esc(n.symbol)}</span></div>
        <div id="w-bal"></div><div id="w-fees"></div>
        <div class="info3"><div>${svg('shield', 22)}<span><small>Withdrawal Fee</small><b id="w-feeinfo">…</b></span></div><div>${svg('clock', 22)}<span><small>Estimated Arrival</small><b>${esc(n.estimated_arrival)}</b></span></div><div>${svg('clock', 22)}<span><small>Processing Time</small><b>${esc(n.processing_time)}</b></span></div></div>
        ${notices(n)}<p class="neg" id="w-err" role="alert"></p>${gt.html}
        <button type="button" class="go" id="w-go" ${gt.block ? 'disabled' : ''}>${svg('send', 20)} Withdraw</button>`, 'w-s4', true)}`;
  }

  function wBal() {
    const n = wCur(), i = W.info, e = $('#w-bal'); if (!n || !e) return;
    e.innerHTML = i ? `<div class="balrow"><span>${svg('wallet', 20)} Account Balance: <b>${esc(money(i.available_usd))}</b></span><button type="button" class="lnk" data-all ${Number(i.max_amount) > 0 ? '' : 'disabled'}>Withdraw All</button></div>
      <p class="limits">Minimum ${esc(fmt(i.min))} ${esc(n.symbol)}${i.max ? ` · Maximum ${esc(fmt(i.max))} ${esc(n.symbol)}` : ''}${i.rate ? ` · Rate: 1 ${esc(n.symbol)} ≈ ${esc(money(i.rate))}` : ''}</p>
      ${i.price_available ? '' : `<p class="vmsg bad">${svg('alert', 16)}<span>${esc(i.price_message || 'This asset cannot be priced right now.')}</span></p>`}` : '<div class="balrow"><span><span class="spin" aria-hidden="true"></span>Loading your balance…</span></div>';
    const f = $('#w-feeinfo'); if (f) f.textContent = i ? feeText(i, n) : '…';
  }

  function wFees() {
    const e = $('#w-fees'); if (!e) return; const n = wCur(), q = W.q, i = W.info;
    if (W.qerr) { e.innerHTML = `<p class="vmsg bad">${svg('alert', 16)}<span>${esc(W.qerr)}</span></p>`; if (!q || !i) return; }
    if (!q || !i) { if (!W.qerr) e.innerHTML = ''; return; }
    const fx = Number(q.fee_fixed) > 0, pc = Number(i.fee_percent) > 0, u = x => `${esc(fmt(x))} ${esc(n.symbol)}`;
    e.innerHTML = (W.qerr ? `<p class="vmsg bad">${svg('alert', 16)}<span>${esc(W.qerr)}</span></p>` : '') + `<div class="feebox" aria-live="polite"><dl><dt>Amount</dt><dd>${u(q.amount)}</dd>${fx ? `<dt>Fixed fee</dt><dd>${u(q.fee_fixed)}</dd>` : ''}${pc ? `<dt>Vaulta service fee (${esc(fmt(i.fee_percent))}%)</dt><dd>${u(q.fee_percent_amount)}</dd>` : ''}
      <dt>Total fees</dt><dd>${u(q.fee)}</dd><dt>Total in ${esc(n.symbol)}</dt><dd>${u(q.total_debit)}</dd><dt class="tot">Deducted from your Account Balance</dt><dd class="tot">${esc(money(q.usd_debit))}</dd><dt>Recipient is expected to receive</dt><dd>${u(q.receives)}</dd></dl>
      <p class="est">Your Account Balance is converted to ${esc(n.symbol)} at the market rate${q.rate ? ` (1 ${esc(n.symbol)} ≈ ${esc(money(q.rate))})` : ''} when you confirm; the rate is then locked. Fees are calculated by our server from the current configuration. Arrival time is an estimate, not a guarantee.</p></div>`;
  }

  async function wLoadInfo() {
    const code = W.net, tok = ++W.itok; W.info = null; wBal(); if (!code) return;
    try { const i = await V.get(`/api/crypto/withdrawal-info?ledger_code=${encodeURIComponent(code)}`); if (tok !== W.itok || code !== W.net) return; W.info = i; }
    catch (e) { if (tok === W.itok && $('#w-bal')) $('#w-bal').innerHTML = `<p class="vmsg bad">${svg('alert', 16)}<span>${esc(e.message)}</span></p>`; return; }
    wBal(); wQuote(true);
  }

  let qTimer = null, aTimer = null;
  function wQuote(now) {
    clearTimeout(qTimer); const n = wCur(), a = (W.amount || '').trim(); W.q = null; W.qerr = null;
    if (!a || !n) { wFees(); return; }
    const frac = /^\d*\.?\d*$/.test(a) ? (a.split('.')[1] || '') : null;
    if (frac === null) { W.qerr = 'Enter a valid number.'; wFees(); return; }
    if (frac.length > n.decimals) { W.qerr = `${n.symbol} supports at most ${n.decimals} decimal places.`; wFees(); return; }
    if (!(Number(a) > 0)) { W.qerr = 'Enter an amount greater than zero.'; wFees(); return; }
    const run = async () => {
      const tok = ++W.qtok, code = W.net;
      try { const q = await V.get(`/api/crypto/withdrawal-quote?ledger_code=${encodeURIComponent(code)}&amount=${encodeURIComponent(a)}`); if (tok !== W.qtok || code !== W.net) return; W.q = q; W.qerr = q.problems.length ? q.problems[0].message : null; }
      catch (e) { if (tok !== W.qtok) return; W.qerr = e.message; }
      wFees();
    };
    if (now) run(); else qTimer = setTimeout(run, 350);
  }

  function wValidate(now) {
    clearTimeout(aTimer); const a = (W.addr || '').trim(); W.av = null;
    const w0 = $('#w-ai'); if (w0) w0.classList.remove('bad');
    if (!a) { const b = $('#w-av'); if (b) b.innerHTML = ''; return Promise.resolve(); }
    const run = async () => {
      const tok = ++W.atok, code = W.net;
      try { const r = await V.get(`/api/crypto/validate-address?ledger_code=${encodeURIComponent(code)}&address=${encodeURIComponent(a)}`); if (tok !== W.atok || code !== W.net) return; W.av = r; }
      catch (e) { if (tok !== W.atok) return; W.av = { valid: false, message: e.message }; }
      const b = $('#w-av'), w = $('#w-ai'); if (b) b.innerHTML = `<p class="vmsg ${W.av.valid ? 'ok' : 'bad'}">${svg(W.av.valid ? 'check' : 'alert', 16)}<span>${esc(W.av.message)}</span></p>`; if (w) w.classList.toggle('bad', !W.av.valid);
    };
    if (now) return run(); aTimer = setTimeout(run, 450); return Promise.resolve();
  }

  function wPaintFlow() { const e = $('#w-flow'); if (!e) return; e.innerHTML = wFlow(); wBal(); wFees(); if (W.addr) wValidate(true); }
  const wPick = (id, v) => {
    const gs = groups(W.assets);
    if (id === 'w-asset') { W.sym = v; const g = gs.find(x => x.symbol === v); W.net = g.nets[0].ledger_code; } else W.net = v;
    W.amount = ''; W.q = null; W.qerr = null; W.av = null; W.done = null; wPaintDone(); wPaintFlow(); const inf = $('#w-info'); if (inf) inf.innerHTML = wInfoCards(); wLoadInfo(); const b = $('#' + id + '-b'); if (b) b.focus();
  };

  /* ---- final checks, then the confirmation dialog. The server re-validates and recalculates everything on submission. ---- */
  async function wGo() {
    const err = $('#w-err'), btn = $('#w-go'); err.textContent = ''; const fail = (m, f) => { err.textContent = m; const el = $(f); if (el) el.focus(); };
    const addr = (W.addr || '').trim(), amt = (W.amount || '').trim(), n = wCur();
    if (!addr) return fail('Enter the destination wallet address.', '#w-addr');
    if (!amt) return fail('Enter the amount you want to withdraw.', '#w-amt');
    btn.disabled = true;
    try {
      await wValidate(true); if (!W.av || !W.av.valid) return fail((W.av && W.av.message) || 'Check the destination address.', '#w-addr');
      let q; try { q = await V.get(`/api/crypto/withdrawal-quote?ledger_code=${encodeURIComponent(W.net)}&amount=${encodeURIComponent(amt)}`); } catch (e) { return fail(e.message, '#w-amt'); }
      W.q = q; W.qerr = null; wFees(); if (q.problems.length) return fail(q.problems[0].message, '#w-amt');
      confirmDialog(n, addr, q);
    } finally { btn.disabled = gate().block; }
  }

  function confirmDialog(n, addr, q) {
    const u = x => `${esc(fmt(x))} ${esc(n.symbol)}`, fx = Number(q.fee_fixed) > 0, pc = Number(q.fee_percent_amount) > 0, key = V.uuid(); let sending = false;
    const d = modal('Confirm withdrawal', `<form class="cf" id="wcf" novalidate><dl><dt>Asset</dt><dd>${icon(n, 22)} ${esc(n.name)} (${esc(n.symbol)})</dd><dt>Network</dt><dd>${esc(n.network_name)}</dd><dt>Destination address</dt><dd><code>${esc(addr)}</code></dd>
      <dt>Withdrawal amount</dt><dd>${u(q.amount)}</dd>${fx ? `<dt>Fixed fee</dt><dd>${u(q.fee_fixed)}</dd>` : ''}${pc ? `<dt>Service fee</dt><dd>${u(q.fee_percent_amount)}</dd>` : ''}<dt>Total fees</dt><dd>${u(q.fee)}</dd>
      <dt>Total in ${esc(n.symbol)}</dt><dd>${u(q.total_debit)}</dd><dt>Rate</dt><dd>1 ${esc(n.symbol)} ≈ ${esc(money(q.rate))}</dd><dt class="tot">Deducted from Account Balance</dt><dd class="tot">${esc(money(q.usd_debit))}</dd><dt>Recipient is expected to receive</dt><dd>${u(q.receives)}</dd></dl>
      <ul><li>Cryptocurrency transfers cannot be reversed. Check the address and network again.</li><li>The dollars are taken from your Account Balance and converted at the rate shown. If the request is cancelled or rejected, the same dollars are returned.</li><li>Your request is reviewed by our team before it is sent. It is <b>not</b> sent to the blockchain when you confirm.</li><li>Estimated arrival: ${esc(n.estimated_arrival)}. This is an estimate, not a guarantee.</li></ul>
      <label class="f">Authenticator code (2FA)<input name="code" id="wcode" inputmode="numeric" maxlength="6" pattern="[0-9]*" autocomplete="one-time-code" required></label>
      <label class="ck"><input type="checkbox" id="wck"><span>I have checked the address and network, and I understand this withdrawal cannot be reversed.</span></label>
      <p class="neg" id="wcm" role="alert"></p><div class="actions"><button type="button" class="btn ghost" data-x>Cancel</button><button class="btn" id="wok" disabled>Confirm withdrawal</button></div></form>`);
    const code = $('#wcode', d), ck = $('#wck', d), ok = $('#wok', d), upd = () => { ok.disabled = sending || !(ck.checked && /^\d{6}$/.test(code.value.trim())); };
    code.oninput = upd; ck.onchange = upd; code.focus();
    $('#wcf', d).onsubmit = async e => {
      e.preventDefault(); if (sending) return; sending = true; upd(); $('#wcm', d).textContent = ''; ok.textContent = 'Submitting…';
      try {          // one Idempotency-Key per open dialog: a retry after a dropped connection cannot create a second withdrawal
        const r = await V.post('/api/crypto/withdrawals', { ledger_code: W.net, amount: q.amount, destination: addr, code: code.value.trim() }, { idem: key });
        d.close(); W.done = r.withdrawal; W.amount = ''; W.addr = ''; W.q = null; W.av = null; toast('Withdrawal requested'); await wRefreshAfter();
      } catch (er) { sending = false; ok.textContent = 'Confirm withdrawal'; $('#wcm', d).textContent = er.message; upd(); }
    };
  }

  async function wRefreshAfter() {
    try { W.items = (await V.get('/api/crypto/funding-history')).items; } catch (e) { /* best effort */ }
    wPaintDone(); wPaintFlow(); wLoadInfo(); wPaintHist();
  }
  function wPaintDone() {
    const e = $('#w-done'); if (!e) return; const w = W.done;
    e.innerHTML = w ? `<section class="fp-card" role="status"><div class="okpanel"><div class="tick">${svg('check', 26)}</div><b>Withdrawal request received</b><p>Reference <b>${esc(w.ref)}</b> for ${esc(fmt(w.amount))} ${esc(w.asset)} is <b>pending review</b>. It has <b>not</b> been sent yet: our team reviews it first, and you will be notified as it progresses. Until then you can cancel it from the history below.</p></div></section>` : '';
    if (w) e.scrollIntoView({ block: 'nearest' });
  }
  function wPaintHist() {
    const e = $('#w-hist'); if (!e) return;
    e.innerHTML = history(W.items, 'withdrawal', 'Recent Withdrawals', 'No withdrawals yet. Your withdrawals will appear here.') +
      `<section class="fp-card" style="margin-top:18px"><h3 style="font-size:17px;margin-bottom:6px">How withdrawals are paid</h3><p class="limits">Crypto withdrawals are paid from your <b>Account Balance</b>. The amount is converted to the asset you choose at the market rate when you confirm, so you do not need to hold the crypto first. Fund your Account Balance from the <a href="#/deposit" style="text-decoration:underline">Deposit</a> page.</p></section>`;
  }

  function wInfoCards() {
    const n = wCur();
    const li = ['Make sure you enter the correct wallet address and network.', 'Withdrawals are irreversible. Funds cannot be recovered once sent.',
      n && W.info ? `Minimum withdrawal amount is ${fmt(W.info.min)} ${n.symbol}${W.info.max ? `; maximum ${fmt(W.info.max)} ${n.symbol}` : ''}.` : 'Minimum and maximum withdrawal amounts apply to each asset and network.',
      'Fees may vary by asset and network and are shown before you confirm.', ...(n ? lines(n.instructions) : [])];
    return `<div class="fp-info"><section class="icard" aria-labelledby="w-ii"><h3 id="w-ii">${svg('shield', 22)} Important Information</h3><ul>${li.map(x => `<li>${esc(x)}</li>`).join('')}</ul></section>
      <section class="icard" aria-labelledby="w-sf"><h3 id="w-sf">${svg('lock', 22)} Security First</h3><p>Withdrawals need your authenticator code, are checked against your account limits on our servers, and are reviewed by our team before they are processed.</p>
      <div class="badges"><div>${svg('shield', 26)}Server-side checks</div><div>${svg('snow', 26)}Team review</div><div>${svg('layers', 26)}2FA required</div></div></section></div>`;
  }

  /* ---- QR scanning (only offered when the browser supports BarcodeDetector and camera access) ---- */
  function scanDialog() {
    const d = modal('Scan wallet QR code', '<div class="scan"><video id="scv" autoplay playsinline muted></video></div><p class="limits" style="margin-top:10px">Point the camera at the QR code of the destination wallet. Always check the address that appears afterwards.</p><p class="neg" id="scm" role="alert"></p>');
    let stream, stop = false; const v = $('#scv', d);
    d.addEventListener('close', () => { stop = true; if (stream) stream.getTracks().forEach(t => t.stop()); });
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false }); v.srcObject = stream; const det = new BarcodeDetector({ formats: ['qr_code'] });
        const tick = async () => {
          if (stop) return;
          try { const r = await det.detect(v); if (r.length) { const addr = String(r[0].rawValue || '').replace(/^[a-z][a-z0-9+.-]*:/i, '').split(/[?@/]/)[0].trim(); if (addr) { d.close(); W.addr = addr; const i = $('#w-addr'); if (i) i.value = addr; wValidate(true); return; } } } catch (e) { /* keep scanning */ }
          requestAnimationFrame(tick);
        };
        v.onloadedmetadata = tick;
      } catch (e) { $('#scm', d).textContent = 'We could not open the camera. Allow camera access, or paste the address instead.'; }
    })();
  }

  async function paintWithdraw(v) {
    const tok = ++TOKEN;
    const [cfg, as, hist] = await Promise.all([V.get('/api/crypto/config'), V.get('/api/crypto/assets'), V.get('/api/crypto/funding-history')]);
    if (tok !== TOKEN) return;
    W.cfg = cfg; W.assets = as.withdrawal_assets; W.items = hist.items;
    const gs = groups(W.assets);
    if (!gs.some(g => g.symbol === W.sym)) { const g = gs[0]; W.sym = g ? g.symbol : null; W.net = null; }
    const g = gs.find(x => x.symbol === W.sym); if (g && !g.nets.some(n => n.ledger_code === W.net)) { W.net = g.nets[0].ledger_code; W.info = null; }
    v.innerHTML = `<div class="fp" id="fp">${testBanner(cfg)}${header('Withdraw Funds', 'Withdraw', 'Transfer your crypto from Vaulta to your external wallet. Choose the asset and network, check the address, and confirm.')}
      <div id="w-done"></div><section class="fp-card flow" id="w-flow" aria-label="Withdrawal form"></section><div id="w-info"></div><div id="w-hist"></div><div class="sr" id="fp-live" aria-live="polite"></div></div>`;
    wPaintFlow(); wPaintDone(); $('#w-info').innerHTML = wInfoCards(); wPaintHist(); bindWithdraw(v);
    if (W.net) wLoadInfo();
  }

  function bindWithdraw(v) {
    v.onclick = async e => {
      if (ddClick(e, wPick)) return;
      const dd_ = e.target.closest('[data-det]'); if (dd_) { const it = itemByRef(W.items, dd_.dataset.det); if (it) details(it); return; }
      if (e.target.closest('[data-all]')) { const i = W.info; if (i && Number(i.max_amount) > 0) { W.amount = fmt(i.max_amount); const a = $('#w-amt'); a.value = W.amount; $('#w-err').textContent = ''; wQuote(true); a.focus(); } return; }
      if (e.target.closest('[data-paste]')) { try { const t = (await navigator.clipboard.readText()).trim(); if (t) { W.addr = t; $('#w-addr').value = t; wValidate(true); } } catch (er) { toast('Paste the address into the box manually'); } return; }
      if (e.target.closest('[data-scan]')) { scanDialog(); return; }
      if (e.target.closest('#w-go')) wGo();
    };
    v.onkeydown = e => ddKey(e, wPick);
    v.oninput = e => {
      if (e.target.id === 'w-addr') { W.addr = e.target.value; $('#w-err').textContent = ''; wValidate(false); }
      else if (e.target.id === 'w-amt') { W.amount = e.target.value.replace(',', '.'); if (W.amount !== e.target.value) e.target.value = W.amount; $('#w-err').textContent = ''; wQuote(false); }
    };
  }

  /* ================= routes ================= */
  const page = fn => () => { const v = $('#view'); if (v) { v.onclick = null; v.onkeydown = null; v.oninput = null; } return load(x => fn(x)); };
  R.deposit = page(paintDeposit);
  R.withdraw = page(paintWithdraw);
  R.crypto = () => { location.hash = '#/deposit'; };            // legacy alias
});
