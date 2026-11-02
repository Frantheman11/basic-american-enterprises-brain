/* Guided help and feedback. Drafts stay in this tab; submitted work lives in the CRM. */
window.StrategyHelp = (() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const kinds = {bug:'Bug', improvement:'Improvement', feature:'Feature request', help:'Help request'};
  const views = {overview:'Command', companies:'Companies', company:'Company details', technology:'Tech tree', departments:'Departments', decisions:'Decisions', payments:'Payments',connections:'Connections'};
  const topics = [
    {title:'What does website traffic measure?',words:'traffic Analytics sessions visitors page views website',body:'Website sessions counts visits, not individual people. Open Connections for active visitors, page views, the property and exact dates. The report covers the last 30 completed days in the Analytics property timezone and only the configured website hosts. Recent data may still be processing. A failed read shows unavailable, not zero.',to:'connections',link:'Review website traffic'},
    {title:'Which connections need attention?',words:'connection source health Stripe analytics refresh failure data',body:'Open Connections for CRM, payments, website chat, feedback and Analytics status. Each card uses the latest data read. A built module is not a live connection, and stale or missing data is not a zero.',to:'connections',link:'Open Connections'},
    {title:'How do I record cash or a check?', words:'payment money cash check receipt refund collected', when:s=>s?.payments?.enabled, body:'Open Payments or use Record payment on a company. Cash is collected on the date received. Checks stay pending until you record their cleared date. Payments opens on All time; use the period selector for this month or last month. Connected Stripe collections are added to manual receipts for the same dates, before fees. Bank payouts are not new collections. Record refunds already made, or void incorrect entries while preserving the history.', to:'payments', link:'Open Payments'},
    {title:'How do I use the Delivery board?', words:'delivery company build queue progress waiting next installed evidence', when:s=>s?.delivery_board?.enabled, body:'Choose a company in Delivery. In progress shows recorded builds; Needs input shows linked waiting tasks and missing foundations. Next builds are proposals. Built & evidence keeps the proof visible, including systems that are built but not activated. Open a build for its tasks and available CRM controls.', to:'delivery', link:'Open Delivery'},
    {title:'How are suggested actions chosen?', words:'command focus priorities next today order due date', when:s=>s?.daily_command, body:'Daily command separates Next actions, Client input, Blocked work and Decisions. Parked reviews stay below. Each task appears in one queue. Owner, due-date and search filters apply to every count and summary. Past due uses the last CRM read time; Upcoming means a later recorded deadline, and No date includes invalid or absent dates. The filtered total includes parked reviews. Clear filters restores all work. Oldest due dates appear first, with undated work last; this is a review order, not a confirmed business priority. Next actions have no recorded hold, so review their conditions before acting.', to:'overview', link:'Open Command'},
    {title:'What does a customer have installed?', words:'company city business modules systems builds second brain website CRM', body:'Open a company from the map or company list. Systems separates installed modules, work in progress, and proposed upgrades. Tasks and Notes hold the supporting context.', to:'companies', link:'Find a company'},
    {title:'What should we build next?', words:'products services own CTA business technology tech tree roadmap future dependencies', body:'Choose our business in the Tech tree selector. Built products appear beside possible next connections. Pick a product to see its purpose and evidence; suggestions are ideas, not scheduled work.', to:'technology', link:'Explore our tech tree'},
    {title:'Where is the work happening?', words:'sales marketing operations delivery department tasks queue agents running', body:'Departments groups the real task queue by team. Open a task for its details. Agent activity is only shown when run data is connected; “unknown” does not mean an agent is working.', to:'departments', link:'Open departments'},
    {title:'What needs my attention?', words:'decision blocked approval pending waiting overdue next priority', body:'Decisions shows work needing your input. Items kept pending for later stay in their own section. Review the task and its company notes before changing its status.', to:'decisions', link:'Review decisions'},
    {title:'Why is a company missing from the map?', words:'address location pin customer prospect city geography', body:'The map needs a confirmed address match. Companies without one remain in the list below the map and in Companies. Map positions are approximate; a missing marker never removes a record.', to:'companies', link:'See every company'},
    {title:'How do I update a task or a company?', words:'CRM edit add save note dates history progress complete', body:'Use Add task on Command, or Open / edit task on a task card. Open a company to edit its details or add a note. Saved changes update both the Command Center and CRM automatically; check the live-update status if a change has not appeared. Refresh records retries an interrupted connection. A completed task does not by itself mean a system is installed.', to:'crm', link:'Open CRM'}
  ];
  let api, draft = null, pending = null, receipt = null, handoff = false, busy = false, storageKey = '', opener;
  let tab = 'guide', lastContext = null;
  const state = () => api.state();
  const records = () => state()?.feedback?.records || [];
  const record = () => records().find(r => r.id === draft?.contact_id);
  const writable = () => state()?.feedback?.writable === true;
  const crmUrl = id => '?view=contacts' + (id ? '&contact=' + encodeURIComponent(id) : '');
  const localTime = d => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0,16);
  function persist() {
    if (!storageKey) return;
    try { sessionStorage.setItem(storageKey, JSON.stringify({draft, pending})); }
    catch { if ($('feedback-storage')) $('feedback-storage').textContent = 'Draft stays here while this page is open. Browser draft storage is unavailable.'; }
  }
  function startDraft() {
    const key = document.documentElement.dataset.themeKey + '.feedback.' + state()?.person?.id;
    if (key !== storageKey) {
      storageKey = key; draft = null; pending = null; receipt = null; handoff = false;
      try { const saved = JSON.parse(sessionStorage.getItem(key)); if (saved?.draft && typeof saved.draft === 'object') { draft = saved.draft; pending = saved.pending || null; } } catch {}
    }
    if (!draft) {
      const r = records().find(r => r.kind === 'business') || records().find(r => r.id === lastContext.company_id);
      const due = new Date(); due.setDate(due.getDate()+1); due.setHours(9,0,0,0);
      draft = {kind:'improvement', title:'', details:'', contact_id:r?.id || '', category:r?.default_category || '', due:localTime(due), include_context:true, context:lastContext};
    }
  }
  function showTab(next, focus = false) {
    tab = next;
    ['guide','feedback'].forEach(name => {
      const active = name === next, button = $('help-'+name+'-tab');
      button.setAttribute('aria-selected', active); button.tabIndex = active ? 0 : -1;
      $('help-'+name).hidden = !active;
    });
    if (next === 'feedback') { startDraft(); renderFeedback(); }
    if (focus) $('help-'+next+'-tab').focus();
  }
  function open() {
    opener = document.activeElement; lastContext = api.context();
    startDraft(); renderGuide(); showTab(tab);
    $('help-panel').showModal();
    $('help-'+tab+'-tab').focus();
  }
  function close() { if (busy) return; persist(); $('help-panel').close(); opener?.focus(); }
  function guideResults(query = '') {
    const words = query.toLowerCase().trim().split(/\s+/).filter(Boolean);
    const matches = topics.filter(t => (!t.when || t.when(state())) && words.every(w => (t.title+' '+t.words+' '+t.body).toLowerCase().includes(w)));
    $('help-topics').innerHTML = matches.length ? matches.map(t => `<details class="st-help-topic" ${words.length?'open':''}><summary>${esc(t.title)}</summary><p>${esc(t.body)}</p><button type="button" data-help-go="${t.to}">${esc(t.link)} <span aria-hidden="true">→</span></button></details>`).join('') : '<div class="st-help-empty"><strong>No guide matches that yet.</strong><p>Describe what you are trying to do and keep it as a help request.</p><button type="button" data-help-request>Ask for help</button></div>';
  }
  function renderGuide() {
    $('help-guide').innerHTML = '<p class="st-help-intro">Find the answer. Go straight to the right place.</p><label class="st-help-search" for="help-search"><span class="st-sr-only">Search the guide</span><input id="help-search" type="search" placeholder="Try “installed” or “next”…" autocomplete="off"></label><div id="help-topics"></div><p class="st-help-footnote">Quick guidance for this workspace. Need something else? Share a help request.</p>';
    guideResults();
  }
  function contextLabel() { const c = draft.context || {}; return (views[c.view] || 'Command Center') + (c.company_name ? ' / ' + c.company_name : ''); }
  function report() { return `${kinds[draft.kind]}: ${draft.title.trim()}\n\n${draft.details.trim()}${draft.include_context?'\n\nPage: '+contextLabel():''}`; }
  function trackingFields() {
    const r = record();
    return `<label for="feedback-record">Keep on this CRM record</label><select id="feedback-record" name="contact_id" required><option value="" ${!r?'selected':''} disabled>Choose a record</option>${records().map(r=>`<option value="${esc(r.id)}" ${r.id===draft.contact_id?'selected':''}>${esc(r.name)}${r.kind==='business'?' · our business':''}</option>`).join('')}</select><label for="feedback-category">Task category</label><select id="feedback-category" name="category"><option value="">Other follow-ups</option>${(r?.categories||[]).map(c=>`<option value="${esc(c.id)}" ${c.id===draft.category?'selected':''}>${esc(c.label)}</option>`).join('')}</select>${writable()?`<label for="feedback-due">Review on (${esc(Intl.DateTimeFormat().resolvedOptions().timeZone)})</label><input id="feedback-due" type="datetime-local" name="due" value="${esc(draft.due)}" required><small>A reminder to review this request, not a completion promise.</small>`:''}`;
  }
  function renderFeedback() {
    const target = $('help-feedback');
    if (!state()) { target.innerHTML = '<p class="st-help-empty" role="status">Connecting to your CRM records…</p><p class="st-help-footnote">You can use Find your way while this loads. If the connection fails, close help and refresh the records.</p>'; return; }
    if (receipt) {
      target.innerHTML = `<div class="st-help-receipt" role="status"><span class="st-help-check" aria-hidden="true">✓</span><h3>Saved to your CRM.</h3><p>A review task and its supporting note are on <strong>${esc(receipt.name)}</strong>.</p><p class="st-help-footnote">Task ${esc(receipt.id.slice(0,8))} · ${esc(receipt.title)}</p><a class="st-help-link" href="${crmUrl(receipt.contact_id)}" target="_blank" rel="noopener">View record in CRM ↗</a><button type="button" data-help-another>Share another</button></div>`;
      return;
    }
    if (!records().length) { target.innerHTML = '<p class="st-help-empty">No business or company records are available for feedback yet. Add a record in the CRM first.</p><a class="st-help-link" href="./">Open CRM ↗</a>'; return; }
    if (handoff) {
      target.innerHTML = `<div class="st-help-handoff"><span class="st-pill">Draft · not submitted</span><h3>Ready to take into CRM</h3><p>This preview cannot save changes. On <strong>${esc(record()?.name || 'the selected record')}</strong>, add the report as a note and create a to-do with the title below.</p><label for="feedback-copy-title">To-do title</label><input id="feedback-copy-title" readonly value="${esc(kinds[draft.kind]+': '+draft.title.trim())}"><button type="button" data-help-copy="feedback-copy-title">Copy title</button><label for="feedback-copy-report">Report for the note</label><textarea id="feedback-copy-report" rows="8" readonly>${esc(report())}</textarea><button type="button" data-help-copy="feedback-copy-report">Copy report</button><p id="feedback-copy-status" role="status"></p><a class="st-help-link" href="${crmUrl(draft.contact_id)}" target="_blank" rel="noopener">Open this record in CRM ↗</a><button type="button" data-help-edit>Back to draft</button><p class="st-help-footnote">Nothing has been sent. Your draft remains in this browser tab.</p></div>`;
      return;
    }
    target.innerHTML = `<p class="st-help-intro">Make this workspace work better for you.</p><form id="feedback-form"><fieldset class="st-feedback-fields" ${pending?'disabled':''}><legend class="st-sr-only">Feedback details</legend><div class="st-feedback-kinds" role="group" aria-label="Feedback type">${Object.entries(kinds).map(([id,label])=>`<label><input type="radio" name="kind" value="${id}" ${draft.kind===id?'checked':''}><span>${esc(label)}</span></label>`).join('')}</div><label for="feedback-title">In a few words</label><input id="feedback-title" name="title" maxlength="160" required value="${esc(draft.title)}" placeholder="What would make this better?"><label for="feedback-details">Tell us a little more</label><textarea id="feedback-details" name="details" maxlength="6000" rows="5" required placeholder="${draft.kind==='bug'?'What did you try? What happened, and what did you expect?':'What are you trying to do? What would help?'}">${esc(draft.details)}</textarea><label class="st-feedback-context"><input type="checkbox" name="include_context" ${draft.include_context?'checked':''}><span>Include page context<small>${esc(contextLabel())}</small></span></label><details class="st-feedback-routing" ${!record()?'open':''}><summary>Track on ${esc(record()?.name || 'a CRM record')}</summary><div>${trackingFields()}</div></details></fieldset><p class="st-help-footnote">${writable()?'Saves a review task and note in your CRM.':'Read-only preview. Prepare a draft, then finish saving it in your CRM.'}</p><p id="feedback-error" class="st-feedback-error" role="alert">${pending?'The previous save needs confirmation. Retry the same request to avoid a duplicate.':''}</p><div class="st-help-form-actions"><button type="button" data-help-clear ${pending?'disabled':''}>Clear draft</button><button class="st-primary" type="submit" id="feedback-submit">${pending?'Retry save':writable()?'Save feedback':'Prepare CRM handoff'}</button></div><p class="st-help-footnote" id="feedback-storage">Drafts stay in this tab until you submit or clear them.</p></form>`;
  }
  function readForm() {
    const f = $('feedback-form'); if (!f || pending) return;
    const values = Object.fromEntries(new FormData(f));
    Object.assign(draft, {kind:values.kind, title:values.title, details:values.details, contact_id:values.contact_id, category:values.category, due:values.due || draft.due, include_context:values.include_context === 'on'});
    persist();
  }
  async function submit(event) {
    event.preventDefault(); if (busy) return;
    readForm();
    if (pending && !writable()) { $('feedback-error').textContent = 'This view cannot confirm the earlier save. Check the record in the CRM before submitting again.'; return; }
    if (!pending) {
      if (!draft.title.trim() || !draft.details.trim()) { $('feedback-error').textContent = 'Add a short summary and some details.'; return; }
      if (!record()) { $('feedback-error').textContent = 'Choose an available CRM record.'; return; }
      if (!writable()) { handoff = true; renderFeedback(); $('feedback-copy-title').focus(); return; }
      const due = new Date(draft.due);
      if (!Number.isFinite(due.getTime())) { $('feedback-error').textContent = 'Choose a review date and time.'; return; }
      const context = draft.include_context ? draft.context : {};
      pending = {action:'feedback.submit', request_id:crypto.randomUUID(), data:{kind:draft.kind, title:draft.title.trim(), details:draft.details.trim(), contact_id:draft.contact_id, category:draft.category, due_at:due.toISOString(), context_view:context?.view || '', context_company_id:context?.company_id || ''}};
      persist();
    }
    busy = true; renderFeedback(); $('feedback-submit').disabled = true; $('feedback-submit').textContent = 'Saving…'; $('feedback-error').textContent = '';
    const controller = new AbortController(), timeout = setTimeout(()=>controller.abort(), 25000);
    try {
      const endpoint = state()?.feedback?.endpoint === 'api/crm/feedback' ? 'api/crm/feedback' : 'api/crm/command';
      const response = await fetch(endpoint, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(pending), signal:controller.signal});
      const result = await response.json();
      if (!response.ok) {
        // A validation rejection is safe to edit. Lost access after an earlier
        // uncertain response must not discard the key and risk a second task.
        if (response.status === 400) { pending = null; persist(); }
        throw Error(result.error || 'The CRM could not save this request.');
      }
      if (!result.ok || !result.id || !result.activity_id) throw Error('The save response was incomplete. Retry to confirm the result.');
      receipt = {...result, name:record()?.name, title:draft.title};
      draft = null; pending = null; persist(); renderFeedback();
      api.refresh();
    } catch (error) {
      renderFeedback(); $('feedback-error').textContent = error.message + (pending?' Your draft is kept. Retry save to confirm without creating another task.':' Your draft is kept.');
    } finally { clearTimeout(timeout); busy = false; if ($('feedback-submit')) $('feedback-submit').disabled = false; }
  }
  function init(hooks) {
    api = hooks;
    $('help-launcher').addEventListener('click', open);
    $('help-close').addEventListener('click', close);
    $('help-panel').addEventListener('cancel', event => { event.preventDefault(); close(); });
    $('help-panel').addEventListener('keydown', event => {
      if (!event.target.matches('.st-help-tabs [role=tab]') || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation();
      showTab(event.key==='Home'?'guide':event.key==='End'?'feedback':tab==='guide'?'feedback':'guide', true);
    });
    $('help-panel').addEventListener('click', async event => {
      const b = event.target.closest('button'); if (!b || busy) return;
      if (b.id === 'help-guide-tab') showTab('guide');
      if (b.id === 'help-feedback-tab') showTab('feedback');
      if (b.hasAttribute('data-help-go')) {
        close(); if (b.dataset.helpGo === 'crm') { location.href = './'; return; }
        api.go(b.dataset.helpGo);
        const title = $('workspace-content').querySelector('h1'); if (title) { title.tabIndex = -1; title.focus(); }
      }
      if (b.hasAttribute('data-help-request')) { if (!pending) { draft.kind='help'; if (!draft.title) draft.title=$('help-search').value.slice(0,160); persist(); } showTab('feedback'); }
      if (b.hasAttribute('data-help-clear') || b.hasAttribute('data-help-another')) { draft=null; pending=null; receipt=null; handoff=false; startDraft(); persist(); renderFeedback(); $('feedback-title')?.focus(); }
      if (b.hasAttribute('data-help-edit')) { handoff=false; renderFeedback(); $('feedback-title').focus(); }
      if (b.dataset.helpCopy) {
        const field = $(b.dataset.helpCopy);
        try { await navigator.clipboard.writeText(field.value); $('feedback-copy-status').textContent = 'Copied. Paste it into the CRM to save it.'; }
        catch { field.focus(); field.select(); $('feedback-copy-status').textContent = 'Select and copy this text, then paste it into the CRM.'; }
      }
    });
    $('help-panel').addEventListener('input', event => {
      if (event.target.id === 'help-search') { guideResults(event.target.value); return; }
      if (event.target.closest('#feedback-form')) readForm();
    });
    $('help-panel').addEventListener('change', event => {
      if (!event.target.closest('#feedback-form')) return;
      readForm();
      if (event.target.id === 'feedback-record') { draft.category=record()?.default_category || ''; persist(); renderFeedback(); $('feedback-record').closest('details').open=true; $('feedback-record').focus(); }
      if (event.target.name === 'kind') $('feedback-details').placeholder=draft.kind==='bug'?'What did you try? What happened, and what did you expect?':'What are you trying to do? What would help?';
    });
    $('help-panel').addEventListener('invalid', event => { const detail=event.target.closest('details'); if(detail)detail.open=true; }, true);
    $('help-panel').addEventListener('submit', submit);
  }
  function updated() {
    if ($('help-panel').open && tab === 'feedback' && !$('feedback-form') && !receipt && !handoff) {
      startDraft(); renderFeedback();
    }
  }
  return {init, updated};
})();
