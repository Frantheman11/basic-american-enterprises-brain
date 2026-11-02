/* Shared search reads the same authenticated records as both workspaces. */
window.WorkspaceSearch = (() => {
  const launcher = document.getElementById('workspace-search-launcher');
  if (!launcher) return {};
  const e = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const names = {company:'Companies',contact:'Contacts',task:'Tasks',note:'Notes & conversations'};
  const dialog = document.createElement('dialog'); dialog.id = 'workspace-search'; dialog.setAttribute('aria-labelledby','workspace-search-title');
  dialog.innerHTML = `<div class="ws-search-heading"><div><small>Command Center & CRM</small><h2 id="workspace-search-title">Search your workspace</h2></div><button type="button" id="ws-search-close" aria-label="Close search">×</button></div><div id="ws-search-form"><label for="ws-search-query">Companies, contacts, tasks, and notes</label><div class="ws-search-input"><input id="ws-search-query" type="search" maxlength="160" autocomplete="off" placeholder="Search a name, phone number, or phrase…" aria-describedby="ws-search-status"><button type="button" id="ws-search-clear">Clear</button></div><div class="ws-search-filters"><label for="ws-search-type">Search in<select id="ws-search-type"><option value="all">Everything</option>${Object.entries(names).map(([key,name])=>`<option value="${key}">${name}</option>`).join('')}</select></label><label class="ws-search-check"><input type="checkbox" id="ws-search-archived"> Include archived</label></div></div><p id="ws-search-status" role="status" aria-live="polite"></p><div id="ws-search-results"></div><div id="ws-search-detail" hidden></div>`;
  document.body.append(dialog);
  const $ = id => document.getElementById(id), query = $('ws-search-query'), type = $('ws-search-type'), archived = $('ws-search-archived'), results = $('ws-search-results'), detail = $('ws-search-detail');
  let timer, controller, generation = 0, offset = 0, noteId = '', signature = '', busy = false, refreshAgain = false;
  const crmURL = id => { const url = new URL('./',document.baseURI); url.search = new URLSearchParams({view:'contacts',contact:id}); return url.href; };
  function recordURL(item) {
    if (item.type !== 'company' || !document.getElementById('workspace-content')) return crmURL(item.contact_id);
    const url = new URL('strategy',document.baseURI); url.search = new URLSearchParams({view:'company',company:item.id,tab:'systems'}); url.hash='workspace'; return url.href;
  }
  function stamp(value) { return value ? new Date(value).toLocaleDateString(undefined,{year:'numeric',month:'short',day:'numeric'}) : ''; }
  function cancel() { clearTimeout(timer); timer=null; controller?.abort(); generation++; busy=false; }
  function controls() { $('ws-search-form').hidden=!!noteId; results.hidden=!!noteId; detail.hidden=!noteId; }
  function setStatus(text) { if ($('ws-search-status').textContent!==text) $('ws-search-status').textContent=text; }
  function rows(body) {
    const focusKey = dialog.contains(document.activeElement) ? document.activeElement?.dataset.searchKey : null;
    const html = body.groups.filter(g=>g.total).map(group=>`<section class="ws-search-group" aria-label="${e(names[group.type])}"><div class="ws-search-group-head"><h3>${e(names[group.type])} <span>${group.total}</span></h3>${type.value==='all'&&group.next_offset!==null?`<button type="button" data-search-type="${group.type}" data-search-key="type-${group.type}">View all ${group.total}</button>`:''}</div><ul>${group.items.map(item=>{
      const meta = [item.type==='note'?stamp(item.created_at):item.type==='task'?'Due '+stamp(item.due_at):'',item.status].filter(Boolean).join(' · ');
      const content = `<span class="ws-search-row-top"><strong>${e(item.title)}</strong><span aria-hidden="true">${item.type==='note'?'Read →':item.type==='task'?'Open →':'↗'}</span></span><span class="ws-search-context">${e(item.context)}${meta?' · '+e(meta):''}</span>${item.snippet?`<span class="ws-search-snippet">${e(item.snippet)}</span>`:''}`;
      return `<li>${['task','note'].includes(item.type)?`<button type="button" class="ws-search-result" data-search-${item.type}="${e(item.id)}" data-search-key="${item.type}-${e(item.id)}">${content}</button>`:`<a class="ws-search-result" href="${e(recordURL(item))}" data-search-key="${item.type}-${e(item.id)}">${content}</a>`}</li>`;
    }).join('')}</ul>${type.value!=='all'?`<div class="ws-search-pages"><span>${offset+1}–${Math.min(offset+25,group.total)} of ${group.total}</span>${offset?`<button type="button" data-search-page="${Math.max(0,offset-25)}" data-search-key="previous">Previous 25</button>`:''}${group.next_offset!==null?`<button type="button" data-search-page="${group.next_offset}" data-search-key="next">Next 25</button>`:''}</div>`:''}</section>`).join('');
    if (html!==signature) {
      signature=html; results.innerHTML=html;
      if(focusKey) (results.querySelector(`[data-search-key="${CSS.escape(focusKey)}"]`)||query).focus({preventScroll:true});
    }
    setStatus(body.total?`${body.total} ${body.total===1?'result':'results'}${archived.checked?' · Including archived':''}`:'No matches. Try a shorter name or phrase, another record type, or include archived.');
  }
  async function read(url, signal) {
    const response=await fetch(url,{cache:'no-store',signal:AbortSignal.any([signal,AbortSignal.timeout(15000)])});
    if(response.redirected||response.status===401)throw Error('Your session has expired. Reload the workspace to sign in again.');
    if(!response.headers.get('content-type')?.includes('application/json'))throw Error('Search is temporarily unavailable. Try again.');
    let body;
    try{body=await response.json();}catch{throw Error('Search is temporarily unavailable. Try again.');}
    if(!response.ok)throw Error(body.error||'Search is temporarily unavailable. Try again.');
    return body;
  }
  async function run(quiet=false) {
    if(!dialog.open)return;
    cancel(); const current=generation; controller=new AbortController(); busy=true;
    if(!noteId&&query.value.trim().length<2){results.innerHTML='';signature='';setStatus('Type at least 2 characters to search all records you can access.');busy=false;return;}
    if(!quiet)setStatus(noteId?'Loading note…':'Searching…');
    try {
      if(noteId){
        const body=await read('api/crm/search-note?'+new URLSearchParams({id:noteId}),controller.signal);
        if(current!==generation||!dialog.open)return;
        const html=`<button type="button" id="ws-search-back">← Back to results</button><h3 tabindex="-1" id="ws-search-note-title">${e(body.kind[0].toUpperCase()+body.kind.slice(1))} · ${e(body.contact_name)}</h3><p class="ws-search-context">${e(stamp(body.created_at))}${body.edited_at?' · Edited '+e(stamp(body.edited_at)):''}</p><div class="ws-search-note-body">${e(body.body)}</div><a class="ws-search-record-link" href="${e(crmURL(body.contact_id))}">Open CRM record ↗</a>`;
        if(detail.innerHTML!==html){const focused=detail.contains(document.activeElement)?document.activeElement.id:'';detail.innerHTML=html;if(!quiet)$('ws-search-note-title').focus();else if(focused)$(focused)?.focus({preventScroll:true});}
        setStatus('Full note · Updates live');
      }else{
        const body=await read('api/crm/search?'+new URLSearchParams({q:query.value.trim(),type:type.value,archived:archived.checked?'1':'0',offset:String(offset)}),controller.signal);
        if(current!==generation||!dialog.open)return;
        // A deletion can empty the last page; return to the first valid page.
        if(offset&&body.total<=offset){offset=0;return run(quiet);}
        rows(body);
      }
    }catch(error){
      if(current!==generation||error.name==='AbortError')return;
      signature='';results.innerHTML='';detail.innerHTML=noteId?'<button type="button" id="ws-search-back">← Back to results</button>':'';
      (noteId?detail:results).insertAdjacentHTML('beforeend','<button type="button" id="ws-search-retry">Try again</button>');
      setStatus(error.name==='TimeoutError'?'Search took too long. Try again.':error instanceof TypeError?'Could not connect to search. Check your connection and try again.':error.message||'Search is temporarily unavailable. Try again.');
    }finally{if(current===generation){busy=false;if(refreshAgain){refreshAgain=false;run(true);}}}
  }
  function schedule() { cancel(); offset=0;signature='';results.innerHTML='';setStatus('Searching…');timer=setTimeout(()=>{timer=null;run();},220); }
  function open() {
    if(document.querySelector('dialog[open]'))return;
    noteId='';offset=0;controls();dialog.showModal();query.focus();run();
  }
  launcher.addEventListener('click',open);
  $('ws-search-close').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',()=>{cancel();if(!document.querySelector('dialog[open]'))launcher.focus({preventScroll:true});});
  $('ws-search-clear').addEventListener('click',()=>{query.value='';offset=0;run();query.focus();});
  query.addEventListener('input',schedule);
  type.addEventListener('change',()=>{offset=0;run();});
  archived.addEventListener('change',()=>{offset=0;run();});
  dialog.addEventListener('click',event=>{
    const b=event.target.closest('button');if(!b)return;
    if(b.id==='ws-search-retry'){run();return;}
    if(b.id==='ws-search-back'){noteId='';detail.innerHTML='';controls();run();query.focus();return;}
    if(b.dataset.searchType){type.value=b.dataset.searchType;offset=0;run();type.focus();return;}
    if(b.dataset.searchPage!==undefined){offset=Number(b.dataset.searchPage);run();query.focus();return;}
    if(b.dataset.searchNote){cancel();noteId=b.dataset.searchNote;detail.innerHTML='';controls();run();return;}
    if(b.dataset.searchTask){dialog.close();launcher.focus();window.TaskEditor?.open(b.dataset.searchTask);}
  });
  document.addEventListener('keydown',event=>{
    if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'&&!event.altKey){
      if(document.querySelector('dialog[open]')&&!dialog.open)return;
      event.preventDefault();if(!dialog.open)open();else if(!noteId)query.focus();return;
    }
    if(!dialog.open||noteId)return;
    const links=[...results.querySelectorAll('.ws-search-result')];
    if(event.target===query&&event.key==='ArrowDown'&&links.length){event.preventDefault();links[0].focus();}
    const index=links.indexOf(event.target);
    if(index!==-1&&['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();(event.key==='ArrowDown'?links[Math.min(index+1,links.length-1)]:links[index-1]||query).focus();}
    if(event.target===query&&event.key==='Enter'){event.preventDefault();run();}
  });
  document.addEventListener('crm:updated',()=>{if(!dialog.open||timer)return;if(busy)refreshAgain=true;else run(true);});
  return {open};
})();
