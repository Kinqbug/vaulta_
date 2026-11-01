/* Contact page only. Validation (incl. international phone), submit state, social links.
   Without data-endpoint on the form, submission falls through to the site-wide demo handler in site.js
   (which states plainly that nothing is sent). Set data-endpoint to a real URL to send JSON via POST. */
(()=>{
const SOCIAL={x:'',facebook:'',instagram:'',linkedin:'',youtube:'',telegram:''}; /* add verified official profile URLs here */
document.querySelectorAll('.ct-soc').forEach(a=>{const u=SOCIAL[a.dataset.social];
 if(u){a.href=u;a.target='_blank';a.rel='noopener noreferrer'}else{a.setAttribute('aria-disabled','true');a.title='Profile link not configured yet'}});
const cta=document.querySelector('[data-scroll-form]');
if(cta)cta.addEventListener('click',()=>{setTimeout(()=>{const t=document.querySelector('#fn');t&&t.focus({preventScroll:true})},450)});     // after the anchor scroll, move focus into the form
const f=document.querySelector('form.ct-form');if(!f)return;
const note=f.querySelector('.fmsg'),btn=f.querySelector('.ct-submit'),label=btn.querySelector('span');let busy=false;
const phoneOk=v=>{const d=v.replace(/\D/g,'');return /^\+?[\d\s().-]+$/.test(v)&&d.length>=7&&d.length<=15};
const rules={fn:v=>!v?'This field is required.':'',
 em2:v=>!v?'This field is required.':!/^\S+@\S+\.\S+$/.test(v)?'Enter a valid email address.':'',
 ph:v=>!v?'This field is required.':!phoneOk(v)?'Enter a valid phone number.':'',
 ms:v=>!v?'This field is required.':v.length<10?'Enter at least 10 characters.':''};
const check=id=>{const el=f.querySelector('#'+id),m=rules[id](el.value.trim());f.querySelector('#e-'+id).textContent=m;el.setAttribute('aria-invalid',!!m);return m?el:null};
Object.keys(rules).forEach(id=>f.querySelector('#'+id).addEventListener('blur',e=>{if(e.target.value.trim()||e.target.getAttribute('aria-invalid')==='true')check(id)}));
f.addEventListener('submit',async e=>{
 if(busy){e.preventDefault();e.stopImmediatePropagation();return}
 const bad=Object.keys(rules).map(check).find(Boolean);
 if(bad){e.preventDefault();e.stopImmediatePropagation();bad.focus();note.textContent='Please fix the highlighted fields.';return}
 const url=f.dataset.endpoint;if(!url)return; /* demo fallback handled by site.js */
 e.preventDefault();e.stopImmediatePropagation();busy=true;btn.disabled=true;btn.setAttribute('aria-busy','true');label.textContent='SENDING';note.textContent='';
 try{const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({firstName:f.fn.value.trim(),email:f.em2.value.trim(),phone:f.ph.value.trim(),message:f.ms.value.trim()})});
  if(!r.ok)throw new Error(r.status);note.textContent='Thank you. Your message has been sent.';f.reset()}
 catch(x){note.textContent='Sorry, we could not send your message. Please try again.'}
 finally{busy=false;btn.disabled=false;btn.removeAttribute('aria-busy');label.textContent='SUBMIT'}
},true);
})();
