/* VAULTA admin: Legal management (documents, drafts, preview, publishing, history, acceptance records, legal configuration).
   Registered through window.VAULTA_ADMIN_EXT. Buttons are shown according to permissions, but the API re-checks legal.read / legal.manage /
   legal.publish on every call. Published versions are immutable: changes are made by creating and publishing a new version. */
(window.VAULTA_ADMIN_EXT = window.VAULTA_ADMIN_EXT || []).push(({ R, load, V, UI, can, NAV }) => {
  const { $, $$, esc, dt, day, label, toast, table, modal, confirmBox, run, badge, pager } = UI;
  if (!can('legal.read')) return;
  const at = NAV.findIndex(n => n[0] === 'content');
  NAV.splice(at < 0 ? NAV.length : at + 1, 0, ['legal', 'Legal', '§', 'legal.read']);
  const head = (t, a = '') => `<div class="top"><h1>${esc(t)}</h1><div style="display:flex;gap:8px;flex-wrap:wrap">${a}</div></div>`;
  const who = u => u ? esc(u.email) : '<small>system</small>';
  const ST = { PUBLISHED: 'completed', DRAFT: 'pending', ARCHIVED: 'draft' };
  const sb = s => `<span class="st st-${ST[s] || 'pending'}">${esc(s.charAt(0) + s.slice(1).toLowerCase())}</span>`;
  const ATYPE = { TERMS_ACCEPTANCE: 'Terms accepted', PRIVACY_NOTICE_ACKNOWLEDGEMENT: 'Privacy acknowledged', RISK_DISCLOSURE_ACKNOWLEDGEMENT: 'Risk disclosure acknowledged' };
  let ACC = { slug: '', type: '', page: 1 };

  R.legal = () => load(async v => {
    const [l, s] = await Promise.all([V.get('/api/admin/legal'), V.get('/api/admin/legal/status')]);
    const cols = [
      { h: 'Document', r: d => `<b>${esc(d.title)}</b><small>/legal/${esc(d.slug)}/ · ${d.required ? 'required' : esc(d.region + ' supplement')}</small>` },
      { h: 'Published', r: d => d.published ? `${sb('PUBLISHED')} v${d.published.version}<small>effective ${day(d.published.effective_at)}</small>` : '<span class="st st-rejected">Not published</span>' },
      { h: 'Published by', r: d => d.published ? `${who(d.published.published_by)}<small>${dt(d.published.published_at)}</small>` : '' },
      { h: 'Draft', r: d => d.draft ? `${sb('DRAFT')} v${d.draft.version}<small>${dt(d.draft.updated_at)}</small>` : '<small>none</small>' },
      { h: 'Open details', r: d => d.unresolved.length ? `<span class="st st-pending">${d.unresolved.length} unresolved</span>` : '<small>complete</small>' },
      { h: '', cls: 'r', r: d => `<button class="btn ghost sm" data-h="${esc(d.slug)}">History</button> ${can('legal.manage') ? (d.draft ? `<button class="btn sm" data-e="${d.draft.id}">Edit draft</button>` : `<button class="btn sm" data-n="${esc(d.slug)}">New draft</button>`) : ''}` }
    ];
    const miss = s.missing_config.length;
    v.innerHTML = head('Legal management', `<button class="btn ghost" id="lcfg">Legal configuration</button>${can('legal.manage') ? '<button class="btn ghost" id="lsup">New regional supplement</button>' : ''}`) +
      `<div class="banner" style="margin-bottom:16px"><span><b>${s.ready ? 'Ready for production review' : 'Not ready for production'}.</b> ${miss ? `${miss} required legal detail${miss === 1 ? ' is' : 's are'} not configured (${s.missing_config.map(m => esc(m.label)).join(', ')}). ` : ''}${s.env === 'production' && miss ? 'Publishing is blocked until these are supplied. ' : ''}Documents are drafts of legal text and must be reviewed by qualified legal counsel.</span></div>` +
      `<section class="box">${table(cols, l.documents)}</section>
       <section class="box" style="margin-top:18px"><h3>Acceptance records</h3><div id="acc"></div></section>`;
    v.onclick = async e => {
      const b = e.target.closest('button'); if (!b) return;
      if (b.dataset.h) history(b.dataset.h);
      if (b.dataset.e) editor(+b.dataset.e);
      if (b.dataset.n) { const r = await run(b, () => V.post(`/api/admin/legal/documents/${b.dataset.n}/drafts`)); if (r) { toast('Draft created'); editor(r.id) } }
    };
    $('#lcfg').onclick = config; const sp = $('#lsup'); if (sp) sp.onclick = supplement;
    acceptances(l.documents);
  });

  async function acceptances(docs) {
    const box = $('#acc'); if (!box) return;
    if (!can('users.read')) { box.innerHTML = '<div class="empty">Viewing acceptance records also requires the users.read permission.</div>'; return }
    const q = new URLSearchParams({ page: ACC.page, size: 15 }); if (ACC.slug) q.set('slug', ACC.slug); if (ACC.type) q.set('type', ACC.type);
    let j; try { j = await V.get('/api/admin/legal/acceptances?' + q) } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return }
    box.innerHTML = `<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px"><select id="af1" aria-label="Document"><option value="">All documents</option>${docs.map(d => `<option value="${esc(d.slug)}" ${ACC.slug === d.slug ? 'selected' : ''}>${esc(d.title)}</option>`).join('')}</select>
      <select id="af2" aria-label="Type"><option value="">All types</option>${Object.keys(ATYPE).map(k => `<option value="${k}" ${ACC.type === k ? 'selected' : ''}>${ATYPE[k]}</option>`).join('')}</select></div>` +
      table([{ h: 'When', r: a => dt(a.accepted_at) }, { h: 'User', r: a => `${esc(a.email)}<small>${esc(a.user)}</small>` }, { h: 'Record', r: a => esc(ATYPE[a.type] || a.type) }, { h: 'Document', r: a => `${esc(a.slug)} v${a.version}` }, { h: 'Content hash', r: a => `<code>${esc(a.hash)}</code>` }], j.acceptances, { empty: 'No acceptance records match.' }) + pager(j, () => { });
    $('#af1').onchange = e => { ACC.slug = e.target.value; ACC.page = 1; acceptances(docs) }; $('#af2').onchange = e => { ACC.type = e.target.value; ACC.page = 1; acceptances(docs) };
    box.onclick = e => { const p = e.target.closest('[data-pg]'); if (p && !p.disabled) { ACC.page = +p.dataset.pg; acceptances(docs) } };
  }

  async function history(slug) {
    const j = await V.get(`/api/admin/legal/documents/${slug}/versions`);
    const d = modal(`${j.document.title} — version history`, table([
      { h: 'Version', r: x => `v${x.version} ${sb(x.status)}${x.material ? '<small>material</small>' : ''}` },
      { h: 'Effective', r: x => x.effective_at ? day(x.effective_at) : '' },
      { h: 'Published', r: x => x.published_at ? `${dt(x.published_at)}<small>${who(x.published_by)}</small>` : '' },
      { h: 'Created by', r: x => `${who(x.created_by)}<small>${dt(x.created_at)}</small>` },
      { h: 'Change note', r: x => esc(x.change_note || '') },
      { h: '', cls: 'r', r: x => `<button class="btn ghost sm" data-v="${x.id}">View</button>${x.status === 'DRAFT' && can('legal.manage') ? ` <button class="btn sm" data-ed="${x.id}">Edit</button> <button class="btn ghost sm" data-ar="${x.id}">Discard</button>` : ''}${x.status === 'PUBLISHED' && can('legal.manage') && !j.document.required ? ` <button class="btn ghost sm" data-ar="${x.id}">Archive</button>` : ''}` }
    ], j.versions), { wide: true });
    d.onclick = async e => {
      const b = e.target.closest('button'); if (!b) return;
      if (b.dataset.v) { d.close(); preview(+b.dataset.v) }
      if (b.dataset.ed) { d.close(); editor(+b.dataset.ed) }
      if (b.dataset.ar && await confirmBox('Archive version', 'Archived versions stay in the history and acceptance records; they can no longer be published or edited.', { ok: 'Archive', danger: true })) {
        const r = await run(b, () => V.post(`/api/admin/legal/versions/${b.dataset.ar}/archive`)); if (r) { d.close(); toast('Archived'); R.legal() }
      }
    };
  }

  async function preview(id) {
    const j = await V.get('/api/admin/legal/versions/' + id);
    modal(`${j.version.title} — v${j.version.version} (${j.version.status.toLowerCase()})`, `<p><small>Hash ${esc(j.version.content_hash.slice(0, 16))}… · ${j.preview.unresolved.length ? esc(j.preview.unresolved.length + ' unresolved detail(s)') : 'no unresolved details'}</small></p><div class="lg-preview" style="max-height:60vh;overflow:auto;line-height:1.65">${j.preview.html}</div>`, { wide: true });
  }

  async function editor(id) {
    const j = await V.get('/api/admin/legal/versions/' + id), x = j.version;
    if (x.status !== 'DRAFT') return preview(id);
    const d = modal(`Edit draft — ${x.title} (v${x.version})`, `<form id="lf" novalidate>
      <div class="f-row"><label class="f">Title<input name="title" maxlength="200" required value="${esc(x.title)}"></label><label class="f">Effective date (YYYY-MM-DD)<input name="effective_at" placeholder="${esc(new Date().toISOString().slice(0, 10))}" value="${esc((x.effective_at || '').slice(0, 10))}"></label></div>
      <label class="f">Change note (required before publishing a new version)<input name="change_note" maxlength="500" value="${esc(x.change_note || '')}"></label>
      <label class="chk" style="display:flex;gap:8px;align-items:center;margin:6px 0"><input type="checkbox" name="material" ${x.material ? 'checked' : ''}> <span>Material change: users must re-acknowledge (Risk Disclosure) and new sign-ups see this version</span></label>
      <div class="actions" style="justify-content:flex-start;margin:8px 0" role="toolbar" aria-label="Formatting">${[['H2', '### ', ''], ['H3', '#### ', ''], ['B', '**', '**'], ['I', '*', '*'], ['• List', '* ', ''], ['1. List', '1. ', ''], ['Link', '[', '](https://)']].map(([l, a, b]) => `<button type="button" class="btn ghost sm" data-f="${esc(a)}" data-g="${esc(b)}">${esc(l)}</button>`).join('')}</div>
      <label class="f">Content (Markdown: headings, paragraphs, lists, bold/italic, links, tables). Raw HTML is never rendered.<textarea name="content" id="lc" rows="16" style="font-family:ui-monospace,monospace;font-size:13px">${esc(x.content)}</textarea></label>
      <p class="neg" id="lm" role="alert"></p>
      <div class="actions"><button type="button" class="btn ghost" id="lp">Preview</button><button type="submit" class="btn ghost" id="ls">Save draft</button>${can('legal.publish') ? '<button type="button" class="btn" id="lpub">Publish…</button>' : ''}</div></form>
      <div id="lpv" class="lg-preview" style="margin-top:14px;max-height:40vh;overflow:auto;line-height:1.65" hidden></div>`, { wide: true });
    const form = $('#lf', d), data = () => { const f = new FormData(form); return { title: f.get('title'), content: f.get('content'), change_note: f.get('change_note'), effective_at: f.get('effective_at') || null, material: form.material.checked } };
    d.addEventListener('click', e => { const b = e.target.closest('[data-f]'); if (!b) return; const t = $('#lc', d), s = t.selectionStart, en = t.selectionEnd, a = b.dataset.f, g = b.dataset.g; t.setRangeText(a + (t.value.slice(s, en) || 'text') + g, s, en, 'end'); t.focus() });
    const save = async () => { try { await V.put('/api/admin/legal/versions/' + id, data()); return true } catch (err) { $('#lm', d).textContent = err.message; return false } };
    form.onsubmit = async e => { e.preventDefault(); if (await save()) { d.close(); toast('Draft saved'); R.legal() } };
    $('#lp', d).onclick = async () => { try { const r = await V.post('/api/admin/legal/preview', { content: form.content.value, effective_at: form.effective_at.value || undefined }); const p = $('#lpv', d); p.hidden = false; p.innerHTML = (r.unresolved.length ? `<div class="note warn">Unresolved details: ${esc(r.unresolved.join(', '))}</div>` : '') + r.html } catch (err) { $('#lm', d).textContent = err.message } };
    const pub = $('#lpub', d); if (pub) pub.onclick = async () => {
      if (!await save()) return;
      if (!await confirmBox('Publish this version?', `Publishing v${x.version} immediately replaces the current published version for all visitors${form.material.checked ? ' and requires users to re-acknowledge where applicable' : ''}. The previous version is kept in the history. This cannot be undone; changes require a new version.`, { ok: 'Publish' })) return;
      try { await V.post(`/api/admin/legal/versions/${id}/publish`, { confirm: true, effective_at: form.effective_at.value || undefined }); d.close(); toast('Published'); R.legal() }
      catch (err) { $('#lm', d).textContent = err.message + (err.details && err.details.missing ? ' Missing: ' + err.details.missing.join(', ') : '') }
    };
  }

  async function config() {
    const j = await V.get('/api/admin/legal/config'), canEdit = can('legal.manage');
    const d = modal('Legal configuration', `<p class="note">These details are substituted into the legal documents. Leave a value empty only if it is genuinely not yet known: unresolved details stay highlighted on the public pages. Do not enter values you cannot verify.</p>
      <form id="cf2" novalidate>${Object.keys(j.fields).map(k => `<label class="f">${esc(j.fields[k])}<input name="${k}" maxlength="400" value="${esc(j.config[k] || '')}" ${canEdit ? '' : 'disabled'}></label>`).join('')}<p class="neg" id="cm2" role="alert"></p>${canEdit ? '<div class="actions"><button class="btn">Save configuration</button></div>' : ''}</form>`, { wide: true });
    $('#cf2', d).onsubmit = async e => { e.preventDefault(); const f = {}; new FormData(e.target).forEach((v, k) => f[k] = v); try { await V.put('/api/admin/legal/config', { config: f }); d.close(); toast('Configuration saved'); R.legal() } catch (err) { $('#cm2', d).textContent = err.message } };
  }

  function supplement() {
    const d = modal('New regional supplement', `<p class="note">A supplement is an additional document (for example a regional privacy notice). It starts as a draft and is only public after it is published. It does not change the global master documents.</p>
      <form id="sf" novalidate><div class="f-row"><label class="f">Slug (a-z, 0-9, hyphens)<input name="slug" required pattern="[a-z0-9][a-z0-9-]{1,59}"></label><label class="f">Region<input name="region" maxlength="40" placeholder="EU"></label></div>
      <label class="f">Title<input name="title" required maxlength="200"></label><label class="f">Content (Markdown)<textarea name="content" rows="10" required></textarea></label><p class="neg" id="sm" role="alert"></p><div class="actions"><button class="btn">Create draft</button></div></form>`, { wide: true });
    $('#sf', d).onsubmit = async e => { e.preventDefault(); const f = {}; new FormData(e.target).forEach((v, k) => f[k] = v); try { await V.post('/api/admin/legal/documents', f); d.close(); toast('Supplement draft created'); R.legal() } catch (err) { $('#sm', d).textContent = err.message } };
  }
});
