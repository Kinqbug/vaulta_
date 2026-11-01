/* Community chat panel for the Trade terminal. Persisted server-side; other clients receive messages by cursor polling (every ~3s while the tab is visible).
   Everything user-written is rendered with textContent (never innerHTML). The sender identity is derived by the server from the session. */
(()=>{
const $=(s,r=document)=>r.querySelector(s),$$=(s,r=document)=>[...r.querySelectorAll(s)];
const el=(t,c,txt)=>{const e=document.createElement(t);if(c)e.className=c;if(txt!=null)e.textContent=txt;return e};
const hhmm=s=>new Date(s).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
const dayl=s=>{const d=new Date(s),n=new Date();return d.toDateString()===n.toDateString()?'Today':d.toLocaleDateString([], {month:'short',day:'numeric'})};
const nonce=()=>Array.from(crypto.getRandomValues(new Uint8Array(12)),b=>b.toString(16).padStart(2,'0')).join('');
const REASONS=[['spam','Spam'],['scam','Scam or fraud'],['abuse','Abuse or harassment'],['impersonation','Impersonation'],['other','Other']];

class TChat{
 constructor(root,{V,UI,isMod=false,onChannel}){Object.assign(this,{root,V,UI,isMod,onChannel});this.channels=[];this.slug=null;this.msgs=[];this.ids=new Set();this.last=0;this.seen={};this.timer=null;this.cl=null;this.fails=0;this.state='connecting';this.replyTo=null;this.sending=false;this.destroyed=false;this.build()}
 build(){const r=this.root;r.innerHTML='';r.classList.add('tc');
  this.side=el('aside','tc-side');this.main=el('section','tc-main');
  this.side.innerHTML='<div class="tc-sh">Channels</div>';this.list=el('div','tc-list');this.list.setAttribute('role','listbox');this.list.setAttribute('aria-label','Chat channels');this.side.appendChild(this.list);
  const hd=el('header','tc-head');this.title=el('div','tc-title');this.status=el('span','tc-status');this.status.setAttribute('role','status');hd.append(this.title,this.status);
  this.feed=el('div','tc-feed');this.feed.setAttribute('role','log');this.feed.setAttribute('aria-live','polite');this.feed.tabIndex=0;
  this.more=el('button','tc-more','Load earlier messages');this.more.type='button';this.more.hidden=true;this.more.onclick=()=>this.older();
  this.replyBar=el('div','tc-reply');this.replyBar.hidden=true;
  const f=el('form','tc-form');this.ta=el('textarea','tc-input');this.ta.rows=1;this.ta.maxLength=500;this.ta.placeholder='Message the channel…';this.ta.setAttribute('aria-label','Message');this.cnt=el('span','tc-count','0/500');this.btn=el('button','tc-send','Send');this.btn.type='submit';f.append(this.ta,this.cnt,this.btn);this.form=f;
  this.note=el('div','tc-note');this.note.setAttribute('role','alert');
  this.main.append(hd,this.feed,this.replyBar,this.note,f);r.append(this.side,this.main);
  this.ta.addEventListener('input',()=>{this.cnt.textContent=this.ta.value.length+'/500';this.ta.style.height='auto';this.ta.style.height=Math.min(96,this.ta.scrollHeight)+'px'});
  this.ta.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();f.requestSubmit()}});
  f.addEventListener('submit',e=>{e.preventDefault();this.send()});
  this.vis=()=>{if(document.visibilityState==='visible')this.poll(true)};document.addEventListener('visibilitychange',this.vis);
  this.feed.addEventListener('click',e=>{const b=e.target.closest('[data-act]');if(b)this.act(b)});
  document.addEventListener('click',this.docClick=e=>{if(!e.target.closest('.tc-menu')&&!e.target.closest('[data-act=menu]'))$$('.tc-menu',this.root).forEach(m=>m.remove())})}
 destroy(){this.destroyed=true;clearTimeout(this.timer);clearInterval(this.cl);document.removeEventListener('visibilitychange',this.vis);document.removeEventListener('click',this.docClick);this.ctl&&this.ctl.abort();this.root.innerHTML=''}
 setState(s,txt){this.state=s;this.status.className='tc-status '+s;this.status.textContent=txt||({connecting:'Connecting…',connected:'Connected',reconnecting:'Reconnecting…',offline:'Unable to connect'}[s])}
 async init(slug){this.setState('connecting');await this.loadChannels();this.cl=setInterval(()=>this.loadChannels(true),20000);await this.open(slug||'general')}
 async loadChannels(quiet){try{const j=await this.V.get('/api/community/channels');this.channels=j.channels;this.renderChannels()}catch(e){if(!quiet)this.setState('offline')}}
 renderChannels(){this.list.innerHTML='';this.channels.forEach(c=>{const b=el('button','tc-ch'+(c.slug===this.slug?' on':''));b.type='button';b.setAttribute('role','option');b.setAttribute('aria-selected',c.slug===this.slug);b.dataset.slug=c.slug;b.title=c.description;
   b.append(el('span','tc-hash',c.kind==='announcement'?'📢'.replace('📢','!'):'#'),el('span','tc-cn',c.name));if(c.online)b.append(el('span','tc-on',String(c.online)));
   if(c.slug!==this.slug&&c.last_id>(this.seen[c.slug]??c.last_id))b.append(el('span','tc-dot'));
   b.onclick=()=>{this.open(c.slug);this.onChannel&&this.onChannel(c.slug,true)};this.list.appendChild(b)})}
 async open(slug){if(this.destroyed)return;clearTimeout(this.timer);this.ctl&&this.ctl.abort();this.slug=slug;this.msgs=[];this.ids=new Set();this.last=0;this.replyTo=null;this.replyBar.hidden=true;this.note.textContent='';
  const c=this.channels.find(x=>x.slug===slug);this.title.textContent=c?'# '+c.name:'# '+slug;this.feed.innerHTML='';this.feed.append(...[1,2,3].map(()=>el('div','tc-skel')));this.renderChannels();this.applyWrite(c);
  try{const j=await this.V.get(`/api/community/channels/${encodeURIComponent(slug)}/messages?limit=50&poll=1`);if(this.slug!==slug||this.destroyed)return;this.feed.innerHTML='';this.ingest(j.messages,false);this.more.hidden=!j.has_more;this.feed.prepend(this.more);this.presence(j.presence);this.applyWrite(c,j.can_write);this.setState('connected');this.fails=0;this.scroll(true);this.empty();this.schedule()}
  catch(e){if(this.slug!==slug)return;this.feed.innerHTML='';this.feed.append(el('div','tc-empty',e.status===404?'This channel does not exist.':'Could not load messages.'));this.setState('offline');this.fails++;this.schedule()}}
 applyWrite(c,can){const w=can!==undefined?can:(c&&c.can_write);this.ta.disabled=!w;this.btn.disabled=!w;this.ta.placeholder=w?'Message the channel…':(c&&c.read_only?'Only Vaulta staff can post in this channel.':'You can read here. Verify your email or wait for a mute to end to post.')}
 empty(){if(!this.msgs.length&&!$('.tc-empty',this.feed))this.feed.append(el('div','tc-empty','No messages yet. Start the conversation.'))}
 presence(p){if(!p)return;this.online=p;const c=this.title;let o=$('.tc-pres',this.main);if(!o){o=el('span','tc-pres');this.status.before(o)}o.textContent=p.count+' online';o.title=p.users.map(u=>u.name).join(', ')||'No one else is here right now'}
 schedule(){clearTimeout(this.timer);if(this.destroyed)return;const d=this.fails?Math.min(30000,3000*2**this.fails):(document.visibilityState==='visible'?3000:15000);this.timer=setTimeout(()=>this.poll(),d)}
 async poll(now){if(this.destroyed||!this.slug)return;if(this.busy)return;this.busy=true;const slug=this.slug;
  try{const j=await this.V.get(`/api/community/channels/${encodeURIComponent(slug)}/messages?after=${this.last}&poll=1`);if(slug!==this.slug||this.destroyed)return;if(this.state!=='connected'){this.setState('connected')}this.fails=0;const stick=this.nearBottom();const n=this.ingest(j.messages,true);this.presence(j.presence);if(n&&stick)this.scroll();this.seen[slug]=this.last}
  catch(e){this.fails++;this.setState(this.fails>2?'offline':'reconnecting')}finally{this.busy=false;this.schedule()}}
 ingest(list,append){let n=0;list.forEach(m=>{if(this.ids.has(m.id)){return}this.ids.add(m.id);this.msgs.push(m);if(m.id>this.last)this.last=m.id;n++;const old=$(`[data-id="${m.id}"]`,this.feed);if(!old)this.feed.appendChild(this.node(m))});if(n&&$('.tc-empty',this.feed))$('.tc-empty',this.feed).remove();this.seen[this.slug]=this.last;return n}
 node(m){const li=el('div','tc-msg'+(m.mine?' mine':''));li.dataset.id=m.id;
  if(m.removed){li.classList.add('removed');li.append(el('em',null,'Message removed'));return li}
  const av=el('span','tc-av',m.user.initials);if(m.user.staff)av.classList.add('staff');
  const body=el('div','tc-body');const top=el('div','tc-top');const nm=el('b','tc-name',m.user.name);top.append(nm);if(m.user.staff)top.append(el('span','tc-badge','Staff'));top.append(el('time','tc-time',hhmm(m.created_at)));
  const menu=el('button','tc-dots','⋯');menu.type='button';menu.dataset.act='menu';menu.setAttribute('aria-label','Message actions');top.append(menu);body.append(top);
  if(m.reply_to){const q=el('div','tc-quote');q.append(el('b',null,m.reply_to.name+' '),document.createTextNode(m.reply_to.snippet));body.append(q)}
  body.append(el('div','tc-text',m.content));li.append(av,body);li._m=m;return li}
 nearBottom(){const f=this.feed;return f.scrollHeight-f.scrollTop-f.clientHeight<80}
 scroll(force){this.feed.scrollTop=this.feed.scrollHeight}
 async older(){const first=this.msgs[0];if(!first)return;this.more.disabled=true;try{const j=await this.V.get(`/api/community/channels/${encodeURIComponent(this.slug)}/messages?limit=50&before=${first.id}`);const h=this.feed.scrollHeight;const frag=document.createDocumentFragment();j.messages.forEach(m=>{if(this.ids.has(m.id))return;this.ids.add(m.id);this.msgs.unshift(m)});j.messages.forEach(m=>{if(!$(`[data-id="${m.id}"]`,this.feed))frag.appendChild(this.node(m))});this.more.after(frag);this.more.hidden=!j.has_more;this.feed.scrollTop+=this.feed.scrollHeight-h}catch(e){this.note.textContent='Could not load earlier messages.'}finally{this.more.disabled=false}}
 async send(){if(this.sending)return;const text=this.ta.value.trim();if(!text)return;this.sending=true;this.btn.disabled=true;this.note.textContent='';const n=nonce();
  try{const j=await this.V.post(`/api/community/channels/${encodeURIComponent(this.slug)}/messages`,{content:text,nonce:n,reply_to:this.replyTo||undefined});this.ta.value='';this.ta.style.height='auto';this.cnt.textContent='0/500';this.replyTo=null;this.replyBar.hidden=true;this.ingest([j.message],true);this.scroll(true);this.fails=0;this.setState('connected')}
  catch(e){this.note.textContent=e.message||'Message not sent.';if(!e.status||e.status>=500)this.setState('reconnecting','Not sent — check your connection')}
  finally{this.sending=false;const c=this.channels.find(x=>x.slug===this.slug);this.btn.disabled=this.ta.disabled;this.ta.focus()}}
 act(b){const li=b.closest('.tc-msg');if(!li||!li._m)return;const m=li._m;$$('.tc-menu',this.root).forEach(x=>x.remove());const menu=el('div','tc-menu');menu.setAttribute('role','menu');
  const item=(label,fn,danger)=>{const x=el('button',danger?'danger':'',label);x.type='button';x.setAttribute('role','menuitem');x.onclick=()=>{menu.remove();fn()};menu.append(x)};
  item('Reply',()=>{this.replyTo=m.id;this.replyBar.hidden=false;this.replyBar.textContent='Replying to '+m.user.name+': '+m.content.slice(0,60);const x=el('button','tc-x','×');x.type='button';x.setAttribute('aria-label','Cancel reply');x.onclick=()=>{this.replyTo=null;this.replyBar.hidden=true};this.replyBar.append(x);this.ta.focus()});
  if(m.mine)item('Delete my message',()=>this.del(m.id));else{item('Report…',()=>this.report(m));item('Block '+m.user.name,()=>this.block(m))}
  if(this.isMod&&!m.mine){item('Remove (moderator)',()=>this.del(m.id,true),true);item('Mute user 1 hour',()=>this.mute(m,60),true)}
  li.append(menu);menu.querySelector('button').focus()}
 async del(id,mod){try{await this.V.del(`/api/community/messages/${id}`,mod?{reason:'removed by moderator'}:undefined);const li=$(`[data-id="${id}"]`,this.feed);if(li){const n=this.node({id,removed:true});li.replaceWith(n)}}catch(e){this.note.textContent=e.message}}
 report(m){const UI=this.UI;const d=UI.modal('Report message',`<p class="note">Reports go to Vaulta moderators. Reporting does not reveal who you are to the author.</p><form id="rf"><label class="f">Reason<select name="reason">${REASONS.map(r=>`<option value="${r[0]}">${r[1]}</option>`).join('')}</select></label><label class="f">Details (optional)<input name="details" maxlength="300"></label><p class="neg" id="rm" role="alert"></p><div class="actions"><button class="btn">Send report</button></div></form>`);
  $('#rf',d).onsubmit=async e=>{e.preventDefault();const f=UI.formData(e.target);try{await this.V.post(`/api/community/messages/${m.id}/report`,{reason:f.reason,details:f.details});d.close();UI.toast('Report sent. Thank you.')}catch(err){$('#rm',d).textContent=err.message}}}
 async block(m){try{await this.V.put(`/api/community/blocks/${m.user.id}`,{});this.UI.toast(m.user.name+' blocked. You will no longer see their messages.');$$('.tc-msg',this.feed).forEach(x=>{if(x._m&&x._m.user&&x._m.user.id===m.user.id)x.remove()})}catch(e){this.note.textContent=e.message}}
 async mute(m,mins){try{await this.V.post('/api/community/moderation/mute',{user_id:m.user.id,minutes:mins,reason:'muted from chat'});this.UI.toast(m.user.name+' muted for '+mins+' minutes.')}catch(e){this.note.textContent=e.message}}
}
window.TChat=TChat;
})();
