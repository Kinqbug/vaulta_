/* VAULTA Trade terminal. Replaces the dashboard's Trade route (R.trade) with: market explorer, interactive chart, order panel, market details,
   order book / recent trades (only where real data exists), community chat and the user's orders. All market data comes from /api/terminal (server-side provider). */
(window.VAULTA_EXT = window.VAULTA_EXT || []).push(({ R, load, V, UI }) => {
  const { $, $$, esc, toast, modal, confirmBox, run, usd, label, dt, badge } = UI;
  const S = window.__tmState = window.__tmState || { tab: 'all', q: '', sort: 'market_cap', dir: 'desc', coin: 'bitcoin', tf: '1D', mode: 'candles', lower: 'details', m: 'chart', exOpen: true, wide: false, chan: null, ex: {} };
  let ME = null;
  const num = v => (v === null || v === undefined || isNaN(v)) ? null : Number(v);
  const fP = p => { p = num(p); if (p === null) return '—'; const a = Math.abs(p); const d = a >= 1000 ? 2 : a >= 1 ? 4 : a >= .01 ? 5 : 8; return '$' + p.toLocaleString('en-US', { minimumFractionDigits: a >= 1000 ? 2 : 2, maximumFractionDigits: d }) };
  const fAx = p => { p = num(p); if (p === null) return ''; const a = Math.abs(p); return (a >= 1000 ? p.toLocaleString('en-US', { maximumFractionDigits: 2, minimumFractionDigits: 2 }) : a >= 1 ? p.toFixed(3) : a >= .01 ? p.toFixed(5) : p.toPrecision(4)) };
  const fC = n => { n = num(n); if (n === null) return '—'; const a = Math.abs(n); const u = [[1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'K']].find(x => a >= x[0]); return '$' + (u ? (n / u[0]).toFixed(2) + u[1] : n.toFixed(2)) };
  const fN = n => { n = num(n); return n === null ? '—' : n >= 1e9 ? (n / 1e9).toFixed(2) + 'B' : n >= 1e6 ? (n / 1e6).toFixed(2) + 'M' : n.toLocaleString('en-US', { maximumFractionDigits: 2 }) };
  const pct = v => { v = num(v); return v === null ? '—' : (v > 0 ? '+' : '') + v.toFixed(2) + '%' };
  const dir = v => num(v) === null ? 'mu' : v >= 0 ? 'up' : 'dn';
  const ago = s => s < 90 ? s + 's' : s < 5400 ? Math.round(s / 60) + 'm' : Math.round(s / 3600) + 'h';
  const img = (c, cls = '') => c.image ? `<img src="${esc(c.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" class="${cls}">` : `<span class="tm-ph ${cls}">${esc((c.symbol || '?').slice(0, 2))}</span>`;
  const spark = c => { const p = c.sparkline; if (!p || p.length < 2) return '<span class="mu">—</span>'; const lo = Math.min(...p), hi = Math.max(...p), W = 70, H = 22, pts = p.map((v, i) => `${(i / (p.length - 1) * W).toFixed(1)},${(H - 2 - (v - lo) / ((hi - lo) || 1) * (H - 4)).toFixed(1)}`).join(' '); return `<svg class="tm-spark" viewBox="0 0 ${W} ${H}" aria-hidden="true"><polyline fill="none" stroke="${p[p.length - 1] >= p[0] ? '#26d9a0' : '#f0447f'}" stroke-width="1.5" points="${pts}"/></svg>` };
  const CHAN = { bitcoin: 'bitcoin', ethereum: 'ethereum', solana: 'solana' };
  const pill = (meta, err) => { if (err) return `<span class="tm-pill err"><i></i>Market data unavailable</span>`; if (!meta) return ''; return meta.stale ? `<span class="tm-pill stale" title="The provider could not be reached (${esc(meta.stale_reason || 'error')}). Showing the last data received."><i></i>Stale · ${ago(meta.age_seconds)} old</span>` : `<span class="tm-pill live"><i></i>${meta.source === 'cache' ? 'Cached' : 'Live'} · updated ${ago(meta.age_seconds)} ago</span>` };

  R.trade = () => load(async v => {
    try { ME = (await V.get('/api/auth/me')).user } catch (e) { ME = ME || {} }
    const isMod = (ME.permissions || []).includes('chat.moderate');
    v.innerHTML = `<div class="tm" id="tm" data-m="${S.m}">
      <div class="tm-mtabs" role="tablist" aria-label="Trade sections">${[['markets', 'Markets'], ['chart', 'Chart'], ['trade', 'Trade'], ['book', 'Book'], ['community', 'Chat']].map(([k, l]) => `<button type="button" role="tab" data-m="${k}" class="${S.m === k ? 'on' : ''}" aria-selected="${S.m === k}">${l}</button>`).join('')}</div>
      <div class="tm-grid">
        <aside class="tm-ex tm-panel" id="ex" aria-label="Market explorer"></aside>
        <section class="tm-main tm-panel" id="main" aria-label="Selected market"></section>
        <aside class="tm-ord tm-panel" id="ord" aria-label="Order panel"></aside>
        <section class="tm-low tm-panel" id="low" aria-label="Market information"></section>
      </div></div>`;
    const root = $('#tm', v); root.classList.toggle('ex-closed', !S.exOpen); root.classList.toggle('ex-wide', !!S.wide);
    const T = { rows: [], page: 1, hasMore: false, meta: null, err: null, loading: false, seq: 0, detail: null, dmeta: null, derr: null, cmeta: null, ctx: null, orders: [], chart: null, chat: null, timers: [], last: { m: 0, c: 0, ch: 0 }, cseq: 0, dseq: 0 };
    const alive = () => root.isConnected && location.hash.startsWith('#/trade');
    const cleanup = () => { T.timers.forEach(clearInterval); T.timers = []; if (T.chart) { T.chart.destroy(); T.chart = null } if (T.chat) { T.chat.destroy(); T.chat = null } };
    T.timers.push(setInterval(() => { if (!alive()) return cleanup(); if (document.hidden) return; const n = Date.now(); if (n - T.last.m > 60000) loadMarkets(false, true); if (n - T.last.c > 60000) loadCoin(true); if (n - T.last.ch > 300000) loadChart(true) }, 5000));

    v.addEventListener('error', e => { const i = e.target; if (i && i.tagName === 'IMG' && i.closest('#tm')) { const s = document.createElement('span'); s.className = 'tm-ph ' + (i.className || ''); s.textContent = (i.closest('tr') ? i.closest('tr').dataset.id : S.coin).slice(0, 2); i.replaceWith(s) } }, true);
    /* ---------------- explorer ---------------- */
    const exEl = $('#ex', v);
    const drawEx = () => {
      const wide = S.wide, sortBtn = (k, l) => `<th scope="col" ${S.sort === k ? `aria-sort="${S.dir === 'asc' ? 'ascending' : 'descending'}"` : ''}><button type="button" data-sort="${k}">${l}${S.sort === k ? (S.dir === 'asc' ? ' ▲' : ' ▼') : ''}</button></th>`;
      const rows = T.rows.map(c => `<tr class="row ${c.id === S.coin ? 'sel' : ''}" data-id="${esc(c.id)}" tabindex="0" aria-label="${esc(c.name)}">
        <td><div class="tm-asset"><button class="tm-star ${c.favorite ? 'on' : ''}" data-fav="${esc(c.id)}" aria-label="${c.favorite ? 'Remove' : 'Add'} ${esc(c.name)} ${c.favorite ? 'from' : 'to'} favourites" aria-pressed="${c.favorite}">${c.favorite ? '★' : '☆'}</button>${img(c)}<span style="min-width:0"><b>${esc(c.symbol)}${c.trade.enabled ? '<span class="tm-tag t" title="Vaulta offers simulated orders for this asset">Trade</span>' : ''}</b><small>${esc(c.name)}${c.rank ? ' · #' + c.rank : ''}</small></span></div></td>
        <td>${fP(c.price)}${wide ? '' : `<br><small class="${dir(c.change_24h_pct)}">${pct(c.change_24h_pct)}</small>`}</td>
        <td class="c-wide ${dir(c.change_24h_pct)}">${pct(c.change_24h_pct)}</td><td class="c-wide ${dir(c.change_7d_pct)}">${pct(c.change_7d_pct)}</td><td class="c-wide">${fC(c.market_cap)}</td><td class="c-wide">${fC(c.volume_24h)}</td><td class="c-wide">${fN(c.circulating_supply)}</td><td class="c-wide">${spark(c)}</td></tr>`).join('');
      const body = T.loading && !T.rows.length ? Array.from({ length: 9 }, () => `<div class="tm-skel" style="height:44px;margin:8px 12px"></div>`).join('')
        : T.err && !T.rows.length ? `<div class="tm-empty"><b>Market data unavailable</b><br>${esc(T.err.message)}<br><button class="tm-loadmore" data-retry>Try again</button></div>`
        : !T.rows.length ? `<div class="tm-empty">${S.tab === 'favorites' && !S.q ? 'No favourites yet. Tap ☆ next to a coin to add it.' : 'No coins match your search or filter.'}</div>`
        : `<table class="tm-tbl ${wide ? '' : 'tm-compact'}"><thead><tr><th scope="col">Asset</th>${sortBtn('price', 'Price')}${wide ? sortBtn('change_24h', '24h') + sortBtn('change_7d', '7d') + sortBtn('market_cap', 'Market cap') + sortBtn('volume', '24h volume') + '<th scope="col" class="c-wide">Circulating</th><th scope="col" class="c-wide">7d chart</th>' : ''}</tr></thead><tbody>${rows}</tbody></table>${T.hasMore ? `<button class="tm-loadmore" data-more ${T.loading ? 'disabled' : ''}>${T.loading ? 'Loading…' : 'Load more'}</button>` : ''}`;
      exEl.innerHTML = `<div class="tm-exh"><h2>Markets</h2><button class="tm-ib" data-wide title="${wide ? 'Back to terminal' : 'Expand to full table'}" aria-label="${wide ? 'Back to terminal' : 'Expand markets table'}">${wide ? '⤡' : '⤢'}</button><button class="tm-ib" data-collapse title="Hide markets" aria-label="Hide markets panel">«</button></div>
        <input class="tm-search" id="qs" type="search" placeholder="Search coins by name or symbol" aria-label="Search coins" value="${esc(S.q)}" autocomplete="off">
        <div class="tm-tabs" role="tablist" aria-label="Market filters">${[['all', 'All'], ['trending', 'Trending'], ['gainers', 'Top gainers'], ['losers', 'Top losers'], ['favorites', '★ Favourites']].map(([k, l]) => `<button role="tab" type="button" data-tab="${k}" class="${S.tab === k && !S.q ? 'on' : ''}" aria-selected="${S.tab === k && !S.q}">${l}</button>`).join('')}</div>
        <div class="tm-scope">${T.scope ? esc(T.scope) : 'Market data only unless tagged “Trade”.'}</div><div class="tm-list" id="exl">${body}</div>
        <div class="tm-fresh"><span>${pill(T.meta, T.err && !T.rows.length)}</span><span>Data: CoinGecko</span></div>`;
      const q = $('#qs', exEl); if (S.focusQ) { q.focus(); q.setSelectionRange(q.value.length, q.value.length) }
    };
    async function loadMarkets(reset, quiet) {
      if (T.loading && !reset) return; const seq = ++T.seq; if (reset) { T.page = 1; if (!quiet) T.rows = [] } T.loading = !quiet; T.last.m = Date.now(); if (!quiet) drawEx();
      const p = new URLSearchParams({ tab: S.tab, sort: S.sort, dir: S.dir, page: reset ? 1 : T.page, per_page: 50 }); if (S.q) p.set('q', S.q);
      try {
        const j = await V.get('/api/terminal/markets?' + p); if (seq !== T.seq || !alive()) return;
        if (quiet && !reset) { const pos = T.rows.length; if (T.page === 1 || pos <= 50) T.rows = j.coins; else { const m = new Map(j.coins.map(c => [c.id, c])); T.rows = T.rows.map(c => m.get(c.id) || c) } } else T.rows = reset ? j.coins : T.rows.concat(j.coins);
        T.hasMore = j.has_more; T.meta = j.meta; T.scope = j.scope; T.err = null; if (!reset) T.page = j.page
      } catch (e) { if (seq !== T.seq || !alive()) return; T.err = e; if (!T.rows.length) T.meta = null; else if (T.meta) T.meta = { ...T.meta, stale: true, stale_reason: e.details?.reason || 'error' } }
      T.loading = false; drawEx(); if (!T.detail && !S.focusQ) header();
    }
    exEl.addEventListener('click', e => {
      const t = e.target;
      if (t.closest('[data-fav]')) { e.stopPropagation(); return toggleFav(t.closest('[data-fav]').dataset.fav) }
      if (t.closest('[data-tab]')) { S.tab = t.closest('[data-tab]').dataset.tab; S.q = ''; S.sort = 'market_cap'; S.dir = 'desc'; return loadMarkets(true) }
      if (t.closest('[data-sort]')) { const k = t.closest('[data-sort]').dataset.sort; S.dir = S.sort === k ? (S.dir === 'desc' ? 'asc' : 'desc') : 'desc'; S.sort = k; return loadMarkets(true) }
      if (t.closest('[data-more]')) { T.page++; return loadMarkets(false) }
      if (t.closest('[data-retry]')) return loadMarkets(true);
      if (t.closest('[data-wide]')) { S.wide = !S.wide; root.classList.toggle('ex-wide', S.wide); drawEx(); return S.wide || (T.chart && T.chart.resize()) }
      if (t.closest('[data-collapse]')) { if (innerWidth <= 1100) exEl.classList.remove('drawer'); S.exOpen = false; root.classList.add('ex-closed'); showOpenBtn(); return T.chart && setTimeout(() => T.chart.resize(), 50) }
      const row = t.closest('tr.row'); if (row) { select(row.dataset.id); if (S.wide) { S.wide = false; root.classList.remove('ex-wide'); drawEx() } if (innerWidth <= 1100) exEl.classList.remove('drawer'); if (innerWidth <= 760) setM('chart') }
    });
    exEl.addEventListener('keydown', e => { const row = e.target.closest && e.target.closest('tr.row'); if (row && (e.key === 'Enter' || e.key === ' ') && !e.target.closest('[data-fav]')) { e.preventDefault(); row.click() } });
    let qt; exEl.addEventListener('input', e => { if (e.target.id !== 'qs') return; clearTimeout(qt); S.q = e.target.value.trim(); S.focusQ = true; qt = setTimeout(() => { loadMarkets(true).then(() => { S.focusQ = false }) }, 400) });
    async function toggleFav(id) {
      const c = T.rows.find(x => x.id === id) || (T.detail && T.detail.id === id && T.detail), on = !(c && c.favorite); if (c) c.favorite = on; drawEx(); header();
      try { on ? await V.put('/api/terminal/favorites/' + encodeURIComponent(id), {}) : await V.del('/api/terminal/favorites/' + encodeURIComponent(id)); if (S.tab === 'favorites' && !on) loadMarkets(true, true) }
      catch (e) { if (c) c.favorite = !on; drawEx(); header(); toast(e.message) }
    }
    function showOpenBtn() { if ($('#exopen', v)) return; const b = document.createElement('button'); b.id = 'exopen'; b.className = 'tm-ib'; b.title = 'Show markets'; b.setAttribute('aria-label', 'Show markets panel'); b.textContent = '»'; b.style.marginRight = '8px'; b.onclick = () => { S.exOpen = true; root.classList.remove('ex-closed'); b.remove(); T.chart && setTimeout(() => T.chart.resize(), 50) }; $('#main', v).prepend(b) }
    if (!S.exOpen) showOpenBtn();

    /* ---------------- selected coin: header, chart, stats ---------------- */
    const row0 = () => T.rows.find(c => c.id === S.coin) || {};
    const cur = () => Object.assign({}, row0(), T.detail && T.detail.id === S.coin ? Object.fromEntries(Object.entries(T.detail).filter(([, x]) => x !== null && x !== undefined)) : {});
    function header() {
      const c = cur(), id = S.coin, mainEl = $('#main', v); if (!mainEl) return; const keep = $('#chartHost', mainEl);
      const open = $('#exopen', mainEl);
      const top = `<div class="tm-coin"><div class="tm-id">${c.image ? img(c) : `<span class="tm-ph">${esc((c.symbol || id).slice(0, 2).toUpperCase())}</span>`}<div><h1>${esc(c.name || id)} <span class="mu">${esc(c.symbol || '')}</span><button class="tm-star ${c.favorite ? 'on' : ''}" data-fav="${esc(id)}" aria-pressed="${!!c.favorite}" aria-label="${c.favorite ? 'Remove from' : 'Add to'} favourites">${c.favorite ? '★' : '☆'}</button>${c.trade ? (c.trade.enabled ? '<span class="tm-tag t">Tradable · simulated</span>' : '<span class="tm-tag v">Market data only</span>') : ''}</h1>
        <div><span class="tm-price" id="px">${fP(c.price)}</span><span class="tm-chg ${dir(c.change_24h_pct)}">${c.change_24h_abs != null ? (c.change_24h_abs >= 0 ? '▲ ' : '▼ ') + fP(Math.abs(c.change_24h_abs)) + ' ' : ''}(${pct(c.change_24h_pct)})</span></div></div></div>
        <div>${T.derr ? pill(null, true) : pill(T.dmeta || T.meta)}</div></div>`;
      const tools = `<div class="tm-tools"><div class="tm-seg" id="tfs" role="group" aria-label="Timeframe">${['1D', '7D', '30D', '90D', '1Y'].map(t => `<button type="button" data-tf="${t}" class="${S.tf === t ? 'on' : ''}" aria-pressed="${S.tf === t}">${t}</button>`).join('')}</div>
        <div style="display:flex;gap:8px;align-items:center"><span class="tm-note" id="gran"></span><div class="tm-seg" role="group" aria-label="Chart type">${[['candles', 'Candles'], ['line', 'Line'], ['area', 'Area']].map(([k, l]) => `<button type="button" data-mode="${k}" class="${S.mode === k ? 'on' : ''}" aria-pressed="${S.mode === k}">${l}</button>`).join('')}</div><button type="button" class="tm-ib" data-reset title="Reset zoom" aria-label="Reset zoom" style="width:auto;padding:0 10px">Reset</button></div></div>`;
      if (!keep) { mainEl.innerHTML = top + tools + `<div class="tm-chartwrap" id="chartHost"></div><div id="stats"></div>`; mountChart() } else { const t = document.createElement('div'); t.innerHTML = top + tools; const nodes = [...t.children]; const old = [...mainEl.children].filter(x => x.id !== 'chartHost' && x.id !== 'stats' && x.id !== 'exopen' && x.id !== 'exdrawer'); old.forEach(x => x.remove()); nodes.slice(0, 2).reverse().forEach(n => mainEl.prepend(n)) }
      const drw = $('#exdrawer', mainEl); if (drw) mainEl.prepend(drw); if (open) mainEl.prepend(open); stats(); const g = $('#gran', mainEl); if (g && T.granularity) g.textContent = 'Candles: ' + T.granularity;
    }
    function stats() {
      const c = cur(), el = $('#stats', v); if (!el) return; const L = (l, val) => `<div class="tm-stat"><small>${l}</small><b>${val}</b></div>`;
      el.innerHTML = `<div class="tm-stats">${L('Market cap', fC(c.market_cap))}${L('24h volume', fC(c.volume_24h))}${L('24h high', fP(c.high_24h))}${L('24h low', fP(c.low_24h))}${L('Circulating supply', fN(c.circulating_supply) + (c.symbol ? ' ' + esc(c.symbol) : ''))}${L('Rank', c.rank ? '#' + c.rank : '—')}${L('7d change', `<span class="${dir(c.change_7d_pct)}">${pct(c.change_7d_pct)}</span>`)}</div>`;
    }
    function mountChart() {
      const host = $('#chartHost', v); if (!host) return; if (T.chart) T.chart.destroy(); T.chart = new window.TChart(host, { fmt: fAx }); T.chart.emptyText = ''; T.chart.setMode(S.mode); drawChartState()
    }
    function drawChartState(msg) { const host = $('#chartHost', v); if (!host) return; let o = $('.tm-overlay', host); if (!msg) { o && o.remove(); return } if (!o) { o = document.createElement('div'); o.className = 'tm-overlay'; host.appendChild(o) } o.innerHTML = msg }
    async function loadChart(quiet) {
      const seq = ++T.cseq, id = S.coin, tf = S.tf; T.last.ch = Date.now(); if (!T.chart) return;
      if (!quiet) { T.chart.setData([]); drawChartState('<div style="width:80%;display:grid;gap:10px"><div class="tm-skel" style="height:18px"></div><div class="tm-skel" style="height:120px"></div><div class="tm-skel" style="height:18px"></div></div>') }
      try {
        const j = await V.get(`/api/terminal/coins/${encodeURIComponent(id)}/chart?tf=${tf}`); if (seq !== T.cseq || !alive() || !T.chart) return;
        T.cmeta = j.meta; T.granularity = j.chart.granularity; const g = $('#gran', v); if (g) g.textContent = 'Candles: ' + j.chart.granularity;
        if (j.chart.empty) { T.chart.setData([]); drawChartState('<div>No price history is available for this coin and timeframe.<br><small>Nothing is estimated or filled in.</small></div>') } else { drawChartState(); T.chart.setData(j.chart.candles) }
        if (j.meta.stale) { const g2 = $('#gran', v); if (g2) g2.textContent += ' · stale ' + ago(j.meta.age_seconds) }
      } catch (e) { if (seq !== T.cseq || !alive() || !T.chart) return; if (quiet && T.chart.data.length) return; T.chart.setData([]); drawChartState(`<div><b>Chart unavailable</b><br>${esc(e.message)}<br><button class="tm-loadmore" data-cretry>Try again</button></div>`) }
    }
    $('#main', v).addEventListener('click', e => {
      const t = e.target;
      if (t.closest('[data-tf]')) { S.tf = t.closest('[data-tf]').dataset.tf; $$('[data-tf]', v).forEach(b => { b.classList.toggle('on', b.dataset.tf === S.tf); b.setAttribute('aria-pressed', b.dataset.tf === S.tf) }); return loadChart() }
      if (t.closest('[data-mode]')) { S.mode = t.closest('[data-mode]').dataset.mode; $$('[data-mode]', v).forEach(b => { b.classList.toggle('on', b.dataset.mode === S.mode); b.setAttribute('aria-pressed', b.dataset.mode === S.mode) }); return T.chart && T.chart.setMode(S.mode) }
      if (t.closest('[data-reset]')) return T.chart && T.chart.reset();
      if (t.closest('[data-cretry]')) return loadChart();
      if (t.closest('[data-fav]')) return toggleFav(t.closest('[data-fav]').dataset.fav);
    });
    async function loadCoin(quiet) {
      const seq = ++T.dseq, id = S.coin; T.last.c = Date.now(); if (!quiet) { T.detail = null; T.dmeta = null; T.derr = null }
      try { const j = await V.get('/api/terminal/coins/' + encodeURIComponent(id)); if (seq !== T.dseq || !alive()) return; T.detail = j.coin; T.dmeta = j.meta; T.derr = null }
      catch (e) { if (seq !== T.dseq || !alive()) return; T.derr = e; if (!quiet) T.detail = null }
      header(); ordPanel(); lowDraw(false);
    }
    function select(id) {
      if (id === S.coin && T.detail) return; S.coin = id; T.detail = null; T.derr = null; header(); ordPanel(); lowDraw(S.lower !== 'chat'); loadCoin(); loadChart(); drawEx();
      const slug = CHAN[id] || 'altcoins'; if (T.chat && S.lower === 'chat' && S.chan !== slug) { S.chan = slug; T.chat.open(slug) } else if (!S.chanManual) S.chan = slug
    }

    /* ---------------- order panel ---------------- */
    const ordEl = $('#ord', v); const OS = { side: 'buy', type: 'market' };
    async function loadCtx() { try { T.ctx = await V.get('/api/terminal/trade-context') } catch (e) { T.ctx = null } ordPanel() }
    function ordPanel() {
      const c = cur(), id = S.coin, ctx = T.ctx;
      if (!c.trade) { ordEl.innerHTML = `<h2>Trade</h2><div class="tm-skel" style="height:220px"></div>`; return }
      if (!c.trade.enabled) { ordEl.innerHTML = `<h2>Trade</h2><div class="tm-blocked"><b>${esc(c.name || id)} is market data only</b>${esc(c.trade.reason)}<br><br>Vaulta currently offers simulated orders for: <span id="tl" class="mu">loading…</span></div><button class="tm-btn ghost" style="margin-top:12px" disabled>Orders unavailable for this asset</button>`; tradableList(); return }
      const q = c.vaulta_quote ? parseFloat(c.vaulta_quote.price) : null, fee = ctx ? ctx.fee_bps / 10000 : null, avail = ctx ? ctx.available_cents / 100 : null, tfa = ME && ME.two_factor_enabled;
      ordEl.innerHTML = `<h2>Trade ${esc(c.trade.symbol)}</h2><span class="tm-sim"><b>Simulation.</b> Orders fill on Vaulta's internal ledger at Vaulta's own quote (${q ? usd(q) : '—'}), not at the live market price${c.price ? ' (' + fP(c.price) + ')' : ''}. No exchange order is placed.</span>
      <div class="tm-side" role="tablist" aria-label="Order side"><button type="button" role="tab" data-side="buy" class="buy ${OS.side === 'buy' ? 'on' : ''}" aria-selected="${OS.side === 'buy'}">Buy</button><button type="button" role="tab" data-side="sell" class="sell ${OS.side === 'sell' ? 'on' : ''}" aria-selected="${OS.side === 'sell'}">Sell</button></div>
      <form id="of" novalidate><label class="tm-f"><span>Order type</span><select id="ot">${[['market', 'Market'], ['limit', 'Limit'], ['stop', 'Stop'], ['stop_limit', 'Stop-limit']].map(([k, l]) => `<option value="${k}" ${OS.type === k ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
      <label class="tm-f"><span>Quantity (${esc(c.trade.symbol)})</span><input id="oq" inputmode="decimal" autocomplete="off" placeholder="0.00"></label>
      ${OS.side === 'buy' && q && avail != null ? `<div class="tm-quick" aria-label="Quick amounts">${[25, 50, 100].map(p => `<button type="button" data-pct="${p}">${p === 100 ? 'Max' : p + '%'}</button>`).join('')}</div>` : ''}
      <label class="tm-f" id="lw" ${OS.type === 'limit' || OS.type === 'stop_limit' ? '' : 'hidden'}><span>Limit price (USD)</span><input id="ol" inputmode="decimal" autocomplete="off"></label><label class="tm-f" id="sw" ${OS.type === 'stop' || OS.type === 'stop_limit' ? '' : 'hidden'}><span>Stop price (USD)</span><input id="os" inputmode="decimal" autocomplete="off"></label>
      ${tfa ? '<label class="tm-f"><span>2FA code (required for larger orders)</span><input id="oc" inputmode="numeric" maxlength="6" autocomplete="one-time-code"></label>' : ''}
      <div class="tm-row"><span>Available balance</span><b>${avail != null ? usd(avail) : '—'}</b></div><div class="tm-row"><span>Estimated value</span><b id="ev">—</b></div><div class="tm-row"><span>Estimated fee (${ctx ? (ctx.fee_bps / 100).toFixed(2) : '—'}%)</span><b id="ef">—</b></div><div class="tm-row"><span>Estimated total</span><b id="et">—</b></div>
      <p class="tm-err" id="oe" role="alert"></p><button class="tm-btn" id="orev" type="submit">Review ${OS.side} order</button></form><p class="tm-note" style="margin-top:10px">Estimates use Vaulta's quote. The server sets the final price, fee and status, and rejects orders that break balance or risk rules.</p>`;
      const est = () => { const qty = parseFloat($('#oq', ordEl).value) || 0, limit = parseFloat($('#ol', ordEl).value), px = (OS.type === 'limit' || OS.type === 'stop_limit') && limit ? limit : q, val = px ? qty * px : null; $('#ev', ordEl).textContent = val != null && qty ? usd(val) : '—'; $('#ef', ordEl).textContent = val != null && qty && fee != null ? usd(val * fee) : '—'; $('#et', ordEl).textContent = val != null && qty && fee != null ? usd(OS.side === 'buy' ? val * (1 + fee) : val * (1 - fee)) : '—' };
      ordEl.oninput = e => { if (e.target.closest('#of')) est() };
      $('#ot', ordEl).onchange = e => { OS.type = e.target.value; ordPanel() };
      ordEl.onclick = e => {
        const s = e.target.closest('[data-side]'); if (s) { OS.side = s.dataset.side; return ordPanel() }
        const p = e.target.closest('[data-pct]'); if (p && q && fee != null) { const qty = (avail * (+p.dataset.pct / 100)) / (q * (1 + fee)); $('#oq', ordEl).value = (Math.floor(qty * 1e6) / 1e6).toString(); est() }
      };
      $('#of', ordEl).onsubmit = async e => {
        e.preventDefault(); const err = $('#oe', ordEl); err.textContent = ''; const qty = $('#oq', ordEl).value.trim(); if (!/^\d*\.?\d+$/.test(qty) || parseFloat(qty) <= 0) return err.textContent = 'Enter a quantity greater than zero.';
        const body = { symbol: c.trade.symbol, side: OS.side, order_type: OS.type, quantity: qty };
        if (OS.type === 'limit' || OS.type === 'stop_limit') { const l = $('#ol', ordEl).value.trim(); if (!/^\d*\.?\d+$/.test(l) || +l <= 0) return err.textContent = 'Enter a valid limit price.'; body.limit_price = l }
        if (OS.type === 'stop' || OS.type === 'stop_limit') { const s2 = $('#os', ordEl).value.trim(); if (!/^\d*\.?\d+$/.test(s2) || +s2 <= 0) return err.textContent = 'Enter a valid stop price.'; body.stop_price = s2 }
        const code = $('#oc', ordEl) && $('#oc', ordEl).value.trim(); if (code) body.code = code;
        const ok = await confirmBox(`Review ${OS.side} order`, `${OS.side === 'buy' ? 'Buy' : 'Sell'} ${qty} ${c.trade.symbol} · ${label(OS.type)}.\nEstimated total ${$('#et', ordEl).textContent} at Vaulta's quote.\nThis is a simulated order on Vaulta's internal ledger. The server decides the final price, fee and status.`, { ok: OS.side === 'buy' ? 'Place buy order' : 'Place sell order' }); if (!ok) return;
        const b = $('#orev', ordEl); b.disabled = true;
        try { const r = await V.money('/api/orders', body); toast(`Order ${r.order.ref}: ${label(r.order.status)}`); $('#oq', ordEl).value = ''; await Promise.all([loadCtx(), loadOrders()]) }
        catch (er) { err.textContent = er.message; b.disabled = false }
      };
    }
    let tlCache = null;
    async function tradableList() { const el = $('#tl', v); if (!el) return; try { if (!tlCache) tlCache = (await V.get('/api/assets')).assets.filter(a => a.tradable && a.status === 'active' && a.asset_class === 'Crypto').map(a => a.symbol); if (el.isConnected) el.textContent = tlCache.join(', ') || 'no crypto assets right now' } catch (e) { el.textContent = '—' } }

    /* ---------------- lower tabs ---------------- */
    const lowEl = $('#low', v);
    const LT = [['details', 'Market details'], ['book', 'Order book'], ['trades', 'Recent trades'], ['chat', 'Community chat'], ['orders', 'My orders']];
    function lowDraw(full) {
      if (full || !$('.tm-lt', lowEl)) { if (T.chat) { T.chat.destroy(); T.chat = null } lowEl.innerHTML = `<div class="tm-lt" role="tablist" aria-label="Market information">${LT.map(([k, l]) => `<button type="button" role="tab" data-lt="${k}" class="${S.lower === k ? 'on' : ''}" aria-selected="${S.lower === k}">${l}</button>`).join('')}</div><div class="tm-lb ${S.lower === 'chat' ? 'chat' : ''}" id="lb"></div>`; $$('[data-lt]', lowEl).forEach(b => b.onclick = () => { S.lower = b.dataset.lt; lowDraw(true) }); if (S.lower === 'chat' && T.chat) { /* keep */ } }
      const lb = $('#lb', lowEl); const c = cur();
      if (S.lower === 'details') {
        if (!T.detail && !T.derr) lb.innerHTML = Array.from({ length: 5 }, () => '<div class="tm-skel" style="height:28px;margin:8px 0"></div>').join('');
        else if (T.derr && !T.detail) lb.innerHTML = `<div class="tm-empty"><b>Details unavailable</b><br>${esc(T.derr.message)}</div>`;
        else { const d = T.detail, K = (k, val) => `<div class="tm-kv"><span>${k}</span><b>${val}</b></div>`; lb.innerHTML = `<div class="tm-dl">${K('Market cap', fC(d.market_cap))}${K('Fully diluted valuation', fC(d.fully_diluted_valuation))}${K('24h volume', fC(d.volume_24h))}${K('24h high / low', fP(d.high_24h) + ' / ' + fP(d.low_24h))}${K('All-time high', fP(d.ath))}${K('All-time low', fP(d.atl))}${K('Circulating supply', fN(d.circulating_supply))}${K('Total supply', fN(d.total_supply))}${K('Max supply', d.max_supply ? fN(d.max_supply) : '∞ / not capped')}${K('24h change', `<span class="${dir(d.change_24h_pct)}">${pct(d.change_24h_pct)}</span>`)}${K('7d change', `<span class="${dir(d.change_7d_pct)}">${pct(d.change_7d_pct)}</span>`)}${K('30d change', `<span class="${dir(d.change_30d_pct)}">${pct(d.change_30d_pct)}</span>`)}${K('1y change', `<span class="${dir(d.change_1y_pct)}">${pct(d.change_1y_pct)}</span>`)}</div>
          ${d.categories && d.categories.length ? `<p class="tm-note" style="margin-top:12px">${d.categories.map(esc).join(' · ')}</p>` : ''}${d.description ? `<p style="line-height:1.6;color:#cfcfd6;margin-top:10px">${esc(d.description)}${d.description.length >= 700 ? '…' : ''}</p>` : ''}${d.homepage ? `<p><a href="${esc(d.homepage)}" target="_blank" rel="noopener noreferrer" style="color:#8b7bff">Project website ↗</a></p>` : ''}<p class="tm-note">Source: CoinGecko. ${pill(T.dmeta)}</p>` }
      } else if (S.lower === 'book') bookDraw(lb);
      else if (S.lower === 'trades') tradesDraw(lb);
      else if (S.lower === 'orders') ordersDraw(lb);
      else if (S.lower === 'chat') chatDraw(lb);
    }
    async function bookDraw(lb) { lb.innerHTML = '<div class="tm-skel" style="height:80px"></div>'; try { const j = await V.get(`/api/terminal/coins/${encodeURIComponent(S.coin)}/orderbook`); if (S.lower !== 'book') return; lb.innerHTML = j.available ? '<div class="tm-empty">Order book data is available but not rendered yet.</div>' : `<div class="tm-empty"><b>Order book unavailable</b><br>${esc(j.reason)}<br><br>Market data (CoinGecko) does not include exchange depth, so none is shown rather than inventing it.</div>` } catch (e) { lb.innerHTML = `<div class="tm-empty">${esc(e.message)}</div>` } }
    async function tradesDraw(lb) { lb.innerHTML = '<div class="tm-skel" style="height:80px"></div>'; try { const j = await V.get(`/api/terminal/coins/${encodeURIComponent(S.coin)}/fills`); if (S.lower !== 'trades') return; lb.innerHTML = !j.available ? `<div class="tm-empty"><b>No trade feed</b><br>${esc(j.reason)}</div>` : !j.fills.length ? `<div class="tm-empty">No platform fills for ${esc(j.symbol)} yet.<br><small>${esc(j.source)}</small></div>` : `<table class="tm-ot"><thead><tr><th>Time</th><th>Side</th><th>Price</th><th>Quantity</th></tr></thead><tbody>${j.fills.map(f => `<tr><td>${esc(dt(f.ts))}</td><td class="${f.side === 'buy' ? 'up' : 'dn'}">${esc(f.side)}</td><td>${usd(f.price)}</td><td>${esc(f.quantity)}</td></tr>`).join('')}</tbody></table><p class="tm-note" style="margin-top:10px">${esc(j.source)}</p>` } catch (e) { lb.innerHTML = `<div class="tm-empty">${esc(e.message)}</div>` } }
    async function loadOrders() { try { T.orders = (await V.get('/api/orders')).orders } catch (e) { } if (S.lower === 'orders') lowDraw(false) }
    function ordersDraw(lb) { lb.innerHTML = !T.orders.length ? '<div class="tm-empty">No orders yet.</div>' : `<div style="overflow:auto"><table class="tm-ot"><thead><tr><th>Order</th><th>Asset</th><th>Side · type</th><th>Qty</th><th>Price</th><th>Fee</th><th>Status</th><th></th></tr></thead><tbody>${T.orders.map(o => `<tr><td>${esc(o.ref)}<br><small class="mu">${esc(dt(o.created_at))}</small></td><td>${esc(o.symbol)}</td><td>${esc(label(o.side))} · ${esc(label(o.order_type))}</td><td>${esc(o.quantity)}</td><td>${o.execution_price ? usd(o.execution_price) : (o.limit_price ? '≤/≥ ' + usd(o.limit_price) : 'market')}</td><td>${usd(o.fee)}</td><td>${badge(o.status)}</td><td>${['open', 'pending', 'partially_filled'].includes(o.status) ? `<button class="tm-loadmore" style="margin:0" data-cx="${o.id}">Cancel</button>` : (o.reason ? `<small class="mu">${esc(o.reason)}</small>` : '')}</td></tr>`).join('')}</tbody></table></div><p class="tm-note" style="margin-top:8px">Simulated orders on Vaulta's internal ledger. Status is always the one the server reports.</p>`; lb.onclick = async e => { const x = e.target.closest('[data-cx]'); if (x && await confirmBox('Cancel order', 'Cancel this order and release any reserved cash?', { ok: 'Cancel order', danger: true })) { await run(x, () => V.post(`/api/orders/${x.dataset.cx}/cancel`), 'Order cancelled'); loadOrders(); loadCtx() } } }
    function chatDraw(lb) {
      lb.classList.add('chat'); if (T.chat) return; const slug = S.chan || CHAN[S.coin] || 'general'; S.chan = slug;
      T.chat = new window.TChat(lb, { V, UI, isMod, onChannel: s => { S.chan = s; S.chanManual = true } }); T.chat.init(slug)
    }

    /* ---------------- mobile tabs & drawer ---------------- */
    function setM(m) {
      S.m = m; root.dataset.m = m; $$('.tm-mtabs [data-m]', v).forEach(b => { b.classList.toggle('on', b.dataset.m === m); b.setAttribute('aria-selected', b.dataset.m === m) });
      if (m === 'book' && S.lower !== 'book') { S.lower = 'book'; lowDraw(true) } if (m === 'community' && S.lower !== 'chat') { S.lower = 'chat'; if (T.chat) { T.chat.destroy(); T.chat = null } lowDraw(true) }
      if (m === 'chart' && T.chart) setTimeout(() => T.chart.resize(), 30); if (m === 'markets' && !T.rows.length && !T.loading) loadMarkets(true)
    }
    $('.tm-mtabs', v).addEventListener('click', e => { const b = e.target.closest('[data-m]'); if (b) setM(b.dataset.m) });
    const dr = document.createElement('button'); dr.type = 'button'; dr.className = 'tm-ib'; dr.id = 'exdrawer'; dr.textContent = '☰'; dr.title = 'Markets'; dr.setAttribute('aria-label', 'Open markets'); dr.style.cssText = 'display:none;margin-right:8px';
    dr.onclick = () => { exEl.classList.toggle('drawer'); exEl.style.display = exEl.classList.contains('drawer') ? 'flex' : ''; if (!T.rows.length) loadMarkets(true) };
    const mq = matchMedia('(max-width:1100px) and (min-width:761px)'); const mqf = () => { dr.style.display = mq.matches ? 'inline-grid' : 'none'; if (!mq.matches) { exEl.classList.remove('drawer'); exEl.style.display = '' } }; mq.addEventListener('change', mqf);

    /* ---------------- go ---------------- */
    header(); $('#main', v).prepend(dr); mqf(); ordPanel(); lowDraw(true); drawEx();
    if (S.m === 'book' || S.m === 'community') setM(S.m);
    loadMarkets(true); loadCoin(); loadChart(); loadCtx(); loadOrders();
  });
});
