/* Account preferences (favorites, saved views) live in the CRM, private to one person, on every device. */
window.WorkspacePrefs=(()=>{
 const base=document.baseURI;let data=null,loading=null,started=null,person='',offline=false;
 const key=()=>'crm.prefs.v1:'+base+':'+person;
 const stamp=()=>JSON.stringify(Object.entries(data||{}).map(([k,v])=>[k,v.revision]).sort());
 function cache(){try{if(person){localStorage.setItem(key(),JSON.stringify(data));localStorage.setItem('crm.prefs.person:'+base,person);}}catch{}}
 async function fetchAll(){
  const response=await fetch('api/crm/preferences',{cache:'no-store',signal:AbortSignal.timeout(10000)});
  if(!response.ok)throw Error('Account preferences are unavailable.');
  const next=await response.json();person=next.person;data=next.preferences||{};offline=false;cache();
 }
 function load(){
  if(loading)return loading;
  loading=(async()=>{try{await fetchAll();}catch{offline=true;try{person=localStorage.getItem('crm.prefs.person:'+base)||'';data=JSON.parse(localStorage.getItem(key())||'null')||{};}catch{data={};}}finally{loading=null;}return data;})();
  return started=loading;
 }
 const get=k=>data?.[k]||null;
 async function set(k,value){
  if(offline||!data)throw Object.assign(Error('Your account could not be reached, so this change stays on this browser for now.'),{code:'offline'});
  const current=get(k)?.revision||0;
  const response=await fetch('api/crm/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'preferences.set',data:{key:k,value,revision:current},request_id:crypto.randomUUID()}),signal:AbortSignal.timeout(15000)});
  let result;try{result=await response.json();}catch{throw Error('The server response was incomplete.');}
  if(response.status===409){try{await fetchAll();}catch{}throw Object.assign(Error(result.error||'These preferences changed on another device.'),{code:'conflict'});}
  if(!response.ok)throw Error(result.error||'Could not save your preferences.');
  data[k]={value,revision:result.revision,updated_at:new Date().toISOString()};cache();return data[k];
 }
 async function refresh(){
  if(loading||!data)return;const before=stamp();
  try{await fetchAll();}catch{return;}
  if(stamp()!==before)document.dispatchEvent(new Event('prefs:updated'));
 }
 document.addEventListener('crm:updated',()=>{if(data&&!offline)refresh();});
 return {load,get,set,refresh,available:()=>!offline&&!!data,person:()=>person,ready:()=>started||load()};
})();

/* Favorites: screens a person pins to the navigation. The list is the same on every device. */
window.WorkspaceFavorites=(()=>{
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const LIMIT=12;let busy=false,note='';
 const STAR=on=>`<svg class="ws-nav-icon" viewBox="0 0 24 24" width="18" height="18" fill="${on?'currentColor':'none'}" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.2 1 5.9-5.2-2.8-5.2 2.8 1-5.9L3.5 9.7l5.9-.8z"/></svg>`;
 const mount=()=>document.getElementById('workspace-favorites');
 function route(){const url=new URL(location.href);for(const k of ['task','return_to'])url.searchParams.delete(k);url.hash='';return (url.pathname.endsWith('/strategy')?'strategy':'./')+url.search;}
 function label(){
  const heading=document.querySelector('#workspace-content h1,#content h1,main h1')?.textContent.trim().replace(/\s+/g,' ');
  const nav=document.querySelector('.ws-nav-primary [aria-current=page],.ws-nav-more [aria-current=page]')?.textContent.trim();
  return (heading||nav||(location.pathname.endsWith('/strategy')?'Command Center':'CRM')).slice(0,60);
 }
 function render(){
  const el=mount(),prefs=window.WorkspacePrefs;if(!el)return;
  if(!prefs?.available()){el.innerHTML='';return;}
  const list=prefs.get('favorites')?.value||[],current=route(),mine=list.some(f=>f.href===current);
  el.innerHTML=`<div class="ws-nav-label">Favorites</div>${list.map(f=>`<a href="${esc(f.href)}" ${f.href===current?'aria-current="page"':''}>${STAR(true)}<span>${esc(f.name)}</span></a>`).join('')}<button type="button" data-favorite-toggle ${busy?'disabled':''}>${STAR(mine)}<span>${mine?'Remove from favorites':'Save this screen'}</span></button>${list.length?'<button type="button" data-favorite-manage class="ws-fav-manage-link">Manage favorites</button>':''}${note?`<small role="status" aria-live="polite">${esc(note)}</small>`:''}`;
 }
 async function toggle(){
  const prefs=window.WorkspacePrefs;if(busy||!prefs?.available())return;
  busy=true;note='';render();
  const current=route();
  for(let attempt=0;attempt<2;attempt++){
   const list=(prefs.get('favorites')?.value||[]).slice(),at=list.findIndex(f=>f.href===current);
   if(at>=0)list.splice(at,1);
   else{if(list.length>=LIMIT){note=`${LIMIT} favorites are saved. Remove one first.`;break;}
    let name=label();if(list.some(f=>f.name===name))name=name.slice(0,55)+' '+(list.length+1);list.push({name,href:current});}
   try{await prefs.set('favorites',list);note=at>=0?'Removed from your favorites.':'Saved to your favorites on every device.';break;}
   catch(error){if(error.code==='conflict'&&attempt===0)continue;note=error.message;break;}
  }
  busy=false;render();
 }
 RecordForm.register('favorites',{
  title:'Manage favorites',saveLabel:'Save favorites',
  load:async()=>{const prefs=window.WorkspacePrefs;await prefs.ready();if(!prefs.available())throw Error('Your account could not be reached. Try again when live updates are on.');return {can_edit:true,list:(prefs.get('favorites')?.value||[]).map(f=>({name:f.name,href:f.href}))};},
  context:()=>'Rename, reorder or remove. Favorites follow your account to every device.',
  fields:m=>m.list.length?`<ol class="ws-fav-manage">${m.list.map((f,i)=>`<li class="ws-fav-row" data-href="${esc(f.href)}"><input name="name-${i}" aria-label="Favorite name" maxlength="60" value="${esc(f.name)}" required><span class="ws-fav-actions"><button type="button" data-fav-up aria-label="Move up">↑</button><button type="button" data-fav-down aria-label="Move down">↓</button><button type="button" data-fav-remove aria-label="Remove favorite">×</button></span></li>`).join('')}</ol>`:'<p>No favorites yet. Open a screen and choose Save this screen.</p>',
  command:(v,m)=>{const rows=[...document.querySelectorAll('#record-fields .ws-fav-row')].map(r=>({name:r.querySelector('input').value.trim(),href:r.dataset.href}));if(rows.some(r=>!r.name))throw Error('Every favorite needs a name.');if(new Set(rows.map(r=>r.name)).size!==rows.length)throw Error('Each favorite needs a different name.');if(JSON.stringify(rows)===JSON.stringify(m.list))return null;return {action:'preferences.set',data:{key:'favorites',value:rows,revision:window.WorkspacePrefs.get('favorites')?.revision||0}};},
  failed:status=>{if(status===409)window.WorkspacePrefs.refresh();},
  saved:()=>{note='Favorites saved.';window.WorkspacePrefs.refresh().then(render);render();}
 });
 document.addEventListener('click',event=>{
  if(event.target.closest('[data-favorite-toggle]'))toggle();
  if(event.target.closest('[data-favorite-manage]'))RecordForm.open('favorites');
  const b=event.target.closest('[data-fav-up],[data-fav-down],[data-fav-remove]');if(!b)return;const row=b.closest('.ws-fav-row');
  if(b.hasAttribute('data-fav-remove'))row.remove();else if(b.hasAttribute('data-fav-up'))row.previousElementSibling?.before(row);else row.nextElementSibling?.after(row);
  document.getElementById('record-form')?.dispatchEvent(new Event('input',{bubbles:true}));
 });
 document.addEventListener('prefs:updated',render);
 return {update:()=>{const prefs=window.WorkspacePrefs;if(!prefs||!mount())return;prefs.ready().then(render);},route,render};
})();
