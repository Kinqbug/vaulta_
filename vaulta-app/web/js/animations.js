const reduce=matchMedia('(prefers-reduced-motion:reduce)').matches;
const io=new IntersectionObserver(es=>es.forEach(e=>{if(!e.isIntersecting)return;const t=e.target;t.classList.add('in');
t.querySelectorAll('.bar i').forEach(b=>b.style.width=b.dataset.w+'%');
t.querySelectorAll('svg[data-chart]').forEach(Charts.draw);
t.querySelectorAll('[data-count]').forEach(countUp);io.unobserve(t)}),{threshold:.15});
document.querySelectorAll('.rise,.sec,.hero,.stats').forEach(el=>io.observe(el));
function countUp(el){const to=+el.dataset.count,dec=+el.dataset.dec||0,pre=el.dataset.prefix||'',suf=el.dataset.suffix||'';
if(reduce){el.textContent=pre+to.toFixed(dec)+suf;return}let s=null;
const f=t=>{s??=t;const p=Math.min((t-s)/1600,1);el.textContent=pre+(to*(1-Math.pow(1-p,3))).toFixed(dec)+suf;p<1&&requestAnimationFrame(f)};requestAnimationFrame(f)}
