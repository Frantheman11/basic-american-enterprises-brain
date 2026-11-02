/* Uses existing contact commands and owner permissions through one atomic save. */
RecordForm.register('company',{
  title:'Edit company',
  load:id=>RecordForm.json('api/crm/company?id='+encodeURIComponent(id)),
  context:model=>model.record.name,
  fields:(model,e)=>{
    const r=model.record;
    const field=(name,label,type='text',max=200)=>`<label for="company-${name}">${label}</label><input id="company-${name}" name="${name}" type="${type}" maxlength="${max}" value="${e(r[name])}" ${name==='name'?'required':''}>`;
    return field('name','Company name')+field('email','Email','email')+field('phone','Phone')+field('website','Website','url',2000)+field('address','Address','text',1000)+field('industry','Industry','text',120)+
      `<label for="company-lifecycle">Relationship</label><select id="company-lifecycle" name="lifecycle">${[['lead','Prospect'],['customer','Customer'],['archived','Archived']].map(([id,label])=>`<option value="${id}" ${r.lifecycle===id?'selected':''}>${label}</option>`).join('')}</select>`+
      (model.can_assign?`<label for="company-owner">Company owner</label><select id="company-owner" name="owner">${model.members.map(m=>`<option value="${e(m.id)}" ${r.owner===m.id?'selected':''}>${e(m.name)}</option>`).join('')}</select><p>Changing the owner also moves linked contacts and their access together.</p>`:'');
  },
  command:(values,model,base)=>{const changes=Object.fromEntries(Object.entries(values).filter(([key,value])=>value!==base[key]));return Object.keys(changes).length?{action:'company.update',data:{id:model.record.id,revision:model.record.revision,...changes}}:null;}
});
document.addEventListener('click',event=>{const button=event.target.closest('[data-edit-company]');if(button)RecordForm.open('company',button.dataset.editCompany);});
