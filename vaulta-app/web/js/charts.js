const NS='http://www.w3.org/2000/svg';
const Charts={
line(svg){const d=svg.dataset.chart.split(',').map(Number),c=svg.dataset.color||'#0A0A0A',max=Math.max(...d),min=Math.min(...d);
const pts=d.map((v,i)=>[i*(200/(d.length-1)),66-((v-min)/(max-min||1))*58]);
const p=pts.map((q,i)=>(i?'L':'M')+q[0].toFixed(1)+' '+q[1].toFixed(1)).join('');
svg.innerHTML=`<path d="${p}L200 70L0 70Z" fill="${c}" opacity=".06" stroke="none"/><path class="ln" d="${p}" stroke="${c}"/>`;
const l=svg.querySelector('.ln'),len=l.getTotalLength();l.style.strokeDasharray=len;l.style.strokeDashoffset=len},
draw(svg){const l=svg.querySelector('.ln');if(!l)return;l.style.transition='stroke-dashoffset 1.6s ease';l.style.strokeDashoffset=0},
donut(svg){const v=svg.dataset.donut.split(',').map(Number),t=v.reduce((a,b)=>a+b),g=['#0A0A0A','#333','#666','#A3A3A3','#20B26B','#D4D4D4'];let o=0;
v.forEach((x,i)=>{const c=document.createElementNS(NS,'circle'),p=x/t*100;c.setAttribute('cx',21);c.setAttribute('cy',21);c.setAttribute('r',15.9);c.setAttribute('fill','none');c.setAttribute('stroke',g[i]);c.setAttribute('stroke-width',5);c.setAttribute('stroke-dasharray',`${p-.6} ${100-p+.6}`);c.setAttribute('stroke-dashoffset',-o);svg.appendChild(c);o+=p})}};
document.querySelectorAll('svg[data-chart]').forEach(Charts.line);
document.querySelectorAll('svg[data-donut]').forEach(Charts.donut);
