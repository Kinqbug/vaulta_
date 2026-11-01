/* Public unsubscribe page. The link carries a signed token (no login needed). Text is set with textContent, never innerHTML. */
(()=>{
 const t=new URLSearchParams(location.search).get('t')||'',msg=document.getElementById('umsg'),btn=document.getElementById('ubtn');
 const fix=document.createElement('style');fix.textContent='[hidden]{display:none!important}';document.head.append(fix);
 const call=async(m,u,b)=>{const r=await fetch(u,{method:m,credentials:'same-origin',headers:{'Content-Type':'application/json'},body:b?JSON.stringify(b):undefined});const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error((j.error&&j.error.message)||'Something went wrong');return j};
 if(!t){msg.textContent='This link is missing its token.';return}
 call('GET','/api/email/unsubscribe/info?t='+encodeURIComponent(t)).then(i=>{
  if(!i.subscribed){msg.textContent='You are not subscribed to marketing emails'+(i.email?' at '+i.email:'')+'. Nothing more to do.';return}
  msg.textContent='Stop marketing emails to '+i.email+'?';btn.hidden=false;
 }).catch(e=>{msg.textContent=e.message});
 btn.addEventListener('click',async()=>{btn.disabled=true;try{await call('POST','/api/email/unsubscribe',{token:t});msg.textContent='Done. You will no longer receive marketing emails from VAULTA.';btn.hidden=true}catch(e){msg.textContent=e.message;btn.disabled=false}});
})();
