/* Working CRM UI: no timed DOM replacement, no demo records, no external sends. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let state, research = null, contentFeed = null, callFeed = null, callFilter = 'real', contentFilter = 'approved', leaders = null, leaderPeriod = 'month', section = 'today', selected = null, filter = '', lifecycle = 'all', editing = null, dirty = false, pending = null, saving = false;
  const entry = new URLSearchParams(location.search);
  const validSection=value=>[...document.querySelectorAll('[data-nav]')].some(el=>el.dataset.nav===value);
  if(validSection(entry.get('view')))section=entry.get('view');
  selected = entry.get('contact') || null;
  // Per-viewer conveniences only: the Research view (tree or list) and the colour theme.
  const remember = (key, value) => { try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); } catch (e) { return null; } };
  let channel = 'all';
  // Whose records Contacts and Pipeline show, for someone who sees the whole team (Sam, 2026-09-28).
  let owner = remember('crm.owner') || 'all';
  const ownerOk = c => owner === 'all' || c?.owner === owner;
  const ownerSelect = () => state.members.length > 1 ? `<select id="owner" aria-label="Whose records">${[{id:'all',label:'Everyone'},...state.members.map(m=>({id:m.id,label:m.id===state.person.id?'Mine ('+m.name+')':m.name}))].map(m=>`<option ${m.id===owner?'selected':''} value="${escape(m.id)}">${escape(m.label)}</option>`).join('')}</select>` : '';
  let researchMode = ['list', 'map'].includes(remember('crm.researchMode')) ? remember('crm.researchMode') : 'list';
  const folded = new Map(); // category id -> open or closed, kept across re-renders
  const can = cap => state.capabilities.includes('crm_' + cap);
  const btn = (label, action, id = '', primary = false) => `<button data-action="${action}" data-id="${escape(id)}" class="${primary?'primary':''}">${label}</button>`;
  const when = value => new Date(value).toLocaleString([], {dateStyle:'medium', timeStyle:'short'});
  const title = (name, sub, actions = '', refresh = true) => `<div class="top"><div><h1>${escape(name)}</h1><p class="muted">${escape(sub)}</p></div><div class="actions">${actions}${refresh?btn('Refresh','refresh'):''}</div></div>`;
  function placeViews(){const bar=document.querySelector('.workspace-views'),anchor=$('content').querySelector('.top');if(bar&&anchor&&!(section==='today'&&!selected))anchor.after(bar);}
  const contact = id => state.contacts.find(c => c.id === id);
  const member = id => state.members.find(m => m.id === id)?.name || (id === state.person.id ? state.person.name : 'Unassigned');
  const stage = id => state.stages.find(s => s.id === id);
  // More than one pipeline in one business (Portfolio CRM P3): each stage names its pipeline.
  let pipelineId = entry.get('pipeline') || '';
  const pipelines = () => state.pipelines?.length ? state.pipelines : [{id:'main',label:'Pipeline'}];
  const multi = () => pipelines().length > 1;
  const lineOf = s => s?.pipeline || pipelines()[0].id;
  const currentLine = () => pipelines().find(p => p.id === pipelineId) || pipelines()[0];
  const stagesIn = id => state.stages.filter(s => lineOf(s) === id);
  // Territory (Portfolio CRM P5): a contact's US state decides which master or regional lead sees it.
  const states = () => state.territories || [];
  const stateName = code => states().find(x => x.id === code)?.label || code;
  const empty = message => `<p class="empty">${escape(message)}</p>`;
  // One tap on a phone: call, text, email, directions (sales rep review 2026-09-27).
  const dial = p => String(p || '').replace(/[^\d+]/g, '');
  const phoneLinks = p => dial(p).length >= 7 ? `<a href="tel:${escape(dial(p))}">${escape(p)}</a> · <a href="sms:${escape(dial(p))}">Text</a>` : escape(p || 'Not listed');
  const mailLink = m => m ? `<a href="mailto:${escape(m)}">${escape(m)}</a>` : 'Not provided';
  const mapLink = a => a ? `<a href="https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(a)}" target="_blank" rel="noopener noreferrer">${escape(a)}</a>` : 'Not listed';
  const dateInput = value => { const d = new Date(value); return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16); };
  const cashOf = (cents, cur) => (cents/100).toLocaleString(undefined, {style:'currency', currency:cur || 'USD', maximumFractionDigits:0});
  const money = d => escape(cashOf(d.amount_cents, d.currency)) + (d.monthly_cents ? ' + ' + escape(cashOf(d.monthly_cents, d.currency)) + '/mo' : '');
  const offerLabel = id => (state.offers || []).find(o => o.id === id)?.label || '';
  const handoffWords = {quote: 'Waiting for Sam: quote', signed: 'Signed: onboarding waiting'};
  const openTasks = () => state.tasks.filter(t => t.status === 'open').sort((a,b) => a.due_at.localeCompare(b.due_at));
  // A checked to-do stays on its list, struck through, until it is archived (batch 1, Sam 2026-09-28).
  const onList = t => t.status === 'open' || (t.status === 'completed' && !t.archived_at);
  let taskSearch='',taskAssignee='',taskDue='all',taskQueue='all';
  // Bulk changes on All work: rows carry a Select box; the selection resets when the view changes.
  let picking=false,bulkView='';
  const taskFiltered=()=>!!(taskSearch||taskAssignee||taskDue!=='all'||taskQueue!=='all');
  const taskMatches=t=>(!taskAssignee||(t.assignee||contact(t.contact_id)?.owner)===taskAssignee)&&(taskQueue==='all'||(taskQueue==='waiting'?t.is_waiting:t.queue===taskQueue))&&(taskDue==='all'||(taskDue==='past'?Date.parse(t.due_at)<Date.parse(state.server_time):Date.parse(t.due_at)>=Date.parse(state.server_time)))&&(!taskSearch||[t.title,contact(t.contact_id)?.name,t.category_label].join(' ').toLowerCase().includes(taskSearch.toLowerCase()));
  const listed = () => state.tasks.filter(t=>onList(t)&&(section!=='tasks'||taskMatches(t))).sort((a,b) => a.due_at.localeCompare(b.due_at));
  const checkedOf = (id, deep) => state.tasks.filter(t => t.status === 'completed' && !t.archived_at && (t.contact_id === id || (deep && clientOf(contact(t.contact_id))?.id === id)));
  const archiveBtn = (id, deep) => can('edit') && checkedOf(id, deep).length ? btn('Archive checked', 'task-archive', id + (deep ? '|deep' : '')) : '';
  // Every phone and email on a contact; the first of each kind is the main one (contact_channels.py).
  const chans = c => (state.channels || {})[c?.id] || [c?.phone && {kind:'phone', label:'', value:c.phone}, c?.email && {kind:'email', label:'', value:c.email}].filter(Boolean);
  const chanLabel = x => x.label || (x.kind === 'phone' ? 'Phone' : 'Email');
  const peopleAt = c => c?.kind === 'company' ? state.contacts.filter(p => p.company_id === c.id) : [];
  const send = async (action, data) => { const r = await fetch('api/crm/command', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action, data, request_id:crypto.randomUUID()})}); const res = await r.json(); if (!r.ok) throw Error(res.error || 'Save failed.'); return res; };
  let undoTimer = null;
  function undoNotice(message, action, id, box = $('notice')) {
    box.innerHTML = `${escape(message)} <button data-action="${action}" data-id="${escape(id)}" data-undo>Undo</button>`;
    clearTimeout(undoTimer); undoTimer = setTimeout(() => box.querySelector('[data-undo]')?.remove(), 10000);
  }
  const doneNotice = id => undoNotice('Done. It stays checked on its list until you archive it.', 'task-undo', id);
  const next = id => openTasks().find(t => t.contact_id === id);
  const nextDeal = id => openTasks().find(t => t.deal_id === id);
  let viewMemory=null;
  function rememberViews(){if(viewMemory)return;viewMemory=WorkspaceViews.start({kind:'crm',person:state.person.id,root:$('content'),get:()=>({section,selected,filter,lifecycle,channel,owner,taskSearch,taskAssignee,taskDue,taskQueue}),set:(s,initial)=>{
    if(typeof s.taskSearch==='string')taskSearch=s.taskSearch;if(typeof s.taskAssignee==='string')taskAssignee=s.taskAssignee;if(['all','past','upcoming'].includes(s.taskDue))taskDue=s.taskDue;if(['all','next','waiting','client','blocked','decisions','parked'].includes(s.taskQueue))taskQueue=s.taskQueue;
    if(validSection(s.section))section=s.section;if(typeof s.selected==='string')selected=s.selected;
    if(typeof s.filter==='string')filter=s.filter;if(['all','lead','customer','archived'].includes(s.lifecycle))lifecycle=s.lifecycle;if(typeof s.channel==='string')channel=s.channel;if(typeof s.owner==='string')owner=s.owner;
    if(initial){if(entry.has('view'))section=validSection(entry.get('view'))?entry.get('view'):'today';if(entry.has('contact'))selected=entry.get('contact');else if(entry.has('view')||entry.has('task'))selected=null;}
  }});}
  let navigationGeneration=0;
  function writeRoute(replace=false,keepTask=false){
    const url=new URL(location.href);url.searchParams.set('view',section);
    if(selected)url.searchParams.set('contact',selected);else url.searchParams.delete('contact');
    if(section==='deals'&&pipelineId&&multi())url.searchParams.set('pipeline',pipelineId);else url.searchParams.delete('pipeline');
    if(!keepTask){url.searchParams.delete('task');url.searchParams.delete('return_to');url.hash='';}
    const method=replace||url.href===location.href?'replaceState':'pushState';history[method]({crm:{section,selected,y:0}},'',url);
  }
  async function navigate(nextSection,nextContact=null,fromHistory=false,y=0){
    const generation=++navigationGeneration;
    if(!fromHistory)history.replaceState({crm:{section,selected,y:scrollY}},'',location.href);
    section=validSection(nextSection)?nextSection:'today';selected=nextContact&&contact(nextContact)?nextContact:null;
    if(!fromHistory)writeRoute();$('notice').textContent='';
    try{await refreshFeed();}catch(error){$('notice').textContent=error.message;}
    if(generation!==navigationGeneration)return;
    render();viewMemory?.persist();window.scrollTo(0,y);
    $('main').setAttribute('tabindex','-1');$('main').focus({preventScroll:true});
  }
  window.addEventListener('popstate',()=>{const q=new URLSearchParams(location.search);navigate(q.get('view'),q.get('contact'),true,history.state?.crm?.y||0);});
  const feedReads={};
  async function readFeed(key,url){
    const record=feedReads[key]||(feedReads[key]={});
    try{const response=await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(15000)});const result=await response.json();if(!response.ok)throw Error(result.error||'This screen could not refresh.');record.at=new Date();record.error='';return result;}
    catch(error){record.error=error.message;throw error;}
  }
  async function refreshFeed(){if(selected)return;const read={calls:loadCalls,content:loadContent,research:loadResearch,leaderboard:loadLeaders}[section];if(read)await read();}
  function feedStatus(){const record=feedReads[section];return record?`<p class="muted" role="status" id="view-freshness">${record.error?'Refresh failed; displayed results may be stale. ':''}${record.at?'Last updated '+escape(record.at.toLocaleString()):'No successful update yet.'}</p>`:'';}
  let loadPromise=null;
  async function load(background=false,opening=false) {
    if(loadPromise){await loadPromise;return load(background,opening);}
    let done;loadPromise=new Promise(resolve=>{done=resolve;});
    try {
    const response = await fetch('api/crm', {cache:'no-store'});
    const result = await response.json();
    if (!response.ok) throw Error(result.error || 'Unable to load the CRM');
    state = result;rememberViews();
    if (owner !== 'all' && !state.members.some(m => m.id === owner)) owner = 'all';
    $('identity').textContent = state.person.name; $('identity').title = 'Signed in as ' + state.person.name;
    if (selected && !contact(selected)) selected = null;
    let feedError;try{await refreshFeed();}catch(error){feedError=error;}
    if(background)CRMSync.preserve($('content'),render);else render();
    if(feedError){if(opening)$('notice').textContent=feedError.message;else throw feedError;}
    } finally {loadPromise=null;done();}
  }
  const catsFor = c => c?.kind === 'business' ? state.business_task_categories : state.task_categories;
  const categoryLabel = id => [...state.business_task_categories, ...state.task_categories].find(c => c.id === id)?.label || 'Other follow-ups';
  // A collapsible section. Buttons sit below the heading, never inside it, so a click on a
  // button does not also fold the section.
  function fold(key, heading, actions, body, openByDefault = true) {
    const open = folded.has(key) ? folded.get(key) : openByDefault;
    return `<details class="panel fold" data-group="${escape(key)}" ${open?'open':''}><summary><h2>${heading}</h2></summary>${actions?`<div class="actions fold-actions">${actions}</div>`:''}${body}</details>`;
  }
  // Main tasks (schema 26): a main task holds subtasks, one level, with a "4 of 11 done" count (task_work.py).
  const mainOf = t => t.parent_id ? state.tasks.find(m => m.id === t.parent_id && m.is_main) : null;
  const sorted = t => !!(t.is_main || mainOf(t));
  const progress = m => `<span class="chip">${m.subtasks_done||0} of ${m.subtasks_total||0} done</span>`;
  const actionButton = t => t.status !== 'open' ? '' : t.action === 'ask_client' ? btn('Ask the client','ask',t.id) : t.action === 'work_session' ? btn('Open a work session','session',t.id) : '';
  function todoItem(t, who = false, cat = false, under = true) {
    const overdue = t.status === 'open' && new Date(t.due_at) < new Date(state.server_time), done = t.status === 'completed';
    const deal = t.deal_id ? state.deals.find(d => d.id === t.deal_id) : null;
    return `<li class="todo${done?' done':''}"><label class="check"><input type="checkbox" data-task="${escape(t.id)}" ${done?'checked':''} ${can('edit')?'':'disabled'}><span><strong>${escape(t.title)}</strong>${t.is_main?' '+progress(t):''}<small>${under&&mainOf(t)?'Under '+escape(mainOf(t).title)+' · ':''}${cat&&t.category?escape(t.category_label||categoryLabel(t.category))+' · ':''}${who?escape(contact(t.contact_id)?.name)+' · ':''}<span class="${overdue?'overdue':''}">${done?'Done '+escape(when(t.completed_at)):(overdue?'Overdue · ':'Due ')+escape(when(t.due_at))}</span>${deal?' · '+escape(deal.title):''} · Assigned to ${escape(member(t.assignee||contact(t.contact_id)?.owner))}</small>${WorkspaceDesign.task(t,state.server_time)}</span></label><div class="actions">${picking&&t.status==='open'?WorkspaceBulk.checkbox(t,'Select'):''}${actionButton(t)}${can('edit')?btn('Edit','task-edit',t.id)+(t.status==='open'?WorkspaceActions.button(t):''):''}</div></li>`;
  }
  function categoryGroups(c, tasks, prefix = '') {
    const ids = [...catsFor(c).map(x => x.id), ''];
    tasks.forEach(t => { if (!ids.includes(t.category)) ids.push(t.category); });
    return ids.map(id => {
      const items = tasks.filter(t => t.category === id).sort((a,b) => (a.status==='open'?0:1)-(b.status==='open'?0:1) || a.due_at.localeCompare(b.due_at));
      if (!items.length) return '';
      const open = items.filter(t => t.status === 'open').length, key = prefix + c.id + ':' + (id || '_other');
      const expanded = (section==='tasks'&&taskFiltered())||folded.get(key)||false;
      return `<details class="group" data-group="${escape(key)}" ${expanded?'open':''}><summary><span>${escape(items[0].category_label||categoryLabel(id))}</span><span class="chip">${open ? open+' open' : 'Completed in this category'}</span></summary><ul>${items.map(t => todoItem(t,t.contact_id!==c.id)).join('')}</ul></details>`;
    }).join('');
  }
  function todoList(c) {
    const related=new Set([c.id,...peopleAt(c).map(p=>p.id)]),mine=state.tasks.filter(t=>related.has(t.contact_id)), groups=categoryGroups(c,mine.filter(onList));
    const open=mine.filter(t=>t.status==='open'),waiting=open.filter(t=>t.is_waiting);
    const archived = mine.filter(t => t.status === 'completed' && t.archived_at).sort((a,b) => b.completed_at.localeCompare(a.completed_at));
    return `<section class="panel" id="record-tasks"><div class="top"><h2>To-do list</h2><div class="actions">${archiveBtn(c.id,c.kind==='company')}${can('edit')?btn('Add to-do','task-new',c.id):''}</div></div><p class="muted">${open.length} open tasks · ${waiting.length} waiting / review${c.kind==='company'?' · Includes linked people':''}</p>${groups || empty('No loose ends. Add the next thing this client needs.')}${archived.length ? `<details class="group" data-group="archived:${escape(c.id)}" ${folded.get('archived:'+c.id)?'open':''}><summary><span>Done / archived</span><span class="chip">${archived.length}</span></summary><ul>${archived.map(t => todoItem(t)).join('')}</ul></details>` : ''}</section>`;
  }
  // A client is a company, or a person with no company. The business record is not a client.
  const business = () => state.contacts.find(c => c.kind === 'business');
  const clientOf = c => (c && c.company_id && contact(c.company_id)) || c;
  function clientGroups(tasks, prefix, withBusiness = false) {
    const byClient = new Map();
    tasks.forEach(t => { const c = clientOf(contact(t.contact_id)); if (!c || (c.kind === 'business' && !withBusiness)) return; if (!byClient.has(c.id)) byClient.set(c.id, []); byClient.get(c.id).push(t); });
    return [...byClient].map(([id, items]) => ({c: contact(id), items: items.sort((a,b) => a.due_at.localeCompare(b.due_at))}))
      .sort((a,b) => a.items[0].due_at.localeCompare(b.items[0].due_at)).map(({c, items}) => {
        const key = prefix + c.id, now = new Date(state.server_time);
        const late = items.filter(t => t.status === 'open' && new Date(t.due_at) < now).length;
        const open = items.filter(t => t.status === 'open').length;
        return `<details class="group client" data-group="${escape(key)}" ${folded.get(key)?'open':''}><summary><span>${escape(c.name)}<small>${open?'Next: '+escape(items.find(t=>t.status==='open').title):prefix==='done:'?'':'Nothing open'}</small></span>${late?`<span class="chip late">${late} overdue</span>`:''}<span class="chip">${open?open+' open':items.length+' done'}</span></summary><ul>${items.map(t => todoItem(t, t.contact_id !== c.id, true)).join('')}</ul><div class="group-foot">${prefix === 'done:' ? '' : archiveBtn(c.id, true)}${btn('Open '+escape(c.name),'open',c.id)}</div></details>`;
      }).join('');
  }
  // My day (sales rep review 2026-09-27): one list, in time order, of what is mine to do now.
  // Late and due-today callbacks first, then my Research leads with no callback set (soonest
  // back to the pool first), then the rest of the week, folded. Every row has its one-tap actions.
  function myDay() {
    const me = state.person.id, now = new Date(state.server_time), end = new Date(now); end.setHours(23,59,59,999);
    const week = new Date(now); week.setDate(week.getDate() + 7);
    const owned = t => { const c = contact(t.contact_id); return c && c.kind !== 'business' && (t.assignee || c.owner) === me; };
    const tasks = openTasks().filter(t=>owned(t)&&t.queue==='next'&&((state.research_links||{})[t.contact_id]||(state.research_links||{})[clientOf(contact(t.contact_id))?.id]));
    const linkOf = c => { const x = (state.research_links || {})[c?.id] || (state.research_links || {})[clientOf(c)?.id]; return x && x.live ? x : null; };
    const time = d => d.toLocaleTimeString([], {hour:'numeric', minute:'2-digit'});
    const row = (whenText, late, title, c, extra, t) => {
      const link = linkOf(c), phone = c?.phone || clientOf(c)?.phone;
      return `<li class="day-row"><span class="day-when${late ? ' overdue' : ''}">${escape(whenText)}</span><div class="lead-main"><strong>${escape(title)}</strong><small>${escape(clientOf(c)?.name || '')}${extra ? ' · ' + escape(extra) : ''}</small></div><div class="actions">${dial(phone).length >= 7 ? `<a class="button" href="tel:${escape(dial(phone))}">Call</a>` : ''}${peopleAt(clientOf(c)).filter(p => p.id !== c?.id && dial(p.phone).length >= 7).slice(0, 2).map(p => `<a class="button" href="tel:${escape(dial(p.phone))}">Call ${escape(p.name.split(' ')[0])}</a>`).join('')}${link && can('edit') ? btn('Record outcome', 'record-outcome', link.place_id, true) : ''}${t && can('edit') ? btn('Done', 'task-done', t.id) : ''}${btn('Open', 'open', clientOf(c)?.id || c.id)}</div></li>`;
    };
    const late = tasks.filter(t => new Date(t.due_at) < now), today = tasks.filter(t => new Date(t.due_at) >= now && new Date(t.due_at) <= end);
    const later = tasks.filter(t => new Date(t.due_at) > end && new Date(t.due_at) <= week);
    const withTask = new Set(openTasks().filter(owned).map(t => clientOf(contact(t.contact_id))?.id));
    const idle = Object.entries(state.research_links || {}).filter(([cid, l]) => l.live && l.status === 'open' && contact(cid)?.owner === me && !withTask.has(cid))
      .sort((a, b) => String(a[1].expires_at).localeCompare(String(b[1].expires_at)));
    const rows = late.map(t => row('Late', true, t.title, contact(t.contact_id), 'was due ' + when(t.due_at), t)).join('') +
      today.map(t => row(time(new Date(t.due_at)), false, t.title, contact(t.contact_id), '', t)).join('') +
      idle.map(([cid, l]) => row('No callback', false, contact(cid).name, contact(cid), 'back to the pool ' + day(l.expires_at), null)).join('');
    const count = late.length + today.length + idle.length;
    if(!count&&!later.length)return '';
    return fold('today:myday', `Research callbacks <span class="chip">${count} to do</span>`, '',
      (rows ? `<ul class="plain">${rows}</ul>` : empty('Nothing due. Find your next lead in Research.')) +
      (later.length ? `<details class="group" data-group="today:week" ${folded.get('today:week') ? 'open' : ''}><summary><span>Later this week</span><span class="chip">${later.length}</span></summary><ul class="plain">${later.map(t => row(new Date(t.due_at).toLocaleDateString([], {weekday:'short'}) + ' ' + time(new Date(t.due_at)), false, t.title, contact(t.contact_id), '', t)).join('')}</ul></details>` : ''));
  }
  // Waiting for you: everything only the owner can settle, in one list (approvals.py).
  function waitingPanel() {
    const w = state.waiting || []; if (!w.length) return '';
    const go = i => i.kind === 'print' ? btn('Review printout', 'research-open', i.place_id, true)
      : i.kind === 'request' ? '<button data-nav="research" class="primary">Open Research</button>'
      : i.kind === 'alert' ? btn('Open the lead', 'open', i.contact_id, true)
      : i.kind === 'lead' ? btn('Open the lead', 'open', i.contact_id) + btn('Send to client', 'lead-hold', i.id + '|release', true) + btn('Not a lead', 'lead-hold', i.id + '|dismiss')
      : i.kind === 'event' ? (i.contact_id ? btn('Open the lead', 'open', i.contact_id) : '')
      : btn('Open', 'open', i.contact_id) + btn('Mark done', 'handoff', i.id + '|done', true);
    return fold('today:waiting', `Waiting for you <span class="chip late">${w.length}</span>`, '',
      `<ul class="plain">${w.map(i => `<li class="lead"><div class="lead-main"><strong>${escape(i.title)}</strong><small>Asked by ${escape(i.by)} · ${escape(when(i.at))}</small></div><div class="actions">${go(i)}</div></li>`).join('')}</ul>`);
  }
  function taskFilters(){
    const select=(id,label,rows,value)=>`<label>${label}<select id="${id}" aria-label="${escape(label)}">${rows.map(([id,label])=>`<option value="${escape(id)}" ${id===value?'selected':''}>${escape(label)}</option>`).join('')}</select></label>`;
    const owners=new Map(state.tasks.map(t=>{const id=t.assignee||contact(t.contact_id)?.owner;return [id,member(id)];}));
    return `<div class="toolbar task-filters"><label>Find tasks<input id="task-search" type="search" value="${escape(taskSearch)}" placeholder="Task, contact or category"></label>${select('task-assignee-filter','Filter by assignee',[['','Everyone'],...owners],taskAssignee)}${select('task-due-filter','Filter by due date',[['all','All dates'],['past','Past due'],['upcoming','Upcoming']],taskDue)}${select('task-queue-filter','Work status',[['all','All work'],['next','Next actions'],['waiting','Waiting / review'],['client','Waiting on client'],['blocked','Blocked'],['decisions','Decisions'],['parked','Parked for later']],taskQueue)}${taskFiltered()?btn('Clear filters','task-filters-clear'):''}</div>`;
  }
  function mainPanel(prefix) {
    const now = new Date(state.server_time), subs = listed();
    const mains = state.tasks.filter(t => t.is_main && onList(t)).sort((a,b) => (a.status==='open'?0:1)-(b.status==='open'?0:1) || a.title.localeCompare(b.title));
    const groups = mains.map(m => {
      const items = subs.filter(t => t.parent_id === m.id).sort((a,b) => (a.status==='open'?0:1)-(b.status==='open'?0:1) || a.due_at.localeCompare(b.due_at));
      if (taskFiltered() && !items.length && !taskMatches(m)) return '';
      const key = prefix + 'main:' + m.id, c = contact(m.contact_id), open = items.filter(t => t.status === 'open');
      const late = open.filter(t => new Date(t.due_at) < now).length;
      return `<details class="group client" data-group="${escape(key)}" ${(taskFiltered()||folded.get(key))?'open':''}><summary><span>${m.status==='completed'?'<s>'+escape(m.title)+'</s>':escape(m.title)}<small>${c&&c.kind!=='business'?escape(c.name)+' · ':''}${open.length?'Next: '+escape(open[0].title):'Nothing open'}</small></span>${late?`<span class="chip late">${late} overdue</span>`:''}${progress(m)}</summary><ul>${items.map(t => todoItem(t, true, true, false)).join('') || '<li class="muted">No subtasks yet.</li>'}</ul><div class="group-foot">${can('edit')?btn('Edit main task','task-edit',m.id):''}${c?btn('Open '+escape(c.name),'open',c.id):''}</div></details>`;
    }).join('');
    return mains.length ? fold(prefix + 'mains', `Main tasks <span class="chip">${mains.filter(m => m.status === 'open').length} open</span>`, '', groups || empty('No main task matches these filters.')) : '';
  }
  function businessPanel(prefix) {
    const b = business();
    if (!b) return can('assign') ? fold(prefix + 'business', 'Business next actions', btn('Set up the business record','business-new','',true), empty('Work on the business itself lives on its own record. Set it up once.')) : '';
    const items = listed().filter(t => t.contact_id === b.id && !sorted(t));
    return fold(prefix + 'business', `Business next actions <span class="chip">${items.filter(t => t.status === 'open').length} open</span>`,
      (can('edit')?btn('Add to-do','task-new',b.id):'')+archiveBtn(b.id)+btn('Open the business record','open',b.id),
      categoryGroups(b, items, prefix) || empty('No open business actions.'));
  }
  function render() {
    WorkspaceActions.configure({capabilities:state.capabilities,contact_id:selected});
    document.querySelectorAll('[data-nav]').forEach(b => { if(b.dataset.nav === section)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current'); });
    {const bar=document.querySelector('.workspace-views');if(bar&&$('content').contains(bar))$('content').before(bar);}
    WorkspaceShell.update();document.querySelector('.workspace-views')?.toggleAttribute('hidden',section==='today'&&!selected);
    {const v=selected?'record':section;if(v!==bulkView){window.WorkspaceBulk?.clear();bulkView=v;}picking=!!window.WorkspaceBulk&&!selected&&section==='tasks'&&can('edit');}
    if (selected) { renderRecord(); placeViews(); return; }
    let html = '';
    if (section === 'today') {
      const tasks = openTasks().filter(t=>t.queue==='next'), today = new Date(state.server_time).toLocaleDateString();
      const due = tasks.filter(t => new Date(t.due_at).toLocaleDateString() === today);
      const overdue = tasks.filter(t => new Date(t.due_at) < new Date(state.server_time));
      const noNext = state.contacts.filter(c => c.kind !== 'business' && c.lifecycle === 'lead' && !next(c.id) && !(c.kind === 'company' && state.contacts.some(p => p.company_id === c.id && next(p.id))) && !(c.company_id && next(c.company_id)));
      html = WorkspaceDaily.placeholder+
        waitingPanel()+
        fold('today:missing','Leads without a next action','',noNext.map(c=>`<div class="row"><div><strong>${escape(c.name)}</strong><small>${escape(member(c.owner))}</small></div>${btn('Open record','open',c.id)}</div>`).join('')||empty('Every lead has a next action.'),false);
    } else if(section === 'contacts') {
      html = title('Contacts','People and companies, from first conversation to customer.'+((state.my_states||[]).length?' You see your own contacts and every contact in '+state.my_states.map(stateName).join(', ')+'.':''),(state.data_io?'<button type="button" data-io="export">Export to Excel</button>'+(can('create')?'<button type="button" data-io="import">Import contacts</button>':''):'')+(can('create')?btn('Add contact','contact-new','',true):''),false)+
        `<div class="toolbar"><input id="search" aria-label="Search contacts" placeholder="Search name, email or phone" value="${escape(filter)}">${ownerSelect()}<select id="lifecycle" aria-label="Relationship filter">${['all','lead','customer','archived'].map(x=>`<option ${x===lifecycle?'selected':''} value="${x}">${x==='all'?'All relationships':x[0].toUpperCase()+x.slice(1)}</option>`).join('')}</select><select id="channel" aria-label="Where they came from">${['all',...channels(),'Not recorded'].map(x=>`<option ${x===channel?'selected':''} value="${escape(x)}">${x==='all'?'Every source':escape(x)}</option>`).join('')}</select></div><div class="panel" id="contact-list"></div>`+deletedPanel();
    } else if(section === 'deals') {
      html = title('Pipeline','Every lead and deal by stage. A lead taken from Research lands here on its own; open a contact to add a priced deal.',can('assign')?btn('Edit stages','stages-edit'):'',false)+
        (ownerSelect()||multi()?`<div class="toolbar">${multi()?`<div class="pipeline-switch" role="group" aria-label="Pipelines">${pipelines().map(p=>`<button type="button" data-pipeline="${escape(p.id)}" aria-pressed="${p.id===currentLine().id}">${escape(p.label)} <span class="chip">${state.deals.filter(d=>lineOf(stage(d.stage))===p.id&&ownerOk(contact(d.contact_id))).length}</span></button>`).join('')}</div>`:''}${ownerSelect()}</div>`:'')+
        `<div class="board">${(multi()?stagesIn(currentLine().id):state.stages).map(s=>{const deals=state.deals.filter(d=>d.stage===s.id&&ownerOk(contact(d.contact_id)));return `<section class="lane"><h2>${escape(s.label)} · ${deals.length}</h2>${deals.map(d=>{const link=(state.research_links||{})[d.contact_id],back=link&&!link.live&&link.status!=='converted'&&!['won','lost'].includes(stage(d.stage)?.outcome);return `<button class="deal${back?' faded':''}" data-action="open" data-id="${d.contact_id}"><small>${escape(contact(d.contact_id)?.name)}${link?' · Research':''}</small><strong>${escape(d.title)}</strong>${d.amount_cents||d.monthly_cents?money(d):'<small>Lead, no amount yet</small>'}${back?`<span class="chip late">${link.resting?escape(link.rest_reason):'Back in the pool'}</span>`:''}<small>${nextDeal(d.id)?'Next: '+escape(when(nextDeal(d.id).due_at)):'No follow-up set'}</small></button>`}).join('') || empty('Nothing here')}</section>`}).join('')}</div>`;
    } else if(section === 'reports') {
      html = reportsView();
    } else if(section === 'research') {
      html = research?researchView():title('Research','Retry when the connection is available.')+empty('Research could not load. Use Refresh to retry.');
    } else if(section === 'calls') {
      html = callFeed?callsView():title('Calls and chats','Retry when the connection is available.')+empty('Calls could not load. Use Refresh to retry.');
    } else if(section === 'content') {
      html = contentFeed?contentView():title('Content','Retry when the connection is available.')+empty('Content could not load. Use Refresh to retry.');
    } else if(section === 'leaderboard') {
      html = leaders?leaderView():title('Leaderboard','Retry when the connection is available.')+empty('Leaderboard could not load. Use Refresh to retry.');
    } else {
      if(picking){const rows=state.tasks.map(t=>({...t,record_name:contact(t.contact_id)?.name||'',assigned_name:member(t.assignee||contact(t.contact_id)?.owner)}));WorkspaceBulk.reconcile(rows);const shown=new Set(listed().filter(t=>t.status==='open').map(t=>t.id));WorkspaceBulk.shown(rows.filter(t=>shown.has(t.id)));}
      html = title('Tasks','Due times use your browser timezone. Reminders remain here until completed.','',false)+taskFilters()+(picking?`<div data-bulk-host>${WorkspaceBulk.toolbar()}</div>`:'')+
        mainPanel('tasks:')+businessPanel('tasks:')+
        fold('tasks:clients', state.tasks.some(t => t.is_main) ? 'Unsorted, by client' : 'Open, by client', '', clientGroups(listed().filter(t => !sorted(t)), 'tasks:') || empty('No open tasks. Schedule one from a contact record.'))+
        fold('tasks:done', 'Done / archived', '', `<p class="muted">Every checked to-do, the business's too. Uncheck one to put it back on its list.</p>` + (clientGroups(state.tasks.filter(t=>t.status==='completed'&&taskMatches(t)), 'done:', true) || empty('Completed work will appear here.')), false);
    }
    $('content').innerHTML = feedStatus()+html;
    WorkspaceDaily.mount();placeViews();window.WorkspaceBulk?.layout();
    if(section==='contacts') renderContacts();
    if(section==='research' && researchMode==='map') mountMap();
  }
  // Content: the business's posts and articles, read-only. Approval and "posted" are recorded
  // where the content state lives (one writer), so this view copies and downloads, never edits.
  // Calls and chats: every phone call and website chat the AI receptionist answered (call_log.py).
  // Read-only: the recap is on the contact's history, the full transcript opens here.
  async function loadCalls() {
    callFeed = await readFeed('calls','api/crm/calls');
  }
  // The one recap line a list needs: why they called, else the first line.
  const callGist = r => { const lines = r.split('\n').map(l => l.replace(/^- /, '')); return lines.find(l => l.startsWith('Why they called:')) || lines[0]; };
  const secs = s => s == null ? '' : s < 60 ? s + ' s' : Math.round(s / 60) + ' min';
  const callChip = c => `<span class="chip${c.spam || c.outcome === 'no_speech' ? '' : ' call-' + escape(c.outcome)}">${escape(c.outcome_label)}</span>`;
  function callsView() {
    const c = callFeed, n = c.numbers;
    const stat = (label, value) => `<div class="stat"><span class="muted">${escape(label)}</span><strong>${escape(value)}</strong></div>`;
    const strip = `<div class="stats calls-stats">${stat('Conversations, last 7 days', n.calls)}${stat('Phone calls', n.phone ?? n.calls)}${stat('Website chats', n.chats ?? 0)}${stat('Real people', n.real)}${stat('Spam screened', n.spam)}${stat('Booked', n.booked)}${stat('Callbacks', n.callbacks)}${stat('Transfers', n.transfers)}${n.to_booking ? stat('Sent to booking', n.to_booking) : ''}${stat('Median reply', n.reply_ms_median == null ? 'n/a' : (n.reply_ms_median / 1000).toFixed(1) + ' s')}</div>`;
    const real = x => !x.spam && x.outcome !== 'no_speech';
    const isChat = x => x.channel === 'chat';
    const filters = [['real', 'Real people', c.calls.filter(real).length], ['phone', 'Phone', c.calls.filter(x => !isChat(x)).length], ['chat', 'Chats', c.calls.filter(isChat).length], ['spam', 'Spam and empty', c.calls.filter(x => !real(x)).length], ['all', 'All', c.calls.length]];
    const seg = `<div class="actions seg" role="group" aria-label="Show">${filters.map(([id,l,k]) => `<button data-action="calls-filter" data-id="${id}" aria-pressed="${callFilter===id}" class="${callFilter===id?'primary':''}">${escape(l)} · ${k}</button>`).join('')}</div>`;
    const shown = c.calls.filter(x => callFilter === 'all' || (callFilter === 'phone' ? !isChat(x) : callFilter === 'chat' ? isChat(x) : (callFilter === 'real') === real(x)));
    const row = x => `<li class="row"><div><strong>${escape(x.name || x.caller_id || (isChat(x) ? 'Website visitor' : 'Unknown caller'))}</strong>${x.business ? ` <small class="muted">${escape(x.business)}</small>` : ''}<small>${escape(when(x.received_at))}${x.seconds != null ? ' · ' + escape(secs(x.seconds)) : ''}${x.caller_id ? ' · ' + phoneLinks(x.caller_id) : ''}</small>${x.recap ? `<small class="call-line">${escape(callGist(x.recap))}</small>` : ''}</div><div class="actions"><span class="chip">${isChat(x) ? 'Chat' : 'Phone'}</span>${callChip(x)}${btn('Open','call-open',x.id)}</div></li>`;
    return title('Calls and chats', 'Every phone call and website chat the AI receptionist answered. The recap goes on the contact; the full transcript is here.') + strip +
      `<section class="panel">${seg}<ul class="plain">${shown.map(row).join('') || empty(c.calls.length ? 'Nothing in this view.' : 'No calls or chats yet. They appear here when a call or chat ends.')}</ul></section>`;
  }
  async function showCall(id) {
    $('viewer-title').textContent = 'Phone call';
    $('viewer-body').innerHTML = '<p class="muted">Loading the call&hellip;</p>';
    if (!$('viewer').open) $('viewer').showModal();
    const r = await fetch('api/crm/calls?id=' + encodeURIComponent(id), {cache:'no-store'});
    const x = await r.json();
    if (!r.ok) { $('viewer-body').innerHTML = `<p>${escape(x.error || 'Unable to load the call')}</p>`; return; }
    const chat = x.channel === 'chat';
    $('viewer-title').textContent = x.name || x.caller_id || (chat ? 'Website chat' : 'Phone call');
    const lines = x.transcript.map(t => `<p class="say say-${escape(t.role)}"><strong>${t.role === 'caller' ? (chat ? 'Visitor' : 'Caller') : (chat ? 'Assistant' : 'Receptionist')}</strong> ${escape(t.text)}</p>`).join('');
    $('viewer-body').innerHTML = `<p><span class="chip">${chat ? 'Website chat' : 'Phone call'}</span> ${callChip(x)} <small class="muted">${escape(when(x.received_at))}${x.seconds != null ? ' · ' + escape(secs(x.seconds)) : ''}${x.language && x.language !== 'en' ? ' · language ' + escape(x.language) : ''}</small></p>` +
      (x.caller_id ? `<p>${phoneLinks(x.caller_id)}</p>` : '') +
      `<h3>Recap</h3><p class="node-evidence">${escape(x.recap || (chat ? 'No recap for this chat.' : 'No recap for this call.'))}</p>` +
      (x.contact_id && contact(x.contact_id) ? `<div class="actions">${btn('Open the contact','call-contact',x.contact_id,true)}</div>` : '') +
      `<h3>Full transcript</h3><div class="transcript">${lines || empty('Nothing was said.')}</div>`;
  }
  async function loadContent() {
    contentFeed = await readFeed('content','api/crm/content');
  }
  const contentWords = {approved:'Ready to post', posted:'Posted', draft:'Draft', published:'Published'};
  const contentChip = st => `<span class="chip content-${escape(st)}">${escape(contentWords[st] || st)}</span>`;
  function contentView() {
    const c = contentFeed, n = c.counts;
    const filters = [['approved','Ready to post',n.ready],['posted','Posted',n.posted],['draft','Drafts',n.draft],['all','All',c.posts.length]];
    const seg = `<div class="actions seg" role="group" aria-label="Show">${filters.map(([id,l,k]) => `<button data-action="content-filter" data-id="${id}" aria-pressed="${contentFilter===id}" class="${contentFilter===id?'primary':''}">${escape(l)} · ${escape(k)}</button>`).join('')}</div>`;
    const shown = c.posts.filter(p => contentFilter === 'all' || p.status === contentFilter);
    const who = p => p.status === 'posted' && p.posted ? `Posted ${escape(day(p.posted.at))}${p.posted.url ? ` · <a href="${escape(p.posted.url)}" target="_blank" rel="noopener">See the post</a>` : ''}`
      : p.approval ? `Approved by ${escape(p.approval.by)} ${escape(day(p.approval.at))}` : 'Waiting for approval';
    const card = p => `<article class="post-card">${p.image ? `<img src="${escape(p.image)}" alt="${escape(p.title + ' ' + p.solution)}" loading="lazy">` : ''}
      <div class="post-body"><div>${contentChip(p.status)} <small class="muted">${escape(p.channel)}</small></div>
      <h2>${escape(p.title)} <span class="accent">${escape(p.solution)}</span></h2>
      <textarea id="caption-${escape(p.id)}" class="caption" readonly aria-label="Caption for ${escape(p.title)}">${escape(p.caption)}</textarea>
      <small class="muted">${who(p)}${p.article_url ? ` · From <a href="${escape(p.article_url)}" target="_blank" rel="noopener">${escape(p.article_title)}</a>` : ''}</small>
      <div class="actions"><button data-action="content-copy" data-id="${escape(p.id)}" class="primary">Copy caption</button>${p.image ? `<a class="button" href="${escape(p.image)}" download="${escape(p.id)}.png">Download image</a>` : ''}</div></div></article>`;
    const art = a => `<li class="row"><div><strong>${a.url ? `<a href="${escape(a.url)}" target="_blank" rel="noopener">${escape(a.title)}</a>` : escape(a.title)}</strong><small>${a.status === 'published' ? 'Live' + (a.published_at ? ' since ' + escape(day(a.published_at)) : '') : a.publish_on ? 'Planned for ' + escape(day(a.publish_on + 'T12:00:00')) : 'No date yet'}${a.approval ? ' · approved by ' + escape(a.approval.by) : ''}</small></div>${contentChip(a.status)}</li>`;
    const planned = c.articles.filter(a => a.status !== 'published' || a.publish_on);
    return title('Content', 'Posts made from your articles, and the articles on the way. Copy a caption and download its image to post it.') +
      `<div class="stats"><div class="stat"><span class="muted">Ready to post</span><strong>${escape(n.ready)}</strong></div><div class="stat"><span class="muted">Posted</span><strong>${escape(n.posted)}</strong></div><div class="stat"><span class="muted">Drafts</span><strong>${escape(n.draft)}</strong></div></div>` +
      (c.note ? `<p class="muted">${escape(c.note)}</p>` : '') +
      `<section class="panel"><div class="top"><h2>Posts</h2>${seg}</div>${shown.length ? `<div class="post-grid">${shown.map(card).join('')}</div>` : empty('Nothing here yet.')}</section>` +
      fold('content:articles', `Articles <span class="chip">${c.articles.length}</span>`, '', planned.length ? `<ul class="plain">${planned.map(art).join('')}</ul>` : empty('No articles planned.')) +
      `<p class="muted">Updated ${c.exported_at ? escape(new Date(c.exported_at).toLocaleString()) : 'unknown'}.</p>`;
  }
  // Research: the pool every signed-in person sees. Industry, then ring, then company, best lane first.
  async function loadResearch() {
    research = await readFeed('research','api/crm/research');
  }
  const laneOf = id => research.lanes.find(l => l.id === id) || {label:id, rank:9};
  const laneChip = id => { const l = laneOf(id); return `<span class="chip lane lane-${escape(l.rank)}">${escape(l.label)}</span>`; };
  const day = v => new Date(v).toLocaleDateString([], {month:'short', day:'numeric'});
  const claimOf = id => (research.claims || []).find(c => c.place_id === id && c.live);
  const holder = c => { const k = claimOf(c.id); if (!k) return ''; return `<span class="chip">${k.status === 'converted' ? 'Contact of '+escape(k.owner_name) : k.mine ? 'Yours' : escape(k.owner_name)+', since '+escape(day(k.claimed_at))}</span>`; };
  async function researchCommand(action, data) {
    const r = await fetch('api/crm/command', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action, data, request_id:crypto.randomUUID()})});
    const res = await r.json(); if (!r.ok) throw Error(res.error || 'Save failed.');
    await loadResearch(); await load(); return res;
  }
  const findCompany = id => { for (const i of research.industries) { const c = i.companies.find(x => x.id === id); if (c) return {c, i}; } return {}; };
  const touchText = id => { const n = (research.touches || {})[id]; return n ? ` · contacted ${n} time${n === 1 ? '' : 's'}` : ''; };
  const requestWords = {queued:'Waiting to run', needs_approval:'Needs approval', approved:'Approved, waiting to run', running:'Running now', done:'Done', failed:'Failed', declined:'Declined'};
  function requestsPanel() {
    const reqs = research.requests || [], pol = research.request_policy || {};
    const ringLabel = n => research.rings[n]?.label || '';
    const indLabel = id => research.industries.find(i => i.id === id)?.label || id;
    const item = q => `<li class="row"><div><strong>${q.kind === 'discover' ? 'Find more: ' + escape(indLabel(q.industry)) + ', ' + escape(ringLabel(q.ring)) : escape(q.body)}</strong><small>${escape(requestWords[q.status] || q.status)} · asked by ${escape(q.actor.startsWith('agent:') ? 'the research worker' : q.actor_name)} ${escape(day(q.created_at))}${q.calls != null ? ' · ' + escape(q.calls) + ' searches' : ''}</small>${q.result ? `<small>${escape(q.result)}</small>` : ''}</div><div class="actions">${pol.can_decide && q.status === 'needs_approval' ? btn('Approve', 'request-approve', q.id, true) + btn('Decline', 'request-decline', q.id) : ''}${pol.can_decide && q.kind === 'question' && q.status === 'queued' ? btn('Answer', 'request-answer', q.id, true) : ''}</div></li>`;
    return fold('research:requests', `Research requests <span class="chip">${reqs.filter(q => ['queued','needs_approval','approved','running'].includes(q.status)).length} open</span>`,
      can('edit') ? btn('Ask for research', 'request-new', '', true) : '',
      `<p class="muted">Searches this month: ${escape(pol.used_this_month ?? 0)} of ${escape(pol.monthly_cap ?? 0)}. Ask for more companies in an industry and distance, or ask a question. A search that could pass the monthly budget waits for approval. Nothing is sent to anyone.</p>` +
      (reqs.length ? `<ul class="plain">${reqs.map(item).join('')}</ul>` : empty('No requests yet.')), false);
  }
  function researchView() {
    const r = research, main = r.lanes.filter(l => l.rank <= 3);
    const total = r.industries.reduce((n, i) => n + i.companies.length, 0);
    const modes = `<div class="actions seg" role="group" aria-label="View">${['list','map'].map(m => `<button data-action="research-mode" data-id="${m}" aria-pressed="${researchMode===m}" class="${researchMode===m?'primary':''}">${m === 'map' ? 'Map' : 'List'}</button>`).join('')}</div>`;
    const head = title('Research', `Best fit for our services first, from ${r.office} outwards.`, modes) +
      `<div class="research"><div class="stats">${main.map(l => `<div class="stat"><span class="muted">${escape(l.label)}</span><strong>${escape(r.totals[l.id] || 0)}</strong></div>`).join('')}</div>` +
      (r.policy ? `<p class="muted">You hold ${escape(r.policy.my_open)} of ${escape(r.policy.cap)} open leads. A lead goes back to the pool after ${escape(r.policy.days)} days with no recorded outcome.${r.policy.rest?.not_interested ? ` Not interested takes a lead off this list for ${escape(Math.round(r.policy.rest.not_interested/30))} months.` : ''}${r.policy.max_touches ? ` ${escape(r.policy.max_touches)} tries in a row with no answer rests it for ${escape(Math.round((r.policy.rest?.no_reply||0)/30))} months.` : ''}</p>` : '');
    const tail = requestsPanel() + (r.resting?.length ? fold('research:resting', `Off the list for now <span class="chip">${r.resting.length}</span>`, '', `<ul class="plain">${r.resting.map(x => `<li class="row"><div><strong>${escape(x.name)}</strong><small>${escape(x.industry)} · ${escape(x.reason)} · back ${escape(day(x.until))}</small></div></li>`).join('')}</ul>`, false) : '') +
      (r.excluded.length ? fold('research:excluded', 'Left out on purpose', '', r.excluded.map(x => `<div class="row"><div><strong>${escape(x.label)}</strong><small>${escape(x.reason)}</small></div></div>`).join(''), false) : '') + `</div>`;
    const none = total ? '' : `<section class="panel"><h2>No companies found yet</h2><p class="muted">Discovery searches Google Places from the office outwards, one ring at a time. It has not run yet${r.center_set ? '' : ', and the office location is not set'}. The industries below are in order of fit.</p></section>`;
    if (researchMode === 'map') return mapView(head, none, tail);
    return head + none + listBody(r) + tail;
  }
  // The list a rep works from (Sam, 2026-09-27): my open leads first, then one dropdown per
  // industry with its best open leads (up_next, computed on the server). Take one and the next
  // moves up; the rest wait behind them.
  function listBody(r) {
    const cap = r.policy?.cap, facts = c => [laneChip(c.lane), c.distance_mi != null ? escape(c.distance_mi) + ' mi' : '', c.reviews != null ? escape(c.reviews) + ' reviews' : '', (research.touches || {})[c.id] ? 'contacted ' + escape(research.touches[c.id]) + 'x' : ''].filter(Boolean).join(' · ');
    const row = (c, actions) => `<li class="lead"><div class="lead-main"><strong>${escape(c.name)}</strong><small>${facts(c)}</small><small class="lead-why">${escape(c.reasons.slice(0, 2).join('; '))}</small></div><div class="actions">${actions}</div></li>`;
    const mine = (r.claims || []).filter(k => k.mine && k.status === 'open').map(k => ({k, c: findCompany(k.place_id).c})).filter(x => x.c);
    const mineHtml = mine.length ? `<section class="panel"><div class="top"><h2>My open leads <span class="chip">${mine.length} of ${escape(cap)}</span></h2></div><p class="muted">Record what happened after every call, email or visit. That keeps the lead yours.</p><ul class="plain">${mine.map(({k, c}) => row(c, (dial(c.phone).length >= 7 ? `<a class="button" href="tel:${escape(dial(c.phone))}">Call</a>` : '') + (can('edit') ? btn('Record outcome', 'research-outcome', c.id, true) : '') + btn('Details', 'research-open', c.id)) + `<li class="lead-note"><small>Yours since ${escape(day(k.claimed_at))}, back to the pool ${escape(day(k.expires_at))} without a new outcome</small></li>`).join('')}</ul></section>` : '';
    const firstOpen = r.industries.find(i => (i.up_next || []).length)?.id;
    const industries = r.industries.map(i => {
      const next = (i.up_next || []).map(id => i.companies.find(c => c.id === id)).filter(Boolean);
      const started = (i.searched_rings || []).length > 0 || i.companies.length > 0;
      const asking = (r.requests || []).some(q => q.kind === 'discover' && q.industry === i.id && ['queued','needs_approval','approved','running'].includes(q.status));
      const head = `${escape(i.rank)}. ${escape(i.label)} <span class="chip">${started ? escape(i.open_count ?? 0) + ' open' : 'Not searched yet'}</span>`;
      let body = next.length ? `<ul class="plain">${next.map(c => row(c, (can('edit') && research.policy ? btn('Take', 'research-claim', c.id, true) : '') + btn('Details', 'research-open', c.id))).join('')}</ul>` : '';
      if (i.backlog) body += `<p class="muted list-foot">${escape(i.backlog)} more waiting behind these. Take one or mark it Not interested, and the next moves up.</p>`;
      if (asking) body += `<p class="muted list-foot">A search for more ${escape(i.label.toLowerCase())} companies is waiting or running. See Research requests below.</p>`;
      else if (!started) body += `<p class="muted">${escape(i.why)}</p>` + (can('edit') ? `<div class="actions"><button data-action="request-new" data-industry="${escape(i.id)}" data-ring="0">Find companies near the office</button></div>` : '');
      else if (!next.length) body += `<p class="muted">No open leads left here. The research worker searches the next ring by itself, inside the monthly search budget.</p>`;
      return fold('research:' + i.id, head, '', body, i.id === firstOpen);
    }).join('');
    return mineHtml + industries;
  }
  // The Research map (Sam, 2026-09-27): the leads a rep can work, on a real map, and a route for
  // a day of drop-ins. OpenStreetMap tiles through Leaflet: free, no key, loaded only when the
  // map opens. The route opens in Google Maps on the phone (a link, so it is free too).
  const LEAFLET = 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/';
  let leafletReady = null, theMap = null, mapLayer = null;
  const route = (() => { try { return JSON.parse(localStorage.getItem('crm.route') || '[]'); } catch (e) { return []; } })();
  const saveRoute = () => { try { localStorage.setItem('crm.route', JSON.stringify(route)); } catch (e) {} };
  let mapIndustry = 'all';
  function loadLeaflet() {
    if (window.L) return Promise.resolve();
    if (leafletReady) return leafletReady;
    leafletReady = new Promise((ok, fail) => {
      const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = LEAFLET + 'leaflet.min.css'; document.head.append(css);
      const js = document.createElement('script'); js.src = LEAFLET + 'leaflet.min.js'; js.onload = ok; js.onerror = () => { leafletReady = null; fail(Error('The map could not load. Check the connection and press Refresh.')); }; document.head.append(js);
    });
    return leafletReady;
  }
  const miles = (a, b) => { const r = 3958.8, t = x => x * Math.PI / 180, dl = t(b.lat - a.lat), dg = t(b.lng - a.lng); const h = Math.sin(dl / 2) ** 2 + Math.cos(t(a.lat)) * Math.cos(t(b.lat)) * Math.sin(dg / 2) ** 2; return 2 * r * Math.asin(Math.sqrt(h)); };
  // Stops in a sensible order: always the nearest next stop, starting at the office.
  function ordered() {
    const home = research.center ? {lat: research.center.latitude, lng: research.center.longitude} : null;
    let left = route.map(id => findCompany(id).c).filter(c => c && c.lat != null), at = home, out = [];
    while (left.length) { const next = at ? left.reduce((b, c) => miles(at, c) < miles(at, b) ? c : b) : left[0]; out.push(next); left = left.filter(c => c !== next); at = next; }
    return out;
  }
  function routeLink(stops) {
    if (!stops.length) return '';
    const p = c => `${c.lat},${c.lng}`, home = research.center ? `${research.center.latitude},${research.center.longitude}` : '';
    const url = 'https://www.google.com/maps/dir/?api=1&travelmode=driving' + (home ? '&origin=' + encodeURIComponent(home) : '') +
      '&destination=' + encodeURIComponent(p(stops[stops.length - 1])) + (stops.length > 1 ? '&waypoints=' + encodeURIComponent(stops.slice(0, -1).map(p).join('|')) : '');
    return `<a class="button primary" href="${escape(url)}" target="_blank" rel="noopener noreferrer">Open route in Google Maps</a>`;
  }
  function routePanel() {
    const stops = ordered(), mine = (research.claims || []).filter(k => k.mine && k.status === 'open').map(k => k.place_id);
    return `<h3>My route <span class="chip">${stops.length} of 10 stops</span></h3>` +
      (stops.length ? `<ol class="route-list">${stops.map(c => `<li><div class="lead-main"><strong>${escape(c.name)}</strong><small>${c.address ? escape(c.address) : ''}</small></div><button data-action="route-remove" data-id="${escape(c.id)}" aria-label="Remove ${escape(c.name)} from the route">Remove</button></li>`).join('')}</ol><div class="actions">${routeLink(stops)}<button data-action="route-clear">Clear</button></div>`
        : '<p class="muted">Tap a pin and choose Add to route. Stops are put in driving order from the office.</p>') +
      (mine.some(id => !route.includes(id)) ? `<div class="actions"><button data-action="route-mine">Add my open leads</button></div>` : '');
  }
  function mapView(head, none, tail) {
    const opts = [['all', 'Every industry'], ...research.industries.filter(i => i.companies.length).map(i => [i.id, i.label])];
    return head + none + `<section class="panel map-panel" id="map-panel"><div class="map-bar"><label for="map-industry" class="sr-only">Industry</label><select id="map-industry">${opts.map(([id, l]) => `<option value="${escape(id)}" ${id === mapIndustry ? 'selected' : ''}>${escape(l)}</option>`).join('')}</select><p class="muted">Pins: the up-next leads per industry and your own. Big pins are yours.</p></div><div class="map-body"><div id="map" class="map" role="region" aria-label="Map of leads"></div><aside class="map-side" id="map-side">${routePanel()}</aside></div><p class="muted map-credit">Map data &copy; OpenStreetMap contributors.</p></section>` + tail;
  }
  function pinPopup(c) {
    const k = claimOf(c.id), inRoute = route.includes(c.id);
    return `<div class="pin"><strong>${escape(c.name)}</strong><div>${laneChip(c.lane)} ${holder(c)}</div><small>${[c.distance_mi != null ? c.distance_mi + ' mi' : '', c.reviews != null ? c.reviews + ' reviews' : ''].filter(Boolean).map(escape).join(' · ')}</small><small>${escape(c.reasons.slice(0, 2).join('; '))}</small>${c.phone ? `<small>${phoneLinks(c.phone)}</small>` : ''}<div class="actions">${!k && can('edit') && research.policy ? btn('Take', 'research-claim', c.id, true) : ''}${btn(inRoute ? 'Remove from route' : 'Add to route', inRoute ? 'route-remove' : 'route-add', c.id)}${btn('Details', 'research-open', c.id)}</div></div>`;
  }
  async function mountMap() {
    const el = $('map'); if (!el || !research) return;
    try { await loadLeaflet(); } catch (err) { el.textContent = err.message; return; }
    if (!$('map')) return;
    if (theMap) { theMap.remove(); theMap = null; }
    const home = research.center ? [research.center.latitude, research.center.longitude] : [33.836, -117.872];
    theMap = L.map('map', {scrollWheelZoom: false}).setView(home, 13);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '&copy; OpenStreetMap'}).addTo(theMap);
    mapLayer = L.layerGroup().addTo(theMap);
    L.circleMarker(home, {radius: 9, color: '#fff', weight: 3, fillColor: '#111', fillOpacity: 1}).addTo(mapLayer).bindTooltip('The office');
    const colour = {1: '#3355e0', 2: '#1c9c5c', 3: '#7d95ff'};
    const mine = new Set((research.claims || []).filter(k => k.mine && k.live).map(k => k.place_id));
    const shown = new Map();
    research.industries.filter(i => mapIndustry === 'all' || i.id === mapIndustry).forEach(i => {
      (i.up_next || []).forEach(id => { const c = i.companies.find(x => x.id === id); if (c) shown.set(c.id, c); });
      i.companies.filter(c => mine.has(c.id)).forEach(c => shown.set(c.id, c));
    });
    route.forEach(id => { const c = findCompany(id).c; if (c) shown.set(c.id, c); });
    const pts = [];
    shown.forEach(c => {
      if (c.lat == null) return;
      const rank = laneOf(c.lane).rank, big = mine.has(c.id) || route.includes(c.id);
      L.circleMarker([c.lat, c.lng], {radius: big ? 10 : 7, color: big ? '#111' : '#fff', weight: big ? 3 : 1.5, fillColor: colour[rank] || '#8a93a6', fillOpacity: .95})
        .addTo(mapLayer).bindPopup(() => pinPopup(c), {maxWidth: 280});
      pts.push([c.lat, c.lng]);
    });
    if (pts.length) theMap.fitBounds(L.latLngBounds([...pts, home]).pad(0.1), {maxZoom: 15});
  }
  const outcomeWords ={no_answer:'No answer yet', refused:'Not interested', interested:'Interested', quoted:'Sent a quote', won:'Won', lost:'Lost after interest'};
  const outcomeHelp = {no_answer:'No answer, left a message, or not the decision maker yet. The lead stays yours.', refused:'They said no. The lead leaves the Research list for everyone.', interested:'They want to hear more. The lead stays yours and stops expiring.', quoted:'You sent a price.', won:'They signed.', lost:'They were interested, then said no. The lead leaves the Research list.'};
  function claimPanel(c) {
    const p = research.policy; if (!p) return '';
    const k = claimOf(c.id), history = (research.outcomes || []).filter(o => o.place_id === c.id);
    let html = '<h3>Who works this lead</h3>';
    const n = (research.touches || {})[c.id] || 0, stageName = state.stages.find(s => s.id === p.stage)?.label;
    if (n) html += `<p class="muted">Contacted ${escape(n)} time${n === 1 ? '' : 's'} so far, by anyone on the team.</p>`;
    if (!k) html += c.lane === 'disqualified' ? '<p class="muted">Disqualified leads cannot be taken.</p>' : `<p class="muted">Nobody has it. Take it and it goes into your pipeline${stageName ? ' as ' + escape(stageName) : ''}, yours for ${escape(p.days)} days; each recorded outcome resets the clock.</p>${can('edit') ? `<div class="actions">${btn('Take this lead','research-claim',c.id,true)}</div>` : ''}`;
    else if (k.status === 'converted') html += `<p>A live relationship, owned by ${escape(k.owner_name)}. It no longer expires.</p>${k.mine ? `<div class="actions">${can('edit') ? btn('Record outcome','research-outcome',c.id,true) : ''}${btn('Open in the pipeline','research-contact',k.contact_id)}</div>` : ''}`;
    else html += `<p>${k.mine ? 'Yours' : escape(k.owner_name)+"'s"}, since ${escape(day(k.claimed_at))}. Goes back to the pool on ${escape(day(k.expires_at))} with no new outcome.</p><div class="actions">${k.mine && can('edit') ? btn('Record outcome','research-outcome',c.id,true)+btn('Open in the pipeline','research-contact',k.contact_id)+btn('Release','research-release',c.id) : ''}${p.can_reassign ? btn('Reassign','research-reassign',c.id) : ''}</div>`;
    const fixable = o => can('edit') && (o.actor === 'human:' + state.person.id || p.can_reassign);
    if (history.length) html += `<h3>History</h3><ul>${history.map(o => `<li>${escape(day(o.created_at))} · ${escape(outcomeWords[o.outcome] || o.outcome)} · ${escape(o.actor_name)}${o.notes ? ': '+escape(o.notes) : ''}${o.edited_at ? ' <small>(edited)</small>' : ''}${fixable(o) ? ' ' + btn('Fix notes', 'research-note-edit', o.id) : ''}</li>`).join('')}</ul>`;
    return html;
  }
  // Leaderboard: the same board for everyone. Wins first, then the work that leads to wins.
  async function loadLeaders() {
    leaders = await readFeed('leaderboard','api/crm/leaderboard?period=' + encodeURIComponent(leaderPeriod));
  }
  const cash = (cents, cur) => (cents/100).toLocaleString(undefined, {style:'currency', currency:cur, maximumFractionDigits:0});
  function leaderView() {
    const b = leaders, place = n => n ? (['1st','2nd','3rd'][n-1] || n+'th') : 'No activity yet';
    const periods = `<div class="actions" role="group" aria-label="Period">${b.periods.map(p => `<button data-action="leader-period" data-id="${escape(p.id)}" aria-pressed="${p.id===b.period}" class="${p.id===b.period?'primary':''}">${escape(p.label)}</button>`).join('')}</div>`;
    const stat = (label, value) => `<div><span class="muted">${escape(label)}</span><strong>${escape(value)}</strong></div>`;
    const rows = b.rows.map(r => `<section class="panel leader${r.you?' you':''}"><div class="leader-head"><span class="place place-${r.rank || 0}">${escape(place(r.rank))}</span><strong>${escape(r.name)}</strong>${r.you?'<span class="chip">You</span>':''}</div>
      <div class="leader-stats">${b.any_closed?stat('Won', cash(r.won_cents, b.currency))+stat('Deals won', r.won_count):''}${stat('Companies interested', r.interested)}${stat('Touches (calls, emails, visits)', r.touches)}${stat('Leads taken', r.leads_taken)}</div>
      ${r.deals.length?`<ul class="plain">${r.deals.map(d => `<li class="row"><div><strong>${escape(d.client)}</strong><small>${escape(d.title)} · ${escape(cash(d.amount_cents, b.currency))} · won ${escape(day(d.won_at))}${d.approximate?' (approximate date)':''}</small></div>${contact(d.contact_id)?btn('Open','open',d.contact_id):''}</li>`).join('')}</ul>`:''}</section>`).join('');
    return title('Leaderboard', 'Who is winning, and who is doing the work that leads to winning. Everyone sees the same board.') + periods +
      (b.any_closed ? '' : `<p class="muted leader-note">${escape(b.empty_note)} The work below still counts.</p>`) +
      `<div class="leaders">${rows || empty('Nobody on the team can work records yet.')}</div>`;
  }
  function showCompany(id) {
    const {c, i} = findCompany(id);
    if (!c) return;
    const lane = laneOf(c.lane), site = /^https?:\/\//i.test(c.url) ? c.url : '';
    const grades = Object.entries(c.grades).map(([k, v]) => `${k.replace(/_/g,' ')} ${v}`).join(' · ');
    $('viewer-title').textContent = c.name;
    $('viewer-body').innerHTML = `<p>${laneChip(c.lane)}</p>${lane.pitch ? `<p class="muted">Pitch: ${escape(lane.pitch)}</p>` : ''}<h3>Why this lane</h3><ul>${c.reasons.map(x => `<li>${escape(x)}</li>`).join('')}</ul>` +
      `<dl class="detail-fields"><dt>Industry</dt><dd>${escape(i.label)}</dd><dt>Distance</dt><dd>${c.distance_mi != null ? escape(c.distance_mi)+' miles from the office' : 'Not measured'}</dd><dt>Reviews</dt><dd>${c.reviews != null ? escape(c.reviews)+(c.rating != null ? ' · rated '+escape(c.rating) : '') : 'Not listed'}</dd><dt>Phone</dt><dd>${phoneLinks(c.phone)}</dd><dt>Address</dt><dd>${mapLink(c.address)}</dd><dt>Website</dt><dd>${site ? `<a href="${escape(site)}" target="_blank" rel="noopener noreferrer">${escape(c.domain || site)}</a>` : 'None'}</dd><dt>Checked</dt><dd>${c.scanned_at ? escape(c.scanned_at.slice(0,10)) + (grades ? ' · '+escape(grades) : '') : 'Not scanned yet'}</dd><dt>Google details</dt><dd>${c.details ? 'Fetched live from Google' : 'Not loaded yet'}</dd></dl>` +
      (c.worst_finding ? `<h3>Top finding</h3><p>${escape(c.worst_finding)}</p>` : '') +
      claimPanel(c) +
      `<h3>Prep package</h3><div id="package"><p class="muted">Loading the package&hellip;</p></div><p id="viewer-note" role="status"></p>`;
    if (!$('viewer').open) $('viewer').showModal();
    loadPackage(id);
  }
  // The prep package. Every part prepares; nothing sends, and nothing prints without approval.
  async function loadPackage(id) {
    try {
      const r = await fetch('api/crm/research/package?place=' + encodeURIComponent(id), {cache:'no-store'});
      const pkg = await r.json(); if (!r.ok) throw Error(pkg.error || 'Unable to load the package');
      if ($('package')) $('package').innerHTML = packageView(pkg, id);
    } catch (err) { if ($('package')) $('package').textContent = err.message; }
  }
  function packageView(pkg, id) {
    const k = claimOf(id), works = (k && k.mine) || research.policy?.can_reassign;
    const e = pkg.email, d = pkg.document, p = pkg.print;
    const steps = `<h4>Approach checklist</h4><ol>${pkg.checklist.map(s => `<li>${escape(s)}</li>`).join('')}</ol>`;
    const sc = pkg.tailored_script || pkg.script, tailored = !!pkg.tailored_script;
    const script = sc ? `<details class="group script" data-group="script" ${folded.get('script') === false ? '' : 'open'}><summary><span>Phone script${tailored ? ', written for this lead' : sc.status === 'approved' ? '' : ' (draft, Sam has not approved the wording yet)'}</span></summary>${tailored ? '' : `<p class="muted">${d && d.status === 'requested' ? 'A script written for this lead is on its way (a few minutes).' : 'The general script. Take the lead and a script written for this lead follows in a few minutes.'}</p>`}
      <p><strong>Open:</strong> ${escape(sc.opener)}</p><p><strong>The one fact:</strong> ${escape(sc.fact)}</p>
      <p><strong>Ask:</strong></p><ul>${sc.questions.map(q => `<li>${escape(q)}</li>`).join('')}</ul><p><strong>Close:</strong> ${escape(sc.ask)}</p>
      <p><strong>If they say...</strong></p><dl class="objections">${sc.objections.map(o => `<dt>"${escape(o.q)}"</dt><dd>${escape(o.a)}</dd>`).join('')}</dl>
      <p><strong>Voicemail:</strong> ${escape(sc.voicemail)}</p></details>` : '';
    const email = `<h4>Email draft</h4>${e.ready ? '' : `<p class="overdue">Not ready to send${e.missing.length ? ': add '+escape(e.missing.join(', ')) : ''}${e.problems.length ? ': '+escape(e.problems.join('; ')) : ''}.</p>`}<p class="muted">Nothing is sent from here. Copy it into your own email.</p><label for="p-subject">Subject</label><input id="p-subject" readonly value="${escape(e.subject)}"><label for="p-body">Draft</label><textarea id="p-body" rows="10" readonly>${escape(e.body)}</textarea><div class="actions">${copyBtn('Copy subject','p-subject')}${copyBtn('Copy draft','p-body',true)}</div>`;
    let doc = '<h4>Research document</h4>';
    if (!d) doc += `<p class="muted">Not written yet. It is written from our own scan and Places data only, together with a phone script for this lead.</p>${works ? btn('Write research document and script','pkg-doc',id) : ''}`;
    else if (d.status === 'requested') doc += '<p class="muted">Requested. The document worker writes it; if it does not appear soon, ask Sam to run it.</p>';
    else if (d.status === 'failed') doc += `<p class="overdue">The last draft failed its check: ${escape(d.problems.join('; '))}.</p>${works ? btn('Try again','pkg-doc',id) : ''}`;
    else doc += `<div class="node-evidence doc-text" id="p-doc">${escape(d.body)}</div><div class="actions">${copyBtn('Copy document','p-doc')}${works ? btn('Write it again','pkg-doc',id) : ''}</div>`;
    let print = '<h4>Printout</h4>';
    if (!pkg.printable) print += '<p class="muted">No printout for this lead: it is built from a scan of their website.</p>';
    else if (!p || p.status === 'revoked') print += `<p class="muted">Sam approves every printout before it prints.</p>${works ? btn('Ask Sam to approve the printout','pkg-print',id) : ''}`;
    else if (p.status === 'requested') print += `<p class="muted">Waiting for Sam's approval.</p>${pkg.can_approve ? btn('Approve','pkg-approve',id,true)+btn('Keep it back','pkg-revoke',id) : ''}`;
    else if (!p.current) print += `<p class="muted">Their site was scanned again since the approval. Ask again.</p>${works ? btn('Ask Sam to approve the printout','pkg-print',id) : ''}`;
    else print += `<p>Approved by ${escape(p.reviewed_by)}.</p><div class="actions"><a class="button primary" href="api/crm/research/printout?place=${encodeURIComponent(id)}" target="_blank" rel="noopener">Open printout</a>${pkg.can_approve ? btn('Revoke','pkg-revoke',id) : ''}</div>`;
    return script + steps + email + doc + print;
  }
  async function packageCommand(action, id) {
    const pr = action === 'pkg-approve' || action === 'pkg-revoke';
    const r = await fetch('api/crm/research/package?place=' + encodeURIComponent(id), {cache:'no-store'});
    const pkg = await r.json();
    const name = {'pkg-doc':'research.document.request','pkg-print':'research.print.request'}[action] || 'research.print.review';
    const data = pr ? {place_id:id, revision:pkg.print.revision, decision:action==='pkg-approve'?'approved':'revoked'} : {place_id:id};
    await researchCommand(name, data);
    await loadPackage(id);
  }
  // Where a lead came from: its own record, else its company's (R4, three levels).
  function sourceOf(c) {
    const own = c && (c.source_channel || c.source_campaign || c.source_ad) ? c : (c && c.company_id ? contact(c.company_id) : null) || c || {};
    const parts = [own.source_channel, own.source_campaign, own.source_ad].filter(Boolean);
    return {channel: own.source_channel || 'Not recorded', campaign: own.source_campaign || '', text: parts.join(' · ') || 'Not recorded'};
  }
  const channels = () => [...new Set([...(state.source_channels || []), ...state.contacts.map(c => c.source_channel).filter(Boolean)])];
  // Reports: from the records this person may see, nothing else. Money is what the deals say.
  function reportsView() {
    const people = state.contacts.filter(c => c.kind !== 'business' && !(c.kind === 'person' && c.company_id));
    const out = id => state.stages.find(s => s.id === id)?.outcome || '';
    const keyOf = c => { const src = sourceOf(c); return src.channel + (src.campaign ? ' · ' + src.campaign : ''); };
    const groups = new Map();
    people.forEach(c => { const key = keyOf(c); if (!groups.has(key)) groups.set(key, {key, leads:0, open:0, won:0, won_cents:0, lost:0}); groups.get(key).leads++; });
    state.deals.forEach(d => { const c = clientOf(contact(d.contact_id)); if (!c || c.kind === 'business') return; const g = groups.get(keyOf(c)); if (!g) return; const o = out(d.stage); if (o === 'won') { g.won++; g.won_cents += d.amount_cents; } else if (o === 'lost') g.lost++; else g.open++; });
    const rows = [...groups.values()].sort((a, b) => b.won_cents - a.won_cents || b.leads - a.leads);
    const cash = cents => (cents / 100).toLocaleString(undefined, {style:'currency', currency:state.currency, maximumFractionDigits:0});
    const bySource = rows.length ? `<div class="table-wrap"><table><thead><tr><th scope="col">Came from</th><th scope="col">Leads</th><th scope="col">Open</th><th scope="col">Won</th><th scope="col">Won value</th><th scope="col">Lost</th></tr></thead><tbody>${rows.map(g => `<tr><th scope="row">${escape(g.key)}</th><td>${g.leads}</td><td>${g.open}</td><td>${g.won}</td><td>${escape(cash(g.won_cents))}</td><td>${g.lost}</td></tr>`).join('')}</tbody></table></div>` : empty('No contacts yet.');
    const byStage = `<div class="table-wrap"><table><thead><tr><th scope="col">Stage</th><th scope="col">Cards</th><th scope="col">Value</th></tr></thead><tbody>${state.stages.map(s => { const ds = state.deals.filter(d => d.stage === s.id); return `<tr><th scope="row">${multi() ? escape(pipelines().find(p => p.id === lineOf(s))?.label) + ' · ' : ''}${escape(s.label)}</th><td>${ds.length}</td><td>${escape(cash(ds.reduce((n, d) => n + d.amount_cents, 0)))}</td></tr>`; }).join('')}</tbody></table></div>`;
    return title('Reports', 'Where leads come from and what they turn into, from the records you can see. Ad spend and cost per lead come once ad accounts are connected.','',false) + earningsPanel() +
      `<section class="panel"><h2>By source</h2>${bySource}</section><section class="panel"><h2>By stage</h2>${byStage}</section>`;
  }
  // My earnings: an estimate from won deals and the profile's commission plans (commission.py).
  function earningsPanel() {
    const cm = state.commission; if (!cm || !Object.keys(cm.members).length) return '';
    const won = state.deals.filter(d => state.stages.find(s => s.id === d.stage)?.outcome === 'won' && d.won_by);
    const pct = x => Math.round(x * 100) + '%';
    const rows = Object.entries(cm.members).map(([pid, plan]) => {
      const p = cm.plans[plan], mine = won.filter(d => d.won_by === pid);
      const setup = mine.reduce((n, d) => n + d.amount_cents * p.setup, 0), monthly = mine.reduce((n, d) => n + (d.monthly_cents || 0) * p.monthly, 0);
      const who = pid === state.person.id ? 'You' : (state.members.find(m => m.id === pid)?.name || pid);
      return `<tr><th scope="row">${escape(who)}<small>${escape(p.label)}: ${pct(p.setup)} of setup, ${pct(p.monthly)} of monthly${p.monthly_months ? ' for ' + escape(p.monthly_months) + ' months' : ''}</small></th><td>${mine.length}</td><td>${escape(cashOf(Math.round(setup), state.currency))}</td><td>${escape(cashOf(Math.round(monthly), state.currency))}/mo</td></tr>`;
    }).join('');
    const title = Object.keys(cm.members).length === 1 && cm.members[state.person.id] ? 'My earnings (estimate)' : 'Commission (estimate)';
    return `<section class="panel"><h2>${escape(title)}</h2><div class="table-wrap"><table><thead><tr><th scope="col">Person</th><th scope="col">Deals won</th><th scope="col">From setups</th><th scope="col">From monthly fees</th></tr></thead><tbody>${rows}</tbody></table></div><p class="muted">${escape(cm.note)}</p></section>`;
  }
  const lineSelect = s => multi() ? `<select data-line aria-label="Pipeline">${pipelines().map(p => `<option value="${escape(p.id)}" ${p.id === lineOf(s) ? 'selected' : ''}>${escape(p.label)}</option>`).join('')}</select>` : '';
  const stageRow = s => `<li data-id="${escape(s.id || '')}" data-outcome="${escape(s.outcome || '')}"><input aria-label="Stage name" maxlength="40" value="${escape(s.label || '')}" required>${lineSelect(s)}<div class="actions"><button type="button" data-stage="up" aria-label="Move up">&uarr;</button><button type="button" data-stage="down" aria-label="Move down">&darr;</button>${s.outcome ? `<small>${s.outcome === 'won' ? 'Marks a win' : 'Marks a loss'}</small>` : '<button type="button" data-stage="remove">Remove</button>'}</div></li>`;
  function syncStageMoves() {
    const box = $('stage-moves'); if (!box) return;
    const live = [...$('stage-rows').children].filter(li => !li.hidden && !li.dataset.outcome && li.dataset.id);
    box.innerHTML = [...$('stage-rows').children].filter(li => li.hidden && li.dataset.id).map(li => {
      const n = state.deals.filter(d => d.stage === li.dataset.id).length; if (!n) return '';
      const name = state.stages.find(x => x.id === li.dataset.id)?.label || li.dataset.id;
      return `<label for="move-${escape(li.dataset.id)}">Move the ${n} card${n === 1 ? '' : 's'} in ${escape(name)} to</label><select id="move-${escape(li.dataset.id)}" data-from="${escape(li.dataset.id)}">${live.map(x => `<option value="${escape(x.dataset.id)}">${escape(x.querySelector('input').value)}</option>`).join('')}</select>`;
    }).join('');
  }
  $('fields').addEventListener('click', e => {
    const ch = e.target.closest('[data-chan]');
    if (ch) { dirty = true; if (ch.dataset.chan === 'remove') ch.closest('.chan-row').remove(); else { $('chan-rows').insertAdjacentHTML('beforeend', chanRow({kind:ch.dataset.chan})); $('chan-rows').lastElementChild.querySelector('[data-ch=value]').focus(); } return; }
    const b = e.target.closest('[data-stage]'); if (!b) return; dirty = true;
    if (b.dataset.stage === 'add') { $('stage-rows').insertAdjacentHTML('beforeend', stageRow({label:'',pipeline:currentLine().id})); $('stage-rows').lastElementChild.querySelector('input').focus(); return; }
    const li = b.closest('li');
    if (b.dataset.stage === 'up' && li.previousElementSibling) li.parentNode.insertBefore(li, li.previousElementSibling);
    if (b.dataset.stage === 'down' && li.nextElementSibling) li.parentNode.insertBefore(li.nextElementSibling, li);
    if (b.dataset.stage === 'remove') { if (li.dataset.id) { li.hidden = true; li.querySelector('input').required = false; } else li.remove(); }
    syncStageMoves();
  });
  // Deleted records (batch 3): only an administrator gets `state.deleted`, and only they restore.
  function deletedPanel() {
    const d = state.deleted; if (!d || !(d.contacts.length + d.deals.length)) return '';
    const who = x => member(String(x.deleted_by || '').replace('human:', ''));
    const rows = d.contacts.map(c => `<div class="row"><div><strong>${escape(c.name)}</strong><small>${escape(c.kind)} · deleted ${escape(when(c.deleted_at))} by ${escape(who(c))}</small></div>${btn('Restore','contact-restore',c.id)}</div>`).join('') +
      d.deals.map(x => `<div class="row"><div><strong>${escape(x.title)}</strong><small>Deal on ${escape(x.contact_name)} · deleted ${escape(when(x.deleted_at))} by ${escape(who(x))}</small></div>${btn('Restore','deal-restore',x.id)}</div>`).join('');
    return fold('contacts:deleted', `Deleted <span class="chip">${d.contacts.length + d.deals.length}</span>`, '', `<p class="muted">Deleted contacts and deals leave every list. Restore brings one back with its deals, to-dos and notes.</p>${rows}`, false);
  }
  function renderContacts() {
    const rows=state.contacts.filter(c=>ownerOk(c)&&(lifecycle==='all'||c.lifecycle===lifecycle)&&(channel==='all'||sourceOf(c).channel===channel)&&[c.name,c.email,c.phone,c.address,c.territory,c.territory&&stateName(c.territory),contact(c.company_id)?.name,...chans(c).map(x=>x.value)].join(' ').toLowerCase().includes(filter.toLowerCase()));
    $('contact-list').innerHTML=rows.map(c=>`<div class="row"><div><strong>${escape(c.name)}</strong><small>${escape(c.kind)} · ${escape(c.lifecycle)} · ${escape(member(c.owner))}${c.company_id?' · '+escape(contact(c.company_id)?.name):''}</small></div>${btn('Open','open',c.id)}</div>`).join('') || empty('No matching contacts. Add a person or company to get started.');
  }
  const historyShown=new Map(),historyKind=new Map(),historyKinds={notes:['note','call','meeting','email','chat'],tasks:['task','task_reopened']};
  function renderRecord() {
    const c=contact(selected), deals=state.deals.filter(d=>d.contact_id===c.id), activities=state.activities.filter(a=>a.contact_id===c.id),historyLimit=historyShown.get(c.id)||20,historyFilter=historyKind.get(c.id)||'all',history=activities.filter(a=>historyFilter==='all'||(historyKinds[historyFilter]?historyKinds[historyFilter].includes(a.kind):![...historyKinds.notes,...historyKinds.tasks].includes(a.kind)));
    $('content').innerHTML=`<nav class="workspace-breadcrumb" aria-label="Breadcrumb"><button data-nav="contacts">Contacts</button><span aria-hidden="true">›</span><span aria-current="page">${escape(c.name)}</span></nav>`+title(c.name,`${c.kind} · ${c.lifecycle} · ${member(c.owner)}`,'',false)+
      `<div class="split record-layout"><div>${todoList(c)}<section class="panel" id="record-history"><div class="top"><h2>Relationship history</h2><label class="history-filter">Show<select id="history-kind" data-contact="${escape(c.id)}" aria-label="History type">${[['all','Everything'],['notes','Notes and calls'],['tasks','Task changes'],['other','Outcomes and other']].map(([id,label])=>`<option value="${id}" ${historyFilter===id?'selected':''}>${label}</option>`).join('')}</select></label><div class="actions">${can('edit')?btn('Add note / log call','activity-new',c.id,true):''}</div></div>${history.slice(0,historyLimit).map(a=>`<article class="timeline"><strong>${escape({task:'Task completed',task_reopened:'Task reopened',outcome:'Outcome'}[a.kind]||a.kind)}</strong><small>${escape(when(a.created_at))} · ${escape(state.actors[a.actor] || a.actor)}${a.edited_at?' · Edited '+escape(when(a.edited_at)):''}</small><p>${escape(a.body)}</p>${(state.call_of||{})[a.id]?`<div class="actions">${btn('Full transcript','call-open',state.call_of[a.id])}</div>`:''}${canChange(a)?`<div class="actions">${btn('Edit','activity-edit',a.id)}</div>`:''}</article>`).join('') || empty('No conversations recorded yet. Add your first note or call outcome.')}<p class="muted">Showing ${Math.min(historyLimit,history.length)} of ${history.length}${history.length!==activities.length?' matching':''} entries</p><div class="actions">${history.length>historyLimit?btn('Show '+Math.min(20,history.length-historyLimit)+' older entries','history-more',c.id):''}${historyLimit>20?btn('Show recent only','history-recent',c.id):''}</div></section>
      <section class="panel" id="record-deals"><div class="top"><h2>Deals</h2>${can('create')?btn('Create deal','deal-new',c.id):''}</div>${deals.map(d=>`<div class="row"><div><strong>${escape(d.title)}</strong><small>${escape(stage(d.stage)?.label || d.stage)} · ${money(d)}${d.offer?' · '+escape(offerLabel(d.offer)):''}</small>${handoffWords[d.handoff]?`<span class="chip late">${escape(handoffWords[d.handoff])}</span>`:''}</div><div class="actions">${can('edit')&&!handoffWords[d.handoff]?btn('Ask Sam for a quote','handoff',d.id+'|quote')+btn('Signed: start onboarding','handoff',d.id+'|signed'):''}${can('assign')&&handoffWords[d.handoff]?btn('Mark done','handoff',d.id+'|done',true):''}${can('edit')?btn('Move stage','deal-stage',d.id)+btn('Edit deal','deal-edit',d.id)+btn('Delete','deal-delete',d.id):''}</div></div>`).join('') || (can('create')&&c.kind!=='business'&&openStages().length?`<div class="row"><div><strong>Not in the pipeline yet</strong><small>Choose a stage to put ${escape(c.name)} on the Pipeline board.</small></div>${btn('Add to pipeline','pipeline-add',c.id,true)}</div>`:empty('No deals yet. You can create more than one for this contact.'))}</section></div>
      <div>${researchPanel(c)}<section class="panel" id="record-details"><div class="top"><h2>Contact details</h2>${can('edit')?btn('Edit','contact-edit',c.id):''}</div><dl class="detail-fields">${c.role?`<dt>Role</dt><dd>${escape(c.role)}</dd>`:''}${chans(c).length ? chans(c).map(x=>`<dt>${escape(chanLabel(x))}</dt><dd>${x.kind==='phone'?phoneLinks(x.value):mailLink(x.value)}</dd>`).join('') : '<dt>Phone</dt><dd>Not provided</dd><dt>Email</dt><dd>Not provided</dd>'}${c.address?`<dt>Address</dt><dd>${mapLink(c.address)}</dd>`:''}${c.territory&&states().length?`<dt>State</dt><dd>${escape(stateName(c.territory))}</dd>`:''}${c.website?`<dt>Website</dt><dd><a href="${escape(c.website)}" target="_blank" rel="noopener noreferrer">${escape(c.website.replace(/^https?:\/\//,'').replace(/\/$/,''))}</a></dd>`:''}<dt>Came from</dt><dd>${escape(sourceOf(c).text)}</dd><dt>Company</dt><dd>${c.company_id?btn(escape(contact(c.company_id)?.name),'open',c.company_id):escape(c.kind==='company'?'Company record':'None linked')}</dd></dl>${can('assign')&&!c.company_id?btn('Assign owner','assign',c.id):''}${can('edit')&&c.kind!=='business'?btn('Delete contact','contact-delete',c.id):''}</section>
      ${c.kind==='company'?`<section class="panel" id="record-people"><div class="top"><h2>People at this company</h2>${can('create')?btn('Add a person','person-new',c.id,true):''}</div>${peopleAt(c).map(p=>`<div class="row"><div><strong>${escape(p.name)}</strong><small>${[p.role?escape(p.role):'',...chans(p).filter(x=>x.kind==='phone').map(x=>escape(chanLabel(x))+' '+phoneLinks(x.value))].filter(Boolean).join(' · ')||'No phone yet'}</small></div>${btn('Open person','open',p.id)}</div>`).join('')||empty('No people linked yet. Add the person you spoke to.')}</section>`:''}</div></div>`+`<div id="record-builds">${buildTree(c)}</div>`;
    const layout=$('content').querySelector('.record-layout');
    const sections=[['details','Contact details'],['tasks','Tasks'],['people','People'],['deals','Deals'],['builds','Systems'],['research','Research'],['history','History']].filter(([id])=>$('record-'+id)?.textContent.trim());
    layout.replaceChildren(...sections.map(([id])=>$('record-'+id)));
    layout.insertAdjacentHTML('beforebegin',`<nav class="record-sections" aria-label="Contact sections">${sections.map(([id,label])=>`<a href="#record-${id}">${label}</a>`).join('')}</nav>`);
  }
  // A note is changed by the person who wrote it, or by an administrator (crm_store: activity.update).
  const canChange = a => can('edit') && ['note','call','meeting','email'].includes(a.kind) && (a.actor === 'human:' + state.person.id || (can('assign') && state.members.length > 0));
  const chanRow = x => `<div class="chan-row"><select data-ch="kind" aria-label="Type"><option value="phone" ${x.kind==='phone'?'selected':''}>Phone</option><option value="email" ${x.kind==='email'?'selected':''}>Email</option></select><input data-ch="label" aria-label="Label" list="chan-labels" maxlength="40" placeholder="Direct, Cell" value="${escape(x.label||'')}"><input data-ch="value" aria-label="Number or address" maxlength="254" value="${escape(x.value||'')}"><button type="button" data-chan="remove" aria-label="Remove this line">Remove</button></div>`;
  function researchPanel(c) {
    const link=(state.research_links||{})[c.id];if(!link)return '';
    const mine=c.owner===state.person.id||state.members.length;
    const words=link.resting?'Off the Research list: '+link.rest_reason+', back '+day(link.rest_until):link.status==='converted'?'A live relationship. It no longer expires.':link.live?'Taken from Research. It stays yours while you record outcomes.':'Back in the Research pool. Anyone can take it.';
    return `<section class="panel" id="record-research"><h2>From Research</h2><p class="muted">${escape(words)}</p>${link.live&&mine&&can('edit')?`<div class="actions">${btn('Record outcome','record-outcome',link.place_id,true)}</div>`:''}</section>`;
  }
  const nodeRecord = (cid,nid) => state.client_nodes.find(n=>n.contact_id===cid&&n.node_id===nid);
  const nodeLabel = status => ({built:'Built',in_progress:'In progress',recommended:'Recommended',ghost:'Possible next build'}[status]);
  const treeIcon = '<span class="tree-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/><path d="M17 10V7h-3M7 14v3h3"/></svg></span>';
  function buildTree(c) {
    const catalog=state.tech_tree_catalog;
    if(c.kind==='business'||c.lifecycle!=='customer'||!catalog?.nodes.length)return '';
    const records=state.client_nodes.filter(n=>n.contact_id===c.id),built=records.filter(n=>n.state==='built').length,progress=records.filter(n=>n.state==='in_progress').length;
    return fold('build-out:'+c.id, `Build-out <span class="chip">${built} built · ${progress} in progress</span>`, '', `<div class="build-out"><p class="muted">Each status shows the recorded build state, not a live health check. Select a build to see the evidence or plan the work.</p><div class="tree-hub">${treeIcon}<div><strong>${escape(c.name)}</strong><small>Your build-out, by layer</small></div></div><div class="tree-layers">${catalog.layers.map(layer=>`<section class="tree-layer"><h3>${escape(layer.label)}</h3><div class="tree-nodes">${catalog.nodes.filter(n=>n.layer===layer.id).map(n=>{const r=nodeRecord(c.id,n.id),status=r?.state||'ghost';return `<button class="tree-node ${status}" data-tone="${/publishing off/i.test(r?.evidence||'')?'warning':''}" data-action="node-view" data-id="${escape(n.id)}">${treeIcon}<span><strong>${escape(n.label)}</strong>${WorkspaceDesign.installation(r)}</span></button>`;}).join('')}</div></section>`).join('')}</div></div>`, false);
  }
  function showNode(id) {
    const n=state.tech_tree_catalog.nodes.find(n=>n.id===id),r=nodeRecord(selected,id),status=r?.state||'ghost',review=state.node_reviews?.find(x=>x.id===r?.id);
    const approved=review?.decision==='approved'&&review.node_revision===r?.revision;
    const prereqs=n.prerequisites.map(id=>{const p=state.tech_tree_catalog.nodes.find(n=>n.id===id);return escape(p.label)+' ('+(nodeRecord(selected,id)?.state==='built'?'built':'not recorded as built')+')';});
    $('viewer-title').textContent=n.label;
    $('viewer-body').innerHTML=`<p>${WorkspaceDesign.installation(r)}</p><p>${escape(n.value)}</p>${prereqs.length?`<p class="muted">Prerequisites: ${prereqs.join(', ')}</p>`:''}${r?.evidence?`<h3>Evidence / reason</h3><p class="node-evidence">${escape(r.evidence)}</p>`:''}${r?.location?`<h3>Where it lives</h3><p class="node-evidence">${escape(r.location)}</p>`:''}${r?.brief_path?`<h3>Brief</h3><p class="node-evidence">${escape(r.brief_path)}</p><label for="v-prompt">Start command</label><textarea id="v-prompt" readonly rows="3">${escape('Read '+r.brief_path+' and do it.')}</textarea>${copyBtn('Copy start command','v-prompt')}`:''}<p class="muted">Work sessions are prepared here. Nothing launches automatically.</p>${status==='recommended'?`<p><strong>Client visibility:</strong> ${approved?'Approved for the client view':'Internal only'}</p>${approved?`<p>${escape(review.public_reason)}</p>`:''}${state.can_review_buildout?btn(approved?'Review or revoke client approval':'Review for client view','node-review',id):''}`:''}<div class="actions">${can('edit')?btn('Update build state','node-edit',id)+(status==='ghost'||status==='recommended'?btn('Plan this build','node-plan',id,true):''):''}</div><p id="viewer-note" role="status"></p>`;
    $('viewer').showModal();
  }
  const field=(label,name,value='',type='text',required=false)=>`<label for="f-${name}">${label}</label><input id="f-${name}" name="${name}" type="${type}" value="${escape(value)}" ${required?'required':''} ${type==='text'?'maxlength="200"':''}>`;
  const select=(label,name,options,value)=>`<label for="f-${name}">${label}</label><select id="f-${name}" name="${name}">${options.map(o=>`<option value="${escape(o.id)}" ${o.id===value?'selected':''}>${escape(o.label)}</option>`).join('')}</select>`;
  const openStages=()=>state.stages.filter(s=>!s.outcome);
  const firstOpen=()=>(stagesIn(currentLine().id).find(s=>!s.outcome)||openStages()[0])?.id;
  // A stage picker; with more than one pipeline, the stages are grouped under their pipeline's name.
  const stageSelect=(label,name,options,value)=>{
    if(!multi())return select(label,name,options,value);
    const opt=o=>`<option value="${escape(o.id)}" ${o.id===value?'selected':''}>${escape(o.label)}</option>`;
    return `<label for="f-${name}">${label}</label><select id="f-${name}" name="${name}">${options.filter(o=>!o.id).map(opt).join('')}${pipelines().map(p=>{const list=options.filter(o=>o.id&&lineOf(o)===p.id);return list.length?`<optgroup label="${escape(p.label)}">${list.map(opt).join('')}</optgroup>`:'';}).join('')}</select>`;
  };
  const choices=values=>values.map(v=>({id:v,label:v[0].toUpperCase()+v.slice(1)}));
  function editor(action,id,preset={}) {
    if(action==='task-new'){TaskCreate.open(id);return;}if(action==='task-edit'){TaskEditor.open(id);return;}
    pending=null;dirty=false;editing={action,id};let fields='',label='';
    if(action==='stages-edit') {
      label='Edit pipeline stages';editing.revision=state.stages_revision;
      fields='<p class="muted">Rename, reorder, add or remove stages. The win and loss stages keep their meaning, so they can be renamed but not removed.'+(multi()?' Each stage belongs to the pipeline picked beside it.':'')+'</p><ol class="stage-rows" id="stage-rows">'+state.stages.map(stageRow).join('')+'</ol><div class="actions"><button type="button" data-stage="add">Add a stage</button></div><div id="stage-moves"></div>';
    } else if(action==='request-new') {
      label='Ask for research';
      const inds=research.industries.map(i=>({id:i.id,label:i.label})),rings=research.rings.map(g=>({id:String(g.index),label:g.label}));
      fields=select('What do you need','kind',[{id:'discover',label:'Find more companies'},{id:'question',label:'Ask a question'}],'discover')+'<div id="req-discover">'+select('Industry','industry',inds,preset.industry||inds[0]?.id)+select('Distance from the office','ring',rings,preset.ring?String(preset.ring):'0')+`<p class="muted">The research worker counts the searches first. Inside this month's budget (${escape(research.request_policy?.used_this_month??0)} of ${escape(research.request_policy?.monthly_cap??0)} used) it runs by itself; past it, it waits for approval.</p></div><div id="req-question" hidden><label for="f-body">Your question</label><textarea id="f-body" name="body" rows="4" maxlength="1000" required></textarea><p class="muted">A person answers it here. Nothing runs by itself.</p></div>`;
    } else if(action==='research-note-edit') {
      const o=research.outcomes.find(x=>x.id===id);editing.revision=o.revision;label='Fix the notes';
      fields=`<p class="muted">${escape(outcomeWords[o.outcome]||o.outcome)}, ${escape(day(o.created_at))}. Only the notes change. To correct the outcome itself, record a new one.</p><label for="f-notes">Notes</label><textarea id="f-notes" name="notes" rows="5" maxlength="4000">${escape(o.notes)}</textarea><p class="muted">The line will show "edited", and the old text is kept in the record's history.</p>`;
    } else if(action==='request-answer') {
      const q=research.requests.find(x=>x.id===id);editing.revision=q.revision;label='Answer the question';
      fields=`<p>${escape(q.body)}</p><label for="f-note">Answer</label><textarea id="f-note" name="note" rows="6" maxlength="4000" required></textarea>`;
    } else if(action==='research-outcome'||action==='research-reassign') {
      const k=claimOf(id),{c}=findCompany(id);editing.revision=k.revision;
      if(action==='research-outcome'){label='Record outcome: '+c.name;const n=(research.touches||{})[id]||0;fields=`<p class="muted">Contact number ${n+1} with this company.</p><label for="f-outcome">What happened</label><select id="f-outcome" name="outcome" required><option value="" selected disabled>Choose what happened</option>${Object.entries(outcomeWords).map(([id,label])=>`<option value="${id}">${escape(label)}</option>`).join('')}</select>`+'<p class="muted" id="outcome-help"></p>'+select('How you reached out','channel',[{id:'call',label:'Call'},{id:'email',label:'Email'},{id:'in_person',label:'In person'},{id:'text',label:'Text'}],'call')+select('Offer','offer',[{id:'A',label:'Offer A'},{id:'B',label:'Offer B'},{id:'both',label:'Both'}],'A')+'<label for="f-notes">Notes (optional)</label><textarea id="f-notes" name="notes" rows="3" maxlength="4000"></textarea>'+`<div id="next-step"><label for="f-next">Next step</label><select id="f-next" name="next"><option value="">No follow-up</option><option value="1">Tomorrow, 9 am</option><option value="2">In 2 days, 9 am</option><option value="7">In a week, 9 am</option><option value="pick">Pick a date and time</option></select><div id="next-pick" hidden><label for="f-next_at_local">When</label><input id="f-next_at_local" name="next_at_local" type="datetime-local"></div><label for="f-next_title">To-do</label><input id="f-next_title" name="next_title" maxlength="200" value="${escape('Call back ' + c.name)}"></div>`;}
      else{label='Reassign: '+c.name;fields=select('Owner','owner',research.policy.members,k.owner);}
    } else if(action==='node-review') {
      const n=state.tech_tree_catalog.nodes.find(n=>n.id===id),r=nodeRecord(selected,id),review=state.node_reviews?.find(x=>x.id===r.id);
      editing.revision=review?.revision||0;editing.node_revision=r.revision;editing.contact_id=selected;
      label='Review for client view';
      fields=`<p><strong>${escape(n.label)}</strong></p><p>${escape(n.value)}</p><p class="muted">Approve this wording and a client-facing reason. Editing the build later invalidates this approval. Nothing is sent or deployed.</p>`+select('Client visibility','decision',[{id:'approved',label:'Approve for client view'},{id:'revoked',label:'Keep internal / revoke approval'}],review?.decision||'approved')+'<label for="f-reason">Reason the client will see (required to approve)</label><textarea id="f-reason" name="reason" rows="4" maxlength="600">'+escape(review?.public_reason||'')+'</textarea>';
    } else if(action==='node-edit') {
      const n=state.tech_tree_catalog.nodes.find(n=>n.id===id),r=nodeRecord(selected,id)||{};
      editing.revision=r.revision||0;editing.contact_id=selected;label=n.label;
      fields=select('Build state','state',[{id:'recommended',label:'Recommended'},{id:'in_progress',label:'In progress'},{id:'built',label:'Built'}],r.state||'recommended')+field('Brief path (optional)','brief_path',r.brief_path)+field("Location in the client’s own repository",'location',r.location)+'<label for="f-evidence">Evidence or recommendation reason</label><textarea id="f-evidence" name="evidence" rows="5" maxlength="4000">'+escape(r.evidence||'')+'</textarea>';
    } else if(action==='contact-new'||action==='contact-edit') {
      const c=action==='contact-edit'?contact(id):{},at=preset.company_id&&contact(preset.company_id);editing.revision=c.revision;editing.stay=at?at.id:null;
      label=at?'Add a person at '+at.name:action==='contact-new'?'Add a contact':'Edit contact';
      if(!at&&c.kind!=='business') fields+=select('Contact type','kind',choices(['person','company']),c.kind||'person')+(action==='contact-edit'?'<p class="muted">Saved as the wrong type? Change it here. Deals, to-dos and notes stay on it.</p>':'');
      if(c.kind!=='business') fields+='<div id="company-fields">'+select('Company','company_id',[{id:'',label:'No company'},...state.contacts.filter(x=>x.kind==='company'&&x.owner===(c.owner||at?.owner||state.person.id)).map(x=>({id:x.id,label:x.name})),...(can('create')?[{id:'__new__',label:'+ Add a new company'}]:[])],c.company_id||at?.id||'')+'<div id="new-company" hidden>'+field('New company name','company_name','','text',true)+'</div>'+`<label for="f-role">Role</label><input id="f-role" name="role" list="roles" maxlength="60" placeholder="Owner, Manager, Front desk" value="${escape(c.role||'')}"><datalist id="roles">${['Owner','Manager','Front desk'].map(x=>`<option value="${x}">`).join('')}</datalist></div>`;
      const lines=action==='contact-edit'?chans(c):[{kind:'phone'},{kind:'email'}];
      fields+=field('Name','name',c.name,'text',true)+`<fieldset class="source channels"><legend>Phones and emails</legend><p class="muted">The first phone and the first email are the main ones: Call buttons and email drafts use them.</p><div id="chan-rows">${lines.map(chanRow).join('')}</div><datalist id="chan-labels">${['Main line','Direct','Cell','Office','Other'].map(x=>`<option value="${x}">`).join('')}</datalist><div class="actions"><button type="button" data-chan="phone">Add a phone</button><button type="button" data-chan="email">Add an email</button></div></fieldset>`+field('Address','address',c.address||'')+(states().length?select('State','territory',[{id:'',label:'No state'},...states().map(x=>({id:x.id,label:x.label+(x.region?' · '+x.region:'')}))],c.territory??at?.territory??'')+'<p class="muted">The state decides which territory sees this contact.</p>':'')+field('Website','website',c.website||'')+select('Relationship','lifecycle',choices(['lead','customer','archived']),c.lifecycle||'lead');
      if(action==='contact-new'&&!at&&openStages().length) fields+=stageSelect('Pipeline stage','stage',[{id:'',label:'Not in the pipeline yet'},...openStages()],firstOpen())+'<p class="muted">A stage puts them on the Pipeline board now, as a lead with no amount yet. Add the price later from Edit deal.</p>';
      fields+=`<fieldset class="source"><legend>Where they came from</legend><label for="f-source_channel">Channel</label><input id="f-source_channel" name="source_channel" list="source-channels" maxlength="120" value="${escape(c.source_channel||'')}" placeholder="Referral, Google Ads, Research"><datalist id="source-channels">${channels().map(x=>`<option value="${escape(x)}">`).join('')}</datalist>`+field('Campaign','source_campaign',c.source_campaign||'')+field('Ad or detail','source_ad',c.source_ad||'')+'</fieldset>';
    } else if(action==='activity-edit') {
      const a=state.activities.find(x=>x.id===id);editing.revision=a.revision;label='Edit '+a.kind;
      fields=select('Activity type','kind',choices(['note','call','meeting','email']),a.kind)+`<label for="f-body">What happened and what comes next?</label><textarea id="f-body" name="body" rows="6" maxlength="10000" required>${escape(a.body)}</textarea><p class="muted">Written ${escape(when(a.created_at))}. The note will show "Edited", and the old text is kept in the record's history.</p><div class="actions"><button type="button" data-action="activity-delete" data-id="${escape(id)}">Remove this note</button></div>`;
    } else if(action==='activity-new') {
      label='Record a conversation';fields=select('Activity type','kind',choices(['note','call','meeting','email']),'note')+'<label for="f-body">What happened and what comes next?</label><textarea id="f-body" name="body" rows="6" maxlength="10000" required></textarea>';
    } else if(action==='deal-new'||action==='deal-edit') {
      const d=action==='deal-edit'?state.deals.find(x=>x.id===id):{};editing.revision=d.revision;
      label=action==='deal-new'?'Create a deal':'Edit deal';fields=field('Deal title','title',d.title,'text',true)+stageSelect('Stage','stage',state.stages,d.stage||firstOpen()||state.stages[0]?.id)+((state.offers||[]).length?select('Offer','offer',[{id:'',label:'Not chosen yet'},...state.offers],d.offer||''):'')+field(`Setup, one time (${d.currency||state.currency})`,'amount',((d.amount_cents||0)/100).toFixed(2),'text',true)+field(`Monthly (${d.currency||state.currency})`,'monthly',((d.monthly_cents||0)/100).toFixed(2),'text')+'<p class="muted">Our fees only. Never ad spend or other costs the client pays through us.</p>';
      fields+=field('Loss reason (required for a lost deal)','lost_reason',d.lost_reason);
    } else if(action==='pipeline-add') {
      label='Add to the pipeline';fields=`<p>${escape(contact(id).name)} goes on the Pipeline board as a lead with no amount yet.</p>`+stageSelect('Stage','stage',openStages(),firstOpen());
    } else if(action==='deal-stage') {
      const d=state.deals.find(x=>x.id===id);editing.revision=d.revision;
      label='Move stage: '+d.title;fields=stageSelect('Stage','stage',state.stages,d.stage)+field('Loss reason (required for a lost deal)','lost_reason',d.lost_reason);
    } else if(action==='assign') {
      const c=contact(id);editing.revision=c.revision;label='Assign relationship owner';fields='<p>The company, linked people, deals, notes and tasks move together.</p>'+select('Owner','owner',state.members.map(m=>({id:m.id,label:m.name})),c.owner);
    } else {
      const t=action==='task-edit'?state.tasks.find(x=>x.id===id):{};editing.revision=t.revision;
      label=action==='task-new'?'Add a to-do':'Edit to-do';fields=field('Task','title',t.title,'text',true)+field(`Due date and time (${Intl.DateTimeFormat().resolvedOptions().timeZone})`,'due_at',t.due_at?dateInput(t.due_at):'','datetime-local',true);
      fields+=select('Related deal','deal_id',[{id:'',label:'General contact follow-up'},...state.deals.filter(d=>d.contact_id===(t.contact_id||id)).map(d=>({id:d.id,label:d.title}))],t.deal_id||'');
      const cats=catsFor(contact(t.contact_id||id));
      if(cats.length)fields+=select('Category','category',[{id:'',label:'Other follow-ups'},...cats],t.category||'');
      fields+=select('Action','action',[{id:'none',label:'None, a plain checkbox'},{id:'ask_client',label:'Ask the client (email draft)'},{id:'work_session',label:'Open a work session (brief)'}],t.action||'none');
      fields+='<div id="action-detail"><label for="f-action_detail" id="action-detail-label"></label><textarea id="f-action_detail" name="action_detail" rows="3" maxlength="2000" required>'+escape(t.action_detail||'')+'</textarea></div>';
      if(action==='task-edit')fields+=select('Status','status',choices(['open','completed','cancelled']),t.status);
    }
    $('editor-title').textContent=label;$('fields').innerHTML=fields;$('form-error').textContent='';$('save-editor').disabled=false;syncCompany();$('editor').showModal();
  }
  function syncAction() {
    const kind=$('f-action');if(!kind)return;const detail=$('f-action_detail'),none=kind.value==='none';
    $('action-detail').hidden=none;detail.disabled=none||!!pending;
    $('action-detail-label').textContent=kind.value==='ask_client'?'The exact question for the client':'Brief path, relative to the repository (for example clients/acme/handoffs/H01-start.md)';
  }
  function syncCompany() {
    syncAction();
    if($('f-outcome')&&$('outcome-help')){$('outcome-help').textContent=outcomeHelp[$('f-outcome').value]||'';const drop=['refused','lost'].includes($('f-outcome').value);$('next-step').hidden=drop;$('next-pick').hidden=$('f-next').value!=='pick';}
    if($('req-discover')){const q=$('f-kind').value==='question';$('req-discover').hidden=q;$('req-question').hidden=!q;$('f-industry').disabled=q;$('f-ring').disabled=q;$('f-body').disabled=!q;return;}
    const group=$('company-fields');if(!group)return;
    const isPerson=!$('f-kind')||$('f-kind').value==='person';
    group.hidden=!isPerson;
    const isNew=isPerson&&$('f-company_id').value==='__new__';
    if($('f-role'))$('f-role').disabled=!isPerson;
    $('new-company').hidden=!isNew;
    $('f-company_id').disabled=!isPerson;
    $('f-company_name').disabled=!isNew;
  }
  $('edit-form').addEventListener('change',syncCompany);
  function closeEditor(){if(saving)return;if(pending){$('form-error').textContent='The save outcome is uncertain. Keep the original values and press Save again to safely confirm it.';return;}if(dirty&&!confirm('Discard the unsaved form?'))return;$('editor').close();dirty=false;editing=null;pending=null;}
  $('close-editor').onclick=closeEditor;$('cancel-editor').onclick=closeEditor;
  $('editor').addEventListener('cancel',e=>{e.preventDefault();closeEditor()});
  $('edit-form').addEventListener('input',()=>dirty=true);
  window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue=''}});
  $('edit-form').addEventListener('submit',async e=>{
    e.preventDefault();if(saving)return;const form=Object.fromEntries(new FormData(e.target));const {action,id,revision}=editing;let name,data;
    try {
      if(!pending) {
      if(form.company_id==='__new__')delete form.company_id;
      if($('chan-rows'))form.channels=[...$('chan-rows').children].map(r=>({kind:r.querySelector('[data-ch=kind]').value,label:r.querySelector('[data-ch=label]').value.trim(),value:r.querySelector('[data-ch=value]').value.trim()})).filter(x=>x.value);
      if(action==='research-outcome'){name='research.outcome';const {next,next_at_local,next_title,...rest}=form;data={...rest,place_id:id,revision};
        if(next&&!['refused','lost'].includes(form.outcome)){let at;if(next==='pick'){at=new Date(next_at_local);if(!next_at_local||!Number.isFinite(at.getTime()))throw Error('Choose when to follow up.');}else{at=new Date();at.setDate(at.getDate()+Number(next));at.setHours(9,0,0,0);}data.next_at=at.toISOString();data.next_title=(next_title||'').trim();}}
      else if(action==='research-reassign'){name='research.reassign';data={...form,place_id:id,revision};}
      else if(action==='research-note-edit'){name='research.outcome.edit';data={notes:form.notes||'',id,revision};}
      else if(action==='stages-edit'){name='pipeline.stages';const moves={};$('stage-moves').querySelectorAll('select').forEach(x=>moves[x.dataset.from]=x.value);data={stages:[...$('stage-rows').children].filter(li=>!li.hidden).map(li=>({...(li.dataset.id?{id:li.dataset.id}:{}),label:li.querySelector('input').value.trim(),...(li.querySelector('[data-line]')?{pipeline:li.querySelector('[data-line]').value}:{})})),moves,revision};}
      else if(action==='request-new'){name='research.request';data=form.kind==='question'?{kind:'question',body:form.body}:{kind:'discover',industry:form.industry,ring:Number(form.ring)};}
      else if(action==='request-answer'){name='research.request.decide';data={id,revision,decision:'answered',note:form.note};}
      else if(action==='node-review'){name='node.review';data={...form,contact_id:editing.contact_id,node_id:id,node_revision:editing.node_revision,revision};}
      else if(action==='node-edit'){name='node.set';data={...form,contact_id:editing.contact_id,node_id:id,revision};}
      else if(action==='contact-new'){name='contact.create';data=form;if(data.kind==='company')delete data.company_id;if(!data.stage)delete data.stage;if(editing.confirm)data.confirm_duplicate=true;}
      else if(action==='pipeline-add'){name='deal.create';data={contact_id:id,title:contact(id).name,stage:form.stage,amount_cents:0,currency:state.currency};}
      else if(action==='deal-stage'){name='deal.update';data={...form,id,revision};}
      else if(action==='contact-edit'){name='contact.update';data={...form,id,revision};}
      else if(action==='activity-new'){name='activity.add';data={...form,contact_id:id};}
      else if(action==='activity-edit'){name='activity.update';data={...form,id,revision};}
      else if(action==='assign'){name='contact.assign';data={...form,id,revision};}
      else if(action.startsWith('deal-')) {
        form.amount=String(form.amount).replace(/[$,\s]/g,'');
        if(!/^\d+(\.\d{1,2})?$/.test(form.amount))throw Error('Enter an amount in dollars, like 1800 or 1,800.00.');
        const toCents=v=>{const x=String(v||'0').replace(/[$,\s]/g,'')||'0';if(!/^\d+(\.\d{1,2})?$/.test(x))throw Error('Enter amounts in dollars, like 1800 or 1,800.00.');const q=x.split('.');return Number(q[0])*100+Number((q[1]||'').padEnd(2,'0'));};
        const parts=form.amount.split('.'),cents=Number(parts[0])*100+Number((parts[1]||'').padEnd(2,'0'));delete form.amount;form.monthly_cents=toCents(form.monthly);delete form.monthly;
        data={...form,amount_cents:cents};name=action==='deal-new'?'deal.create':'deal.update';
        if(action==='deal-new')data={...data,contact_id:id,currency:state.currency};else data={...data,id,revision};
      } else {
        const due=new Date(form.due_at);if(!Number.isFinite(due.getTime()))throw Error('Choose a valid due date.');
        // Reject browser normalization of nonexistent local times during DST jumps.
        if(dateInput(due.toISOString())!==form.due_at)throw Error('This local time does not exist. Choose another time.');
        data={...form,due_at:due.toISOString()};name=action==='task-new'?'task.create':'task.update';
        data=action==='task-new'?{...data,contact_id:id}:{...data,id,revision};
      }
      const signature=JSON.stringify([name,data]);
      if(pending&&pending.signature!==signature)throw Error('The previous save has an uncertain outcome. Retry the original values or refresh the workspace before making another change.');
      if(!pending)pending={signature,body:{action:name,data,request_id:crypto.randomUUID()}};
      } else name=pending.body.action;
      saving=true;$('save-editor').disabled=true;$('close-editor').disabled=true;$('cancel-editor').disabled=true;$('fields').querySelectorAll('input,select,textarea').forEach(el=>el.disabled=true);$('form-error').textContent='';
      const response=await fetch('api/crm/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pending.body)});
      const result=await response.json();
      if(!response.ok&&response.status===409&&name==='contact.create'&&String(result.error).startsWith('Possible duplicate')){pending=null;
        if(!confirm(result.error))throw Error('Not saved. Open the existing record instead.');
        editing.confirm=true;setTimeout(()=>$('edit-form').requestSubmit(),0);return;}
      if(!response.ok){if(response.status<500)pending=null;throw Error(result.error||'Save failed. Retry.');}
      dirty=false;pending=null;$('editor').close();if(name==='contact.create')selected=editing?.stay||result.id;
      try{if(name.startsWith('research.'))await loadResearch();await load();writeRoute(true,true);$('notice').textContent='Saved.';}catch(err){$('notice').textContent='Saved, but refresh failed. Use Refresh to reload the workspace.';}
    } catch(err){$('form-error').textContent=err.message+(pending?' Save could not be confirmed. Press Retry save to safely check the original request.':'');}finally{saving=false;$('save-editor').disabled=false;$('close-editor').disabled=false;$('cancel-editor').disabled=false;$('fields').querySelectorAll('input,select,textarea').forEach(el=>el.disabled=!!pending);if(!pending)syncCompany();$('save-editor').textContent=pending?'Retry save':'Save';}
  });
  document.addEventListener('click',async e=>{
    const nav=e.target.closest('[data-nav]');if(nav){await navigate(nav.dataset.nav);return;}
    const line=e.target.closest('[data-pipeline]');if(line){pipelineId=line.dataset.pipeline;writeRoute(true);render();return;}
    const b=e.target.closest('[data-action]');if(!b)return;const {action,id}=b.dataset;
    if(action==='history-more'||action==='history-recent'){historyShown.set(id,action==='history-more'?(historyShown.get(id)||20)+20:20);CRMSync.preserve($('content'),render);}
    else if(action==='task-filters-clear'){taskSearch='';taskAssignee='';taskDue='all';taskQueue='all';render();}
    else if(action==='open'){await navigate(section,id);}
    else if(action==='business-new'){try{const r=await fetch('api/crm/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'contact.create',data:{name:state.name,kind:'business',lifecycle:'customer'},request_id:crypto.randomUUID()})});const res=await r.json();if(!r.ok)throw Error(res.error||'Save failed.');await load();$('notice').textContent='Business record ready. Add its next actions.';}catch(err){$('notice').textContent=err.message;}}else if(action==='back'){await navigate(section);}
    else if(action==='node-view')showNode(id);
    else if(action==='research-open'){try{if(!research)await loadResearch();}catch(err){$('notice').textContent=err.message;return;}showCompany(id);}
    else if(action==='lead-hold'){const [lid,step]=id.split('|');
      if(step==='release'&&!confirm('Send this lead to the client now? The call-back task opens and the alert email goes out.'))return;
      b.disabled=true;try{const r=await fetch('api/crm/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'lead.'+step,data:{id:lid,step},request_id:crypto.randomUUID()})});const res=await r.json();if(!r.ok)throw Error(res.error||'Save failed.');await load();$('notice').textContent=step==='release'?'Released. The client gets the alert and the call-back task.':'Set aside. The person is archived and the deal moved to Lost.';}catch(err){$('notice').textContent=err.message;b.disabled=false;}}
    else if(action==='handoff'){const [did,step]=id.split('|'),d=state.deals.find(x=>x.id===did);
      if(step==='signed'&&!confirm('Mark this deal as signed and tell Sam to start onboarding?'))return;
      b.disabled=true;try{const r=await fetch('api/crm/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'deal.handoff',data:{id:did,revision:d.revision,step},request_id:crypto.randomUUID()})});const res=await r.json();if(!r.ok)throw Error(res.error||'Save failed.');await load();if(step==='done')undoNotice('Done. It left Waiting for you.','handoff',did+'|reopen');else $('notice').textContent={quote:'Sent to Sam: it shows under Waiting for you.',signed:'Marked signed. Sam sees it under Waiting for you. Move the deal to Won when the first payment clears.',reopen:'Undone. It is back under Waiting for you.'}[step];}catch(err){$('notice').textContent=err.message;b.disabled=false;}}
    else if(action==='content-filter'){contentFilter=id;render();}
    else if(action==='calls-filter'){callFilter=id;render();}
    else if(action==='call-open'){try{await showCall(id);}catch(err){$('viewer-body').textContent=err.message;}}
    else if(action==='call-contact'){$('viewer').close();await navigate(section,id);}
    else if(action==='content-copy'){const box=$('caption-'+id);try{await navigator.clipboard.writeText(box.value);$('notice').textContent='Caption copied.';}catch(err){box.select();$('notice').textContent='Copy failed. The caption is selected: press Ctrl+C.';}}
    else if(action==='research-mode'){researchMode=id;remember('crm.researchMode',id);render();}
    else if(action==='request-new'){try{if(!research)await loadResearch();}catch(err){$('notice').textContent=err.message;return;}editor('request-new','',{industry:b.dataset.industry||'',ring:b.dataset.ring||''});}
    else if(action==='request-approve'||action==='request-decline'){const q=research.requests.find(x=>x.id===id);b.disabled=true;try{await researchCommand('research.request.decide',{id,revision:q.revision,decision:action==='request-approve'?'approved':'declined'});$('notice').textContent=action==='request-approve'?'Approved. The worker runs it next.':'Declined.';}catch(err){$('notice').textContent=err.message;b.disabled=false;}}
    else if(action==='route-add'||action==='route-remove'||action==='route-clear'||action==='route-mine'){
      if(action==='route-add'){if(route.length>=10){$('notice').textContent='A route holds 10 stops. Remove one first.';return;}if(!route.includes(id))route.push(id);}
      if(action==='route-remove')route.splice(route.indexOf(id),1);
      if(action==='route-clear')route.splice(0,route.length);
      if(action==='route-mine')(research.claims||[]).filter(k=>k.mine&&k.status==='open').forEach(k=>{if(route.length<10&&!route.includes(k.place_id))route.push(k.place_id);});
      saveRoute();theMap?.closePopup();if($('map-side'))$('map-side').innerHTML=routePanel();mountMap();}
    else if(action==='task-done'){const t=state.tasks.find(x=>x.id===id);b.disabled=true;try{await send('task.update',{id:t.id,revision:t.revision,status:'completed'});await load();doneNotice(t.id);}catch(err){$('notice').textContent=err.message;b.disabled=false;}}
    else if(action==='task-undo'){const t=state.tasks.find(x=>x.id===id);clearTimeout(undoTimer);b.disabled=true;try{await send('task.update',{id:t.id,revision:t.revision,status:'open'});await load();$('notice').textContent='Undone. It is open again.';}catch(err){$('notice').textContent=err.message+' Refresh, then uncheck it on its list.';}}
    else if(action==='task-archive'){const [cid,deep]=id.split('|'),ids=checkedOf(cid,!!deep).map(t=>t.id);if(!ids.length)return;b.disabled=true;try{await send('task.archive',{ids});await load();$('notice').textContent=`Archived ${ids.length} checked to-do${ids.length===1?'':'s'}. They are under Done / archived, and can be unchecked there.`;}catch(err){$('notice').textContent=err.message;b.disabled=false;try{await load();}catch(e){}}}
    else if(action==='person-new')editor('contact-new','',{company_id:id});
    else if(action==='contact-delete'||action==='deal-delete'){
      const isContact=action==='contact-delete',rec=isContact?contact(id):state.deals.find(x=>x.id===id),name=isContact?rec.name:rec.title;
      if(!confirm(`Delete ${name}? It leaves every list${isContact?', with its deals, to-dos and notes':''}. ${can('assign')?'You can restore it from Contacts, Deleted.':'Only an administrator can restore it.'}`))return;
      b.disabled=true;try{await send(isContact?'contact.delete':'deal.delete',{id,revision:rec.revision});if(isContact){selected=null;writeRoute(true);}await load();
        if(can('assign'))undoNotice(`Deleted ${name}.`,isContact?'contact-restore':'deal-restore',id);else $('notice').textContent=`Deleted ${name}. Ask Sam if it needs to come back.`;}
      catch(err){$('notice').textContent=err.message;b.disabled=false;}}
    else if(action==='contact-restore'||action==='deal-restore'){clearTimeout(undoTimer);b.disabled=true;try{await send(action==='contact-restore'?'contact.restore':'deal.restore',{id});await load();$('notice').textContent='Restored.';}catch(err){$('notice').textContent=err.message;b.disabled=false;}}
    else if(action==='activity-delete'){const a=state.activities.find(x=>x.id===id);if(!confirm('Remove this note? It leaves the history on screen; the record keeps a copy.'))return;b.disabled=true;try{await send('activity.delete',{id,revision:a.revision});dirty=false;pending=null;$('editor').close();editing=null;await load();$('notice').textContent='Note removed.';}catch(err){$('form-error').textContent=err.message;b.disabled=false;}}
    else if(action==='record-outcome'){try{if(!research)await loadResearch();}catch(err){$('notice').textContent=err.message;return;}editor('research-outcome',id);}
    else if(action==='theme'){const next=currentTheme()==='light'?'dark':'light';document.documentElement.dataset.theme=next;remember('crm.theme',next);themeLabel();}
    else if(action==='leader-period'){leaderPeriod=id;try{await loadLeaders();render();}catch(err){$('notice').textContent=err.message;}}
    else if(action==='research-claim'||action==='research-release'){
      if(action==='research-release'&&!confirm('Release this lead back to the pool? Its history stays on it.'))return;
      b.disabled=true;const k=claimOf(id);
      const inCard=$('viewer').open,say=m=>{if(inCard&&$('viewer-note'))$('viewer-note').textContent=m;else $('notice').textContent=m;};
      try{await researchCommand(action.replace('-','.'),action==='research-claim'?{place_id:id}:{place_id:id,revision:k.revision});if(inCard)showCompany(id);if(action==='research-release')undoNotice('Released. It is back in the pool.','research-unrelease',id,inCard&&$('viewer-note')?$('viewer-note'):$('notice'));else say('Yours. It is in your pipeline as Contacted. Record an outcome after each approach.');}
      catch(err){say(err.message);b.disabled=false;}}
    else if(action.startsWith('pkg-')){b.disabled=true;try{await packageCommand(action,id);$('viewer-note').textContent={'pkg-doc':'Requested.','pkg-print':'Sent to Sam for approval.','pkg-approve':'Approved. It can be printed now.','pkg-revoke':'Kept back.'}[action];}catch(err){$('viewer-note').textContent=err.message;b.disabled=false;}}
    else if(action==='research-unrelease'){const k=(research.claims||[]).find(x=>x.place_id===id),inCard=$('viewer').open;clearTimeout(undoTimer);b.disabled=true;
      try{await researchCommand('research.release.undo',{place_id:id,revision:k.revision});if(inCard)showCompany(id);(inCard&&$('viewer-note')?$('viewer-note'):$('notice')).textContent='Undone. The lead is yours again, same clock.';}catch(err){(inCard&&$('viewer-note')?$('viewer-note'):$('notice')).textContent=err.message;}}
    else if(action==='research-note-edit'){$('viewer').close();editor(action,id);}
    else if(action==='research-contact'){$('viewer').close();await navigate(section,id);}
    else if(action==='research-outcome'||action==='research-reassign'){$('viewer').close();editor(action,id);}
    else if(action==='node-edit'||action==='node-review'){$('viewer').close();editor(action,id);}
    else if(action==='node-plan'){
      const n=state.tech_tree_catalog.nodes.find(n=>n.id===id),r=nodeRecord(selected,id);
      $('viewer').close();editor('task-new',selected);
      $('editor-title').textContent='Plan: '+n.label;
      $('f-title').value='Plan '+n.label;
      $('f-action').value='work_session';$('f-action_detail').value=r?.brief_path||'';syncAction();
    }
    else if(action==='ask'||action==='session')showAction(action,id);
    else if(action==='copy'){const box=$(b.dataset.target);try{await navigator.clipboard.writeText(box.value??box.textContent);$('viewer-note').textContent='Copied.';}catch(err){box.select?.();$('viewer-note').textContent='Copy failed. The text is selected: press Ctrl+C.';}}
    else if(action==='refresh'){try{await load();$('notice').textContent='Workspace refreshed.';}catch(err){$('notice').textContent=err.message;}}
    else editor(action,id);
  });
  const copyBtn=(label,target,primary=false)=>`<button type="button" data-action="copy" data-target="${target}" class="${primary?'primary':''}">${label}</button>`;
  // A task action prepares the work. It never sends or starts anything by itself.
  function showAction(kind,id) {
    const t=state.tasks.find(x=>x.id===id),c=contact(t.contact_id);let html;
    if(kind==='ask') {
      const people=[c,...state.contacts.filter(p=>p.company_id===c.id)].filter(p=>p.email);
      const named=people.filter(p=>p.kind==='person'),first=named.length===1?named[0].name.split(' ')[0]:'';
      const body=`Hi${first?' '+first:''},

${t.action_detail}

Thank you,
${state.person.name}`;
      $('viewer-title').textContent='Ask the client';
      html=`<p class="muted">Email is not connected. Nothing is sent. Copy this draft into your email, then send it yourself.</p><label for="v-to">To</label><input id="v-to" value="${escape(people.map(p=>p.email).join(', ')||'No email on this record')}"><label for="v-subject">Subject</label><input id="v-subject" value="${escape('Quick question: '+t.title)}"><label for="v-body">Draft</label><textarea id="v-body" rows="8">${escape(body)}</textarea><div class="actions">${copyBtn('Copy subject','v-subject')}${copyBtn('Copy draft','v-body',true)}</div>`;
    } else {
      $('viewer-title').textContent='Open a work session';
      const prompt=`Read ${t.action_detail} and do it.`;
      html=`<p class="muted">Open a fresh Claude Code session in the repository, then paste this start command. Nothing starts from here.</p><label>Brief</label><p><code>${escape(t.action_detail)}</code></p><label for="v-prompt">Start command</label><textarea id="v-prompt" rows="2" readonly>${escape(prompt)}</textarea><div class="actions">${copyBtn('Copy start command','v-prompt',true)}</div>`;
    }
    $('viewer-body').innerHTML=html+'<p id="viewer-note" role="status" aria-live="polite"></p>';$('viewer').showModal();
  }
  $('close-viewer').onclick=()=>$('viewer').close();
  document.addEventListener('toggle',e=>{const g=e.target.closest?.('[data-group]');if(g)folded.set(g.dataset.group,g.open);},true);
  document.addEventListener('change',async e=>{
    const box=e.target.closest('[data-task]');if(!box)return;
    const t=state.tasks.find(x=>x.id===box.dataset.task);
    if(box.checked&&t.requires_completion_evidence){box.checked=false;TaskEditor.open(t.id,{status:'completed',focus:'completion_evidence'});return;}
    box.disabled=true;
    const body={action:'task.update',data:{id:t.id,revision:t.revision,status:box.checked?'completed':'open'},request_id:crypto.randomUUID()};
    try{
      const response=await fetch('api/crm/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const result=await response.json();if(!response.ok)throw Error(result.error||'Save failed.');
      if(box.checked){await load();doneNotice(t.id);return;}
      $('notice').textContent='Reopened.';
    }catch(err){$('notice').textContent=err.message+' The list was reloaded.';}
    try{await load();}catch(err){$('notice').textContent=err.message;}
  });
  document.addEventListener('input',e=>{if(e.target.id==='task-search'){taskSearch=e.target.value;CRMSync.preserve($('content'),render);if(taskFiltered())$('content').querySelectorAll('details.group').forEach(el=>el.open=true);}if(e.target.id==='search'){filter=e.target.value;renderContacts();}});
  document.addEventListener('change',e=>{const id=e.target.id;if(id==='history-kind'){historyKind.set(e.target.dataset.contact,e.target.value);historyShown.set(e.target.dataset.contact,20);CRMSync.preserve($('content'),render);return;}if(['task-assignee-filter','task-due-filter','task-queue-filter'].includes(id)){if(id==='task-assignee-filter')taskAssignee=e.target.value;if(id==='task-due-filter')taskDue=e.target.value;if(id==='task-queue-filter')taskQueue=e.target.value;render();$(id)?.focus({preventScroll:true});}if(e.target.id==='map-industry'){mapIndustry=e.target.value;mountMap();}if(e.target.id==='lifecycle'){lifecycle=e.target.value;renderContacts();}if(e.target.id==='channel'){channel=e.target.value;renderContacts();}if(e.target.id==='owner'){owner=e.target.value;remember('crm.owner',owner);if(section==='contacts')renderContacts();else render();}});
  function currentTheme(){const t=document.documentElement.dataset.theme;return t==='light'||t==='dark'?t:(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');}
  function themeLabel(){const t=$('theme');if(t)t.textContent=currentTheme()==='light'?'Dark mode':'Light mode';}
  {const saved=remember('crm.theme');if(saved==='light'||saved==='dark')document.documentElement.dataset.theme=saved;themeLabel();}
  // The recap email links to one call: #call=<id> opens it.
  load(false,true).then(async()=>{writeRoute(true,true);CRMSync.start(()=>load(true));const tid=entry.get('task')||TaskEditor.recover();if(tid)await TaskEditor.open(tid);else RecordForm.recover();const m=location.hash.match(/^#call=([A-Za-z0-9_-]+)$/);if(m&&document.querySelector('[data-nav=calls]')){section='calls';writeRoute(true,true);try{await loadCalls();render();await showCall(m[1]);}catch(err){$('notice').textContent=err.message;}}}).catch(err=>{$('content').textContent=err.message;});
  // CRM changes are delivered by the shared event stream in every view.
})();
