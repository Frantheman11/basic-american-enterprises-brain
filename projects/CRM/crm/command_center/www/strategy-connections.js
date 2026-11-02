/* Source status comes from the current read, never from a module's built label. */
window.StrategyConnections=(()=>{
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels={connected:'Connected',attention:'Needs attention',not_connected:'Not connected',restricted:'Limited access',local:'Local data',unknown:'Not verified'};
  function models(data,failed=false,now=Date.now()){
    const connection=data.connection||{},read=connection.read_at||data.server_time;
    const age=now-Date.parse(read),fresh=!failed&&Number.isFinite(age)&&age>=-60000&&age<=180000;
    const payments=data.payments||{},stripe=payments.stripe||{},chat=data.chat_handoffs||{},analytics=data.analytics||{};
    const rows=[
      {id:'crm',name:'Clients, prospects & tasks',state:['live_readonly','live_hosted'].includes(connection.mode)?'connected':'local',detail:'Company, task and payment edits update automatically.',at:read,view:'companies',action:'Open companies'},
      {id:'manual',name:'Cash & check receipts',state:payments.enabled?(payments.writable?'connected':'restricted'):'not_connected',detail:payments.writable?'Receipts save to the live CRM with their history. Checks wait for recorded clearance.':'Manual receipt entry is unavailable for this view.',at:read,view:'payments',action:'Open payments'},
      {id:'stripe',name:'Stripe reporting',state:stripe.state==='connected'?'connected':stripe.state==='restricted'?'restricted':stripe.state==='error'?'attention':'not_connected',detail:stripe.state==='connected'?'Read-only collections, refunds, fees and balances. Stripe remains the source; refreshed about every minute.':stripe.message||'A read-only Stripe connection is needed.',at:stripe.read_at,view:'payments',action:'Review payments'},
      {id:'chat',name:'Website chat → CRM',state:chat.restricted?'restricted':!chat.enabled?'not_connected':!chat.worker_healthy||chat.unreadable||chat.counts?.attention||chat.counts?.retrying?'attention':'connected',detail:chat.restricted?'Handoff status requires team-wide CRM visibility.':!chat.enabled?'Website inquiry delivery is not connected.':!chat.worker_healthy?'The handoff worker has no recent heartbeat. Review delivery before relying on it.':`${chat.counts?.attention||0} need attention · ${chat.counts?.retrying||0} retrying · ${chat.counts?.delivered||0} delivered.`,at:chat.checked_at,view:'departments',department:'sales',action:'Review chat handoffs'},
      {id:'feedback',name:'Feedback → CRM',state:data.feedback?.writable?'connected':'not_connected',detail:data.feedback?.writable?'Bugs, ideas and requests save as CRM review tasks.':'Feedback is saved as a local draft until a CRM writer is available.',at:read,action:'Open help & feedback',help:true},
      {id:'analytics',name:'Website traffic',state:analytics.state==='connected'?'connected':analytics.state==='error'?'attention':analytics.state==='restricted'?'restricted':'not_connected',detail:analytics.state==='connected'?`${analytics.sessions.toLocaleString()} sessions · ${analytics.active_users.toLocaleString()} active visitors · ${analytics.page_views.toLocaleString()} page views. ${analytics.from} through ${analytics.to} (${analytics.timezone}).` :analytics.message||'Google Analytics reporting needs a read-only sign-in. An installed website tag does not connect this counter.',at:analytics.read_at,view:'connections',action:analytics.dashboard_url?'Open Google Analytics ↗':'Review traffic setup',url:analytics.dashboard_url,analytics}
    ];
    for(const row of rows)if(!fresh&&['connected','attention'].includes(row.state)){row.state='unknown';row.detail='The latest CRM refresh is unavailable or stale. Refresh records before relying on this status.';}
    return rows;
  }
  function render(data,failed=false){
    const rows=models(data,failed),attention=rows.filter(r=>r.state==='attention').length;
    const rank={attention:0,unknown:1,not_connected:2,restricted:3,connected:4,local:5};
    rows.sort((a,b)=>rank[a.state]-rank[b.state]);
    return `<div class="st-heading"><div><h1>Connections</h1><p class="st-subtitle">What is feeding the command center, and what needs attention.</p></div><button data-action="refresh-sources" class="st-primary">Refresh connections</button></div>
      <p>${attention?`${attention} connection${attention===1?' needs':'s need'} attention.`:'Each status is based on the latest read.'} Catalogued builds are separate from live connections.</p>
      <div class="st-module-grid">${rows.map(r=>`<article class="st-task st-module"><div class="st-task-head"><h2>${esc(r.name)}</h2><span class="st-pill ${r.state==='connected'?'st-good':''}">${labels[r.state]}</span></div><p>${esc(r.detail)}</p>${r.analytics?.state==='connected'?`<p class="st-subtitle">${esc(r.analytics.property_name)} · property ${esc(r.analytics.property_id)}<br>${esc(r.analytics.hostnames.join(', '))}<br>Refreshes about every five minutes. Excludes today; Google may still be processing recent data.</p>`:''}${r.at?`<p class="st-subtitle">Checked ${esc(new Date(r.at).toLocaleString())}</p>`:''}<div class="st-actions">${r.url&&/^https:\/\/analytics\.google\.com\//.test(r.url)?`<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.action)}</a>`:`<button ${r.help?'data-action="source-help"':r.department?`data-action="department" data-id="${esc(r.department)}"`:`data-action="view" data-id="${esc(r.view)}"`}>${esc(r.action)}</button>`}</div></article>`).join('')}</div>`;
  }
  return {models,render};
})();
