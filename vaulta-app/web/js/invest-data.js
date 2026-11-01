/* VAULTA invest data layer: API-backed. Products, balances and holdings all come from the server.
   Nothing financial is stored in the browser. */
const $=(s,r=document)=>r.querySelector(s),$$=(s,r=document)=>[...r.querySelectorAll(s)];
const fmt=n=>'$'+Number(n).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
const signed=n=>(n<0?'−':'+')+fmt(Math.abs(n)),pct=n=>(n<0?'−':'+')+Math.abs(n).toFixed(2)+'%';
const reduce=matchMedia('(prefers-reduced-motion:reduce)').matches;
const ser=(k,r)=>Array.from({length:12},(_,i)=>+(100+r*i/11+Math.sin(i*k)*Math.abs(r)*.12).toFixed(2));
let DATA=[];
const S={user:null,balance:0,holdings:[],txns:[],dash:null,twofa:false};
const COL=['#0A0A0A','#3d3d3d','#6b6b6b','#979797','#bdbdbd','#dedede'];
const mapProduct=p=>({id:p.slug,pid:p.id,name:p.name,type:p.category,kind:p.kind||p.category,issuer:p.issuer||'',risk:p.risk,min:+p.min_investment,ret:+p.return_pct,hor:p.horizon||'Medium',fee:+p.fee_bps/10000,added:p.added,score:p.score,blurb:p.description||'',illus:p.return_basis==='illustrative',series:p.series.length>2?p.series:ser(p.slug.length,+p.return_pct)});
const totals=()=>{const d=S.dash&&S.dash.summary;if(!d)return{invested:0,value:0,ret:0,pct:0};return{invested:+d.total_invested,value:+d.holdings_value,ret:+d.total_returns,pct:+d.total_returns_pct}};
const alloc=()=>{const a=(S.dash&&S.dash.allocation||[]).filter(x=>x.label!=='Savings');return a.map(x=>({k:x.label,v:+x.value,p:+x.pct}))};
const allocBar=a=>a.map((x,i)=>`<i style="flex:${x.p};background:${COL[i%6]}" title="${x.k}"></i>`).join('');
const allocKey=a=>a.map((x,i)=>`<li><i style="background:${COL[i%6]}"></i>${x.k}<b>${x.p.toFixed(1)}%</b></li>`).join('');
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function refresh(){
 const u=await VApi.session();S.user=u?{name:u.first_name,email:u.email}:null;
 if(u){S.twofa=u.two_factor_enabled;const d=await VApi.get('/api/dashboard');S.dash=d;S.balance=+d.summary.available_cash;
  S.txns=d.recent.filter(t=>t.type==='investment'||t.type==='trade').map(t=>({id:t.ref,name:(t.metadata&&(t.metadata.product||t.metadata.asset))||t.type,amount:+t.amount,date:t.created_at.slice(0,10),status:t.status[0].toUpperCase()+t.status.slice(1)}))}
 else{S.dash=null;S.balance=0;S.txns=[]}}
const api={
 async list(){const j=await VApi.get('/api/products');DATA=j.products.map(mapProduct);return DATA},
 async get(id){return DATA.find(d=>d.id===id)},
 /* Real order: the server validates funds, minimums, 2FA and creates the ledger entry. The UI shows the status the server returns. */
 async order(d,amount){
  const idem=VApi.uuid();let code;
  for(let i=0;i<2;i++){
   try{const r=await VApi.post('/api/investments',{product_id:d.pid,amount:amount.toFixed(2),accepted_disclosures:true,code},{idem});const v=r.investment;await refresh();
    return{id:v.transaction_ref,name:v.product,amount:+v.amount,fee:+v.fee,date:(v.completed_at||v.created_at).slice(0,10),status:v.status[0].toUpperCase()+v.status.slice(1)}}
   catch(e){if(e.code==='two_factor_required'&&i===0){code=prompt('Enter the 6-digit code from your authenticator app');if(code)continue}throw e}}},
};
function initNav(){const nav=$('#nav'),menu=$('#mobileMenu'),mb=$('#menuBtn'),on=()=>nav.classList.toggle('scrolled',scrollY>60);
 addEventListener('scroll',on,{passive:true});on();
 const set=o=>{menu.classList.toggle('open',o);mb.classList.toggle('open',o);mb.setAttribute('aria-expanded',o);document.body.style.overflow=o?'hidden':''};
 mb.onclick=()=>set(!menu.classList.contains('open'));menu.onclick=e=>e.target.closest('a')&&set(false)}
const ready=refresh().catch(()=>{});
