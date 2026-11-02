/* Shared entry points and revision-protected quick changes. */
window.WorkspaceActions=(()=>{
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const local=value=>{const d=new Date(value);return new Date(d-d.getTimezoneOffset()*60000).toISOString().slice(0,16);};
 const select=(name,label,rows,value='')=>`<label for="quick-${name}">${label}</label><select id="quick-${name}" name="${name}">${rows.map(([id,label])=>`<option value="${esc(id)}" ${id===value?'selected':''}>${esc(label)}</option>`).join('')}</select>`;
 let context={},picker,undo=null,undoBusy=false;
 const undoKey='crm.quick-undo:'+document.baseURI;
 try{const saved=JSON.parse(sessionStorage.getItem(undoKey)||'null');if(saved?.request)undo=saved;}catch{}
 function notice(text,change){
  if(arguments.length>1&&!undo?.pending)undo=change?{request:change,pending:false}:null;
  let box=document.getElementById('workspace-action-status');
  if(!box){box=document.createElement('div');box.id='workspace-action-status';box.setAttribute('role','status');document.body.append(box);}
  box.hidden=false;box.innerHTML=`<span>${esc(text)}</span>${undo?`<button type="button" data-quick-undo ${undoBusy?'disabled':''}>${undo.pending?'Check Undo result':'Undo'}</button>`:''}<button type="button" data-action-dismiss aria-label="Dismiss notification" ${undo?.pending?'hidden':''}>×</button>`;
 }
 async function undoChange(){
  if(undoBusy||!undo)return;
  try{undo.pending=true;sessionStorage.setItem(undoKey,JSON.stringify(undo));}catch{notice('Undo cannot start because browser recovery storage is unavailable.');return;}
  undoBusy=true;notice('Undoing change…');
  try{const response=await fetch('api/crm/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(undo.request),signal:AbortSignal.timeout(15000)});
   let result;try{result=await response.json();}catch{throw Error('The Undo response was incomplete.');}
   if(!response.ok){const bulk=undo.request.action==='task.bulk';if(response.status<500){undo=null;sessionStorage.removeItem(undoKey);}throw Error(response.status===409?(bulk?result.error+' Undo did not overwrite anything.':'This task changed again. Open the task to review the latest version; Undo did not overwrite it.'):result.error||'Undo failed.');}
   if(!result.ok||!result.id)throw Error('The Undo response was incomplete.');
   undo=null;sessionStorage.removeItem(undoKey);await CRMSync.saved();notice('Change undone.');
  }catch(error){notice(error.message);}finally{undoBusy=false;const b=document.querySelector('[data-quick-undo]');if(b)b.disabled=false;}
 }
 RecordForm.register('task-quick',{
  title:'Change date or assignment',saveLabel:'Save task',
  load:id=>RecordForm.json('api/crm/task?id='+encodeURIComponent(id)),
  context:m=>m.contact.name+' · '+m.task.title,
  fields:m=>`<label for="quick-due_at">Due date</label><input id="quick-due_at" name="due_at" type="datetime-local" value="${local(m.task.due_at)}" required>`+
   select('assignee','Assigned to',[['','Company owner (automatic)'],...m.assignees.map(x=>[x.id,x.name]),...(m.task.assignee&&!m.assignees.some(x=>x.id===m.task.assignee)?[[m.task.assignee,m.task.assignee+' · access needs review']]:[])],m.task.assignee)+
   '<p class="task-editor-meta">The follow-up review date stays the same. Assignment keeps company access unchanged.</p>',
  command:(v,m,base)=>{const data={id:m.task.id,revision:m.task.revision};
   if(v.due_at!==base.due_at){const due=new Date(v.due_at);if(!Number.isFinite(due.getTime())||local(due)!==v.due_at)throw Error('Choose a valid local date and time.');data.due_at=due.toISOString();}
   if(v.assignee!==base.assignee)data.assignee=v.assignee;
   return Object.keys(data).length>2?{action:'task.update',data}:null;
  },
  saved:(result,m,sent)=>{let reverse;
   if(m?.task.revision===sent.data.revision){const data={id:result.id,revision:sent.data.revision+1};for(const field of ['due_at','assignee'])if(field in sent.data)data[field]=m.task[field];reverse={action:'task.update',data,request_id:crypto.randomUUID()};}
   notice('Task updated in both workspaces.',reverse);
  }
 });
 RecordForm.register('quick-note',{
  title:'Add note',saveLabel:'Save note',
  load:async id=>{const s=await RecordForm.json('api/crm');const contacts=s.contacts.filter(c=>c.lifecycle!=='archived');if(!contacts.length)throw Error('Add a contact before recording a note.');return {can_edit:s.capabilities.includes('crm_edit'),contacts,chosen:contacts.find(c=>c.id===id)?.id||contacts[0].id};},
  context:()=> 'Saved to the contact’s history in both workspaces.',
  fields:m=>select('contact_id','Company or contact',m.contacts.map(c=>[c.id,c.name]),m.chosen)+'<label for="quick-body">Note</label><textarea id="quick-body" name="body" maxlength="4000" rows="5" required></textarea>',
  command:v=>({action:'activity.add',data:{...v,kind:'note'}}),saved:()=>notice('Note saved to the contact’s history.',null)
 });
 RecordForm.register('quick-contact',{
  title:'Add contact',saveLabel:'Create contact',
  load:async id=>{const s=await RecordForm.json('api/crm'),companies=s.contacts.filter(c=>c.kind==='company'&&c.lifecycle!=='archived');return {can_edit:s.capabilities.includes('crm_create'),companies,chosen:companies.find(c=>c.id===id)?.id||''};},
  context:()=> 'Create a person or company. Possible duplicates are checked before saving.',
  fields:m=>select('kind','Contact type',[['person','Person'],['company','Company']])+ '<label for="quick-name">Name</label><input id="quick-name" name="name" maxlength="200" required>'+select('company_id','Company (for a person)',[['','No company'],...m.companies.map(c=>[c.id,c.name])],m.chosen)+'<label for="quick-email">Email</label><input id="quick-email" name="email" type="email" maxlength="254"><label for="quick-phone">Phone</label><input id="quick-phone" name="phone" type="tel" maxlength="60"><details><summary>Relationship</summary>'+select('lifecycle','Relationship',[['lead','Lead'],['customer','Customer']])+'</details><p>If this contact already exists, cancel and use Search to open it.</p><label id="quick-duplicate" hidden><input type="checkbox" name="confirm_duplicate"> I reviewed the duplicate warning and need a separate contact.</label>',
  changed:event=>{if(event.target.name==='kind'){const field=document.getElementById('quick-company_id');field.disabled=event.target.value==='company';field.parentElement.querySelector('label[for=quick-company_id]').hidden=field.disabled;field.hidden=field.disabled;}},
  failed:(status,result)=>{if(status===409&&result.error?.startsWith('Possible duplicate:'))document.getElementById('quick-duplicate').hidden=false;},
  command:v=>({action:'contact.create',data:{...v,confirm_duplicate:v.confirm_duplicate==='on',company_id:v.kind==='person'?v.company_id:''}}),saved:r=>notice('Contact created. Open it from Search or Contacts.',null)
 });
 function open(){
  if(!picker){picker=document.createElement('dialog');picker.id='workspace-add';picker.className='task-editor';picker.setAttribute('aria-labelledby','quick-add-title');document.body.append(picker);}
  picker.innerHTML=`<div class="task-editor-head"><h2 id="quick-add-title">Add to the workspace</h2><button type="button" data-add-close aria-label="Close quick add">×</button></div><div class="ws-add-choices">${context.capabilities?.includes('crm_edit')?'<button data-add-kind="task">Add task<small>Set the next action and deadline</small></button><button data-add-kind="note">Add note<small>Record context or a conversation</small></button>':''}${context.capabilities?.includes('crm_create')?'<button data-add-kind="contact">Add contact<small>Create a person or company</small></button>':''}</div>`;picker.showModal();
 }
 document.addEventListener('click',event=>{
  const b=event.target.closest('button');if(!b)return;
  if(b.id==='workspace-add-launcher')open();
  if(b.hasAttribute('data-add-close'))picker.close();
  if(b.dataset.addKind){picker.close();if(b.dataset.addKind==='task')TaskCreate.open(context.contact_id,context.department);else RecordForm.open('quick-'+b.dataset.addKind,context.contact_id);}
  if(b.hasAttribute('data-task-quick'))RecordForm.open('task-quick',b.dataset.taskQuick);
  if(b.hasAttribute('data-quick-undo'))undoChange();
  if(b.hasAttribute('data-action-dismiss')){document.getElementById('workspace-action-status').hidden=true;undo=null;}
 });
 return {notice,configure:next=>{context=next;const b=document.getElementById('workspace-add-launcher');if(b)b.hidden=next.readonly||!next.capabilities?.some(c=>['crm_edit','crm_create'].includes(c));if(undo?.pending&&!document.getElementById('workspace-action-status'))notice('An Undo save needs confirmation.');},button:t=>`<button type="button" data-task-quick="${esc(t.id)}">Date / assignee</button>`};
})();
