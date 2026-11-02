// The Quote Desk screen: Inbox, one RFQ, Follow-ups, Quote log, Numbers, Paste an RFQ.
// One script, hash routes. Every number on screen comes from /api/quotes/*; every write is a POST
// to /api/quotes with an action, and the server holds the approval gate (quote_desk_store.py).
(() => {
  const main = document.getElementById('qd');
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const usd = (n) => n == null ? '' : '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const when = (iso) => iso ? new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '';
  const day = (iso) => iso ? new Date(iso + (iso.length === 10 ? 'T12:00:00' : '')).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '';
  const ago = (iso) => { const h = (Date.now() - new Date(iso)) / 36e5; return h < 1 ? 'just now' : h < 24 ? Math.round(h) + ' h ago' : Math.round(h / 24) + ' d ago'; };
  const STATUS = {
    drafted: ['Draft ready', 'ok'], needs_info: ['Needs info', 'warn'], approved: ['Approved', 'ok'],
    stopped_itar: ['ITAR: stopped', 'bad'], quoted: ['Quote sent', 'done'], not_rfq: ['Not an RFQ', 'quiet'],
    asked: ['Asked the buyer', 'warn'], declined: ['No bid', 'quiet'], new: ['New', 'warn'],
    draft: ['Draft', 'ok'], sent: ['Sent', 'done'], won: ['Won', 'good'], lost: ['Lost', 'bad'], no_answer: ['No answer', 'quiet'],
  };
  const KIND = { rfq: 'RFQ', po: 'PO', question: 'Question', other: 'Other' };
  const tag = (s) => { const [t, c] = STATUS[s] || [s, 'quiet']; return `<span class="qd-tag ${c}">${esc(t)}</span>`; };

  async function get(path) {
    const r = await fetch(path, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || r.statusText);
    return j;
  }
  async function act(body) {
    const r = await fetch('api/quotes', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || r.statusText);
    return j;
  }
  function flash(msg, bad) {
    const el = document.createElement('p');
    el.className = 'qd-flash' + (bad ? ' bad' : '');
    el.setAttribute('role', bad ? 'alert' : 'status');
    el.textContent = msg;
    main.prepend(el);
    setTimeout(() => el.remove(), 6000);
  }

  // ---- Inbox --------------------------------------------------------------------------------
  async function inbox() {
    const d = await get('api/quotes/inbox');
    const c = d.counts;
    const rows = d.rfqs.map((r) => `<tr data-go="rfq/${esc(r.id)}" tabindex="0">
      <td>${esc(ago(r.received_at))}</td>
      <td><strong>${esc(r.customer || r.domain)}</strong><br><small>${esc(r.sender_name || r.sender)}</small></td>
      <td>${esc(r.subject)}</td><td><span class="qd-kind">${KIND[r.kind] || r.kind}</span></td>
      <td>${tag(r.status)}${r.status === 'needs_info' ? `<br><small>${r.gaps.reduce((n, g) => n + g[1].length, 0)} missing</small>` : ''}</td>
      <td class="num">${r.value ? usd(r.value) : ''}</td></tr>`).join('');
    main.innerHTML = `<h1>Inbox</h1>
      <p class="qd-sub">Every email the desk sorted. RFQs are read, matched to past jobs and drafted. Nothing goes to a customer until a person approves it.</p>
      <div class="qd-stats">
        ${stat('Drafts to review', c.drafted, 'Read, matched, priced')}
        ${stat('Need info', c.needs_info + c.asked, 'Missing specs, question drafted')}
        ${stat('ITAR: stopped', c.stopped_itar, 'Handle by hand, never read')}
        ${stat('Quotes sent', c.quoted, 'Waiting on the buyer')}
      </div>
      <div class="qd-card"><table class="qd-table"><thead><tr><th>In</th><th>From</th><th>Subject</th><th>Type</th><th>Status</th><th class="num">Value</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="6">No email yet.</td></tr>'}</tbody></table></div>`;
  }
  const stat = (label, n, sub) => `<div class="qd-stat"><span>${esc(label)}</span><b>${n ?? 0}</b><small>${esc(sub)}</small></div>`;

  // ---- One RFQ ------------------------------------------------------------------------------
  async function rfq(id) {
    const r = await get('api/quotes/rfq?id=' + encodeURIComponent(id));
    const q = r.quote;
    const d = q ? q.draft : null;
    const editable = q && (q.status === 'draft' || q.status === 'approved');
    let html = `<p><a href="quotes#inbox">← Inbox</a></p>
      <h1>${esc(r.subject || '(no subject)')}</h1>
      <p class="qd-sub">${esc(r.customer || r.domain)} · ${esc(r.sender_name)} &lt;${esc(r.sender)}&gt; · ${esc(when(r.received_at))} · ${tag(r.status)}
      ${q ? ` · Quote <b>${esc(q.number)}</b> ${tag(q.status)}` : ''}</p>`;
    if (r.status === 'stopped_itar') {
      html += `<div class="qd-card qd-alert"><h2>ITAR mark found. The desk stopped.</h2>
        <p>This email carries: ${r.itar.map((m) => `<code>${esc(m)}</code>`).join(' ')}.</p>
        <p>v1 takes no ITAR or CUI work. Nothing in it was read, matched, priced or sent to an AI model. Quote it by hand under your own ITAR process.</p>
        <button data-act="decline" data-reason="ITAR: quoted by hand outside the desk">Mark as handled by hand</button></div>`;
    }
    html += `<div class="qd-grid"><div class="qd-card"><h2>The email</h2><pre class="qd-mail">${esc(r.body)}</pre>
      ${r.attachments.map((a) => `<details><summary>📎 ${esc(a.name)}</summary><pre class="qd-mail">${esc(a.text)}</pre></details>`).join('')}
      ${r.kind !== 'rfq' ? `<p class="qd-note">Sorted as <b>${KIND[r.kind]}</b>, so the desk did not draft a quote. The order desk takes these later.</p>` : ''}</div>`;
    if (d) html += `<div class="qd-card"><h2>What the desk read</h2>${d.items.map(readTable).join('')}</div>`;
    html += '</div>';
    if (r.gaps.length && (r.status === 'needs_info')) {
      html += `<div class="qd-card qd-warn"><h2>Missing specs</h2><ul>${r.gaps.map(([pn, gs]) => gs.map((g) => `<li>${pn ? `<b>${esc(pn)}</b>: ` : ''}${esc(g)}</li>`).join('')).join('')}</ul>
        <p>Fill them in below if you know them, or send this question to the buyer:</p>
        <textarea id="qd-question" rows="8">${esc(r.question)}</textarea>
        <div class="qd-actions"><button class="primary" data-act="ask">Send question to buyer</button><button data-act="decline" data-reason="No bid">No bid</button></div></div>`;
    }
    if (d) {
      d.items.forEach((it, i) => { html += itemCard(it, i, r, editable); });
      html += quoteCard(q, d, r, editable);
    }
    if (r.followups.length) {
      html += `<div class="qd-card"><h2>Follow-ups</h2><ul class="qd-list">${r.followups.map((f) => `<li>Follow-up ${f.n}, due ${esc(day(f.due_on))}: <b>${f.status === 'pending' ? 'waiting for your click' : esc(f.status)}</b>${f.sent_by ? ` by ${esc(f.sent_by)}` : ''}</li>`).join('')}</ul></div>`;
    }
    if (r.outbox.length) {
      html += `<div class="qd-card"><h2>What went out</h2><ul class="qd-list">${r.outbox.map((o) => `<li><b>${esc(o.kind.replace('_', '-'))}</b> to ${esc(o.to_addr)}, ${esc(when(o.at))}, approved by ${esc(o.who)}. <small>${esc(o.delivery)}</small></li>`).join('')}</ul></div>`;
    }
    main.innerHTML = html;
    wire(r, q);
  }

  function readTable(it) {
    const row = (k, v, need) => `<tr class="${!v && need ? 'missing' : ''}"><th>${k}</th><td>${v ? esc(v) : need ? '<b>Missing</b>' : '<span class="qd-muted">not stated</span>'}</td></tr>`;
    const size = it.size ? (it.size.shape === 'round' ? `Ø${it.size.dims[0]} × ${it.size.dims[1]} in` : it.size.dims.join(' × ') + ' in') : '';
    return `<h3>${esc(it.part_number || 'Part')} ${it.revision ? 'Rev ' + esc(it.revision) : ''}</h3><table class="qd-read">
      ${row('Description', it.description)}${row('Material', it.material, 1)}${row('Quantities', (it.quantities || []).join(' / '), 1)}
      ${row('Size', size)}${row('Tightest tolerance', (it.tolerances || [])[0])}${row('Finish', it.finish, 1)}
      ${row('Heat treat', it.heat_treat)}${row('Plating', it.plating)}${row('Certs', (it.certs || []).join(', '))}${row('Due', it.due_date ? day(it.due_date) : '', 1)}</table>`;
  }

  function itemCard(it, i, r, editable) {
    const inp = it.inputs;
    const machines = Object.entries(r.machines).map(([k, m]) => `<option value="${esc(k)}"${k === inp.machine ? ' selected' : ''}>${esc(m.label)} ($${m.rate}/h)</option>`).join('');
    const matches = it.matches.map((m, n) => `<div class="qd-match${m.id === inp.basis ? ' basis' : ''}">
        <div class="qd-score" title="Similarity score">${Math.round(m.score)}</div>
        <div><b>${esc(m.part_number)}</b> ${esc(m.description)} <small>· ${esc(m.customer)} · ${esc(m.material)} · ${esc(day(m.quoted_on))}</small><br>
        ${m.quantities.map((q, k) => `${q} @ ${usd(m.unit_prices[k])}`).join(' · ')} · ${tag(m.outcome)}${m.reason && m.outcome !== 'won' ? ` <small>${esc(m.reason)}</small>` : ''}<br>
        <small>${esc(m.why.join(', '))}${m.id === inp.basis ? ' · <b>run time taken from this job</b>' : ''} · job ${esc(m.id)}</small></div></div>`).join('');
    const fill = (it.missing || []).length && editable ? `<div class="qd-fill"><b>Fill a gap:</b>
        ${!it.material || it.material.startsWith('?') ? `<label>Material <select data-fill="material" data-i="${i}"><option value="">choose</option>${r.materials.map((m) => `<option>${esc(m)}</option>`).join('')}</select></label>` : ''}
        ${!it.finish ? `<label>Finish <input data-fill="finish" data-i="${i}" placeholder="e.g. As machined"></label>` : ''}
        ${!(it.quantities || []).length ? `<label>Quantities <input data-fill="quantities" data-i="${i}" placeholder="25, 50, 100"></label>` : ''}
        ${!it.due_date ? `<label>Due <input type="date" data-fill="due_date" data-i="${i}"></label>` : ''}</div>` : '';
    const lines = it.lines.map((l) => `<tr><td class="num">${l.qty}</td><td class="num">${editable ? `<input class="qd-num" data-over="${l.qty}" data-i="${i}" value="${l.unit.toFixed(2)}" aria-label="Unit price at ${l.qty}">` : usd(l.unit)}</td>
        <td class="num">${usd(l.total)}</td><td class="num qd-muted">${usd(l.cost_each)}</td><td><small>${esc(l.note)}</small></td></tr>`).join('');
    return `<section class="qd-card"><h2>${esc(it.part_number || 'Part')}: 3 most similar past jobs</h2>${matches || '<p>No past job looks like this one. The estimator sets the run time.</p>'}
      ${fill}<h2 class="qd-gap">Draft price</h2>
      <div class="qd-inputs">
        <label>Machine <select data-in="machine" data-i="${i}" ${editable ? '' : 'disabled'}>${machines}</select></label>
        <label>Setup (h) <input class="qd-num" data-in="setup_hours" data-i="${i}" value="${inp.setup_hours}" ${editable ? '' : 'disabled'}></label>
        <label>Run (min each) <input class="qd-num" data-in="cycle_minutes" data-i="${i}" value="${inp.cycle_minutes}" ${editable ? '' : 'disabled'}></label>
        <label>Material each ($) <input class="qd-num" data-in="material_each" data-i="${i}" value="${inp.material_each ?? ''}" ${editable ? '' : 'disabled'}></label>
        <label>Markup (%) <input class="qd-num" data-in="markup_pct" data-i="${i}" value="${inp.markup_pct}" ${editable ? '' : 'disabled'}></label>
      </div>
      <table class="qd-table"><thead><tr><th class="num">Qty</th><th class="num">Unit</th><th class="num">Line</th><th class="num">Our cost each</th><th></th></tr></thead><tbody>${lines || '<tr><td colspan="5">No quantity yet.</td></tr>'}</tbody></table>
      <details><summary>Where every number came from</summary><ul class="qd-list">${it.sources.map((s) => `<li>${esc(s)}</li>`).join('')}</ul></details>
      ${editable ? `<div class="qd-actions"><button data-act="save" data-i="${i}">Recalculate with my numbers</button></div>` : ''}</section>`;
  }

  function quoteCard(q, d, r, editable) {
    const gate = q.status === 'approved' && q.approval_current;
    const lead = `<label>Lead time (working days) <input class="qd-num" id="qd-lead" value="${d.lead_days}" ${editable ? '' : 'disabled'}></label>`;
    let buttons = '';
    if (q.status === 'draft') buttons = `<button class="primary" data-act="approve">Approve this quote</button><button disabled title="Approve first">Send</button>`;
    if (gate) buttons = `<button disabled>Approved by ${esc(q.approved_by)}</button><button class="primary" data-act="send">Send quote</button>`;
    if (q.status === 'sent') buttons = `<label>Outcome <select id="qd-outcome"><option value="won">Won</option><option value="lost">Lost</option><option value="no_answer">No answer</option></select></label>
      <label>Reason <select id="qd-reason"><option value="">(none)</option>${r.reasons.map((x) => `<option>${esc(x)}</option>`).join('')}</select></label><button class="primary" data-act="outcome">Record</button>`;
    return `<section class="qd-card qd-quote"><h2>The quote, as the buyer will see it</h2>
      ${d.warnings.map((w) => `<p class="qd-note warn">${esc(w)}</p>`).join('')}
      <p><b>To:</b> ${esc(d.to)}<br><b>Subject:</b> ${esc(d.subject)}</p><pre class="qd-mail">${esc(d.body)}</pre>
      ${editable ? `<div class="qd-inputs">${lead}<label class="wide">Note to add <input id="qd-note" value="${esc(d.note || '')}" placeholder="e.g. Price includes a one-time fixture"></label><button data-act="text">Update the quote text</button></div>` : ''}
      <div class="qd-gate"><span>${q.status === 'draft' ? 'Nothing goes out until you approve. Any change after approval needs a new approval.' : gate ? 'Approved. Send logs it and schedules the day 3 and day 7 follow-ups.' : ''}</span>
      <div class="qd-actions">${buttons}</div></div></section>`;
  }

  function wire(r, q) {
    const run = async (body, msg) => {
      try { await act(body); await rfq(r.id); flash(msg); } catch (e) { flash(e.message, true); }
    };
    main.querySelectorAll('[data-act]').forEach((b) => b.addEventListener('click', () => {
      const a = b.dataset.act;
      if (a === 'ask') return run({ action: 'rfq.ask', rfq_id: r.id, text: document.getElementById('qd-question').value }, 'Question logged to send to the buyer.');
      if (a === 'decline') return run({ action: 'rfq.decline', rfq_id: r.id, reason: b.dataset.reason }, 'Recorded.');
      if (a === 'approve') return run({ action: 'quote.approve', quote_id: q.id }, 'Approved. Send is now open.');
      if (a === 'send') return run({ action: 'quote.send', quote_id: q.id }, 'Sent and logged. Follow-ups scheduled.');
      if (a === 'outcome') return run({ action: 'quote.outcome', quote_id: q.id, outcome: document.getElementById('qd-outcome').value, reason: document.getElementById('qd-reason').value }, 'Outcome recorded.');
      if (a === 'text') return run({ action: 'quote.edit', quote_id: q.id, changes: { note: document.getElementById('qd-note').value, lead_days: document.getElementById('qd-lead').value } }, 'Quote text updated.');
      if (a === 'save') {
        const i = Number(b.dataset.i);
        const ch = { index: i, overrides: {} };
        main.querySelectorAll(`[data-in][data-i="${i}"]`).forEach((el) => { ch[el.dataset.in] = el.dataset.in === 'machine' ? el.value : (el.value === '' ? null : Number(el.value)); });
        const item = q.draft.items[i];
        main.querySelectorAll(`[data-over][data-i="${i}"]`).forEach((el) => {
          const line = item.lines.find((l) => String(l.qty) === el.dataset.over);
          if (line && Number(el.value).toFixed(2) !== line.unit.toFixed(2)) ch.overrides[el.dataset.over] = Number(el.value);
          else if (item.inputs.overrides[el.dataset.over] != null) ch.overrides[el.dataset.over] = Number(el.value);
        });
        main.querySelectorAll(`[data-fill][data-i="${i}"]`).forEach((el) => {
          if (!el.value) return;
          ch[el.dataset.fill] = el.dataset.fill === 'quantities' ? el.value.split(/[^0-9]+/).filter(Boolean).map(Number) : el.value;
        });
        return run({ action: 'quote.edit', quote_id: q.id, changes: { items: [ch] } }, q.status === 'approved' ? 'Recalculated. The approval was withdrawn: approve again.' : 'Recalculated.');
      }
    }));
  }

  // ---- Follow-ups ---------------------------------------------------------------------------
  async function followups() {
    const d = await get('api/quotes/followups');
    const card = (f, due) => `<div class="qd-card"><h2>${esc(f.customer)}: quote ${esc(f.number)} (${usd(f.value)})</h2>
      <p class="qd-sub">Sent ${esc(day(f.sent_at.slice(0, 10)))} · follow-up ${f.n} · due ${esc(day(f.due_on))} · <a href="quotes#rfq/${esc(f.rfq_id)}">open the RFQ</a></p>
      <textarea rows="6" data-text="${esc(f.id)}">${esc(f.text)}</textarea>
      <div class="qd-actions">${due ? `<button class="primary" data-fu="${esc(f.id)}">Approve and send</button>` : '<button disabled>Not due yet</button>'}</div></div>`;
    main.innerHTML = `<h1>Follow-ups</h1><p class="qd-sub">Every open quote gets a follow-up on day 3 and day 7, drafted here. Nothing goes out until you click.</p>
      <h2>Due now (${d.due.length})</h2>${d.due.map((f) => card(f, true)).join('') || '<p>Nothing due today.</p>'}
      <h2>Coming up (${d.upcoming.length})</h2>${d.upcoming.map((f) => card(f, false)).join('') || '<p>Nothing scheduled.</p>'}`;
    main.querySelectorAll('[data-fu]').forEach((b) => b.addEventListener('click', async () => {
      try {
        await act({ action: 'followup.send', followup_id: b.dataset.fu, text: main.querySelector(`[data-text="${b.dataset.fu}"]`).value });
        await followups(); flash('Follow-up sent and logged.');
      } catch (e) { flash(e.message, true); }
    }));
  }

  // ---- Quote log ----------------------------------------------------------------------------
  async function log() {
    const d = await get('api/quotes/log');
    main.innerHTML = `<h1>Quote log</h1><p class="qd-sub">Every quote: when it came in, when it went out, what it was worth, and how it ended. Rows marked "log" came from the shop's old quote log.</p>
      <p><a class="qd-export" href="api/quotes/export.csv" download>Download approved quotes (CSV)</a> <small class="qd-muted">One row per quantity break, for Excel, JobBOSS, E2, Paperless Parts or QuickBooks. The desk works beside your tools; it does not replace them.</small></p>
      <div class="qd-card"><table class="qd-table"><thead><tr><th>Quote</th><th>Customer</th><th>In</th><th>Out</th><th class="num">Hours</th><th class="num">Value</th><th>Follow-ups</th><th>Status</th></tr></thead><tbody>
      ${d.quotes.map((q) => { const h = q.sent_at ? (new Date(q.sent_at) - new Date(q.received_at)) / 36e5 : null; return `<tr${q.source === 'log' ? '' : ` data-go="rfq/${esc(q.rfq_id)}" tabindex="0"`}>
        <td>${esc(q.number)}${q.source === 'log' ? ' <small class="qd-muted">log</small>' : ''}</td><td>${esc(q.customer)}</td><td>${esc(when(q.received_at))}</td><td>${esc(when(q.sent_at))}</td>
        <td class="num">${h == null ? '' : `<span class="${h <= 24 ? 'qd-good' : 'qd-slow'}">${h.toFixed(0)}</span>`}</td><td class="num">${usd(q.value)}</td><td>${q.followups || ''}</td>
        <td>${tag(q.status)} ${q.reason && q.status !== 'won' ? `<small>${esc(q.reason)}</small>` : ''}</td></tr>`; }).join('')}</tbody></table></div>`;
  }

  // ---- Numbers ------------------------------------------------------------------------------
  async function numbers() {
    const k = await get('api/quotes/kpis?weeks=4');
    const pct = (v) => v == null ? '–' : v + '%';
    const max = Math.max(1, ...k.weekly.map((w) => w.sent));
    main.innerHTML = `<h1>Numbers</h1><p class="qd-sub">The last ${k.window_weeks} weeks, from the quote log. ${k.rfqs} RFQs in, ${k.sent} quotes out.</p>
      <div class="qd-stats">
        ${stat('Time to quote', k.time_to_quote_hours == null ? '–' : k.time_to_quote_hours + ' h', 'Median, RFQ in to quote out')}
        ${stat('Quotes per week', k.quotes_per_week, 'Quotes that left the shop')}
        ${stat('Same-day rate', pct(k.same_day_rate), 'Sent within 24 hours')}
        ${stat('Follow-up rate', pct(k.follow_up_rate), 'Open quotes followed up')}
        ${stat('Win rate', pct(k.win_rate), `${pct(k.win_rate_decided)} of decided · ${pct(k.win_rate_dollars)} by dollars`)}
        ${stat('RFQs never answered', k.never_answered, 'No quote after 48 hours')}
      </div>
      <div class="qd-card"><h2>Quotes sent per week</h2><div class="qd-bars">${k.weekly.map((w) => `<div class="qd-bar"><i style="height:${Math.round(100 * w.sent / max)}%"></i><b>${w.sent}</b><small>wk of ${esc(day(w.week_of))}</small><small>${w.same_day == null ? '' : w.same_day + '% same day'}</small></div>`).join('')}</div></div>
      <div class="qd-card"><p class="qd-sub">Won ${usd(k.won_value)} of ${usd(k.sent_value)} quoted. ${k.itar_stopped} ITAR RFQ(s) stopped and handled by hand. Buyers expect a quote inside 24 hours, so same-day rate is the number to move first.</p></div>`;
  }

  // ---- Paste an RFQ -------------------------------------------------------------------------
  function paste() {
    main.innerHTML = `<h1>Paste an RFQ</h1><p class="qd-sub">Try the desk on any request. It is read the same way as the inbox: ITAR check first, then the reader, then the 3 most similar past jobs and a draft.</p>
      <form class="qd-card qd-form" id="qd-paste"><label>From <input name="sender" value="Sam Buyer <buyer@newcustomer.example>" required></label>
      <label>Subject <input name="subject" value="RFQ: bracket"></label>
      <label>Email <textarea name="body" rows="12" required>Please quote:\n\nPart number: NC-100 Rev A\nDescription: Mounting bracket\nMaterial: 6061-T6\nSize 3.00 x 2.00 x 0.50\nTolerances +/- .002\nFinish: clear anodize Type II\nQty 25 / 100\nNeed by 12/12</textarea></label>
      <div class="qd-actions"><button class="primary">Read it</button></div></form>`;
    document.getElementById('qd-paste').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const f = new FormData(ev.target);
      try {
        const res = await act({ action: 'rfq.add', sender: f.get('sender'), subject: f.get('subject'), body: f.get('body') });
        location.hash = 'rfq/' + res.id;
      } catch (e) { flash(e.message, true); }
    });
  }

  async function route() {
    const h = location.hash.replace(/^#/, '') || 'inbox';
    const [tab, id] = h.split('/');
    document.querySelectorAll('.qd-top nav a').forEach((a) => a.toggleAttribute('aria-current', a.dataset.tab === (tab === 'rfq' ? 'inbox' : tab)));
    document.querySelectorAll('.qd-top nav a[aria-current]').forEach((a) => a.setAttribute('aria-current', 'page'));
    try {
      if (tab === 'rfq') await rfq(id);
      else if (tab === 'followups') await followups();
      else if (tab === 'log') await log();
      else if (tab === 'numbers') await numbers();
      else if (tab === 'new') paste();
      else await inbox();
    } catch (e) { main.innerHTML = `<p class="qd-flash bad" role="alert">${esc(e.message)}</p>`; }
    main.querySelectorAll('[data-go]').forEach((tr) => {
      const go = () => { location.hash = tr.dataset.go; };
      tr.addEventListener('click', go);
      tr.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
    });
  }
  window.addEventListener('hashchange', () => { route(); window.scrollTo(0, 0); });
  route();
})();
