/* VAULTA Profile Settings: overview + sub-pages (#/profile/<page>), theme, and profile-photo upload/crop.
   Registered through the dashboard's VAULTA_EXT hook so app.js, its shell, header and mobile nav stay untouched.
   Every control here reads and writes server state; nothing is faked client-side. */
(()=>{
/* ---------------- theme (server value is the truth; localStorage is only a flash-free cache) ---------------- */
const TKEY='vaulta_theme_cache',mq=window.matchMedia?matchMedia('(prefers-color-scheme: dark)'):null;let tmode='light';
function applyTheme(t){tmode=['light','dark','system'].includes(t)?t:'light';const dark=tmode==='dark'||(tmode==='system'&&mq&&mq.matches);const r=document.documentElement;r.dataset.theme=dark?'dark':'light';r.style.colorScheme=dark?'dark':'light';try{localStorage.setItem(TKEY,tmode)}catch(e){}}
if(mq)mq.addEventListener('change',()=>{if(tmode==='system')applyTheme('system')});
let cached='light';try{cached=localStorage.getItem(TKEY)||'light'}catch(e){}applyTheme(cached);
window.VaultaTheme={apply:applyTheme,get:()=>tmode};

(window.VAULTA_EXT=window.VAULTA_EXT||[]).push(({R,load,V,UI})=>{
const {$,$$,esc,toast,modal,confirmBox,dt,day,label}=UI;
const me=()=>V.state.user||{};
applyTheme(me().theme);
const fire=()=>window.dispatchEvent(new Event('vaulta:profile'));
const SRC_MAX=10*1024*1024;

/* ---------------- icons ---------------- */
const P={user:'<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',lock:'<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',shield:'<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>',clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',device:'<rect x="7" y="2" width="10" height="20" rx="2"/><path d="M11 18h2"/>',cloud:'<path d="M17.5 19a4.5 4.5 0 0 0 .5-8.97A6 6 0 0 0 6.3 9.2 4 4 0 0 0 7 19z"/><path d="M12 12v6m-3-3 3-3 3 3"/>',file:'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="m9 15 2 2 4-4"/>',bell:'<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',mail:'<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',chat:'<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>',mega:'<path d="M3 11v2a1 1 0 0 0 1 1h3l8 4V6L7 10H4a1 1 0 0 0-1 1z"/><path d="M18 9a4 4 0 0 1 0 6"/>',gear:'<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',head:'<path d="M3 14v-2a9 9 0 0 1 18 0v2"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/>',help:'<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 1-1 1.7"/><path d="M12 17h.01"/>',globe:'<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',coin:'<circle cx="12" cy="12" r="9"/><path d="M14.5 9.5c-.5-1-1.5-1.5-2.5-1.5-1.4 0-2.5.8-2.5 2s1.1 1.7 2.5 2 2.5.8 2.5 2-1.1 2-2.5 2c-1 0-2-.5-2.5-1.5M12 6.5V8m0 8v1.5"/>',moon:'<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',cookie:'<path d="M21 12a9 9 0 1 1-9-9 3 3 0 0 0 4 4 3 3 0 0 0 5 5z"/><path d="M8.5 9h.01M12 14h.01M8 14.5h.01M15.5 15.5h.01"/>',share:'<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="m8.6 13.5 6.8 4M15.4 6.5l-6.8 4"/>',chev:'<path d="m9 6 6 6-6 6"/>',camera:'<path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>',eye:'<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z"/><circle cx="12" cy="12" r="3"/>',eyeoff:'<path d="M17.9 17.9A10.9 10.9 0 0 1 12 20C5 20 1 12 1 12a18.5 18.5 0 0 1 5.1-5.9M9.9 4.2A9 9 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.2 3.2M1 1l22 22"/>',check:'<path d="m5 12 5 5 9-10"/>',x:'<path d="M6 6l12 12M18 6 6 18"/>',plus:'<path d="M12 5v14M5 12h14"/>',minus:'<path d="M5 12h14"/>',down:'<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5M12 15V3"/>',back:'<path d="m15 18-6-6 6-6"/>',info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',alert:'<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',out:'<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5M21 12H9"/>',trash:'<path d="M3 6h18M8 6V4h8v2m-9 0 1 14h8l1-14"/>'};
const ic=(k,n=20)=>`<svg class="psi" width="${n}" height="${n}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${P[k]||''}</svg>`;

/* ---------------- shared building blocks ---------------- */
const ini=()=>{const u=me();return((u.first_name||u.email||'?')[0]+(u.last_name||'')[0]).toUpperCase().slice(0,2)};
const fullName=()=>[me().first_name,me().last_name].filter(Boolean).join(' ')||me().email||'';
const avatar=(cls='')=>`<span class="ps-av ${cls}" data-avatar>${me().avatar_url?`<img src="${esc(me().avatar_url)}" alt="" decoding="async">`:esc(ini())}</span>`;
const memberSince=()=>{const d=new Date(me().created_at);return isNaN(d)?'':'Member since '+d.toLocaleDateString('en-US',{month:'long',year:'numeric'})};
const back=()=>'';   /* the shared top-left Back button (nav.js) now serves every page, including these sub-pages (parent: Profile) */
const pageHead=(t,d,sub)=>`<header class="ps-head">${sub?back():''}<h1>${esc(t)}</h1><p>${esc(d)}</p></header>`;
const card=(icon,title,desc,inner,id)=>`<section class="ps-card" ${id?`id="${id}"`:''} aria-labelledby="${id||title.replace(/\W/g,'')}-h"><div class="ps-card-h"><span class="ps-ico">${ic(icon)}</span><div><h2 id="${id||title.replace(/\W/g,'')}-h">${esc(title)}</h2>${desc?`<p>${esc(desc)}</p>`:''}</div></div><div class="ps-card-b">${inner}</div></section>`;
const chip=(t,on)=>`<span class="ps-chip ${on?'on':'off'}">${esc(t)}</span>`;
const sw=(id,lab,on,{dis=false}={})=>`<span class="sw-w"><button type="button" class="sw" role="switch" aria-checked="${!!on}" aria-label="${esc(lab)}" data-sw="${esc(id)}" ${dis?'disabled':''}><i></i></button><span class="sw-t" aria-hidden="true">${on?'On':'Off'}</span></span>`;
function setSw(b,on,busy){b.setAttribute('aria-checked',String(!!on));b.disabled=!!busy;b.classList.toggle('busy',!!busy);const t=b.nextElementSibling;if(t)t.textContent=on?'On':'Off'}
const alertBox=(msg,kind='err')=>msg?`<div class="ps-msg ${kind}" role="${kind==='err'?'alert':'status'}">${ic(kind==='err'?'alert':'check',18)}<span>${esc(msg)}</span></div>`:'';
const field=(name,lab,{type='text',value='',readonly=false,help='',max,auto,mode,req=false,opts})=>{const id='f-'+name;return`<div class="ps-f"><label for="${id}">${esc(lab)}${req?' <abbr title="required" aria-hidden="true">*</abbr>':''}</label>${opts?`<select id="${id}" name="${name}" ${readonly?'disabled':''}>${opts.map(([v,l])=>`<option value="${esc(v)}" ${v===value?'selected':''}>${esc(l)}</option>`).join('')}</select>`:`<input id="${id}" name="${name}" type="${type}" value="${esc(value)}" ${readonly?'readonly aria-readonly="true"':''} ${max?`maxlength="${max}"`:''} ${auto?`autocomplete="${auto}"`:''} ${mode?`inputmode="${mode}"`:''} ${req?'required aria-required="true"':''} aria-describedby="${id}-e${help?' '+id+'-h':''}">`}${help?`<small id="${id}-h" class="ps-help">${esc(help)}</small>`:''}<small id="${id}-e" class="ps-err" role="alert"></small></div>`};
const setErr=(form,name,msg)=>{const i=$('#f-'+name,form),e=$('#f-'+name+'-e',form);if(!i||!e)return;e.textContent=msg||'';if(msg)i.setAttribute('aria-invalid','true');else i.removeAttribute('aria-invalid')};
const busyBtn=async(btn,fn,txt='Saving…')=>{const t=btn.innerHTML;btn.disabled=true;btn.setAttribute('aria-busy','true');btn.innerHTML=`<span class="spin" aria-hidden="true"></span>${esc(txt)}`;try{return await fn()}finally{btn.disabled=false;btn.removeAttribute('aria-busy');btn.innerHTML=t}};
const row=(r)=>`<a class="ps-row" href="${r.href}">${r.icon?`<span class="ps-ri">${ic(r.icon,20)}</span>`:''}<span class="ps-rl">${esc(r.label)}</span>${r.val?`<span class="ps-rv">${esc(r.val)}</span>`:''}${r.chip||''}<span class="ps-rc">${ic('chev',16)}</span></a>`;
const cookieStatus=()=>window.VaultaConsent&&VaultaConsent.get();
function device(ua){ua=ua||'';const b=/Edg\//.test(ua)?'Edge':/OPR\//.test(ua)?'Opera':/Firefox\//.test(ua)?'Firefox':/Chrome\//.test(ua)?'Chrome':/Safari\//.test(ua)?'Safari':null;const o=/iPhone|iPad/.test(ua)?'iOS':/Android/.test(ua)?'Android':/Windows/.test(ua)?'Windows':/Mac OS X/.test(ua)?'macOS':/Linux/.test(ua)?'Linux':null;return b&&o?`${b} on ${o}`:b||o||'Unknown device'}
const EVT={login_success:['Signed in','ok'],login_failed:['Failed sign-in attempt','bad'],login_blocked_locked:['Sign-in blocked (account locked)','bad'],account_locked:['Account temporarily locked','bad'],logout:['Signed out','ok'],password_changed:['Password changed','ok'],password_reset_requested:['Password reset requested','ok'],password_reset_completed:['Password reset','ok'],'2fa_enabled':['Two-factor authentication enabled','ok'],'2fa_disabled':['Two-factor authentication disabled','warn'],'2fa_failed':['Incorrect 2FA code','bad'],session_revoked:['Device signed out','ok'],other_sessions_revoked:['Other devices signed out','ok'],avatar_changed:['Profile photo changed','ok'],avatar_removed:['Profile photo removed','ok'],data_exported:['Personal data export downloaded','ok'],account_created:['Account created','ok'],email_verified:['Email verified','ok'],permission_denied:['Blocked action','bad']};

/* ---------------- profile photo: pick -> crop -> preview -> save ---------------- */
const TYPES=['image/jpeg','image/png','image/webp'];
function sniff(buf){const b=new Uint8Array(buf);if(b[0]===0xff&&b[1]===0xd8&&b[2]===0xff)return'image/jpeg';if(b[0]===0x89&&b[1]===0x50&&b[2]===0x4e&&b[3]===0x47)return'image/png';if(String.fromCharCode(...b.slice(0,4))==='RIFF'&&String.fromCharCode(...b.slice(8,12))==='WEBP')return'image/webp';return null}
function pickPhoto(){
 const inp=document.createElement('input');inp.type='file';inp.accept='image/jpeg,image/png,image/webp';inp.style.display='none';document.body.append(inp);
 inp.onchange=async()=>{const f=inp.files[0];inp.remove();if(!f)return;
  if(f.size>SRC_MAX)return toast('That image is larger than 10 MB. Choose a smaller photo.','err');
  let real;try{real=sniff(await f.slice(0,16).arrayBuffer())}catch(e){}
  if(!real||!TYPES.includes(real))return toast('Unsupported file. Choose a JPG, PNG or WebP image.','err');
  const url=await new Promise((res,rej)=>{const r=new FileReader();r.onload=()=>res(r.result);r.onerror=rej;r.readAsDataURL(f)}).catch(()=>null);
  if(!url)return toast('That file could not be read.','err');
  const img=new Image();img.onload=()=>{if(Math.min(img.naturalWidth,img.naturalHeight)<128)return toast('That image is too small. Use a photo at least 128 × 128 pixels.','err');cropDialog(img)};img.onerror=()=>toast('That file is not a valid image.','err');img.src=url};
 inp.click()}
function cropDialog(img){
 const S=300,OUT=512,iw=img.naturalWidth,ih=img.naturalHeight,minS=S/Math.min(iw,ih);let z=1,ox=0,oy=0;
 const d=modal('Adjust your photo',`<div class="crop" id="cr1"><p class="ps-help" id="crh">Drag the photo to position it. Use the slider, the buttons, your mouse wheel or a pinch to zoom.</p>
  <div class="crop-stage" id="stage" tabindex="0" role="application" aria-label="Photo crop area. Arrow keys move the photo, plus and minus zoom." aria-describedby="crh"><canvas id="cv" width="${S*2}" height="${S*2}"></canvas></div>
  <div class="crop-z"><button type="button" class="ps-ib" id="zo" aria-label="Zoom out">${ic('minus',18)}</button><input type="range" id="zr" min="1" max="4" step="0.01" value="1" aria-label="Zoom"><button type="button" class="ps-ib" id="zi" aria-label="Zoom in">${ic('plus',18)}</button></div>
  <div class="crop-pv"><canvas id="pv" width="96" height="96" aria-hidden="true"></canvas><span class="ps-help">Preview of your avatar</span></div>
  <div class="actions"><button class="btn ghost" type="button" data-x>Cancel</button><button class="btn" type="button" id="ap">Apply crop</button></div></div>
  <div class="crop" id="cr2" hidden><p class="ps-help">This is how your photo will look. It saves only when you choose Save photo.</p><div class="crop-fin"><img id="fi" alt="Final avatar preview" width="160" height="160"></div><div id="cm"></div>
  <div class="actions"><button class="btn ghost" type="button" id="bk">Back</button><button class="btn" type="button" id="sv">Save photo</button></div></div>`);
 const cv=$('#cv',d),g=cv.getContext('2d'),pv=$('#pv',d),pg=pv.getContext('2d'),zr=$('#zr',d),st=$('#stage',d);let saving=false;
 const sc=()=>minS*z;
 const clamp=()=>{ox=Math.min(0,Math.max(S-iw*sc(),ox));oy=Math.min(0,Math.max(S-ih*sc(),oy))};
 const center=()=>{ox=(S-iw*sc())/2;oy=(S-ih*sc())/2};center();
 function draw(){clamp();const k=cv.width/S;g.setTransform(k,0,0,k,0,0);g.fillStyle='#111';g.fillRect(0,0,S,S);g.drawImage(img,ox,oy,iw*sc(),ih*sc());
  g.save();g.beginPath();g.rect(0,0,S,S);g.arc(S/2,S/2,S/2-6,0,Math.PI*2,true);g.fillStyle='rgba(0,0,0,.55)';g.fill('evenodd');g.restore();g.beginPath();g.arc(S/2,S/2,S/2-6,0,Math.PI*2);g.strokeStyle='rgba(255,255,255,.9)';g.lineWidth=1.5;g.stroke();
  pg.clearRect(0,0,96,96);pg.save();pg.beginPath();pg.arc(48,48,48,0,Math.PI*2);pg.clip();pg.drawImage(img,-ox/sc(),-oy/sc(),S/sc(),S/sc(),0,0,96,96);pg.restore()}
 const setZ=nz=>{nz=Math.min(4,Math.max(1,nz));const cx=(S/2-ox)/sc(),cy=(S/2-oy)/sc();z=nz;ox=S/2-cx*sc();oy=S/2-cy*sc();zr.value=z;draw()};
 zr.oninput=()=>setZ(+zr.value);$('#zi',d).onclick=()=>setZ(z+.25);$('#zo',d).onclick=()=>setZ(z-.25);
 st.addEventListener('wheel',e=>{e.preventDefault();setZ(z*(e.deltaY<0?1.08:.93))},{passive:false});
 const ptr=new Map();let pd=0;
 st.addEventListener('pointerdown',e=>{st.setPointerCapture(e.pointerId);ptr.set(e.pointerId,[e.clientX,e.clientY]);if(ptr.size===2){const [a,b]=[...ptr.values()];pd=Math.hypot(a[0]-b[0],a[1]-b[1])}});
 st.addEventListener('pointermove',e=>{if(!ptr.has(e.pointerId))return;const p=ptr.get(e.pointerId),k=S/st.getBoundingClientRect().width;
  if(ptr.size===1){ox+=(e.clientX-p[0])*k;oy+=(e.clientY-p[1])*k;ptr.set(e.pointerId,[e.clientX,e.clientY]);draw()}
  else{ptr.set(e.pointerId,[e.clientX,e.clientY]);const [a,b]=[...ptr.values()],nd=Math.hypot(a[0]-b[0],a[1]-b[1]);if(pd)setZ(z*nd/pd);pd=nd}});
 const up=e=>{ptr.delete(e.pointerId);pd=0};st.addEventListener('pointerup',up);st.addEventListener('pointercancel',up);
 st.addEventListener('keydown',e=>{const m={ArrowLeft:[12,0],ArrowRight:[-12,0],ArrowUp:[0,12],ArrowDown:[0,-12]}[e.key];if(m){e.preventDefault();ox+=m[0];oy+=m[1];draw()}else if(e.key==='+'||e.key==='='){e.preventDefault();setZ(z+.2)}else if(e.key==='-'){e.preventDefault();setZ(z-.2)}});
 draw();
 let blob=null;
 const render=()=>new Promise(res=>{const c=document.createElement('canvas');c.width=c.height=OUT;const x=c.getContext('2d');x.fillStyle='#fff';x.fillRect(0,0,OUT,OUT);x.imageSmoothingQuality='high';x.drawImage(img,-ox/sc(),-oy/sc(),S/sc(),S/sc(),0,0,OUT,OUT);c.toBlob(b=>res({b,url:c.toDataURL('image/jpeg',.92)}),'image/jpeg',.92)});
 $('#ap',d).onclick=async()=>{const r=await render();if(!r.b)return toast('Could not process that image.','err');blob=r.b;$('#fi',d).src=r.url;$('#cr1',d).hidden=true;$('#cr2',d).hidden=false;$('#sv',d).focus()};
 $('#bk',d).onclick=()=>{if(saving)return;$('#cr2',d).hidden=true;$('#cr1',d).hidden=false;$('#cm',d).innerHTML='';st.focus()};
 d.addEventListener('cancel',e=>{if(saving)e.preventDefault()});
 $('#sv',d).onclick=async()=>{if(saving||!blob)return;saving=true;const b=$('#sv',d),bk=$('#bk',d);bk.disabled=true;$('#cm',d).innerHTML='';
  try{await busyBtn(b,async()=>{const fd=new FormData();fd.append('file',blob,'avatar.jpg');await V.upload('/api/profile/avatar',fd)},'Uploading…');
   await V.session();d.close();fire();toast('Profile photo updated');if(location.hash.startsWith('#/profile'))setTimeout(refreshPhotoUI,0)}
  catch(e){$('#cm',d).innerHTML=alertBox(e.message||'Upload failed. Your current photo was not changed.')}finally{saving=false;bk.disabled=false}}}
const refreshPhotoUI=()=>{$$('[data-avatar]').forEach(a=>{a.innerHTML=me().avatar_url?`<img src="${esc(me().avatar_url)}" alt="" decoding="async">`:esc(ini())});const rm=$('#rmph');if(rm)rm.hidden=!me().avatar_url};
async function removePhoto(){const ok=await confirmBox('Remove profile photo','Your photo will be deleted and replaced with your initials everywhere in VAULTA.',{ok:'Remove photo',danger:true});if(!ok)return;
 try{await V.del('/api/profile/avatar');await V.session();fire();toast('Profile photo removed');refreshPhotoUI()}catch(e){toast(e.message,'err')}}
const photoBlock=(extra='')=>`<div class="ps-photo"><div class="ps-avw">${avatar('lg')}<button type="button" class="ps-cam" data-act="photo" aria-label="Change profile photo">${ic('camera',16)}</button></div>${extra}</div>`;

/* ---------------- overview ---------------- */
async function overview(v){
 const [prof,np,pr]=await Promise.all([V.get('/api/profile'),V.get('/api/notification-preferences'),V.get('/api/settings/preferences')]);
 const u=prof.user,pf=prof.profile,types=np.preferences,promo=types.find(t=>t.type==='promotional'),emailOn=types.some(t=>t.type!=='promotional'&&t.email),ch=pr.channels,th=pr.preferences.theme,thl=({light:'Light',dark:'Dark',system:'System'})[th];
 const tf=!!me().two_factor_enabled,dob=(pf.date_of_birth||'');
 const G=[
  ['General Settings',[{label:'Personal Information',icon:'user',href:'#/profile/personal'},{label:'Privacy Settings',icon:'shield',href:'#/profile/privacy'},{label:'Verification & Legal',icon:'file',href:'#/settings'}]],
  ['Data Management',[{label:'Export Data',icon:'file',href:'#/profile/data'}]],
  ['Account & Security',[{label:'Change Password',icon:'lock',href:'#/profile/password'},{label:'Two-Factor Authentication',icon:'shield',href:'#/profile/two-factor',chip:chip(tf?'On':'Off',tf)},{label:'Login Activity',icon:'clock',href:'#/profile/activity'},{label:'Device Management',icon:'device',href:'#/profile/devices'}]],
  ['Notifications',[{label:'Email Notifications',icon:'mail',href:'#/profile/notifications',chip:chip(emailOn?'On':'Off',emailOn)},{label:'Push Notifications',icon:'bell',href:'#/profile/notifications',chip:chip('Unavailable',false)},{label:'SMS Notifications',icon:'chat',href:'#/profile/notifications',chip:chip('Unavailable',false)},{label:'Marketing Updates',icon:'mega',href:'#/profile/notifications',chip:chip(promo&&promo.email?'On':'Off',promo&&promo.email)}]],
  ['Preferences',[{label:'Language',icon:'globe',val:'English',href:'#/profile/preferences'},{label:'Currency',icon:'coin',val:'USD',href:'#/profile/preferences'},{label:'Theme Mode',icon:'moon',val:thl,href:'#/profile/preferences'}]],
  ['Privacy & Data',[{label:'Data Sharing',icon:'share',href:'#/profile/privacy?s=sharing'},{label:'Cookie Preferences',icon:'cookie',href:'#/profile/privacy?s=cookies'},{label:'Marketing Permissions',icon:'mega',href:'#/profile/privacy?s=marketing'}]],
  ['Help & Support',[{label:'Help Center',icon:'help',href:'#/profile/help'},{label:'Contact Support',icon:'head',href:'#/profile/help?s=contact'}]]];
 const list=(rows)=>`<div class="ps-list">${rows.map(row).join('')}</div>`;
 const kv=(k,val)=>`<div class="ps-kv"><span>${esc(k)}</span><b>${esc(val||'—')}</b></div>`;
 const cardLink=(icon,t,desc,href,inner)=>`<section class="ps-card link"><a class="ps-card-h" href="${href}"><span class="ps-ico">${ic(icon)}</span><div><h2>${esc(t)}</h2><p>${esc(desc)}</p></div><span class="ps-rc">${ic('chev',16)}</span></a><div class="ps-card-b">${inner}</div></section>`;
 const mini=(rows)=>`<div class="ps-list sm">${rows.map(row).join('')}</div>`;
 v.innerHTML=`<div class="ps"><header class="ps-head"><h1>Profile Settings</h1><p>Manage your account, preferences and security settings.</p></header>
 <div class="ps-grid">
  <aside class="ps-side"><section class="ps-id">${photoBlock()}<div class="ps-idt"><h2 class="ps-name">${esc(fullName())}</h2><p class="ps-since">${esc(memberSince())}</p></div></section>
   <div class="ps-nav">${[G[0],G[1],['Help',[{label:'Help & FAQ',icon:'help',href:'#/profile/help'},{label:'Customer Support',icon:'head',href:'#/profile/help?s=contact'}]]].map(([t,r])=>`<div class="ps-grp"><h3>${esc(t)}</h3>${list(r)}</div>`).join('')}</div></aside>
  <div class="ps-cards">
   ${cardLink('user','Personal Information','Update your personal details and contact information.','#/profile/personal',kv('Full Name',fullName())+kv('Email Address',u.email)+kv('Phone Number',u.phone)+kv('Date of Birth',dob?day(dob):''))}
   ${cardLink('lock','Account Security','Manage your password and security settings.','#/profile/security',mini([{label:'Change Password',href:'#/profile/password'},{label:'Two-Factor Authentication',href:'#/profile/two-factor',chip:chip(tf?'On':'Off',tf)},{label:'Login Activity',href:'#/profile/activity'},{label:'Device Management',href:'#/profile/devices'}]))}
   ${cardLink('bell','Notifications','Control how you receive updates and alerts.','#/profile/notifications',mini([{label:'Email Notifications',href:'#/profile/notifications',chip:chip(emailOn?'On':'Off',emailOn)},{label:'Push Notifications',href:'#/profile/notifications',chip:chip('Unavailable',false)},{label:'SMS Notifications',href:'#/profile/notifications',chip:chip('Unavailable',false)},{label:'Marketing Updates',href:'#/profile/notifications',chip:chip(promo&&promo.email?'On':'Off',promo&&promo.email)}]))}
   ${cardLink('gear','Preferences','Customize your app experience.','#/profile/preferences',mini([{label:'Language',val:'English',href:'#/profile/preferences'},{label:'Currency',val:'USD',href:'#/profile/preferences'},{label:'Theme Mode',val:thl,href:'#/profile/preferences'}]))}
   ${cardLink('shield','Privacy Settings','Manage your data and privacy preferences.','#/profile/privacy',mini([{label:'Data Sharing',href:'#/profile/privacy?s=sharing'},{label:'Cookie Preferences',href:'#/profile/privacy?s=cookies'},{label:'Marketing Permissions',href:'#/profile/privacy?s=marketing'}]))}
   ${cardLink('head','Help & Support','Get help or contact our support team.','#/profile/help',mini([{label:'Help Center',href:'#/profile/help'},{label:'Contact Support',href:'#/profile/help?s=contact'}]))}
  </div>
  <div class="ps-mob">${G.map(([t,r])=>`<section class="ps-grp card"><h3>${esc(t)}</h3>${list(r)}</section>`).join('')}
   <section class="ps-grp card"><h3>More from VAULTA</h3>${list([{label:'Savings',icon:'coin',href:'#/savings'},{label:'Retirement',icon:'coin',href:'#/retirement'},{label:'Watchlist',icon:'eye',href:'#/watchlist'},{label:'Transactions',icon:'clock',href:'#/transactions'},{label:'Statements',icon:'file',href:'#/statements'},{label:'Notifications',icon:'bell',href:'#/notifications'},{label:'Support',icon:'head',href:'#/support'}].concat((me().permissions||[]).length?[{label:'Admin console',icon:'gear',href:'/admin/'}]:[]))}
   <div class="ps-list"><button type="button" class="ps-row ps-out" id="ps-lo"><span class="ps-ri">${ic('out',20)}</span><span class="ps-rl">Log out</span></button></div></section></div>
 </div></div>`;
 const lo=v.querySelector('#ps-lo');if(lo)lo.onclick=async()=>{try{await V.logout()}finally{location.href='/login/'}}}

/* ---------------- personal information ---------------- */
async function personal(v){
 const j=await V.get('/api/profile'),p=j.profile,u=j.user,kyc=await V.get('/api/kyc').catch(()=>null),dob=(kyc&&kyc.kyc.date_of_birth)||p.date_of_birth||'';
 const F=[['first_name','First name',{max:60,auto:'given-name',req:true,value:p.first_name||''}],['last_name','Last name',{max:60,auto:'family-name',req:true,value:p.last_name||''}],
  ['email','Email address',{type:'email',value:u.email,readonly:true,help:'Your email is your sign-in identity, so it can only be changed by our support team after identity checks.'}],
  ['phone','Phone number',{type:'tel',max:30,auto:'tel',mode:'tel',value:u.phone||'',help:'Include the country code, for example +234 801 234 5678.'}],
  ['country','Country of residence',{max:60,auto:'country-name',value:p.country||''}],
  ['dob','Date of birth',{value:dob,readonly:true,help:'Set during identity verification. To correct it, update your details under Verification & Legal.'}],
  ['employment','Employment',{max:60,value:p.employment||''}],['income_range','Income range',{max:40,value:p.income_range||''}],
  ['investment_goal','Investment goal',{max:80,value:p.investment_goal||''}],['investment_horizon','Investment horizon',{max:30,value:p.investment_horizon||'',help:'For example: 5 years.'}],
  ['risk_tolerance','Risk tolerance',{value:p.risk_tolerance||'',opts:[['','Not set'],['Conservative','Conservative'],['Moderate','Moderate'],['Aggressive','Aggressive']]}]];
 v.innerHTML=`<div class="ps ps-sub">${pageHead('Personal Information','Update your personal details and contact information.',1)}
 <div class="ps-two"><section class="ps-card ps-photocard"><div class="ps-card-b">${photoBlock(`<div class="ps-photo-t"><b>Profile photo</b><small>JPG, PNG or WebP. You can crop and zoom before saving.</small><div class="ps-btns"><button type="button" class="btn ghost sm" data-act="photo">Change photo</button><button type="button" class="btn ghost sm" id="rmph" data-act="rmphoto" ${me().avatar_url?'':'hidden'}>Remove photo</button></div></div>`)}</div></section>
 <form class="ps-card" id="pform" novalidate><div class="ps-card-b"><div id="fmsg" aria-live="polite"></div><div class="ps-fgrid">${F.map(([n,l,o])=>field(n,l,o)).join('')}</div>
 <div class="ps-actions"><button type="button" class="btn ghost" id="pc" disabled>Cancel</button><button class="btn" id="ps" disabled>Save changes</button></div></div></form></div></div>`;
 const f=$('#pform'),names=F.filter(x=>!x[2].readonly).map(x=>x[0]),snap=()=>JSON.stringify(names.map(n=>f.elements[n].value.trim()));let base=snap();
 const dirty=()=>{const d=snap()!==base;$('#ps').disabled=!d;$('#pc').disabled=!d};f.addEventListener('input',dirty);f.addEventListener('change',dirty);
 $('#pc').onclick=()=>{names.forEach(n=>{f.elements[n].value=F.find(x=>x[0]===n)[2].value;setErr(f,n,'')});$('#fmsg').innerHTML='';dirty()};
 const rules={first_name:x=>x?'':'Enter your first name.',last_name:x=>x?'':'Enter your last name.',phone:x=>!x||/^\+?[0-9 ()\-.]{6,30}$/.test(x)?'':'Enter a valid phone number, for example +234 801 234 5678.'};
 f.onsubmit=async e=>{e.preventDefault();let bad=null;names.forEach(n=>{const m=(rules[n]||(()=>''))(f.elements[n].value.trim());setErr(f,n,m);if(m&&!bad)bad=n});if(bad){f.elements[bad].focus();return}
  const body={};names.forEach(n=>body[n]=f.elements[n].value.trim());$('#fmsg').innerHTML='';
  try{await busyBtn($('#ps'),()=>V.put('/api/profile',body));await V.session();fire();const r=await V.get('/api/profile');names.forEach(n=>{const s=n==='phone'?r.user.phone:r.profile[n];f.elements[n].value=s||'';F.find(x=>x[0]===n)[2].value=s||''});base=snap();dirty();$('#fmsg').innerHTML=alertBox('Your details were saved.','ok');toast('Personal information saved')}
  catch(err){$('#fmsg').innerHTML=alertBox(err.message||'Could not save your changes. Your edits are still here.');dirty()}}}

/* ---------------- account security ---------------- */
async function security(v){
 const [s,ev]=await Promise.all([V.get('/api/auth/sessions'),V.get('/api/auth/security-events')]);const tf=!!me().two_factor_enabled,last=ev.events.find(e=>e.type==='login_success');
 v.innerHTML=`<div class="ps ps-sub">${pageHead('Account Security','Manage your password, two-factor authentication and signed-in devices.',1)}<div class="ps-stack">
 ${card('shield','Security status','','<div class="ps-status">'+[['Two-factor authentication',tf?'Enabled':'Not enabled',tf],['Signed-in devices',s.sessions.length+' active',true],['Email verified',me().email_verified?'Verified':'Not verified',!!me().email_verified]].map(([a,b,ok])=>`<div class="ps-kv"><span>${a}</span><b>${chip(b,ok)}</b></div>`).join('')+'</div>')}
 <div class="ps-list card-list">${[{label:'Change Password',icon:'lock',href:'#/profile/password'},{label:'Two-Factor Authentication',icon:'shield',href:'#/profile/two-factor',chip:chip(tf?'On':'Off',tf)},{label:'Login Activity',icon:'clock',href:'#/profile/activity',val:last?dt(last.created_at):''},{label:'Device Management',icon:'device',href:'#/profile/devices',val:s.sessions.length+' active'}].map(row).join('')}</div></div></div>`}

function pwPage(v){
 v.innerHTML=`<div class="ps ps-sub">${pageHead('Change Password','Choose a strong password you don’t use anywhere else.',1)}
 <form class="ps-card" id="pw" novalidate><div class="ps-card-b"><div id="fmsg"></div><div class="ps-pwgrid"><div class="ps-pwf">
 ${['current_password|Current password|current-password','new_password|New password|new-password','confirm|Confirm new password|new-password'].map(s=>{const [n,l,a]=s.split('|');return`<div class="ps-f"><label for="f-${n}">${l}</label><div class="ps-pw"><input id="f-${n}" name="${n}" type="password" autocomplete="${a}" maxlength="128" required aria-describedby="f-${n}-e${n==='new_password'?' reqs':''}"><button type="button" class="ps-eye" data-eye="f-${n}" aria-pressed="false" aria-label="Show ${l.toLowerCase()}">${ic('eye',18)}</button></div><small id="f-${n}-e" class="ps-err" role="alert"></small></div>`}).join('')}
 </div><aside class="ps-pwa"><h3>Password requirements</h3><ul class="ps-reqs" id="reqs" aria-label="Password requirements"><li data-r="len">At least 10 characters</li><li data-r="let">Includes a letter</li><li data-r="num">Includes a number</li></ul>
 <p class="ps-help">Changing your password signs you out of all other devices.</p></aside></div>
 <div class="ps-actions"><a class="btn ghost" href="#/profile/security">Cancel</a><button class="btn" id="ps">Update password</button></div></div></form></div>`;
 const f=$('#pw'),chk=()=>{const x=f.elements.new_password.value;[['len',x.length>=10],['let',/[A-Za-z]/.test(x)],['num',/\d/.test(x)]].forEach(([k,ok])=>{const li=$(`[data-r=${k}]`,f);li.classList.toggle('ok',ok);li.setAttribute('data-state',ok?'met':'unmet')})};f.addEventListener('input',chk);chk();
 f.onsubmit=async e=>{e.preventDefault();const c=f.elements.current_password.value,n=f.elements.new_password.value,k=f.elements.confirm.value;let bad=0;
  const m={current_password:c?'':'Enter your current password.',new_password:n.length<10?'Use at least 10 characters.':!/[A-Za-z]/.test(n)||!/\d/.test(n)?'Include a letter and a number.':n===c?'Choose a password different from your current one.':'',confirm:k===n?'':'The passwords don’t match.'};
  Object.entries(m).forEach(([a,b])=>{setErr(f,a,b);if(b&&!bad){bad=1;f.elements[a].focus()}});if(bad)return;$('#fmsg').innerHTML='';
  try{await busyBtn($('#ps'),()=>V.post('/api/auth/change-password',{current_password:c,new_password:n}),'Updating…');f.reset();chk();$('#fmsg').innerHTML=alertBox('Password updated. Your other devices were signed out.','ok');toast('Password updated')}
  catch(err){$('#fmsg').innerHTML=alertBox(err.message||'Could not update your password.')}}}

async function twofa(v){
 const tf=!!me().two_factor_enabled;
 v.innerHTML=`<div class="ps ps-sub">${pageHead('Two-Factor Authentication','Add a second step to sign-in and to protect withdrawals and larger trades.',1)}
 <div class="ps-stack"><section class="ps-card"><div class="ps-card-b"><div class="ps-kv big"><span>Status</span><b>${chip(tf?'Enabled':'Not enabled',tf)}</b></div><p class="ps-help">This status comes straight from your account.</p>
 <div id="tfa" class="ps-tfa">${tf?'<button class="btn ghost" id="d2" type="button">Disable two-factor authentication</button>':'<button class="btn" id="e2" type="button">Set up two-factor authentication</button>'}</div></div></section>
 ${card('shield','How it works','',`<p class="ps-p">Two-factor authentication uses an authenticator app (TOTP) such as Google Authenticator, 1Password or Authy. After your password, you enter a 6-digit code from the app.</p><p class="ps-p">Turning it on or off signs you out of your other devices.</p>`)}</div></div>`;
 const e2=$('#e2');if(e2)e2.onclick=async()=>{try{const j=await busyBtn(e2,()=>V.post('/api/auth/2fa/setup'),'Preparing…');
  $('#tfa').innerHTML=`<ol class="ps-steps"><li>In your authenticator app, add an account using this key:<div class="ps-key"><code id="sk">${esc(j.secret)}</code><button type="button" class="btn ghost sm" id="cp">Copy</button></div></li><li>Enter the 6-digit code the app shows.</li></ol>
  <form id="ef" novalidate><div class="ps-f"><label for="f-code">6-digit code</label><input id="f-code" name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="[0-9]{6}" required aria-describedby="f-code-e"><small id="f-code-e" class="ps-err" role="alert"></small></div><p class="ps-help">Turning this on signs you out of your other devices.</p><div class="ps-actions"><button type="button" class="btn ghost" id="cn">Cancel</button><button class="btn" id="es">Turn on</button></div></form>`;
  $('#cp').onclick=async()=>{try{await navigator.clipboard.writeText(j.secret);toast('Key copied')}catch(e){toast('Select the key and copy it manually.','err')}};$('#cn').onclick=()=>twofa(v);$('#f-code').focus();
  $('#ef').onsubmit=async ev=>{ev.preventDefault();const code=$('#f-code').value.replace(/\s/g,'');if(!/^\d{6}$/.test(code)){$('#f-code-e').textContent='Enter the 6-digit code from your app.';return}$('#f-code-e').textContent='';
   try{await busyBtn($('#es'),()=>V.post('/api/auth/2fa/enable',{code}),'Verifying…');await V.session();fire();toast('Two-factor authentication is on');twofa(v)}catch(err){$('#f-code-e').textContent=err.message}}}catch(err){toast(err.message,'err')}};
 const d2=$('#d2');if(d2)d2.onclick=()=>{const d=modal('Disable two-factor authentication',`<form id="xf" novalidate><p>Confirm with your password and a current code.</p><div class="ps-f"><label for="x-pw">Password</label><input id="x-pw" type="password" name="password" autocomplete="current-password" required></div><div class="ps-f"><label for="x-c">Current 2FA code</label><input id="x-c" name="code" inputmode="numeric" maxlength="6" autocomplete="one-time-code" required></div><div id="xm" aria-live="polite"></div><div class="actions"><button type="button" class="btn ghost" data-x>Cancel</button><button class="btn danger" id="xs">Disable</button></div></form>`);
  $('#xf',d).onsubmit=async ev=>{ev.preventDefault();const fd=Object.fromEntries(new FormData(ev.target));try{await busyBtn($('#xs',d),()=>V.post('/api/auth/2fa/disable',fd),'Disabling…');d.close();await V.session();fire();toast('Two-factor authentication disabled');twofa(v)}catch(err){$('#xm',d).innerHTML=alertBox(err.message)}}}}

async function activity(v){
 const ev=(await V.get('/api/auth/security-events')).events;
 v.innerHTML=`<div class="ps ps-sub">${pageHead('Login Activity','Recent sign-ins and security events on your account. Review anything you don’t recognise.',1)}
 <section class="ps-card"><div class="ps-card-b flush">${ev.length?`<ul class="ps-act">${ev.map(e=>{const m=EVT[e.type]||[label(e.type),'ok'];return`<li class="${m[1]}"><span class="ps-ai">${ic(m[1]==='bad'?'alert':m[1]==='warn'?'info':'check',18)}</span><div><b>${esc(m[0])}</b><small>${esc(dt(e.created_at))}${e.user_agent?' · '+esc(device(e.user_agent)):''}${e.ip?' · IP '+esc(e.ip):''}</small></div></li>`}).join('')}</ul>`:'<div class="empty">No security activity has been recorded yet.</div>'}</div></section>
 <p class="ps-help">If you see activity that isn’t yours, <a href="#/profile/password">change your password</a> and <a href="#/profile/devices">sign out other devices</a> right away.</p></div>`}

async function devices(v){
 const s=(await V.get('/api/auth/sessions')).sessions,others=s.filter(x=>!x.current);
 v.innerHTML=`<div class="ps ps-sub">${pageHead('Device Management','Devices currently signed in to your account.',1)}
 <section class="ps-card"><div class="ps-card-b flush">${s.length?`<ul class="ps-act dev">${s.map(x=>`<li><span class="ps-ai">${ic('device',18)}</span><div><b>${esc(device(x.user_agent))} ${x.current?'<span class="ps-chip on">This device</span>':''}</b><small>Signed in ${esc(dt(x.created_at))} · Last active ${esc(dt(x.last_seen_at))}${x.ip?' · IP '+esc(x.ip):''}</small></div>${x.current?'':`<button type="button" class="btn ghost sm" data-out="${x.id}" aria-label="Sign out ${esc(device(x.user_agent))}">Sign out</button>`}</li>`).join('')}</ul>`:'<div class="empty">No active sessions.</div>'}</div></section>
 ${others.length?'<div class="ps-actions left"><button type="button" class="btn danger" id="ro">Sign out all other devices</button></div>':'<p class="ps-help">This is the only device signed in.</p>'}</div>`;
 v.onclick=async e=>{const b=e.target.closest('[data-out]');if(b){const ok=await confirmBox('Sign out this device','That device will be signed out immediately and will need to sign in again.',{ok:'Sign out',danger:true});if(!ok)return;try{await V.del('/api/auth/sessions/'+b.dataset.out);toast('Device signed out');devices(v)}catch(err){toast(err.message,'err')}}
  if(e.target.closest('#ro')){const ok=await confirmBox('Sign out all other devices',`This signs out ${others.length} other ${others.length===1?'device':'devices'}. You will stay signed in here.`,{ok:'Sign out others',danger:true});if(!ok)return;try{const r=await V.post('/api/auth/sessions/revoke-others');toast(`Signed out ${r.revoked} ${r.revoked===1?'device':'devices'}`);devices(v)}catch(err){toast(err.message,'err')}}}}

/* ---------------- notifications ---------------- */
async function notifications(v){
 const [np,pr]=await Promise.all([V.get('/api/notification-preferences'),V.get('/api/settings/preferences')]);const P2=np.preferences,ch=pr.channels,promo=P2.find(t=>t.type==='promotional'),rest=P2.filter(t=>t.type!=='promotional');
 const NL=t=>({kyc:'Identity verification'})[t]||label(t);
 const chRow=(icon,name,c,on)=>`<div class="ps-set"><span class="ps-ri">${ic(icon)}</span><div class="ps-st"><b>${name}</b><small>${esc(c.note||'Delivery is connected.')}</small></div>${chip(c.available?'Available':'Unavailable',c.available)}</div>`;
 v.innerHTML=`<div class="ps ps-sub">${pageHead('Notifications','Choose which updates you receive and where.',1)}<div class="ps-stack">
 ${card('bell','Delivery channels','What can actually be delivered right now.',chRow('mail','Email',ch.email)+chRow('bell','Push notifications',ch.push)+chRow('chat','SMS notifications',ch.sms))}
 ${card('mega','Marketing updates','Product news and offers. Never required to use your account.',`<div class="ps-set"><div class="ps-st"><b>Marketing emails</b><small>Security alerts and account notices are always sent, whatever you choose here.</small></div>${sw('promotional:email','Marketing emails',promo&&promo.email)}</div>`)}
 ${card('bell','Account notifications','Per category: in the app and by email.',`<div class="ps-nt-h" aria-hidden="true"><span></span><span>In-app</span><span>Email</span></div>`+rest.map(t=>`<div class="ps-nt"><div class="ps-st"><b>${esc(NL(t.type))}</b>${t.locked?'<small>Always on for your protection.</small>':''}</div>${sw(t.type+':in_app',NL(t.type)+' in-app',t.in_app,{dis:t.locked})}${sw(t.type+':email',NL(t.type)+' email',t.email)}</div>`).join(''))}
 <p class="ps-help" id="nlive" aria-live="polite"></p></div></div>`;
 v.onclick=async e=>{const b=e.target.closest('[data-sw]');if(!b||b.disabled)return;const [type,field]=b.dataset.sw.split(':'),cur=P2.find(t=>t.type===type),on=b.getAttribute('aria-checked')!=='true';setSw(b,on,true);
  try{await V.put('/api/notification-preferences',{preferences:[{type,in_app:cur.in_app,email:cur.email,[field]:on}]});cur[field]=on;setSw(b,on);$('#nlive').textContent=`${NL(type)} ${field==='email'?'email':'in-app'} ${on?'on':'off'}. Saved.`}catch(err){setSw(b,!on);toast(err.message||'Could not save that setting.','err')}}}

/* ---------------- preferences ---------------- */
async function preferences(v){
 const j=await V.get('/api/settings/preferences'),p=j.preferences,o=j.options;
 const sel=(id,l,opts,val,help)=>`<div class="ps-f"><label for="${id}">${l}</label><select id="${id}" ${opts.length<2?'disabled':''}>${opts.map(x=>`<option value="${x.value}" ${x.value===val?'selected':''}>${esc(x.label)}</option>`).join('')}</select><small class="ps-help">${esc(help)}</small></div>`;
 v.innerHTML=`<div class="ps ps-sub">${pageHead('Preferences','Customize your VAULTA experience.',1)}<div class="ps-stack">
 ${card('moon','Theme','Applies across your dashboard on every device you sign in on.',`<div class="ps-theme" role="radiogroup" aria-label="Theme mode">${o.theme.map(t=>`<button type="button" role="radio" aria-checked="${t.value===p.theme}" class="ps-tcard" data-theme="${t.value}"><span class="tp ${t.value}" aria-hidden="true"></span><b>${esc(t.label)}</b></button>`).join('')}</div>${o.theme.length<2?'<p class="ps-help">Light is the only theme right now. Dark mode needs every dashboard page, chart and the trading terminal restyled first, so it isn’t offered yet.</p>':''}<p class="ps-help" id="tlive" aria-live="polite"></p>`)}
 ${card('globe','Language & currency','',sel('lang','Language',o.language,p.language,'VAULTA is currently available in English only.')+sel('cur','Display currency',o.currency,p.currency,'All balances are held and shown in US dollars. Other display currencies need a live exchange-rate source and are not available yet. Your balances and transactions are never converted.'))}
 </div></div>`;
 v.onclick=async e=>{const b=e.target.closest('[data-theme]');if(!b)return;const t=b.dataset.theme,prev=VaultaTheme.get();applyTheme(t);$$('.ps-tcard',v).forEach(x=>x.setAttribute('aria-checked',String(x===b)));
  try{await V.put('/api/settings/preferences',{theme:t});me().theme=t;$('#tlive').textContent='Theme saved.'}catch(err){applyTheme(prev);$$('.ps-tcard',v).forEach(x=>x.setAttribute('aria-checked',String(x.dataset.theme===prev)));toast(err.message||'Could not save your theme.','err')}}}

/* ---------------- privacy & data ---------------- */
async function privacy(v){
 const [np]=await Promise.all([V.get('/api/notification-preferences')]);const promo=np.preferences.find(t=>t.type==='promotional'),sec=(new URLSearchParams((location.hash.split('?')[1])||'')).get('s');
 const cats=[['necessary','Strictly necessary','Keeps you signed in and your account secure. Always on.',true],['preferences','Preferences','Remember display choices.'],['analytics','Analytics','Help us understand usage and fix errors.'],['marketing','Marketing','Measure and personalise marketing.']];
 const cookies=()=>{const c=cookieStatus();return cats.map(([k,t,d,always])=>`<div class="ps-set"><div class="ps-st"><b>${t}</b><small>${d}</small></div>${chip(always?'Always on':!c?'Not chosen yet':c[k]?'Allowed':'Off',always||(c&&c[k]))}</div>`).join('')};
 v.innerHTML=`<div class="ps ps-sub">${pageHead('Privacy & Data','Clear choices about how your information is used.',1)}<div class="ps-stack">
 ${card('share','Data sharing','What you can and can’t control.',`<p class="ps-p">VAULTA has no optional data-sharing features for you to switch on or off. Information needed to run your account, keep it secure and meet legal and regulatory obligations is always processed and can’t be turned off. Read how it’s used in the <a href="/legal/privacy/" target="_blank" rel="noopener">Privacy Policy</a>.</p>`,'sharing')}
 ${card('cookie','Cookie preferences','Optional cookies stay off until you allow them.',`<div id="ck">${cookies()}</div><div class="ps-actions left"><button type="button" class="btn ghost" data-cookie-settings>Review cookie preferences</button></div><p class="ps-help">Changes apply immediately. Withdrawing consent stops optional tracking.</p>`,'cookies')}
 ${card('mega','Marketing permissions','',`<div class="ps-set"><div class="ps-st"><b>Marketing emails</b><small>Product news and offers. Security alerts and essential account notices are always sent.</small></div>${sw('promotional:email','Marketing emails',promo&&promo.email)}</div><p class="ps-help" id="mlive" aria-live="polite"></p>`,'marketing')}
 ${card('file','Your data','',`<div class="ps-list">${row({label:'Export my data',icon:'down',href:'#/profile/data'})}</div><p class="ps-p">Account closure isn’t available as a self-service action because financial records must be retained. To close your account, <a href="#/profile/help?s=contact">contact support</a>.</p>`,'data')}</div></div>`;
 if(window.VaultaConsent)VaultaConsent.onChange(()=>{const e=$('#ck');if(e)e.innerHTML=cookies()});
 v.onclick=async e=>{const b=e.target.closest('[data-sw]');if(!b)return;const on=b.getAttribute('aria-checked')!=='true';setSw(b,on,true);
  try{await V.put('/api/notification-preferences',{preferences:[{type:'promotional',in_app:promo.in_app,email:on}]});promo.email=on;setSw(b,on);$('#mlive').textContent='Marketing emails '+(on?'on':'off')+'. Saved.'}catch(err){setSw(b,!on);toast(err.message,'err')}};
 if(sec){const t=$('#'+sec);if(t)setTimeout(()=>t.scrollIntoView({block:'start'}),50)}}

async function dataPage(v){
 v.innerHTML=`<div class="ps ps-sub">${pageHead('Data Management','Download your information and find your records.',1)}<div class="ps-stack">
 ${card('down','Export my data','A copy of your personal data as a JSON file.',`<p class="ps-p">Includes your profile, preferences, notification settings, identity-verification status, legal acceptances, security activity, support conversations and transaction history. It never includes passwords, security keys or other people’s data.</p><div id="xm"></div><div class="ps-actions left"><button type="button" class="btn" id="xb">${ic('down',16)} Download my data</button></div>`)}
 ${card('cloud','Backups','',`<p class="ps-p">VAULTA operators maintain system backups and they aren’t user-accessible. The export above is your personal copy. A self-service restore isn’t offered.</p>`)}
 ${card('file','Other records','',`<div class="ps-list">${[{label:'Statements & receipts',icon:'file',href:'#/statements'},{label:'Transactions',icon:'clock',href:'#/transactions'}].map(row).join('')}<a class="ps-row" href="/api/portfolio/export"><span class="ps-ri">${ic('down')}</span><span class="ps-rl">Holdings (CSV)</span><span class="ps-rc">${ic('chev',16)}</span></a></div>`)}</div></div>`;
 $('#xb').onclick=async()=>{const b=$('#xb');$('#xm').innerHTML='';
  try{await busyBtn(b,async()=>{const r=await fetch('/api/settings/export',{credentials:'same-origin'});if(!r.ok){let m='The export failed.';try{m=(await r.json()).error.message}catch(e){}throw new Error(m)}
   const bl=await r.blob();if(!bl.size)throw new Error('The export was empty.');const a=document.createElement('a');a.href=URL.createObjectURL(bl);a.download=(/filename="([^"]+)"/.exec(r.headers.get('Content-Disposition')||'')||[])[1]||'vaulta-data-export.json';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),4000)},'Preparing…');
   $('#xm').innerHTML=alertBox('Your export was generated and the download started.','ok')}catch(e){$('#xm').innerHTML=alertBox(e.message||'The export failed.')}}}

/* ---------------- help & support ---------------- */
async function help(v){
 const t=(await V.get('/api/support/tickets')).tickets.slice(0,3),CATS=['account','deposit','withdrawal','investment','trading','kyc','security','technical'];
 v.innerHTML=`<div class="ps ps-sub">${pageHead('Help & Support','Find answers or get in touch with our team.',1)}<div class="ps-stack">
 ${card('help','Help Center','Guides and answers.',`<div class="ps-list">${[['Frequently asked questions','/resources/faq/'],['Guides','/resources/guides/'],['Security tips','/resources/security/'],['Investment education','/resources/investment-education/']].map(([l,h])=>`<a class="ps-row" href="${h}" target="_blank" rel="noopener"><span class="ps-rl">${l}</span><span class="ps-rc">${ic('chev',16)}</span></a>`).join('')}</div>`)}
 ${card('chat','Live chat','',`<div class="ps-set"><div class="ps-st"><b>Live chat with a support agent</b><small>Not available yet. Contact support below and we’ll reply in your ticket.</small></div>${chip('Unavailable',false)}</div>`)}
 ${card('head','Contact support','We reply inside your support ticket.',`<form id="sf" novalidate><div id="fmsg"></div><div class="ps-f"><label for="f-category">Category</label><select id="f-category" name="category">${CATS.map(c=>`<option value="${c}">${esc(label(c))}</option>`).join('')}</select></div>${field('subject','Subject',{max:140,req:true})}<div class="ps-f"><label for="f-message">Message <abbr aria-hidden="true">*</abbr></label><textarea id="f-message" name="message" rows="5" maxlength="4000" required aria-describedby="f-message-e"></textarea><small id="f-message-e" class="ps-err" role="alert"></small></div><div class="ps-actions"><button class="btn" id="sb">Send message</button></div></form>`,'contact')}
 ${t.length?card('file','Your recent tickets','',`<div class="ps-list">${t.map(x=>`<a class="ps-row" href="#/support"><span class="ps-rl">${esc(x.subject)}<small>${esc(x.ref)} · ${esc(dt(x.updated_at))}</small></span>${chip(label(x.status),x.status==='resolved'||x.status==='closed')}<span class="ps-rc">${ic('chev',16)}</span></a>`).join('')}</div><div class="ps-actions left"><a class="btn ghost sm" href="#/support">View all tickets</a></div>`):''}</div></div>`;
 const f=$('#sf');if(new URLSearchParams((location.hash.split('?')[1])||'').get('s')==='contact')setTimeout(()=>$('#contact').scrollIntoView(),50);
 f.onsubmit=async e=>{e.preventDefault();const s=f.elements.subject.value.trim(),m=f.elements.message.value.trim();setErr(f,'subject',s?'':'Enter a subject.');$('#f-message-e').textContent=m.length<10?'Tell us a little more (at least 10 characters).':'';if(!s||m.length<10)return;
  try{const r=await busyBtn($('#sb'),()=>V.post('/api/support/tickets',{category:f.elements.category.value,subject:s,message:m}),'Sending…');f.reset();$('#fmsg').innerHTML=alertBox(`Your request was received. Reference ${r.ref}. We’ll reply in your ticket.`,'ok')}catch(err){$('#fmsg').innerHTML=alertBox(err.message||'We couldn’t send your message. Please try again.')}}}

/* ---------------- routing ---------------- */
const PAGES={personal,security,password:async v=>pwPage(v),'two-factor':twofa,activity,devices,notifications,preferences,privacy,data:dataPage,help};
R.profile=(sub)=>load(async v=>{const k=(sub&&sub[0])||'';await(PAGES[k]||overview)(v)});
R.security=()=>{location.replace('#/profile/security')};
document.addEventListener('click',e=>{const a=e.target.closest&&e.target.closest('[data-act]');if(!a)return;if(a.dataset.act==='photo')pickPhoto();if(a.dataset.act==='rmphoto')removePhoto();
 const eye=e.target.closest('[data-eye]');if(eye)return});
document.addEventListener('click',e=>{const b=e.target.closest&&e.target.closest('[data-eye]');if(!b)return;const i=document.getElementById(b.dataset.eye),show=i.type==='password';i.type=show?'text':'password';b.setAttribute('aria-pressed',String(show));b.innerHTML=ic(show?'eyeoff':'eye',18)});
});
})();
