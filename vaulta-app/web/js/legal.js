/* Legal document viewer: loads the single published version from /api/legal (server-rendered, sanitised HTML). */
(()=>{
const $=(s,r=document)=>r.querySelector(s),$$=(s,r=document)=>[...r.querySelectorAll(s)];
const main=$('.lg-main');if(!main)return;
const q=new URLSearchParams(location.search);
const slug=main.dataset.doc||q.get('doc')||'',ver=q.get('version');
const body=$('#lgBody'),meta=$('#lgMeta'),toc=$('#lgToc'),title=$('#lgTitle');
const fmt=t=>t?new Date(t).toLocaleDateString('en-GB',{day:'numeric',month:'long',year:'numeric',timeZone:'UTC'}):'';
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function fail(msg){body.innerHTML='<p class="lg-error" role="alert">'+esc(msg)+'</p>';if(meta)meta.textContent='';if(toc)toc.innerHTML=''}
async function load(){
 if(!/^[a-z0-9][a-z0-9-]{1,59}$/.test(slug))return fail('This document could not be found.');
 let r;try{r=await fetch('/api/legal/documents/'+encodeURIComponent(slug)+(/^\d+$/.test(ver||'')?'?version='+ver:''),{headers:{Accept:'application/json'}})}catch(e){return fail('The document could not be loaded. Please check your connection and try again.')}
 if(!r.ok)return fail(r.status===404?'This document could not be found.':'The document could not be loaded. Please try again.');
 const d=(await r.json()).document;
 title.textContent=d.title;document.title='VAULTA — '+d.title;
 meta.textContent='Version '+d.version+(d.effective_at?' · Effective '+fmt(d.effective_at):'')+(d.published_at?' · Last updated '+fmt(d.published_at):'');
 let notes='';
 if(!d.is_current)notes+='<div class="lg-note" role="note"><strong>Earlier version.</strong> This version has been superseded. <a href="'+location.pathname+'">Read the current version</a>.</div>';
 if(d.needs_completion)notes+='<div class="lg-note lg-note--warn" role="note"><strong>Not final.</strong> Details highlighted in this document have not yet been supplied by the operator, and the text is awaiting legal review. It must not be relied on as a complete legal document.</div>';
 body.innerHTML=notes+d.html;
 toc.innerHTML=d.toc.map(t=>'<li><a href="#'+esc(t.id)+'">'+esc(t.text)+'</a></li>').join('');
 if(slug==='cookies'){const p=document.createElement('p');p.className='lg-actions';p.id='preferences';p.innerHTML='<button type="button" class="btn lg-btn" data-cookie-settings>Manage cookie preferences</button>';body.appendChild(p)}
 if(location.hash){const t=document.getElementById(decodeURIComponent(location.hash.slice(1)));t&&t.scrollIntoView()}
 spy();
}
function spy(){const links=$$('#lgToc a');if(!('IntersectionObserver' in window)||!links.length)return;
 const map=new Map(links.map(a=>[a.getAttribute('href').slice(1),a]));
 const io=new IntersectionObserver(es=>{es.forEach(e=>{if(e.isIntersecting){links.forEach(a=>a.removeAttribute('aria-current'));const a=map.get(e.target.id);a&&a.setAttribute('aria-current','true')}})},{rootMargin:'0px 0px -75% 0px'});
 $$('#lgBody h2[id]').forEach(h=>io.observe(h))}
$('#lgPrint')&&$('#lgPrint').addEventListener('click',()=>window.print());
(async()=>{const back=$('#lgBack');try{const r=await fetch('/api/auth/me');if(r.ok){const j=await r.json();back.href=(j.user.permissions&&j.user.permissions.length)?'/admin/':'/app/';back.textContent='Back to dashboard'}}catch(e){}})();
load();
})();
