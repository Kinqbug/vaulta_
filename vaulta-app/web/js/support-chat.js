/* VAULTA support chat widget, shared by the customer app and the admin inbox.
   Transport: authenticated polling (/poll?after=<last message id>). Messages are rendered with textContent-safe escaping, ordered by server id,
   and a failed send keeps its client_msg_id so "Retry" can never create a duplicate. Nothing here decides who the sender is: the server does. */
(() => {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const dt = s => { try { return new Date(s).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) } catch { return s } };
  const cid = () => 'c' + (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, '') : Date.now().toString(36) + Math.random().toString(36).slice(2, 12));
  const size = n => n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB';

  /* opts: { V, modal, ref, staff:bool, title, onClose, extraHead(html), extraTools(html, dlg) } */
  async function open(o) {
    const { V, ref, staff } = o, base = staff ? `/api/admin/support/${ref}` : `/api/support/conversations/${ref}`;
    const seen = new Map(); let last = 0, timer = null, stopped = false, fails = 0, status = '', online = false, readIds = new Set(), canReopen = false;
    const d = o.modal(o.title || 'Conversation', `<div class="chat" data-chat>
      <div class="chat-top"><span id="ch-st"></span><span id="ch-pres" class="chat-pres"></span></div>
      <div class="chat-warn" role="note">Never share your password, recovery phrase or private keys with anyone, including VAULTA staff.</div>
      <div class="chat-net" id="ch-net" role="status" hidden>Reconnecting…</div>
      <div class="chat-log" id="ch-log" role="log" aria-live="polite" aria-label="Messages" tabindex="0"><div class="empty" id="ch-empty">Loading messages…</div></div>
      <div id="ch-tools"></div>
      <form class="chat-form" id="ch-form" novalidate>
        <textarea id="ch-ta" rows="2" maxlength="4000" placeholder="Write a message…" aria-label="Message"></textarea>
        <div class="chat-row"><label class="btn ghost sm chat-attach" title="Attach a JPG, PNG or PDF (max 5 MB)">Attach<input type="file" id="ch-file" accept="image/jpeg,image/png,application/pdf" hidden></label>
          <span id="ch-fn" class="chat-fn"></span>${staff ? '<label class="chat-int"><input type="checkbox" id="ch-int"> Internal note</label><select id="ch-status" class="in" aria-label="Set status"><option value="">Status: auto</option><option value="in_progress">In progress</option><option value="waiting_for_user">Waiting for customer</option><option value="resolved">Resolved</option><option value="closed">Closed</option></select>' : ''}
          <span style="flex:1"></span><button class="btn" id="ch-send" type="submit">Send</button></div>
        <p class="neg" id="ch-err" role="alert"></p></form></div>`, { wide: 1, key: 'chat:' + ref });
    const $ = s => d.querySelector(s), log = $('#ch-log'), ta = $('#ch-ta'), form = $('#ch-form'), fileIn = $('#ch-file');
    if (o.extraTools) o.extraTools($('#ch-tools'), d);
    const mine = m => staff ? m.is_staff : !m.is_staff;

    function paint(m, state) {
      let el = log.querySelector(`[data-k="${m.client_msg_id || 'id' + m.id}"]`) || log.querySelector(`[data-id="${m.id}"]`);
      const who = staff ? (m.internal ? 'Internal note · ' : '') + esc(m.author || (m.is_staff ? 'Staff' : 'Customer')) : (m.is_staff ? 'VAULTA Support' : 'You');
      const att = (m.attachments || []).map(a => `<a class="chat-att" href="/api/support/attachments/${a.id}" target="_blank" rel="noopener">📎 ${esc(a.name)} <small>${size(a.size)}</small></a>`).join('');
      const tick = !staff && !m.is_staff && m.id ? (readIds.has(m.id) ? '<small class="chat-tick">Read by support</small>' : '<small class="chat-tick">Sent</small>') : staff && m.is_staff && m.id ? (m.read_by_customer_at ? '<small class="chat-tick">Seen by customer</small>' : '') : '';
      const html = `<small>${who} · ${m.created_at ? dt(m.created_at) : 'sending…'}</small><div class="chat-body">${esc(m.body)}</div>${att}${state === 'sending' ? '<small class="chat-tick">Sending…</small>' : state === 'failed' ? '<small class="neg">Not sent. <button type="button" class="linkbtn" data-retry>Retry</button></small>' : tick}`;
      const cls = 'ticket-msg chat-msg ' + (mine(m) ? 'chat-me ' : '') + (m.internal ? 'internal ' : m.is_staff ? 'staff ' : '');
      if (!el) { el = document.createElement('div'); log.append(el) }
      el.className = cls; el.innerHTML = html; if (m.id) el.dataset.id = m.id; if (m.client_msg_id) el.dataset.k = m.client_msg_id; return el;
    }
    function render(msgs) {
      const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80; let fresh = 0;
      msgs.forEach(m => { const isNew = !seen.has(m.id); seen.set(m.id, m); last = Math.max(last, m.id); paint(m); if (isNew) fresh++ });
      $('#ch-empty')?.remove(); if (fresh && atBottom) log.scrollTop = log.scrollHeight;
    }
    function header() {
      $('#ch-st').innerHTML = `<span class="st st-${status === 'closed' || status === 'resolved' ? 'completed' : 'pending'}">${esc(status.replace(/_/g, ' '))}</span>`;
      $('#ch-pres').textContent = staff ? '' : (online ? 'Support is online' : 'Support is offline. We will reply as soon as we can; you will get an email.');
      const closed = status === 'closed'; form.hidden = !staff && closed && !canReopen && false; $('#ch-send').disabled = closed && !staff;
      const bar = $('#ch-tools'); if (!staff) { let b = bar.querySelector('[data-cust]'); if (!b) { b = document.createElement('div'); b.dataset.cust = 1; b.className = 'chat-cust'; bar.append(b) }
        b.innerHTML = (status === 'closed' || status === 'resolved') ? (canReopen ? '<button type="button" class="btn ghost sm" data-reopen>Reopen conversation</button>' : '<small>This conversation can no longer be reopened. Please start a new one.</small>') : '<button type="button" class="btn ghost sm" data-close>Close conversation</button>' }
    }
    async function tick() {
      if (stopped) return;
      try {
        const j = await V.get(`${base}/poll?after=${last}`); fails = 0; $('#ch-net').hidden = true;
        status = j.status; online = !!j.staff_online; canReopen = !!j.can_reopen; (j.read_by_support || []).forEach(i => readIds.add(i));
        render(j.messages); if (!seen.size) { const e = $('#ch-empty'); if (e) e.textContent = 'No messages yet.' }
        if (!staff) log.querySelectorAll('[data-id]').forEach(el => { const m = seen.get(+el.dataset.id); if (m) paint(m) });
        header(); if (j.has_more) return schedule(200);
      } catch (e) { fails++; if (e.status === 401 || e.status === 403 || e.status === 404) { $('#ch-err').textContent = e.message; stopped = true; return } $('#ch-net').hidden = false }
      schedule();
    }
    function schedule(ms) { clearTimeout(timer); if (stopped) return; const base_ = document.hidden ? 20000 : 4000; timer = setTimeout(tick, ms ?? Math.min(60000, base_ * Math.pow(2, Math.min(fails, 4)))) }
    document.addEventListener('visibilitychange', onVis); function onVis() { if (!document.hidden && !stopped) { clearTimeout(timer); tick() } }
    d.addEventListener('close', () => { stopped = true; clearTimeout(timer); document.removeEventListener('visibilitychange', onVis); o.onClose && o.onClose() });

    const pending = new Map();                                 // client_msg_id -> {body, file, internal, status}
    async function deliver(id) {
      const p = pending.get(id); $('#ch-err').textContent = '';
      try {
        let j;
        if (p.file) { const fd = new FormData(); fd.set('file', p.file); fd.set('message', p.body); fd.set('client_msg_id', id); if (staff && p.internal) fd.set('internal', '1'); j = await V.upload(staff ? `/api/admin/support/${ref}/attachments` : `/api/support/conversations/${ref}/attachments`, fd) }
        else j = await V.post(staff ? `/api/admin/support/${ref}/reply-live` : `/api/support/conversations/${ref}/messages`, { message: p.body, client_msg_id: id, ...(staff ? { internal: p.internal, ...(p.status ? { status: p.status } : {}) } : {}) });
        pending.delete(id); const el = log.querySelector(`[data-k="${id}"]`); if (el) el.remove(); render([j.message]); clearTimeout(timer); tick();
      } catch (e) {
        if (e.status && e.status < 500 && e.status !== 429) { pending.delete(id); log.querySelector(`[data-k="${id}"]`)?.remove(); $('#ch-err').textContent = e.message; return }   // rejected by the server: it was NOT delivered
        paint({ id: 0, client_msg_id: id, is_staff: staff, body: p.body, created_at: null, internal: p.internal, attachments: p.file ? [{ id: 0, name: p.file.name, size: p.file.size }] : [] }, 'failed');
      }
    }
    form.onsubmit = async ev => {
      ev.preventDefault(); const body = ta.value.trim(), file = fileIn.files[0]; if (!body && !file) { $('#ch-err').textContent = 'Write a message first.'; return }
      if (file && file.size > 5 * 1024 * 1024) { $('#ch-err').textContent = 'Files must be 5 MB or smaller.'; return }
      const id = cid(); pending.set(id, { body: body || '(attachment)', file, internal: staff && $('#ch-int')?.checked, status: staff ? $('#ch-status')?.value : '' });
      paint({ id: 0, client_msg_id: id, is_staff: staff, body: body || '(attachment)', created_at: null, attachments: [] }, 'sending'); log.scrollTop = log.scrollHeight;
      ta.value = ''; fileIn.value = ''; $('#ch-fn').textContent = ''; deliver(id);
    };
    ta.onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit() } };
    fileIn.onchange = () => { $('#ch-fn').textContent = fileIn.files[0] ? fileIn.files[0].name : '' };
    d.addEventListener('click', async e => {
      const r = e.target.closest('[data-retry]'); if (r) { const el = r.closest('[data-k]'); if (el && pending.has(el.dataset.k)) deliver(el.dataset.k) }
      if (e.target.closest('[data-close]')) { try { await V.post(`/api/support/tickets/${ref}/close`); clearTimeout(timer); tick() } catch (x) { $('#ch-err').textContent = x.message } }
      if (e.target.closest('[data-reopen]')) { try { await V.post(`/api/support/tickets/${ref}/reopen`); clearTimeout(timer); tick() } catch (x) { $('#ch-err').textContent = x.message } }
    });
    tick(); return d;
  }
  window.VAULTA_CHAT = { open };
})();
