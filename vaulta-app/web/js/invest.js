const CATS=[['Stocks','↗','Growth-oriented investments in publicly traded companies.','Moderate–High'],['ETFs','◫','Diversified funds designed for long-term growth.','Moderate'],['Bonds','▤','Fixed-income opportunities aimed at steadier returns.','Low'],['Crypto','₿','Access to selected digital assets.','High'],['Real Estate','⌂','Property-backed investment opportunities.','Moderate'],['Commodities','◆','Exposure to assets such as gold and other commodities.','Moderate']];
const RK={Low:0,Moderate:1,High:2},HOR={Short:'Short term',Medium:'Medium term',Long:'Long term'};
const sorters={rec:(a,b)=>b.score-a.score,perf:(a,b)=>b.ret-a.ret,risk:(a,b)=>RK[a.risk]-RK[b.risk],min:(a,b)=>a.min-b.min,new:(a,b)=>b.added.localeCompare(a.added)};
const RISKTXT='Investing involves risk, including the possible loss of principal. Past performance does not guarantee future results.';
const io=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){e.target.classList.add('in');io.unobserve(e.target)}}),{threshold:.1});
const reveal=()=>$$('.rise:not(.in)').forEach(e=>io.observe(e));
const perfLabel=d=>d.illus?'Illustrative return':'Historical performance (1Y)';
const spark=(s,w=200,h=60)=>{const mn=Math.min(...s),mx=Math.max(...s),p=s.map((v,i)=>[i/(s.length-1)*w,h-4-(v-mn)/(mx-mn||1)*(h-8)]),
 l=p.map((q,i)=>(i?'L':'M')+q[0].toFixed(1)+' '+q[1].toFixed(1)).join(''),up=s[s.length-1]>=s[0];
 return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Performance chart, demo data"><path d="${l}L${w} ${h}L0 ${h}Z" fill="${up?'rgba(32,178,107,.12)':'rgba(214,69,69,.1)'}" stroke="none"/><path class="ln" d="${l}" stroke="${up?'#20B26B':'#d64545'}" pathLength="1"/></svg>`};
function count(el,to,f){if(reduce){el.textContent=f(to);return}const t0=performance.now();(function s(t){const k=Math.min(1,(t-t0)/900);el.textContent=f(to*(1-Math.pow(1-k,3)));if(k<1)requestAnimationFrame(s)})(t0)}

/* summary + portfolio preview */
function renderSummary(first){const t=totals(),a=alloc(),set=(id,v,f)=>{const e=$('#'+id);first?count(e,v,f):e.textContent=f(v)};
 set('sInv',t.invested,fmt);set('sVal',t.value,fmt);set('sRet',t.ret,signed);set('sPct',t.pct,pct);
 set('pVal',t.value,fmt);set('pInv',t.invested,fmt);set('pRet',t.ret,signed);set('hVal',t.value,fmt);
 $('#hPct').textContent=pct(t.pct)+' overall';$('#hBar').innerHTML=allocBar(a);
 $('#allocBar').innerHTML=allocBar(a);$('#allocKey').innerHTML=allocKey(a);
 $('#recent').innerHTML=S.txns.slice(0,5).map(t=>`<li><span>${t.name}<small>${t.id} · ${t.date}</small></span><b>${fmt(t.amount)}</b><em class="st">${t.status}</em></li>`).join('');
 $('#pBal').textContent=S.user?fmt(S.balance):'Sign in to see'}

/* categories */
function drawCats(){$('#cats').innerHTML=CATS.map(([n,ic,desc,risk])=>{const r=DATA.filter(d=>d.type===n).map(d=>d.ret),lo=Math.min(...r),hi=Math.max(...r);
 return `<article class="card iv-cat rise"><span class="ic" aria-hidden="true">${ic}</span><h3>${n}</h3><p>${desc}</p><dl class="facts"><div><dt>Risk</dt><dd>${risk}</dd></div><div><dt>Illustrative range</dt><dd>${lo===hi?lo:lo+'–'+hi}%</dd></div></dl><button class="btn ghost" data-cat="${n}">Explore ${n}</button></article>`}).join('');}

/* marketplace */
let LIST=[],F={q:'',type:'All',risk:'All',hor:'Any',sort:'rec'};
const card=d=>`<article class="card iv-card rise"><div class="iv-top"><span class="tag">${d.kind}</span><span class="risk r${d.risk[0]}">${d.risk} risk</span></div><h3>${d.name}</h3><small>${d.issuer}</small><p>${d.blurb}</p>${spark(d.series)}
<dl class="facts"><div><dt>${perfLabel(d)}</dt><dd class="up">${pct(d.ret)}</dd></div><div><dt>Minimum</dt><dd>${fmt(d.min).replace('.00','')}</dd></div><div><dt>Horizon</dt><dd>${HOR[d.hor]}</dd></div></dl>
<div class="row"><button class="btn ghost" data-a="view" data-id="${d.id}">View Details</button><button class="btn" data-a="invest" data-id="${d.id}">Invest Now</button></div></article>`;
function render(){const q=F.q.trim().toLowerCase(),r=LIST.filter(d=>(F.type==='All'||d.type===F.type)&&(F.risk==='All'||d.risk===F.risk)&&(F.hor==='Any'||d.hor===F.hor)&&(!q||[d.name,d.kind,d.type,d.issuer,d.blurb].join(' ').toLowerCase().includes(q))).sort(sorters[F.sort]);
 $('#count').textContent=r.length+' investment'+(r.length===1?'':'s');$('#grid').innerHTML=r.map(card).join('');$('#grid').removeAttribute('aria-busy');$('#empty').hidden=!!r.length;reveal()}
async function load(){$('#err').hidden=true;$('#empty').hidden=true;$('#grid').setAttribute('aria-busy','true');$('#grid').innerHTML='<div class="sk"></div>'.repeat(6);
 try{LIST=await api.list();render()}catch(e){$('#grid').innerHTML='';$('#grid').removeAttribute('aria-busy');$('#count').textContent='';$('#err').hidden=false}}
const bind=(id,k)=>$('#'+id).addEventListener('input',e=>{F[k]=e.target.value;render()});
[['fq','q'],['ftype','type'],['frisk','risk'],['fhor','hor'],['fsort','sort']].forEach(a=>bind(...a));
const clear=()=>{F={q:'',type:'All',risk:'All',hor:'Any',sort:'rec'};$('#fq').value='';$('#ftype').value='All';$('#frisk').value='All';$('#fhor').value='Any';$('#fsort').value='rec';render()};
$('#clear').onclick=clear;$('#retry').onclick=load;
$('#ft').onclick=e=>{const o=$('#filters').classList.toggle('open');e.currentTarget.setAttribute('aria-expanded',o)};
document.addEventListener('click',e=>{const c=e.target.closest('[data-cat]');if(!c)return;F.type=c.dataset.cat;$('#ftype').value=F.type;render();$('#explore').scrollIntoView({behavior:reduce?'auto':'smooth'})});

/* dialogs */
document.addEventListener('click',e=>{if(e.target.closest('[data-close]'))e.target.closest('dialog').close()});
$$('dialog').forEach(d=>d.addEventListener('click',e=>{if(e.target===d)d.close()}));
let cur=null,amt=0,pending=null;
$('#grid').onclick=async e=>{const b=e.target.closest('[data-a]');if(!b)return;const d=await api.get(b.dataset.id);b.dataset.a==='view'?openDetail(d):startFlow(d,d.min)};
function openDetail(d){cur=d;const r3=+(d.ret*.85).toFixed(1);
 $('#dBody').innerHTML=`<small>${d.kind} · ${d.issuer}</small><h2 id="dT">${d.name}</h2><p>${d.blurb}</p>${spark(d.series,400,140)}<small>Demo performance chart. ${d.illus?'Illustrative only.':'Historical performance.'}</small>
<dl class="facts"><div><dt>Risk level</dt><dd><span class="risk r${d.risk[0]}">${d.risk}</span></dd></div><div><dt>Minimum investment</dt><dd>${fmt(d.min)}</dd></div><div><dt>Investment horizon</dt><dd>${HOR[d.hor]}</dd></div><div><dt>Fees</dt><dd>${(d.fee*100).toFixed(2)}% per order</dd></div><div><dt>${perfLabel(d)}</dt><dd class="up">${pct(d.ret)}</dd></div><div><dt>${d.illus?'Illustrative return, 3Y a year':'Historical, 3Y a year'}</dt><dd class="up">${pct(r3)}</dd></div></dl>
<h4>Key information</h4><ul class="klist"><li>Asset type: ${d.type}</li><li>Provider: ${d.issuer}</li><li>Orders are processed after submission, not instantly.</li></ul>
<p class="iv-disc"><b>Risk disclosure.</b> ${RISKTXT}</p>
<label for="dAmt">Investment amount</label><input id="dAmt" class="iv-in" inputmode="decimal" value="${d.min}"><button class="btn" id="dGo">Invest Now</button>`;
 $('#detail').showModal();$('#dGo').onclick=()=>startFlow(d,parseFloat($('#dAmt').value.replace(/,/g,''))||d.min)}

/* invest flow */
const fee=()=>+(amt*cur.fee).toFixed(2),total=()=>+(amt+fee()).toFixed(2);
function step(n){$$('#flow [data-step]').forEach(s=>s.hidden=+s.dataset.step!==n);const h=$(`#flow [data-step="${n}"] h2`);h&&h.focus()}
function startFlow(d,a){if(!S.user){pending=[d,a];$('#detail').close();$('#auth').showModal();return}
 cur=d;amt=a;$('#detail').close();$('#fName').textContent=d.name+' · minimum '+fmt(d.min);$('#amt').value=a;$('#ack').checked=false;$('#rNext').disabled=true;$('#cMsg').textContent='';validate();step(1);$('#flow').showModal()}
function validate(){let e='';const f=$('#aFund');f.hidden=true;
 if(!(amt>0))e='Enter an amount to invest.';else if(amt<cur.min)e='Minimum investment is '+fmt(cur.min)+'.';else if(total()>S.balance){e="Your available balance isn't enough for this investment.";f.hidden=false}
 $('#aMsg').textContent=e;$('#aNext').disabled=!!e;$('#aAmt').textContent=fmt(amt||0);$('#aBal').textContent=fmt(S.balance);
 $$('#chips button').forEach(b=>b.classList.toggle('on',+b.dataset.v===amt))}
$('#amt').oninput=e=>{amt=parseFloat(e.target.value.replace(/,/g,''))||0;validate()};
$('#chips').onclick=e=>{const b=e.target.closest('button');if(!b)return;if(+b.dataset.v){amt=+b.dataset.v;$('#amt').value=amt;validate()}else $('#amt').focus()};
$('#aFund').onclick=()=>{location.href='/app/#/deposit'};
const rows=x=>x.map(([k,v])=>`<div><dt>${k}</dt><dd>${v}</dd></div>`).join('');
const base=()=>[['Investment',cur.name],['Amount',fmt(amt)],['Fees',fmt(fee())],['Total',`<b>${fmt(total())}</b>`]];
$('#aNext').onclick=()=>{$('#rRows').innerHTML=rows(base());step(2)};
$('#ack').onchange=e=>$('#rNext').disabled=!e.target.checked;
$('#rNext').onclick=()=>{$('#cRows').innerHTML=rows([...base().slice(0,3),['Payment source','VAULTA cash balance ('+fmt(S.balance)+')'],base()[3]]);step(3)};
$$('[data-back]').forEach(b=>b.onclick=()=>step(+b.closest('[data-step]').dataset.step-1));
$('#cGo').onclick=async e=>{const b=e.currentTarget;b.disabled=true;b.textContent='Submitting…';$('#cMsg').textContent='';
 try{const t=await api.order(cur,amt);$('#sRows').innerHTML=rows([['Transaction ID',t.id],['Investment',t.name],['Amount',fmt(t.amount)],['Date',t.date],['Status',t.status]]);renderSummary();step(4)}
 catch(err){$('#cMsg').textContent=err.message}
 b.disabled=false;b.textContent='Confirm Investment'};

/* demo auth */

ready.then(()=>{drawCats();renderSummary(true);$('#heroChart').innerHTML=spark(ser(5,14.4),400,120);reveal();load()});
