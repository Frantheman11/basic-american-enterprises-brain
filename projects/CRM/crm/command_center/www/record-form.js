/* Shared recoverable form for record commands. Revisions are enforced by the server. */
window.RecordForm = (() => {
  const esc = v => String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const key = 'crm.record-form:' + document.baseURI, types = {};
  let dialog, spec, model, base, pending, busy=false, current, opener, generation=0;
  const $=id=>document.getElementById(id);
  const values=()=>Object.fromEntries(new FormData($('record-form')));
  const dirty=()=>base && JSON.stringify(values())!==JSON.stringify(base);
  function init(){
    if(dialog)return;
    dialog=document.createElement('dialog');dialog.id='record-editor';dialog.className='task-editor';dialog.setAttribute('aria-labelledby','record-title');
    dialog.innerHTML='<form id="record-form"><div class="task-editor-head"><h2 id="record-title"></h2><button type="button" id="record-close" aria-label="Close editor">×</button></div><p id="record-context" class="task-editor-meta"></p><div class="task-editor-body"><fieldset id="record-fields"></fieldset></div><p id="record-error" class="task-editor-alert" role="alert"></p><div class="task-editor-actions"><button type="button" id="record-reload" hidden>Load latest version</button><button type="button" id="record-cancel">Cancel</button><button type="submit" id="record-save">Save changes</button></div></form>';
    document.body.append(dialog);
    const close=()=>{if(busy)return;if(pending){$('record-error').textContent='Check the saved result before closing.';return;}if(dirty()&&!confirm('Discard your unsaved changes?'))return;generation++;dialog.close();base=null;opener?.isConnected&&opener.focus({preventScroll:true});};
    $('record-close').onclick=close;$('record-cancel').onclick=close;
    dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
    $('record-reload').onclick=async()=>{if(dirty()&&!confirm('Replace your edits with the latest saved record?'))return;await open(current.type,current.id);};
    $('record-form').addEventListener('submit',save);
    $('record-form').addEventListener('change',async event=>{if(!spec?.changed||busy)return;busy=true;$('record-save').disabled=true;try{await spec.changed(event,model);$('record-error').textContent='';}catch(error){$('record-error').textContent=error.message;}finally{busy=false;$('record-save').disabled=false;}});
    window.addEventListener('beforeunload',event=>{if(dialog.open&&(pending||dirty())){event.preventDefault();event.returnValue='';}});
  }
  async function open(type,id){
    init();if(busy)return;
    let recovery;try{recovery=JSON.parse(sessionStorage.getItem(key)||'null');}catch{}
    if(recovery?.request){type=recovery.type;id=recovery.id;pending=recovery.request;}else pending=null;
    spec=types[type];if(!spec)return;
    const epoch=++generation;current={type,id};opener=document.activeElement;base=null;model=null;
    $('record-title').textContent=spec.title;$('record-fields').innerHTML='<p>Loading…</p>';$('record-context').textContent='';$('record-error').textContent='';$('record-reload').hidden=true;$('record-save').hidden=true;
    if(!dialog.open)dialog.showModal();
    try{const loaded=await spec.load(id);if(epoch!==generation)return;model=loaded;$('record-fields').innerHTML=spec.fields(model,esc);$('record-context').textContent=spec.context?.(model)||'';
      $('record-fields').disabled=!!pending||!model.can_edit;$('record-save').hidden=!model.can_edit;
      $('record-save').textContent=pending?'Check saved result':spec.saveLabel||'Save changes';
      base=values();if(pending)$('record-error').textContent='A previous save was not confirmed. Check its result without repeating the change.';
    }catch(error){if(epoch!==generation)return;$('record-error').textContent=error.message;
      // A committed owner change can remove access before its response arrives. The same
      // request can still retrieve its acknowledgement without returning private data.
      if(pending){$('record-save').hidden=false;$('record-save').textContent='Check saved result';}
    }
  }
  async function save(event){
    event.preventDefault();if(busy||(!pending&&!model?.can_edit))return;
    try{if(!pending){const command=spec.command(values(),model,base);if(!command){dialog.close();return;}
      const request={...command,request_id:crypto.randomUUID()};sessionStorage.setItem(key,JSON.stringify({...current,request}));pending=request;}
      busy=true;$('record-save').disabled=true;$('record-fields').disabled=true;$('record-error').textContent='Saving…';
      const response=await fetch('api/crm/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pending),signal:AbortSignal.timeout(20000)});
      let result;try{result=await response.json();}catch{throw Error('The server response was incomplete.');}
      if(!response.ok){if(response.status<500){pending=null;sessionStorage.removeItem(key);$('record-fields').disabled=!model?.can_edit;}$('record-reload').hidden=response.status!==409||!!spec.failed;spec.failed?.(response.status,result,model);throw Error(result.error||'Save failed.');}
      if(!result.ok||!result.id)throw Error('The save response was incomplete.');
      const sent=pending;pending=null;sessionStorage.removeItem(key);base=null;dialog.close();await CRMSync.saved();
      spec.saved?.(result,model,sent);
    }catch(error){$('record-error').textContent=error.message+(pending?' Check the saved result to safely retry.':'');}
    finally{busy=false;$('record-save').disabled=false;$('record-save').textContent=pending?'Check saved result':spec.saveLabel||'Save changes';}
  }
  async function json(url){
    let response,body;
    try{response=await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(15000)});}catch{throw Error('Could not connect to the workspace. Check your connection and try again.');}
    try{body=await response.json();}catch{throw Error('The workspace service is temporarily unavailable. Try again shortly.');}
    if(!response.ok)throw Error(body.error||'Could not load this record. Try again shortly.');return body;
  }
  function recover(){let row;try{row=JSON.parse(sessionStorage.getItem(key)||'null');}catch{}if(row?.type&&types[row.type])open(row.type,row.id);}
  return {register:(type,definition)=>{types[type]=definition;},open,recover,json};
})();
