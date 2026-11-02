/* Delivery and daily focus are views of the same CRM records, never a second queue. */
window.StrategyDelivery = (() => {
  const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const date = value => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleDateString(undefined,{month:'short',day:'numeric'}) : 'No date';
  const person = (data,id) => data.members.find(p=>p.id===id)?.name || (id&&data.person?.id===id?data.person.name:null) || id || 'Not assigned';
  const title = task => task.title.replace(/^Pending review: /,'');
  const tabs = [['in_progress','In progress'],['waiting','Needs input'],['recommended','Next builds'],['built','Built & evidence']];
  const needsInput = row => row.waiting_ids.length || (row.state!=='built' && row.missing.length);
  const match = (row,tab) => tab==='waiting' ? needsInput(row) : row.state===tab;

  function card(data,row){
    const linked = data.tasks.filter(t=>row.task_ids.includes(t.id));
    const next = linked.find(t=>t.id===row.next_task_id);
    const held = linked.filter(t=>row.waiting_ids.includes(t.id));
    const off = /publishing off/i.test(row.evidence||'');
    const built = row.state==='built';
    return `<article class="st-delivery-card"><div class="st-delivery-card-head"><span class="st-delivery-icon">${StrategyArt.icon(row.node_id)}</span><div><small>${esc(row.company_name)}</small><h3>${esc(row.label)}</h3></div>${WorkspaceDesign.installation(row)}</div>
      <p class="st-delivery-owner">Owner: ${esc(person(data,row.owner))}${row.due_at?' · Next review '+esc(date(row.due_at)):''}</p>
      ${!built&&row.missing.length?`<p class="st-delivery-alert">Foundations needed: ${row.missing.map(group=>group.map(esc).join(' or ')).join('; ')}</p>`:''}
      ${held.length?`<details class="st-delivery-input"><summary>${held.length} linked ${held.length===1?'task needs':'tasks need'} input or review</summary>${held.map(t=>`<div><strong>${esc(title(t))}</strong><small>${esc(person(data,t.owner))} · Due ${esc(date(t.due_at))}</small>${t.context.blocked_reason?`<p>${esc(t.context.blocked_reason)}</p>`:''}<button data-action="task" data-id="${esc(t.id)}">View task</button></div>`).join('')}</details>`:''}
      ${!built?`<div class="st-delivery-next"><small>Next linked action</small>${next?`<button data-action="task" data-id="${esc(next.id)}">${esc(title(next))}</button>`:`<p>${held.length?'Review the waiting items above.':'No actionable task linked yet. Open the build to review its plan.'}</p>`}</div>`:''}
      ${built?`<p class="st-delivery-proof">${row.evidence_recorded?'Evidence and location recorded.':'Evidence or installation location still needed.'} ${off?'Automatic publishing is not active.':''}</p>`:''}
      <div class="st-actions"><button type="button" data-company-action="system" data-company-id="${esc(row.company_id)}" data-record-id="${esc(row.node_id)}">Open build & tasks</button><button data-action="company" data-id="${esc(row.company_id)}">Open company</button></div>
      <details class="st-activity-evidence"><summary>Build evidence</summary><p>${esc(row.evidence||'No evidence recorded.')}</p><small>${esc(row.location||'No location recorded.')}</small><small>Record updated ${esc(date(row.updated_at))}</small></details></article>`;
  }

  function render(data,state={}){
    const board=data.delivery_board;
    if(!board?.enabled)return '';
    const company=state.company||'all',active=tabs.some(([id])=>id===state.tab)?state.tab:'in_progress';
    const builds=board.builds.filter(b=>company==='all'||b.company_id===company);
    const rows=builds.filter(b=>match(b,active)).sort((a,b)=>(active==='built'?0:(a.due_at?Date.parse(a.due_at):Infinity)-(b.due_at?Date.parse(b.due_at):Infinity))||a.label.localeCompare(b.label));
    const explanations={in_progress:'Recorded builds underway. Open a build for its tasks and installation evidence.',waiting:'Linked waiting tasks and missing foundations. A build can also appear in another view.',recommended:'Proposed builds, not commitments. Review prerequisites, scope and permissions before starting.',built:'Recorded build evidence. This does not establish current uptime or prove every feature is activated.'};
    return `<section class="st-delivery-board" aria-label="Company build queues"><div class="st-delivery-toolbar"><div><h2>Company builds</h2><p>What exists, what is being built, and what needs a person.</p></div><label>Company<select id="delivery-company" aria-label="Delivery company"><option value="all">All companies</option>${board.companies.map(c=>`<option value="${esc(c.id)}" ${company===c.id?'selected':''}>${esc(c.name)}</option>`).join('')}</select></label></div>
      <div class="st-delivery-tabs" role="group" aria-label="Build status">${tabs.map(([id,label])=>`<button data-action="delivery-tab" data-id="${id}" aria-pressed="${active===id}"><strong>${builds.filter(b=>match(b,id)).length}</strong><span>${label}</span></button>`).join('')}</div>
      <p class="st-activity-note">${explanations[active]}</p><div class="st-delivery-grid">${rows.slice(0,4).map(r=>card(data,r)).join('')||'<p class="st-activity-empty">No recorded builds in this view.</p>'}</div>${rows.length>4?`<details class="st-delivery-more"><summary>Show ${rows.length-4} more builds</summary><div class="st-delivery-grid">${rows.slice(4).map(r=>card(data,r)).join('')}</div></details>`:''}</section>`;
  }

  function focus(data){
    const items=data.command_focus?.items||[];
    if(!items.length)return '';
    return `<section class="st-command-focus" aria-labelledby="command-focus-title"><div class="st-section-top"><div><h2 id="command-focus-title">Suggested focus</h2><small>Oldest due action in each department · waiting work stays separate</small></div></div><div class="st-command-focus-grid">${items.map(t=>`<article><div class="st-focus-top"><small>${esc(t.department_label)}</small><span class="st-pill ${t.overdue?'is-overdue':''}">${t.overdue?'Overdue':'Due '+esc(date(t.due_at))}</span></div><small>${esc(t.name)}</small><h3>${esc(t.title)}</h3><p>${esc(person(data,t.owner))} · ${esc(date(t.due_at))}</p><div class="st-actions"><button data-action="task" data-id="${esc(t.task_id)}">View task</button><button data-action="department" data-id="${esc(t.department)}">${esc(t.department_label)} queue</button></div></article>`).join('')}</div></section>`;
  }
  const dailyTabs = [['next','Next actions','No recorded hold'],['client','Client input','Follow up or gather answers'],['blocked','Blocked work','Resolve the recorded blocker'],['decisions','Decisions','A choice needs a person']];
  function daily(data,state={}){
    const model=data.daily_command;
    if(!model)return focus(data);
    const active=[...dailyTabs.map(t=>t[0]),'parked'].includes(state.queue)?state.queue:'next';
    const dateFilters=[['all','All dates'],['past','Past due'],['upcoming','Upcoming'],['undated','No date']];
    const dateFilter=dateFilters.some(([id])=>id===state.due)?state.due:'all';
    const dueMatch=t=>{const due=Date.parse(t.due_at),now=Date.parse(model.observed_at);return dateFilter==='all'||(dateFilter==='undated'?!Number.isFinite(due):Number.isFinite(due)&&(dateFilter==='past'?due<now:due>=now));};
    const filtered=Boolean(state.owner||state.search||dateFilter!=='all');
    const scoped=rows=>rows.filter(t=>(!state.owner||(state.owner==='__unassigned'? !t.owner:t.owner===state.owner))&&dueMatch(t)&&(!state.search||[t.title,t.name,t.reason,t.department_label,person(data,t.owner)].join(' ').toLowerCase().includes(state.search.toLowerCase())));
    const queues=Object.fromEntries(Object.entries(model.queues).map(([k,rows])=>[k,scoped(rows)]));
    const rows=queues[active], all=Object.values(model.queues).flat();
    const owners=new Set(all.map(t=>t.owner));
    const ownerOptions=[...(data.members||[])];
    for(const id of owners)if(id&&!ownerOptions.some(p=>p.id===id))ownerOptions.push({id,name:id});
    const due=t=>t.due_at&&Number.isFinite(Date.parse(t.due_at))?`${t.overdue?'Overdue · ':''}${date(t.due_at)}`:'No date recorded';
    const row=t=>`<article class="st-daily-row" data-daily-task="${esc(t.task_id)}"><div class="st-daily-row-top"><small>${esc(t.name)} · ${esc(t.department_label)}</small><span class="st-pill ${t.overdue?'is-overdue':''}">${esc(due(t))}</span></div><h3>${esc(t.title)}</h3>${active==='next'?'':`<p class="st-daily-reason">${esc(t.reason)}</p>`}<div class="st-daily-row-bottom"><span>Owner: ${esc(person(data,t.owner))}</span><button type="button" data-action="task" data-id="${esc(t.task_id)}">${active==='decisions'?'Review decision':t.action==='ask_client'?'Review question':t.action==='work_session'?'Review work brief':'Open task'} →</button></div></article>`;
    const queueButton=(key,label)=>`<button type="button" data-action="daily-queue" data-id="${key}">${label} →</button>`;
    const clientGroups=new Map();
    for(const t of queues.client){const id=t.company_id;const group=clientGroups.get(id)||{name:data.companies.find(c=>c.id===id)?.name||t.name,count:0,first:t};group.count++;clientGroups.set(id,group);}
    const title=active==='parked'?'Parked for later':dailyTabs.find(t=>t[0]===active)[1];
    const notes={next:'Start by reviewing the oldest reminders. Check the task’s conditions before acting.',client:'The CRM records client input as outstanding. Open the saved question before following up.',blocked:'These tasks have a recorded blocker. Resolve it before restarting the work.',decisions:'Review the choice and its context. Opening a task does not approve it.',parked:'Saved for a later review. Original dates and evidence are preserved.'};
    return `<section class="st-daily" aria-labelledby="daily-title"><div class="st-daily-heading"><div><small class="st-eyebrow">${esc(new Date(model.observed_at).toLocaleDateString(undefined,{weekday:'long',month:'long',day:'numeric'}))}</small><h1 id="daily-title">Browse all work</h1><p>Explore every task by owner, deadline or work status.</p>${data.capabilities?.includes('crm_edit')&&data.connection?.mode!=='live_readonly'?'<button type="button" class="st-primary" data-new-task="">Add task</button>':''}</div><div class="st-daily-filters"><label>Owner<select id="daily-owner"><option value="">All owners</option>${all.some(t=>!t.owner)?`<option value="__unassigned" ${state.owner==='__unassigned'?'selected':''}>Not assigned</option>`:''}${ownerOptions.filter(p=>owners.has(p.id)).map(p=>`<option value="${esc(p.id)}" ${state.owner===p.id?'selected':''}>${esc(p.name)}</option>`).join('')}</select></label><label>Due date<select id="daily-due">${dateFilters.map(([id,label])=>`<option value="${id}" ${dateFilter===id?'selected':''}>${label}</option>`).join('')}</select></label><label>Find work<input id="daily-search" type="search" value="${esc(state.search||'')}" placeholder="Task, company or blocker"></label></div></div>
      <div class="st-daily-scope"><p role="status">${filtered?`Showing ${Object.values(queues).flat().length} of ${all.length} open tasks · Includes ${queues.parked.length} parked reviews`:`${all.length} open tasks · Includes ${queues.parked.length} parked reviews`}</p>${filtered?'<button type="button" data-action="daily-reset">Clear filters</button>':''}</div>
      <div class="st-daily-tabs" role="group" aria-label="Daily work queues">${dailyTabs.map(([key,label,hint])=>`<button type="button" data-action="daily-queue" data-id="${key}" aria-pressed="${active===key}"><strong>${queues[key].length}</strong><span>${label}<small>${hint}</small></span></button>`).join('')}</div>
      <div class="st-daily-layout"><section class="st-daily-work" aria-label="${esc(title)}"><div class="st-daily-section-head"><h3>${esc(title)} <span>${rows.length}</span></h3><small>Oldest due first</small></div><p class="st-daily-note">${notes[active]}</p><div aria-live="polite">${rows.slice(0,3).map(row).join('')||`<p class="st-activity-empty">${filtered?'No matching work. Clear the filters to see the full queue.':'No open tasks in this queue.'}</p>`}${rows.length>3?`<details class="st-daily-more" data-daily-disclosure="${active}"><summary>Show ${rows.length-3} more ${esc(title.toLowerCase())}</summary>${rows.slice(3).map(row).join('')}</details>`:''}</div></section>
      <aside class="st-daily-council" aria-label="Client input and decision council"><section><div class="st-daily-section-head"><h3>Client input</h3><small>${clientGroups.size} records</small></div>${[...clientGroups.values()].slice(0,3).map(g=>`<div class="st-daily-signal"><strong>${esc(g.name)}</strong><small>${g.count} ${g.count===1?'item':'items'} waiting</small><button type="button" data-action="task" data-id="${esc(g.first.task_id)}">${esc(g.first.title)} →</button></div>`).join('')||'<p class="st-daily-note">No matching client input recorded.</p>'}${queueButton('client','Review client input')}</section><section><div class="st-daily-section-head"><h3>Decision council</h3><small>${queues.decisions.length} open</small></div>${queues.decisions.slice(0,2).map(t=>`<div class="st-daily-signal"><small>${esc(t.name)} · ${esc(person(data,t.owner))}</small><button type="button" data-action="task" data-id="${esc(t.task_id)}">${esc(t.title)} →</button></div>`).join('')||'<p class="st-daily-note">No matching decisions recorded.</p>'}${queueButton('decisions','Review decisions')}</section></aside></div>
      <div class="st-daily-footer"><p>${esc(model.basis)}</p>${queueButton('parked',`${queues.parked.length} parked for later`)}</div></section>`;
  }
  return {render,focus,daily};
})();
