/* Bulk task changes: see the list first, one atomic save with revision checks, then Undo. */
window.WorkspaceBulk=(()=>{
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const local=value=>{const d=new Date(value);return new Date(d-d.getTimezoneOffset()*60000).toISOString().slice(0,16);};
 const select=(name,label,rows,value='')=>`<label for="bulk-${name}">${label}</label><select id="bulk-${name}" name="${name}">${rows.map(([id,label])=>`<option value="${esc(id)}" ${id===value?'selected':''}>${esc(label)}</option>`).join('')}</select>`;
 const kinds={due_at:{label:'Change due date',title:'Change the due date'},assignee:{label:'Assign',title:'Assign'},followup:{label:'Follow-up status',title:'Set the follow-up status'},department:{label:'Department',title:'Move to a department'},completed:{label:'Mark complete',title:'Mark as completed'}};
 const selected=new Map();let all=[],visible=[],kind='due_at';
 function checkbox(t,text=''){return `<label class="ws-bulk-pick${text?' ws-bulk-pick-text':''}"><input type="checkbox" data-bulk-pick="${esc(t.id)}" ${selected.has(t.id)?'checked':''} aria-label="Select ${esc(t.title)}">${esc(text)}</label>`;}
 function toolbar(){
  document.body.classList.toggle('has-bulk-bar',selected.size>0);
  if(!selected.size)return '';
  return `<div class="ws-bulk-bar" role="region" aria-label="Bulk changes"><strong>${selected.size} selected</strong>${Object.entries(kinds).map(([id,k])=>`<button type="button" data-bulk-action="${id}">${k.label}</button>`).join('')}${visible.some(t=>!selected.has(t.id))?'<button type="button" data-bulk-all>Select all shown</button>':''}<button type="button" data-bulk-clear>Clear selection</button></div>`;
 }
 function reconcile(tasks){all=tasks;for(const [id] of selected){const t=tasks.find(x=>x.id===id);if(t)selected.set(id,t);else selected.delete(id);}}
 function shown(rows){visible=rows;}
 function clear(){selected.clear();document.body.classList.remove('has-bulk-bar');}
 function layout(){const bar=document.querySelector('.ws-bulk-bar');document.body.style.setProperty('--ws-bulk-h',bar?bar.offsetHeight+'px':'0px');}
 // A list outside Today hosts the bar in [data-bulk-host]; it updates in place, without a re-render.
 function refresh(){const host=document.querySelector('[data-bulk-host]');if(!host){window.WorkspaceDaily?.render();return;}document.querySelectorAll('[data-bulk-pick]').forEach(b=>{b.checked=selected.has(b.dataset.bulkPick);});host.innerHTML=toolbar();layout();}
 window.addEventListener('resize',layout);
 RecordForm.register('task-bulk',{
  title:'Change several tasks',saveLabel:'Apply changes',
  load:async()=>{const tasks=[...selected.values()];if(!tasks.length)throw Error('Select at least one task first.');const options=await RecordForm.json('api/crm/task-options?contact_id='+encodeURIComponent(tasks[0].contact_id));return {can_edit:options.can_edit,tasks,options};},
  context:m=>kinds[kind].title+' for '+m.tasks.length+(m.tasks.length===1?' task':' tasks')+'. Every task is checked first; if one cannot change, none do.',
  fields:m=>{
   const list=`<p class="task-editor-meta">Selected tasks</p><ul class="ws-bulk-list">${m.tasks.map(t=>`<li><strong>${esc(t.title)}</strong><small>${esc(t.record_name)} · ${esc(t.assigned_name||'Unassigned')} · Due ${esc(new Date(t.due_at).toLocaleDateString())}</small></li>`).join('')}</ul>`;
   let field='';
   if(kind==='due_at')field=`<label for="bulk-due_at">New due date</label><input id="bulk-due_at" name="due_at" type="datetime-local" value="${local(Date.now()+86400000)}" required><p class="task-editor-meta">Each task keeps its original deadline on record. Review dates stay unchanged.</p>`;
   if(kind==='assignee')field=select('assignee','Assign to',[['','Company owner (automatic)'],...m.options.assignees.map(x=>[x.id,x.name])])+'<p class="task-editor-meta">Assignment never changes company access. A task this person cannot already work on is refused, and nothing changes.</p>';
   if(kind==='department')field=select('department','Department',m.options.departments.map(d=>[d.id,d.label]));
   if(kind==='followup')field=select('followup_state','Follow-up status',[['next','Ready to work'],['client','Waiting on someone'],['blocked','Blocked'],['decisions','Decision needed'],['parked','Parked for later']])+'<label for="bulk-waiting_on" hidden>Waiting on</label><input id="bulk-waiting_on" name="waiting_on" maxlength="200" hidden><label for="bulk-review_at" hidden>Review on</label><input id="bulk-review_at" name="review_at" type="datetime-local" hidden><label for="bulk-blocked_reason" hidden>Blocker</label><textarea id="bulk-blocked_reason" name="blocked_reason" maxlength="2000" hidden></textarea><p class="task-editor-meta">Deadlines stay unchanged. A review date brings each task back to Needs review.</p>';
   if(kind==='completed')field='<p>These tasks will be marked completed. A build plan that needs completion evidence is refused, and nothing changes: complete it from its own task editor.</p>';
   return list+field+'<label for="bulk-note">Note for each task (optional)</label><textarea id="bulk-note" name="note" maxlength="4000" rows="2"></textarea>';
  },
  changed:event=>{if(event.target.name!=='followup_state')return;const v=event.target.value;for(const [id,on] of [['waiting_on',v==='client'],['review_at',['client','parked'].includes(v)],['blocked_reason',v==='blocked']]){const el=document.getElementById('bulk-'+id);el.hidden=!on;el.previousElementSibling.hidden=!on;if(on)el.required=true;else el.required=false;}},
  failed:status=>{if(status===409)CRMSync.refresh();},
  command:(v,m)=>{
   const set={};
   if(kind==='due_at'){const due=new Date(v.due_at);if(!Number.isFinite(due.getTime())||local(due)!==v.due_at)throw Error('Choose a valid local date and time.');set.due_at=due.toISOString();}
   if(kind==='assignee')set.assignee=v.assignee;
   if(kind==='department')set.department=v.department;
   if(kind==='completed')set.status='completed';
   if(kind==='followup'){set.followup_state=v.followup_state;if(v.followup_state==='client')set.waiting_on=v.waiting_on;if(['client','parked'].includes(v.followup_state)){const r=new Date(v.review_at);if(!v.review_at||!Number.isFinite(r.getTime()))throw Error('Choose when to review these tasks.');set.review_at=r.toISOString();}if(v.followup_state==='blocked')set.blocked_reason=v.blocked_reason;}
   if(v.note)set.note=v.note;
   return {action:'task.bulk',data:{tasks:m.tasks.map(t=>({id:t.id,revision:t.revision})),set}};
  },
  saved:result=>{clear();refresh();const reverse={action:'task.bulk',data:{tasks:result.undo.map(u=>({id:u.id,revision:u.revision,values:u.values}))},request_id:crypto.randomUUID()};WorkspaceActions.notice(`Updated ${result.count} ${result.count===1?'task':'tasks'}.`,reverse);}
 });
 document.addEventListener('change',event=>{const box=event.target.closest('[data-bulk-pick]');if(!box)return;const t=all.find(x=>x.id===box.dataset.bulkPick);if(!t)return;if(box.checked)selected.set(t.id,t);else selected.delete(t.id);refresh();});
 document.addEventListener('click',event=>{const b=event.target.closest('button');if(!b)return;
  if(b.hasAttribute('data-bulk-all')){visible.forEach(t=>selected.set(t.id,t));refresh();}
  if(b.hasAttribute('data-bulk-clear')){clear();refresh();}
  if(b.dataset.bulkAction){kind=b.dataset.bulkAction;RecordForm.open('task-bulk',kind);}
 });
 return {checkbox,toolbar,reconcile,shown,clear,layout,count:()=>selected.size};
})();
