/* One task editor for CRM and Command Center. The server decides permissions and completion rules. */
window.TaskEditor = (() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const $ = id => document.getElementById(id);
  const localDate = value => { const d = new Date(value); return new Date(d - d.getTimezoneOffset()*60000).toISOString().slice(0,16); };
  let dialog, model, baseline, pending, busy = false, latest, opener, previousURL, previousState, modalURL, modalState, modalEntry = false, generation = 0;
  const fields = ['title','due_at','status','category','deal_id','action','action_detail','blocked_reason','completion_evidence','note','assignee','department','followup_state','review_at','waiting_on'];
  const storageKey = 'crm.task-save:' + document.baseURI;
  function init() {
    if (dialog) return;
    dialog = document.createElement('dialog'); dialog.id = 'task-editor'; dialog.className = 'task-editor';
    dialog.setAttribute('aria-labelledby', 'task-editor-title');
    dialog.innerHTML = `<form id="task-editor-form"><div class="task-editor-head"><h2 id="task-editor-title">Task details</h2><button type="button" id="task-editor-close" aria-label="Close task">×</button></div><p id="task-editor-meta" class="task-editor-meta"></p><div id="task-editor-context" class="task-editor-context" hidden></div><div id="task-editor-update" class="task-editor-alert" role="status"></div><div class="task-editor-body"><fieldset id="task-editor-fields"></fieldset></div><p id="task-editor-error" class="task-editor-alert task-editor-error" role="alert"></p><div class="task-editor-actions"><a id="task-editor-other">Open in CRM</a><button type="button" id="task-editor-cancel">Cancel</button><button type="submit" id="task-editor-save">Save task</button></div></form>`;
    document.body.append(dialog);
    $('task-editor-close').onclick = close; $('task-editor-cancel').onclick = close;
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
    $('task-editor-form').addEventListener('submit', submit);
    $('task-editor-form').addEventListener('change', controls);
    window.addEventListener('beforeunload', event => { if (dialog.open && (dirty() || pending)) { event.preventDefault(); event.returnValue = ''; } });
    window.addEventListener('popstate', event => {
      const id = new URLSearchParams(location.search).get('task');
      if (dialog.open && id === model?.task.id) return;
      if (dialog.open) {
        if (busy || pending || (dirty() && !confirm('Discard your unsaved task changes?'))) {
          event.stopImmediatePropagation();
          modalEntry = true; modalState = {...modalState, taskEditor: true};
          history.pushState(modalState, '', modalURL);
          if (pending || busy) $('task-editor-error').textContent = 'Check the saved result before leaving this task.';
          return;
        }
        finish(false);
      }
      if (id) queueMicrotask(() => open(id));
    }, true);
    $('task-editor-update').addEventListener('click', event => {
      if (event.target.id === 'task-use-latest' && latest && (!dirty() || confirm('Replace your unsaved edits with the latest saved task?'))) { model = latest; latest = null; draw(); }
    });
  }
  function values() { return Object.fromEntries(new FormData($('task-editor-form'))); }
  function dirty() {
    if (!baseline || !dialog?.open || busy) return false;
    const current = values(); return fields.some(key => (current[key] || '') !== (baseline[key] || ''));
  }
  function close() {
    if (busy) return;
    if (pending) { $('task-editor-error').textContent = 'Check the saved result before closing. The original request is kept so it cannot be duplicated.'; return; }
    if (dirty() && !confirm('Discard your unsaved task changes?')) return;
    finish();
  }
  function finish(restore = true) {
    dialog.close(); generation++; model = null; latest = null;
    baseline = null;
    if (restore && previousURL) {
      if (modalEntry && location.href === modalURL) history.back();
      else history.replaceState(previousState, '', previousURL);
    }
    if (opener?.isConnected) opener.focus({preventScroll: true});
  }
  async function fetchTask(id) {
    const response = await fetch('api/crm/task?id=' + encodeURIComponent(id), {cache:'no-store', signal:AbortSignal.timeout(15000)});
    const body = await response.json(); if (!response.ok) throw Error(body.error || 'Could not load this task.'); return body;
  }
  async function open(id, options = {}) {
    init(); if (busy || (dialog.open && (pending || dirty()))) return;
    const savedTask = recover();
    if (savedTask && savedTask !== id) { id = savedTask; options = {}; }
    opener = document.activeElement; previousURL = location.href;
    previousState = {...history.state}; delete previousState.taskEditor;
    const before = new URL(previousURL); before.searchParams.delete('task'); previousURL = before.href;
    const current = ++generation; baseline = null; model = null; latest = null;
    $('task-editor-fields').innerHTML = '<p>Loading task…</p>'; $('task-editor-save').hidden = true;
    $('task-editor-update').textContent = ''; $('task-editor-error').textContent = '';
    $('task-editor-meta').textContent = ''; $('task-editor-other').hidden = true; $('task-editor-context').hidden = true;
    if (!dialog.open) dialog.showModal();
    try {
      const next = await fetchTask(id); if (current !== generation) return;
      model = next;
      const url = new URL(location.href), hasTask = url.searchParams.has('task');
      modalEntry = !hasTask || history.state?.taskEditor === true;
      url.searchParams.set('task', id); modalURL = url.href;
      modalState = {...history.state, taskEditor: modalEntry};
      history[hasTask?'replaceState':'pushState'](modalState, '', url);
      try { const saved = JSON.parse(sessionStorage.getItem(storageKey) || 'null'); pending = saved?.data?.id === id ? saved : null; } catch { pending = null; }
      draw();
      if (options.status && !pending) $('task-status').value = options.status;
      controls();
      if (options.focus) $('task-' + options.focus)?.focus();
    } catch (error) { $('task-editor-error').textContent = error.message; }
  }
  function draw() {
    const t = model.task;
    const input = (label, name, type='text', max=200) => `<div><label for="task-${name}">${label}</label><input id="task-${name}" name="${name}" type="${type}" maxlength="${max}" value="${esc(type==='datetime-local'?localDate(t[name]):t[name])}" required></div>`;
    const area = (label, name, max=4000) => `<div><label for="task-${name}">${label}</label><textarea id="task-${name}" name="${name}" maxlength="${max}">${esc(t[name] || '')}</textarea></div>`;
    const select = (label, name, rows) => `<div><label for="task-${name}">${label}</label><select id="task-${name}" name="${name}">${rows.map(([id,label])=>`<option value="${esc(id)}" ${String(t[name]||'')===id?'selected':''}>${esc(label)}</option>`).join('')}</select></div>`;
    const categories = [['','Other follow-ups'], ...model.categories.map(c=>[c.id,c.label])];
    if (t.category && !categories.some(([id])=>id===t.category)) categories.push([t.category, t.category_label || 'Existing category: '+t.category]);
    $('task-editor-title').textContent = 'Task details';
    $('task-editor-meta').textContent = (t.plan?.verification ? 'Build plan' : 'CRM task') + (model.can_edit ? '' : ' · View only');
    context();
    $('task-editor-fields').innerHTML = input('Task title','title') + `<div class="task-editor-grid">${input('Due date','due_at','datetime-local')}${select('Status','status',[['open','Open'],['completed','Completed'],['cancelled','Cancelled']])}</div>` +
      select('Follow-up status','followup_state',[['','From category'],['next','Ready to work'],['client','Waiting on someone'],['blocked','Blocked'],['decisions','Decision needed'],['parked','Parked for later']])+
      `<div id="task-followup-fields"><label for="task-waiting_on">Waiting on</label><input id="task-waiting_on" name="waiting_on" maxlength="200" value="${esc(t.waiting_on||'')}" placeholder="Person, team, or information needed"><label for="task-review_at">Review on</label><input id="task-review_at" name="review_at" type="datetime-local" value="${t.review_at?esc(localDate(t.review_at)):''}"><p class="task-editor-meta">Back in Needs review at this time. The deadline stays.</p></div>`+
      (t.original_due_at&&t.original_due_at!==t.due_at?`<p class="task-editor-meta">Original deadline: ${esc(new Date(t.original_due_at).toLocaleString())}</p>`:'')+
      select('Assigned to','assignee',[['','Company owner (automatic)'],...(model.assignees||[]).map(m=>[m.id,m.name]),...(t.assignee&&!(model.assignees||[]).some(m=>m.id===t.assignee)?[[t.assignee,t.assignee+' · access needs review']]:[])]) +
      '<p class="task-editor-meta">Only people who can already work on this record. Access does not change.</p>' +
      select('Department','department',[['','From category or build plan'],...(model.departments||[]).map(d=>[d.id,d.label])]) +
      area('Blocker (leave blank when unblocked)','blocked_reason',2000) + area('Completion evidence','completion_evidence') +
      (t.plan?.verification?`<p><strong>Acceptance check</strong><br>${esc(t.plan.verification)}</p><p>Completing this task does not change the system’s installation status.</p>`:'') +
      area('Add a note to the history','note') + `<details><summary>Category, related deal and task instructions</summary>${select('Category','category',categories)}${select('Related deal','deal_id',[['','No deal'],...model.deals.map(d=>[d.id,d.title])])}${select('Action','action',[['none','No prepared action'],['ask_client','Question for the client'],['work_session','Work brief']])}${area('Question or work brief path','action_detail',2000)}</details>` +
      (model.history.length?`<details class="task-editor-history"><summary>Recent task notes</summary>${model.history.map(n=>`<p><small>${esc(new Date(n.created_at).toLocaleString())}</small><br>${esc(n.body.split('\n').slice(1).join('\n'))}</p>`).join('')}</details>`:'');
    $('task-editor-fields').disabled = !model.can_edit || !!pending;
    $('task-editor-save').hidden = !model.can_edit;
    $('task-editor-save').disabled = false;
    $('task-editor-save').textContent = pending ? 'Check saved result' : 'Save task';
    $('task-editor-error').textContent = pending ? 'A previous save was not confirmed. Check its result using the same request.' : '';
    $('task-editor-update').textContent = '';
    const strategy = location.pathname.endsWith('/strategy');
    const other = new URL(strategy?'./':'strategy', document.baseURI);
    other.searchParams.set('task', t.id);
    other.searchParams.set('view', strategy?'tasks':'overview');
    const returnTo = new URLSearchParams(location.search).get('return_to');
    if (returnTo) {
      try { const back = new URL(returnTo, location.origin); if(back.origin===location.origin && back.pathname.startsWith(new URL(document.baseURI).pathname)){other.href=back.href;other.searchParams.set('task',t.id);} } catch {}
    } else { const back = new URL(location.href); back.searchParams.delete('task'); other.searchParams.set('return_to',back.pathname+back.search); }
    $('task-editor-other').href = other.href; $('task-editor-other').textContent = strategy?'Open this task in CRM ↗':'Open this task in Command Center ↗';
    $('task-editor-other').hidden = !strategy && !document.querySelector('a[href^="strategy"]');
    $('task-editor-other').onclick = event => { if (pending || (dirty() && !confirm('Leave without saving your task changes?'))) event.preventDefault(); };
    controls(); baseline = values();
  }
  function context() {
    const c = model.context || {}, record = model.contact, strategy = location.pathname.endsWith('/strategy');
    const company = c.kind === 'company' ? record : c.company;
    const crm = new URL('./', document.baseURI); crm.searchParams.set('view', 'contacts'); crm.searchParams.set('contact', record.id);
    const links = [];
    if (company && strategy) { const url = new URL('strategy', document.baseURI); url.searchParams.set('view', 'company'); url.searchParams.set('company', company.id); url.searchParams.set('tab', 'tasks'); links.push([url.href, 'Open ' + company.name + ' →']); }
    else if (company) links.push([new URL('strategy?view=company&company=' + encodeURIComponent(company.id) + '&tab=tasks', document.baseURI).href, 'Company in Command Center ↗']);
    links.push([crm.href, strategy ? 'Record in CRM ↗' : 'Open ' + record.name + ' →']);
    const when = c.last_activity ? new Date(c.last_activity.created_at).toLocaleDateString(undefined, {month:'short', day:'numeric'}) : null;
    $('task-editor-context').innerHTML = `<strong>${esc(record.name)}</strong> · ${esc(c.lifecycle === 'customer' ? 'Customer' : c.lifecycle === 'lead' ? 'Prospect' : c.lifecycle || '')} ${esc(c.kind === 'company' ? 'company' : c.kind === 'business' ? 'business record' : 'contact')}${c.company && c.kind !== 'company' ? ' at ' + esc(c.company.name) : ''}<div class="task-editor-context-row"><span>${c.other_open_tasks === 0 ? 'No other open tasks' : (c.other_open_tasks || 0) + ' other open ' + (c.other_open_tasks === 1 ? 'task' : 'tasks')}</span><span>${when ? 'Last activity ' + esc(when) : 'No history yet'}</span>${links.map(([href, label]) => `<a href="${esc(href)}" class="task-editor-leave">${esc(label)}</a>`).join('')}</div>`;
    $('task-editor-context').hidden = false;
    $('task-editor-context').querySelectorAll('a').forEach(a => { a.onclick = event => { if (pending || (dirty() && !confirm('Leave without saving your task changes?'))) event.preventDefault(); }; });
  }
  function controls() {
    if (!model || !$('task-status')) return;
    $('task-completion_evidence').required = model.task.requires_completion_evidence && $('task-status').value==='completed' && model.task.status!=='completed';
    $('task-completion_evidence').parentElement.hidden = !model.task.requires_completion_evidence && !$('task-completion_evidence').value && $('task-status').value !== 'completed';
    const followup=$('task-followup_state').value,held=['client','parked','blocked','decisions'].includes(followup);
    $('task-followup-fields').hidden=!held||$('task-status').value!=='open';
    $('task-waiting_on').hidden=followup!=='client';
    document.querySelector('label[for=task-waiting_on]').hidden=followup!=='client';
    const changed=$('task-waiting_on').value!==(model.task.waiting_on||'')||followup!==(model.task.followup_state||'')||$('task-review_at').value!==(model.task.review_at?localDate(model.task.review_at):'');
    $('task-waiting_on').required=changed&&followup==='client'&&$('task-status').value==='open';
    $('task-review_at').required=changed&&['client','parked'].includes(followup)&&$('task-status').value==='open';
    $('task-blocked_reason').required=followup==='blocked'&&$('task-status').value==='open';
    $('task-action_detail').required = $('task-action').value !== 'none';
    $('task-action_detail').parentElement.hidden = $('task-action').value === 'none';
  }
  function conflict(next) {
    latest = next;
    const t = next.task;
    $('task-editor-update').innerHTML = `<strong>This task changed elsewhere. Your edits are preserved.</strong><p class="task-editor-diff">Latest title: ${esc(t.title)}\nStatus: ${esc(t.status)}\nDue: ${esc(new Date(t.due_at).toLocaleString())}\nBlocker: ${esc(t.blocked_reason || 'None')}\nFollow-up: ${esc(t.followup_state||'From category')}\nReview: ${esc(t.review_at?new Date(t.review_at).toLocaleString():'Not scheduled')}\nWaiting on: ${esc(t.waiting_on||'None')}</p><button id="task-use-latest" type="button">Load latest version</button>`;
  }
  async function observe() {
    if (!dialog?.open || !model || busy || pending) return;
    const gen = generation;
    try {
      const next = await fetchTask(model.task.id); if (gen !== generation || busy || pending) return;
      $('task-editor-save').disabled = !next.can_edit;
      if (next.task.revision !== model.task.revision) {
        if (dirty()) conflict(next); else { model = next; draw(); }
      }
    } catch (error) { if (gen === generation) { $('task-editor-update').textContent = error.message; $('task-editor-save').disabled = true; } }
  }
  async function submit(event) {
    event.preventDefault(); if (busy || !model?.can_edit) return;
    if (!pending) {
      const current = values(), data = {id:model.task.id, revision:model.task.revision};
      for (const key of fields) if ((current[key]||'') !== (baseline[key]||'')) data[key] = current[key] || '';
      if ('due_at' in data) {
        const date = new Date(data.due_at);
        if (!Number.isFinite(date.getTime()) || localDate(date.toISOString()) !== data.due_at) { $('task-editor-error').textContent='Choose a valid local date and time.'; return; }
        data.due_at = date.toISOString();
      }
      if ('review_at' in data && data.review_at) {
        const date=new Date(data.review_at);
        if(!Number.isFinite(date.getTime())||localDate(date.toISOString())!==data.review_at){$('task-editor-error').textContent='Choose a valid local review time.';return;}
        data.review_at=date.toISOString();
      }
      if (data.status==='completed' && model.task.requires_completion_evidence) data.completion_evidence = current.completion_evidence;
      if (Object.keys(data).length===2) { close(); return; }
      pending = {action:'task.update', data, request_id:crypto.randomUUID()};
      try { sessionStorage.setItem(storageKey,JSON.stringify(pending)); } catch { pending=null; $('task-editor-error').textContent='Enable tab storage so an interrupted save can be recovered.'; return; }
    }
    busy = true; $('task-editor-fields').disabled = true; $('task-editor-save').disabled = true; $('task-editor-save').textContent = 'Saving…';
    $('task-editor-error').textContent = '';
    try {
      const response = await fetch('api/crm/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pending),signal:AbortSignal.timeout(15000)});
      const reply = await response.json();
      if (!response.ok) {
        if (response.status<500) { pending=null; sessionStorage.removeItem(storageKey); }
        if (response.status===409) conflict(await fetchTask(model.task.id));
        throw Error(reply.error || 'Could not save the task.');
      }
      if (!reply.ok || !reply.id) throw Error('The save response was incomplete.');
      pending = null; sessionStorage.removeItem(storageKey); finish();
      const notice = document.getElementById('notice'); if (notice) notice.textContent = 'Task saved to the CRM.';
      window.CRMSync?.saved();
    } catch(error) { $('task-editor-error').textContent = error.message + (pending?' Your original request is kept. Check the saved result to safely retry.':''); }
    finally { busy=false; $('task-editor-fields').disabled=!!pending; $('task-editor-save').disabled=false; $('task-editor-save').textContent=pending?'Check saved result':'Save task'; }
  }
  function recover() { try { const saved=JSON.parse(sessionStorage.getItem(storageKey)||'null'); return saved?.data?.id; } catch { return null; } }
  return {open, observe, recover};
})();
