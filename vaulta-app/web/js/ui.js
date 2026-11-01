/* Shared UI kit for the user and admin dashboards: DOM helpers, formatting, charts (inline SVG), modal, toast, tables. */
(()=>{
const $=(s,r=document)=>r.querySelector(s),$$=(s,r=document)=>[...r.querySelectorAll(s)];
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const usd=n=>(n==null||n==='')?'—':(Number(n)<0?'−$':'$')+Math.abs(Number(n)).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
const sgn=n=>(Number(n)>=0?'+':'−')+'$'+Math.abs(Number(n)).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
const pc=n=>(Number(n)>=0?'+':'−')+Math.abs(Number(n)).toFixed(2)+'%';
const dt=s=>s?new Date(s).toLocaleString(undefined,{dateStyle:'medium',timeStyle:'short'}):'—';
const day=s=>s?new Date(s).toLocaleDateString(undefined,{dateStyle:'medium'}):'—';
const cls=n=>Number(n)>=0?'pos':'neg';
const label=s=>String(s||'').replace(/_/g,' ').replace(/^\w/,c=>c.toUpperCase());
const badge=s=>`<span class="st st-${esc(s)}">${esc(label(s))}</span>`;
const COLORS=['#0A0A0A','#20B26B','#6b6b6b','#979797','#3d3d3d','#bdbdbd','#dedede'];

/* ---- charts ---- */
function line(vals,{w=640,h=200,color,fill=true,labels}={}){
 vals=vals.map(Number);if(vals.length<2)return'<div class="empty">Not enough data yet.</div>';
 const mn=Math.min(...vals),mx=Math.max(...vals),pad=(mx-mn)*.1||1,lo=mn-pad,hi=mx+pad;
 const X=i=>i/(vals.length-1)*w,Y=v=>h-(v-lo)/(hi-lo)*h;
 const p=vals.map((v,i)=>(i?'L':'M')+X(i).toFixed(1)+' '+Y(v).toFixed(1)).join('');
 const up=vals[vals.length-1]>=vals[0],c=color||(up?'#20B26B':'#d64545');
 return`<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Line chart"><path d="${p}L${w} ${h}L0 ${h}Z" fill="${fill?c:'none'}" opacity=".08"/><path d="${p}" fill="none" stroke="${c}" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linecap="round"/></svg>`}
function bars(series,{w=640,h=180,names}={}){ /* series: [[...],[...]] grouped */
 const n=series[0].length,all=series.flat().map(Number),mx=Math.max(...all,1),gw=w/n,bw=gw*.7/series.length;
 const r=series.map((s,k)=>s.map((v,i)=>{const bh=Number(v)/mx*(h-4);return`<rect x="${(i*gw+gw*.15+k*bw).toFixed(1)}" y="${(h-bh).toFixed(1)}" width="${Math.max(bw-1,1).toFixed(1)}" height="${bh.toFixed(1)}" rx="1.5" fill="${COLORS[k]}"/>`}).join('')).join('');
 const key=names?`<div class="key">${names.map((x,i)=>`<span><i style="background:${COLORS[i]}"></i>${esc(x)}</span>`).join('')}</div>`:'';
 return`<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Bar chart">${r}</svg>${key}`}
function donut(items){ /* [{label,value}] */
 const t=items.reduce((a,x)=>a+Number(x.value),0);if(!t)return'<div class="empty">Nothing to show yet.</div>';let o=0;
 const arcs=items.map((x,i)=>{const p=Number(x.value)/t*100,a=`<circle cx="21" cy="21" r="15.9" fill="none" stroke="${COLORS[i%7]}" stroke-width="5" stroke-dasharray="${Math.max(p-.5,.1)} ${100-p+.5}" stroke-dashoffset="${-o}"/>`;o+=p;return a}).join('');
 return`<div class="donut"><svg viewBox="0 0 42 42" role="img" aria-label="Allocation chart" style="transform:rotate(-90deg)">${arcs}</svg><ul class="key v">${items.map((x,i)=>`<li><i style="background:${COLORS[i%7]}"></i>${esc(x.label)}<b>${(Number(x.value)/t*100).toFixed(1)}%</b></li>`).join('')}</ul></div>`}

/* ---- toast ---- */
function toast(msg,kind='ok'){let t=$('#toasts');if(!t){t=document.createElement('div');t.id='toasts';t.setAttribute('aria-live','polite');document.body.append(t)}
 /* identical feedback for one action is shown once: a repeat refreshes the existing toast instead of stacking a copy */
 const dup=$$('.toast',t).find(x=>x.textContent===String(msg)&&x.classList.contains(kind));
 if(dup){clearTimeout(dup._t);dup._t=setTimeout(()=>dup.remove(),kind==='err'?7000:3800);return dup}
 const e=document.createElement('div');e.className='toast '+kind;e.textContent=msg;if(kind==='err')e.setAttribute('role','alert');
 t.append(e);while(t.children.length>4)t.firstElementChild.remove();e._t=setTimeout(()=>e.remove(),kind==='err'?7000:3800);return e}

/* ---- centralized dialog manager ----
   ONE implementation for every popup in the dashboards (details, forms, confirmations, sheets).
   - Dialogs live on a stack. Only the top dialog receives input: the dialog underneath is made `inert` while a child is open
     (managed confirmation layer), so a click can never reach a parent's handlers, and it is restored when the child closes.
   - `key` makes a dialog a singleton: a repeated click on the same control returns the open dialog instead of opening another.
   - Every dialog owns an AbortController; all of its listeners are removed and its DOM node deleted when it closes (Esc, Cancel,
     backdrop, ×, success, or closeAll() on navigation). Focus returns to the control that opened it.
   - Async actions run INSIDE the dialog (confirm({run}) / form({onSubmit})): buttons lock while the request is in flight, the
     dialog stays open on failure with the server's message, and closes only after the server confirms success. */
const DLG=(()=>{
 const stack=[];let uid=0;
 const top=()=>stack[stack.length-1];
 document.addEventListener('keydown',e=>{if(e.key==='Escape'){const t=top();if(t&&(t.busy||!t.dismissible)){e.preventDefault();e.stopPropagation()}}},true);   /* a dialog whose request is running (or that is not dismissible) ignores Escape, however often it is pressed */
 const entry=d=>stack.find(s=>s.el===d);
 const SZ={sm:' sm',md:'',lg:' wide',wide:' wide'};
 function open({title='',body='',size,wide,key,cls='',dismissible=true,onClose}={}){
  if(key){const ex=stack.find(s=>s.key===key);if(ex)return ex.el}
  const id='dlg'+(++uid),opener=document.activeElement instanceof HTMLElement&&document.activeElement!==document.body?document.activeElement:null;
  const d=document.createElement('dialog');d.className='dlg'+(SZ[size||(wide?'lg':'md')]||'')+(cls?' '+cls:'');d.setAttribute('aria-labelledby',id);
  d.innerHTML=`<div class="dlg-in"><header><h2 id="${id}">${esc(title)}</h2><button type="button" class="x" aria-label="Close" data-x>×</button></header><div class="dlg-body">${body}</div></div>`;
  const ac=new AbortController(),ent={el:d,key,ac,opener,dismissible,busy:false,onClose};
  const parent=top();if(parent){parent.el.inert=true;parent.el.classList.add('dlg-under')}
  stack.push(ent);document.body.append(d);
  ent.t=Date.now();
  /* a dialog underneath another one is inert: swallow synthetic/stray events before any handler the page attached to it can run */
  ['click','submit','change'].forEach(t=>d.addEventListener(t,e=>{if(d.inert){e.stopPropagation();e.preventDefault()}},{capture:true,signal:ac.signal}));
  d.addEventListener('click',e=>{if(top()!==ent||ent.busy)return;
   /* the 2nd click of a double-click lands on the backdrop of the dialog the 1st click just opened: ignore backdrop clicks for a moment */
   if(e.target===d){if(ent.dismissible&&Date.now()-ent.t>450)d.close();return}
   if(e.target.closest&&e.target.closest('[data-x]'))d.close()},{signal:ac.signal});
  d.addEventListener('cancel',e=>{if(ent.busy||!ent.dismissible)e.preventDefault()},{signal:ac.signal});   /* Esc: ignored while a request is running */
  let cleaned=false;const cleanup=()=>{if(cleaned)return;cleaned=true;if(ent.busy)ent.orphan=true;ac.abort();const i=stack.indexOf(ent),wasTop=i===stack.length-1;if(i>-1)stack.splice(i,1);d.remove();
   const t=top();if(wasTop&&t){t.el.inert=false;t.el.classList.remove('dlg-under')}
   const back=wasTop&&(opener&&opener.isConnected?opener:null);if(back){try{back.focus({preventScroll:true})}catch(e){}}
   if(!stack.length)document.body.classList.remove('dlg-open');if(ent.onClose)ent.onClose()};
  ent.cleanup=cleanup;d.addEventListener('close',cleanup,{once:true});
  document.body.classList.add('dlg-open');d.showModal();
  const f=d.querySelector('[autofocus],.dlg-body input:not([type=hidden]):not([disabled]),.dlg-body textarea,.dlg-body select');if(f)f.focus();
  return d}
 const close=d=>{if(d&&d.open)d.close();const e=d&&entry(d);if(e)e.cleanup()};
 const closeAll=()=>{[...stack].reverse().forEach(s=>{if(s.busy)s.orphan=true;s.busy=false;s.el.close();s.cleanup()})};   /* orphan: its request is still running; the outcome is reported by a notice */
 const tt=t=>String(t||'The action').replace(/\?$/,'');
 const orphanOk=(ent,t)=>{if(ent.orphan)toast(tt(t)+' — completed.')},orphanErr=(ent,t,e)=>{if(ent.orphan)toast(tt(t)+' failed: '+(e&&e.message||'something went wrong'),'err')};
 const setBusy=(d,on,btn,txt)=>{const e=entry(d);if(e)e.busy=on;d.querySelectorAll('button,input,select,textarea').forEach(el=>{if(on){el.dataset.was=el.disabled?'1':'';el.disabled=true}else{el.disabled=el.dataset.was==='1';delete el.dataset.was}});
  if(btn){if(on){btn._t=btn.textContent;btn.textContent=txt||'Working…'}else if(btn._t!=null)btn.textContent=btn._t}};
 const showErr=(d,e)=>{const p=d.querySelector('.dlg-err');if(p){p.textContent=e&&e.message||'Something went wrong';p.hidden=false}};
 const clearErr=d=>{const p=d.querySelector('.dlg-err');if(p){p.textContent='';p.hidden=true}};

 /* Confirmation. Legacy form: resolves true / reason text / null (cancelled). With {run}: the request executes inside the dialog and
    the promise resolves {value,result} only after it succeeds; failures are shown inline and the dialog stays open. */
 function confirm(title,message,{ok='Confirm',cancel='Cancel',reason=false,danger=false,key,run:action}={}){
  const k=key||'confirm:'+title+':'+message,ex=stack.find(s=>s.key===k);if(ex&&ex.promise)return ex.promise;
  let settle;const promise=new Promise(r=>settle=r);let done=false;const fin=v=>{if(!done){done=true;settle(v)}};
  const d=open({title,size:'sm',key:k,cls:'dlg-confirm',body:`<p class="dlg-msg">${esc(message)}</p>${reason?'<label class="f">Reason (required, recorded in the audit log)<textarea class="cr" rows="3" maxlength="500"></textarea></label>':''}<p class="neg dlg-err" role="alert" hidden></p><div class="actions"><button type="button" class="btn ghost" data-x>${esc(cancel)}</button><button type="button" class="btn ${danger?'danger':''}" data-ok>${esc(ok)}</button></div>`});
  const ent=entry(d);ent.promise=promise;const btn=d.querySelector('[data-ok]'),ta=d.querySelector('.cr');
  btn.addEventListener('click',async()=>{if(ent.busy)return;clearErr(d);let v=true;
   if(reason){v=ta.value.trim();if(v.length<5){showErr(d,{message:'Please enter a reason (at least 5 characters).'});ta.focus();return}}
   if(!action){fin(v);d.close();return}
   setBusy(d,true,btn);let res;
   try{res=await action(v)}catch(e){setBusy(d,false,btn);showErr(d,e);orphanErr(ent,title,e);return}
   setBusy(d,false,btn);orphanOk(ent,title);fin({value:v,result:res});d.close()},{signal:ent.ac.signal});
  d.addEventListener('close',()=>fin(null),{once:true});return promise}

 /* Form dialog. body = the form's fields; onSubmit(dataObject, form, dialog) may be async. Resolves the handler's result on success, null if dismissed. */
 function form({title,body='',size,submit='Save',cancel='Cancel',danger=false,key,onSubmit,onClose}={}){
  let settle;const promise=new Promise(r=>settle=r);let done=false;const fin=v=>{if(!done){done=true;settle(v)}};
  const d=open({title,size,key,onClose,cls:'dlg-formd',body:`<form class="dlg-form">${body}<p class="neg dlg-err" role="alert" hidden></p><div class="actions"><button type="button" class="btn ghost" data-x>${esc(cancel)}</button><button type="submit" class="btn ${danger?'danger':''}">${esc(submit)}</button></div></form>`});
  const ent=entry(d),f=d.querySelector('form'),btn=f.querySelector('[type=submit]');
  f.addEventListener('submit',async e=>{e.preventDefault();if(ent.busy)return;clearErr(d);if(!f.reportValidity())return;
   const data={};new FormData(f).forEach((v,k)=>{data[k]=v});setBusy(d,true,btn,'Saving…');let res;
   try{res=await onSubmit(data,f,d)}catch(err){setBusy(d,false,btn);showErr(d,err);orphanErr(ent,title,err);return}
   setBusy(d,false,btn);if(res!==false)orphanOk(ent,title);if(res===false)return;fin(res===undefined?true:res);d.close()},{signal:ent.ac.signal});
  d.addEventListener('close',()=>fin(null),{once:true});return promise}

 return{open,close,closeAll,confirm,form,details:o=>open({size:'lg',...o}),get depth(){return stack.length},get top(){const t=top();return t&&t.el}}})();
/* back-compatible helpers, now thin wrappers over the manager */
const modal=(title,html,o={})=>DLG.open({title,body:html,wide:o.wide,size:o.size,key:o.key});
const confirmBox=(title,message,o)=>DLG.confirm(title,message,o);
/* async button helper: disables while the request runs (re-entrancy safe) and always shows the server's real outcome */
async function run(btn,fn,okMsg){if(btn&&btn.dataset.busy)return undefined;const t=btn&&btn.textContent;if(btn){btn.dataset.busy='1';btn.disabled=true;btn.textContent='Working…'}
 try{const r=await fn();if(okMsg)toast(okMsg);return r}catch(e){toast(e.message||'Something went wrong','err');return undefined}finally{if(btn){delete btn.dataset.busy;btn.disabled=false;btn.textContent=t}}}

/* ---- tables & states ---- */
function table(cols,rows,{empty='Nothing here yet.',onRow}={}){
 if(!rows.length)return`<div class="empty">${esc(empty)}</div>`;
 return`<div class="tbl-wrap"><table class="tbl"><thead><tr>${cols.map(c=>`<th class="${c.cls||''}">${esc(c.h)}</th>`).join('')}</tr></thead><tbody>${rows.map((r,i)=>`<tr ${onRow?`data-i="${i}" tabindex="0" class="click"`:''}>${cols.map(c=>`<td class="${c.cls||''}" data-l="${esc(c.h)}">${c.r?c.r(r):esc(r[c.k])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`}
const skeleton=n=>'<div class="sk-wrap">'+'<div class="sk"></div>'.repeat(n||3)+'</div>';
const errBox=(msg,retry)=>`<div class="errbox" role="alert"><p>${esc(msg)}</p>${retry?'<button class="btn ghost" data-retry>Try again</button>':''}</div>`;
function pager(meta,go){const pages=Math.max(1,Math.ceil(meta.total/meta.size));return`<div class="pager"><span>${meta.total} result${meta.total===1?'':'s'}</span><div><button class="btn ghost sm" data-pg="${meta.page-1}" ${meta.page<=1?'disabled':''}>Previous</button> <span>Page ${meta.page} of ${pages}</span> <button class="btn ghost sm" data-pg="${meta.page+1}" ${meta.page>=pages?'disabled':''}>Next</button></div></div>`}
function formData(form){const o={};new FormData(form).forEach((v,k)=>{o[k]=v});return o}
/* One asset-logo component for the whole app. The image URL comes from the admin-managed backend record (a.logo); with no logo, or if it fails to load,
   a generated symbol badge is shown. Never hard-code a logo in a view. */
const hueOf=sym=>{let h=0;for(const c of String(sym||'?'))h=(h*31+c.charCodeAt(0))%360;return h};
const badge2=(sym,size)=>`<span class="alogo fb" aria-hidden="true" style="width:${size}px;height:${size}px;background:hsl(${hueOf(sym)},52%,36%);font-size:${Math.max(9,Math.round(size*.38))}px">${esc(String(sym||'?').slice(0,3))}</span>`;
function logo(a,size=28){const sym=(a&&a.symbol)||(a&&a.asset)||'?';return a&&a.logo?`<img class="alogo" src="${esc(a.logo)}" alt="" width="${size}" height="${size}" loading="lazy" decoding="async" style="width:${size}px;height:${size}px" data-sym="${esc(sym)}">`:badge2(sym,size)}
document.addEventListener('error',e=>{const t=e.target;if(t&&t.tagName==='IMG'&&t.classList.contains('alogo')){const w=document.createElement('span');w.innerHTML=badge2(t.dataset.sym,t.width||28);t.replaceWith(w.firstChild)}},true);
window.UI={logo,$,$$,esc,usd,sgn,pc,dt,day,cls,label,badge,line,bars,donut,toast,modal,confirmBox,run,table,skeleton,errBox,pager,formData,COLORS,
 dialog:DLG,openModal:DLG.open,openConfirm:DLG.confirm,openFormDialog:DLG.form,openDetailsDialog:DLG.details,closeModal:DLG.close,closeAllModals:DLG.closeAll};
})();
