window.TaskCreate=(()=>{
  const e=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const local=value=>{const d=new Date(value);return new Date(d-d.getTimezoneOffset()*60000).toISOString().slice(0,16);};
  const select=(name,label,rows,value='')=>`<label for="create-${name}">${label}</label><select id="create-${name}" name="${name}">${rows.map(([id,label])=>`<option value="${e(id)}" ${id===value?'selected':''}>${e(label)}</option>`).join('')}</select>`;
  const options=model=>select('assignee','Assigned to',[['','Company owner (automatic)'],...model.options.assignees.map(m=>[m.id,m.name])])+ '<details><summary>Category and related deal</summary>'+select('category','Category',[['','Other follow-ups'],...model.options.categories.map(c=>[c.id,c.label])])+select('deal_id','Related deal',[['','No deal'],...model.options.deals.map(d=>[d.id,d.title])])+'</details>';
  RecordForm.register('task-create',{
    title:'Add task',saveLabel:'Create task',
    load:async context=>{
      const snapshot=await RecordForm.json('api/crm'),contacts=snapshot.contacts.filter(c=>c.lifecycle!=='archived');
      const chosen=contacts.find(c=>c.id===context?.contact_id)||contacts.find(c=>c.kind==='business')||contacts[0];
      if(!chosen)throw Error('Create a contact in the CRM before adding a task.');
      return {can_edit:snapshot.capabilities.includes('crm_edit'),contacts,chosen,department:context?.department||'',options:await RecordForm.json('api/crm/task-options?contact_id='+encodeURIComponent(chosen.id))};
    },
    context:()=> 'Creates one shared CRM task. Assignment does not change company access.',
    fields:model=>select('contact_id','Company or contact',model.contacts.map(c=>[c.id,c.name]),model.chosen.id)+
      '<label for="create-title">Task title</label><input id="create-title" name="title" maxlength="200" required>'+ 
      `<label for="create-due">Due date</label><input id="create-due" name="due_at" type="datetime-local" value="${local(Date.now()+86400000)}" required>`+
      select('department','Department',[['','From category'],...model.options.departments.map(d=>[d.id,d.label])],model.department)+
      `<div id="create-options">${options(model)}</div><label for="create-note">Instructions or note</label><textarea id="create-note" name="note" maxlength="4000"></textarea>`,
    changed:async(event,model)=>{if(event.target.id!=='create-contact_id')return;model.options=await RecordForm.json('api/crm/task-options?contact_id='+encodeURIComponent(event.target.value));document.getElementById('create-options').innerHTML=options(model);},
    saved:()=>WorkspaceActions.notice('Task created in both workspaces.',null),
    command:(values,model)=>{
      if(values.contact_id!==model.options.contact.id)throw Error('Company options could not load. Choose the company again.');
      const due=new Date(values.due_at);if(!Number.isFinite(due.getTime())||local(due)!==values.due_at)throw Error('Choose a valid local date and time.');
      return {action:'task.create',data:{...values,due_at:due.toISOString()}};
    }
  });
  return {open:(contact_id,department)=>RecordForm.open('task-create',{contact_id,department})};
})();
document.addEventListener('click',event=>{const button=event.target.closest('[data-new-task]');if(button)TaskCreate.open(button.dataset.newTask,button.dataset.taskDepartment);});
