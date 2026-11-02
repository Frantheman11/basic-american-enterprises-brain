/* Company build queues use the existing CRM tasks, notes and installation records. */
const StrategyCompany = (() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const e=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let api, current=null, pending=null, busy=false, storageKey='', baseline='';
  const values=()=>Object.fromEntries(new FormData($('company-action-form')));
  const draftKey=()=>storageKey+':draft:'+current?.type+':'+current?.company_id+':'+current?.id;
  const dirty=()=>!!current&&current.type!=='system'&&JSON.stringify(values())!==baseline;
  function keepDraft(){if(!current||current.type==='system'||pending||busy)return;try{if(dirty())sessionStorage.setItem(draftKey(),JSON.stringify(values()));else sessionStorage.removeItem(draftKey());}catch{}}
  function discardDraft(){try{sessionStorage.removeItem(draftKey());}catch{}}
  function close(){
    if(busy)return;
    if(pending){$('company-action-error').textContent='Check the saved result before closing.';return;}
    if(dirty()&&!confirm('Discard your unsaved changes?'))return;
    discardDraft();current=null;baseline='';$('company-editor').close();
  }
  const state=()=>api.state();
  const company=id=>state()?.companies.find(c=>c.id===id);
  const node=id=>state()?.nodes.find(n=>n.id===id);
  const name=t=>t.title.replace(/^Pending review: /,'');
  const wait=t=>['parked','client','blocked','decisions'].includes(WorkspaceDesign.queue(t));
  const date=d=>new Date(d).toLocaleDateString(undefined,{month:'short',day:'numeric'});
  function summary(c,work,history){
    const open=work.filter(t=>t.status==='open'),next=open.filter(t=>!wait(t)).sort((a,b)=>a.due_at.localeCompare(b.due_at))[0];
    const blockers=open.filter(t=>t.context.blocked_reason),touch=history.find(a=>['call','email','meeting'].includes(a.kind));
    const person=id=>state().members.find(m=>m.id===id)?.name||(state().person.id===id?state().person.name:id)||'Not assigned';
    return `<section class="cq-overview" aria-label="Company overview"><article class="cq-focus" data-state="${open.length?'active':'clear'}"><div class="cq-focus-label"><h2>${open.length?'Next action':'Task overview'}${next?'<small>Earliest due · ready to work</small>':''}</h2>${next?WorkspaceDesign.task(next,state().server_time):open.length?WorkspaceDesign.badge('Waiting / review','warning'):WorkspaceDesign.badge('No open tasks')}</div>${next?`<p class="cq-focus-title">${e(next.title)}</p><div class="cq-focus-footer"><small>Due ${e(date(next.due_at))} · Assigned to ${e(person(next.owner))}</small><button class="st-primary" data-action="task" data-id="${e(next.id)}">Open next action <span aria-hidden="true">→</span></button></div>`:open.length?'<p class="cq-focus-title">Open tasks are waiting for input or review.</p><button class="st-primary" data-action="company-tab" data-id="tasks">Review tasks</button>':`<p class="cq-focus-title">No open tasks recorded.</p>${work.length?'<button data-action="company-tab" data-id="tasks">View task history</button>':''}`}</article><div class="cq-context"><div><h2>Company owner</h2><p>${e(person(c.owner))}</p></div><div><h2>Blockers</h2>${blockers.length?`${WorkspaceDesign.badge(blockers.length+' recorded','danger')}<p>${e(blockers[0].context.blocked_reason)}</p><button data-action="task" data-id="${e(blockers[0].id)}">Review blocker${blockers.length>1?` · ${blockers.length} total`:''}</button>`:'<p>No blockers recorded</p>'}</div><div><h2>Last recorded contact</h2>${touch?`<p>${e(date(touch.created_at))} · ${e(touch.kind)}</p><details><summary>Contact summary</summary><p>${e((touch.body||'').slice(0,110))}</p></details>`:'<p>No contact recorded</p>'}<button data-action="company-tab" data-id="notes">View history</button></div></div></section>`;
  }
  const writable=c=>!!c&&(state().connection?.mode==='live_readonly'?(state().connection.writable_companies||[]).includes(c.id):state().capabilities.includes('crm_edit'));
  const button=(label,action,cid,id='',style='')=>`<button type="button" data-company-action="${action}" data-company-id="${e(cid)}" data-record-id="${e(id)}" class="${style}">${label}</button>`;
  function restore(){
    const next='strategy.company-actions.'+state().name+'.'+state().person.id;
    if(next===storageKey)return; storageKey=next;
    try{const saved=JSON.parse(sessionStorage.getItem(storageKey)||'null');if(saved?.request?.request_id&&saved?.form){pending=saved.request;current=saved.form;}}catch{}
  }
  function persist(){
    if(pending)sessionStorage.setItem(storageKey,JSON.stringify({request:pending,form:current}));
    else sessionStorage.removeItem(storageKey);
  }
  function work(c,nid){return state().tasks.filter(t=>t.company_id===c.id&&(!nid||(t.build_node_ids||[]).includes(nid))).sort((a,b)=>a.due_at.localeCompare(b.due_at));}
  function taskActions(t){const c=company(t.company_id),canWrite=state().connection?.mode==='live_readonly'?writable(c):state().capabilities.includes('crm_edit');if(!canWrite||!['open','completed'].includes(t.status))return '';
    return `<div class="st-actions">${button(t.status==='completed'?'Reopen task':'Complete task',t.status==='completed'?'reopen':'complete',t.company_id,t.id)}${t.status==='open'?button('Change date','date',t.company_id,t.id):''}</div>`;
  }
  function tasks(rows,c){return rows.map(t=>`<article class="cq-task"><div>${WorkspaceDesign.task(t,state().server_time)}<small>Due ${date(t.due_at)}</small><h3>${e(name(t))}</h3>${t.context.blocked_reason?`<p>${e(t.context.blocked_reason)}</p>`:''}</div>${taskActions(t)}</article>`).join('')||'<p class="cq-empty">No linked tasks recorded.</p>';}
  function systemCard(c,i,compact=false){
    const rows=work(c,i.node_id).filter(t=>t.status==='open');
    const stopped=/publishing off/i.test(i.evidence||'');
    return `<article class="cq-system ${compact?'is-compact':''}" data-build-node="${e(i.node_id)}" data-tone="${stopped?'warning':i.state==='recommended'?'neutral':i.state==='built'?'success':'info'}"><div class="cq-symbol">${StrategyArt.icon(i.node_id)}</div><div class="cq-system-name"><h3>${e(node(i.node_id)?.label||i.node_id)}</h3>${WorkspaceDesign.installation(i)}</div>${rows.length?`<span class="cq-count">${rows.length} open</span>`:''}${!compact?`<p class="cq-system-next">${rows.length?e(name(rows[0])):'No next task linked yet.'}</p>`:''}<div class="st-actions">${button(compact?'Details':'View build & tasks','system',c.id,i.node_id)}${writable(c)&&i.state==='recommended'?button('Start build','start',c.id,i.node_id):''}</div></article>`;
  }
  function systems(c){
    restore(); const built=c.installations.filter(i=>i.state==='built'),building=c.installations.filter(i=>i.state==='in_progress'),next=c.installations.filter(i=>i.state==='recommended');
    const rows=work(c).filter(t=>t.status==='open'),waiting=rows.filter(wait),other=rows.filter(t=>!(t.build_node_ids||[]).length&&!wait(t));
    return `${pending?`<div class="cq-pending" role="status">A save still needs confirmation. ${button('Check saved result','resume',pending.company_id||current.company_id)}</div>`:''}<div class="cq-summary"><a href="#company-building"><strong>${building.length}</strong><span>In progress</span></a><a href="#company-installed"><strong>${built.length}</strong><span>Installed</span></a><a href="#company-next"><strong>${next.length}</strong><span>Proposed next</span></a><div>${writable(c)?button('Add note','note',c.id):'<small>View only</small>'}<small>${rows.length} open tasks</small></div></div>
      <div class="cq-layout"><div><section id="company-building" class="cq-section"><div class="st-section-top"><h2>In progress</h2><span class="cq-live">${writable(c)?(state().connection?.mode==='live_readonly'?'Connected to live CRM':'CRM controls connected'):'CRM records'}</span></div><div class="cq-grid">${building.map(i=>systemCard(c,i)).join('')||'<p class="cq-empty">No builds in progress.</p>'}</div></section>
      <section id="company-installed" class="cq-section"><div class="st-section-top"><h2>Installed systems</h2><small>Based on recorded evidence</small></div><div class="cq-grid cq-installed">${built.map(i=>systemCard(c,i,true)).join('')||'<p class="cq-empty">No installations recorded.</p>'}</div></section>
      <section id="company-next" class="cq-section"><div class="st-section-top"><h2>What’s next</h2><small>Proposed, not started</small></div><div class="cq-grid">${next.map(i=>systemCard(c,i)).join('')||'<p class="cq-empty">No proposed builds recorded.</p>'}</div></section></div>
      <aside class="cq-side"><h2>Waiting & review <span>${waiting.length}</span></h2><p>Keep these for the next check-in.</p>${tasks(waiting.slice(0,3),c)}${waiting.length>3?`<details><summary>${waiting.length-3} more waiting</summary>${tasks(waiting.slice(3),c)}</details>`:''}<details><summary>Other open work · ${other.length}</summary>${tasks(other,c)}</details><small>Task completion and installation status are tracked separately.</small></aside></div>`;
  }
  function show(type,cid,id=''){
    if(busy)return;
    if($('company-editor').open&&dirty()&&!pending){if(!confirm('Discard your unsaved changes?'))return;discardDraft();}
    if(['complete','reopen','date'].includes(type)&&state().connection?.mode!=='live_readonly'){TaskEditor.open(id,type==='date'?{focus:'due_at'}:{status:type==='complete'?'completed':'open'});return;}
    restore();
    if(type==='resume'){if(!pending)return;draw();return;}
    if(pending){current=current||{};draw();return;}
    const c=company(cid);if(!c)return;
    if(type!=='system'&&!writable(c))return;
    const t=state().tasks.find(t=>t.id===id&&t.company_id===cid),i=c.installations.find(i=>i.node_id===id);
    current={type,company_id:cid,id,revision:t?.revision,installation:i,task:t};
    draw();
  }
  function draw(){
    const f=current,c=company(f.company_id);if(!c)return;
    const i=f.installation,t=f.task;let title='',fields='',save='Save to CRM';
    const area=(label,name,value='')=>`<label>${label}<textarea name="${name}" required maxlength="4000">${e(value)}</textarea></label>`;
    if(f.type==='system'){
      title=node(i.node_id)?.label||i.node_id;
      const rows=work(c,i.node_id),missing=c.eligibility[i.node_id]?.missing||[];
      fields=`${WorkspaceDesign.installation(i)}<div class="st-actions">${writable(c)&&i.state==='recommended'?button('Start build','start',c.id,i.node_id,'st-primary'):''}${writable(c)&&i.state==='in_progress'?button('Record installation','install',c.id,i.node_id,'st-primary'):''}${writable(c)?button('Add company note','note',c.id):''}</div>${missing.length?`<p class="cq-warning">Foundations still needed: ${missing.map(g=>g.map(id=>e(node(id)?.label||id)).join(' or ')).join(', ')}.</p>`:''}<h3>Open tasks</h3>${tasks(rows.filter(t=>t.status==='open'),c)}<details><summary>Evidence & location</summary><p>${e(i.evidence||'No evidence recorded.')}</p><p class="st-source">${e(i.location||'Location not recorded')}</p></details>${rows.some(t=>t.status!=='open')?`<details><summary>Completed work</summary>${tasks(rows.filter(t=>t.status!=='open'),c)}</details>`:''}`;
    } else if(f.type==='note'){title='Add a note';fields=area('Note','body');save='Save note';
    } else if(f.type==='complete'||f.type==='reopen'){
      title=f.type==='complete'?'Complete task':'Reopen task';save=title;
      fields=`<p class="cq-confirm-title">${e(name(t))}</p><p>${f.type==='complete'?'This completes the task only. The system’s installation status stays separate.':'This puts the task back in the open queue, with its original date and history.'}</p>`;
    } else if(f.type==='date'){
      title='Change task date';fields=`<p>${e(name(t))}</p><label>Review date<input name="due_at" type="datetime-local" value="${new Date(new Date(t.due_at).getTime()-new Date(t.due_at).getTimezoneOffset()*60000).toISOString().slice(0,16)}" required></label><small>A review reminder, not a delivery estimate.</small>`;
    } else {
      title=f.type==='start'?'Start '+(node(i.node_id)?.label||i.node_id):'Record installation';
      save=f.type==='start'?'Mark in progress':'Record as installed';
      fields=`<p>${f.type==='start'?'Updates this build’s CRM status to In progress.':'Confirm where the finished system is installed and how it was checked.'}</p>`+(f.type==='install'?`<label>Installation location<input name="location" required maxlength="2000" value="${e(i.location)}" placeholder="Client repository or service URL"></label>`:'')+area(f.type==='start'?'First step / progress note':'Installation evidence','evidence');
    }
    $('company-action-title').textContent=title;$('company-action-context').textContent=c.name;
    $('company-action-fields').innerHTML=fields;$('company-action-error').textContent='';
    baseline=JSON.stringify(values());
    if(!pending&&f.type!=='system'){try{const saved=JSON.parse(sessionStorage.getItem(draftKey())||'null');if(saved){for(const [name,value] of Object.entries(saved)){const field=$('company-action-form').elements.namedItem(name);if(field)field.value=value;}$('company-action-error').textContent='Your unsaved draft was restored.';}}catch{}}
    $('company-action-save').hidden=f.type==='system';$('company-action-save').disabled=false;$('company-action-save').textContent=save;
    if(pending){$('company-action-fields').innerHTML=`<p>${e(pending.action==='activity.add'?pending.data.body:pending.data.evidence||f.task?.title||title)}</p><p>The earlier save was not confirmed. Checking again uses the same request and will not duplicate it.</p>`;$('company-action-save').textContent='Check saved result';}
    if(!$('company-editor').open)$('company-editor').showModal();
  }
  async function submit(event){
    event.preventDefault();if(busy||!current)return;
    if(!pending){
      const f=current,fields=Object.fromEntries(new FormData(event.target));let action,data;
      if(f.type==='note'){action='activity.add';data={contact_id:f.company_id,kind:'note',body:fields.body.trim()};}
      else if(['complete','reopen','date'].includes(f.type)){action='task.update';data={id:f.id,revision:f.revision,...(f.type==='date'?{due_at:new Date(fields.due_at).toISOString()}:{status:f.type==='complete'?'completed':'open'})};}
      else {action='node.set';data={contact_id:f.company_id,node_id:f.id,revision:f.installation.revision,state:f.type==='start'?'in_progress':'built',evidence:[f.installation.evidence,`${new Date().toISOString().slice(0,10)}: ${fields.evidence.trim()}`].filter(Boolean).join('\n\n'),...(f.type==='install'?{location:fields.location.trim()}:{})};}
      if(action==='node.set'&&(!fields.evidence.trim()||data.evidence.length>4000)){$('company-action-error').textContent='Add a brief progress note. Combined evidence must stay within 4,000 characters.';return;}
      if(action==='activity.add'&&!data.body){$('company-action-error').textContent='Add a note before saving.';return;}
      keepDraft();
      pending={company_id:f.company_id,action,data,request_id:crypto.randomUUID()};
      try{persist();}catch{pending=null;$('company-action-error').textContent='Enable tab storage before saving so a disconnected request can be recovered.';return;}
    }
    busy=true;draw();$('company-action-save').disabled=true;$('company-action-save').textContent='Saving…';
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);
    try{
      const live=state().connection?.mode==='live_readonly';const body=live?pending:(({company_id,...rest})=>rest)(pending);
      const response=await fetch(live?'api/crm/company-action':'api/crm/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal});
      const result=await response.json();
      if(!response.ok){
        if([400,409].includes(response.status)){pending=null;persist();draw();if(response.status===409){$('company-action-save').hidden=true;await api.refresh();throw Error('This record changed. Close this form and reopen it to use the latest CRM record.');}}
        throw Error(result.error||'The CRM could not save this change.');
      }
      if(result.ok!==true||!result.id)throw Error('The save response was incomplete.');
      pending=null;discardDraft();try{persist();}catch{};$('company-editor').close();current=null;baseline='';api.notify(state().connection?.mode==='live_readonly'?'Saved to the live CRM.':'Saved to the CRM.');await api.refresh();
    }catch(error){$('company-action-error').textContent=(error.name==='AbortError'?'The save could not be confirmed.':error.message)+(pending?' Your request is kept. Check the saved result to confirm without repeating the change.':'');}
    finally{clearTimeout(timer);busy=false;$('company-action-save').disabled=false;$('company-action-save').textContent=pending?'Check saved result':'Save to CRM';}
  }
  function init(hooks){api=hooks;
    document.addEventListener('click',event=>{const b=event.target.closest('button');if(!b)return;
      if(b.hasAttribute('data-company-close')){close();return;}
      if(b.dataset.companyAction)show(b.dataset.companyAction,b.dataset.companyId,b.dataset.recordId);
    });
    $('company-action-form').addEventListener('submit',submit);
    $('company-editor').addEventListener('cancel',event=>{event.preventDefault();close();});
    $('company-action-form').addEventListener('input',keepDraft);
    $('company-action-form').addEventListener('change',keepDraft);
    window.addEventListener('beforeunload',event=>{if($('company-editor').open&&(pending||dirty())){keepDraft();event.preventDefault();event.returnValue='';}});
  }
  return {init,systems,taskActions,writable,button,summary};
})();
