/* VAULTA admin: Email Center (overview, compose, templates, automations, campaigns, outbox, delivery logs, suppression & preferences, settings).
   Registered through window.VAULTA_ADMIN_EXT. Tabs/buttons are shown according to permissions, but EVERY API call is re-checked on the server (email.* permissions);
   hiding a control here is a convenience, never the security control. All dynamic text is escaped; message previews render inside a sandboxed iframe. */
(window.VAULTA_ADMIN_EXT = window.VAULTA_ADMIN_EXT || []).push(({ R, load, V, UI, can, kpi, NAV }) => {
  const { $, $$, esc, dt, label, toast, table, modal, confirmBox, run, badge, pager } = UI;
  if (!document.getElementById('email-hidden-fix')) { const st = document.createElement('style'); st.id = 'email-hidden-fix'; st.textContent = '#view [hidden], .dlg [hidden]{display:none!important}'; document.head.append(st) }   // site CSS (.f/.banner display rules) would otherwise override the hidden attribute
  const ANY = ['email.logs.read', 'email.templates.read', 'email.send', 'email.send_bulk', 'email.settings.manage', 'email.smtp.manage'];
  if (!ANY.some(can)) return;
  const at = NAV.findIndex(n => n[0] === 'notifications');
  NAV.splice(at < 0 ? NAV.length : at + 1, 0, ['email', 'Email Center', '✉', ANY]);
  const API = '/api/admin/email';
  const head = (t, a = '') => `<div class="top"><h1>${esc(t)}</h1><div style="display:flex;gap:8px;flex-wrap:wrap">${a}</div></div>`;
  const TABS = [['overview', 'Overview', 'email.logs.read'], ['compose', 'Compose Email', ['email.send', 'email.send_bulk']], ['templates', 'Email Templates', 'email.templates.read'], ['automations', 'Automations', 'email.logs.read'],
    ['campaigns', 'Campaigns & Broadcasts', 'email.logs.read'], ['outbox', 'Email Outbox', 'email.logs.read'], ['logs', 'Delivery Logs', 'email.logs.read'], ['suppression', 'Suppression & Preferences', 'email.logs.read'], ['settings', 'Email Settings', 'email.settings.manage'], ['smtp', 'Email & SMTP', 'email.smtp.manage']];
  const allowed = () => TABS.filter(t => [].concat(t[2]).some(can));
  let TAB = null, VIEW = null, PRE = null, FILTER = {};
  const STATUS_CLASS = { queued: 'pending', retry: 'pending', sending: 'processing', accepted: 'processing', delayed: 'pending', delivered: 'completed', bounced: 'failed', complained: 'failed', failed: 'failed',
    suppressed: 'draft', blocked: 'draft', cancelled: 'cancelled', dev_captured: 'draft', legacy_unsent: 'draft', draft: 'pending', pending_approval: 'pending', approved: 'processing', scheduled: 'pending', completed: 'completed', active: 'completed', inactive: 'draft' };
  const NICE = { accepted: 'Accepted by provider', delivered: 'Delivered (confirmed)', dev_captured: 'Captured (dev, not sent)', blocked: 'Blocked (sandbox)', retry: 'Retrying', pending_approval: 'Awaiting approval' };
  const sb = s => `<span class="st st-${STATUS_CLASS[s] || 'pending'}">${esc(NICE[s] || label(s))}</span>`;
  const mono = s => `<code style="font-size:12px">${esc(s)}</code>`;
  const frame = html => `<iframe sandbox="" referrerpolicy="no-referrer" title="Email preview" style="width:100%;height:520px;border:1px solid #ddd;border-radius:12px;background:#1c1c1c" srcdoc="${esc(html)}"></iframe>`;
  const q = o => Object.entries(o).filter(([, v]) => v !== '' && v != null).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&');
  const tabsBar = () => `<div class="tabs-in" role="tablist" aria-label="Email Center sections">${allowed().map(([k, l]) => `<a href="#/email" role="tab" data-tab="${k}" aria-selected="${k === TAB}" class="${k === TAB ? 'on' : ''}" style="${k === TAB ? 'background:#0a0a0a;color:#fff' : ''}">${esc(l)}</a>`).join('')}</div>`;

  R.email = () => load(async v => {
    VIEW = v; const ok = allowed(); if (!ok.length) { v.innerHTML = head('Email Center') + '<div class="empty">You do not have access to any Email Center section.</div>'; return }
    if (PRE) TAB = 'compose'; if (!TAB || !ok.some(t => t[0] === TAB)) TAB = ok[0][0];
    v.onclick = e => { const a = e.target.closest('[data-tab]'); if (a) { e.preventDefault(); TAB = a.dataset.tab; FILTER = {}; R.email() } };
    v.innerHTML = head('Email Center') + tabsBar() + '<div id="etab">' + UI.skeleton(3) + '</div>';
    const body = $('#etab', v);
    try { await SECTIONS[TAB](body) } catch (err) { body.innerHTML = UI.errBox(err.message || 'Could not load this section', true); body.onclick = e => { if (e.target.closest('[data-retry]')) R.email() } }
  });

  // Entry point used by the Users screen: opens the composer with that customer preselected.
  window.VAULTA_EMAIL_COMPOSE = (customerId, name) => { PRE = { id: customerId, name }; TAB = 'compose'; if (location.hash === '#/email') R.email(); else location.hash = '#/email' };

  // ------------------------------------------------------------------ 1. overview
  const SECTIONS = {};
  SECTIONS.overview = async el => {
    const days = +(FILTER.days || 7), o = await V.get(`${API}/overview?days=${days}`), c = o.counts, p = o.provider;
    const max = Math.max(1, ...o.trend.map(t => t.queued + t.accepted + t.delivered + t.bounced + t.failed + t.other));
    el.innerHTML = `<div class="banner" style="margin-bottom:16px"><span>${p.live_delivery ? `<b>Live delivery via ${esc(p.provider)}.</b> ${esc(o.note || '')}` : `<b>${esc(o.note || 'Real email delivery is NOT configured.')}</b>`}${p.live_delivery && !p.delivery_confirmation ? ' <b>No webhook secret is configured, so messages can never be confirmed as delivered.</b>' : ''}${o.legacy_unsent ? ` <small>${o.legacy_unsent} older record(s) from the previous system were never sent and can never be delivered.</small>` : ''}</span></div>
      <div class="row-h" style="display:flex;justify-content:space-between;margin-bottom:10px"><h3>Last ${days} days</h3><div class="seg" role="group" aria-label="Period">${[7, 30, 90].map(d => `<button data-d="${d}" class="${d === days ? 'on' : ''}">${d}d</button>`).join('')}</div></div>
      <div class="grid g4 keep2" style="margin-bottom:16px">${kpi('Queued', c.queued, 'waiting or retrying')}${kpi('Processing', c.processing, 'claimed by a worker')}${kpi('Accepted by provider', c.accepted_by_provider, 'API accepted; not proof of inbox delivery')}${kpi('Confirmed delivered', c.confirmed_delivered, p.delivery_confirmation ? 'verified provider events only' : 'needs provider webhooks')}</div>
      <div class="grid g4 keep2" style="margin-bottom:16px">${kpi('Bounced', c.bounced + c.complained, `${c.complained} spam complaint(s)`)}${kpi('Failed', c.failed, 'permanent or retries exhausted')}${kpi('Suppressed / blocked', c.suppressed + c.blocked, 'consent, bounce or sandbox rules')}${p.live_delivery ? kpi('Active automations', `${o.active_automations}/${o.total_automations}`, `${o.templates.active}/${o.templates.total} templates active`) : kpi('Captured (dev)', c.dev_captured, 'never sent')}</div>
      <section class="box" style="margin-bottom:16px"><h3>Delivery trend</h3>${o.trend.length ? `<div style="display:grid;gap:6px;margin-top:10px">${o.trend.map(t => { const tot = t.queued + t.accepted + t.delivered + t.bounced + t.failed + t.other; const w = k => (t[k] / max * 100).toFixed(1); return `<div style="display:grid;grid-template-columns:90px 1fr 40px;gap:10px;align-items:center;font-size:12px"><span>${esc(t.date)}</span><div style="display:flex;height:14px;background:#f0f0f0;border-radius:99px;overflow:hidden" title="queued ${t.queued}, accepted ${t.accepted}, delivered ${t.delivered}, bounced ${t.bounced}, failed ${t.failed}"><i style="width:${w('delivered')}%;background:#0a0a0a"></i><i style="width:${w('accepted')}%;background:#6b6b6b"></i><i style="width:${w('queued')}%;background:#b5b5b5"></i><i style="width:${w('bounced')}%;background:#d9d9d9"></i><i style="width:${w('failed')}%;background:#8c2f2f"></i></div><b>${tot}</b></div>` }).join('')}</div><p><small>Black: confirmed delivered · dark gray: accepted by provider · light gray: queued · red: failed. Open and click tracking is off by default (privacy).</small></p>` : '<div class="empty">No email activity in this period.</div>'}</section>
      <section class="box"><h3>Campaign activity</h3>${Object.keys(o.campaigns).length ? Object.entries(o.campaigns).map(([k, n]) => `<span style="margin-right:14px">${sb(k)} <b>${n}</b></span>`).join('') : '<div class="empty">No campaigns yet.</div>'}</section>`;
    el.onclick = e => { const b = e.target.closest('[data-d]'); if (b) { FILTER.days = b.dataset.d; R.email() } };
  };

  // ------------------------------------------------------------------ 2. compose
  SECTIONS.compose = async el => {
    const canBulk = can('email.send_bulk'), canSend = can('email.send');
    const tpls = (await V.get(`${API}/templates?status=active`)).items.filter(t => t.manual_send);
    const preset = PRE; PRE = null;
    let chosen = preset ? [{ id: preset.id, label: preset.name || preset.id }] : [], tpl = null, aud = [], est = null;
    const byCat = {}; tpls.forEach(t => (byCat[t.category_label] = byCat[t.category_label] || []).push(t));
    el.innerHTML = `<form id="cf" novalidate class="box">
      <fieldset class="f" style="border:0;padding:0"><legend style="font-weight:700;margin-bottom:6px">Who is this for?</legend><div class="seg" role="radiogroup" aria-label="Send mode">
        ${canSend ? '<button type="button" data-mode="individual" class="on">One customer</button><button type="button" data-mode="selected">Selected customers</button>' : ''}${canBulk ? `<button type="button" data-mode="broadcast" class="${canSend ? '' : 'on'}">Audience broadcast</button>` : ''}</div></fieldset>
      <div id="rcpt"></div>
      <label class="f">Template<select name="template" required><option value="">Choose an approved template…</option>${Object.entries(byCat).map(([c, l]) => `<optgroup label="${esc(c)}">${l.map(t => `<option value="${esc(t.id)}">${esc(t.name)} (${esc(t.mail_class)})</option>`).join('')}</optgroup>`).join('')}</select><small>Templates that describe a specific transaction (deposit, withdrawal, trade…) are sent only by the system when that event really happens, so they are not listed.</small></label>
      <div id="tfields"></div>
      <div id="sched" class="f-row"><label class="f">Send<select name="when"><option value="now">Immediately</option><option value="later">Schedule…</option></select></label><label class="f" id="whenbox" hidden>Date and time (your local time)<input type="datetime-local" name="at"></label></div>
      <p class="neg" id="cerr" role="alert"></p>
      <div id="est" class="banner" style="margin:8px 0" hidden></div>
      <div class="actions"><button type="button" class="btn ghost" id="cprev">Preview</button><button type="button" class="btn ghost" id="ctest">Send test to me</button><button type="submit" class="btn" id="csend">Review &amp; send…</button></div></form>
      <section class="box" style="margin-top:16px"><h3>Preview</h3><div id="pv"><div class="empty">Choose a template and select Preview to see the final email.</div></div></section>`;
    const form = $('#cf', el); let mode = canSend ? 'individual' : 'broadcast';
    const err = m => { $('#cerr', el).textContent = m || '' };
    const payload = () => {
      const f = new FormData(form), variables = {}, overrides = {};
      $$('[data-var]', el).forEach(i => { if (i.value.trim()) variables[i.dataset.var] = i.value });
      $$('[data-ov]', el).forEach(i => { if (i.value.trim()) overrides[i.dataset.ov] = i.value.trim() });
      const at = f.get('when') === 'later' && f.get('at') ? new Date(f.get('at')).toISOString().replace(/\.\d+Z$/, 'Z') : null;
      return { mode, template_id: f.get('template'), variables, overrides, schedule_at: at, name: tpl ? tpl.name : '', audience: { user_ids: chosen.map(c => c.id), segment: f.get('segment') || null } };
    };
    const drawRcpt = async () => {
      const box = $('#rcpt', el);
      if (mode === 'broadcast') {
        const cls = tpl && tpl.mail_class === 'marketing' ? 'marketing' : 'service'; aud = (await V.get(`${API}/audiences?mail_class=${cls}`));
        box.innerHTML = `<label class="f">Audience<select name="segment">${aud.items.map(a => `<option value="${esc(a.key)}">${esc(a.label)} — ${a.eligible} eligible</option>`).join('')}</select><small>${esc(aud.note)}</small></label>`;
        $('select[name=segment]', box).onchange = showEst; showEst(); return;
      }
      box.innerHTML = `<label class="f">Find customers<input id="cs" type="search" placeholder="Name, email or customer ID (min. 2 characters)" autocomplete="off"></label><div id="csr"></div><div id="chips" class="chips" aria-live="polite"></div>`;
      const chips = () => { $('#chips', box).innerHTML = chosen.map((c, i) => `<span class="chip">${esc(c.label)} <button type="button" class="x" data-rm="${i}" aria-label="Remove ${esc(c.label)}" style="border:0;background:none;cursor:pointer">×</button></span>`).join('') || '<small>No customers selected yet.</small>'; showEst() };
      let timer; $('#cs', box).oninput = e => { clearTimeout(timer); const t = e.target.value.trim(); if (t.length < 2) { $('#csr', box).innerHTML = ''; return } timer = setTimeout(async () => {
        const r = await V.get(`${API}/customers/search?q=${encodeURIComponent(t)}`);
        $('#csr', box).innerHTML = r.items.length ? `<div class="tbl-wrap"><table class="tbl"><tbody>${r.items.map(c => `<tr><td><b>${esc((c.first_name || '') + ' ' + (c.last_name || ''))}</b><small>${esc(c.email)} · ${esc(c.id)}</small></td><td>${badge(c.status)}${c.suppressed ? ' <span class="st st-failed">Suppressed</span>' : ''}</td><td class="r"><button type="button" class="btn ghost sm" data-add="${esc(c.id)}" data-nm="${esc(((c.first_name || '') + ' ' + (c.last_name || '')).trim() || c.email)}" ${c.suppressed ? 'disabled' : ''}>Add</button></td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">No matching customers.</div>' }, 250) };
      box.onclick = e => { const a = e.target.closest('[data-add]'), r = e.target.closest('[data-rm]');
        if (a && mode === 'individual') chosen = [{ id: a.dataset.add, label: a.dataset.nm }]; else if (a && !chosen.some(c => c.id === a.dataset.add)) chosen.push({ id: a.dataset.add, label: a.dataset.nm });
        if (r) chosen.splice(+r.dataset.rm, 1); if (a || r) chips() };
      chips();
    };
    const showEst = () => { const b = $('#est', el); if (mode === 'broadcast') { const s = $('select[name=segment]', el); const a = aud.items && aud.items.find(x => x.key === (s && s.value)); b.hidden = !a; if (a) b.innerHTML = `<b>Estimated recipients: ${a.eligible}.</b> You will be asked to confirm this number before anything is sent. Larger sends need approval by a second administrator.` }
      else { b.hidden = false; b.innerHTML = `<b>${chosen.length} customer${chosen.length === 1 ? '' : 's'} selected.</b> Each person receives their own private message; recipients never see each other.` } };
    const drawFields = () => {
      const t = tpl, box = $('#tfields', el); if (!t) { box.innerHTML = ''; return }
      const vars = (t.admin_variables || []).filter(x => x !== 'message_body'), body = (t.admin_variables || []).includes('message_body');
      const DESC = { maintenance_window: 'Maintenance window (e.g. 2026-11-01 01:00–03:00 UTC)', document_name: 'Document name', effective_date: 'Effective date', transaction_reference: 'Transaction reference (must belong to the customer)' };
      box.innerHTML = `<div class="f-row"><label class="f">Subject<input data-ov="subject" maxlength="200" placeholder="${esc(t.subject)}"></label><label class="f">Headline<input data-ov="headline" maxlength="200" placeholder="${esc(t.spec.headline)}"></label></div>
        ${vars.map(x => `<label class="f">${esc(DESC[x] || label(x))}<input data-var="${esc(x)}" maxlength="300"></label>`).join('')}
        ${body ? `<label class="f">Message<textarea data-var="message_body" id="mb" rows="7" maxlength="4000" placeholder="Write your message. Leave a blank line between paragraphs. Plain text only: HTML is never rendered."></textarea><small>Insert a personalisation variable at the cursor:</small></label><div class="chips" id="vchips">${['first_name', 'customer_id', 'dashboard_url', 'support_url', 'security_url'].map(x => `<button type="button" class="chip" data-ins="${x}">{{${x}}}</button>`).join('')}</div>` : ''}
        <div class="f-row"><label class="f">Button label (optional)<input data-ov="cta_label" maxlength="40" placeholder="${esc(t.spec.cta ? t.spec.cta.label : '')}"></label><label class="f">Button link (optional)<input data-ov="cta_url" maxlength="2000" placeholder="https:// (must be a VAULTA or approved address)"></label></div>
        <small>${t.mail_class === 'marketing' ? 'Marketing: only customers who opted in and have a verified email receive this. An unsubscribe link is added automatically.' : t.mail_class === 'admin' ? 'One-to-one administrative message.' : 'Service message: non-promotional.'}</small>`;
      box.onclick = e => { const c = e.target.closest('[data-ins]'); if (c) { const ta = $('#mb', box), s = ta.selectionStart; ta.setRangeText(`{{${c.dataset.ins}}}`, s, ta.selectionEnd, 'end'); ta.focus() } };
    };
    $$('[data-mode]', el).forEach(b => b.onclick = () => { mode = b.dataset.mode; $$('[data-mode]', el).forEach(x => x.classList.toggle('on', x === b)); if (mode === 'individual' && chosen.length > 1) chosen = chosen.slice(0, 1); drawRcpt() });
    form.template.onchange = () => { const id = form.template.value; tpl = null; if (id) V.get(`${API}/templates/${id}`).then(t => { tpl = t; drawFields(); if (mode === 'broadcast') drawRcpt() }); else drawFields() };
    form.when.onchange = () => { $('#whenbox', el).hidden = form.when.value !== 'later' };
    $('#cprev', el).onclick = async e => { err(); if (!form.template.value) return err('Choose a template first.'); if (mode !== 'broadcast' && !chosen.length) return err('Select a customer to preview with their name.');
      const p = payload(); const r = await run(e.target, () => V.post(`${API}/compose/preview`, { ...p, user_id: mode !== 'broadcast' ? chosen[0].id : null })); if (r) $('#pv', el).innerHTML = `<p><b>Subject:</b> ${esc(r.subject)}</p>${frame(r.html)}<details style="margin-top:10px"><summary>Plain-text version</summary><pre style="white-space:pre-wrap;font-size:12px">${esc(r.text)}</pre></details>` ;};
    $('#ctest', el).onclick = async e => { err(); if (!form.template.value) return err('Choose a template first.'); const p = payload();
      const r = await run(e.target, () => V.post(`${API}/compose/test-send`, p)); if (r) toast(`Test ${r.status === 'accepted' ? 'accepted by the provider' : r.status === 'dev_captured' ? 'captured locally (nothing was sent: delivery is not configured)' : r.status} → ${r.sent_to}`, r.status === 'failed' ? 'err' : 'ok') };
    form.onsubmit = async ev => { ev.preventDefault(); err(); const p = payload(); if (!p.template_id) return err('Choose a template.'); if (mode === 'broadcast' ? !p.audience.segment : !p.audience.user_ids.length) return err(mode === 'broadcast' ? 'Choose an audience.' : 'Select at least one customer.');
      let c; try { c = await V.post(`${API}/campaigns`, p) } catch (e) { return err(e.message) }
      const d = modal('Confirm send', `<p>You are about to send <b>${esc(tpl.name)}</b> to <b>${c.estimated_count} recipient${c.estimated_count === 1 ? '' : 's'}</b>${p.schedule_at ? ` at <b>${esc(dt(p.schedule_at))}</b>` : ' <b>immediately</b>'}.</p>
        ${mode === 'broadcast' ? `<p>${c.needs_approval ? '<b>This send is large: a second administrator must approve it before anything is sent.</b>' : 'Every recipient receives an individual message. Addresses are never shared between recipients.'}</p>` : ''}
        ${c.estimated_count < chosen.length && mode !== 'broadcast' ? `<p class="neg">${chosen.length - c.estimated_count} selected customer(s) are not eligible and will be skipped (for example no marketing consent, suppressed address or closed account).</p>` : ''}
        <label class="chk" style="display:flex;gap:8px;align-items:center;margin:10px 0"><input type="checkbox" id="ok"> <span>I have reviewed the preview and the recipient count (${c.estimated_count}).</span></label>
        <p class="neg" id="derr" role="alert"></p><div class="actions"><button class="btn ghost" data-x id="dc">Cancel draft</button><button class="btn" id="dgo" disabled>${c.needs_approval ? 'Submit for approval' : 'Send'}</button></div>`);
      $('#ok', d).onchange = e => { $('#dgo', d).disabled = !e.target.checked };
      $('#dc', d).onclick = () => V.post(`${API}/campaigns/${c.id}/cancel`, {}).catch(() => {});
      $('#dgo', d).onclick = async b => { try { const r = await V.post(`${API}/campaigns/${c.id}/submit`, { confirm_count: c.estimated_count }); d.close(); toast(r.status === 'pending_approval' ? 'Submitted: waiting for a second administrator to approve.' : r.status === 'scheduled' ? 'Scheduled.' : 'Queued for delivery.'); chosen = []; TAB = 'campaigns'; R.email() } catch (e) { $('#derr', d).textContent = e.message } } };
    await drawRcpt(); if (preset) toast(`Composing for ${preset.name || preset.id}`);
  };

  // ------------------------------------------------------------------ 3. templates
  SECTIONS.templates = async el => {
    const f = FILTER, r = await V.get(`${API}/templates?${q({ q: f.q, category: f.category, status: f.status })}`), st = await V.get(`${API}/status`);
    const cols = [{ h: 'Template', r: t => `<b>${esc(t.name)}</b><small>${mono(t.id)}</small>` }, { h: 'Category', r: t => `${esc(t.category_label)}<small>${esc(t.mail_class)}</small>` }, { h: 'Subject', r: t => `<small style="color:inherit">${esc(t.subject)}</small>` },
      { h: 'Trigger', r: t => `<small style="color:inherit">${esc(t.trigger)}</small>` }, { h: 'Status', r: t => sb(t.status) + (t.has_draft ? ' <span class="st st-pending">Draft</span>' : '') }, { h: 'Version', r: t => `v${t.version}<small>${esc(dt(t.updated_at))} · ${esc(t.updated_by)}</small>` },
      { h: '', cls: 'r', r: t => `<div style="display:flex;gap:6px;justify-content:flex-end;align-items:center"><button class="btn ghost sm" data-a="preview" data-id="${esc(t.id)}">Preview</button><select class="in" style="min-height:34px;font-size:13px;padding:0 8px;width:auto" data-menu data-id="${esc(t.id)}" data-s="${t.status}" aria-label="More actions for ${esc(t.name)}"><option value="">More…</option>${can('email.templates.edit') ? '<option value="edit">Edit</option><option value="dup">Duplicate</option>' : ''}${can('email.templates.publish') ? `<option value="toggle">${t.status === 'active' ? 'Disable' : 'Enable'}</option>` : ''}${can('email.send') ? '<option value="test">Send test email</option>' : ''}${t.trigger_event ? '<option value="auto">View automation</option>' : ''}${can('email.logs.read') ? '<option value="hist">Delivery history</option>' : ''}</select></div>` }];
    el.innerHTML = `<div class="box"><div class="f-row" style="grid-template-columns:2fr 1fr 1fr;margin-bottom:10px"><label class="f">Search<input id="tq" type="search" value="${esc(f.q || '')}" placeholder="Name, ID or description"></label><label class="f">Category<select id="tc"><option value="">All</option>${Object.entries(st.categories).map(([k, l]) => `<option value="${esc(k)}" ${f.category === k ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></label><label class="f">Status<select id="ts"><option value="">All</option><option value="active" ${f.status === 'active' ? 'selected' : ''}>Active</option><option value="inactive" ${f.status === 'inactive' ? 'selected' : ''}>Inactive</option></select></label></div><p><small>${r.items.length} template${r.items.length === 1 ? '' : 's'}. Templates without an automated trigger are sent by hand or describe workflows the backend does not have yet.</small></p>${table(cols, r.items, { empty: 'No templates match.' })}</div>`;
    const reload = () => { FILTER = { q: $('#tq', el).value.trim(), category: $('#tc', el).value, status: $('#ts', el).value }; R.email() };
    $('#tq', el).onkeydown = e => { if (e.key === 'Enter') reload() }; $('#tc', el).onchange = reload; $('#ts', el).onchange = reload;
    const handle = async b => { const id = b.dataset.id, a = b.dataset.a;
      if (a === 'preview') { const p = await run(b, () => V.post(`${API}/templates/${id}/preview`, {})); if (p) modal(`Preview — ${id}`, `<p><small>Sample data only. Nothing is sent.</small></p><p><b>Subject:</b> ${esc(p.subject)}</p>${frame(p.html)}<details style="margin-top:10px"><summary>Plain text</summary><pre style="white-space:pre-wrap;font-size:12px">${esc(p.text)}</pre></details>`, { wide: true }) }
      if (a === 'edit') editTemplate(id);
      if (a === 'dup') { const d = modal('Duplicate template', `<form id="df"><label class="f">New template ID<input name="nid" required pattern="[A-Z0-9_]+" placeholder="CUSTOM_PROMO_001"></label><label class="f">Name<input name="nm" required maxlength="120"></label><p class="neg" id="de"></p><div class="actions"><button class="btn" type="submit">Duplicate</button></div></form>`); $('#df', d).onsubmit = async ev => { ev.preventDefault(); try { await V.post(`${API}/templates/${id}/duplicate`, { new_id: ev.target.nid.value, name: ev.target.nm.value }); d.close(); toast('Duplicated (inactive until you enable it).'); R.email() } catch (x) { $('#de', d).textContent = x.message } } }
      if (a === 'toggle') { const to = b.dataset.s === 'active' ? 'inactive' : 'active', reason = await confirmBox(`${to === 'active' ? 'Enable' : 'Disable'} template`, to === 'inactive' ? 'Customers will stop receiving this email until it is enabled again.' : 'This template will be used for sending.', { reason: true, ok: to === 'active' ? 'Enable' : 'Disable', danger: to === 'inactive' });
        if (reason && await run(b, () => V.post(`${API}/templates/${id}/status`, { status: to, reason }), 'Updated') !== undefined) R.email() }
      if (a === 'test') { const r = await run(b, () => V.post(`${API}/templates/${id}/test-send`, {})); if (r) toast(r.status === 'dev_captured' ? 'Captured locally: real delivery is not configured, nothing was sent.' : `Test ${r.status} → ${r.sent_to}`, r.status === 'failed' ? 'err' : 'ok') }
      if (a === 'auto') { TAB = 'automations'; FILTER = { template: id }; R.email() }
      if (a === 'hist') { TAB = 'outbox'; FILTER = { template: id }; R.email() } };
    el.onclick = e => { const b = e.target.closest('[data-a]'); if (b) handle(b) };
    el.onchange = e => { const m = e.target.closest('[data-menu]'); if (!m || !m.value) return; const act = { dataset: { a: m.value, id: m.dataset.id, s: m.dataset.s }, textContent: '', disabled: false }; m.value = ''; handle(act) };
  };

  async function editTemplate(id) {
    const t = await V.get(`${API}/templates/${id}`), cur = t.draft || { subject: t.subject, spec: t.spec }, sp = cur.spec, lines = rows => (rows || []).map(r => `${r[0]} | ${r[1]}`).join('\n');
    const d = modal(`Edit — ${t.name} (${id})`, `<p><small>Editing creates a <b>draft</b>. Customers keep receiving version ${t.version} until a draft is published. ${t.mail_class === 'security' || t.mail_class === 'transactional' ? '<b>Security and transactional templates must be published by a different administrator than the author.</b>' : ''}</small></p>
      <form id="ef" novalidate><label class="f">Subject<input name="subject" maxlength="200" value="${esc(cur.subject)}"></label><label class="f">Headline<input name="headline" maxlength="200" value="${esc(sp.headline)}"></label>
      <label class="f">Paragraphs (blank line between paragraphs)<textarea name="intro" rows="6">${esc((sp.intro || []).join('\n\n'))}</textarea></label>
      <div class="f-row"><label class="f">Status badge (optional)<input name="badge" maxlength="40" value="${esc(sp.badge || '')}"></label><label class="f">Details title<input name="summary_title" maxlength="60" value="${esc(sp.summary_title || '')}"></label></div>
      <label class="f">Detail rows in the panel (one per line: Label | {{variable}})<textarea name="summary" rows="4">${esc(lines(sp.summary))}</textarea></label>
      <label class="f">Stacked fields (one per line: Label | {{variable}})<textarea name="details" rows="3">${esc(lines(sp.details))}</textarea></label>
      <label class="f">Text after the details (blank line between paragraphs)<textarea name="after" rows="3">${esc((sp.after || []).join('\n\n'))}</textarea></label>
      <div class="f-row"><label class="f">Button label<input name="cta_label" maxlength="40" value="${esc(sp.cta ? sp.cta.label : '')}"></label><label class="f">Button links to<select name="cta_var"><option value="">(no button)</option>${t.variables.filter(v => v.kind === 'url').map(v => `<option value="${esc(v.name)}" ${sp.cta && sp.cta.url_var === v.name ? 'selected' : ''}>${esc(v.description)}</option>`).join('')}</select></label></div>
      <label class="f">Footnote<textarea name="notice" rows="2" maxlength="600">${esc(sp.notice || '')}</textarea></label>
      <details><summary>Variables you can use here</summary><p style="line-height:2">${t.variables.map(v => `<span class="chip" title="${esc(v.description)}">{{${esc(v.name)}}}</span>`).join(' ')}</p><small>Only variables this template's data can supply are accepted. Anything else, and any HTML or template code, is rejected.</small></details>
      <p class="neg" id="ee" role="alert"></p><div class="actions"><button type="button" class="btn ghost" id="ep">Preview</button><button type="submit" class="btn ghost">Save draft</button>${can('email.templates.publish') ? '<button type="button" class="btn" id="epub">Publish…</button>' : ''}${t.draft ? '<button type="button" class="btn danger sm" id="edis">Discard draft</button>' : ''}</div></form><div id="epv" style="margin-top:14px"></div>
      <h3 style="margin:14px 0 6px">Versions</h3>${table([{ h: 'Version', r: v => 'v' + v.version }, { h: 'State', r: v => sb(v.state === 'published' ? 'active' : v.state === 'draft' ? 'draft' : 'inactive') + ' ' + esc(v.state) }, { h: 'Author', r: v => esc(v.author || 'system') }, { h: 'Published', r: v => esc(dt(v.published_at)) }, { h: 'Note', r: v => esc(v.note || '') }], t.versions)}`, { wide: true });
    const form = $('#ef', d), rows = s => s.split('\n').map(l => l.trim()).filter(Boolean).map(l => { const i = l.indexOf('|'); return i < 0 ? [l, ''] : [l.slice(0, i).trim(), l.slice(i + 1).trim()] });
    const paras = s => s.split(/\n\s*\n/).map(x => x.trim()).filter(Boolean);
    const data = () => ({ subject: form.subject.value, spec: { preheader: sp.preheader || '', headline: form.headline.value, intro: paras(form.intro.value), badge: form.badge.value, summary_title: form.summary_title.value, summary: rows(form.summary.value), details: rows(form.details.value), after: paras(form.after.value), notice: form.notice.value,
      cta: form.cta_var.value ? { label: form.cta_label.value, url_var: form.cta_var.value } : null, ...(sp._optional ? { _optional: sp._optional } : {}) } });
    const msg = m => { $('#ee', d).textContent = m || '' };
    $('#ep', d).onclick = async b => { msg(); try { const p = await V.post(`${API}/templates/${id}/preview`, data()); $('#epv', d).innerHTML = `<p><b>Subject:</b> ${esc(p.subject)}</p>${frame(p.html)}` } catch (e) { msg(e.message) } };
    form.onsubmit = async ev => { ev.preventDefault(); msg(); try { await V.post(`${API}/templates/${id}/draft`, data()); toast('Draft saved. It is not live until published.') } catch (e) { msg(e.message) } };
    const pub = $('#epub', d); if (pub) pub.onclick = async () => { msg(); try { await V.post(`${API}/templates/${id}/draft`, data()) } catch (e) { return msg(e.message) }
      const note = await confirmBox('Publish new version', 'Every future email from this template will use this version. Published versions cannot be edited, only replaced.', { reason: true, ok: 'Publish' }); if (!note) return;
      try { const r = await V.post(`${API}/templates/${id}/publish`, { note }); d.close(); toast(`Published v${r.version}`); R.email() } catch (e) { msg(e.message) } };
    const dis = $('#edis', d); if (dis) dis.onclick = async () => { await V.del(`${API}/templates/${id}/draft`); d.close(); toast('Draft discarded'); R.email() };
  }

  // ------------------------------------------------------------------ 4. automations
  SECTIONS.automations = async el => {
    const r = await V.get(`${API}/automations`), cm = can('email.automations.manage'), only = FILTER.template;
    const items = only ? r.items.filter(a => a.template_id === only) : r.items;
    const cols = [{ h: 'Automation', r: a => `<b>${esc(a.template)}</b><small>${esc(a.event)} · ${mono(a.event_type)}</small>` }, { h: 'Type', r: a => `${esc(a.kind)}<small>${esc(a.mail_class)}</small>` },
      { h: 'Timing', r: a => a.kind === 'reminder' ? `after ${a.delay_minutes >= 1440 ? Math.round(a.delay_minutes / 1440) + ' day(s)' : Math.round(a.delay_minutes / 60) + ' h'}<small>max ${a.params.max_attempts || 1} · every ${a.params.interval_hours || 0}h · quiet hours ${a.params.respect_quiet_hours === false ? 'ignored' : 'respected'}</small>` : (a.delay_minutes ? `${a.delay_minutes} min delay` : 'immediate') },
      { h: 'Last 30 days', r: a => Object.entries(a.last_30_days).map(([s, n]) => `${esc(label(s))}: ${n}`).join('<br>') || '<small>no activity</small>' + (a.reminder_state ? '' : '') },
      { h: 'State', r: a => sb(a.enabled ? 'active' : 'inactive') + (a.reminder_state ? `<small>${a.reminder_state.in_progress} in progress · ${a.reminder_state.stopped} stopped</small>` : '') },
      { h: '', cls: 'r', r: a => cm ? `<button class="btn ghost sm" data-t="${a.id}" data-on="${a.enabled ? 0 : 1}" data-k="${esc(a.kind)}" data-c="${esc(a.mail_class)}">${a.enabled ? 'Disable' : 'Enable'}</button> ${a.kind === 'reminder' ? `<button class="btn ghost sm" data-cfg="${a.id}">Configure</button>` : ''}` : '' }];
    el.innerHTML = `<div class="box"><div class="row-h"><h3>Automations${only ? ` for ${esc(only)}` : ''}</h3>${only ? '<button class="btn ghost sm" id="aall">Show all</button>' : ''}${cm ? '<button class="btn sm" id="arun">Run reminders now</button>' : ''}</div><p><small>Event emails are queued by the backend at the moment the real state change is recorded. Reminders are checked every few minutes by the worker; they stop automatically when the condition is resolved.</small></p>${table(cols, items, { empty: 'No automations.' })}</div>
      <section class="box" style="margin-top:16px"><h3>Templates with no automated trigger</h3><p><small>These describe workflows that do not exist in the backend yet (so no email can honestly be sent) or are sent by hand. Nothing here is faked.</small></p>${table([{ h: 'Template', r: g => `<b>${esc(g.name)}</b><small>${mono(g.template)}</small>` }, { h: 'Why', r: g => esc(g.reason) }], r.no_backend_trigger)}</section>`;
    const all = $('#aall', el); if (all) all.onclick = () => { FILTER = {}; R.email() };
    const rn = $('#arun', el); if (rn) rn.onclick = async () => { const x = await run(rn, () => V.post(`${API}/automations/run-reminders`, {})); if (x) { const sent = Object.values(x.result).reduce((a, b) => a + b.sent, 0), post = Object.values(x.result).reduce((a, b) => a + b.postponed, 0); toast(`Reminders queued: ${sent}${post ? ` · ${post} postponed for quiet hours` : ''}`); R.email() } };
    el.addEventListener('click', async e => { const b = e.target.closest('[data-t]'), c = e.target.closest('[data-cfg]');
      if (b) { const on = b.dataset.on === '1', needs = !on && b.dataset.k === 'event' && ['security', 'transactional'].includes(b.dataset.c); let reason; if (needs) { reason = await confirmBox('Switch off an alert email', 'Customers will stop receiving this security/transactional email. A reason is required and is recorded.', { reason: true, ok: 'Disable', danger: true }); if (!reason) return }
        if (await run(b, () => V.patch(`${API}/automations/${b.dataset.t}`, { enabled: on, reason }), 'Updated') !== undefined) R.email() }
      if (c) { const a = r.items.find(x => x.id === +c.dataset.cfg), d = modal(`Configure — ${a.template}`, `<form id="rf"><div class="f-row"><label class="f">Wait (hours) before the first reminder<input name="h" type="number" min="0" value="${Math.round(a.delay_minutes / 60)}"></label><label class="f">Maximum reminders per customer<input name="m" type="number" min="1" max="10" value="${a.params.max_attempts || 1}"></label></div><div class="f-row"><label class="f">Hours between reminders<input name="i" type="number" min="0" value="${a.params.interval_hours || 0}"></label><label class="chk" style="display:flex;gap:8px;align-items:center;margin-top:24px"><input type="checkbox" name="q" ${a.params.respect_quiet_hours === false ? '' : 'checked'}> Respect quiet hours (customer's time zone)</label></div><p class="neg" id="re"></p><div class="actions"><button class="btn" type="submit">Save</button></div></form>`);
        $('#rf', d).onsubmit = async ev => { ev.preventDefault(); const f = ev.target; try { await V.patch(`${API}/automations/${a.id}`, { delay_minutes: Math.round(+f.h.value * 60), params: { max_attempts: +f.m.value, interval_hours: +f.i.value, respect_quiet_hours: f.q.checked } }); d.close(); toast('Saved'); R.email() } catch (x) { $('#re', d).textContent = x.message } } } });
  };

  // ------------------------------------------------------------------ 5. campaigns
  SECTIONS.campaigns = async el => {
    const r = await V.get(`${API}/campaigns`), ap = can('email.bulk_approve');
    const cols = [{ h: 'Campaign', r: c => `<b>${esc(c.name)}</b><small>${mono(c.ref)} · ${esc(c.mode)} · ${esc(c.template_id)}</small>` }, { h: 'Class', r: c => esc(c.mail_class) }, { h: 'Status', r: c => sb(c.status) + (c.scheduled_at ? `<small>for ${esc(dt(c.scheduled_at))}</small>` : '') },
      { h: 'Recipients', cls: 'r', r: c => `${c.final_count ?? c.estimated_count ?? '—'}` }, { h: 'Delivery', r: c => Object.entries(c.delivery).map(([s, n]) => `${esc(NICE[s] || label(s))}: ${n}`).join('<br>') || '<small>—</small>' }, { h: 'Created by', r: c => `${esc(c.creator)}<small>${esc(dt(c.created_at))}</small>` },
      { h: '', cls: 'r', r: c => `<button class="btn ghost sm" data-v="${c.id}">Details</button> ${c.status === 'pending_approval' && ap ? `<button class="btn sm" data-ap="${c.id}">Approve</button> ` : ''}${['draft', 'pending_approval', 'approved', 'scheduled', 'sending'].includes(c.status) && can('email.send') ? `<button class="btn danger sm" data-cx="${c.id}">Cancel</button>` : ''}` }];
    el.innerHTML = `<div class="box"><div class="row-h"><h3>Campaigns & broadcasts</h3></div>${table(cols, r.items, { empty: 'No campaigns yet. Use Compose Email to send one.' })}</div>`;
    el.onclick = async e => { const b = e.target.closest('button'); if (!b) return;
      if (b.dataset.v) { const j = await run(b, () => V.get(`${API}/campaigns/${b.dataset.v}`)); if (j) modal(`${j.campaign.name} — ${j.campaign.ref}`, `<p>${sb(j.campaign.status)} · audience: ${esc(j.audience.segment || j.audience.selected + ' selected customer(s)')}</p><p><small>Recipient addresses are masked.</small></p>${table([{ h: 'Customer', r: x => mono(x.customer) }, { h: 'Address', r: x => esc(x.email) }, { h: 'Status', r: x => sb(x.status) }, { h: 'Note', r: x => esc(x.skip_reason || '') }], j.recipients)}`, { wide: true }) }
      if (b.dataset.ap) { const ok = await confirmBox('Approve send', 'You are approving this send to all of its recipients. You cannot approve a campaign you created yourself.', { ok: 'Approve & send' }); if (ok && await run(b, () => V.post(`${API}/campaigns/${b.dataset.ap}/approve`, {}), 'Approved: sending') !== undefined) R.email() }
      if (b.dataset.cx) { const ok = await confirmBox('Cancel campaign', 'Messages not yet sent will be cancelled. Messages already sent cannot be recalled.', { ok: 'Cancel campaign', danger: true }); if (ok) { const x = await run(b, () => V.post(`${API}/campaigns/${b.dataset.cx}/cancel`, {})); if (x) { toast(`${x.cancelled_messages} queued message(s) cancelled`); R.email() } } } };
  };

  // ------------------------------------------------------------------ 6. outbox
  SECTIONS.outbox = async el => {
    const f = FILTER, p = +(f.page || 1), r = await V.get(`${API}/outbox?${q({ status: f.status, template: f.template, q: f.q, page: p })}`), S = ['queued', 'retry', 'sending', 'accepted', 'delivered', 'delayed', 'bounced', 'complained', 'failed', 'suppressed', 'blocked', 'cancelled', 'dev_captured'];
    const cols = [{ h: 'When', r: m => esc(dt(m.created_at)) }, { h: 'Message', r: m => `<b>${esc(m.template_id || '—')}</b><small>${esc(m.event || '')} · ${esc(m.entity_ref || '')}</small>` }, { h: 'To', r: m => `${esc(m.to)}<small>${esc(m.customer || '')}</small>` }, { h: 'Class', r: m => esc(m.mail_class || '') },
      { h: 'Status', r: m => sb(m.status) + (m.last_error ? `<small>${esc(m.last_error)}</small>` : '') + (m.next_attempt_at ? `<small>next: ${esc(dt(m.next_attempt_at))}</small>` : '') }, { h: 'Tries', cls: 'r', r: m => m.attempts }];
    el.innerHTML = `<div class="box"><div class="f-row" style="grid-template-columns:2fr 1fr 1fr;margin-bottom:10px"><label class="f">Search<input id="oq" type="search" value="${esc(f.q || '')}" placeholder="Customer ID, reference, provider message ID"></label><label class="f">Status<select id="os"><option value="">All</option>${S.map(s => `<option value="${s}" ${f.status === s ? 'selected' : ''}>${esc(NICE[s] || label(s))}</option>`).join('')}</select></label><label class="f">Template<input id="ot" value="${esc(f.template || '')}" placeholder="e.g. WELCOME_001"></label></div>
      <p><small>Addresses are masked. Message bodies are not stored here; they are re-rendered on demand and viewing one is recorded in the audit log.</small></p>${table(cols, r.items, { empty: 'No messages match.', onRow: true })}${pager(r)}</div>`;
    const go = extra => { FILTER = { q: $('#oq', el).value.trim(), status: $('#os', el).value, template: $('#ot', el).value.trim(), ...extra }; R.email() };
    $('#oq', el).onkeydown = e => { if (e.key === 'Enter') go() }; $('#ot', el).onkeydown = e => { if (e.key === 'Enter') go() }; $('#os', el).onchange = () => go();
    el.onclick = async e => { const pg = e.target.closest('[data-pg]'); if (pg && !pg.disabled) return go({ page: pg.dataset.pg }); const tr = e.target.closest('tr[data-i]'); if (tr) openMessage(r.items[+tr.dataset.i].id) };
    el.onkeydown = e => { if (e.key === 'Enter') { const tr = e.target.closest('tr[data-i]'); if (tr) openMessage(r.items[+tr.dataset.i].id) } };
  };
  async function openMessage(id) {
    const m = await V.get(`${API}/outbox/${id}`), cs = can('email.settings.manage');
    const d = modal(`Message #${m.id}`, `<p>${sb(m.status)} ${m.status_explained ? `<small>${esc(m.status_explained)}</small>` : ''}</p>
      <dl><dt>Template</dt><dd>${esc(m.template_id || '')} v${m.template_version || m.version || ''}</dd><dt>Event</dt><dd>${esc(m.event || '')} · ${esc(m.entity_ref || '')}</dd><dt>To</dt><dd>${esc(m.to)} <small>${esc(m.customer || '')}</small></dd><dt>Provider</dt><dd>${esc(m.provider || '—')} ${m.provider_message_id ? mono(m.provider_message_id) : ''}</dd><dt>Accepted</dt><dd>${esc(dt(m.accepted_at))}</dd><dt>Confirmed delivered</dt><dd>${m.delivered_at ? esc(dt(m.delivered_at)) : 'Not confirmed'}</dd>${m.last_error ? `<dt>Last error</dt><dd>${esc(m.last_error)}</dd>` : ''}${m.non_live ? '<dt>Note</dt><dd>Relates to simulated (non-live) money: never sent through a real provider.</dd>' : ''}</dl>
      <h3 style="margin:12px 0 6px">Delivery attempts</h3>${table([{ h: '#', r: a => a.attempt_no }, { h: 'Started', r: a => esc(dt(a.started_at)) }, { h: 'Outcome', r: a => esc(label(a.outcome)) }, { h: 'HTTP', r: a => a.http_status || '' }, { h: 'Error', r: a => esc((a.error_code ? a.error_code + ': ' : '') + (a.error_message || '')) }], m.attempts_log, { empty: 'No attempts yet.' })}
      <h3 style="margin:12px 0 6px">Provider events</h3>${table([{ h: 'Event', r: e => esc(e.event_type) }, { h: 'Occurred', r: e => esc(dt(e.occurred_at)) }, { h: 'Received', r: e => esc(dt(e.received_at)) }, { h: 'Applied', r: e => e.applied ? 'yes' : 'no' }], m.provider_events, { empty: 'No provider events.' })}
      <div class="actions" style="justify-content:flex-start;margin-top:12px"><button class="btn ghost sm" id="mv">View message</button>${cs && m.status === 'failed' ? '<button class="btn ghost sm" id="mr">Retry</button>' : ''}${cs && ['queued', 'retry'].includes(m.status) ? '<button class="btn danger sm" id="mc">Cancel</button>' : ''}</div><div id="mpv"></div>`, { wide: true });
    $('#mv', d).onclick = async b => { const p = await run(b.target, () => V.get(`${API}/outbox/${id}/preview`)); if (p) $('#mpv', d).innerHTML = `<p><b>Subject:</b> ${esc(p.subject)}</p>${frame(p.html)}` };
    const mr = $('#mr', d); if (mr) mr.onclick = async () => { if (await run(mr, () => V.post(`${API}/outbox/${id}/retry`, {}), 'Re-queued') !== undefined) { d.close(); R.email() } };
    const mc = $('#mc', d); if (mc) mc.onclick = async () => { if (await run(mc, () => V.post(`${API}/outbox/${id}/cancel`, {}), 'Cancelled') !== undefined) { d.close(); R.email() } };
  }

  // ------------------------------------------------------------------ 7. delivery logs
  SECTIONS.logs = async el => {
    const f = FILTER, kind = f.kind || 'events', p = +(f.page || 1), r = await V.get(`${API}/delivery-logs?${q({ kind, page: p })}`);
    const cols = kind === 'attempts' ? [{ h: 'Started', r: a => esc(dt(a.started_at)) }, { h: 'Message', r: a => `#${a.email_id} <small>${esc(a.template_id || '')}</small>` }, { h: 'Attempt', r: a => a.attempt_no }, { h: 'Transport', r: a => esc(a.transport) }, { h: 'Outcome', r: a => esc(label(a.outcome)) }, { h: 'HTTP', r: a => a.http_status || '' }, { h: 'Error', r: a => esc((a.error_code ? a.error_code + ': ' : '') + (a.error_message || '')) }]
      : [{ h: 'Received', r: a => esc(dt(a.received_at)) }, { h: 'Message', r: a => a.email_id ? `#${a.email_id} <small>${esc(a.template_id || '')}</small>` : '<small>unmatched</small>' }, { h: 'Provider', r: a => esc(a.provider) }, { h: 'Event', r: a => esc(a.event_type) }, { h: 'Occurred', r: a => esc(dt(a.occurred_at)) }, { h: 'Changed status', r: a => a.applied ? 'yes' : 'no' }];
    el.innerHTML = `<div class="box"><div class="row-h"><h3>Delivery logs</h3><div class="seg" role="group"><button data-k="events" class="${kind === 'events' ? 'on' : ''}">Provider events</button><button data-k="attempts" class="${kind === 'attempts' ? 'on' : ''}">Delivery attempts</button></div></div><p><small>Provider events are signature-verified webhooks (delivered, bounced, complained…). Attempts show what each send call returned.</small></p>${table(cols, r.items, { empty: kind === 'events' ? 'No provider events received yet. Without verified webhooks no message can be shown as delivered.' : 'No attempts yet.' })}${pager(r)}</div>`;
    el.onclick = e => { const k = e.target.closest('[data-k]'), pg = e.target.closest('[data-pg]'); if (k) { FILTER = { kind: k.dataset.k }; R.email() } if (pg && !pg.disabled) { FILTER = { kind, page: pg.dataset.pg }; R.email() } };
  };

  // ------------------------------------------------------------------ 8. suppression & preferences
  SECTIONS.suppression = async el => {
    const r = await V.get(`${API}/suppressions`), cm = can('email.suppression.manage');
    el.innerHTML = `<div class="grid g3" style="margin-bottom:16px">${kpi('Opted in to marketing', r.marketing_opted_in, 'explicit opt-in only')}${kpi('Consent records', r.consent_events, 'append-only log')}${kpi('Active suppressions', r.items.filter(s => !s.released_at).length, 'never emailed')}</div>
      <div class="box"><div class="row-h"><h3>Suppression list</h3>${cm ? '<button class="btn sm" id="sadd">Suppress an address</button>' : ''}</div><p><small>Hard bounces and spam complaints are added automatically from verified provider events. Unsubscribes withdraw marketing consent. Security and transaction emails still reach customers who unsubscribed from marketing, but never a suppressed address.</small></p>
      ${table([{ h: 'Address', r: s => esc(s.email_key) }, { h: 'Scope', r: s => esc(s.scope === 'all' ? 'All email' : 'Marketing only') }, { h: 'Reason', r: s => esc(label(s.reason)) + `<small>${esc(s.source || '')}</small>` }, { h: 'Added', r: s => esc(dt(s.created_at)) }, { h: 'Status', r: s => s.released_at ? `<span class="st st-draft">Released</span><small>${esc(s.release_reason || '')}</small>` : '<span class="st st-failed">Active</span>' },
        { h: '', cls: 'r', r: s => cm && !s.released_at ? `<button class="btn ghost sm" data-rel="${s.id}">Release</button>` : '' }], r.items, { empty: 'No suppressed addresses.' })}</div>`;
    const add = $('#sadd', el); if (add) add.onclick = () => { const d = modal('Suppress an address', `<form id="sf"><label class="f">Email address<input name="e" type="email" required></label><label class="f">Scope<select name="s"><option value="all">All email</option><option value="marketing">Marketing only</option></select></label><label class="f">Reason (recorded)<input name="r" required minlength="5" maxlength="300"></label><p class="neg" id="se"></p><div class="actions"><button class="btn" type="submit">Suppress</button></div></form>`); $('#sf', d).onsubmit = async ev => { ev.preventDefault(); const f = ev.target; try { await V.post(`${API}/suppressions`, { email: f.e.value, scope: f.s.value, reason: f.r.value }); d.close(); toast('Added'); R.email() } catch (x) { $('#se', d).textContent = x.message } } };
    el.onclick = async e => { const b = e.target.closest('[data-rel]'); if (!b) return; const reason = await confirmBox('Release suppression', 'Only release an address if you have verified with the customer that they want email again. A reason is required.', { reason: true, ok: 'Release' }); if (reason && await run(b, () => V.post(`${API}/suppressions/${b.dataset.rel}/release`, { reason }), 'Released') !== undefined) R.email() };
  };

  // ------------------------------------------------------------------ 9. settings
  SECTIONS.settings = async el => {
    const r = await V.get(`${API}/settings`), p = r.provider, S = r.settings, nm = { email_bulk_approval_threshold: 'Approval needed above (recipients)', email_campaign_max_recipients: 'Maximum recipients per campaign', email_quiet_start_hour: 'Quiet hours start (0-23)', email_quiet_end_hour: 'Quiet hours end (0-23)', email_tracking_enabled: 'Record opens/clicks (1 = yes)', email_max_attempts: 'Delivery attempts before failing' };
    el.innerHTML = `<div class="box" style="margin-bottom:16px"><h3>Provider status</h3><div class="banner" style="margin:10px 0"><span>${p.live_delivery ? `<b>Live delivery through ${esc(p.provider)}.</b>` : `<b>${esc(p.warning || 'Real email delivery is NOT configured.')}</b>`}</span></div>
      <dl><dt>Environment</dt><dd>${esc(p.environment)}</dd><dt>Sender</dt><dd>${esc(p.from_name || '')} ${p.from_address ? `&lt;${esc(p.from_address)}&gt;` : '<span class="st st-failed">Not configured</span>'}</dd><dt>Trusted app URL (all email links)</dt><dd>${p.public_base_url ? esc(p.public_base_url) : '<span class="st st-failed">Not configured</span>'}</dd><dt>Delivery webhooks</dt><dd>${p.webhook_secret_configured ? 'Signing secret configured' : '<span class="st st-pending">Not configured: delivery cannot be confirmed</span>'}</dd><dt>Sandbox allow-list</dt><dd>${p.sandbox_allowlist_configured ? 'Configured (non-production only)' : 'None'}</dd></dl>
      <p><small>Provider credentials are environment variables on the server (${Object.keys(r.env).filter(k => k !== 'note').map(esc).join(', ')}). They are never shown or editable here. ${esc(r.env.note)}</small></p><div class="actions" style="justify-content:flex-start"><button class="btn ghost sm" id="qp">Process the queue now</button></div></div>
      <form id="sform" class="box"><h3>Sending limits</h3><div class="f-row">${Object.keys(nm).map(k => `<label class="f">${esc(nm[k])}<input name="${k}" type="number" value="${esc(S[k] ? S[k].value : '')}"><small>${esc(S[k] ? S[k].description : '')}</small></label>`).join('')}</div><label class="f">Reason for the change (audit log)<input name="reason" maxlength="300"></label><p class="neg" id="serr"></p><div class="actions"><button class="btn" type="submit">Save limits</button></div></form>`;
    $('#qp', el).onclick = async e => { const x = await run(e.target, () => V.post(`${API}/queue/process`, {})); if (x) toast(`Processed ${x.processed} message(s)${x.note ? ': ' + x.note : ''}`) };
    $('#sform', el).onsubmit = async ev => { ev.preventDefault(); const f = ev.target, settings = {}; Object.keys(nm).forEach(k => { if (f[k].value !== '' && f[k].value !== (S[k] && S[k].value)) settings[k] = +f[k].value });
      if (!Object.keys(settings).length) return toast('Nothing changed'); try { await V.put(`${API}/settings`, { settings, reason: f.reason.value }); toast('Saved'); R.email() } catch (x) { $('#serr', el).textContent = x.message } };
  };
  // ------------------------------------------------------------------ 10. Email & SMTP host configuration
  SECTIONS.smtp = async el => {
    const r = await V.get(`${API}/smtp`), S = r.settings, ok = x => x == null ? '<span class="st st-draft">Never run</span>' : x ? '<span class="st st-completed">Succeeded</span>' : '<span class="st st-failed">Failed</span>';
    const opt = (v, cur, l) => `<option value="${v}" ${cur === v ? 'selected' : ''}>${esc(l)}</option>`;
    el.innerHTML = `<div class="banner" style="margin-bottom:16px"><span>${r.smtp_active ? `<b>SMTP is the active transport.</b> ${S.enabled ? 'Sending is switched on.' : 'Sending is switched OFF: queued messages wait.'}` : `<b>${esc(r.notice)}</b>`} Accepted by the mail server is <b>not</b> the same as delivered to an inbox; SMTP gives no delivery confirmation.</span></div>
      ${S.encryption_key_configured ? '' : '<div class="banner" style="margin-bottom:16px"><span><b>SETTINGS_ENCRYPTION_KEY is not set on the server.</b> A password cannot be stored until it is.</span></div>'}
      <form id="smf" class="box" novalidate autocomplete="off"><h3>SMTP host</h3>
        <label class="f" style="flex-direction:row;gap:10px;align-items:center"><input type="checkbox" name="enabled" ${S.enabled ? 'checked' : ''}> Enable transactional email sending through this SMTP server</label>
        <div class="f-row"><label class="f">SMTP host<input name="host" value="${esc(S.host)}" placeholder="smtp.example.com" maxlength="253" autocomplete="off"></label>
          <label class="f">Port<select name="port">${S.allowed_ports.map(p => opt(String(p), String(S.port), String(p))).join('')}</select></label>
          <label class="f">Security<select name="security">${opt('starttls', S.security, 'STARTTLS (recommended, usually 587)')}${opt('ssl', S.security, 'Implicit TLS (usually 465)')}${r.environment === 'development' ? opt('none', S.security, 'None (development only)') : ''}</select></label></div>
        <div class="f-row"><label class="f">Authentication<select name="auth_method">${opt('login', S.auth_method, 'Username and password')}${opt('none', S.auth_method, 'None')}</select></label>
          <label class="f">Username${S.username_masked ? ` <small>(saved: ${esc(S.username_masked)})</small>` : ''}<input name="username" placeholder="${S.username_masked ? 'Leave blank to keep the saved username' : ''}" autocomplete="off"></label>
          <label class="f">Password<input name="password" type="password" autocomplete="new-password" placeholder="${S.password_configured ? '•••••••• saved. Leave blank to keep it' : 'Enter password'}"><small>${S.password_configured ? 'A password is stored encrypted. It is never shown again; type a new one only to replace it.' : 'Stored encrypted on the server; never shown again.'}</small></label></div>
        ${S.password_configured ? '<label class="f" style="flex-direction:row;gap:10px;align-items:center"><input type="checkbox" name="clear_password"> Remove the stored password</label>' : ''}
        <div class="f-row"><label class="f">Sender email address<input name="from_address" type="email" value="${esc(S.from_address)}" placeholder="no-reply@yourdomain.com"></label><label class="f">Sender display name<input name="from_name" value="${esc(S.from_name)}" maxlength="100" placeholder="VAULTA"></label><label class="f">Reply-to address<input name="reply_to" type="email" value="${esc(S.reply_to)}"></label></div>
        <div class="f-row"><label class="f">Public application base URL<input name="public_base_url" value="${esc(S.public_base_url)}" placeholder="https://app.yourdomain.com"><small>Origin only. Every link in every email is built from this.</small></label><label class="f">Default language<input name="default_language" value="${esc(S.default_language)}" maxlength="10"></label><label class="f">Default timezone<input name="default_timezone" value="${esc(S.default_timezone)}" maxlength="64"></label></div>
        <label class="f">Reason for the change (audit log)<input name="reason" maxlength="300"></label><p class="neg" id="smerr" role="alert"></p>
        <div class="actions"><button class="btn" type="submit">Save SMTP settings</button></div>
        <p><small>Last updated: ${esc(S.updated_at ? dt(S.updated_at) : 'never')}. The server only connects to public mail hosts on ports ${S.allowed_ports.join(', ')}; private, loopback and cloud-metadata addresses are refused.</small></p></form>
      <section class="box" style="margin-top:16px"><h3>Test the saved settings</h3>
        <p><small>Both actions use the settings as last <b>saved</b>. Save first.</small></p>
        <dl><dt>Last connection test</dt><dd>${ok(S.last_test.ok)} ${S.last_test.at ? `<small>${esc(dt(S.last_test.at))}</small>` : ''} ${S.last_test.error ? `<small>${esc(S.last_test.error)}</small>` : ''}</dd>
        <dt>Last message accepted</dt><dd>${S.last_send.ok_at ? esc(dt(S.last_send.ok_at)) : '<span class="st st-draft">None yet</span>'}</dd>
        <dt>Last send error</dt><dd>${S.last_send.error_at ? `${esc(dt(S.last_send.error_at))} <small>${esc(S.last_send.error || '')}</small>` : 'None'}</dd></dl>
        <div class="actions" style="justify-content:flex-start"><button class="btn ghost" id="smtc">Test SMTP connection</button></div><p id="smtcr"></p>
        <hr style="margin:14px 0;opacity:.2"><div class="f-row" style="grid-template-columns:2fr 1fr;align-items:end"><label class="f">Send a test email to (one address)<input id="smto" type="email" placeholder="you@example.com"></label><div class="actions" style="justify-content:flex-start"><button class="btn ghost" id="smts">Send test email</button></div></div><p id="smtsr"></p></section>`;
    $('#smf', el).onsubmit = async ev => {
      ev.preventDefault(); const f = ev.target, b = { enabled: f.enabled.checked, host: f.host.value.trim(), port: +f.port.value, security: f.security.value, auth_method: f.auth_method.value, username: f.username.value.trim(),
        from_address: f.from_address.value.trim(), from_name: f.from_name.value.trim(), reply_to: f.reply_to.value.trim(), public_base_url: f.public_base_url.value.trim(), default_language: f.default_language.value.trim(), default_timezone: f.default_timezone.value.trim(), reason: f.reason.value };
      if (f.password.value) b.password = f.password.value; if (f.clear_password && f.clear_password.checked) b.clear_password = true;
      const btn = f.querySelector('[type=submit]'); $('#smerr', el).textContent = '';
      const x = await run(btn, async () => { try { return await V.put(`${API}/smtp`, b) } catch (e) { $('#smerr', el).textContent = e.message; return null } });
      if (x) { f.password.value = ''; toast('SMTP settings saved'); R.email() }
    };
    $('#smtc', el).onclick = async e => { const x = await run(e.target, () => V.post(`${API}/smtp/test-connection`, {})); if (x) $('#smtcr', el).innerHTML = `<span class="st st-${x.ok ? 'completed' : 'failed'}">${x.ok ? 'Success' : 'Failed'}</span> ${esc(x.message)}${x.steps ? `<br><small>${x.steps.map(esc).join(' → ')}</small>` : ''}` };
    $('#smts', el).onclick = async e => { const to = $('#smto', el).value.trim(); if (!to) return toast('Enter a recipient address'); const x = await run(e.target, () => V.post(`${API}/smtp/send-test`, { to })); if (x) $('#smtsr', el).innerHTML = `<span class="st st-${x.ok ? 'processing' : 'failed'}">${esc(x.ok ? 'Accepted by server' : label(x.status))}</span> ${esc(x.message)} <small>(outbox #${esc(x.email_id)})</small>` };
  };
});
