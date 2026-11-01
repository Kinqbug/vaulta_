/* VAULTA cookie consent.
   - Optional categories (preferences, analytics, marketing) are OFF until the visitor opts in; rejecting is as easy as accepting.
   - Optional scripts must be declared as  <script type="text/plain" data-consent="analytics" data-src="..."></script>  (or registered
     through VaultaConsent.run('analytics', fn)). They are never executed before consent and are not re-run after withdrawal.
   - The choice is kept in a first-party cookie (vaulta_consent, 180 days) and logged minimally on the server (random id, choices, policy version).
   Currently the site loads NO optional technologies: its only storage is the sign-in session cookie and demo/UI state, which are strictly necessary. */
(()=>{
const COOKIE='vaulta_consent',VERSION=1,CATS=['preferences','analytics','marketing'],DAYS=180;
const $=(s,r=document)=>r.querySelector(s),$$=(s,r=document)=>[...r.querySelectorAll(s)];
const readCookie=()=>{const m=document.cookie.split('; ').find(c=>c.startsWith(COOKIE+'='));if(!m)return null;try{const v=JSON.parse(decodeURIComponent(m.slice(COOKIE.length+1)));return v&&v.v===VERSION&&v.p?v:null}catch(e){return null}};
const writeCookie=v=>{document.cookie=COOKIE+'='+encodeURIComponent(JSON.stringify(v))+'; Max-Age='+DAYS*86400+'; Path=/; SameSite=Lax'+(location.protocol==='https:'?'; Secure':'')};
let state=readCookie(),active=new Set(),listeners=[],cleanups={},lastFocus=null;
const allowed=c=>!!(state&&state.p&&state.p[c]===true);
function activate(){
 $$('script[type="text/plain"][data-consent]').forEach(el=>{const c=el.dataset.consent;if(!allowed(c)||el.dataset.done)return;el.dataset.done='1';
  const s=document.createElement('script');[...el.attributes].forEach(a=>{if(!['type','data-consent','data-src','data-done'].includes(a.name))s.setAttribute(a.name,a.value)});
  if(el.dataset.src)s.src=el.dataset.src;else s.text=el.text;document.head.appendChild(s);active.add(c)})}
function save(action,p){
 const prev=state&&state.p||{};const clean={};CATS.forEach(c=>clean[c]=!!p[c]);
 const rec={v:VERSION,id:state&&state.id||null,ts:new Date().toISOString(),p:clean};state=rec;writeCookie(rec);
 fetch('/api/legal/cookie-consent',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({consent_id:rec.id||undefined,preferences:clean,action})})
  .then(r=>r.ok?r.json():null).then(j=>{if(j&&j.consent_id&&rec.id!==j.consent_id){rec.id=j.consent_id;state=rec;writeCookie(rec)}}).catch(()=>{});
 const withdrawn=CATS.filter(c=>prev[c]&&!clean[c]);
 withdrawn.forEach(c=>(cleanups[c]||[]).forEach(f=>{try{f()}catch(e){}}));
 listeners.forEach(f=>{try{f(clean)}catch(e){}});activate();
 if(withdrawn.some(c=>active.has(c)))location.reload();      // scripts that already ran can only be fully stopped by a fresh page load
}
/* ---------- UI ---------- */
const INFO={preferences:['Preferences','Remember choices such as display settings. Not used by VAULTA at present.'],analytics:['Analytics','Help us understand how the site is used and fix errors. Not used by VAULTA at present.'],marketing:['Marketing','Measure campaigns or show relevant promotions. Not used by VAULTA at present.']};
function banner(){
 if($('.vc-banner'))return;const b=document.createElement('div');b.className='vc-banner';b.setAttribute('role','region');b.setAttribute('aria-label','Cookie consent');
 b.innerHTML='<div class="vc-text"><strong>Cookies on VAULTA</strong><p>We use cookies that are strictly necessary to sign you in and keep the site secure. Optional cookies stay off unless you choose them. See our <a href="/legal/cookies/">Cookie Policy</a>.</p></div><div class="vc-actions"><button type="button" class="vc-btn" data-a="accept">Accept optional cookies</button><button type="button" class="vc-btn" data-a="reject">Reject optional cookies</button><button type="button" class="vc-btn" data-a="manage">Manage preferences</button></div>';
 document.body.appendChild(b);
 b.addEventListener('click',e=>{const a=e.target.dataset&&e.target.dataset.a;if(!a)return;
  if(a==='accept'){save('accept_all',{preferences:1,analytics:1,marketing:1});b.remove()}
  else if(a==='reject'){save('reject_optional',{});b.remove()}
  else openPrefs()})}
function openPrefs(){
 lastFocus=document.activeElement;const cur=state&&state.p||{};$('.vc-modal')&&$('.vc-modal').remove();
 const m=document.createElement('div');m.className='vc-modal';
 m.innerHTML='<div class="vc-dialog" role="dialog" aria-modal="true" aria-labelledby="vcT" tabindex="-1"><h2 id="vcT">Cookie preferences</h2><p>Choose which optional cookies VAULTA may use. You can change or withdraw your choice at any time. Details are in the <a href="/legal/cookies/">Cookie Policy</a>.</p>'+
 '<div class="vc-cat"><div><strong>Strictly necessary</strong><p>Sign-in session, security and fraud prevention, and remembering this choice. Always on.</p></div><span class="vc-on">Always on</span></div>'+
 CATS.map(c=>'<div class="vc-cat"><div><strong id="vl-'+c+'">'+INFO[c][0]+'</strong><p>'+INFO[c][1]+'</p></div><label class="vc-sw"><input type="checkbox" data-c="'+c+'" aria-labelledby="vl-'+c+'"'+(cur[c]?' checked':'')+'><span></span></label></div>').join('')+
 '<div class="vc-actions"><button type="button" class="vc-btn" data-a="save">Save preferences</button><button type="button" class="vc-btn" data-a="accept">Accept all optional</button><button type="button" class="vc-btn" data-a="reject">Reject all optional</button></div><button type="button" class="vc-x" data-a="close" aria-label="Close">×</button></div>';
 document.body.appendChild(m);const d=$('.vc-dialog',m);d.focus();
 const close=()=>{m.remove();document.removeEventListener('keydown',key);lastFocus&&lastFocus.focus&&lastFocus.focus()};
 const key=e=>{if(e.key==='Escape')close();if(e.key==='Tab'){const f=$$('button,input,a[href]',d).filter(x=>!x.disabled);if(!f.length)return;const i=f.indexOf(document.activeElement);if(e.shiftKey&&(i<=0)){f[f.length-1].focus();e.preventDefault()}else if(!e.shiftKey&&i===f.length-1){f[0].focus();e.preventDefault()}}};
 document.addEventListener('keydown',key);
 m.addEventListener('click',e=>{if(e.target===m)return close();const a=e.target.dataset&&e.target.dataset.a;if(!a)return;
  if(a==='close')return close();
  const b=$('.vc-banner');
  if(a==='save'){const p={};$$('input[data-c]',m).forEach(i=>p[i.dataset.c]=i.checked);save('custom',p)}
  if(a==='accept')save('accept_all',{preferences:1,analytics:1,marketing:1});
  if(a==='reject')save(state&&Object.values(state.p).some(Boolean)?'withdraw':'reject_optional',{});
  b&&b.remove();close()})}
window.VaultaConsent={get:()=>state?{...state.p}:null,has:allowed,open:openPrefs,onChange:f=>listeners.push(f),
 /* run(category, fn): fn executes only after the category is consented (immediately if already granted); cleanup(category, fn) runs on withdrawal */
 run:(c,f)=>{if(allowed(c))f();else listeners.push(()=>{if(allowed(c)&&!f.done){f.done=1;f()}})},cleanup:(c,f)=>{(cleanups[c]=cleanups[c]||[]).push(f)}};
document.addEventListener('click',e=>{const t=e.target.closest&&e.target.closest('[data-cookie-settings]');if(t){e.preventDefault();openPrefs()}});
function footerLink(){const h=$$('.footer h4').find(x=>x.textContent.trim()==='Legal');if(h&&!$('[data-cookie-settings]',h.parentElement)){const a=document.createElement('a');a.href='#';a.dataset.cookieSettings='';a.textContent='Cookie settings';h.parentElement.appendChild(a)}}
function init(){footerLink();activate();if(!state)banner()}
document.readyState==='loading'?document.addEventListener('DOMContentLoaded',init):init();
})();
