/* VAULTA API client. The server is the source of truth: no balances, permissions or auth state live in localStorage. */
(()=>{
const state={csrf:null,user:null,mode:'internal'};
class ApiError extends Error{constructor(status,body){super(body?.error?.message||'Request failed');this.status=status;this.code=body?.error?.code;this.details=body?.error?.details}}
const uuid=()=>crypto.randomUUID?crypto.randomUUID():String(Date.now())+Math.random();
async function call(method,url,data,opt={}){
 const h={'Accept':'application/json'};let body;
 if(data instanceof FormData)body=data;else if(data!==undefined){h['Content-Type']='application/json';body=JSON.stringify(data)}
 if(method!=='GET'&&state.csrf)h['X-CSRF-Token']=state.csrf;
 if(opt.idem)h['Idempotency-Key']=opt.idem;
 let r;try{r=await fetch(url,{method,headers:h,body,credentials:'same-origin'})}catch(e){throw new ApiError(0,{error:{code:'network',message:'Cannot reach the server. Check your connection and try again.'}})}
 if(opt.raw)return r;
 const j=await r.json().catch(()=>null);
 if(!r.ok)throw new ApiError(r.status,j);
 return j}
const V={state,ApiError,uuid,
 get:(u)=>call('GET',u),post:(u,d,o)=>call('POST',u,d??{},o),put:(u,d)=>call('PUT',u,d),patch:(u,d)=>call('PATCH',u,d),del:(u)=>call('DELETE',u),upload:(u,fd)=>call('POST',u,fd),
 async session(){try{const j=await call('GET','/api/auth/me');state.csrf=j.csrf_token;state.user=j.user;state.mode=j.mode;return j.user}catch(e){if(e.status===401){state.user=null;return null}throw e}},
 async login(email,password,code){const j=await call('POST','/api/auth/login',{email,password,code});state.csrf=j.csrf_token;state.user=j.user;state.mode=j.mode;return j},
 async register(b){const j=await call('POST','/api/auth/register',b);state.csrf=j.csrf_token;state.user=j.user;return j},
 async logout(){try{await call('POST','/api/auth/logout',{})}finally{state.user=null;state.csrf=null}},
 /* money-moving POSTs carry an Idempotency-Key so a double-click or retry can never create a second transaction */
 money:(u,d)=>call('POST',u,d,{idem:uuid()})};
window.VApi=V})();
