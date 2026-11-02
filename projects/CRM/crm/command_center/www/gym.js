// Gym screens: Today, Members, a member, Schedule, a class roster, Billing, the switch guide, Import and export.
// Reads come from /api/gym/*; every write is a command on /api/crm/command (Store.execute).
(() => {
  'use strict';
  const main = document.getElementById('gym');
  const dialog = document.getElementById('gym-dialog');
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : Date.now() + '-' + Math.random().toString(16).slice(2));
  let ranks = [];

  async function get(path) {
    const r = await fetch(path, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || 'Could not load (' + r.status + ')');
    return body;
  }
  async function command(action, data, requestId) {
    const r = await fetch('api/crm/command', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, data, request_id: requestId || uuid() }) });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || 'Could not save (' + r.status + ')');
    return body;
  }
  const beltDot = (systemId, beltId) => {
    const s = ranks.find(x => x.id === systemId) || ranks.find(x => x.belts.some(b => b.id === beltId));
    const b = s && s.belts.find(x => x.id === beltId);
    const c = b && /^#[0-9a-fA-F]{6}$/.test(b.color || '') ? b.color : '';
    return beltId ? `<span class="gym-belt" style="${c ? '--belt:' + c : ''}" aria-hidden="true"></span>` : '';
  };
  const list = (items, row, empty) => items.length ? `<ul class="gym-list">${items.map(row).join('')}</ul>` : `<p class="gym-empty">${esc(empty)}</p>`;
  const memberLink = (id, name) => `<a href="gym#member/${encodeURIComponent(id)}">${esc(name)}</a>`;
  const dot = w => w === null || w === undefined ? ''
    : w ? `<span class="gym-dot ok" title="Waiver signed ${esc(String(w).slice(0, 10))}" role="img" aria-label="Waiver signed"></span>`
        : '<span class="gym-dot no" title="No waiver yet" role="img" aria-label="No waiver yet"></span>';
  const named = (id, name, w) => `<span class="gym-who">${dot(w)}${memberLink(id, name)}</span>`;  // one item in a list row
  // Dates the way people say them: "Sep 18", "Tue Oct 27"; the year only when it is not this year.
  const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const ymd = s => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s || ''); return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null; };
  const when = (s, weekday) => {
    const d = ymd(s);
    if (!d) return esc(s);
    return (weekday ? DOW[d.getDay()] + ' ' : '') + MON[d.getMonth()] + ' ' + d.getDate() + (d.getFullYear() !== new Date().getFullYear() ? ', ' + d.getFullYear() : '');
  };
  const day = s => when(s, true);
  // What a class shows: only the numbers that say something ("5 attended", never "0 booked"). With
  // bookings, who attended splits into booked and walk-ins, so "14 attended, 13 booked" never reads wrong.
  const counts = c => {
    const walk = c.came - (c.booked_came || 0), plural = n => n === 1 ? '' : 's';
    if (!c.booked) return c.came ? c.came + ' attended' : '';
    if (!c.came) return c.booked + ' booked' + (c.cap ? ' of ' + c.cap : '');
    return [c.came + ' attended', (c.booked_came || 0) + ' of ' + c.booked + ' booked', walk ? walk + ' walk-in' + plural(walk) : ''].filter(Boolean).join(' &middot; ');
  };
  const time12 = hm => { const [h, m] = String(hm || '').split(':').map(Number); return isNaN(h) ? esc(hm) : ((h % 12) || 12) + ':' + String(m || 0).padStart(2, '0') + (h < 12 ? ' AM' : ' PM'); };
  const tel = v => String(v || '').replace(/[^0-9+]/g, '');
  // A phone you can tap to call or text, an email you can tap to write.
  const reach = (phone, email) => [phone ? `<a href="tel:${esc(tel(phone))}">${esc(phone)}</a> <a href="sms:${esc(tel(phone))}">Text</a>` : '',
    email ? `<a href="mailto:${esc(email)}">${esc(email)}</a>` : ''].filter(Boolean).join(' &middot; ');
  const METHOD = { kiosk: 'at the tablet', roster: 'marked by a coach' };
  const SKELETON = '<div class="gym-skel" aria-hidden="true"><i class="h"></i><i class="row"></i><i class="row"></i><i class="card"></i><i class="card"></i></div><p class="gym-sr">Loading</p>';

  // ---------- a small form dialog ----------
  function form(title, fields, onSave) {
    document.getElementById('gym-dialog-title').textContent = title;
    document.getElementById('gym-error').textContent = '';
    document.getElementById('gym-fields').innerHTML = fields.map(f => {
      const id = 'f-' + f.name;
      let input;
      if (f.options) input = `<select id="${id}" name="${f.name}">${f.options.map(o => `<option value="${esc(o.value)}"${String(o.value) === String(f.value ?? '') ? ' selected' : ''}>${esc(o.label)}</option>`).join('')}</select>`;
      else input = `<input id="${id}" name="${f.name}" type="${f.type || 'text'}" value="${esc(f.value ?? '')}"${f.required ? ' required' : ''}${f.min !== undefined ? ` min="${f.min}"` : ''}${f.max !== undefined ? ` max="${f.max}"` : ''}>`;
      return `<div class="gym-field"><label for="${id}">${esc(f.label)}</label>${input}</div>`;
    }).join('');
    const save = document.getElementById('gym-save');
    const requestId = uuid();  // one id per opened form: a retried save replays, never doubles
    save.onclick = async ev => {
      ev.preventDefault();
      const values = Object.fromEntries(new FormData(document.getElementById('gym-form')));
      save.disabled = true;
      try { await onSave(values, requestId); dialog.close(); route(); }
      catch (e) { document.getElementById('gym-error').textContent = e.message; }
      finally { save.disabled = false; }
    };
    document.getElementById('gym-cancel').onclick = () => dialog.close();
    dialog.showModal();
  }

  // ---------- Today ----------
  // Status has its own colour: red only for money that failed, amber for not seen, green for a
  // stripe due, dark for the class on the mat. Every number is a door to its list.
  const SHOWN = 8;
  const LEGEND = '<p class="gym-legend"><span class="gym-dot ok"></span>waiver signed <span class="gym-dot no"></span>no waiver yet</p>';
  async function today() {
    const t = await get('api/gym/today');
    const cls = t.classes.map(s => `<div class="gym-class ${s.canceled ? 'canceled' : ''}"><span class="t">${esc(s.time)}</span>
      <span class="n"><a href="gym#roster/${encodeURIComponent(s.id)}">${esc(s.name)}</a>${s.instructor || s.room ? `<br><span class="gym-muted">${esc(s.instructor)}${s.instructor && s.room ? ' &middot; ' : ''}${esc(s.room)}</span>` : ''}</span>
      <span class="c"><span class="gym-pill ${s.state}">${s.state === 'now' ? 'On the mat now' : s.state === 'next' ? 'Next' : s.state === 'done' ? 'Done' : s.state === 'canceled' ? 'Canceled' : 'Later'}</span>
      <span class="gym-muted">${counts(s)}</span></span></div>`).join('');
    const f = t.focus;
    // One roster for the class: who attended (a check mark) and who booked but is not here yet.
    const came = new Set(f ? f.came_names.map(n => n.id) : []);
    const who = f ? [...f.came_names, ...f.booked_names.filter(n => !came.has(n.id))] : [];
    const focus = f ? `<div class="gym-card" id="today-focus"><h2>${f.state === 'now' ? 'On the mat now' : 'Next class'}: ${esc(f.name)}, ${esc(f.time)}</h2>
      <p class="gym-muted">${came.size} attended${who.length > came.size ? ` &middot; ${who.length - came.size} booked, not here yet` : ''}</p>
      ${list(who, (n, i) => `<li${i >= SHOWN ? ' hidden' : ''}>${named(n.id, n.name, n.waiver)}${came.has(n.id) ? '<span class="gym-came">&#10003; Attended</span>' : '<span class="gym-muted">Booked</span>'}</li>`, 'Nobody booked or checked in yet.')}
      ${who.length > SHOWN ? `<button type="button" class="gym-quiet" data-more>Show all ${who.length}</button>` : ''}
      <p><a class="gym-btn" href="gym#roster/${encodeURIComponent(f.id)}">Open the roster</a></p></div>` : '';
    const failed = t.billing.connected ? t.billing.failed : [];
    main.innerHTML = `<h1>Today</h1><p class="gym-sub"><span>${day(t.date)} &middot; ${esc(t.now)}</span> <a class="gym-print" href="gym/print?date=${esc(t.date)}" target="_blank" rel="noopener">Print the paper roster</a></p>
      <div class="gym-stats" id="gym-stats">
        ${t.billing.connected ? `<a class="gym-stat ${failed.length ? 'bad' : ''}" href="gym#billing"><b>${failed.length}</b><span>failed payments</span></a>`
          : '<div class="gym-stat off"><b>Billing not connected</b><span>failed payments show here later</span></div>'}
        <a class="gym-stat ${t.not_seen.length ? 'warn' : ''}" href="gym#today" data-jump="today-quiet"><b>${t.not_seen.length}</b><span>not seen in ${t.not_seen_days} days</span></a>
        ${t.has_ranks === false ? '' : `<a class="gym-stat ${t.due.length ? 'good' : ''}" href="gym#today" data-jump="today-due"><b>${t.due.length}</b><span>due a stripe or belt review</span></a>`}
        <a class="gym-stat" href="gym#today" data-jump="today-came"><b>${t.came.length}</b><span>attended today</span></a>
        <a class="gym-stat" href="gym#members"><b>${t.active_members}</b><span>active members${t.trials ? ` &middot; ${t.trials} on trial` : ''}</span></a>
      </div>
      <div class="gym-grid">
        <div class="gym-card ${failed.length ? 'alert' : ''}" id="today-failed"><h2>Failed payments ${t.billing.connected === 'demo' ? '<span class="gym-demo-tag">Demo</span>' : t.billing.connected === 'stripe' ? '<span class="gym-demo-tag">Test mode</span>' : ''}</h2>
          ${t.billing.connected ? failedList(failed) : `<p class="gym-banner">${esc(t.billing.text)}</p>`}</div>
        <div class="gym-card" id="today-quiet"><h2>Not seen in ${t.not_seen_days} days</h2>${list(t.not_seen, q => `<li><span>${memberLink(q.id, q.name)}<br><span class="gym-muted">${q.last_visit ? 'last attended ' + when(q.last_visit) : 'never checked in'}${q.phone ? ' &middot; ' + esc(q.phone) : ''}</span></span>${q.days === null ? '' : `<span class="gym-pill warn">${q.days} days</span>`}</li>`, 'Everyone on a plan has been in recently.')}</div>
        ${focus}
        ${t.has_ranks === false ? '' : `<div class="gym-card" id="today-due"><h2>Due a stripe</h2>${list(t.due, d => `<li><span>${memberLink(d.id, d.name)}<br><span class="gym-muted">${esc(d.rank)} &middot; ${d.classes} classes since (needs ${d.threshold})</span></span><span class="gym-pill good">${esc(d.what)}</span></li>`, 'Nobody is due right now.')}</div>`}
        <div class="gym-card" id="today-classes"><h2>Classes today</h2>${cls || '<p class="gym-empty">No classes on the schedule today.</p>'}</div>
        <div class="gym-card wide" id="today-came"><h2>Who attended today</h2>${t.came.some(c => c.waiver !== null && c.waiver !== undefined) ? LEGEND : ''}${list(t.came, c => `<li><span>${named(c.contact_id, c.name, c.waiver)}<br><span class="gym-muted">${esc(c.class)} &middot; ${esc(c.time)} &middot; ${esc(METHOD[c.method] || c.method)}</span></span></li>`, 'Nobody has checked in yet today.')}</div>
      </div>`;
    main.querySelectorAll('[data-jump]').forEach(a => a.onclick = ev => {
      ev.preventDefault();
      const el = document.getElementById(a.dataset.jump);
      if (el) el.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
    });
    const more = main.querySelector('[data-more]');
    if (more) more.onclick = () => { more.closest('.gym-card').querySelectorAll('li[hidden]').forEach(li => li.hidden = false); more.remove(); };
    wireCardLinks();
  }

  // ---------- Billing (simulated in a demo; Stripe test mode in P2) ----------
  // A Stripe row (it has an attempt count) never offers "mark card updated": Stripe says when it paid.
  const failedList = items => list(items, f => `<li><span>${memberLink(f.id, f.name)}<br><span class="gym-muted">${f.amount_cents ? '<b>' + money(f.amount_cents) + '</b> &middot; ' : ''}${esc(f.reason)} &middot; ${when(f.failed_on)}${f.attempt ? ' &middot; try ' + f.attempt : ''}${f.plan ? ' &middot; ' + esc(f.plan) : ''}</span></span>
    <span>${f.attempt !== undefined
      ? `${f.link_sent ? '<span class="gym-pill ok">Link ready</span> ' : ''}<button class="${f.link_sent ? '' : 'primary'}" data-link="${esc(f.id)}">${f.link_sent ? 'Copy again' : 'Copy card link'}</button>`
      : f.link_sent ? `<span class="gym-pill ok">Link sent</span> <button data-paid="${esc(f.id)}">Mark card updated</button>` : `<button class="primary" data-link="${esc(f.id)}">Send card link</button>`}</span></li>`, 'No failed payments.');
  function wireCardLinks() {
    main.querySelectorAll('[data-link],[data-paid]').forEach(b => b.onclick = async () => {
      const id = b.dataset.link || b.dataset.paid;
      b.disabled = true;
      try {
        const r = await post('api/gym/billing', { action: b.dataset.link ? 'link' : 'paid', contact_id: id });
        if (r.message) {
          const li = b.closest('li') || b.parentElement;
          li.insertAdjacentHTML('afterend', `<li class="gym-sent"><div><p class="gym-muted">Text the member gets (${esc(r.note)})</p><div class="gym-sms">${esc(r.message)}</div>${r.url ? '<button type="button" class="primary" data-copy>Copy text</button>' : ''}</div></li>`);
          const copy = li.nextElementSibling.querySelector('[data-copy]');
          // Its own tap: a clipboard write waits on the browser and must never hold the screen.
          if (copy) copy.onclick = () => (navigator.clipboard ? navigator.clipboard.writeText(r.message) : Promise.reject())
            .then(() => { copy.textContent = 'Copied'; }, () => { getSelection().selectAllChildren(copy.previousElementSibling); copy.textContent = 'Selected: copy it'; });
          b.outerHTML = b.parentElement.querySelector('.gym-pill') ? '' : `<span class="gym-pill ok">${r.url ? 'Link ready' : 'Link sent'}</span>`;
        } else route();
      } catch (e) { alert(e.message); b.disabled = false; }
    });
  }
  async function post(path, body) {
    const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const out = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(out.error || 'Could not save (' + r.status + ')');
    return out;
  }
  const money = c => '$' + (c / 100).toLocaleString('en-US', { minimumFractionDigits: c % 100 ? 2 : 0, maximumFractionDigits: 2 });
  const EVERY = { month: 'a month', year: 'a year', once: 'once', none: 'no billing' };
  function stripeBilling(b) {
    const plans = list(b.plans, p => `<li><span><b>${esc(p.name)}</b>${p.active ? '' : ' <span class="gym-muted">(not active)</span>'}<br><span class="gym-muted">${p.amount_cents ? money(p.amount_cents) + ' ' + EVERY[p.interval] : 'No price yet'}${p.priced ? ' &middot; on Stripe' : ''}</span></span>
      ${b.connected && b.can.manage && p.interval !== 'none' ? `<button data-price="${esc(p.id)}">${p.amount_cents ? 'Change price' : 'Set price'}</button>` : ''}</li>`, 'No plans yet.');
    const check = b.money_check;
    main.innerHTML = `<h1>Billing <span class="gym-demo-tag">Test mode</span></h1>
      <p class="gym-sub">Stripe holds every card in your own Stripe account and runs every charge. Test mode: Stripe's test cards only, no real money moves.</p>
      ${b.connected ? '' : `<div class="gym-card alert"><h2>Connect your Stripe account</h2><p>Billing runs on your own Stripe account. Connect it once; your members' cards stay there.</p>
        ${b.can_connect && b.can.manage ? '<p><a class="gym-btn primary" href="gym/billing/connect">Connect Stripe</a></p>' : '<p class="gym-muted">An owner connects it, once the platform settings are in place.</p>'}</div>`}
      <div class="gym-stats">
        <div class="gym-stat"><b>${b.members}</b><span>members on a plan</span></div>
        <div class="gym-stat"><b>${money(b.last_30_days.paid_cents)}</b><span>paid in the last 30 days</span></div>
        <div class="gym-stat ${b.failed.length ? 'bad' : ''}"><b>${b.failed.length}</b><span>failed payments</span></div>
        <div class="gym-stat ${check && !check.equal ? 'bad' : ''}"><b>${check ? (check.equal ? 'Matches' : check.gaps + ' to check') : 'Not run'}</b><span>money check${check ? ' for ' + when(check.day) : ''}</span></div>
      </div>
      <div class="gym-grid">
        <div class="gym-card ${b.failed.length ? 'alert' : ''}"><h2>Failed payments</h2><p class="gym-muted">Copy the member's card link: it opens a secure Stripe page to update the card, and Stripe retries the charge. Nothing is sent from here yet.</p>${failedList(b.failed)}</div>
        <div class="gym-card"><h2>Plans and prices</h2>${plans}${b.connected ? '<p class="gym-muted">Your join page: <a href="join" target="_blank" rel="noopener">open it</a>. A new price applies to new members; members keep the price they joined on.</p>' : ''}</div>
        <div class="gym-card"><h2>Money check</h2><p class="gym-muted">Every night, every Stripe payment is compared with every record here. A gap is shown, never fixed by itself.</p>
          <div id="gym-check">${check ? `<p>${when(check.day)}: ${check.equal ? '<span class="gym-pill ok">Stripe matches</span>' : `<span class="gym-pill bad">${check.gaps} to check</span>`}</p>` : ''}</div>
          ${b.connected && b.can.manage ? '<button data-check>Check yesterday now</button>' : ''}</div>
      </div>`;
    main.querySelectorAll('[data-price]').forEach(btn => btn.onclick = () => {
      const p = b.plans.find(x => x.id === btn.dataset.price);
      form(`Price for ${p.name}`, [{ name: 'amount', label: `Amount in dollars, ${EVERY[p.interval]}`, type: 'number', min: 1, value: p.amount_cents ? p.amount_cents / 100 : '', required: true }],
        (v, rid) => command('gym.plan.save', { id: p.id, revision: p.revision, name: p.name, interval: p.interval, amount_cents: Math.round(Number(v.amount) * 100) }, rid));
    });
    const run = main.querySelector('[data-check]');
    if (run) run.onclick = async () => {
      run.disabled = true;
      try {
        const r = await post('api/gym/billing', { action: 'check' });
        document.getElementById('gym-check').innerHTML = `<p>${when(r.day)}: ${r.equal ? '<span class="gym-pill ok">Stripe matches</span>' : `<span class="gym-pill bad">${r.gaps.length} to check</span>`}
          <br><span class="gym-muted">Stripe ${r.stripe.paid} paid, ${money(r.stripe.paid_cents)} &middot; here ${r.ours.paid} paid, ${money(r.ours.paid_cents)}</span></p>`;
      } catch (e) { alert(e.message); }
      run.disabled = false;
    };
    wireCardLinks();
  }
  async function billing() {
    const b = await get('api/gym/billing');
    if (b.stripe) return stripeBilling(b);
    main.innerHTML = `<h1>Billing ${b.demo ? '<span class="gym-demo-tag">Demo</span>' : ''}</h1>
      <p class="gym-sub">${b.demo ? 'Simulated with made-up members: no real cards, no charges, nothing is sent. With billing on, Stripe holds the cards in your own Stripe account and runs every charge.' : ''}</p>
      <div class="gym-stats three">
        <div class="gym-stat"><b>${money(b.monthly_cents || 0)}</b><span>coming in each month</span></div>
        <div class="gym-stat ${b.failed.length ? 'bad' : ''}"><b>${money(b.at_risk_cents || 0)}</b><span>owed from ${b.failed.length} failed payment${b.failed.length === 1 ? '' : 's'}</span></div>
        <div class="gym-stat"><b>${b.paid}</b><span>of ${b.members} members paid up</span></div>
      </div>
      <div class="gym-grid">
        <div class="gym-card ${b.failed.length ? 'alert' : ''}"><h2>Failed payments</h2><p class="gym-muted">One tap sends the member a link to a secure Stripe page to update their card. Stripe retries the charge.</p>${failedList(b.failed)}</div>
        <div class="gym-card"><h2>Billing next</h2>${list(b.upcoming, u => `<li><span>${memberLink(u.id, u.name)}<br><span class="gym-muted">${esc(u.plan)}${u.amount_cents ? ' &middot; ' + money(u.amount_cents) : ''}</span></span><span class="gym-pill">${when(u.next_on)}</span></li>`, 'Nothing due soon.')}</div>
        ${b.plans && b.plans.length ? `<div class="gym-card"><h2>Plans ${b.demo ? '<span class="gym-demo-tag">Sample prices</span>' : ''}</h2>
          ${b.demo ? '<p class="gym-muted">Made-up prices for the demo. You set your own.</p>' : ''}
          ${list(b.plans, p => `<li><span><b>${esc(p.name)}</b><br><span class="gym-muted">${p.members} member${p.members === 1 ? '' : 's'}</span></span><span>${money(p.amount_cents)} ${EVERY[p.interval] || ''}</span></li>`, '')}</div>` : ''}
      </div>`;
    wireCardLinks();
  }

  // ---------- Your switch: from the gym's current tool, step by step ----------
  let cardPath = 0;
  async function switchGuide() {
    const g = await get('api/gym/switch');
    const cards = g.cards || { paths: [] };
    const step = st => st.kind === 'cards' ? `${esc(st.text)}</p>
        <div class="gym-card" style="margin:8px 0"><h3>${esc(cards.title)}</h3>
        ${cards.paths.length > 1 ? `<div class="gym-choice" role="group" aria-label="${esc(cards.title)}">${cards.paths.map((c, i) => `<button type="button" data-path="${i}" aria-pressed="${i === cardPath}">${esc(c.label)}</button>`).join('')}</div>` : ''}
        <p id="gym-card-path">${esc((cards.paths[cardPath] || cards.paths[0] || {}).text)}</p></div><p>`
      : st.kind === 'import' ? `${esc(st.text)}</p><div class="gym-card" style="margin:8px 0"><h3>Sample import</h3>
        <p class="gym-muted">${esc(g.export.note)}</p>
        <p><button class="primary" id="gym-sample-run">Run a sample import</button> <a href="api/gym/switch/sample.csv" download>Download the sample file</a></p>
        <div id="gym-sample"></div></div><p>` : esc(st.text);
    main.innerHTML = `<h1>Moving from ${esc(g.from_label)}</h1><p class="gym-sub">${esc(g.intro)}</p>
      <div class="gym-card"><ol class="gym-steps">${g.steps.map(st => `<li><h3>${esc(st.title)}</h3><p class="who">${esc(st.who)}</p><p>${step(st)}</p></li>`).join('')}</ol></div>`;
    main.querySelectorAll('[data-path]').forEach(b => b.onclick = () => {
      cardPath = Number(b.dataset.path);
      main.querySelectorAll('[data-path]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
      document.getElementById('gym-card-path').textContent = cards.paths[cardPath].text;
    });
    const run = document.getElementById('gym-sample-run');
    run.onclick = async () => {
      const out = document.getElementById('gym-sample');
      run.disabled = true;
      try {
        const csv = await (await fetch('api/gym/switch/sample.csv', { cache: 'no-store' })).text();
        const lines = csv.trim().split(/\r?\n/).map(l => l.split(','));
        const b64 = btoa(unescape(encodeURIComponent(csv)));
        const p = await post('api/gym/import', { step: 'preview', file: b64 });
        out.innerHTML = `<p class="gym-muted">The file, as ${esc(g.from_label)} would export it (made-up people):</p>
          <div class="gym-scroll"><table class="gym-table"><thead><tr>${lines[0].map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead>
          <tbody>${lines.slice(1, 4).map(r => `<tr>${r.map(c => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
          <p class="gym-muted">${lines.length - 1} rows in the file, 3 shown.</p>
          <p><b>${p.new}</b> new members &middot; <b>${p.duplicates.length}</b> already here (skipped) &middot; <b>${p.problems.length}</b> rows with a problem</p>
          <p class="gym-muted">Columns matched: ${p.columns.map(c => esc(c.column)).join(', ')}</p>
          ${p.new ? `<button class="primary" id="gym-sample-save">Import ${p.new} members</button>` : '<p class="gym-banner">Already imported: running it again changes nothing.</p>'}`;
        const save = document.getElementById('gym-sample-save');
        if (save) save.onclick = async () => {
          save.disabled = true;
          const r = await post('api/gym/import', { step: 'save', file: b64 });
          save.outerHTML = `<p class="gym-banner"><b>${r.created}</b> members imported with their plans and belts. Find them under <a href="gym#members">Members</a>. Run it again: nothing doubles.</p>`;
        };
      } catch (e) { out.innerHTML = `<p class="gym-banner">${esc(e.message)}</p>`; }
      finally { run.disabled = false; }
    };
  }

  // ---------- Members ----------
  const FLAG = { card_failed: '<span class="gym-pill bad">Card failed</span>', due: '<span class="gym-pill good">Due a stripe</span>' };
  let memberFilter = { q: '', status: 'active' };
  async function members() {
    const m = await get('api/gym/members');
    ranks = m.ranks;
    const draw = () => {
      const q = memberFilter.q.trim().toLowerCase();
      const rows = m.members.filter(x => (!q || x.name.toLowerCase().includes(q) || (x.phone || '').includes(q) || (x.email || '').toLowerCase().includes(q))
        && (memberFilter.status === 'all' || x.status === memberFilter.status || (memberFilter.status === 'active' && x.status === 'trial')));
      // A tag only when something needs a look: a failed card, a stripe due, a plan that is not active.
      const tags = x => [...(x.flags || []).map(f => FLAG[f] || ''),
        x.status !== 'active' ? `<span class="gym-pill">${x.status === 'none' ? 'no plan' : esc(x.status)}</span>` : ''].join(' ').trim();
      document.getElementById('gym-rows').innerHTML = rows.map(x => `<tr><td>${named(x.id, x.name, x.waiver)}<br><span class="gym-muted">${esc(x.phone)}</span>${tags(x) ? `<div class="gym-tags">${tags(x)}</div>` : ''}</td>
        ${m.ranks.length ? `<td>${beltDot('', x.belt)}${esc(x.rank)}</td>` : ''}<td class="hide-sm">${esc(x.plan) || '<span class="gym-muted">No plan</span>'}</td>
        <td class="hide-sm">${x.last_visit ? when(x.last_visit) : '<span class="gym-muted">never</span>'}</td></tr>`).join('')
        || '<tr><td colspan="4" class="gym-empty">No members match.</td></tr>';
      document.getElementById('gym-count').textContent = rows.length + ' shown';
    };
    main.innerHTML = `<h1>Members</h1><p class="gym-sub"><span id="gym-count"></span></p>
      <div class="gym-toolbar"><input id="gym-q" type="search" placeholder="Search name, phone or email" aria-label="Search members" value="${esc(memberFilter.q)}">
      <select id="gym-status" aria-label="Membership status">${['active', 'paused', 'canceled', 'none', 'all'].map(s => `<option value="${s}"${s === memberFilter.status ? ' selected' : ''}>${s === 'active' ? 'Active and trial' : s === 'none' ? 'No plan' : s[0].toUpperCase() + s.slice(1)}</option>`).join('')}</select>
      ${m.can.manage ? '<button class="primary" id="gym-add">Add member</button>' : ''}</div>
      <div class="gym-card"><table class="gym-table gym-members"><thead><tr><th>Name</th>${m.ranks.length ? '<th>Rank</th>' : ''}<th class="hide-sm">Plan</th><th class="hide-sm">Last visit</th></tr></thead><tbody id="gym-rows"></tbody></table></div>`;
    document.getElementById('gym-q').oninput = e => { memberFilter.q = e.target.value; draw(); };
    document.getElementById('gym-status').onchange = e => { memberFilter.status = e.target.value; draw(); };
    const add = document.getElementById('gym-add');
    if (add) add.onclick = () => addMember(m);
    draw();
  }

  function addMember(m) {
    const today = m.today;
    form('Add member', [
      { name: 'name', label: 'Name', required: true }, { name: 'phone', label: 'Phone', type: 'tel' },
      { name: 'email', label: 'Email', type: 'email' }, { name: 'birth_date', label: 'Birth date', type: 'date' },
      { name: 'program', label: 'Program', options: [{ value: '', label: 'Not set' }, ...m.programs.map(p => ({ value: p.id, label: p.label || p.id }))] },
      { name: 'plan_id', label: 'Plan', options: [{ value: '', label: 'No plan yet' }, ...m.plans.filter(p => p.active).map(p => ({ value: p.id, label: p.name }))] },
      { name: 'started_on', label: 'Start date', type: 'date', value: today },
    ], async (v, rid) => {
      const lines = [];
      if (v.phone) lines.push({ kind: 'phone', label: '', value: v.phone });
      if (v.email) lines.push({ kind: 'email', label: '', value: v.email });
      const c = await command('contact.create', { name: v.name, kind: 'person', channels: lines, lifecycle: v.plan_id ? 'customer' : 'lead' }, rid + ':c');
      await command('gym.member.save', { contact_id: c.id, revision: 0, birth_date: v.birth_date, joined_on: v.started_on, program: v.program }, rid + ':m');
      if (v.plan_id) await command('gym.membership.save', { contact_id: c.id, plan_id: v.plan_id, status: 'active', started_on: v.started_on }, rid + ':p');
      location.hash = 'member/' + c.id;
    });
  }

  // ---------- One member ----------
  const perWeek = (n, first, today) => (n / Math.max(1, ((ymd(today) - ymd(first)) / 864e5 + 1) / 7)).toFixed(1);
  // The last 12 months as small bars, so a slowdown shows at a glance.
  const months = ms => { const top = Math.max(1, ...ms.map(x => x.visits)); return `<div class="gym-months" role="img" aria-label="Classes per month, last 12 months">${ms.map(x =>
    `<div title="${MON[+x.month.slice(5) - 1]} ${x.month.slice(0, 4)}: ${x.visits}"><span class="n">${x.visits || ''}</span><i style="height:${Math.round(x.visits / top * 100)}%"></i><span class="l">${MON[+x.month.slice(5) - 1][0]}</span></div>`).join('')}</div>`; };
  const topOf = (sys, beltId) => ((sys && sys.belts.find(b => b.id === beltId)) || { stripes: 4 }).stripes;
  const VISITS = 30;
  async function member(id) {
    const d = await get('api/gym/member?id=' + encodeURIComponent(id));
    ranks = d.ranks;
    const m = d.member, cur = d.current;
    const live = d.memberships.find(x => x.status === 'active' || x.status === 'trial');
    main.innerHTML = `<p><a href="gym#members">&larr; Members</a></p><h1>${esc(m.name)}</h1>
      <p class="gym-sub">${reach(m.phone, m.email)}${document.body.dataset.demo ? '' : ` &middot; <a href="./?view=contacts&contact=${encodeURIComponent(m.id)}">Notes and timeline in the CRM</a>`}</p>
      <div class="gym-grid">
        ${d.ranks.length ? `<div class="gym-card"><h2>Rank</h2><p>${cur ? beltDot(cur.system_id, cur.belt) : ''}<b>${esc(d.rank)}</b></p>
          <p class="gym-muted">${cur ? `Since ${when(cur.awarded_on)} &middot; ${d.classes_since_award} classes since` : 'No rank recorded yet.'}</p>
          ${d.can.promote ? '<button class="primary" id="gym-promote">Promote</button>' : ''}
          <h2 style="margin-top:16px">History</h2>${list(d.awards, a => `<li><span>${beltDot(a.system_id, a.belt)}${esc(a.label)}<br><span class="gym-muted">${when(a.awarded_on)} &middot; ${esc(a.by)}${a.note ? ' &middot; ' + esc(a.note) : ''}</span></span></li>`, 'No promotions recorded.')}</div>` : ''}
        <div class="gym-card"><h2>Membership</h2>${list(d.memberships, x => `<li><span><b>${esc(x.plan)}</b><br><span class="gym-muted">from ${when(x.started_on)}${x.ends_on ? ' to ' + when(x.ends_on) : ''}</span></span><span class="gym-pill ${x.status === 'active' ? 'ok' : ''}">${esc(x.status)}</span>${d.can.manage ? ` <button data-ms="${esc(x.id)}">Change</button>` : ''}</li>`, 'Not on a plan.')}
          ${d.can.manage ? '<p><button id="gym-plan">Add a plan</button></p>' : ''}
          ${d.billing && d.billing.stripe ? `<h2 style="margin-top:16px">Billing <span class="gym-demo-tag">Test mode</span></h2>
            <p>${d.billing.status === 'failed' ? `<span class="gym-pill bad">Payment failed</span> <span class="gym-muted">${esc(d.billing.reason)} &middot; ${when(d.billing.failed_on)}</span> <a href="gym#billing">Copy the card link</a>`
              : d.billing.status === 'paid' ? `<span class="gym-pill ok">Paid</span> <span class="gym-muted">${when(d.billing.failed_on)}</span>` : '<span class="gym-muted">No payment yet</span>'}</p>
            ${list(d.billing.history, h => `<li><span>${esc({ paid: 'Paid', failed: 'Failed', refunded: 'Refunded' }[h.kind] || h.kind)} <span class="gym-muted">${when(h.occurred_at)}</span></span><span>${money(h.amount_cents)}</span></li>`, 'No payments yet.')}`
          : d.billing ? `<h2 style="margin-top:16px">Billing <span class="gym-demo-tag">Demo</span></h2>${d.billing.status === 'failed'
            ? `<p><span class="gym-pill bad">Payment failed</span> <span class="gym-muted">${esc(d.billing.reason)} &middot; ${when(d.billing.failed_on)}</span></p><p><a href="gym#billing">Send the card link</a></p>`
            : d.billing.classes_left !== null && d.billing.classes_left !== undefined  // a class pack never bills monthly
            ? `<p><span class="gym-pill ok">Paid</span> <span class="gym-muted">class pack &middot; ${d.billing.classes_left} class${d.billing.classes_left === 1 ? '' : 'es'} left</span></p>`
            : `<p><span class="gym-pill ok">Paid</span> <span class="gym-muted">next billing ${when(d.billing.next_on)}</span></p>`}`
            : '<p class="gym-muted">Billing is not connected yet. Amounts and cards come with billing.</p>'}</div>
        <div class="gym-card"><h2>Details</h2><dl class="gym-dl"><dt>Birth date</dt><dd>${m.birth_date ? when(m.birth_date) : '&ndash;'}</dd><dt>Joined</dt><dd>${m.joined_on ? when(m.joined_on) : '&ndash;'}</dd>
          <dt>Program</dt><dd>${esc((d.programs.find(p => p.id === m.program) || {}).label || m.program) || '&ndash;'}</dd>
          <dt>Emergency</dt><dd>${esc(m.emergency_name) || '&ndash;'} ${esc(m.emergency_phone)}</dd>
          <dt>Family</dt><dd>${d.family.map(f => memberLink(f.id, f.name)).join(', ') || '&ndash;'}</dd>
          ${d.waivers ? `<dt>Waiver</dt><dd>${d.waivers.length ? `${dot(d.waivers[0].signed_on)}Signed ${when(d.waivers[0].signed_on)}${d.waivers[0].guardian_name ? ' by ' + esc(d.waivers[0].guardian_name) : ''} &middot; version ${esc(d.waivers[0].version)}`
            : `${dot('')}Not signed yet &middot; <a href="gym#checkin">the waiver code</a>`}</dd>` : ''}</dl>
          ${d.can.manage ? '<p><button id="gym-edit">Edit details</button></p>' : ''}</div>
        <div class="gym-card" id="gym-attendance"><h2>Attendance</h2>
          <p><b>${m.visits}</b> classes since ${d.first_visit ? 'the first, ' + when(d.first_visit) : 'joining'}${d.first_visit ? ` &middot; ${perWeek(m.visits, d.first_visit, d.today)} a week` : ''} &middot; last ${m.last_visit ? when(m.last_visit) : 'never'}</p>
          ${months(d.months)}
          ${d.can.checkin && live && d.today_classes.length ? `<p class="gym-checkin-today">${d.today_classes.map(c => `<button data-class="${esc(c.id)}">Check in: ${esc(c.time)} ${esc(c.name)}</button>`).join(' ')}</p>` : ''}
          ${list(d.visits, (v, i) => `<li${i >= VISITS ? ' hidden' : ''}><span>${day(v.local_date)} &middot; ${esc(v.class)}</span><span class="gym-muted">${esc(METHOD[v.method] || v.method)}</span></li>`, 'No visits recorded.')}
          ${d.visits.length > VISITS ? `<button type="button" class="gym-quiet" data-more>Show all ${d.visits.length}</button>` : ''}</div>
      </div>`;
    const on = (sel, fn) => { const el = document.getElementById(sel); if (el) el.onclick = fn; };
    on('gym-promote', () => {
      const sys = cur ? d.ranks.find(s => s.id === cur.system_id) : d.ranks[0];
      // The next step: one more stripe, or, with every stripe on the belt, the next belt at 0.
      const ids = sys ? sys.belts.map(b => b.id) : [];
      const full = cur && cur.stripes >= topOf(sys, cur.belt) && ids.indexOf(cur.belt) + 1 < ids.length;
      const belt = cur ? (full ? ids[ids.indexOf(cur.belt) + 1] : cur.belt) : '';
      form('Promote ' + m.name, [
        { name: 'system_id', label: 'Program', options: d.ranks.map(s => ({ value: s.id, label: s.label })), value: sys && sys.id },
        { name: 'belt', label: 'Belt', options: ((sys || d.ranks[0]).belts).map(b => ({ value: b.id, label: b.label })), value: belt },
        { name: 'stripes', label: 'Stripes', type: 'number', min: 0, max: topOf(sys, belt), value: cur && !full ? Math.min(cur.stripes + 1, topOf(sys, belt)) : 0 },
        { name: 'awarded_on', label: 'Date', type: 'date', value: d.today }, { name: 'note', label: 'Note' },
      ], (v, rid) => command('gym.rank.award', { contact_id: m.id, system_id: v.system_id, belt: v.belt, stripes: Number(v.stripes), awarded_on: v.awarded_on, note: v.note }, rid));
      // The belt list follows the rank system: two ladders can share a belt id. The stripes follow the
      // belt: never more than it has (the profile says how many: 4 on most belts).
      const stripes = document.getElementById('f-stripes');
      const fit = () => {
        const s = d.ranks.find(x => x.id === document.getElementById('f-system_id').value);
        stripes.max = topOf(s, document.getElementById('f-belt').value);
        if (Number(stripes.value) > Number(stripes.max)) stripes.value = stripes.max;
        stripes.closest('.gym-field').querySelector('label').textContent = `Stripes (0 to ${stripes.max})`;
      };
      document.getElementById('f-system_id').onchange = e => {
        const s = d.ranks.find(x => x.id === e.target.value);
        document.getElementById('f-belt').innerHTML = s.belts.map(b => `<option value="${esc(b.id)}">${esc(b.label)}</option>`).join('');
        fit();
      };
      document.getElementById('f-belt').onchange = () => { stripes.value = 0; fit(); };  // a new belt starts at 0
      fit();
    });
    on('gym-edit', () => form('Edit details', [
      { name: 'birth_date', label: 'Birth date', type: 'date', value: m.birth_date }, { name: 'joined_on', label: 'Joined', type: 'date', value: m.joined_on },
      { name: 'program', label: 'Program', options: [{ value: '', label: 'Not set' }, ...d.programs.map(p => ({ value: p.id, label: p.label || p.id }))], value: m.program },
      { name: 'emergency_name', label: 'Emergency contact', value: m.emergency_name }, { name: 'emergency_phone', label: 'Emergency phone', type: 'tel', value: m.emergency_phone },
    ], (v, rid) => command('gym.member.save', { contact_id: m.id, revision: m.details_revision || 0, ...v }, rid)));
    on('gym-plan', () => form('Add a plan', [
      { name: 'plan_id', label: 'Plan', options: d.plans.filter(p => p.active).map(p => ({ value: p.id, label: p.name })) },
      { name: 'status', label: 'Status', options: ['active', 'trial'].map(s => ({ value: s, label: s })) },
      { name: 'started_on', label: 'Start date', type: 'date', value: d.today },
    ], (v, rid) => command('gym.membership.save', { contact_id: m.id, ...v }, rid)));
    main.querySelectorAll('[data-ms]').forEach(b => b.onclick = () => {
      const x = d.memberships.find(y => y.id === b.dataset.ms);
      form('Change ' + x.plan, [
        { name: 'status', label: 'Status', options: ['active', 'trial', 'paused', 'canceled'].map(s => ({ value: s, label: s })), value: x.status },
        { name: 'ends_on', label: 'End date', type: 'date', value: x.ends_on },
      ], (v, rid) => command('gym.membership.save', { id: x.id, revision: x.revision, ...v }, rid));
    });
    main.querySelectorAll('[data-class]').forEach(b => b.onclick = async () => {
      b.disabled = true;
      try { await command('gym.checkin.add', { session_id: b.dataset.class, contact_id: m.id, method: 'roster' }); route(); }
      catch (e) { alert(e.message); b.disabled = false; }
    });
    const more = main.querySelector('#gym-attendance [data-more]');
    if (more) more.onclick = () => { more.parentElement.querySelectorAll('li[hidden]').forEach(li => li.hidden = false); more.remove(); };
  }

  // ---------- Schedule ----------
  async function schedule(from) {
    const s = await get('api/gym/schedule' + (from ? '?from=' + encodeURIComponent(from) : ''));
    const shift = n => { const d = new Date(s.days[0].date + 'T12:00:00'); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };
    const coach = s.templates.some(t => t.instructor);  // no empty Coach column while no coach is set
    main.innerHTML = `<h1>Schedule</h1><div class="gym-toolbar"><a href="gym#schedule/${shift(-7)}">&larr; Earlier</a> <span class="gym-muted">${when(s.days[0].date)} to ${when(s.days[6].date)}</span> <a href="gym#schedule/${shift(7)}">Later &rarr;</a></div>
      <div class="gym-week">${s.days.map(day => `<div class="gym-day"><h3>${esc(day.weekday)} <span class="gym-muted">${when(day.date)}</span></h3>
        ${day.classes.map(c => `<div class="gym-card"><b>${esc(c.time)}</b> <a href="gym#roster/${encodeURIComponent(c.id)}">${esc(c.name)}</a><br>
          ${c.instructor || counts(c) ? `<span class="gym-muted">${[esc(c.instructor), counts(c)].filter(Boolean).join(' &middot; ')}</span>` : ''}
          ${c.canceled ? '<br><span class="gym-pill canceled">Canceled</span>' : ''}</div>`).join('') || '<p class="gym-empty">No classes</p>'}</div>`).join('')}</div>
      <div class="gym-card wide" style="margin-top:16px"><h2>Weekly classes</h2>
        <div class="gym-wrap"><table class="gym-table gym-weekly"><thead><tr><th>Day</th><th>Time</th><th>Class</th>${coach ? '<th class="hide-sm">Coach</th>' : ''}<th class="hide-sm">Minutes</th><th></th></tr></thead><tbody>
        ${s.templates.map(t => `<tr><td>${esc(String(s.weekdays[t.weekday]).slice(0, 3))}</td><td class="nowrap">${time12(t.start)}</td><td>${esc(t.name)}${t.active ? '' : ' <span class="gym-muted">(off)</span>'}</td>${coach ? `<td class="hide-sm">${esc(t.instructor)}</td>` : ''}<td class="hide-sm">${t.minutes}</td>
          <td>${s.can.manage ? `<button data-template="${esc(t.id)}">Edit</button>` : ''}</td></tr>`).join('')}</tbody></table></div>
        ${s.can.manage ? '<p><button class="primary" id="gym-new-class">Add a weekly class</button></p>' : ''}</div>`;
    const classForm = t => form(t ? 'Edit ' + t.name : 'Add a weekly class', [
      { name: 'name', label: 'Class name', required: true, value: t && t.name },
      { name: 'weekday', label: 'Day', options: s.weekdays.map((d, i) => ({ value: i, label: d })), value: t ? t.weekday : 0 },
      { name: 'start', label: 'Start', type: 'time', value: t ? t.start : '18:00' }, { name: 'minutes', label: 'Minutes', type: 'number', value: t ? t.minutes : 60 },
      { name: 'instructor', label: 'Coach', value: t && t.instructor }, { name: 'room', label: 'Room', value: t && t.room },
      { name: 'program', label: 'Program', options: [{ value: '', label: 'Everyone' }, ...s.programs.map(p => ({ value: p.id, label: p.label || p.id }))], value: t ? t.program : '' },
      { name: 'active', label: 'On the schedule', options: [{ value: 'true', label: 'Yes' }, { value: 'false', label: 'No' }], value: t ? String(!!t.active) : 'true' },
    ], (v, rid) => command('gym.class.save', { ...(t ? { id: t.id, revision: t.revision } : {}), name: v.name, weekday: Number(v.weekday), start: v.start,
      minutes: Number(v.minutes), instructor: v.instructor, room: v.room, program: v.program, active: v.active === 'true' }, rid));
    main.querySelectorAll('[data-template]').forEach(b => b.onclick = () => classForm(s.templates.find(t => t.id === b.dataset.template)));
    const add = document.getElementById('gym-new-class');
    if (add) add.onclick = () => classForm(null);
  }

  // ---------- Roster: tap who is on the mat ----------
  // Three groups: on the mat, booked but not here yet, and everyone else in the class's program
  // (folded away until asked for, or a search finds them).
  async function roster(sid) {
    const r = await get('api/gym/roster?session=' + encodeURIComponent(sid));
    ranks = r.ranks;
    const s = r.session;
    let q = '', everyone = false;
    const card = p => `<button type="button" data-id="${esc(p.id)}" aria-pressed="${!!p.checkin_id}"${r.can.checkin && !s.canceled ? '' : ' disabled'}>
        <span>${dot(p.waiver)}${esc(p.name)}<br>${r.ranks.length ? `<small>${beltDot('', p.belt)}${esc(p.rank)}</small>` : ''}</span><span>${p.checkin_id ? '&#10003; Here' : 'Check in'}</span></button>`;
    const group = (title, people, empty) => `<h2 class="gym-roster-h">${esc(title)} <span class="gym-muted">${people.length}</span></h2>
      ${people.length ? `<div class="gym-roster">${people.map(card).join('')}</div>` : `<p class="gym-empty">${esc(empty)}</p>`}`;
    const draw = () => {
      const match = p => !q || p.name.toLowerCase().includes(q);
      const here = r.people.filter(p => p.checkin_id && match(p));
      const booked = r.people.filter(p => !p.checkin_id && p.booked && match(p));
      const rest = r.people.filter(p => !p.checkin_id && !p.booked && match(p));
      const open = everyone || q;
      document.getElementById('gym-roster').innerHTML = group('On the mat', here, 'Nobody checked in yet.')
        + group('Booked, not here yet', booked, 'Nobody else booked.')
        + (open ? group('Everyone else', rest, q ? 'Nobody else matches.' : 'Nobody else.')
                : rest.length && !s.canceled && s.local_date <= r.today ? `<p><button type="button" id="gym-everyone">Check in a walk-in</button> <span class="gym-muted">${rest.length} more members can take this class</span></p>` : '');
      document.getElementById('gym-here').textContent = r.people.filter(p => p.checkin_id).length;
      const all = document.getElementById('gym-everyone');
      if (all) all.onclick = () => { everyone = true; draw(); };
      document.querySelectorAll('#gym-roster button[data-id]').forEach(b => b.onclick = async () => {
        const p = r.people.find(x => x.id === b.dataset.id);
        if (p.checkin_id && b.dataset.sure !== '1') {  // a check-in is undone on a second tap, never the first
          b.dataset.sure = '1';
          b.lastElementChild.textContent = 'Tap again to undo';
          setTimeout(() => { if (b.isConnected && b.dataset.sure === '1') { b.dataset.sure = ''; b.lastElementChild.innerHTML = '&#10003; Here'; } }, 4000);
          return;
        }
        b.disabled = true;
        try {
          if (p.checkin_id) { await command('gym.checkin.remove', { id: p.checkin_id }); p.checkin_id = null; }
          else { p.checkin_id = (await command('gym.checkin.add', { session_id: s.id, contact_id: p.id, method: 'roster' })).id; }
        } catch (e) { alert(e.message); }
        draw();
      });
    };
    const sub = [day(s.local_date), esc(s.time || ''), esc(s.instructor || ''), '<b id="gym-here"></b> on the mat'].filter(Boolean).join(' &middot; ');
    const back = cameFrom === 'schedule' ? ['schedule', 'Schedule'] : cameFrom === 'attendance' ? ['attendance', 'Attendance'] : ['today', 'Today'];
    main.innerHTML = `<p><a href="gym#${back[0]}">&larr; ${back[1]}</a></p><h1>${esc(s.name)}</h1>
      <p class="gym-sub">${sub}${s.canceled ? ' &middot; <span class="gym-pill canceled">Canceled</span>' : ''}</p>
      ${s.local_date > r.today ? '<p class="gym-banner">This class is in the future. Check-in opens on the day.</p>' : ''}
      <div class="gym-toolbar"><input id="gym-rq" type="search" placeholder="Find a name" aria-label="Find a member"></div>${r.people.some(p => p.waiver !== null && p.waiver !== undefined) ? LEGEND : ''}<div id="gym-roster"></div>
      ${r.can.manage ? `<p class="gym-roster-foot"><button type="button" id="gym-cancel-class">${s.canceled ? 'Restore this class' : 'Cancel this class'}</button></p>` : ''}`;
    document.getElementById('gym-rq').oninput = e => { q = e.target.value.toLowerCase(); draw(); };
    const cancel = document.getElementById('gym-cancel-class');
    if (cancel) cancel.onclick = async () => {
      if (s.canceled) { await command('gym.session.restore', { session_id: s.id }); return route(); }
      form('Cancel ' + s.name + ', ' + day(s.local_date), [
        { name: 'reason', label: 'Why? For the record: members are not told by this.', required: true },
      ], (v, rid) => command('gym.session.cancel', { session_id: s.id, reason: v.reason }, rid));
    };
    draw();
  }

  // ---------- Attendance: history, trends and who to follow up with ----------
  // A range (presets or two dates) and a program. Every number is compared with the same number of
  // days just before, and every follow-up list says its rule, so an owner can trust it.
  const iso = d => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  const addDays = (s, n) => { const d = ymd(s); d.setDate(d.getDate() + n); return iso(d); };
  function presets(today) {
    const t = ymd(today), dow = (t.getDay() + 6) % 7;  // Monday starts the week
    const monday = addDays(today, -dow), first = iso(new Date(t.getFullYear(), t.getMonth(), 1));
    const lastFirst = iso(new Date(t.getFullYear(), t.getMonth() - 1, 1)), lastEnd = addDays(first, -1);
    return [['This week', monday, today], ['Last week', addDays(monday, -7), addDays(monday, -1)],
            ['This month', first, today], ['Last month', lastFirst, lastEnd], ['Last 90 days', addDays(today, -89), today]];
  }
  const change = (now, before) => {
    const d = now - before;
    return d > 0 ? `<span class="gym-up">&#9650; ${d}</span>` : d < 0 ? `<span class="gym-down">&#9660; ${-d}</span>` : '<span class="gym-muted">same</span>';
  };
  let attSort = 'visits', attQ = '', attAll = false;
  const FEW = 6, ROWS = 25;  // a follow-up card shows its first 6; the member table its first 25
  async function attendanceView(arg) {
    const [from, to, program] = (arg || '').split('/');
    const a = await get('api/gym/attendance?' + new URLSearchParams({ from: from || '', to: to || '', program: program || '' }));
    const go = (f, t2, p) => { location.hash = 'attendance/' + [f, t2, p ?? a.program].join('/'); };
    // The chart: a bar a day, or a bar a week when the range is long.
    let bars = a.per_day.map(d => ({ label: d.date, n: d.checkins, title: day(d.date) }));
    if (bars.length > 62) {
      const weeks = [];
      bars.forEach((b, i) => { if (i % 7 === 0) weeks.push({ label: b.label, n: 0, title: 'Week of ' + when(b.label) }); weeks[weeks.length - 1].n += b.n; });
      bars = weeks;
    }
    const top = Math.max(1, ...bars.map(b => b.n));
    const every = Math.ceil(bars.length / 10);
    const chart = `<div class="gym-chart" role="img" aria-label="Check-ins per ${a.per_day.length > 62 ? 'week' : 'day'}">${bars.map((b, i) => `<div class="gym-bar" title="${esc(b.title)}: ${b.n}">
        <span class="n">${b.n || ''}</span><i style="height:${Math.round(b.n / top * 100)}%"></i><span class="l">${i % every === 0 ? (bars.length <= 7 ? DOW[ymd(b.label).getDay()] : when(b.label)) : ''}</span></div>`).join('')}</div>`;
    const fu = a.follow_up, r = a.rules, T = a.totals;
    const person = (m, detail) => `<li><span>${memberLink(m.id, m.name)}<br><span class="gym-muted">${detail}</span></span>${m.phone ? `<a class="gym-call" href="tel:${esc(tel(m.phone))}">Call</a>` : ''}</li>`;
    const card = (id, title, rule, items, detail, empty) => `<div class="gym-card" id="${id}"><h2>${esc(title)} <span class="gym-muted">${items.length}</span></h2>
      <p class="gym-muted gym-rule">${esc(rule)}</p>${list(items, (m, i) => person(m, detail(m)).replace('<li>', i >= FEW ? '<li hidden>' : '<li>'), empty)}
      ${items.length > FEW ? `<button type="button" class="gym-quiet" data-all>Show all ${items.length}</button>` : ''}</div>`;
    main.innerHTML = `<h1>Attendance</h1>
      <p class="gym-sub">${when(a.from)} to ${when(a.to)} &middot; compared with ${when(a.before.from)} to ${when(a.before.to)}</p>
      <div class="gym-toolbar gym-range">
        <div class="gym-choice" role="group" aria-label="Range">${presets(a.today).map(([l, f, t2]) => `<button type="button" data-range="${f}/${t2}" aria-pressed="${f === a.from && t2 === a.to}">${l}</button>`).join('')}</div>
        <label>From <input type="date" id="att-from" value="${a.from}" max="${a.today}"></label>
        <label>To <input type="date" id="att-to" value="${a.to}" max="${a.today}"></label>
        <button type="button" id="att-go">Show</button>
        <select id="att-program" aria-label="Program"><option value="">Every program</option>${a.programs.map(p => `<option value="${esc(p.id)}"${p.id === a.program ? ' selected' : ''}>${esc(p.label || p.id)}</option>`).join('')}</select>
      </div>
      <div class="gym-stats">
        <div class="gym-stat"><b>${T.checkins}</b><span>check-ins &middot; ${change(T.checkins, T.checkins_before)}</span></div>
        <div class="gym-stat"><b>${T.on_plan_trained}</b><span>of ${T.on_plan} members on a plan trained</span></div>
        <div class="gym-stat"><b>${T.per_day}</b><span>check-ins a day on average</span></div>
        <a class="gym-stat ${fu.slowing_down.length ? 'warn' : ''}" href="gym#attendance" data-jump="att-slow"><b>${fu.slowing_down.length}</b><span>slowing down</span></a>
        <a class="gym-stat ${fu.no_shows.length ? 'warn' : ''}" href="gym#attendance" data-jump="att-noshow"><b>${fu.no_shows.length}</b><span>booked, did not come</span></a>
      </div>
      <div class="gym-grid">
        <div class="gym-card wide"><h2>Check-ins per ${a.per_day.length > 62 ? 'week' : 'day'}</h2>${chart}</div>
        ${card('att-slow', 'Slowing down', `On a plan, ${r.slow_min} or more visits in the period before, half that or less now.`, fu.slowing_down, m => `${m.visits} now, ${m.before} before &middot; last ${m.last_visit ? when(m.last_visit) : 'never'}`, 'Nobody is slowing down.')}
        ${card('att-quiet', 'Not seen', `On a plan, no visit in the last ${r.not_seen_days} days of this range.`, fu.not_seen, m => m.last_visit ? 'last attended ' + when(m.last_visit) : 'never checked in', 'Everyone on a plan came in.')}
        ${card('att-new', 'New, few visits', `Joined in the last ${r.new_days} days, fewer than ${r.new_few} visits so far.`, fu.new_few_visits, m => `joined ${when(m.joined_on)} &middot; ${m.total} visit${m.total === 1 ? '' : 's'}`, 'Every new member is training.')}
        ${card('att-noshow', 'Booked, did not come', 'Booked a class in this range that has ended, and never checked in.', fu.no_shows, m => `${m.no_shows} missed &middot; ${m.visits} attended`, 'No missed bookings.')}
        <div class="gym-card"><h2>Busiest classes</h2>${list(a.classes.slice(0, 10), c => `<li><span>${esc(c.class)}</span><b>${c.checkins}</b></li>`, 'No check-ins in this range.')}</div>
        <div class="gym-card wide" id="att-members"><h2>Every member</h2>
          <div class="gym-toolbar"><input id="att-q" type="search" placeholder="Search a name" aria-label="Search members" value="${esc(attQ)}">
            <select id="att-sort" aria-label="Sort">${[['visits', 'Most visits in range'], ['drop', 'Biggest drop'], ['total', 'Most since day one'], ['last', 'Longest since last visit'], ['name', 'Name']].map(([v, l]) => `<option value="${v}"${v === attSort ? ' selected' : ''}>${l}</option>`).join('')}</select></div>
          <div class="gym-wrap"><table class="gym-table gym-att"><thead><tr><th>Member</th><th>In range</th><th class="hide-sm">Since day one</th><th class="hide-sm">A week</th><th>Last visit</th></tr></thead><tbody id="att-rows"></tbody></table></div>
          <button type="button" class="gym-quiet" id="att-more" hidden></button></div>
      </div>`;
    const draw = () => {
      const q = attQ.trim().toLowerCase();
      const by = { visits: (x, y) => y.visits - x.visits, drop: (x, y) => (x.visits - x.before) - (y.visits - y.before), total: (x, y) => y.total - x.total,
                   last: (x, y) => (x.last_visit || '').localeCompare(y.last_visit || ''), name: (x, y) => x.name.localeCompare(y.name) }[attSort];
      const all = a.members.filter(m => !q || m.name.toLowerCase().includes(q)).sort(by);
      const rows = attAll || q ? all : all.slice(0, ROWS);
      const more = document.getElementById('att-more');
      more.hidden = rows.length === all.length;
      more.textContent = `Show all ${all.length} members`;
      document.getElementById('att-rows').innerHTML = rows.map(m => `<tr><td>${memberLink(m.id, m.name)}<br><span class="gym-muted">${esc(m.plan || 'No plan')}${m.status === 'trial' ? ' &middot; trial' : ''}</span></td>
        <td><b>${m.visits}</b> ${change(m.visits, m.before)}</td>
        <td class="hide-sm">${m.total}${m.first_visit ? `<br><span class="gym-muted">since ${when(m.first_visit)}</span>` : ''}</td>
        <td class="hide-sm">${m.per_week}</td><td>${m.last_visit ? when(m.last_visit) : '<span class="gym-muted">never</span>'}</td></tr>`).join('')
        || '<tr><td colspan="5" class="gym-empty">No members match.</td></tr>';
    };
    main.querySelectorAll('[data-range]').forEach(b => b.onclick = () => { const [f, t2] = b.dataset.range.split('/'); go(f, t2); });
    document.getElementById('att-go').onclick = () => go(document.getElementById('att-from').value, document.getElementById('att-to').value);
    document.getElementById('att-program').onchange = e => go(a.from, a.to, e.target.value);
    document.getElementById('att-q').oninput = e => { attQ = e.target.value; draw(); };
    document.getElementById('att-sort').onchange = e => { attSort = e.target.value; draw(); };
    document.getElementById('att-more').onclick = () => { attAll = true; draw(); };
    main.querySelectorAll('[data-all]').forEach(b => b.onclick = () => { b.parentElement.querySelectorAll('li[hidden]').forEach(li => li.hidden = false); b.remove(); });
    main.querySelectorAll('[data-jump]').forEach(x => x.onclick = ev => { ev.preventDefault(); document.getElementById(x.dataset.jump).scrollIntoView({ behavior: 'smooth', block: 'start' }); });
    draw();
  }

  // ---------- Check-in: the tablet, and the two codes to print for the door ----------
  async function checkinPage() {
    let d = null, err = '';
    try { d = await get('api/gym/door'); } catch (e) { err = e.message; }
    const code = c => `<div class="gym-card gym-qr"><h2>${esc(c.title)}</h2><div class="gym-qr-img">${c.svg}</div>
        <p class="gym-muted gym-qr-url">${esc(c.url)}</p>
        <p><button type="button" class="primary" data-print>Print</button> <a href="${esc(c.url)}" target="_blank" rel="noopener">Open it</a></p></div>`;
    main.innerHTML = `<h1>Check-in</h1><p class="gym-sub">Three ways in, all landing in the same attendance.</p>
      <div class="gym-grid">
        ${d && d.codes.checkin ? code({ ...d.codes.checkin, title: 'QR check-in: members scan it with their own phone' }) : ''}
        ${d && d.codes.waiver ? code({ ...d.codes.waiver, title: 'QR waiver: new members sign before they train' }) : ''}
        <div class="gym-card"><h2>The tablet at the door</h2><p>Members type their name and tap it. One PIN a day opens it.</p>
          <p><a class="gym-btn" href="kiosk">Open the check-in tablet</a></p></div>
        ${err ? `<div class="gym-card"><h2>QR codes</h2><p class="gym-banner">${esc(err)}</p></div>` : ''}
      </div>
      <p class="gym-muted">Print both and tape them at the front desk. The codes only change if you change the door code.</p>`;
    main.querySelectorAll('[data-print]').forEach(b => b.onclick = () => {
      const card = b.closest('.gym-qr');
      const w = open('', '_blank');
      if (!w) return;
      w.document.write(`<!doctype html><title>${esc(d.gym)}</title><body style="font:20px system-ui;text-align:center;padding:40px">
        <h1>${esc(d.gym)}</h1><h2>${card.querySelector('h2').textContent.split(':')[0]}</h2>${card.querySelector('.gym-qr-img').innerHTML.replace(/width="\d+" height="\d+"/, 'width="420" height="420"')}
        <p>Point your phone's camera at the code.</p><script>print()<\/script></body>`);
      w.document.close();
    });
  }

  // ---------- Import and export ----------
  function data() {
    main.innerHTML = `<h1>Import and export</h1><div class="gym-grid">
      <div class="gym-card"><h2>Export everything</h2><p>One ZIP of spreadsheets: contacts, members, plans, memberships, attendance, ranks and classes. Leaving is easy.</p><button class="primary" id="gym-export">Download the ZIP</button><p id="gym-export-msg" class="gym-muted"></p></div>
      <div class="gym-card"><h2>Import members</h2><p>A members file (.csv) from your old system. Nothing already here is changed: a name, phone or email we already have is skipped. A backup is taken first.</p>
        <label class="gym-file"><input type="file" id="gym-file" accept=".csv,text/csv">Choose a members file (.csv)</label>
        <p id="gym-file-name" class="gym-muted"></p><div id="gym-import"></div></div></div>`;
    document.getElementById('gym-export').onclick = async () => {
      const msg = document.getElementById('gym-export-msg');
      const r = await fetch('api/crm/export', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      if (!r.ok) { msg.textContent = ((await r.json().catch(() => ({}))).error) || 'Export failed'; return; }
      const name = (r.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/);
      const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(await r.blob()), download: name ? name[1] : 'export.zip' });
      a.click(); msg.textContent = 'Downloaded.';
    };
    document.getElementById('gym-file').onchange = async e => {
      const file = e.target.files[0];
      if (!file) return;
      document.getElementById('gym-file-name').textContent = file.name;
      const b64 = btoa(new Uint8Array(await file.arrayBuffer()).reduce((s, b) => s + String.fromCharCode(b), ''));
      const step = async name => {
        const r = await fetch('api/gym/import', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ step: name, file: b64 }) });
        const body = await r.json();
        if (!r.ok) throw new Error(body.error || 'Import failed');
        return body;
      };
      const out = document.getElementById('gym-import');
      try {
        const p = await step('preview');
        out.innerHTML = `<p><b>${p.new}</b> new members &middot; <b>${p.duplicates.length}</b> already here (skipped) &middot; <b>${p.problems.length}</b> rows with a problem${p.already ? ` &middot; ${p.already} imported before` : ''}</p>
          ${p.new_plans.length ? `<p class="gym-muted">New plans: ${p.new_plans.map(esc).join(', ')}</p>` : ''}
          <p class="gym-muted">Columns used: ${p.columns.map(c => esc(c.column)).join(', ')}${p.ignored.length ? ' &middot; ignored: ' + p.ignored.map(esc).join(', ') : ''}</p>
          ${list(p.problems.slice(0, 20), x => `<li>Row ${x.row}: ${esc(x.reason)}</li>`, '')}
          ${p.new ? `<button class="primary" id="gym-import-save">Import ${p.new} members</button>` : ''}`;
        const save = document.getElementById('gym-import-save');
        if (save) save.onclick = async () => {
          save.disabled = true;
          const s = await step('save');
          out.innerHTML = `<p class="gym-banner"><b>${s.created}</b> member${s.created === 1 ? '' : 's'} imported &middot; ${s.skipped.length} skipped &middot; ${s.failed.length} failed${s.backup ? ' &middot; backup ' + esc(s.backup) : ''}</p>`;
        };
      } catch (err) { out.innerHTML = `<p class="gym-banner">${esc(err.message)}</p>`; }
    };
  }

  // ---------- routing ----------
  // `moved`: the person went to another screen (a tab, a link, back): start it at the top, with a
  // placeholder, never the old screen's scroll or content. A save or the minute refresh stays put.
  let cameFrom = 'today', lastView = 'today';  // the screen before this one: a roster's way back
  async function route(moved) {
    if (!location.hash) history.replaceState(null, '', location.pathname + location.search + '#today');
    const [view, arg] = decodeURIComponent(location.hash.slice(1) || 'today').split(/\/(.*)/s);
    if (view !== lastView) { if (moved === true) cameFrom = lastView; lastView = view; }  // a first load has no way back but its own
    document.querySelectorAll('[data-tab]').forEach(a => a.toggleAttribute('aria-current', a.dataset.tab === (view === 'member' ? 'members' : view === 'roster' ? (['schedule', 'attendance'].includes(cameFrom) ? cameFrom : 'today') : view)));
    document.querySelectorAll('[data-tab][aria-current]').forEach(a => a.setAttribute('aria-current', 'page'));
    showTab();
    if (moved === true && dialog.open) dialog.close();  // a form belongs to the screen it was opened on
    if (moved === true) { main.innerHTML = SKELETON; scrollTo(0, 0); }
    try {
      if (view === 'members') await members();
      else if (view === 'member') await member(arg);
      else if (view === 'schedule') await schedule(arg);
      else if (view === 'roster') await roster(arg);
      else if (view === 'attendance') await attendanceView(arg);
      else if (view === 'checkin') await checkinPage();
      else if (view === 'data') data();
      else if (view === 'billing') await billing();
      else if (view === 'switch') await switchGuide();
      else await today();
    } catch (e) {
      main.innerHTML = `<p class="gym-banner">${esc(e.message)}</p>`;
    }
  }
  // The tab bar scrolls sideways on a phone: keep the open tab in view, and fade the edge while
  // more tabs sit off-screen.
  const tabs = document.querySelector('.gym-top nav');
  function edge() {
    if (!tabs) return;
    tabs.classList.toggle('more-left', tabs.scrollLeft > 4);
    tabs.classList.toggle('more-right', tabs.scrollLeft + tabs.clientWidth < tabs.scrollWidth - 4);
  }
  function showTab() {
    const a = tabs && tabs.querySelector('[aria-current]');
    if (a && tabs.scrollWidth > tabs.clientWidth) {
      const left = a.getBoundingClientRect().left - tabs.getBoundingClientRect().left + tabs.scrollLeft, right = left + a.offsetWidth;
      if (left < tabs.scrollLeft || right > tabs.scrollLeft + tabs.clientWidth) tabs.scrollLeft = left - 16;
    }
    edge();
  }
  const top = document.querySelector('.gym-top');  // jump links land below the sticky header
  const fit = () => { edge(); if (top) document.documentElement.style.setProperty('--gym-top', top.offsetHeight + 'px'); };
  if (tabs) tabs.addEventListener('scroll', edge, { passive: true });
  addEventListener('resize', fit);
  if (top && window.ResizeObserver) new ResizeObserver(fit).observe(top);  // fonts and the logo load late
  fit();
  addEventListener('hashchange', () => route(true));
  route();
  setInterval(() => { if (!document.hidden && !dialog.open && (location.hash || '#today') === '#today') today().catch(() => {}); }, 60000);
})();
