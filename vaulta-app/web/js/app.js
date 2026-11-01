/* VAULTA user dashboard (hash-routed SPA). All numbers come from the server; this file only renders and submits. */
(()=>{
const {$,$$,esc,usd,sgn,pc,dt,day,cls,label,badge,line,donut,toast,modal,confirmBox,run,table,skeleton,errBox,pager,formData}=UI;
const V=VApi;let ME=null,UNREAD=0;
const IC={
dashboard:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/>',
portfolio:'<path d="M21.2 15.9A10 10 0 1 1 8 2.8"/><path d="M22 12A10 10 0 0 0 12 2v10z"/>',
invest:'<polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/>',
deposit:'<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/>',
withdraw:'<path d="M12 15V3"/><path d="m7 8 5-5 5 5"/><path d="M5 21h14"/>',
trade:'<path d="M8 3 4 7l4 4"/><path d="M4 7h16"/><path d="m16 21 4-4-4-4"/><path d="M20 17H4"/>',
savings:'<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/>',
retirement:'<path d="M22 12a10 10 0 0 0-20 0Z"/><path d="M12 12v8a2 2 0 0 0 4 0"/><path d="M12 2v1"/>',
goals:'<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
watchlist:'<polygon points="12 2 15.1 8.3 22 9.3 17 14.1 18.2 21 12 17.8 5.8 21 7 14.1 2 9.3 8.9 8.3 12 2"/>',
transactions:'<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
statements:'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8M10 9H8"/>',
notifications:'<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
support:'<path d="M3 14v-2a9 9 0 0 1 18 0v2"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/>',
security:'<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
settings:'<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
home:'<path d="m3 10 9-7 9 7v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>',
search:'<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
logout:'<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>',
profile:'<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
user:'<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
chev:'<path d="m6 9 6 6 6-6"/>',
more:'<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
filter:'<path d="M22 3H2l8 9.46V19l4 2v-8.54z"/>',
download:'<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
arrow:'<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
up:'<path d="M12 19V5"/><path d="m5 12 7-7 7 7"/>',
down:'<path d="M12 5v14"/><path d="m19 12-7 7-7-7"/>',
sq:'<rect x="4" y="4" width="16" height="16" rx="3"/><path d="m9 9 6 6M15 9l-6 6"/>',
kebab:'<circle cx="12" cy="5" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="12" cy="19" r="1.2"/>',
admin:'<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>'};
const ico=(k,n=20)=>`<svg class="i" width="${n}" height="${n}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${IC[k]||''}</svg>`;
const NAV=[['dashboard','Dashboard'],['portfolio','Portfolio'],['deposit','Deposit'],['withdraw','Withdraw'],['invest','Invest'],['trade','Trade'],['savings','Savings'],['retirement','Retirement'],['goals','Goals'],['watchlist','Watchlist'],['transactions','Transactions'],['statements','Statements'],['notifications','Notifications'],['support','Support'],['profile','Profile']];
const MOBILE=[['dashboard','Home','home'],['invest','Invest','portfolio'],['trade','Trade','trade'],['goals','Goals','goals'],['profile','Profile','profile']];
let PRODS=null,SQ='';
const SELF='/app/';

const isStaff=()=>!!(ME.permissions&&ME.permissions.length);
const initials=()=>((ME.first_name||ME.email||'?')[0]+(ME.last_name||'')[0]).toUpperCase().slice(0,2);
const avatarInner=()=>ME.avatar_url?`<img src="${esc(ME.avatar_url)}" alt="" decoding="async">`:esc(initials());
function refreshAvatars(){$$('[data-avatar]').forEach(e=>{e.innerHTML=avatarInner()})}
document.addEventListener('error',e=>{const t=e.target,a=t&&t.tagName==='IMG'&&t.closest&&t.closest('[data-avatar]');if(a&&ME)a.textContent=initials()},true);      // a missing/broken photo falls back to initials, never a broken image
window.addEventListener('vaulta:profile',async()=>{try{ME=(await V.session())||ME}catch(e){}refreshAvatars();const f=$('#acctm .mh');if(f)f.innerHTML=`<b>${esc(fullName())}</b><small>${esc(ME.email)}</small>`;if(window.VaultaTheme)VaultaTheme.apply(ME.theme)});
const fullName=()=>[ME.first_name,ME.last_name].filter(Boolean).join(' ')||ME.email;
const greet=()=>{const h=new Date().getHours();return(h<12?'Good morning':h<18?'Good afternoon':'Good evening')+', '+(ME.first_name||'there')};
function shell(){
 const links=NAV.map(([k,l])=>`<a class="nv" data-k="${k}" href="#/${k}"><span class="ic">${ico(k)}</span><span class="t">${l}</span>${k==='notifications'?'<span class="badge-n" id="nbadge" hidden></span>':''}${k==='support'?'<span class="badge-n" id="sbadge" hidden></span>':''}</a>`).join('');
 const staff=isStaff()?`<a class="nv" href="/admin/"><span class="ic">${ico('admin')}</span><span class="t">Admin console</span></a>`:'';
 $('#root').innerHTML=`<div class="shell"><aside class="side" aria-label="Sidebar"><a class="logo" href="/index.html" aria-label="VAULTA home">${BRAND.img('white','logo-full')}${BRAND.img('mark','logo-mark')}</a><nav class="dnav" aria-label="Main">${links}${staff}</nav><div class="grow"></div><div class="who"><span class="dav" data-avatar aria-hidden="true">${avatarInner()}</span><span class="wt"><b>${esc(fullName())}</b><small>${esc(ME.email)}</small></span></div><button class="dout" id="logout" type="button">${ico('logout',18)}<span class="t">Log out</span></button></aside>
 <main class="main"><div class="dhero" id="hero"></div><div class="vbar" id="vbar" hidden></div><div class="content" id="view" tabindex="-1"></div></main></div>
 <nav class="mnav" aria-label="Mobile">${MOBILE.map(([k,l,i])=>`<a data-k="${k}" href="#/${k}">${ico(i,22)}<span>${l}</span></a>`).join('')}</nav>`;
 $('#logout').onclick=async()=>{await V.logout();location.href='/login/'};
 document.addEventListener('click',e=>{const m=$('#acctm');if(m&&!m.hidden&&!e.target.closest('.acct')){m.hidden=true;$('#acctb').setAttribute('aria-expanded','false')}const r=$('#sres');if(r&&!r.hidden&&!e.target.closest('.srch'))r.hidden=true});
 document.addEventListener('keydown',e=>{if(e.key!=='Escape')return;const m=$('#acctm');if(m&&!m.hidden){m.hidden=true;$('#acctb').setAttribute('aria-expanded','false');$('#acctb').focus()}const r=$('#sres');if(r)r.hidden=true})}
const headerHTML=()=>`<header class="hd"><a class="m-logo" href="/index.html" aria-label="VAULTA home">${BRAND.img('black','logo-full')}</a>
 <div class="hd-l"><span class="dav d-only" data-avatar aria-hidden="true">${avatarInner()}</span><div class="hd-t"><b class="greet">${esc(greet())}</b><small class="sub">Here's what's happening with your portfolio today.</small></div></div>
 <form class="srch" id="srchf" role="search"><span class="si">${ico('search',18)}</span><input id="q" type="search" placeholder="Search investments, assets, or anything..." aria-label="Search investments and assets" autocomplete="off" enterkeyhint="search"><ul class="dsr" id="sres" hidden></ul></form>
 <div class="hd-r"><button class="ibtn m-only" id="sbtn" type="button" aria-label="Search" aria-expanded="false">${ico('search',22)}</button>
 <a class="ibtn bell" href="#/notifications" aria-label="Notifications">${ico('notifications',22)}<i class="dot" id="ndot" hidden></i></a>
 <div class="acct"><button class="acct-b" id="acctb" type="button" aria-haspopup="menu" aria-expanded="false" aria-label="My account"><span class="dav dav-m" data-avatar aria-hidden="true">${avatarInner()}</span><span class="d-only">${ico('user',18)}</span><span class="lbl d-only">My account</span><span class="d-only">${ico('chev',16)}</span></button>
 <div class="menu" id="acctm" role="menu" hidden><div class="mh"><b>${esc(fullName())}</b><small>${esc(ME.email)}</small></div><a role="menuitem" href="#/profile">${ico('user',18)}Profile &amp; settings</a><a role="menuitem" href="#/profile/security">${ico('security',18)}Security</a><a role="menuitem" href="#/notifications">${ico('notifications',18)}Notifications</a><a role="menuitem" href="#/support">${ico('support',18)}Support</a>${isStaff()?`<a role="menuitem" href="/admin/">${ico('admin',18)}Admin console</a>`:''}<button role="menuitem" type="button" id="lo3">${ico('logout',18)}Log out</button></div></div></div></header>`;
async function prods(){if(PRODS)return PRODS;try{PRODS=(await V.get('/api/products')).products||[]}catch(e){PRODS=[]}return PRODS}
function bindHeader(){
 const f=$('#srchf'),q=$('#q'),res=$('#sres'),sb=$('#sbtn'),ab=$('#acctb'),am=$('#acctm');
 sb.onclick=()=>{const o=$('.hd').classList.toggle('sopen');sb.setAttribute('aria-expanded',o);if(o)q.focus()};
 q.oninput=async()=>{const t=q.value.trim().toLowerCase();if(!t){res.hidden=true;return}const list=(await prods()).filter(p=>[p.name,p.category,p.issuer,p.kind].join(' ').toLowerCase().includes(t)).slice(0,6);res.innerHTML=list.length?list.map(p=>`<li><a href="#/invest" data-s="${esc(p.name)}"><b>${esc(p.name)}</b><small>${esc(p.category||'')}</small></a></li>`).join(''):'<li class="none">No matches</li>';res.hidden=false};
 f.onsubmit=e=>{e.preventDefault();SQ=q.value.trim();res.hidden=true;if((location.hash||'').startsWith('#/invest'))route();else location.hash='#/invest'};
 res.onclick=e=>{const a=e.target.closest('[data-s]');if(!a)return;SQ=a.dataset.s;res.hidden=true;if((location.hash||'').startsWith('#/invest')){e.preventDefault();route()}};
 ab.onclick=()=>{am.hidden=!am.hidden;ab.setAttribute('aria-expanded',String(!am.hidden))};
 am.onclick=e=>{if(e.target.closest('a'))am.hidden=true};
 $('#lo3').onclick=async()=>{await V.logout();location.href='/login/'}}
function renderHero(k){
 document.body.classList.toggle('m-home',k==='dashboard');const h=$('#hero');h.className='dhero'+(k==='dashboard'?' is-dash':' compact')+(k==='portfolio'?' light':'');
 h.innerHTML=headerHTML()+(k==='dashboard'?'<h1 class="ptitle">Dashboard</h1><div class="fin" id="fin"><div class="sk dk"></div><div class="sk dk"></div></div>':'');bindHeader();badge_()}
function setActive(k){if(k==='settings'||k==='security')k='profile';$$('[data-k]').forEach(a=>a.classList.toggle('on',a.dataset.k===k))}
const head=(t,actions='')=>`<div class="top"><h1>${esc(t)}</h1><div class="row" style="display:flex;gap:8px;flex-wrap:wrap">${actions}</div></div>`;
const kpi=(l,v,sub)=>`<div class="box kpi"><small>${esc(l)}</small><b>${v}</b>${sub?`<em>${sub}</em>`:''}</div>`;
const view=()=>$('#view');
async function load(fn){const v=view();v.innerHTML=skeleton(4);try{await fn(v)}catch(e){if(e.status===401){location.href='/login/';return}const fin=$('#fin');if(fin)fin.innerHTML='';v.innerHTML=errBox(e.message,true);const b=$('[data-retry]',v);if(b)b.onclick=()=>route()}}

/* ---------- dashboard ---------- */
const R={};
const AC=['Stocks','ETFs','Bonds','Crypto','Real Estate','Commodities','Savings'],ACOL=['#0a0a0a','#262626','#404040','#5e5e5e','#8c8c8c','#b4b4b4','#d9d9d9'];
const WAVES='<svg class="waves" viewBox="0 0 600 220" preserveAspectRatio="none" aria-hidden="true" focusable="false">'+[0,1,2,3,4,5,6].map(i=>`<path d="M-20 ${70+i*14} C 90 ${20+i*14}, 170 ${150+i*10}, 280 ${90+i*12} S 470 ${30+i*16}, 560 ${110+i*10} S 640 ${130+i*8}, 640 ${100+i*10}" fill="none" stroke="#fff" stroke-width="1" vector-effect="non-scaling-stroke"/>`).join('')+'</svg>';
function allocHTML(rows,total){
 const m=Object.fromEntries(rows.map(r=>[r.label,r])),t=rows.reduce((a,r)=>a+Number(r.value),0);
 if(!t)return'<div class="empty">Nothing to show yet. Make a deposit or invest to see your allocation.</div>';
 let o=0;const pct=c=>m[c]?Number(m[c].pct??(Number(m[c].value)/t*100)):0;
 const arcs=AC.map((c,i)=>{const p=Number(m[c]?.value||0)/t*100;if(!p)return'';const d=Math.max(p-.6,.1),a=`<circle cx="21" cy="21" r="15.915" fill="none" stroke="${ACOL[i]}" stroke-width="5.5" pathLength="100" stroke-dasharray="${d} ${100-d}" stroke-dashoffset="${-o}"/>`;o+=p;return a}).join('');
 const desc='Asset allocation: '+AC.map(c=>`${c} ${pct(c).toFixed(1)}%`).join(', ');
 return`<div class="alloc-in"><div class="donut2"><svg viewBox="0 0 42 42" role="img" aria-label="${esc(desc)}" style="transform:rotate(-90deg)">${arcs}</svg><div class="dc"><small>Total</small><b>${usd(total)}</b></div></div><ul class="alk">${AC.map((c,i)=>`<li><i style="background:${ACOL[i]}"></i><span>${c}</span><b>${pct(c).toFixed(1)}%</b></li>`).join('')}</ul></div>`}
function perfChart(pts,r){
 const v=pts.map(p=>Number(p.value));if(v.length<2)return'<div class="empty">Not enough data yet.</div>';
 const mn=Math.min(...v),mx=Math.max(...v);let pad=(mx-mn)*.08;if(!pad)pad=Math.max(Math.abs(mx)*.02,1);const lo=mn-pad,hi=mx+pad,W=600,H=200,sp=hi-lo;
 const X=i=>i/(v.length-1)*W,Y=x=>H-(x-lo)/sp*H,d=v.map((x,i)=>(i?'L':'M')+X(i).toFixed(1)+' '+Y(x).toFixed(1)).join('');
 const lab=y=>sp<10?'$'+y.toFixed(2):sp<2000?'$'+Math.round(y).toLocaleString('en-US'):'$'+(Math.abs(y)>=1e6?(y/1e6).toFixed(1)+'M':(y/1e3).toFixed(1)+'K');
 const ys=[0,1,2,3,4].map(i=>lab(hi-sp*i/4));
 const fmt=t=>{const D=new Date(t);return r==='1D'?D.toLocaleTimeString('en-US',{hour:'numeric'}):['1W','1M','3M','6M'].includes(r)?D.toLocaleDateString('en-US',{month:'short',day:'numeric'}):D.toLocaleDateString('en-US',{month:'short',year:'2-digit'})};
 const xs=[0,1,2,3,4,5].map(i=>fmt(pts[Math.round(i*(pts.length-1)/5)].t));
 const grid=[0,1,2,3,4].map(i=>`<line x1="0" x2="${W}" y1="${i*H/4}" y2="${i*H/4}"/>`).join('');
 const ar=`Portfolio value over ${r}: from ${usd(v[0])} to ${usd(v[v.length-1])}, low ${usd(mn)}, high ${usd(mx)}.`;
 return`<div class="pc" role="img" aria-label="${esc(ar)}"><div class="pc-y" aria-hidden="true">${ys.map(y=>`<span>${y}</span>`).join('')}</div><div class="pc-plot"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true" focusable="false"><defs><linearGradient id="pg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#000" stop-opacity=".07"/><stop offset="1" stop-color="#000" stop-opacity=".01"/></linearGradient></defs><g class="gl">${grid}</g><path d="${d}L${W} ${H}L0 ${H}Z" fill="url(#pg)"/><path d="${d}" fill="none" stroke="#0a0a0a" stroke-width="1.5" vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"/></svg></div><div class="pc-x" aria-hidden="true">${xs.map(x=>`<span>${x}</span>`).join('')}</div></div>`}
R.dashboard=()=>load(async v=>{
 const d=await V.get('/api/dashboard');UNREAD=d.unread;badge_();const s=d.summary;
 const banners=[];if(!ME.email_verified)banners.push(`<div class="banner"><span>Please verify your email to deposit, invest and trade.</span><button class="btn sm" id="resend">Resend link</button></div>`);
 if(d.kyc_status!=='verified')banners.push(`<div class="banner"><span>Identity verification: <b>${esc(label(d.kyc_status))}</b>. Required before withdrawals.</span><a class="btn sm" href="#/settings">Open verification</a></div>`);
 const dc=Number(s.day_change),prev=Number(s.portfolio_value)-dc,dp=prev?dc/prev*100:0,up=dc>=0;
 $('#fin').innerHTML=`<section class="fcard pcard" aria-label="Total portfolio value">${WAVES}<div class="ch"><h2>Total portfolio value</h2><a class="kebab" href="#/portfolio" aria-label="Open portfolio details">${ico('kebab',20)}</a></div><p class="fbig">${usd(s.portfolio_value)}</p><p class="chg ${up?'up':'dn'}"><span aria-hidden="true">${up?'↑':'↓'}</span> ${sgn(dc)} today <span class="dpill">${pc(dp)}</span></p><span class="visa" aria-hidden="true">VISA</span></section>
 <section class="fcard ccard" aria-label="Account Balance">${WAVES}<div class="ch"><div class="ct"><span class="cic" aria-hidden="true">${ico('savings',20)}</span><h2>Account Balance</h2></div><a class="kebab" href="#/deposit" aria-label="Open deposit">${ico('kebab',20)}</a></div><p class="fbig sm">${usd(s.available_cash)}</p><p class="hold">${usd(s.cash_held)} on hold</p><div class="cta2"><a class="b-w" href="#/deposit">Deposit</a><a class="b-o" href="#/withdraw">Withdraw</a></div></section>`;
 v.innerHTML=`<div class="mdash">${MDash.header(d)}</div>`+banners.join('')+`<div class="ddash">
 <div class="ana"><section class="box perf" aria-labelledby="ph"><div class="row-h"><h3 id="ph">Portfolio performance</h3><div class="seg" id="ranges" role="group" aria-label="Time range">${['1D','1W','1M','3M','6M','1Y','5Y','ALL'].map(r=>`<button type="button" data-r="${r}" aria-pressed="${r==='1M'}" class="${r==='1M'?'on':''}">${r}</button>`).join('')}</div></div><div id="pchart"></div><p class="foot" id="pfoot"></p></section>
 <section class="box dalloc" aria-labelledby="ah2"><h3 id="ah2">Asset allocation</h3>${allocHTML(d.allocation,s.portfolio_value)}</section></div>
 <div class="grid g3" style="margin-top:16px"><section class="box"><div class="row-h"><h3>Goals</h3><a href="#/goals">All</a></div>${d.goals.length?d.goals.map(goalRow).join(''):'<div class="empty">No goals yet. <a href="#/goals">Create one</a>.</div>'}</section>
 <section class="box"><div class="row-h"><h3>Recent activity</h3><a href="#/transactions">All</a></div>${d.recent.length?d.recent.map(txRow).join(''):'<div class="empty">No activity yet. Make a deposit to get started.</div>'}</section>
 <section class="box"><div class="row-h"><h3>Watchlist</h3><a href="#/watchlist">Manage</a></div>${d.watchlist.length?d.watchlist.map(wRow).join(''):'<div class="empty">Your watchlist is empty.</div>'}</section></div></div><div class="mdash">${MDash.body(d,ME)}</div>`;
 MDash.bind(v);
 const rs=$('#resend');if(rs)rs.onclick=()=>run(rs,()=>V.post('/api/auth/resend-verification'),'Verification email sent.');
 let tok=0;const draw=async r=>{const my=++tok;$('#pchart').innerHTML=skeleton(1);try{const h=await V.get('/api/portfolio/history?range='+r);if(my!==tok)return;if(r==='1M')MDash.spark(h.points);$('#pchart').innerHTML=perfChart(h.points,r);$('#pfoot').textContent=`${r} · cash + savings + holdings, valued by the server`}catch(e){if(my===tok){if(r==='1M')MDash.spark(null);$('#pchart').innerHTML=errBox(e.message)}}};
 $('#ranges').onclick=e=>{const b=e.target.closest('[data-r]');if(!b)return;$$('#ranges button').forEach(x=>{x.classList.toggle('on',x===b);x.setAttribute('aria-pressed',String(x===b))});draw(b.dataset.r)};draw('1M')});
const goalRow=g=>`<div class="goal"><div class="h"><span>${esc(g.name)}</span><span>${usd(g.current)} / ${usd(g.target)}</span></div><div class="bar-p"><i style="width:${esc(g.progress_pct)}%"></i></div><small>${esc(g.progress_pct)}%${g.target_date?' · target '+esc(g.target_date):''}</small></div>`;
const txRow=t=>`<div class="li"><span style="display:inline-flex;gap:10px;align-items:center">${t.crypto?UI.logo(t,28):''}<span>${esc(label(t.type))}${t.crypto?' · '+esc(t.asset):(t.metadata&&t.metadata.crypto_conversion&&t.metadata.asset?' · '+esc(t.metadata.asset)+' conversion':'')}<small>${esc(t.ref)} · ${day(t.created_at)}</small></span></span><span style="text-align:right"><b class="${t.direction==='credit'?'pos':''}">${t.direction==='credit'?'+':'−'}${t.crypto?esc(t.amount)+' '+esc(t.currency):usd(t.amount)}</b><br>${badge(t.status)}</span></div>`;
const wRow=a=>`<div class="li"><span style="display:flex;gap:8px;align-items:center">${a.asset_class==='Crypto'?UI.logo(a,26):''}<span>${esc(a.symbol)}<small>${esc(a.name)}</small></span></span><span style="text-align:right"><b>${usd(a.price)}</b><br><em class="${cls(a.change_pct)}" style="font-style:normal;font-size:12px">${pc(a.change_pct)}</em></span></div>`;
function badge_(){const b=$('#nbadge');if(b){b.hidden=!UNREAD;b.textContent=UNREAD}const n=$('#ndot');if(n)n.hidden=!UNREAD;$$('.m-nb').forEach(e=>{e.hidden=!UNREAD;e.textContent=UNREAD>99?'99+':UNREAD})}

/* ---------- portfolio ---------- */
/* Every figure below is rendered from GET /api/portfolio (summary, holdings, allocation, watchlist, recent_activity). No money maths happens in the browser. */
const PF={q:'',cls:'all',alloc:'all',perf:'all',sort:'value'};
const PFCOL=['#ffffff','#dcdcdc','#bdbdbd','#9e9e9e','#808080','#626262','#454545'];
const ago=s=>{const d=(Date.now()-new Date(s))/1000;return d<60?'just now':d<3600?Math.floor(d/60)+'m ago':d<86400?Math.floor(d/3600)+'h ago':d<2592000?Math.floor(d/86400)+'d ago':day(s)};
const tone=n=>Number(n)>=0?'up':'dn',arw=n=>Number(n)>=0?'↑':'↓';
const goTrade=(sym,side)=>{SEL=sym;SIDE=side;location.hash='#/trade'};
function pfCard(i,icon,l,v,sub,t='',ts=t){
 return`<article class="pf-card" role="group" aria-roledescription="slide" aria-label="${i+1} of 4: ${esc(l)}"><div class="pf-ch"><span class="pf-ic" aria-hidden="true">${ico(icon,20)}</span><h2>${esc(l)}</h2></div><p class="pf-v ${t}">${v}</p><p class="pf-s ${ts}">${sub||'&nbsp;'}</p>
 <div class="pf-pg"><span aria-hidden="true">${i+1} / 4</span><span class="pf-dots">${[0,1,2,3].map(j=>`<button type="button" data-slide="${j}" class="${j===i?'on':''}" aria-label="Show card ${j+1} of 4" ${j===i?'aria-current="true"':''}></button>`).join('')}</span></div></article>`}
function pfCards(s,n){
 const dc=Number(s.day_change);
 return[pfCard(0,'portfolio','Holdings value',usd(s.holdings_value),`<span aria-hidden="true">${arw(dc)}</span> ${sgn(dc)} (${pc(s.holdings_day_change_pct)}) <em>today</em>`,'',tone(dc)),
  pfCard(1,'savings','Cost basis',usd(s.total_invested),`<em>Across ${n} holding${n===1?'':'s'}</em>`),
  pfCard(2,'invest','Unrealized gain/loss',sgn(s.unrealized_pl),`(${pc(s.unrealized_pl_pct)})`,tone(s.unrealized_pl)),
  pfCard(3,'goals','Realized gain/loss',sgn(s.realized_pl),`(${pc(s.realized_pl_pct)})`,tone(s.realized_pl))].join('')}
function pfFilter(hs){
 const q=PF.q.trim().toLowerCase(),a=h=>Number(h.allocation_pct);
 const keep=h=>(!q||[h.symbol,h.name,h.asset_class].join(' ').toLowerCase().includes(q))&&(PF.cls==='all'||h.asset_class===PF.cls)
  &&(PF.perf==='all'||(PF.perf==='gain'?Number(h.unrealized)>=0:Number(h.unrealized)<0))
  &&(PF.alloc==='all'||(PF.alloc==='10'?a(h)>=10:PF.alloc==='5'?a(h)>=5&&a(h)<10:a(h)<5));
 const by={value:(x,y)=>y.value-x.value,alloc:(x,y)=>a(y)-a(x),pl:(x,y)=>y.unrealized-x.unrealized,pct:(x,y)=>y.unrealized_pct-x.unrealized_pct,symbol:(x,y)=>x.symbol.localeCompare(y.symbol)};
 return hs.filter(keep).sort((x,y)=>PF.sort==='symbol'?by.symbol(x,y):by[PF.sort](x,y))}
const pfActive=()=>(PF.cls!=='all')+(PF.perf!=='all')+(PF.alloc!=='all')+(PF.sort!=='value');
function pfRow(h){
 const w=Math.max(0,Math.min(100,Number(h.allocation_pct)||0)),t=tone(h.unrealized);
 return`<div class="hrow" role="row"><div class="hc hc-a" role="cell"><button type="button" class="alink" data-act="view" data-sym="${esc(h.symbol)}" aria-label="View ${esc(h.symbol)} details">${h.asset_class==='Crypto'?UI.logo(h,36):`<span class="av" aria-hidden="true">${esc(h.symbol.slice(0,2))}</span>`}<span class="nm"><b>${esc(h.symbol)}</b><small>${esc(h.name)} · ${esc(h.asset_class)}</small></span></button></div>
 <div class="hc hc-q" role="cell"><span>${esc(h.quantity)}</span><span class="u"> units</span></div><div class="hc hc-c" role="cell">${usd(h.avg_cost)}</div><div class="hc hc-p" role="cell">${usd(h.price)}</div><div class="hc hc-v" role="cell"><b>${usd(h.value)}</b></div>
 <div class="hc hc-l" role="cell"><b class="${t}">${sgn(h.unrealized)}</b><small class="${t}">(${pc(h.unrealized_pct)})</small></div>
 <div class="hc hc-g" role="cell"><span>${esc(h.allocation_pct)}%</span><span class="bar" aria-hidden="true"><i style="width:${w}%"></i></span></div>
 <div class="hc hc-k" role="cell"><button type="button" class="kb" data-menu="${esc(h.symbol)}" aria-haspopup="menu" aria-expanded="false" aria-label="Actions for ${esc(h.symbol)}">${ico('kebab',18)}</button></div></div>`}
function pfRows(hs,all){
 if(!all)return'<div class="empty">No holdings yet. <a href="#/invest">Invest</a> or <a href="#/trade">place a trade</a> to build your portfolio.</div>';
 const list=pfFilter(hs);if(!list.length)return'<div class="empty">No holdings match your search or filters.</div>';
 return`<div class="hrow hhead" role="row">${['Asset','Quantity','Avg cost','Price','Value','Unrealized P/L','Alloc.'].map((c,i)=>`<div class="hc hc-${'aqcpvlg'[i]}" role="columnheader">${c}</div>`).join('')}<div class="hc hc-k" role="columnheader"><span class="sr">Actions</span></div></div>`+list.map(pfRow).join('')}
function pfAlloc(rows,total){
 const t=rows.reduce((a,r)=>a+Number(r.value),0);let o=0;
 const arcs=t?rows.map((r,i)=>{const p=Number(r.value)/t*100;if(p<=0)return'';const d=Math.max(p-.7,.1),c=`<circle cx="21" cy="21" r="15.915" fill="none" stroke="${PFCOL[i%7]}" stroke-width="5" pathLength="100" stroke-dasharray="${d} ${100-d}" stroke-dashoffset="${-o}"/>`;o+=p;return c}).join(''):'';
 const desc=`Asset allocation of ${usd(total)}: `+rows.map(r=>`${r.label} ${Number(r.pct).toFixed(1)}%`).join(', ');
 return`<div class="pf-al"><div class="pf-do"><svg viewBox="0 0 42 42" role="img" aria-label="${esc(desc)}" style="transform:rotate(-90deg)"><circle cx="21" cy="21" r="15.915" fill="none" stroke="#1f1f1f" stroke-width="5"/>${arcs}</svg><div class="pf-dc"><b>${usd(total)}</b><small>Total value</small></div></div>
 <ul class="pf-leg">${rows.map((r,i)=>`<li><i style="background:${PFCOL[i%7]}" aria-hidden="true"></i><span>${esc(r.label)}</span><b>${esc(r.pct)}%</b></li>`).join('')}</ul></div>`}
const pfWatch=w=>w.length?`<div class="pf-wh" aria-hidden="true"><span>Asset</span><span>Price</span><span>24h Change</span></div><ul class="pf-wl">${w.map(a=>`<li>${a.asset_class==='Crypto'?UI.logo(a,36):`<span class="av" aria-hidden="true">${esc(a.symbol.slice(0,2))}</span>`}<span class="nm"><b>${esc(a.symbol)}</b><small>${esc(a.name)}</small></span><span class="px">${usd(a.price)}</span><span class="ch ${tone(a.change_pct)}"><i aria-hidden="true">${Number(a.change_pct)>=0?'▲':'▼'}</i> ${pc(a.change_pct)}</span></li>`).join('')}</ul>`:'<div class="empty">Your watchlist is empty. <a href="#/watchlist">Add assets</a>.</div>';
function pfAct(t){
 const trade=t.type==='trade'&&t.symbol,buy=t.side==='buy',inv=t.type==='investment';
 const title=trade?`${buy?'Bought':'Sold'} ${t.symbol}`:inv?`Invested in ${t.product||t.symbol||'a product'}`:label(t.type);
 const detail=[trade&&t.quantity?`${Number(t.quantity)} units`:'',usd(t.amount),t.status!=='completed'?label(t.status):''].filter(Boolean).join(' · ');
 const ic=trade?(buy?'up':'down'):inv?'up':'sq';
 return`<li><span class="ai" aria-hidden="true">${ico(ic,18)}</span><span class="at"><b>${esc(title)}</b><small>${esc(detail)}</small></span><time datetime="${esc(t.created_at)}">${esc(ago(t.created_at))}</time></li>`}
function pfDetail(h,s){
 const d=modal(h.symbol+' · '+h.name,`<dl><dt>Asset class</dt><dd>${esc(h.asset_class)}</dd><dt>Quantity</dt><dd>${esc(h.quantity)}</dd><dt>Average cost</dt><dd>${usd(h.avg_cost)}</dd><dt>Current price</dt><dd>${usd(h.price)}</dd><dt>Value</dt><dd>${usd(h.value)}</dd><dt>Cost basis</dt><dd>${usd(h.cost)}</dd><dt>Unrealized P/L</dt><dd class="${tone(h.unrealized)}">${sgn(h.unrealized)} (${pc(h.unrealized_pct)})</dd><dt>Realized P/L</dt><dd class="${tone(h.realized)}">${sgn(h.realized)}</dd><dt>Change today</dt><dd class="${tone(h.day_change)}">${sgn(h.day_change)}</dd><dt>Allocation</dt><dd>${esc(h.allocation_pct)}% of holdings</dd></dl>
 <div class="actions"><button class="btn ghost" data-go="sell">Sell</button><button class="btn" data-go="buy">Buy more</button></div>`);
 d.onclick=e=>{const b=e.target.closest('[data-go]');if(b){d.close();goTrade(h.symbol,b.dataset.go)}}}
function pfMenu(btn,h){
 pfCloseMenu();const m=document.createElement('div');m.className='pf-menu';m.setAttribute('role','menu');
 m.innerHTML=[['view','View details'],['trade','Trade'],['buy','Buy more'],['sell','Sell']].map(([k,l])=>`<button type="button" role="menuitem" data-act="${k}" data-sym="${esc(h.symbol)}">${l}</button>`).join('');
 btn.parentElement.append(m);btn.setAttribute('aria-expanded','true');m.querySelector('button').focus()}
function pfCloseMenu(){$$('.pf-menu').forEach(m=>{const b=m.parentElement.querySelector('[data-menu]');if(b)b.setAttribute('aria-expanded','false');m.remove()})}
let pfBound=false;
function pfGlobal(){if(pfBound)return;pfBound=true;
 document.addEventListener('click',e=>{if(!e.target.closest('.pf-menu,[data-menu]'))pfCloseMenu();if(!e.target.closest('.pf-filter,[data-filter]')){const f=$('#pf-fp');if(f&&!f.hidden){f.hidden=true;$('[data-filter]').setAttribute('aria-expanded','false')}}});
 document.addEventListener('keydown',e=>{if(e.key==='Escape'){const o=$('.pf-menu');if(o)o.parentElement.querySelector('[data-menu]').focus();pfCloseMenu();const f=$('#pf-fp');if(f&&!f.hidden){f.hidden=true;$('[data-filter]').setAttribute('aria-expanded','false');$('[data-filter]').focus()}}
  const m=e.target.closest&&e.target.closest('.pf-menu');if(m&&(e.key==='ArrowDown'||e.key==='ArrowUp')){e.preventDefault();const b=[...m.querySelectorAll('button')],i=b.indexOf(document.activeElement);b[(i+(e.key==='ArrowDown'?1:-1)+b.length)%b.length].focus()}})}
R.portfolio=()=>load(async v=>{
 pfGlobal();const p=await V.get('/api/portfolio'),s=p.summary,hs=p.holdings.map(h=>({...h,value:+h.value,unrealized:+h.unrealized,unrealized_pct:+h.unrealized_pct,_raw:h}));
 const raw=Object.fromEntries(p.holdings.map(h=>[h.symbol,h])),classes=[...new Set(hs.map(h=>h.asset_class))],n=hs.length;
 const sel=(id,l,opts,cur)=>`<label class="f" for="${id}">${l}<select id="${id}">${opts.map(([k,t])=>`<option value="${esc(k)}" ${k===cur?'selected':''}>${esc(t)}</option>`).join('')}</select></label>`;
 v.innerHTML=`<div class="pf"><div class="pf-main"><header class="pf-head"><h1>Portfolio</h1><p>Track your holdings, performance and overall portfolio value.</p></header>
 <section class="pf-cards" id="pf-cards" role="region" aria-roledescription="carousel" aria-label="Portfolio summary. Use left and right arrow keys to move between cards." tabindex="0">${pfCards(s,n)}</section>
 <section class="box pf-hold" aria-labelledby="pf-h"><div class="pf-hh"><div><h2 id="pf-h">Holdings</h2><p class="pf-sub" id="pf-count" aria-live="polite">${n} asset${n===1?'':'s'} · ${usd(s.holdings_value)} total value</p></div>
  <div class="pf-tools"><label class="pf-search"><span class="sr">Search holdings</span>${ico('search',17)}<input id="pf-q" type="search" placeholder="Search holdings…" value="${esc(PF.q)}" autocomplete="off" enterkeyhint="search"></label>
  <div class="pf-filter"><button type="button" class="pf-btn" data-filter aria-expanded="false" aria-controls="pf-fp">${ico('filter',17)}<span>Filter</span><em id="pf-fn" ${pfActive()?'':'hidden'}>${pfActive()}</em></button>
   <div class="pf-fp" id="pf-fp" role="group" aria-label="Filter holdings" hidden>${sel('pf-fc','Asset class',[['all','All classes'],...classes.map(c=>[c,c])],PF.cls)}${sel('pf-fa','Allocation',[['all','Any'],['10','10% or more'],['5','5% to 10%'],['0','Under 5%']],PF.alloc)}${sel('pf-fpf','Performance',[['all','All'],['gain','Gaining (P/L ≥ 0)'],['loss','Losing (P/L < 0)']],PF.perf)}${sel('pf-fs','Sort by',[['value','Value'],['alloc','Allocation'],['pl','Unrealized P/L ($)'],['pct','Unrealized P/L (%)'],['symbol','Symbol A–Z']],PF.sort)}<button type="button" class="btn ghost sm" id="pf-fr">Reset</button></div></div>
  <a class="pf-btn" href="/api/portfolio/export" download="vaulta-holdings.csv">${ico('download',17)}<span>Export</span></a></div></div>
  <div class="pf-tbl" id="pf-rows" role="table" aria-label="Holdings">${pfRows(hs,n)}</div></section></div>
 <div class="pf-side"><section class="pf-dark" aria-labelledby="pf-a"><div class="pf-sh"><h2 id="pf-a">Asset Allocation</h2><button type="button" class="pf-more" id="pf-ad">View details ${ico('arrow',15)}</button></div>${pfAlloc(p.allocation,s.portfolio_value)}</section>
  <section class="box pf-watch" aria-labelledby="pf-w"><div class="pf-sh"><h2 id="pf-w">Watchlist</h2><a class="pf-more" href="#/watchlist">View all ${ico('arrow',15)}</a></div>${pfWatch(p.watchlist)}</section>
  <section class="pf-dark pf-act" aria-labelledby="pf-r"><div class="pf-sh"><h2 id="pf-r">Recent Activity</h2><a class="pf-more" href="#/transactions">View all ${ico('arrow',15)}</a></div>${p.recent_activity.length?`<ul class="pf-al-list">${p.recent_activity.map(pfAct).join('')}</ul>`:'<div class="empty">No activity yet. Make a deposit to get started.</div>'}</section></div></div>
 <p class="note" style="margin-top:14px">Values are calculated by the server from your holdings and the latest asset prices. Past performance does not guarantee future results.</p>`;
 const redraw=()=>{$('#pf-rows').innerHTML=pfRows(hs,n);const fl=PF.q.trim()||PF.cls!=='all'||PF.perf!=='all'||PF.alloc!=='all',k=pfFilter(hs).length,tail=` · ${usd(s.holdings_value)} total value`,fn=pfActive();$('#pf-count').textContent=(fl?`Showing ${k} of ${n}`:`${n}`)+` asset${n===1?'':'s'}`+tail;const b=$('#pf-fn');b.hidden=!fn;b.textContent=fn};
 $('#pf-q').oninput=e=>{PF.q=e.target.value;redraw()};
 [['pf-fc','cls'],['pf-fa','alloc'],['pf-fpf','perf'],['pf-fs','sort']].forEach(([id,k])=>$('#'+id).onchange=e=>{PF[k]=e.target.value;redraw()});
 $('#pf-fr').onclick=()=>{Object.assign(PF,{cls:'all',alloc:'all',perf:'all',sort:'value'});[['pf-fc','all'],['pf-fa','all'],['pf-fpf','all'],['pf-fs','value']].forEach(([id,x])=>$('#'+id).value=x);redraw()};
 $('#pf-ad').onclick=()=>modal('Asset allocation',`<div class="pf-dlg">${table([{h:'Asset class',k:'label'},{h:'Value',cls:'r',r:r=>usd(r.value)},{h:'Share',cls:'r',r:r=>esc(r.pct)+'%'}],p.allocation)}<p class="note" style="margin-top:12px">Share of holdings at current prices plus posted cash (${usd(s.portfolio_value)}). Calculated by the server.</p></div>`);
 const cards=$('#pf-cards'),rm=matchMedia('(prefers-reduced-motion:reduce)').matches;
 cards.onkeydown=e=>{if(e.target!==cards||!['ArrowRight','ArrowLeft'].includes(e.key))return;e.preventDefault();cards.scrollBy({left:(e.key==='ArrowRight'?1:-1)*cards.clientWidth,behavior:rm?'auto':'smooth'})};
 v.onclick=e=>{
  const dot=e.target.closest('[data-slide]');if(dot){$$('.pf-card',cards)[+dot.dataset.slide].scrollIntoView({behavior:rm?'auto':'smooth',inline:'center',block:'nearest'});return}
  const f=e.target.closest('[data-filter]');if(f){const pn=$('#pf-fp');pn.hidden=!pn.hidden;f.setAttribute('aria-expanded',String(!pn.hidden));if(!pn.hidden)$('#pf-fc').focus();return}
  const mb=e.target.closest('[data-menu]');if(mb){const open=mb.getAttribute('aria-expanded')==='true';if(open)pfCloseMenu();else pfMenu(mb,raw[mb.dataset.menu]);return}
  const a=e.target.closest('[data-act]');if(!a)return;const h=raw[a.dataset.sym];pfCloseMenu();if(!h)return;
  if(a.dataset.act==='view')pfDetail(h,s);else goTrade(h.symbol,a.dataset.act==='sell'?'sell':'buy')}});

/* ---------- invest ---------- */
let CAT='All';
R.invest=()=>load(async v=>{
 const {products}=await V.get('/api/products'),cats=['All',...new Set(products.map(p=>p.category))];
 const sq=SQ.toLowerCase(),list=products.filter(p=>(CAT==='All'||p.category===CAT)&&(!sq||[p.name,p.category,p.issuer,p.kind,p.description].join(' ').toLowerCase().includes(sq)));
 v.innerHTML=head('Invest')+(SQ?`<p class="note" style="margin-bottom:14px">Results for “${esc(SQ)}” · <a href="#/invest" id="sqc">Clear search</a></p>`:'')+`<div class="tabs-in" id="cats">${cats.map(c=>`<a href="#/invest" data-c="${esc(c)}" class="${c===CAT?'on':''}">${esc(c)}</a>`).join('')}</div>
 <div class="grid g3">${list.map(p=>`<article class="box prod"><div class="t"><span>${esc(p.kind||p.category)} · ${esc(p.issuer||'')}</span><span class="risk r${p.risk[0]}">${esc(p.risk)} risk</span></div><h3>${esc(p.name)}</h3><p style="font-size:14px;color:var(--dgray)">${esc(p.description||'')}</p>${line(p.series,{h:70})}<div class="li"><span><small>${p.return_basis==='illustrative'?'Illustrative return (not a forecast)':'12-month price change'}</small><b class="${cls(p.return_pct)}">${pc(p.return_pct)}</b></span><span style="text-align:right"><small>Minimum</small><b>${usd(p.min_investment)}</b></span></div><button class="btn" data-inv="${p.id}">Invest</button></article>`).join('')||'<div class="empty">No products in this category.</div>'}</div>
 <p class="note" style="margin-top:14px">Investing involves risk, including possible loss of principal. Past performance does not guarantee future results.</p>`;
 const sc=$('#sqc');if(sc)sc.onclick=e=>{e.preventDefault();SQ='';R.invest()};
 $('#cats').onclick=e=>{const a=e.target.closest('[data-c]');if(a){e.preventDefault();CAT=a.dataset.c;R.invest()}};
 view().onclick=e=>{const b=e.target.closest('[data-inv]');if(b)investFlow(products.find(p=>p.id==b.dataset.inv))}});
function investFlow(p){
 const d=modal('Invest in '+p.name,`<div id="s1"><p class="note">Minimum ${usd(p.min_investment)} · fee ${esc(p.fee_pct)}% · risk ${esc(p.risk)}</p><label class="f" style="margin-top:12px">Amount (USD)<input id="amt" inputmode="decimal" value="${esc(p.min_investment)}"></label><label class="f">Funding source<select disabled><option>VAULTA cash balance</option></select></label>
 <div id="rev" class="note"></div><label class="f" style="display:flex;gap:10px;align-items:flex-start;margin-top:12px"><input type="checkbox" id="ack" style="width:auto;min-height:0;margin-top:3px"><span style="font-weight:500">I have read the risk disclosure. ${esc(p.disclaimer)} <a href="/legal/risk-disclosure/" target="_blank" rel="noopener">Risk disclosure</a></span></label>
 ${ME.two_factor_enabled?'<label class="f">2FA code (required for larger amounts)<input id="code" inputmode="numeric" maxlength="6"></label>':''}<p class="neg" id="msg" role="alert"></p><div class="actions"><button class="btn ghost" data-x>Cancel</button><button class="btn" id="go" disabled>Confirm investment</button></div></div><div id="s2" hidden></div>`);
 const upd=()=>{const a=parseFloat($('#amt',d).value)||0,fee=a*p.fee_bps/10000;$('#rev',d).innerHTML=`Amount ${usd(a)}<br>Estimated fee ${usd(fee)} (final fee calculated by the server)<br><b>Total ${usd(a)}</b> (fee is deducted from the amount invested)`;$('#go',d).disabled=!$('#ack',d).checked||a<+p.min_investment};
 $('#amt',d).oninput=upd;$('#ack',d).onchange=upd;upd();
 $('#go',d).onclick=async e=>{const b=e.currentTarget;b.disabled=true;b.textContent='Submitting…';$('#msg',d).textContent='';
  try{const r=await V.money('/api/investments',{product_id:p.id,amount:$('#amt',d).value.trim(),accepted_disclosures:true,code:$('#code',d)?.value||undefined});const i=r.investment;
   $('#s1',d).hidden=true;$('#s2',d).hidden=false;$('#s2',d).innerHTML=`<h3>${i.status==='completed'?'Investment completed':'Investment '+esc(i.status)}</h3><dl><dt>Transaction ID</dt><dd>${esc(i.transaction_ref)}</dd><dt>Reference</dt><dd>${esc(i.ref)}</dd><dt>Amount</dt><dd>${usd(i.amount)}</dd><dt>Fee</dt><dd>${usd(i.fee)}</dd><dt>Units</dt><dd>${esc(i.quantity||'pending')}</dd><dt>Status</dt><dd>${badge(i.status)}</dd></dl><div class="actions"><a class="btn" href="#/portfolio" data-x>View portfolio</a></div>`}
  catch(err){$('#msg',d).textContent=err.message+(err.code==='insufficient_funds'?' Add funds from Deposit.':'');b.disabled=false;b.textContent='Confirm investment'}}}

/* ---------- trade ---------- */
let SEL='AAPL',SIDE='buy';
R.trade=()=>load(async v=>{
 const [{assets},{orders}]=await Promise.all([V.get('/api/assets'),V.get('/api/orders')]);
 const tradable=assets.filter(a=>a.tradable&&a.status==='active');if(!tradable.some(a=>a.symbol===SEL))SEL=tradable[0]?.symbol;
 v.innerHTML=head('Trade')+`<div class="grid g21"><section class="box"><div class="row-h"><h3 id="ah"></h3><div class="seg" id="tr">${['1D','1W','1M','3M','1Y'].map(r=>`<button data-r="${r}" class="${r==='1M'?'on':''}">${r}</button>`).join('')}</div></div><div id="tc"></div>
 <input class="in" id="q" placeholder="Search assets" aria-label="Search assets" style="margin:14px 0"><div id="al" style="max-height:300px;overflow:auto"></div></section>
 <section class="box"><h3>Place order</h3><form id="of"><div class="seg" style="margin-bottom:12px"><button type="button" class="${SIDE==='buy'?'on':''}" data-side="buy">Buy</button><button type="button" class="${SIDE==='sell'?'on':''}" data-side="sell">Sell</button></div>
 <label class="f">Asset<select name="symbol" id="osym">${tradable.map(a=>`<option>${esc(a.symbol)}</option>`).join('')}</select></label><label class="f">Order type<select name="order_type" id="ot"><option value="market">Market</option><option value="limit">Limit</option><option value="stop">Stop</option><option value="stop_limit">Stop-limit</option></select></label>
 <label class="f">Quantity<input name="quantity" id="oq" inputmode="decimal" placeholder="0.00"></label><label class="f" id="lw" hidden>Limit price<input name="limit_price" inputmode="decimal"></label><label class="f" id="sw" hidden>Stop price<input name="stop_price" inputmode="decimal"></label>
 ${ME.two_factor_enabled?'<label class="f">2FA code (for larger orders)<input name="code" inputmode="numeric" maxlength="6"></label>':''}<div class="note" id="est"></div><p class="neg" id="om" role="alert"></p><button class="btn" id="osub" style="width:100%;margin-top:10px">Place ${SIDE} order</button></form><p class="note" style="margin-top:12px">Market orders fill at the current price. Limit and stop orders stay open until the price condition is met. The status shown is always the one the server reports.</p></section></div>
 <section class="box" style="margin-top:18px"><h3>Orders</h3>${table([{h:'Order',r:o=>`${esc(o.ref)}<small>${dt(o.created_at)}</small>`},{h:'Asset',r:o=>esc(o.symbol)},{h:'Side',r:o=>esc(label(o.side))+' · '+esc(label(o.order_type))},{h:'Qty',cls:'r',k:'quantity'},{h:'Price',cls:'r',r:o=>o.execution_price?usd(o.execution_price):(o.limit_price?'≤/≥ '+usd(o.limit_price):'market')},{h:'Fee',cls:'r',r:o=>usd(o.fee)},{h:'Status',r:o=>badge(o.status)},{h:'',r:o=>['open','pending','partially_filled'].includes(o.status)?`<button class="btn ghost sm" data-cx="${o.id}">Cancel</button>`:(o.reason?`<small>${esc(o.reason)}</small>`:'')}],orders,{empty:'No orders yet.'}).replace('class="tbl"','class="tbl stack"')}</section>`;
 let side=SIDE;SIDE='buy';const px=()=>assets.find(a=>a.symbol===$('#osym').value);
 const draw=async r=>{const a=assets.find(x=>x.symbol===SEL);if(!a)return;$('#ah').innerHTML=`${esc(a.symbol)} <small>${esc(a.name)}</small> <b>${usd(a.price)}</b> <span class="${cls(a.change_pct)}">${pc(a.change_pct)}</span>`;$('#tc').innerHTML=skeleton(1);try{const j=await V.get(`/api/assets/${SEL}/prices?range=${r}`);$('#tc').innerHTML=line(j.points.map(p=>p.price))}catch(e){$('#tc').innerHTML=errBox(e.message)}$('#osym').value=SEL;est()};
 const list=()=>{const q=$('#q').value.toLowerCase();$('#al').innerHTML=assets.filter(a=>!q||a.symbol.toLowerCase().includes(q)||a.name.toLowerCase().includes(q)).map(a=>`<div class="li click" data-s="${esc(a.symbol)}" tabindex="0" style="cursor:pointer"><span style="display:flex;gap:8px;align-items:center">${a.asset_class==='Crypto'?UI.logo(a,26):''}<span>${esc(a.symbol)}<small>${esc(a.name)}</small></span></span><span style="text-align:right">${usd(a.price)}<br><small class="${cls(a.change_pct)}">${pc(a.change_pct)}</small></span></div>`).join('')};
 function est(){const t=$('#ot').value;$('#lw').hidden=!(t==='limit'||t==='stop_limit');$('#sw').hidden=!(t==='stop'||t==='stop_limit');const a=px(),q=parseFloat($('#oq').value)||0;$('#est').textContent=a?`Estimated value at last price ${usd(a.price)}: ${usd(q*a.price)} (fee and final value set by the server)`:''}
 $('#al').onclick=e=>{const r=e.target.closest('[data-s]');if(r){SEL=r.dataset.s;draw($('#tr .on').dataset.r)}};$('#q').oninput=list;list();draw('1M');
 $('#tr').onclick=e=>{const b=e.target.closest('[data-r]');if(!b)return;$$('#tr button').forEach(x=>x.classList.toggle('on',x===b));draw(b.dataset.r)};
 $('#osym').onchange=()=>{SEL=$('#osym').value;draw($('#tr .on').dataset.r)};['#ot','#oq'].forEach(s=>$(s).addEventListener('input',est));
 $$('[data-side]').forEach(b=>b.onclick=()=>{side=b.dataset.side;$$('[data-side]').forEach(x=>x.classList.toggle('on',x===b));$('#osub').textContent='Place '+side+' order'});
 $('#of').onsubmit=async e=>{e.preventDefault();const f=formData(e.target);const body={symbol:f.symbol,side,order_type:f.order_type,quantity:f.quantity};if(f.limit_price&&!$('#lw').hidden)body.limit_price=f.limit_price;if(f.stop_price&&!$('#sw').hidden)body.stop_price=f.stop_price;if(f.code)body.code=f.code;
  const b=$('#osub');b.disabled=true;$('#om').textContent='';
  try{const r=await V.money('/api/orders',body);toast(`Order ${r.order.ref}: ${label(r.order.status)}`);R.trade()}catch(err){$('#om').textContent=err.message;b.disabled=false}};
 v.onclick=async e=>{const c=e.target.closest('[data-cx]');if(c&&await confirmBox('Cancel order','Cancel this order and release any reserved cash?',{ok:'Cancel order',danger:true})){await run(c,()=>V.post(`/api/orders/${c.dataset.cx}/cancel`),'Order cancelled');R.trade()}}});

/* ---------- savings & goals ---------- */
R.savings=()=>load(async v=>{
 const s=await V.get('/api/savings'),a=s.account;
 v.innerHTML=head('Savings')+`<div class="grid g4 keep2">${kpi('Savings balance',usd(a.posted))}${kpi('APY (variable)',esc(a.apy_pct)+'%')}${kpi('Interest earned',usd(s.accrued_interest))}${kpi('Available to move',usd(a.available))}</div>
 <div class="grid g2" style="margin-top:18px"><section class="box"><h3>Move money</h3><form id="tf"><div class="f-row"><label class="f">Direction<select name="direction"><option value="to_savings">Account Balance → Savings</option><option value="to_cash">Savings → Account Balance</option></select></label><label class="f">Amount<input name="amount" inputmode="decimal" required></label></div><p class="neg" id="tm" role="alert"></p><button class="btn">Transfer</button></form><p class="note" style="margin-top:12px">${esc(s.note)}</p></section>
 <section class="box"><h3>Recent savings activity</h3>${s.recent.length?s.recent.map(txRow).join(''):'<div class="empty">No activity yet.</div>'}</section></div>`;
 $('#tf').onsubmit=async e=>{e.preventDefault();$('#tm').textContent='';const b=$('button',e.target);b.disabled=true;try{await V.money('/api/savings/transfer',formData(e.target));toast('Transfer complete');R.savings()}catch(err){$('#tm').textContent=err.message;b.disabled=false}}});
R.goals=()=>load(async v=>{
 const {goals}=await V.get('/api/goals');
 v.innerHTML=head('Goals','<button class="btn" id="ng">New goal</button>')+`<div class="grid g3">${goals.map(g=>`<article class="box">${goalRow(g)}<small>${g.auto?`Auto-contribution: ${usd(g.contribution)} ${esc(g.frequency)} · next ${esc(g.auto.next_run)}${g.auto.last_result&&g.auto.last_result!=='ok'?' · last run skipped':''}`:'No automatic schedule'}</small><div class="actions" style="justify-content:flex-start">${g.status==='completed'?badge('completed'):`<button class="btn sm" data-c="${g.id}">Contribute</button><button class="btn ghost sm" data-p="${g.id}" data-st="${g.status==='paused'?'active':'paused'}">${g.status==='paused'?'Resume':'Pause'}</button>`}<button class="btn ghost sm" data-x="${g.id}">Remove</button></div></article>`).join('')||'<div class="box empty">No goals yet. Set one to start tracking progress.</div>'}</div>`;
 $('#ng').onclick=()=>{const d=modal('New goal',`<form id="gf"><label class="f">Name<input name="name" required maxlength="80"></label><label class="f">Type<select name="category">${['Emergency Fund','New Home','Vacation','Education','Retirement','Custom'].map(c=>`<option>${c}</option>`).join('')}</select></label><div class="f-row"><label class="f">Target amount<input name="target_amount" inputmode="decimal" required></label><label class="f">Target date<input type="date" name="target_date"></label></div><div class="f-row"><label class="f">Automatic contribution<select name="frequency"><option value="">None</option><option value="weekly">Weekly</option><option value="biweekly">Every 2 weeks</option><option value="monthly">Monthly</option></select></label><label class="f">Amount each time<input name="contribution_amount" inputmode="decimal"></label></div><p class="neg" id="gm" role="alert"></p><div class="actions"><button class="btn ghost" type="button" data-x>Cancel</button><button class="btn">Create goal</button></div></form>`);
  $('#gf',d).onsubmit=async e=>{e.preventDefault();const f=formData(e.target);Object.keys(f).forEach(k=>f[k]===''&&delete f[k]);try{await V.post('/api/goals',f);d.close();toast('Goal created');R.goals()}catch(err){$('#gm',d).textContent=err.message}}};
 v.onclick=async e=>{const c=e.target.closest('[data-c]'),p=e.target.closest('[data-p]'),x=e.target.closest('[data-x]');
  if(c){const d=modal('Contribute',`<form id="cf"><label class="f">Amount from cash<input name="amount" inputmode="decimal" required></label><p class="neg" id="cm" role="alert"></p><div class="actions"><button class="btn">Contribute</button></div></form>`);$('#cf',d).onsubmit=async ev=>{ev.preventDefault();try{await V.money(`/api/goals/${c.dataset.c}/contribute`,formData(ev.target));d.close();toast('Contribution added');R.goals()}catch(err){$('#cm',d).textContent=err.message}}}
  if(p){await run(p,()=>V.post(`/api/goals/${p.dataset.p}/status`,{status:p.dataset.st}));R.goals()}
  if(x&&await confirmBox('Remove goal','Remove this goal? Money already in savings stays in your savings account.',{ok:'Remove',danger:true})){await run(x,()=>V.post(`/api/goals/${x.dataset.x}/status`,{status:'cancelled'}));R.goals()}}});

/* ---------- retirement ---------- */
R.retirement=()=>load(async v=>{
 const {retirement:r}=await V.get('/api/retirement'),p=r?r.plan:{current_age:34,retirement_age:65,current_savings:'0.00',monthly_contribution:'0.00',employer_match_pct:'0',expected_return_pct:'6',target:'1000000.00'};
 const out=r=>r?`<div class="grid g3 keep2">${kpi('Projected balance',usd(r.projection.final))}${kpi('You + employer contribute',usd(r.projection.contributed))}${kpi('Assumed growth',usd(r.projection.growth))}</div><div style="margin-top:14px">${line(r.projection.series.map(s=>s.balance))}<small>Balance by age ${r.projection.series[0].age} → ${r.projection.series.at(-1).age}</small></div><div class="grid g3" style="margin-top:12px">${['conservative','expected','optimistic'].map(k=>`<div class="note"><b>${label(k)}</b><br>${usd(r.scenarios[k])}</div>`).join('')}</div>${r.goal_progress_pct?`<p style="margin-top:12px">Projected to reach <b>${esc(r.goal_progress_pct)}%</b> of your target.</p>`:''}<p class="note warn" style="margin-top:12px">${esc(r.disclaimer)}</p>`:'<div class="empty">Enter your details to see a projection.</div>';
 v.innerHTML=head('Retirement')+`<div class="grid g2"><section class="box"><h3>Your plan</h3><form id="rf">${[['current_age','Current age'],['retirement_age','Retirement age'],['current_savings','Current savings ($)'],['monthly_contribution','Monthly contribution ($)'],['employer_match_pct','Employer match (%)'],['expected_return_pct','Assumed annual return (%)'],['target','Target balance ($)']].map(([k,l])=>`<label class="f">${l}<input name="${k}" inputmode="decimal" value="${esc(p[k])}"></label>`).join('')}<p class="neg" id="rm" role="alert"></p><div class="actions" style="justify-content:flex-start"><button class="btn" id="rs">Save plan</button></div></form></section><section class="box"><h3>Projection</h3><div id="ro">${out(r)}</div></section></div>`;
 $('#rf').onsubmit=async e=>{e.preventDefault();$('#rm').textContent='';const b=$('#rs');b.disabled=true;try{const j=await V.put('/api/retirement',formData(e.target));$('#ro').innerHTML=out(j.retirement);toast('Plan saved')}catch(err){$('#rm').textContent=err.message}b.disabled=false}});

/* ---------- watchlist ---------- */
R.watchlist=()=>load(async v=>{
 const [{items},{assets}]=await Promise.all([V.get('/api/watchlist'),V.get('/api/assets')]);const have=new Set(items.map(i=>i.symbol));
 v.innerHTML=head('Watchlist',`<select class="in" id="wa" aria-label="Add asset" style="width:auto"><option value="">Add an asset…</option>${assets.filter(a=>!have.has(a.symbol)).map(a=>`<option value="${esc(a.symbol)}">${esc(a.symbol)} · ${esc(a.name)}</option>`).join('')}</select>`)+`<section class="box">${table([{h:'Asset',r:a=>`<b>${esc(a.symbol)}</b><small>${esc(a.name)}</small>`},{h:'Class',k:'asset_class'},{h:'Price',cls:'r',r:a=>usd(a.price)},{h:'Change',cls:'r',r:a=>`<span class="${cls(a.change_pct)}">${pc(a.change_pct)}</span>`},{h:'',r:a=>`<button class="btn sm" data-t="${esc(a.symbol)}">Trade</button> <button class="btn ghost sm" data-r="${esc(a.symbol)}">Remove</button>`}],items,{empty:'Your watchlist is empty. Add an asset above.'}).replace('class="tbl"','class="tbl stack"')}</section>`;
 $('#wa').onchange=async e=>{if(e.target.value){await run(null,()=>V.post('/api/watchlist',{symbol:e.target.value}));R.watchlist()}};
 v.onclick=async e=>{const t=e.target.closest('[data-t]'),r=e.target.closest('[data-r]');if(t){SEL=t.dataset.t;location.hash='#/trade'}if(r){await run(r,()=>V.del('/api/watchlist/'+r.dataset.r));R.watchlist()}}});

/* ---------- transactions ---------- */
let TQ={page:1,type:'',status:'',q:''};
R.transactions=()=>load(async v=>{
 const qs=new URLSearchParams({page:TQ.page,size:20,...(TQ.type&&{type:TQ.type}),...(TQ.status&&{status:TQ.status}),...(TQ.q&&{q:TQ.q})});const j=await V.get('/api/transactions?'+qs);
 const sel=(id,opts,cur)=>`<select class="in" id="${id}"><option value="">All</option>${opts.map(o=>`<option value="${o}" ${o===cur?'selected':''}>${label(o)}</option>`).join('')}</select>`;
 v.innerHTML=head('Transactions')+`<div class="filters"><input class="in" id="tq" placeholder="Search reference" value="${esc(TQ.q)}" aria-label="Search">${sel('tt',['deposit','withdrawal','investment','trade','fee','interest','transfer','refund','adjustment'],TQ.type)}${sel('ts',['pending','processing','completed','failed','cancelled','reversed'],TQ.status)}</div><section class="box">${table([{h:'Date',r:t=>dt(t.created_at)},{h:'Reference',r:t=>esc(t.ref)},{h:'Type',r:t=>t.crypto?`<span style="display:inline-flex;gap:8px;align-items:center">${UI.logo(t,22)}${label(t.type)} · ${esc(t.asset)}</span>`:label(t.type)},{h:'Amount',cls:'r',r:t=>`<b class="${t.direction==='credit'?'pos':''}">${t.direction==='credit'?'+':'−'}${t.crypto?esc(t.amount)+' '+esc(t.currency):usd(t.amount)}</b>`},{h:'Fee',cls:'r',r:t=>t.crypto?(Number(t.fee)?esc(t.fee)+' '+esc(t.currency):'—'):usd(t.fee)},{h:'Status',r:t=>badge(t.status)}],j.transactions,{empty:'No transactions match.',onRow:1}).replace('class="tbl"','class="tbl stack"')}${pager(j,()=>{})}</section>`;
 const rerun=()=>{TQ.page=1;R.transactions()};$('#tt').onchange=e=>{TQ.type=e.target.value;rerun()};$('#ts').onchange=e=>{TQ.status=e.target.value;rerun()};$('#tq').onchange=e=>{TQ.q=e.target.value.trim();rerun()};
 v.onclick=e=>{const pg=e.target.closest('[data-pg]');if(pg){TQ.page=+pg.dataset.pg;R.transactions();return}const tr=e.target.closest('tr[data-i]');if(tr)txDetail(j.transactions[+tr.dataset.i])}});
async function txDetail(t){try{const j=await V.get('/api/transactions/'+t.ref);const x=j.transaction;modal('Transaction '+x.ref,`<dl><dt>Type</dt><dd>${label(x.type)}</dd><dt>Amount</dt><dd>${x.direction==='credit'?'+':'−'}${x.crypto?esc(x.amount):usd(x.amount)} ${esc(x.currency)}${x.crypto&&x.usd?' (≈ $'+esc(x.usd)+' in your Account Balance)':''}</dd><dt>Status</dt><dd>${badge(x.status)}</dd><dt>Created</dt><dd>${dt(x.created_at)}</dd><dt>Updated</dt><dd>${dt(x.updated_at||x.completed_at||x.created_at)}</dd><dt>Reference</dt><dd>${esc(x.reference||'—')}</dd>${x.crypto?`<dt>Asset</dt><dd style="display:flex;gap:8px;align-items:center">${UI.logo(x,20)} ${esc(x.asset)}</dd><dt>Network</dt><dd>${esc(x.network)}</dd>${j.funding&&j.funding.rejection_reason?`<dt>Reason</dt><dd>${esc(j.funding.rejection_reason)}</dd>`:''}`:''}${j.fees.map(f=>`<dt>${label(f.kind)}</dt><dd>${x.crypto?esc(f.amount)+' '+esc(x.currency):usd(f.amount)}</dd>`).join('')}</dl>${x.crypto?'':`<div class="actions"><a class="btn" target="_blank" rel="noopener" href="/api/documents/receipt/${encodeURIComponent(x.ref)}">Open receipt</a></div>`}`)}catch(e){toast(e.message,'err')}}

/* Deposit and Withdraw are separate pages (#/deposit and #/withdraw), implemented in crypto.js. */

/* ---------- statements ---------- */
R.statements=()=>load(async v=>{
 const d=await V.get('/api/documents');
 v.innerHTML=head('Statements & documents')+`<div class="grid g2"><section class="box"><h3>Monthly statements</h3>${d.statements.length?d.statements.map(s=>`<div class="li"><span>${esc(s.title)}</span><span><a class="btn ghost sm" target="_blank" rel="noopener" href="/api/documents/statement/${esc(s.period)}">View</a> <a class="btn ghost sm" href="/api/documents/statement/${esc(s.period)}?format=csv">CSV</a></span></div>`).join(''):'<div class="empty">Statements appear after your first transaction.</div>'}</section>
 <section class="box"><h3>Confirmations & receipts</h3>${d.receipts.length?d.receipts.map(r=>`<div class="li"><span>${label(r.type)} · ${esc(r.ref)}<small>${day(r.created_at)}</small></span><a class="btn ghost sm" target="_blank" rel="noopener" href="/api/documents/receipt/${esc(r.ref)}">Open</a></div>`).join(''):'<div class="empty">No completed transactions yet.</div>'}</section></div>
 <section class="box" style="margin-top:18px"><h3>Disclosures & tax</h3>${d.disclosures.map(x=>`<div class="li"><span>${esc(x.title)}</span><a class="btn ghost sm" target="_blank" rel="noopener" href="${esc(x.url)}">Read</a></div>`).join('')}<p class="note" style="margin-top:12px">${esc(d.tax_note)}</p></section>`});

/* ---------- notifications ---------- */
R.notifications=()=>load(async v=>{
 const j=await V.get('/api/notifications');UNREAD=j.unread;badge_();
 v.innerHTML=head('Notifications',`<button class="btn ghost" id="ra">Mark all read</button><button class="btn ghost" id="pf">Preferences</button>`)+`<section class="box">${j.notifications.length?j.notifications.map(n=>`<div class="li" style="${n.read_at?'opacity:.65':''}"><span><b>${esc(n.title)}</b> <span class="st">${esc(label(n.type))}</span><small>${esc(n.body||'')} · ${dt(n.created_at)}</small></span><span style="display:flex;gap:6px"><button class="btn ghost sm" data-t="${n.id}" data-r="${n.read_at?1:0}">${n.read_at?'Mark unread':'Mark read'}</button><button class="btn ghost sm" data-d="${n.id}" aria-label="Delete">✕</button></span></div>`).join(''):'<div class="empty">You are all caught up.</div>'}</section>`;
 $('#ra').onclick=async()=>{await V.post('/api/notifications/read',{all:true});R.notifications()};
 $('#pf').onclick=async()=>{const p=(await V.get('/api/notification-preferences')).preferences;const d=modal('Notification preferences',`<form id="pform">${table([{h:'Type',r:x=>label(x.type)},{h:'In-app',r:x=>`<input type="checkbox" data-t="${x.type}" data-k="in_app" ${x.in_app?'checked':''} ${x.locked?'disabled':''}>`},{h:'Email',r:x=>`<input type="checkbox" data-t="${x.type}" data-k="email" ${x.email?'checked':''}>`}],p)}<p class="note">Security alerts can't be turned off in-app.</p><div class="actions"><button class="btn">Save</button></div></form>`);
  $('#pform',d).onsubmit=async e=>{e.preventDefault();const m={};$$('input[data-t]',d).forEach(i=>{(m[i.dataset.t]??={type:i.dataset.t})[i.dataset.k]=i.checked});await run(null,()=>V.put('/api/notification-preferences',{preferences:Object.values(m)}),'Preferences saved');d.close()}};
 v.onclick=async e=>{const t=e.target.closest('[data-t]'),x=e.target.closest('[data-d]');if(t&&t.dataset.r!==undefined&&!t.closest('#pform')){await (t.dataset.r==='1'?V.post(`/api/notifications/${t.dataset.t}/unread`):V.post('/api/notifications/read',{ids:[+t.dataset.t]}));R.notifications()}if(x){await V.del('/api/notifications/'+x.dataset.d);R.notifications()}}});

/* ---------- support ---------- */
R.support=()=>load(async v=>{
 const j=await V.get('/api/support/tickets');
 v.innerHTML=head('Support','<button class="btn" id="nt">New ticket</button>')+`<section class="box">${table([{h:'Ticket',r:t=>`<b>${esc(t.subject)}</b><small>${esc(t.ref)}</small>`},{h:'Category',r:t=>label(t.category)},{h:'Status',r:t=>badge(t.status)},{h:'Updated',r:t=>dt(t.updated_at)}],j.tickets,{empty:'No tickets. Need help? Create one.',onRow:1}).replace('class="tbl"','class="tbl stack"')}</section>`;
 $('#nt').onclick=()=>{const d=modal('New support ticket',`<form id="tf"><label class="f">Category<select name="category">${['account','kyc','deposit','withdrawal','transaction','wallet','trading','email','technical','general','security','investment'].map(c=>`<option value="${c}">${c==='kyc'?'Account verification':c==='wallet'?'Wallets and cryptocurrency':label(c)}</option>`).join('')}</select></label><label class="f">Subject<input name="subject" maxlength="140" required></label><label class="f">How can we help?<textarea name="message" required maxlength="4000"></textarea></label><p class="neg" id="tm" role="alert"></p><div class="actions"><button class="btn">Submit</button></div></form>`);$('#tf',d).onsubmit=async e=>{e.preventDefault();try{await V.post('/api/support/tickets',formData(e.target));d.close();toast('Ticket created');R.support()}catch(err){$('#tm',d).textContent=err.message}}};
 v.onclick=e=>{const tr=e.target.closest('tr[data-i]');if(tr)ticket(j.tickets[+tr.dataset.i].ref)}});
async function ticket(ref){const j=await V.get('/api/support/tickets/'+ref);VAULTA_CHAT.open({V,modal,ref,staff:false,title:j.ticket.subject,onClose:()=>{if(location.hash==='#/support')R.support()}})}

/* ---------- security ---------- */
R.security=()=>load(async v=>{
 const [s,ev]=await Promise.all([V.get('/api/auth/sessions'),V.get('/api/auth/security-events')]);
 v.innerHTML=head('Security')+`<div class="grid g2"><section class="box"><h3>Change password</h3><form id="pw"><label class="f">Current password<input type="password" name="current_password" autocomplete="current-password" required></label><label class="f">New password<input type="password" name="new_password" autocomplete="new-password" required><small>At least 10 characters with letters and a number. Other devices are signed out.</small></label><p class="neg" id="pm" role="alert"></p><button class="btn">Update password</button></form></section>
 <section class="box"><h3>Two-factor authentication</h3><p>Status: ${ME.two_factor_enabled?badge('active')+' enabled':badge('paused')+' not enabled'}</p><p class="note" style="margin:10px 0">2FA protects withdrawals and larger investments and trades. Use an authenticator app (TOTP).</p><div id="tfa">${ME.two_factor_enabled?'<button class="btn ghost" id="d2">Disable 2FA</button>':'<button class="btn" id="e2">Set up 2FA</button>'}</div></section></div>
 <section class="box" style="margin-top:18px"><h3>Active sessions & devices</h3>${table([{h:'Device',r:x=>esc((x.user_agent||'Unknown').slice(0,60))+(x.current?' <span class="st st-active">this device</span>':'')},{h:'IP',k:'ip'},{h:'Signed in',r:x=>dt(x.created_at)},{h:'Last seen',r:x=>dt(x.last_seen_at)},{h:'',r:x=>x.current?'':`<button class="btn ghost sm" data-s="${x.id}">Sign out</button>`}],s.sessions).replace('class="tbl"','class="tbl stack"')}</section>
 <section class="box" style="margin-top:18px"><h3>Login history & security events</h3>${table([{h:'When',r:x=>dt(x.created_at)},{h:'Event',r:x=>label(x.type)},{h:'IP',r:x=>esc(x.ip||'—')},{h:'Detail',r:x=>esc(x.detail||'')}],ev.events).replace('class="tbl"','class="tbl stack"')}</section>`;
 $('#pw').onsubmit=async e=>{e.preventDefault();try{await V.post('/api/auth/change-password',formData(e.target));toast('Password updated');e.target.reset();$('#pm').textContent=''}catch(err){$('#pm').textContent=err.message}};
 const e2=$('#e2');if(e2)e2.onclick=async()=>{const j=await V.post('/api/auth/2fa/setup');$('#tfa').innerHTML=`<p>Add this key to your authenticator app:</p><p><code style="word-break:break-all;font-size:15px">${esc(j.secret)}</code></p><p class="note">Or open: <small style="word-break:break-all">${esc(j.otpauth_uri)}</small></p><form id="ef" style="margin-top:10px"><label class="f">6-digit code<input name="code" inputmode="numeric" maxlength="6" required></label><p class="neg" id="em" role="alert"></p><button class="btn">Enable 2FA</button></form>`;$('#ef').onsubmit=async ev=>{ev.preventDefault();try{await V.post('/api/auth/2fa/enable',formData(ev.target));toast('2FA enabled');ME=await V.session();R.security()}catch(err){$('#em').textContent=err.message}}};
 const d2=$('#d2');if(d2)d2.onclick=()=>{const d=modal('Disable 2FA',`<form id="xf"><label class="f">Password<input type="password" name="password" required></label><label class="f">Current 2FA code<input name="code" inputmode="numeric" maxlength="6" required></label><p class="neg" id="xm" role="alert"></p><div class="actions"><button class="btn danger">Disable</button></div></form>`);$('#xf',d).onsubmit=async ev=>{ev.preventDefault();try{await V.post('/api/auth/2fa/disable',formData(ev.target));d.close();toast('2FA disabled');ME=await V.session();R.security()}catch(err){$('#xm',d).textContent=err.message}}};
 v.onclick=async e=>{const b=e.target.closest('[data-s]');if(b){await run(b,()=>V.del('/api/auth/sessions/'+b.dataset.s),'Session signed out');R.security()}}});

/* ---------- settings: profile + verification centre ---------- */
R.settings=()=>load(async v=>{
 const [p,k]=await Promise.all([V.get('/api/profile'),V.get('/api/kyc')]);const kc=k.kyc,editable=!['under_review','verified'].includes(kc.status);
 v.innerHTML=head('Settings')+`<div class="grid g2"><section class="box"><h3>Profile</h3><form id="pf">${[['first_name','First name'],['last_name','Last name'],['phone','Phone'],['country','Country']].map(([n,l])=>`<label class="f">${l}<input name="${n}" value="${esc(n==='phone'?p.user.phone:p.profile[n])||''}"></label>`).join('')}<label class="f">Risk tolerance<select name="risk_tolerance">${['Conservative','Moderate','Aggressive'].map(o=>`<option ${p.profile.risk_tolerance===o?'selected':''}>${o}</option>`).join('')}</select></label><label class="f">Investment horizon<select name="investment_horizon">${['Short','Medium','Long'].map(o=>`<option ${p.profile.investment_horizon===o?'selected':''}>${o}</option>`).join('')}</select></label><p class="neg" id="pm" role="alert"></p><button class="btn">Save profile</button></form><p class="note" style="margin-top:12px">Email: ${esc(ME.email)} ${ME.email_verified?badge('verified'):badge('pending')}. Account ID: ${esc(ME.id)}</p></section>
 <section class="box"><h3>Identity verification</h3><p>${badge(kc.status)}</p>${kc.rejection_reason?`<p class="note warn">Rejected: ${esc(kc.rejection_reason)}</p>`:''}${kc.info_request?`<p class="note warn">More information needed: ${esc(kc.info_request)}</p>`:''}
 <form id="kf" style="margin-top:12px">${[['legal_name','Legal name'],['date_of_birth','Date of birth (YYYY-MM-DD)'],['country','Country'],['address','Address']].map(([n,l])=>`<label class="f">${l}<input name="${n}" value="${esc(kc[n]||'')}" ${editable?'':'disabled'}></label>`).join('')}${editable?'<button class="btn ghost" id="ks">Save details</button>':''}</form>
 <h3 style="margin:16px 0 8px">Documents</h3>${k.documents.map(d=>`<div class="li"><span>${label(d.doc_type)}<small>${esc(d.original_name||'')} · ${day(d.created_at)}</small></span></div>`).join('')||'<div class="empty">No documents uploaded.</div>'}
 ${editable?`<form id="ku" style="margin-top:10px"><label class="f">Upload government ID (JPG, PNG or PDF, max 5 MB)<input type="file" name="file" accept="image/jpeg,image/png,application/pdf" required></label><button class="btn ghost">Upload</button></form><div class="actions" style="justify-content:flex-start"><button class="btn" id="kx">Submit for review</button></div>`:''}
 <p class="note" style="margin-top:12px">Documents are stored privately and visible only to compliance staff. Review history: ${k.history.length?k.history.map(h=>`${label(h.decision)} (${day(h.created_at)})`).join(', '):'none yet'}.</p></section></div>`;
 $('#pf').onsubmit=async e=>{e.preventDefault();try{await V.put('/api/profile',formData(e.target));toast('Profile saved')}catch(err){$('#pm').textContent=err.message}};
 const ks=$('#ks');if(ks){ks.onclick=async e=>{e.preventDefault();await run(ks,()=>V.put('/api/kyc',formData($('#kf'))),'Details saved');R.settings()};
  $('#ku').onsubmit=async e=>{e.preventDefault();const fd=new FormData(e.target);fd.append('doc_type','government_id');await run(null,()=>V.upload('/api/kyc/documents',fd),'Document uploaded');R.settings()};
  $('#kx').onclick=async()=>{await run($('#kx'),()=>V.post('/api/kyc/submit'),'Submitted for review');ME=await V.session();R.settings()}}});

/* ---------- legal: risk-disclosure gate, legal card in settings, contextual links ---------- */
const LEGAL_DOCS=[['terms','Terms and Conditions'],['privacy','Privacy Policy'],['disclosures','Financial and Service Disclosures'],['risk-disclosure','Risk Disclosure'],['accessibility','Accessibility Statement'],['cookies','Cookie Policy']];
const legalLinks=(keys)=>LEGAL_DOCS.filter(d=>!keys||keys.includes(d[0])).map(d=>`<a href="/legal/${d[0]}/" target="_blank" rel="noopener" style="text-decoration:underline">${esc(d[1])}</a>`).join(' · ');
function riskScreen(v,s,proceed){
 v.innerHTML=head('Before you continue')+`<section class="box" style="max-width:720px"><h3>Cryptocurrency and Investment Risk Disclosure</h3>
 <p>Investing and trading, especially in digital assets, involve significant risk. You may lose some or all of the value you commit, prices can change quickly, transfers can be irreversible, and no return is guaranteed.</p>
 <p><a href="/legal/risk-disclosure/" target="_blank" rel="noopener" style="text-decoration:underline">Read the full Risk Disclosure (version ${esc(s.version)}, opens in a new tab)</a></p>
 <label class="chk" style="display:flex;gap:10px;align-items:flex-start;margin:14px 0"><input type="checkbox" id="rka"> <span>I have read and understood the Cryptocurrency and Investment Risk Disclosure (version ${esc(s.version)}).</span></label>
 <p class="neg" id="rkm" role="alert"></p><div class="actions" style="justify-content:flex-start"><button class="btn" id="rkb" disabled>Acknowledge and continue</button><a class="btn ghost" href="#/dashboard">Not now</a></div></section>`;
 const cb=$('#rka'),b=$('#rkb');cb.onchange=()=>{b.disabled=!cb.checked};
 b.onclick=async()=>{b.disabled=true;try{await V.post('/api/legal/risk-acknowledgement',{acknowledged:true,version:s.version});proceed()}catch(e){$('#rkm').textContent=e.message;if(e.code==='legal_version_changed')setTimeout(()=>route(),1500);else b.disabled=false}}}
function wrapRisk(){['invest','trade'].forEach(k=>{const orig=R[k];R[k]=async()=>{const v=view();let s=null;try{s=await V.get('/api/legal/risk-status')}catch(e){}
 if(s&&s.required&&!s.acknowledged&&s.available)riskScreen(v,s,orig);else orig()}});}      // applied after extensions (the Trade terminal replaces R.trade)
const _settings=R.settings;R.settings=async()=>{await _settings();const v=view();if(!v||!$('#pf'))return;let acc=[],rs=null;try{[acc,rs]=await Promise.all([V.get('/api/legal/my-acceptances').then(j=>j.acceptances),V.get('/api/legal/risk-status')])}catch(e){}
 const T={TERMS_ACCEPTANCE:'Terms accepted',PRIVACY_NOTICE_ACKNOWLEDGEMENT:'Privacy Policy acknowledged',RISK_DISCLOSURE_ACKNOWLEDGEMENT:'Risk Disclosure acknowledged'};
 v.insertAdjacentHTML('beforeend',`<section class="box" id="legalbox" style="margin-top:16px"><h3>Legal &amp; privacy</h3><p class="note">${legalLinks()}</p>
 <p><button class="btn ghost" type="button" data-cookie-settings>Cookie preferences</button></p>
 <h3 style="margin:16px 0 8px">Your acceptances</h3>${acc.map(a=>`<div class="li"><span>${esc(T[a.acceptance_type]||a.acceptance_type)}<small>${esc(a.title)} · version ${esc(a.version)} · ${day(a.accepted_at)}</small></span></div>`).join('')||'<div class="empty">No acceptance records yet.</div>'}
 ${rs&&rs.available&&rs.required&&!rs.acknowledged?'<p class="note warn" style="margin-top:10px">You have not yet acknowledged the current Risk Disclosure. You will be asked before you invest or trade.</p>':''}</section>`)};
function wrapDeposit(){['deposit','withdraw'].forEach(k=>{const _p=R[k];R[k]=async()=>{await _p();const v=view();if(v&&!$('#legal-note')&&$('#fp'))$('#fp').insertAdjacentHTML('beforeend',`<p class="note" id="legal-note" style="margin-top:16px">Before you deposit or withdraw, please review: ${legalLinks(['terms','disclosures','risk-disclosure','privacy'])}. Funds are credited only after verification, and transfers sent to the wrong address or network may be unrecoverable.</p>`)}})}      // applied after extensions (crypto.js replaces R.deposit)

/* ---------- router ---------- */
async function route(){
 const parts=(location.hash||'#/dashboard').slice(2).split('?')[0].split('/');const h=parts[0]||'dashboard';const k=R[h]?h:'dashboard';setActive(k);
 if(!$('#view')){shell();setActive(k)}document.title='VAULTA — '+label(k);renderHero(k);if(window.VNav)VNav.render($('#vbar'));R[k](k===h?parts.slice(1):[]);view().focus({preventScroll:true})}
(async()=>{
 try{ME=await V.session()}catch(e){document.body.innerHTML=errBox(e.message,false);return}
 if(!ME){location.href='/login/';return}
 (window.VAULTA_EXT||[]).forEach(f=>f({R,load,head,kpi,V,UI}));
 if(window.VNav)VNav.configure({home:'#/dashboard',titleOf:k=>label(String(k).split('/')[0])});
 wrapDeposit();wrapRisk();
 shell();addEventListener('hashchange',()=>route());route();
 setInterval(async()=>{try{const j=await V.get('/api/notifications');UNREAD=j.unread;badge_()}catch(e){}},60000)})();
})();
/* unread support replies badge (server-derived; polls slowly and only while the tab is visible) */
(()=>{const f=async()=>{if(document.hidden||!VApi.state.user)return;try{const j=await VApi.get('/api/support/unread'),b=document.getElementById('sbadge');if(b){b.textContent=j.unread_messages;b.hidden=!j.unread_messages}}catch{}};setInterval(f,60000);setTimeout(f,3000)})();
