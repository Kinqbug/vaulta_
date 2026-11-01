/* Dependency-free canvas chart for the Trade terminal: candlesticks (real OHLC only), line and area, crosshair, wheel-zoom, drag-pan, responsive.
   (TradingView Lightweight Charts could not be installed in this offline build; this component has the same job and can be swapped for it.) */
(()=>{
const css=getComputedStyle(document.documentElement);
class TChart{
 constructor(el,opts={}){this.el=el;this.mode='candles';this.data=[];this.opts=Object.assign({up:'#26d9a0',down:'#f0447f',line:'#8b7bff',grid:'rgba(255,255,255,.06)',text:'#8b8b95',bg:'transparent',fmt:v=>String(v)},opts);
  this.cv=document.createElement('canvas');this.cv.className='tm-canvas';this.cv.setAttribute('role','img');this.cv.setAttribute('aria-label','Price chart');this.cv.tabIndex=0;el.appendChild(this.cv);this.tip=document.createElement('div');this.tip.className='tm-tip';this.tip.hidden=true;el.appendChild(this.tip);
  this.ctx=this.cv.getContext('2d');this.view={from:0,to:0};this.hover=null;this.drag=null;this._bind();this.ro=new ResizeObserver(()=>this.resize());this.ro.observe(el);this.resize()}
 destroy(){this.ro.disconnect();['mousemove','mouseleave','mousedown','wheel','touchstart','touchmove','touchend','keydown'].forEach(n=>this.cv.removeEventListener(n,this['_h_'+n]));window.removeEventListener('mouseup',this._up);window.removeEventListener('mousemove',this._mv);this.cv.remove();this.tip.remove();this.data=[];this.destroyed=true}
 setMode(m){this.mode=m;this.draw()}
 setData(d){this.data=d||[];this.reset()}
 reset(){const n=this.data.length;this.view={from:0,to:Math.max(1,n)};this.hover=null;this.draw()}
 resize(){const r=this.el.getBoundingClientRect(),dpr=window.devicePixelRatio||1;this.w=Math.max(120,Math.floor(r.width));this.h=Math.max(160,Math.floor(r.height));this.cv.width=this.w*dpr;this.cv.height=this.h*dpr;this.cv.style.width=this.w+'px';this.cv.style.height=this.h+'px';this.ctx.setTransform(dpr,0,0,dpr,0,0);this.draw()}
 _bind(){
  const pos=e=>{const r=this.cv.getBoundingClientRect(),t=e.touches?e.touches[0]:e;return {x:t.clientX-r.left,y:t.clientY-r.top}};
  this._h_mousemove=e=>{if(this.drag)return;this._hover(pos(e).x,pos(e).y)};this._h_mouseleave=()=>{this.hover=null;this.tip.hidden=true;this.draw()};
  this._h_mousedown=e=>{this.drag={x:pos(e).x,from:this.view.from,to:this.view.to};this.cv.classList.add('grab')};
  this._mv=e=>{if(!this.drag)return;const dx=pos(e).x-this.drag.x,span=this.drag.to-this.drag.from,per=this.plot.w/span,sh=-dx/per;this._setView(this.drag.from+sh,this.drag.to+sh)};
  this._up=()=>{this.drag=null;this.cv.classList.remove('grab')};
  this._h_wheel=e=>{if(!this.data.length)return;e.preventDefault();const p=pos(e),f=Math.min(1,Math.max(0,(p.x-this.plot.x)/this.plot.w)),span=this.view.to-this.view.from,ns=Math.min(this.data.length,Math.max(8,span*(e.deltaY>0?1.15:.87))),c=this.view.from+span*f;this._setView(c-ns*f,c+ns*(1-f))};
  this._h_touchstart=e=>{this.drag={x:pos(e).x,from:this.view.from,to:this.view.to};this._hover(pos(e).x,pos(e).y)};this._h_touchmove=e=>{if(!this.drag)return;const dx=pos(e).x-this.drag.x,per=this.plot.w/(this.drag.to-this.drag.from);this._setView(this.drag.from-dx/per,this.drag.to-dx/per);this._hover(pos(e).x,pos(e).y)};this._h_touchend=()=>{this.drag=null};
  this._h_keydown=e=>{const s=(this.view.to-this.view.from)*.1;if(e.key==='ArrowLeft'){this._setView(this.view.from-s,this.view.to-s);e.preventDefault()}if(e.key==='ArrowRight'){this._setView(this.view.from+s,this.view.to+s);e.preventDefault()}if(e.key==='+'){this._zoom(.8)}if(e.key==='-'){this._zoom(1.25)}if(e.key==='0')this.reset()};
  this.cv.addEventListener('mousemove',this._h_mousemove);this.cv.addEventListener('mouseleave',this._h_mouseleave);this.cv.addEventListener('mousedown',this._h_mousedown);window.addEventListener('mousemove',this._mv);window.addEventListener('mouseup',this._up);
  this.cv.addEventListener('wheel',this._h_wheel,{passive:false});this.cv.addEventListener('touchstart',this._h_touchstart,{passive:true});this.cv.addEventListener('touchmove',this._h_touchmove,{passive:true});this.cv.addEventListener('touchend',this._h_touchend);this.cv.addEventListener('keydown',this._h_keydown)}
 _zoom(f){const c=(this.view.from+this.view.to)/2,s=(this.view.to-this.view.from)*f/2;this._setView(c-s,c+s)}
 _setView(a,b){const n=this.data.length,span=Math.min(n,Math.max(8,b-a));a=Math.max(-span*.2,Math.min(a,n-span*.8));this.view={from:a,to:a+span};this.draw()}
 _hover(x,y){if(!this.data.length)return;const i=Math.round(this.view.from+(x-this.plot.x)/this.plot.w*(this.view.to-this.view.from)-.5);this.hover=(i>=0&&i<this.data.length&&x>=this.plot.x&&x<=this.plot.x+this.plot.w)?{i,x,y}:null;this.draw();this._tip()}
 _tip(){const t=this.tip;if(!this.hover){t.hidden=true;return}const d=this.data[this.hover.i],f=this.opts.fmt,dt=new Date(d.t*1000);
  t.innerHTML=`<b>${dt.toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})}</b><span>O ${f(d.o)}</span><span>H ${f(d.h)}</span><span>L ${f(d.l)}</span><span class="${d.c>=d.o?'up':'dn'}">C ${f(d.c)}</span>`;t.hidden=false;const left=this.hover.x>this.w/2?this.hover.x-t.offsetWidth-14:this.hover.x+14;t.style.left=Math.max(4,left)+'px';t.style.top='8px'}
 draw(){
  const c=this.ctx,o=this.opts,W=this.w,H=this.h;c.clearRect(0,0,W,H);const padR=80,padB=24,padT=10,padL=6;this.plot={x:padL,y:padT,w:W-padL-padR,h:H-padT-padB};const P=this.plot;
  if(!this.data.length){c.fillStyle=o.text;c.font='13px system-ui,sans-serif';c.textAlign='center';c.fillText(this.emptyText||'No chart data',W/2,H/2);return}
  const a=Math.max(0,Math.floor(this.view.from)),b=Math.min(this.data.length,Math.ceil(this.view.to)),vis=this.data.slice(a,b);if(!vis.length)return;
  let lo=Infinity,hi=-Infinity;vis.forEach(d=>{lo=Math.min(lo,this.mode==='candles'?d.l:d.c);hi=Math.max(hi,this.mode==='candles'?d.h:d.c)});if(lo===hi){lo*=.99;hi*=1.01}const pad=(hi-lo)*.08;lo-=pad;hi+=pad;
  const span=this.view.to-this.view.from,bw=P.w/span,X=i=>P.x+(i+.5-this.view.from)*bw,Y=v=>P.y+(hi-v)/(hi-lo)*P.h;this.X=X;
  c.font='11px ui-monospace,Menlo,monospace';c.textBaseline='middle';c.lineWidth=1;
  const steps=5;for(let k=0;k<=steps;k++){const v=lo+(hi-lo)*k/steps,y=Y(v);c.strokeStyle=o.grid;c.beginPath();c.moveTo(P.x,Math.round(y)+.5);c.lineTo(P.x+P.w,Math.round(y)+.5);c.stroke();c.fillStyle=o.text;c.textAlign='left';c.fillText(o.fmt(v),P.x+P.w+6,y)}
  const tcount=Math.max(2,Math.floor(P.w/110));c.textAlign='center';c.textBaseline='top';for(let k=0;k<tcount;k++){const i=Math.floor(a+(b-a-1)*k/(tcount-1)),d=this.data[i];if(!d)continue;const dt=new Date(d.t*1000),x=X(i);c.strokeStyle=o.grid;c.beginPath();c.moveTo(Math.round(x)+.5,P.y);c.lineTo(Math.round(x)+.5,P.y+P.h);c.stroke();c.fillStyle=o.text;
   const long=(this.data[this.data.length-1].t-this.data[0].t)>3*86400;c.fillText(long?dt.toLocaleDateString([], {month:'short',day:'numeric'}):dt.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}),Math.min(Math.max(x,P.x+24),P.x+P.w-24),P.y+P.h+6)}
  c.save();c.beginPath();c.rect(P.x,P.y,P.w,P.h);c.clip();
  if(this.mode==='candles'){const w=Math.max(1,Math.min(bw*.7,22));vis.forEach((d,k)=>{const i=a+k,x=X(i),up=d.c>=d.o,col=up?o.up:o.down;c.strokeStyle=col;c.fillStyle=col;c.beginPath();c.moveTo(Math.round(x)+.5,Y(d.h));c.lineTo(Math.round(x)+.5,Y(d.l));c.stroke();const y1=Y(Math.max(d.o,d.c)),y2=Y(Math.min(d.o,d.c));c.fillRect(Math.round(x-w/2),y1,Math.max(1,Math.round(w)),Math.max(1,y2-y1))})}
  else{c.beginPath();vis.forEach((d,k)=>{const x=X(a+k),y=Y(d.c);k?c.lineTo(x,y):c.moveTo(x,y)});c.strokeStyle=o.line;c.lineWidth=2;c.lineJoin='round';c.stroke();
   if(this.mode==='area'){const g=c.createLinearGradient(0,P.y,0,P.y+P.h);g.addColorStop(0,'rgba(139,123,255,.35)');g.addColorStop(1,'rgba(139,123,255,0)');c.lineTo(X(a+vis.length-1),P.y+P.h);c.lineTo(X(a),P.y+P.h);c.closePath();c.fillStyle=g;c.fill()}c.lineWidth=1}
  c.restore();
  const last=this.data[this.data.length-1],ly=Y(last.c),up=last.c>=last.o;if(ly>=P.y&&ly<=P.y+P.h){c.setLineDash([4,4]);c.strokeStyle=up?o.up:o.down;c.beginPath();c.moveTo(P.x,Math.round(ly)+.5);c.lineTo(P.x+P.w,Math.round(ly)+.5);c.stroke();c.setLineDash([]);c.fillStyle=up?o.up:o.down;c.fillRect(P.x+P.w+1,ly-9,padR-2,18);c.fillStyle='#000';c.textAlign='left';c.textBaseline='middle';c.fillText(o.fmt(last.c),P.x+P.w+5,ly)}
  if(this.hover){const d=this.data[this.hover.i],x=X(this.hover.i);c.strokeStyle='rgba(255,255,255,.35)';c.setLineDash([3,3]);c.beginPath();c.moveTo(Math.round(x)+.5,P.y);c.lineTo(Math.round(x)+.5,P.y+P.h);c.moveTo(P.x,Math.round(this.hover.y)+.5);c.lineTo(P.x+P.w,Math.round(this.hover.y)+.5);c.stroke();c.setLineDash([]);
   const pv=hi-(this.hover.y-P.y)/P.h*(hi-lo);if(this.hover.y>=P.y&&this.hover.y<=P.y+P.h){c.fillStyle='#2b2b33';c.fillRect(P.x+P.w+1,this.hover.y-9,padR-2,18);c.fillStyle='#fff';c.textAlign='left';c.textBaseline='middle';c.fillText(o.fmt(pv),P.x+P.w+5,this.hover.y)}}
 }
}
window.TChart=TChart;
})();
