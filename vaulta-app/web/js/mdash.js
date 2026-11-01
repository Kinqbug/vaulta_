/* VAULTA mobile home (<=860px). Pure presentation: every figure is passed in from GET /api/dashboard and
   GET /api/portfolio/history (valued server-side). Nothing is hard-coded or invented here, and no balance is
   ever written to storage. Only the "hide balances" preference is kept (sessionStorage, per tab). */
(()=>{
const {esc,usd}=UI;
const KEY='vaulta:hide-balances';
let hidden=false;try{hidden=sessionStorage.getItem(KEY)==='1'}catch(e){}

const P={
 trend:'<polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/>',
 card:'<rect x="3" y="4.5" width="18" height="15" rx="3"/><path d="M3 10h18M7 15h4"/>',
 withdraw:'<path d="M12 15V4"/><path d="m7.5 8.5 4.5-4.5 4.5 4.5"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
 piggy:'<path d="M19 5c-1.5 0-2.8 1.4-3 2-3.5-1.5-11-.3-11 5 0 1.8 0 3 2 4.5V20h4v-2h3v2h4v-4c1-.5 1.7-1 2-2h2v-5h-2c0-1-.5-1.5-1-2V5z"/><path d="M2 9v1c0 1.1.9 2 2 2h1"/><path d="M16 11h.01"/>',
 target:'<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.2"/>',
 goal:'<circle cx="11" cy="13" r="8"/><circle cx="11" cy="13" r="4"/><path d="m11 13 9-9"/><path d="M16.5 3.5v4h4"/>',
 candle:'<path d="M7 3v4M7 15v5M12 5v5M12 17v4M17 2v3M17 13v4"/><rect x="5" y="7" width="4" height="8" rx="1"/><rect x="10" y="10" width="4" height="7" rx="1"/><rect x="15" y="5" width="4" height="8" rx="1"/>',
 pie:'<path d="M21.2 15.9A10 10 0 1 1 8 2.8"/><path d="M22 12A10 10 0 0 0 12 2v10z"/>',
 palm:'<path d="M13 22V11"/><path d="M13 11C13 7 9.5 4.5 5 5.5c2 .5 3.5 1.7 4.5 3.5"/><path d="M13 11c0-4 3.5-6.5 8-5.5-2 .5-3.5 1.7-4.5 3.5"/><path d="M13 11c-3-1-6-.5-8 2 2.5-.5 5 0 8 1"/><path d="M13 11c3-1 6-.5 8 2-2.5-.5-5 0-8 1"/><path d="M6 22h14"/>',
 doc:'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8M10 9H8"/>',
 clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
 shield:'<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>',
 headset:'<path d="M3 14v-2a9 9 0 0 1 18 0v2"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/><path d="M19 21c0 1-1 1.5-3 1.5h-2"/>',
 home:'<path d="m3 10 9-7 9 7v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>',
 lock:'<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
 bulb:'<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2V17h5v-1.1c0-.8.4-1.5 1-2A6 6 0 0 0 12 3z"/>',
 wallet:'<path d="M3 8a2 2 0 0 1 2-2h12v3"/><rect x="3" y="8" width="18" height="12" rx="2.5"/><path d="M16 14h2"/>',
 eye:'<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
 eyeoff:'<path d="M3 3l18 18"/><path d="M10.6 5.1A10 10 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3 3.9M6.6 6.6A17 17 0 0 0 2 12s3.6 7 10 7a9.7 9.7 0 0 0 4.4-1"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
 bell:'<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
 chev:'<path d="m9 6 6 6-6 6"/>'};
const ic=(k,n=24,sw=1.8)=>`<svg class="mi" width="${n}" height="${n}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${P[k]}</svg>`;

/* The app has no internal-transfer workflow (funding is crypto-only; the only transfer API moves cash<->savings).
   So "Transfer" opens an honest explanation rather than a fake flow. */
const MAIN=[['Invest','trend','#/invest'],['Deposit','card','#/deposit'],['Withdraw','withdraw','#/withdraw'],['Savings','piggy','#/savings'],
 ['Goals','goal','#/goals'],['Trade','candle','#/trade'],['Portfolio','pie','#/portfolio'],['Retirement','palm','#/retirement']];
const ACCT=[['Statements','doc','#/statements'],['Activity','clock','#/transactions'],['Security','shield','#/profile/security'],['Support','headset','#/support']];
const INSIGHTS=[
 ['Build your emergency fund','Aim for 3–6 months of living expenses.','shield','#/goals'],
 ['Diversify your portfolio','Spread risk, grow long term.','pie','/resources/guides/understanding-diversification/'],
 ['Protect your account','Enable 2FA and keep your credentials safe.','lock','#/profile/two-factor']];

/* a value that the visibility toggle can mask */
const sens=(txt,mask='••••••')=>`<span data-sens data-v="${esc(txt)}" data-m="${esc(mask)}">${esc(hidden?mask:txt)}</span>`;
const item=([l,i,h,t])=>`<a class="qa" href="${h}"${t?` data-act="${t}"`:''}><span class="qi">${ic(i,26)}</span><span class="ql">${esc(l)}</span></a>`;
const badgeTxt=n=>n>99?'99+':String(n);

function goalPick(goals){return goals.find(g=>g.status==='active')||goals[0]||null}
function goalIcon(g){return /house|home|property|apartment|rent/i.test(g.name)?'home':'goal'}
function pctOf(g){const t=Number(g.target),c=Number(g.current);const p=t>0?c/t*100:Number(g.progress_pct);return Math.max(0,Math.min(100,Number.isFinite(p)?p:0))}
const pctTxt=p=>(p>=10||p===0||Number.isInteger(p)?String(Math.round(p)):p.toFixed(1))+'%';

function header(unread){
 return `<header class="mh"><a class="mh-logo" href="/index.html" aria-label="VAULTA home">${BRAND.img('black','logo-full')}</a>
  <a class="mh-bell" href="#/notifications" aria-label="Notifications${unread?`, ${unread} unread`:''}">${ic('bell',26,1.7)}<b class="mh-n m-nb" ${unread?'':'hidden'}>${unread?badgeTxt(unread):''}</b></a></header>`}

function goalsCard(goals){
 const g=goalPick(goals);
 const head=`<a class="mc-h" href="#/goals"><span class="mc-t">${ic('goal',24,1.8)}<h2>My Goals</h2></span>${ic('chev',20,2)}<span class="sr">Open savings goals</span></a>`;
 if(!g)return `<section class="mc mgoal" aria-label="My goals">${head}<div class="mempty"><p>You haven’t set a savings goal yet.</p><a class="mbtn" href="#/goals">Create a goal</a></div></section>`;
 const p=pctOf(g);
 return `<section class="mc mgoal" aria-label="My goals">${head}
  <a class="mg-row" href="#/goals" aria-label="${esc(g.name)}: ${esc(pctTxt(p))} complete"><span class="mg-i">${ic(goalIcon(g),26,1.8)}</span>
   <span class="mg-tx"><b>${esc(g.name)}</b><small>${sens(usd(g.current).replace(/\.00$/,''))} of ${sens(usd(g.target).replace(/\.00$/,''))}</small></span>
   <span class="mg-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(p)}" aria-label="Goal progress"><i style="width:${p.toFixed(1)}%"></i></span>
   <span class="mg-p">${esc(pctTxt(p))}</span></a></section>`}

function acctCard(me){
 const id=String((me&&me.id)||''),l4=id.replace(/[^A-Za-z0-9]/g,'').slice(-4);
 return `<section class="mc mdark macct" aria-label="Investment account"><a class="mc-h" href="#/portfolio"><span class="mc-t">${ic('card',24,1.8)}<h2>Investment Account</h2></span>${ic('chev',20,2)}<span class="sr">Open portfolio</span></a>
  <div class="vcard" role="img" aria-label="${l4?`Vaulta Investor account, ID ending ${esc(l4)}`:'Vaulta Investor account'}"><i class="vc-pat" aria-hidden="true"></i>
   <div class="vc-l">${BRAND.img('white','logo-full')}<small>VAULTA INVESTOR</small></div><div class="vc-n" aria-hidden="true"><span>••••</span><b>${esc(l4||'••••')}</b></div></div></section>`}

function insights(){
 return `<section class="mc minsight" aria-label="Vaulta Insights"><a class="mc-h" href="/resources/"><span class="mc-t">${ic('bulb',24,1.8)}<h2>Vaulta Insights</h2></span>${ic('chev',20,2)}<span class="sr">Open learning resources</span></a>
  <ul>${INSIGHTS.map(([t,d,i,h])=>`<li><a href="${h}"><span class="mn-i">${ic(i,22,1.8)}</span><span class="mn-t"><b>${esc(t)}</b><small>${esc(d)}</small></span></a></li>`).join('')}</ul></section>`}

function portfolioCard(s){
 const pv=usd(s.portfolio_value),long=pv.length>11;
 return `<section class="mc mdark mport" aria-label="Total portfolio value">
  <a class="mp-top" href="#/portfolio"><span class="mp-lab">Total portfolio value</span>${ic('chev',20,2)}<span class="sr">Open portfolio details</span></a>
  <p class="mp-val${long?' long':''}">${sens(pv)}</p>
  <p class="mp-perf" id="mperf" aria-live="polite"><span class="mp-ph">&nbsp;</span></p>
  <div class="mp-chart" id="mspark" aria-hidden="true"></div>
  <div class="mp-cash"><span class="mp-w">${ic('wallet',26,1.6)}</span><div class="mp-ct"><small>Account Balance</small><b>${sens(usd(s.available_cash))}</b></div>
   <button type="button" class="mp-eye" id="meye" aria-pressed="${hidden}" aria-label="${hidden?'Show balances':'Hide balances'}"><span class="eo">${ic(hidden?'eyeoff':'eye',24,1.7)}</span></button></div></section>`}

function actions(){
 return `<section class="mqa mc" aria-label="Quick actions"><h2 class="mq-h">Quick actions</h2><div class="qgrid d">${MAIN.slice(0,4).map(item).join('')}</div><div class="qgrid d">${MAIN.slice(4).map(item).join('')}</div>
  <h2 class="mq-h sub">Account &amp; personal</h2><div class="qgrid">${ACCT.map(item).join('')}</div></section>`}

window.MDash={
 header:(d)=>header(d.unread),
 body:(d,me)=>`<div class="mbody">${portfolioCard(d.summary)}${actions()}${goalsCard(d.goals||[])}${acctCard(me)}${insights()}</div>`,
 /* called once the 1M history arrives (or fails). Monthly % is derived from the server's own valuation series. */
 spark(points){
  const perf=document.getElementById('mperf'),box=document.getElementById('mspark');if(!perf||!box)return;
  const v=(points||[]).map(p=>Number(p.value)).filter(Number.isFinite);
  if(v.length<2||!(v[0]>0)){perf.innerHTML='<span class="mp-none">Monthly change unavailable</span>';box.innerHTML='';return}
  const ch=(v[v.length-1]-v[0])/v[0]*100,up=ch>=0,txt=(up?'+':'−')+Math.abs(ch).toFixed(1)+'%';
  perf.className='mp-perf '+(up?'up':'dn');
  perf.innerHTML=`<svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="${up?'M6 1.5 11 10.5H1z':'M6 10.5 1 1.5h10z'}" fill="currentColor"/></svg><b>${sens(txt,'•••')}</b><span>This month</span>`;
  const n=Math.min(v.length,48),s=[];for(let i=0;i<n;i++)s.push(v[Math.round(i*(v.length-1)/(n-1))]);
  const mn=Math.min(...s),mx=Math.max(...s),W=160,H=64,sp=(mx-mn)||1,X=i=>i/(n-1)*W,Y=x=>H-4-(x-mn)/sp*(H-8);
  const d=s.map((x,i)=>(i?'L':'M')+X(i).toFixed(1)+' '+Y(x).toFixed(1)).join('');
  box.innerHTML=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" focusable="false"><defs><linearGradient id="msg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".22"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient></defs><path d="${d}L${W} ${H}L0 ${H}Z" fill="url(#msg)"/><path d="${d}" fill="none" stroke="#fff" stroke-opacity=".9" stroke-width="1.4" vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"/></svg>`},
 bind(root){
  const eye=root.querySelector('#meye');
  const apply=()=>{root.querySelectorAll('[data-sens]').forEach(e=>{e.textContent=hidden?e.dataset.m:e.dataset.v});
   if(eye){eye.setAttribute('aria-pressed',String(hidden));eye.setAttribute('aria-label',hidden?'Show balances':'Hide balances');eye.querySelector('.eo').innerHTML=ic(hidden?'eyeoff':'eye',24,1.7)}};
  if(eye)eye.onclick=()=>{hidden=!hidden;try{sessionStorage.setItem(KEY,hidden?'1':'0')}catch(e){}apply()};
  window.MDash.apply=apply}
};
})();
