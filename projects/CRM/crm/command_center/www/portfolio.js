/* Portfolio tabs: keep the current CRM view when switching business; "+" adds one (owner only). */
(()=>{
 document.addEventListener('DOMContentLoaded',()=>{
  const strip=document.querySelector('.ws-biz-tabs'),active=strip?.querySelector('[aria-current=page]');
  if(active&&active.offsetLeft+active.offsetWidth>strip.clientWidth)strip.scrollLeft=active.offsetLeft-16;
 });
 document.addEventListener('click',event=>{
  const tab=event.target.closest('.ws-biz-tab:not(.ws-biz-all)');
  if(tab&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey){
   const view=new URLSearchParams(location.search).get('view');
   const onCrm=!/\/strategy$/.test(location.pathname);
   if(view&&onCrm){event.preventDefault();location.href=tab.getAttribute('href')+'?view='+encodeURIComponent(view);}
   return;
  }
  const dialog=document.getElementById('portfolio-add');
  if(!dialog)return;
  if(event.target.closest('.ws-biz-add')){dialog.querySelector('.ws-biz-error').textContent='';dialog.showModal();dialog.querySelector('input[name=label]').focus();}
  if(event.target.closest('[data-portfolio-cancel]'))dialog.close();
 });
 document.addEventListener('submit',async event=>{
  const form=event.target.closest('#portfolio-add form');
  if(!form)return;
  event.preventDefault();
  const error=form.querySelector('.ws-biz-error'),button=form.querySelector('[type=submit]');
  const data=Object.fromEntries(new FormData(form));
  button.disabled=true;error.textContent='';
  try{
   const response=await fetch(form.dataset.addUrl,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({label:data.label,source:data.source,color:data.color||''}),signal:AbortSignal.timeout(15000)});
   const body=await response.json().catch(()=>({}));
   if(!response.ok)throw new Error(body.error||'The business was not added. Try again.');
   location.href=body.url;
  }catch(e){error.textContent=e.message||'The business was not added. Try again.';button.disabled=false;}
 });
})();
/* Linked people (P4): "Also in" on an open contact, from the portfolio's link file. Only records this
   person can read in their own tabs come back; the owner can find, link and unlink. */
(()=>{
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const box=()=>document.querySelector('.ws-portfolio');
 const contactId=()=>new URLSearchParams(location.search).get('contact')||'';
 let cache={id:'',data:null,suggest:null,error:''},busy=false;
 async function load(id){
  const p=box();const url=p.dataset.linksUrl+'?'+new URLSearchParams({business:p.dataset.business,contact:id});
  const r=await fetch(url,{signal:AbortSignal.timeout(15000)});const body=await r.json().catch(()=>({}));
  if(!r.ok)throw new Error(body.error||'Links could not load.');return body;
 }
 function html(id){
  const d=cache.data;if(!d||(!d.also_in.length&&!d.can_link))return '';
  const chips=d.also_in.map(x=>`<li style="--tab:${esc(x.color)}"><a href="${esc(x.url)}"><span class="ws-biz-dot" aria-hidden="true"></span>${esc(x.label)} · ${esc(x.name)}</a>${d.can_link?`<button type="button" data-link-unlink data-business="${esc(x.business)}" data-contact="${esc(x.contact_id)}">Unlink</button>`:''}</li>`).join('');
  const sug=cache.suggest?(cache.suggest.length?`<ul class="ws-also-list">${cache.suggest.map(x=>`<li style="--tab:${esc(x.color)}"><span><span class="ws-biz-dot" aria-hidden="true"></span>${esc(x.label)} · ${esc(x.name)} <small>${esc(x.why)}</small></span><button type="button" data-link-add data-business="${esc(x.business)}" data-contact="${esc(x.contact_id)}">Link</button></li>`).join('')}</ul>`:'<p class="muted">No match by email, phone or name in your other businesses.</p>'):'';
  return `<section class="panel ws-also" data-for="${esc(id)}"><div class="top"><h2>Also in</h2>${d.can_link?'<button type="button" data-link-find>Find in other businesses</button>':''}</div>`+
   (chips?`<ul class="ws-also-list">${chips}</ul>`:'<p class="muted">Not linked to a record in another business.</p>')+sug+
   `<p class="ws-also-error" role="alert">${esc(cache.error)}</p></section>`;
 }
 function place(){
  const details=document.getElementById('record-details'),id=contactId();
  if(!box()?.dataset.linksUrl||!details||!id){return;}
  if(cache.id!==id){cache={id,data:null,suggest:null,error:''};if(!busy){busy=true;load(id).then(d=>{if(cache.id===id)cache.data=d;}).catch(()=>{}).finally(()=>{busy=false;place();});}return;}
  const have=document.querySelector('.ws-also');
  if(have&&have.dataset.for===id&&have.isConnected&&!have.dataset.stale)return;
  have?.remove();const markup=html(id);if(markup)details.insertAdjacentHTML('afterend',markup);
 }
 async function post(body){
  const p=box(),r=await fetch(p.dataset.linksUrl,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(15000)});
  const out=await r.json().catch(()=>({}));if(!r.ok)throw new Error(out.error||'That did not work. Try again.');
 }
 async function act(fn){
  try{cache.error='';await fn();cache.data=await load(cache.id);}catch(e){cache.error=e.message;}
  document.querySelector('.ws-also')?.setAttribute('data-stale','1');place();
 }
 document.addEventListener('click',e=>{
  const id=cache.id,biz=box()?.dataset.business;
  if(e.target.closest('[data-link-find]'))act(async()=>{const p=box();const r=await fetch(p.dataset.linksUrl+'/suggest?'+new URLSearchParams({business:biz,contact:id}));const b=await r.json().catch(()=>({}));if(!r.ok)throw new Error(b.error||'Matches could not load.');cache.suggest=b.suggestions;});
  const add=e.target.closest('[data-link-add]');
  if(add)act(async()=>{await post({action:'link',a:{business:biz,contact:id},b:{business:add.dataset.business,contact:add.dataset.contact}});cache.suggest=null;});
  const un=e.target.closest('[data-link-unlink]');
  if(un)act(()=>post({action:'unlink',business:un.dataset.business,contact:un.dataset.contact}));
 });
 document.addEventListener('DOMContentLoaded',()=>{
  const main=document.getElementById('content');if(!main||!box()?.dataset.linksUrl)return;
  new MutationObserver(()=>place()).observe(main,{childList:true,subtree:true});place();
 });
})();
/* Export one business (P6): the owner's account menu gets "Export this business" (a handover ZIP). */
(()=>{
 document.addEventListener('DOMContentLoaded',()=>{
  const p=document.querySelector('.ws-portfolio'),menu=document.querySelector('.ws-account > div');
  if(!p?.dataset.exportUrl||!p.dataset.business||!menu)return;
  menu.insertAdjacentHTML('beforeend','<div class="ws-export"><small>This business</small><button type="button" data-business-export>Export this business</button><p class="ws-export-note" role="status"></p></div>');
 });
 document.addEventListener('click',async e=>{
  const b=e.target.closest('[data-business-export]');if(!b)return;
  const p=document.querySelector('.ws-portfolio'),note=document.querySelector('.ws-export-note');
  b.disabled=true;note.textContent='Building the handover file…';
  try{
   const r=await fetch(p.dataset.exportUrl,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({business:p.dataset.business}),signal:AbortSignal.timeout(60000)});
   if(!r.ok){const out=await r.json().catch(()=>({}));throw new Error(out.error||'The export did not work. Try again.');}
   const name=(r.headers.get('Content-Disposition')||'').match(/filename="([^"]+)"/)?.[1]||'handover.zip';
   const url=URL.createObjectURL(await r.blob()),a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();
   setTimeout(()=>URL.revokeObjectURL(url),1000);note.textContent='Saved '+name+'.';
  }catch(err){note.textContent=err.message;}finally{b.disabled=false;}
 });
})();
