/* Daily plan and backlog share the same CRM records in both workspace routes. */
window.WorkspaceDaily=(()=>{
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const root=()=>document.querySelector('[data-workspace-daily]');
 const date=v=>{if(!v)return 'No date';const d=new Date(v);return d.toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit',...(d.getFullYear()!==new Date().getFullYear()?{year:'numeric'}:{})});};
 let model,loading,scope='mine',tab='',search='',message='',pending=null,busy=false,limit=10;
 const key='crm.priority-save:'+document.baseURI;
 try{pending=JSON.parse(sessionStorage.getItem(key)||'null');}catch{}
 function queues(){
  const now=Date.parse(model.server_time),start=new Date(now),end=new Date(now);start.setHours(0,0,0,0);end.setHours(23,59,59,999);
  const all=model.tasks.filter(t=>scope==='all'||t.assigned_to===model.person.id),pins=model.pins.map(id=>model.tasks.find(t=>t.id===id)).filter(Boolean);
  return {focus:pins,today:all.filter(t=>t.queue==='next'&&Date.parse(t.due_at)>=start.getTime()&&Date.parse(t.due_at)<=end.getTime()),review:all.filter(t=>(t.queue==='next'&&Date.parse(t.due_at)<start.getTime())||(t.review_at&&Date.parse(t.review_at)<=now)),waiting:all.filter(t=>t.is_waiting),all};
 }
 function render(){
  const target=root();if(!target)return;
  if(!model){target.innerHTML=`<p role="status">${esc(message||'Loading today’s work…')}</p>${message?'<button data-daily-retry>Retry daily view</button>':''}`;return;}
  const lists=queues();if(!tab)tab=lists.focus.length?'focus':lists.today.length?'today':lists.review.length?'review':'all';
  const rows=lists[tab].filter(t=>!search||[t.title,t.record_name,t.assigned_name,t.waiting_on].join(' ').toLowerCase().includes(search.toLowerCase()));
  const titles={focus:'My focus',today:'Due today',review:'Needs review',waiting:'Waiting',all:'All open work'};
  const notes={focus:'Your chosen priorities. They stay here until completed or unpinned.',today:'Tasks due today with no recorded hold.',review:'Older deadlines and scheduled reviews. Check what still matters before rescheduling.',waiting:'Waiting on input, blocked, decisions, and work parked for later.',all:'All open work in this scope, including future tasks and holds.'};
  window.WorkspaceBulk?.shown(model.can_edit?rows.slice(0,limit):[]);
  const row=t=>`<article class="ws-day-row" data-focus-task="${esc(t.id)}">${window.WorkspaceBulk&&model.can_edit?WorkspaceBulk.checkbox(t):''}<div class="ws-day-content"><button class="ws-day-title" data-focus-open="${esc(t.id)}">${esc(t.title.replace(/^Pending review: /,''))}</button><div class="ws-day-meta"><span>${esc(t.record_name)}</span><span>${esc(t.assigned_name||'Unassigned')}</span><span>Due ${esc(date(t.due_at))}</span>${t.review_at?`<span>Review ${esc(date(t.review_at))}</span>`:''}</div><div class="ws-day-state">${WorkspaceDesign.task(t,model.server_time)}${t.blocked_reason?`<span>${esc(t.blocked_reason)}</span>`:''}</div></div><div class="ws-day-actions">${model.can_edit?`<button data-focus-pin="${esc(t.id)}" aria-pressed="${model.pins.includes(t.id)}" aria-label="${model.pins.includes(t.id)?'Unpin':'Pin priority'}: ${esc(t.title)}" ${busy?'disabled':''}>${model.pins.includes(t.id)?'Unpin':'Pin'}</button>`:''}</div></article>`;
  CRMSync.preserve(target,()=>{target.innerHTML=`<div class="ws-day-heading"><div><h1>Today</h1><p>${esc(new Date(model.server_time).toLocaleDateString(undefined,{weekday:'long',month:'long',day:'numeric'}))} · A clear next step.</p></div><div class="ws-day-head-actions">${model.connected_apps?'<a class="ws-day-apps" href="oauth/apps">Connected apps</a>':''}${model.can_edit?'<button type="button" data-new-task="">Add task</button>':''}</div></div><div class="ws-day-plan"><span><strong>${lists.focus.length} of 3 priorities chosen</strong>${lists.focus.length?'':' · Pin a task to put it in My focus.'}</span>${lists.focus.length&&tab!=='focus'?'<button data-focus-tab="focus">Open my focus</button>':''}</div><div class="ws-day-tabs" role="group" aria-label="Focus queues">${Object.entries(titles).map(([id,label])=>`<button data-focus-tab="${id}" aria-pressed="${tab===id}">${label} <span>${lists[id].length}</span></button>`).join('')}</div><div class="ws-day-filters"><label class="ws-day-find">Find work<input id="focus-search" type="search" placeholder="Task, company or owner" value="${esc(search)}"></label><label>Scope<select id="focus-scope" ${tab==='focus'?'disabled':''}><option value="mine" ${scope==='mine'?'selected':''}>Assigned to me</option><option value="all" ${scope==='all'?'selected':''}>All accessible work</option></select></label></div><p class="ws-day-queue-note">${notes[tab]}${tab==='focus'?' Pinned tasks can include shared work.':''}</p><div class="ws-day-status" role="status">${esc(pending?'A priority save needs confirmation. '+message:message)}${pending?'<button data-priority-retry>Check saved result</button>':message?'<button data-daily-retry>Refresh</button>':''}</div>${window.WorkspaceBulk&&model.can_edit?WorkspaceBulk.toolbar():''}<section aria-label="${titles[tab]}">${rows.slice(0,limit).map(row).join('')||`<div class="ws-day-empty"><h2>${tab==='focus'?'Choose your priorities':'No tasks in this view'}</h2><p>${tab==='focus'?'Open Due today or Needs review, then pin up to three tasks.':'Try another queue or clear your search.'}</p><button data-focus-tab="${tab==='focus'?'review':'all'}">${tab==='focus'?'Review available work':'Browse all open work'}</button></div>`}${rows.length>limit?`<button class="ws-day-more" data-focus-more>Show ${Math.min(10,rows.length-limit)} more · ${rows.length-limit} remaining</button>`:''}</section>`;});
  window.WorkspaceBulk?.layout();
 }
 async function refresh(){
  if(!root())return;if(loading)return loading;
  loading=(async()=>{try{const next=await RecordForm.json('api/crm/daily');if(!model){try{scope=localStorage.getItem('crm.focus.scope:'+next.person.id)==='all'?'all':'mine';}catch{}}
   model=next;message='';window.WorkspaceBulk?.reconcile(model.tasks);render();
  }catch(error){message='Daily view could not refresh. '+error.message;render();}finally{loading=null;}})();return loading;
 }
 async function pin(id){
  if(busy)return;
  if(!pending){pending={action:model.pins.includes(id)?'task.unpin':'task.pin',data:{id},request_id:crypto.randomUUID()};try{sessionStorage.setItem(key,JSON.stringify(pending));}catch{pending=null;message='Saving is unavailable in this browser session.';render();return;}}
  busy=true;message='Saving priority…';render();
  try{const response=await fetch('api/crm/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pending),signal:AbortSignal.timeout(15000)});
   let result;try{result=await response.json();}catch{throw Error('The server response was incomplete.');}
   if(!response.ok){if(response.status<500){pending=null;sessionStorage.removeItem(key);}throw Error(result.error||'Could not save priority.');}
   if(!result.ok||!result.id)throw Error('The save response was incomplete.');
   pending=null;sessionStorage.removeItem(key);await CRMSync.saved();await refresh();
  }catch(error){message=error.message;}finally{busy=false;render();}
 }
 document.addEventListener('click',event=>{
  const b=event.target.closest('button');if(!b||!root()?.contains(b))return;
  if(b.hasAttribute('data-focus-open'))TaskEditor.open(b.dataset.focusOpen);
  if(b.hasAttribute('data-focus-tab')){tab=b.dataset.focusTab;limit=10;window.WorkspaceBulk?.clear();render();}
  if(b.hasAttribute('data-focus-pin')){if(pending){message='Check the pending saved result first.';render();}else pin(b.dataset.focusPin);}
  if(b.hasAttribute('data-priority-retry'))pin();
  if(b.hasAttribute('data-daily-retry'))refresh();
  if(b.hasAttribute('data-focus-more')){limit+=10;render();}
 });
 document.addEventListener('change',event=>{if(event.target.id==='focus-scope'){scope=event.target.value;limit=10;window.WorkspaceBulk?.clear();try{localStorage.setItem('crm.focus.scope:'+model.person.id,scope);}catch{}render();}});
 document.addEventListener('input',event=>{if(event.target.id==='focus-search'){search=event.target.value;limit=10;window.WorkspaceBulk?.clear();render();}});
 document.addEventListener('crm:updated',refresh);
 return {mount:()=>{render();refresh();},refresh,render,placeholder:'<section class="ws-day" data-workspace-daily aria-label="Daily focus"></section>'};
})();
