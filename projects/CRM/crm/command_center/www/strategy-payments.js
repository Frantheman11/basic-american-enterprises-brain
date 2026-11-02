/* Manual receipts use the same authenticated CRM writer and durable request IDs. */
window.StrategyPayments = (() => {
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const methods={cash:'Cash',check:'Check',bank_transfer:'Bank transfer',other:'Other'};
  const money=(n,currency='USD')=>(n/100).toLocaleString(undefined,{style:'currency',currency});
  const $=id=>document.getElementById(id);
  let api,pending=null,current=null,busy=false,key='',filter='all',period='all_time';
  function restore(data){
    const next='strategy.payments.'+data.name+'.'+data.person.id;
    if(next===key)return;key=next;
    try{const saved=JSON.parse(sessionStorage.getItem(key)||'null');pending=saved?.request||null;current=saved?.form||null;}catch{pending=null;current=null;}
  }
  function persist(){if(pending)sessionStorage.setItem(key,JSON.stringify({request:pending,form:current}));else sessionStorage.removeItem(key);}
  const btn=(label,action,id='',className='')=>`<button type="button" data-payment-action="${action}" data-payment-id="${esc(id)}" class="${className}">${label}</button>`;
  function companyButton(data,c){return data.payments?.writable&&c.lifecycle!=='archived'?btn('Record payment','record',c.id):'';}
  function selectedPeriod(model){
    if(model.periods)return {...model.periods[period],key:period};
    return {key:'this_month',label:model.month,from:model.month+'-01',to:model.month+'-31',manual_cents:model.collected_cents,combined_cents:model.combined_cents,stripe_cents:model.stripe?.collected_cents,fees_cents:model.stripe?.fees_cents};
  }
  const inPeriod=(day,p)=>day>=p.from&&day<=p.to;
  function stripePanel(model,p){
    const s=model.stripe;
    if(!s||s.state==='not_connected')return '<p class="pay-scope">Stripe is not connected.</p>';
    if(s.state==='restricted')return '<p class="pay-scope">Stripe account totals are available to team-wide CRM readers.</p>';
    if(s.state!=='connected')return `<div class="pay-pending" role="alert"><strong>Stripe needs attention</strong><p>${esc(s.message||'Refresh Stripe before using a combined total.')}</p><small>Manual records below are still available. Missing Stripe data is not counted as zero.</small></div>`;
    const labels={charge:'Payment',partial_capture_reversal:'Uncaptured amount returned',charge_failure:'Payment reversed',refund:'Refund',refund_failure:'Refund failed / reversed',dispute:'Dispute',dispute_reversal:'Dispute reversed',payout:'Bank payout',payout_reversal:'Payout reversed',fee:'Stripe fee'};
    const dash='https://dashboard.stripe.com/'+encodeURIComponent(s.account_id);
    const rows=(s.all_rows||s.rows).filter(r=>inPeriod(r.date,p)),failed=(s.all_failed||s.failed).filter(r=>inPeriod(r.date,p));
    const collections=rows.filter(r=>['charge','partial_capture_reversal','charge_failure','refund','refund_failure','dispute','dispute_reversal'].includes(r.category));
    const other=rows.filter(r=>!collections.includes(r));
    const card=r=>`<article class="pay-card"><div class="pay-card-top"><div><small>${esc(labels[r.category]||r.category.replaceAll('_',' '))}</small><h3>${esc(r.payer||r.description||r.source||r.id)}</h3></div><strong>${esc(money(r.amount_cents,r.currency))}</strong></div><p>${esc(r.date)} · Stripe${r.payment_id?` · <a href="${dash}/payments/${encodeURIComponent(r.payment_id)}" target="_blank" rel="noopener">View payment ↗</a>`:''}</p><details><summary>Source details</summary><small>${esc(r.id)} · funds ${esc(r.funds_status)}</small>${r.payer&&r.description?`<p>${esc(r.description)}</p>`:''}</details></article>`;
    return `<section class="pay-stripe"><div class="pay-card-top"><div><h2>Stripe <span class="st-pill">Connected · read only</span></h2><p>${esc(s.name)} · updated ${esc(new Date(s.read_at).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}))}</p></div><a href="${dash}/payments" target="_blank" rel="noopener">Open Stripe ↗</a></div>
      <div class="pay-summary"><div><small>Total collected · ${esc(p.label)}</small><strong>${p.combined_cents==null?'Unavailable':esc(money(p.combined_cents,s.currency))}</strong><small>Stripe + manual receipts · before fees</small></div><div><small>Via Stripe</small><strong>${p.stripe_cents==null?'Unavailable':esc(money(p.stripe_cents,s.currency))}</strong><small>After refunds and disputes</small></div><div><small>Cash, checks & other</small><strong>${esc(money(p.manual_cents,model.currency))}</strong><small>Recorded manual collections</small></div></div>
      <p class="pay-scope">${p.from?esc(p.from)+' through '+esc(p.to):'All Stripe history + recorded manual receipts through '+esc(p.to)} · ${p.fees_cents==null?'Processing fees unavailable':esc(money(p.fees_cents,s.currency))+' processing fees on collection entries'}. Pending checks are excluded.</p>
      ${p.combined_cents==null?'<p class="pay-pending" role="alert">History for this range is unavailable. Refresh records; missing history is not counted as zero.</p>':''}
      <h3>Stripe collections · ${esc(p.label)}</h3><div class="pay-records">${collections.map(card).join('')||(p.stripe_cents==null?'<p>Stripe history is unavailable for this range.</p>':'<p>No Stripe collections in this range.</p>')}</div>
      <p class="pay-scope">Stripe balance: ${esc(money(s.available_cents,s.currency))} available · ${esc(money(s.pending_cents,s.currency))} pending. Payouts to your bank are transfers, not new collections.</p>
      ${!s.charges_enabled?'<p class="pay-pending">Stripe says accepting charges needs account setup. Open Stripe to review.</p>':''}
      ${failed.length?`<details class="pay-pending" open><summary>${failed.length} failed payment attempt${failed.length===1?'':'s'} · ${esc(p.label)}</summary>${failed.map(f=>`<p>${esc(f.date)} · ${esc(money(f.amount_cents,f.currency))} · ${esc(f.reason)} <a href="${dash}/payments/${encodeURIComponent(f.id)}" target="_blank" rel="noopener">Review in Stripe ↗</a></p>`).join('')}</details>`:''}
      <details><summary>Payouts, fees & other Stripe activity (${other.length})</summary><p class="pay-scope">Excluded from collections. These entries are not editable manual receipts and are not yet assigned to CRM companies.</p><div class="pay-records">${other.map(card).join('')||'<p>No other Stripe activity in this range.</p>'}</div></details></section>`;
  }
  function render(data){
    restore(data);const model=data.payments;
    if(!model?.enabled)return '<div class="st-empty">Payment recording is not connected yet.</div>';
    const p=selectedPeriod(model);
    const rows=model.rows.filter(r=>(filter==='all'||r.contact_id===filter)&&inPeriod(r.collected_on||r.received_on,p));
    const available=[...new Map(model.rows.map(p=>[p.contact_id,p.company_name])).entries()];
    return `<div class="st-heading"><div><h1>Payments</h1><p>Stripe collections and the cash, checks and other payments you record.</p></div>${model.writable?btn('Record payment','record','','st-primary'):''}</div>${model.periods?`<div class="st-controls"><label>Payment period<select id="payment-period">${['all_time','this_month','last_month'].map(k=>`<option value="${k}" ${k===period?'selected':''}>${esc(model.periods[k].label)}</option>`).join('')}</select></label><p class="pay-period-note">Compare the same dates across Stripe and manual receipts.</p></div>`:''}${stripePanel(model,p)}<h2>Manual receipts · ${esc(p.label)}</h2>
      ${pending?`<div class="pay-pending" role="status">A payment save needs confirmation. ${btn('Check saved result','resume')}</div>`:''}
      <div class="pay-summary"><div><small>Collected · ${esc(p.label)}</small><strong>${esc(money(p.manual_cents,model.currency))}</strong></div><div><small>Checks currently awaiting clearance</small><strong>${esc(money(model.pending_cents,model.currency))}</strong></div><div><small>All-time manual collections, net</small><strong>${esc(money(model.total_cents,model.currency))}</strong></div></div>
      <p class="pay-scope">Manual records for ${esc(model.scope)}. Totals subtract recorded refunds. Record payments received outside Stripe here; adding a Stripe payment again would count it twice. Historical notes and won deals are not added automatically.</p>
      <div class="st-controls"><label>Filter manual receipts<select id="payment-company-filter"><option value="all">All companies</option>${available.map(([id,name])=>`<option value="${esc(id)}" ${filter===id?'selected':''}>${esc(name)}</option>`).join('')}</select></label></div>
      <div class="pay-records">${rows.map(p=>`<article class="pay-card"><div class="pay-card-top"><div><small>${esc(p.company_name)}${p.company_deleted?' · archived record':''}</small><h2>${p.kind==='refund'?'−':''}${esc(money(p.amount_cents,p.currency))}</h2></div><span class="st-pill">${p.status==='pending'?'Check pending':p.status==='voided'?'Voided entry':p.kind==='refund'?'Refund recorded':'Collected'}</span></div><p>${esc(methods[p.method])} · ${p.kind==='refund'?'Returned':'Received'} ${esc(p.received_on)}${p.method==='check'&&p.collected_on&&p.kind!=='refund'?' · Cleared '+esc(p.collected_on):''}</p>${p.reference?`<p>Reference: ${esc(p.reference)}</p>`:''}${p.refunded_cents?`<p>${esc(money(p.refunded_cents,p.currency))} refunded</p>`:''}
        ${model.writable&&!p.company_deleted&&p.status==='pending'?`<div class="st-actions">${btn('Mark check cleared','clear',p.id,'st-primary')}</div>`:''}
        <details><summary>Details & corrections</summary>${p.note?`<p>${esc(p.note)}</p>`:''}${p.void_reason?`<p>Correction: ${esc(p.void_reason)}</p>`:''}<small>Receipt ${esc(p.id)} · recorded ${esc(p.created_at.slice(0,10))}</small>${model.writable&&!p.company_deleted&&p.status!=='voided'?`<div class="st-actions">${p.kind==='payment'&&p.status==='collected'&&p.refunded_cents<p.amount_cents?btn('Record a refund already made','refund',p.id):''}${!p.refunded_cents?btn('Void incorrect entry','void',p.id):''}</div>`:''}</details></article>`).join('')||'<div class="st-empty">No manual payments recorded here yet. Use Record payment to add a receipt.</div>'}</div>`;
  }
  function show(type,id=''){
    if(busy)return;const data=api.state();restore(data);
    if(pending){draw();return;}
    if(!data.payments?.writable)return;
    current={type,id,row:data.payments.rows.find(p=>p.id===id)};draw();
  }
  function draw(values={}){
    const data=api.state(),model=data.payments,f=current;if(!f)return;
    const input=(label,name,type='text',value='',extra='')=>`<label>${label}<input name="${name}" type="${type}" value="${esc(values[name]??value)}" ${extra}></label>`;
    const area=(label,name,required=false)=>`<label>${label}<textarea name="${name}" maxlength="${name==='duplicate_reason'||name==='reason'?500:2000}" ${required?'required':''}>${esc(values[name]||'')}</textarea></label>`;
    let fields='',title='',save='Save payment';
    if(f.type==='record'){
      title='Record payment';const companies=data.companies.filter(c=>c.lifecycle!=='archived');
      fields=`<label>Company<select name="contact_id" required><option value="">Choose a company</option>${companies.map(c=>`<option value="${esc(c.id)}" ${(values.contact_id||f.id)===c.id?'selected':''}>${esc(c.name)}</option>`).join('')}</select></label><div class="st-form-grid">${input('Amount ('+model.currency+')','amount','number','','required min="0.01" max="1000000000" step="0.01" inputmode="decimal"') }<label>Method<select name="method">${Object.entries(methods).map(([id,label])=>`<option value="${id}" ${(values.method||'cash')===id?'selected':''}>${label}</option>`).join('')}</select></label></div>${input('Date received','received_on','date',model.today,`required max="${esc(model.today)}"`)}<p class="pay-check-note">Checks are saved as pending. Mark a check cleared once the money is available.</p><details ${(values.reference||values.note||values.duplicate_reason)?'open':''}><summary>Reference & notes (optional)</summary>${input('Check number or reference','reference','text','','maxlength="120"')}${area('What was this payment for?','note')}<details ${values.duplicate_reason?'open':''}><summary>Recording a similar, separate payment?</summary>${area('Explain why this is a separate payment','duplicate_reason')}</details></details>`;
    }else{
      const p=f.row;if(!p)return;fields=`<p><strong>${esc(p.company_name)} · ${esc(money(p.amount_cents,p.currency))}</strong><br>${esc(methods[p.method])} received ${esc(p.received_on)}</p>`;
      if(f.type==='clear'){title='Mark check cleared';save='Record clearance';fields+=input('Date cleared','cleared_on','date',model.today,`required min="${esc(p.received_on)}" max="${esc(model.today)}"`);}
      else if(f.type==='void'){title='Void incorrect entry';save='Void this entry';fields+='<p>Use this to correct a mistaken record. The original stays in the history. For money returned to a customer, record a refund instead.</p>'+area('Correction reason','reason',true);}
      else{title='Record refund';save='Save refund record';fields+='<p>Record money already returned to this customer.</p>'+input('Amount refunded ('+p.currency+')','amount','number','',`required min="0.01" max="${(p.amount_cents-p.refunded_cents)/100}" step="0.01"`)+input('Date returned','received_on','date',model.today,`required min="${esc(p.collected_on)}" max="${esc(model.today)}"`)+area('Refund note / reference','note',true);}
    }
    $('payment-title').textContent=title;$('payment-fields').innerHTML=fields;$('payment-error').textContent='';$('payment-save').textContent=save;$('payment-save').disabled=busy;
    if(pending){$('payment-fields').innerHTML=`<p>${esc(title)} is awaiting a confirmed receipt from the CRM.</p><p>Check the saved result using the same request. This will not repeat a confirmed save.</p>`;$('payment-save').textContent='Check saved result';}
    if(!$('payment-editor').open)$('payment-editor').showModal();
  }
  function cents(raw){if(!/^\d+(?:\.\d{1,2})?$/.test(raw))throw Error('Enter an amount with at most two decimal places.');const [whole,part='']=raw.split('.');return Number(whole)*100+Number(part.padEnd(2,'0'));}
  async function submit(event){
    event.preventDefault();if(busy)return;let values={};
    try{
      if(!pending){values=Object.fromEntries(new FormData(event.target));let payload;
        if(current.type==='record'){const {amount,...rest}=values;payload={...rest,amount_cents:cents(amount)};}
        else{payload={id:current.id,revision:current.row.revision,...values};if(current.type==='refund'){payload.amount_cents=cents(values.amount);delete payload.amount;}}
        pending={action:'payment.'+current.type,data:payload,request_id:crypto.randomUUID()};
        try{persist();}catch{pending=null;throw Error('Enable tab storage so interrupted saves can be recovered safely.');}
      }
      busy=true;draw();$('payment-save').disabled=true;
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);
      try{
        const response=await fetch(api.state().connection?.mode==='live_readonly'?'api/crm/payment':'api/crm/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pending),signal:controller.signal});
        const result=await response.json();
        if(!response.ok){
          if([400,403,404,409].includes(response.status)){
            if(!Object.keys(values).length){values={...pending.data};if(values.amount_cents)values.amount=String(values.amount_cents/100);}
            pending=null;persist();busy=false;draw(values);
            if(response.status===409&&current.type!=='record'){$('payment-save').disabled=true;await api.refresh();}
          }
          throw Error(result.error||'Could not save the payment record.');
        }
        if(result.ok!==true||!result.id)throw Error('The save could not be confirmed.');
        pending=null;try{persist();}catch{};$('payment-editor').close();api.notify('Payment record saved to the CRM.');await api.refresh();
      }finally{clearTimeout(timer);}
    }catch(error){$('payment-error').textContent=(error.name==='AbortError'?'The save could not be confirmed.':error.message)+(pending?' Your request is kept. Check the saved result to confirm.':'');}
    finally{busy=false;if(pending){$('payment-save').disabled=false;$('payment-save').textContent='Check saved result';}}
  }
  function init(hooks){api=hooks;
    document.addEventListener('click',event=>{const b=event.target.closest('button');if(!b)return;if(b.hasAttribute('data-payment-close')){if(!busy)$('payment-editor').close();return;}if(b.dataset.paymentAction)show(b.dataset.paymentAction,b.dataset.paymentId);});
    document.addEventListener('change',event=>{if(event.target.id==='payment-company-filter'){filter=event.target.value;api.render();$('payment-company-filter')?.focus();}if(event.target.id==='payment-period'&&['all_time','this_month','last_month'].includes(event.target.value)){period=event.target.value;api.render();$('payment-period')?.focus();}});
    $('payment-form').addEventListener('submit',submit);$('payment-editor').addEventListener('cancel',event=>{if(busy)event.preventDefault();});
  }
  return {init,render,companyButton,money,cents};
})();
