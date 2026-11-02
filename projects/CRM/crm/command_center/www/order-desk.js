// Order Desk: the inbox, one order beside its PO, status replies, the owner's numbers, the part number table.
// Reads come from /api/orders/*; every write is one action on /api/orders/act (order_desk.py), which a
// person makes. Nothing here sends mail or touches an ERP.
(() => {
  'use strict';
  const main = document.getElementById('od');
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : Date.now() + '-' + Math.random().toString(16).slice(2));
  const money = v => v === null || v === undefined ? '' : '$' + Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 });
  const qty = v => v === null || v === undefined ? '' : Number(v).toLocaleString('en-US', { maximumFractionDigits: 3 });
  const time = s => { const d = new Date(s); return isNaN(d) ? '' : d.toLocaleString('en-US', { weekday: 'short', hour: 'numeric', minute: '2-digit' }); };
  const KIND = { order: 'Order', change: 'Change', quote: 'Quote request', status: 'Where is my order', other: 'Other' };
  const SOURCE = { xref: 'part number table', rep: 'taught by a rep', sku: 'our SKU on the PO', history: 'order history',
    fax: 'fax, misread allowed', description: 'description search', none: 'no match' };
  let queue = { items: [] };

  async function get(path) {
    const r = await fetch(path, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || 'Could not load (' + r.status + ')');
    return body;
  }
  async function act(data) {
    const r = await fetch('api/orders/act', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...data, request_id: uuid() }) });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || 'Could not save (' + r.status + ')');
    return body;
  }
  const fail = e => { const p = document.getElementById('od-error'); if (p) { p.textContent = e.message; p.hidden = false; } else alert(e.message); };

  function nav(tab) {
    document.querySelectorAll('.od-top nav a').forEach(a => a.dataset.tab === tab ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current'));
  }

  function badge(it) {
    if (it.state === 'approved') return '<span class="od-pill ok">Approved</span>';
    if (it.state === 'rejected') return '<span class="od-pill">Rejected</span>';
    if (it.state === 'answered' || it.state === 'done') return '<span class="od-pill">Done</span>';
    if (!it.order) return '';
    if (it.red) return `<span class="od-pill red">${it.red} red</span>` + (it.yellow ? ` <span class="od-pill yellow">${it.yellow} yellow</span>` : '');
    if (it.yellow) return `<span class="od-pill yellow">${it.yellow} to check</span>`;
    return '<span class="od-pill ok">All green</span>';
  }

  function list(selected) {
    const items = queue.items;
    const open = items.filter(i => !['approved', 'rejected', 'answered', 'done'].includes(i.state)).length;
    return `<section class="od-list" aria-label="Inbox"><div class="od-list-head"><h1>Inbox</h1><span>${open} open of ${items.length}</span>
      <button type="button" id="od-check">Check inbox</button></div><ul>${items.map(i => {
      const href = i.order ? 'orders#order/' + i.order : 'orders#message/' + i.message;
      const id = i.order || i.message;
      return `<li${id === selected ? ' aria-current="true"' : ''}><a href="${href}"><span class="od-kind k-${esc(i.kind)}">${esc(KIND[i.kind] || i.kind)}</span>
        <b>${esc(i.customer || i.sender)}</b><small>${esc(i.subject)}</small><span class="od-row">${badge(i)}<time>${esc(time(i.received_at))}</time></span></a></li>`;
    }).join('')}</ul></section>`;
  }

  async function showQueue(selected, detail) {
    nav('queue');
    queue = await get('api/orders/queue');
    const empty = '<section class="od-detail"><p class="od-empty">Pick an email on the left. Orders open beside their PO.</p></section>';
    main.innerHTML = `<div class="od-split">${list(selected)}${detail || empty}</div>`;
    document.getElementById('od-check').onclick = () => act({ action: 'inbox.check' }).then(() => route()).catch(fail);
  }

  // ---------------------------------------------------------------- the PO, as it came in
  function original(m) {
    const tabs = m.docs.map((d, i) => {
      let body;
      if (/\.pdf$/i.test(d.name)) body = `<iframe class="od-pdf" title="${esc(d.name)}" src="api/orders/file?m=${esc(m.id)}&n=${d.file}"></iframe>`;
      else if (d.rows) body = `<div class="od-scroll"><table class="od-sheet">${d.rows.map((r, j) => `<tr>${r.map(c => j ? `<td>${esc(c)}</td>` : `<th>${esc(c)}</th>`).join('')}</tr>`).join('')}</table></div>`;
      else body = `<pre class="od-text">${esc(d.text)}</pre>`;
      const ocr = d.how && d.how.includes('OCR') ? `<details><summary>What the OCR step read</summary><pre class="od-text">${esc(d.text)}</pre></details>` : '';
      return `<figure class="od-doc"><figcaption>${esc(d.name)} <small>${esc(d.how)}</small> <a href="api/orders/file?m=${esc(m.id)}&n=${d.file}" target="_blank" rel="noopener">Open</a></figcaption>${body}${ocr}</figure>`;
    }).join('');
    return `<section class="od-po" aria-label="The PO as it came in"><h2>As it came in</h2>
      <dl class="od-mail"><dt>From</dt><dd>${esc(m.from_name)} &lt;${esc(m.sender)}&gt;</dd><dt>Subject</dt><dd>${esc(m.subject)}</dd><dt>Received</dt><dd>${esc(time(m.received_at))}</dd></dl>
      <pre class="od-text od-body">${esc(m.body)}</pre>${tabs}</section>`;
  }

  // ---------------------------------------------------------------- one order
  const flagHtml = (f, ln) => {
    if (f.level === 'info') return `<li class="info">${esc(f.text)}</li>`;
    if (f.resolved) return `<li class="done">${esc(f.text)} <em>${esc(f.how || 'cleared')}</em></li>`;
    const apply = f.suggest && ln ? `<button type="button" data-apply="${esc(f.code)}" data-line="${ln.n}">Apply</button>` : '';
    const accept = ln ? (f.code === 'unknown_part' ? '' : `<button type="button" data-accept="${ln.n}">Keep as is</button>`)
      : (f.code === 'customer' ? '' : `<button type="button" data-oaccept="${esc(f.code)}">${f.code === 'duplicate_po' || f.code === 'change' ? 'Enter anyway' : 'Accept'}</button>`);
    return `<li class="${esc(f.level)}">${esc(f.text)} ${apply}${accept}</li>`;
  };

  function lineRow(ln, o, draft) {
    const open = ln.flags.filter(f => f.level !== 'info' && !f.resolved);
    const level = ln.removed ? 'removed' : open.some(f => f.level === 'red') ? 'red' : open.length ? 'yellow' : 'green';
    const said = [ln.customer_part, ln.description].filter(Boolean).join(' &middot; ');
    const po = `${esc(qty(ln.po_qty))} ${esc(ln.po_uom)}${ln.po_price !== null ? ' @ ' + money(ln.po_price) : ''}`;
    const edit = draft && !ln.removed ? `<details class="od-change"${ln.sku ? '' : ' open'}><summary>Change this line</summary><div class="od-edit"><label>SKU <input data-f="sku" value="${esc(ln.sku || '')}" list="od-sku-${ln.n}" data-line="${ln.n}" autocomplete="off"></label>
      <datalist id="od-sku-${ln.n}">${(ln.alternatives || []).map(a => `<option value="${esc(a)}">`).join('')}</datalist>
      <label>Qty <input data-f="qty" type="number" min="0" step="any" value="${esc(ln.qty)}" data-line="${ln.n}"></label>
      <label>Price <input data-f="price" type="number" min="0" step="0.0001" value="${esc(ln.price ?? '')}" data-line="${ln.n}"></label>
      <button type="button" data-save="${ln.n}">Save</button><button type="button" class="quiet" data-remove="${ln.n}">Remove</button></div></details>` : '';
    return `<article class="od-line ${level}" aria-label="Line ${ln.n}"><header><b>${ln.n}</b><span class="od-said">PO: ${said || '(no part or description)'} &middot; ${po}</span></header>
      <div class="od-ours">${ln.sku ? `<b>${esc(ln.sku)}</b> ${esc(ln.item)}` : '<b>No SKU yet</b>'}
        <span>${esc(qty(ln.qty))} ${esc(ln.uom)} @ ${money(ln.price)}${ln.sku && ln.price !== null ? ' = ' + money(Math.round(ln.qty * ln.price * 100) / 100) : ''}</span>
        <small>Matched by ${esc(SOURCE[ln.source] || ln.source)}${ln.on_hand !== null && ln.on_hand !== undefined ? ' &middot; ' + qty(ln.on_hand) + ' on hand' : ''}</small></div>
      ${ln.flags.length ? `<ul class="od-flags">${ln.flags.map(f => flagHtml(f, draft ? ln : null)).join('')}</ul>` : ''}${edit}</article>`;
  }

  async function showOrder(id) {
    const o = await get('api/orders/order?id=' + encodeURIComponent(id));
    const draft = o.status === 'draft' && o.rights.includes('approve');
    const custPick = draft ? `<select id="od-customer"><option value="">Pick the customer</option>${o.customers.map(c => `<option value="${esc(c.id)}"${c.id === o.customer_id ? ' selected' : ''}>${esc(c.name)}</option>`).join('')}</select>` : esc(o.customer);
    const head = `<div class="od-head"><div><h2>${o.number ? 'Order ' + o.number : 'Draft order'} <small>${esc(o.status)}</small></h2>
      <dl><dt>Customer</dt><dd>${custPick} <small>${esc(o.customer_how)}</small></dd><dt>PO</dt><dd>${esc(o.po_number || 'none found')}</dd>
      <dt>Need by</dt><dd>${esc(o.need_by || 'not given')}</dd><dt>Ship to</dt><dd>${esc(o.ship_to || 'customer default')}</dd></dl></div>
      <div class="od-total"><span>Total</span><b>${money(o.total)}</b></div></div>
      ${o.flags.length ? `<ul class="od-flags order">${o.flags.map(f => flagHtml(f, null)).join('')}</ul>` : ''}`;
    let foot;
    if (draft) {
      foot = `<div class="od-gate">${o.blocking.length ? `<p><b>${o.blocking.length} to clear before approval.</b> Green lines need nothing.</p>` : '<p><b>Ready.</b> Nothing reaches the ERP or the customer until you approve.</p>'}
        <p id="od-error" role="alert" hidden></p><div class="od-actions"><button type="button" class="quiet" id="od-reject">Reject</button>
        <button type="button" class="primary" id="od-approve"${o.blocking.length ? ' disabled' : ''}>Approve order</button></div></div>`;
    } else if (o.status === 'approved') {
      const c = o.confirmation;
      foot = `<div class="od-gate done"><p><b>Approved by ${esc(o.approved_by)}.</b> Import files for the ERP:
        ${Object.keys(o.files).map(l => `<a class="od-file" href="api/orders/import?id=${esc(o.id)}&layout=${esc(l)}">${l === 'qbo' ? 'QuickBooks Online CSV' : 'Generic CSV'}</a>`).join(' ')}</p>
        <p>No import set up? <button type="button" id="od-copy">Copy the lines for the ERP order screen</button>
        <small class="od-note">Tab-separated: paste into the first empty line of the order grid.</small></p>
        <h3>Confirmation to ${esc(c.to)}</h3><p class="od-subj">${esc(c.subject)}</p>
        ${o.confirmation_sent_at ? `<pre class="od-text">${esc(c.body)}</pre><p class="od-sent">Sent by ${esc(o.confirmation_sent_by)}, ${esc(time(o.confirmation_sent_at))}</p>`
          : `<textarea id="od-conf" rows="12">${esc(c.body)}</textarea><p id="od-error" role="alert" hidden></p><div class="od-actions"><button type="button" class="primary" id="od-send">Send confirmation</button></div>`}</div>`;
    } else foot = `<div class="od-gate"><p>Rejected: ${esc(o.reject_reason || '')}</p></div>`;
    const lines = `<section class="od-draft" aria-label="Draft order">${head}${o.lines.map(ln => lineRow(ln, o, draft)).join('')}${foot}</section>`;
    await showQueue(o.id, `<section class="od-detail od-pair">${original(o.message)}${lines}</section>`);
    if (draft && !o.opened_at) act({ action: 'order.open', order: o.id }).catch(() => {});
    wire(o);
  }

  function wire(o) {
    const send = (data) => act({ order: o.id, revision: o.revision, ...data }).then(r => {
      if (r.learned) toast(`Learned: ${r.learned.customer_part} is ${r.learned.sku} for this customer.`);
      return showOrder(o.id);
    }).catch(fail);
    main.querySelectorAll('[data-apply]').forEach(b => b.onclick = () => send({ action: 'line.apply', line: +b.dataset.line, flag: b.dataset.apply }));
    main.querySelectorAll('[data-accept]').forEach(b => b.onclick = () => send({ action: 'line.accept', line: +b.dataset.accept }));
    main.querySelectorAll('[data-oaccept]').forEach(b => b.onclick = () => send({ action: 'order.accept', flag: b.dataset.oaccept }));
    main.querySelectorAll('[data-remove]').forEach(b => b.onclick = () => send({ action: 'line.remove', line: +b.dataset.remove }));
    main.querySelectorAll('[data-save]').forEach(b => b.onclick = () => {
      const n = +b.dataset.save, ln = o.lines.find(l => l.n === n), data = { action: 'line.set', line: n };
      main.querySelectorAll(`input[data-line="${n}"]`).forEach(i => {
        const v = i.value.trim();
        if (i.dataset.f === 'sku' && v && v !== (ln.sku || '')) data.sku = v;
        if (i.dataset.f === 'qty' && v !== '' && Number(v) !== ln.qty) data.qty = Number(v);
        if (i.dataset.f === 'price' && v !== '' && Number(v) !== ln.price) data.price = Number(v);
      });
      if (Object.keys(data).length === 2) return fail(new Error('Nothing changed on line ' + n));
      send(data);
    });
    main.querySelectorAll('input[data-f="sku"]').forEach(i => i.oninput = async () => {
      if (i.value.trim().length < 2) return;
      const r = await get('api/orders/items?customer=' + encodeURIComponent(o.customer_id || '') + '&q=' + encodeURIComponent(i.value)).catch(() => ({ items: [] }));
      document.getElementById(i.getAttribute('list')).innerHTML = r.items.map(x => `<option value="${esc(x.sku)}">${esc(x.description)} (${esc(x.uom)}, ${qty(x.on_hand)} on hand)</option>`).join('');
    });
    const pick = document.getElementById('od-customer');
    if (pick) pick.onchange = () => pick.value && send({ action: 'order.set', customer_id: pick.value });
    const approve = document.getElementById('od-approve');
    if (approve) approve.onclick = () => send({ action: 'order.approve' });
    const reject = document.getElementById('od-reject');
    if (reject) reject.onclick = () => { const why = prompt('Why reject it? (for example: duplicate, handled by phone)'); if (why) send({ action: 'order.reject', reason: why }); };
    const copy = document.getElementById('od-copy');
    if (copy) copy.onclick = () => navigator.clipboard.writeText(o.staging)
      .then(() => toast(`Copied ${o.staging.split('\r\n').length} lines. Paste them into the ERP's order entry grid.`))
      .catch(() => prompt('Copy these lines (Ctrl+C):', o.staging));
    const sendConf = document.getElementById('od-send');
    if (sendConf) sendConf.onclick = () => send({ action: 'order.send_confirmation', body: document.getElementById('od-conf').value });
  }

  function toast(text) {
    const t = document.createElement('p');
    t.className = 'od-toast'; t.setAttribute('role', 'status'); t.textContent = text;
    document.body.append(t); setTimeout(() => t.remove(), 5000);
  }

  // ---------------------------------------------------------------- an email that is not an order
  async function showMessage(id) {
    const m = await get('api/orders/message?id=' + encodeURIComponent(id));
    let side = '';
    if (m.kind === 'status' && m.reply) {
      side = m.reply_sent_at ? `<h2>Reply sent</h2><pre class="od-text">${esc(m.reply.body)}</pre><p class="od-sent">Sent by ${esc(m.reply_sent_by)}, ${esc(time(m.reply_sent_at))}</p>`
        : `<h2>Draft reply</h2><p class="od-subj">To ${esc(m.reply.to)}: ${esc(m.reply.subject)}</p>
           <p class="od-note">${m.reply.found.length ? 'Found in the ERP status export: ' + esc(m.reply.found.join(', ')) : 'Not found: the draft asks for the number.'}</p>
           <textarea id="od-reply" rows="10">${esc(m.reply.body)}</textarea><p id="od-error" role="alert" hidden></p>
           <div class="od-actions"><button type="button" class="primary" id="od-send-reply">Send reply</button></div>`;
    } else if (m.kind === 'quote') side = '<h2>Quote request</h2><p>Quote requests go to the Quote Desk. Nothing to enter here.</p>';
    else side = '<h2>Not an order</h2><p>The desk sorted this as other mail. Nothing to enter.</p>';
    if (m.status !== 'done' && !(m.kind === 'status' && m.reply && !m.reply_sent_at)) side += '<div class="od-actions"><button type="button" id="od-dismiss">Mark done</button></div>';
    await showQueue(m.id, `<section class="od-detail od-pair">${original(m)}<section class="od-draft">${side}</section></section>`);
    const s = document.getElementById('od-send-reply');
    if (s) s.onclick = () => act({ action: 'reply.send', message: m.id, body: document.getElementById('od-reply').value }).then(() => showMessage(m.id)).catch(fail);
    const d = document.getElementById('od-dismiss');
    if (d) d.onclick = () => act({ action: 'message.dismiss', message: m.id }).then(() => showMessage(m.id)).catch(fail);
  }

  // ---------------------------------------------------------------- the owner's numbers
  async function showKpis() {
    nav('kpis');
    const k = await get('api/orders/kpis');
    const tile = (label, value, how, unit = '') => `<div class="od-kpi"><span>${esc(label)}</span><b>${value === null || value === undefined ? '&ndash;' : esc(value) + unit}</b><small>${esc(how)}</small></div>`;
    main.innerHTML = `<section class="od-page"><h1>Numbers</h1><p class="od-sub">${k.orders.received} orders read: ${k.orders.approved} approved, ${k.orders.waiting} waiting, ${k.orders.rejected} rejected.</p>
      <div class="od-kpis">${tile('Minutes per order', k.minutes_per_order, k.how.minutes_per_order)}${tile('Rep hands-on minutes', k.hands_on_minutes, k.how.hands_on_minutes)}
      ${tile('Touchless', k.touchless_pct, k.how.touchless_pct, '%')}${tile('No manual edit', k.no_edit_pct, k.how.no_edit_pct, '%')}
      ${tile('Entry errors', k.error_pct, k.how.error_pct, '%')}${tile('Same-day entry', k.same_day_pct, k.how.same_day_pct, '%')}
      ${tile('Minutes to confirmation', k.confirmation_minutes, k.how.confirmation_minutes)}
      ${tile('Price exceptions caught', k.price_exceptions.count + ' / ' + money(k.price_exceptions.dollars), k.how.price_exceptions)}
      ${tile('Cross-reference coverage', k.xref_coverage_pct, k.how.xref_coverage_pct, '%')}
      ${tile('Status replies', k.status_replies.answered + ' of ' + k.status_replies.asked, 'answered; median ' + (k.status_replies.median_minutes ?? '–') + ' minutes')}</div></section>`;
  }

  async function showXref() {
    nav('xref');
    const k = await get('api/orders/kpis');
    main.innerHTML = `<section class="od-page"><h1>Customer part numbers</h1>
      <p class="od-sub">${k.xref_rows.erp_table} rows came from the ERP's table. The desk learned ${k.xref_rows.learned} more from reps' corrections and approved orders.</p>
      <p>This table is the distributor's own asset. Download it and load it back into the ERP's customer part number table.</p>
      <p><a class="od-file" href="api/orders/xref.csv">Download the cross-reference (CSV)</a></p></section>`;
  }

  function route() {
    const h = location.hash.slice(1) || 'queue';
    const [tab, id] = h.split('/');
    const run = tab === 'order' && id ? showOrder(id) : tab === 'message' && id ? showMessage(id) : tab === 'kpis' ? showKpis() : tab === 'xref' ? showXref() : showQueue();
    run.catch(e => { main.innerHTML = `<p class="od-empty" role="alert">${esc(e.message)}</p>`; });
  }
  addEventListener('hashchange', route);
  route();
})();
