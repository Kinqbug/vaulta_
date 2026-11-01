/* VAULTA shared back-navigation (admin console + customer dashboard). One implementation, configured by each SPA.
   - Both apps are hash-routed, so "a previous internal page" is exactly "an earlier history entry that THIS app stamped" (history.state.vn).
     A deep link, a bookmark, a pasted URL or a page opened from another site has no stamp, so Back never calls history.back() there
     and can therefore never leave Vaulta: it goes to the page's parent instead (or the dashboard).
   - The fallback REPLACES the current entry (no new history entry), so Back cannot ping-pong between a deep link and its parent.
   - The dashboard home (admin overview / customer dashboard) never shows the button.
   - Only same-app "#/..." routes are ever navigated to. Authorisation is untouched: the target route is rendered by the normal router, which
     re-checks the session and permissions (and the server re-checks every API call).
   - Pages with unsaved edits register VNav.guard(fn): leaving by Back, the sidebar, a link or the browser's own buttons asks first. */
(() => {
  if (window.VNav) return;
  const CFG = { home: '#/dashboard', parentOf: null, titleOf: null };
  const SAFE = /^#\/[A-Za-z0-9_\-./]*(\?[A-Za-z0-9_=&%.\-]*)?$/;
  const KEY = 'vaulta:nav:' + (location.pathname.split('/')[1] || 'app');
  let hashes = []; try { hashes = JSON.parse(sessionStorage.getItem(KEY) || '[]'); } catch (e) { hashes = []; }
  let idx = (history.state && Number.isInteger(history.state.vn)) ? history.state.vn : 0;
  let guardFn = null, undoing = false, skipAsk = false, guardMsg = 'You have unsaved changes. Leave this page and discard them?';
  const cur = () => location.hash || CFG.home;
  const save = () => { try { sessionStorage.setItem(KEY, JSON.stringify(hashes.slice(-60))); } catch (e) { } };
  const stamp = () => { try { history.replaceState({ ...(history.state || {}), vn: idx }, '', location.href); } catch (e) { } };
  hashes = hashes.slice(0, idx); hashes[idx] = cur(); stamp(); save();

  const dirty = () => { try { return !!(guardFn && guardFn()); } catch (e) { return false; } };
  const parentOf = h => {
    const raw = (h || cur()).replace(/^#\//, '').split('?')[0].replace(/\/+$/, '');
    const o = CFG.parentOf && CFG.parentOf(raw);
    const p = o !== undefined && o !== null ? o : (raw.includes('/') ? raw.slice(0, raw.lastIndexOf('/')) : CFG.home.slice(2));
    const t = '#/' + p; return SAFE.test(t) ? t : CFG.home;
  };
  const canBack = () => idx > 0;
  const fallback = () => { const p = parentOf(); return p === cur().split('?')[0] ? CFG.home : p; };
  const hasTarget = () => canBack() || fallback() !== cur().split('?')[0];

  /* hashchange runs BEFORE the app's router (this file loads first). A refused navigation stops the router and is undone. */
  addEventListener('hashchange', e => {
    if (undoing) { undoing = false; e.stopImmediatePropagation(); return; }
    const s = history.state && Number.isInteger(history.state.vn) ? history.state.vn : null, was = idx;
    let to = s, kind = 'move';
    if (s === null) { to = was + 1; kind = 'push'; }
    const asked = skipAsk; skipAsk = false;
    if (!asked && dirty() && !confirm(guardMsg)) {
      e.stopImmediatePropagation(); undoing = true;
      if (kind === 'push') history.back(); else history.go(was - to);
      return;
    }
    idx = to; if (kind === 'push') hashes = hashes.slice(0, idx); hashes[idx] = cur(); if (kind === 'push') stamp(); save();
    guardFn = null;                                                  // a guard belongs to the page it was registered on
  }, true);
  addEventListener('beforeunload', e => { if (dirty()) { e.preventDefault(); e.returnValue = ''; } });

  const go = t => { if (!SAFE.test(t)) t = CFG.home; if (t === cur()) return; location.hash = t; };
  function back() {
    if (dirty() && !confirm(guardMsg)) return;
    skipAsk = true;                                                  // already confirmed: the hashchange below must not ask again
    if (canBack()) { history.back(); return; }
    const t = fallback(); guardFn = null; skipAsk = false;
    try { history.replaceState({ vn: 0 }, '', location.pathname + location.search + t); } catch (e) { }
    idx = 0; hashes = [t]; save(); dispatchEvent(new HashChangeEvent('hashchange'));   // history.replaceState does not fire it
  }
  const ICON = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M19 12H5"/><path d="m12 19-7-7 7-7"/></svg>';
  /* Fills (or empties) the bar element for the page that was just routed. The button always has a real destination, or it is not drawn. */
  function render(el) {
    if (!el) return; if (cur().split('?')[0] === CFG.home || !hasTarget()) { el.hidden = true; el.innerHTML = ''; return; }   // never on the dashboard home: it is the root
    const dest = canBack() ? hashes[idx - 1] : fallback(), name = CFG.titleOf ? CFG.titleOf((dest || '').replace(/^#\//, '').split('?')[0]) : '';
    el.hidden = false;
    el.innerHTML = `<button type="button" class="vback" data-vback aria-label="Go back"${name ? ` title="Back to ${name.replace(/"/g, '&quot;')}"` : ''}>${ICON}</button>`;
  }
  document.addEventListener('click', e => { const b = e.target.closest && e.target.closest('[data-vback]'); if (b) { e.preventDefault(); back(); } });
  window.VNav = {
    configure: o => Object.assign(CFG, o), render, back, go, canGoBack: canBack, parentOf, fallback,
    guard: (fn, msg) => { guardFn = fn; if (msg) guardMsg = msg; }, clearGuard: () => { guardFn = null; }, isDirty: dirty, icon: ICON,
    _state: () => ({ idx, hashes: hashes.slice() })
  };
})();
