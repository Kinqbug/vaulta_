(()=>{
const $=(s,r=document)=>r.querySelector(s),$$=(s,r=document)=>[...r.querySelectorAll(s)];
const ROOT=document.currentScript.src.replace(/js\/auth-pages\.js.*$/,'');
const sessionReady=VApi.session().catch(()=>null);   // picks up the CSRF token when a signed-in user opens an email link
const tokenFromUrl=()=>new URLSearchParams(location.search).get('token')||'';
let legalCache=null;
async function legalVersions(){if(legalCache)return legalCache;try{const r=await fetch('/api/legal/current?slugs=terms,privacy');if(!r.ok)return null;const d=(await r.json()).documents;legalCache={terms:d.terms.version,privacy:d.privacy.version};
 const t=$('#tv'),p=$('#pv');if(t)t.textContent=' (version '+d.terms.version+')';if(p)p.textContent=' (version '+d.privacy.version+')';return legalCache}catch(e){return null}}
if($('#sterms'))legalVersions();
function check(f){let bad=null;$$('input',f).forEach(el=>{if(el.type==='checkbox'&&el.name!=='terms'||el.hidden||el.closest('[hidden]'))return;let m='';const v=el.value;
 if(el.name==='terms')m=el.checked?'':'Please accept to continue.';
 else if(el.required&&!v.trim())m='This field is required.';else if(el.type==='email'&&v&&!/^\S+@\S+\.\S+$/.test(v.trim()))m='Enter a valid email address.';
 else if(el.dataset.min&&v.length<+el.dataset.min)m='Use at least '+el.dataset.min+' characters.';else if(el.dataset.match&&v!==$('#'+el.dataset.match).value)m='Passwords do not match.';
 const er=$('#e-'+el.id);if(er)er.textContent=m;el.setAttribute('aria-invalid',!!m);if(m&&!bad)bad=el});return bad}
$$('form[data-auth]').forEach(f=>{const kind=f.dataset.auth,note=$('.fmsg',f),btn=$('button[type=submit]',f);
 const busy=b=>{if(btn){btn.disabled=b;btn.dataset.t=btn.dataset.t||btn.textContent;btn.textContent=b?'Please wait…':btn.dataset.t}};
 if(kind==='verify'){(async()=>{await sessionReady;const t=tokenFromUrl();if(!t){note.textContent='This link is missing its token.';return}
  try{await VApi.post('/api/auth/verify-email',{token:t});note.textContent='Your email is verified. Thank you.'}catch(e){note.textContent=e.message}})();return}
 f.addEventListener('submit',async e=>{e.preventDefault();const bad=check(f);if(bad){bad.focus();note.textContent='Please fix the highlighted fields.';return}
  note.textContent='';busy(true);
  try{await sessionReady;
   if(kind==='login'){const code=$('#lcode');
    try{await VApi.login($('#lem').value.trim(),$('#lpw2').value,code&&!code.closest('[hidden]')?code.value:undefined)}
    catch(err){if(err.code==='two_factor_required'){$('#codeWrap').hidden=false;$('#lcode').focus();note.textContent=code&&code.value?err.message:'Enter the 6-digit code from your authenticator app.';busy(false);return}throw err}
    const u=VApi.state.user;note.textContent='Signed in. Redirecting…';location.href=ROOT+(u.permissions&&u.permissions.length?'admin/':'app/')}
   else if(kind==='signup'){const parts=$('#sn').value.trim().split(/\s+/),first=parts.shift(),last=parts.join(' ')||'-';
    const lv=await legalVersions();if(!lv){note.textContent='The legal documents could not be loaded, so we cannot create your account right now. Please try again.';busy(false);return}
    try{await VApi.register({email:$('#sem').value.trim(),password:$('#spw').value,first_name:first,last_name:last,accepted_terms:$('#sterms').checked,terms_version:lv.terms,privacy_version:lv.privacy})}
    catch(err){if(err.code==='legal_version_changed'){legalCache=null;await legalVersions();$('#sterms').checked=false;note.textContent=err.message;busy(false);return}throw err}
    note.textContent='Account created. Redirecting to your dashboard…';location.href=ROOT+'app/'}
   else if(kind==='forgot'){const j=await VApi.post('/api/auth/request-password-reset',{email:$('#fem').value.trim()});note.textContent=j.message}
   else if(kind==='reset'){await VApi.post('/api/auth/reset-password',{token:tokenFromUrl(),password:$('#rpw').value});note.textContent='Password updated. You can now log in.';setTimeout(()=>location.href=ROOT+'login/',1200)}
  }catch(err){note.textContent=err.message;if(err.code==='email_taken'||err.code==='weak_password'||err.code==='invalid_email'){const el=err.code==='email_taken'||err.code==='invalid_email'?$('#sem'):$('#spw');el&&el.focus()}}
  busy(false)})});
})();