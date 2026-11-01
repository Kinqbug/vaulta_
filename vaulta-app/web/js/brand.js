/* Single source of truth for VAULTA brand assets used by the JS-rendered dashboards (customer + admin).
   Static pages carry equivalent markup; both point at the same files in /assets/brand/. */
window.BRAND=(()=>{const B='/assets/brand/';
 const A={white:{src:B+'vaulta-logo-white.png',w:720,h:105},black:{src:B+'vaulta-logo-black.png',w:720,h:117},mark:{src:B+'vaulta-mark-white.png',w:256,h:196}};
 return{name:'VAULTA',home:'/index.html',favicon:B+'vaulta-favicon.png',assets:A,
  /* variant: 'white' (dark backgrounds) | 'black' (light backgrounds) | 'mark' (white V icon only, dark backgrounds) */
  img:(v,cls='')=>{const a=A[v];return `<img class="logo-img ${cls}" src="${a.src}" width="${a.w}" height="${a.h}" alt="${v==='mark'?'':'VAULTA'}"${v==='mark'?' aria-hidden="true"':''}>`}}})();
