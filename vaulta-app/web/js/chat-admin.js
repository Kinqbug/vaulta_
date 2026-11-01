/* VAULTA admin: community moderation queue (reports, remove message, mute user). Registered via VAULTA_ADMIN_EXT; the API enforces chat.moderate on every call. */
(window.VAULTA_ADMIN_EXT = window.VAULTA_ADMIN_EXT || []).push(({ R, load, V, UI, can, NAV }) => {
  const { $, esc, dt, toast, table, run, confirmBox } = UI;
  if (!can('chat.moderate')) return;
  NAV.splice(Math.max(0, NAV.findIndex(n => n[0] === 'content')) + 1, 0, ['community', 'Community', '#', 'chat.moderate']);
  let status = 'open';
  R.community = () => load(async v => {
    const j = await V.get('/api/community/moderation/reports?status=' + status);
    v.innerHTML = `<div class="top"><h1>Community moderation</h1><div class="seg" role="group" aria-label="Report status">${['open', 'actioned', 'dismissed'].map(s => `<button class="btn ${s === status ? '' : 'ghost'} sm" data-s="${s}">${s[0].toUpperCase() + s.slice(1)}</button>`).join(' ')}</div></div>
      <section class="box">${table([
        { h: 'Reported', r: x => `${dt(x.created_at)}<small>#${esc(x.channel)} · ${esc(x.reason)}</small>` },
        { h: 'Author', r: x => `${esc(x.author.name)}<small>${esc(x.author.id || '')}</small>` },
        { h: 'Message', r: x => x.message_removed ? '<small>Removed</small>' : `<div style="max-width:420px;white-space:pre-wrap;overflow-wrap:anywhere">${esc(x.content)}</div>${x.details ? `<small>Reporter note: ${esc(x.details)}</small>` : ''}` },
        { h: '', cls: 'r', r: x => x.status !== 'open' ? '' : `${x.message_removed ? '' : `<button class="btn sm" data-rm="${x.message_id}" data-id="${x.id}">Remove message</button> `}<button class="btn ghost sm" data-mute="${esc(x.author.id)}" data-id="${x.id}">Mute 1h</button> <button class="btn ghost sm" data-dis="${x.id}">Dismiss</button>` }
      ], j.reports, { empty: 'No reports in this state.' })}</section><p class="note">Removed messages stay in the database (the text is kept for audit) but are hidden from the community. Every action is written to the audit log.</p>`;
    v.onclick = async e => {
      const b = e.target.closest('button'); if (!b) return;
      if (b.dataset.s) { status = b.dataset.s; return R.community() }
      if (b.dataset.rm && await confirmBox('Remove message', 'Hide this message from the community?', { ok: 'Remove', danger: true })) { if (await run(b, () => V.del('/api/community/messages/' + b.dataset.rm), 'Message removed')) R.community() }
      if (b.dataset.mute && await confirmBox('Mute user', 'Mute this user in all channels for 1 hour?', { ok: 'Mute', danger: true })) { if (await run(b, () => V.post('/api/community/moderation/mute', { user_id: b.dataset.mute, minutes: 60, reason: 'reported content' }), 'User muted')) { await V.post(`/api/community/moderation/reports/${b.dataset.id}/resolve`, { action: 'actioned' }); R.community() } }
      if (b.dataset.dis) { if (await run(b, () => V.post(`/api/community/moderation/reports/${b.dataset.dis}/resolve`, { action: 'dismissed' }), 'Report dismissed')) R.community() }
    };
  });
});
