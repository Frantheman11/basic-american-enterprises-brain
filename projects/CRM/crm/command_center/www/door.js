// The door QR pages (gym_door.py), on the member's own phone: check in, or sign the waiver.
// The door code comes from the printed link (?d=). Plain words, big targets, no spinner.
(() => {
  'use strict';
  const root = document.getElementById('kiosk');
  const title = root.querySelector('h1').outerHTML;
  const mode = document.body.dataset.mode;
  const door = new URLSearchParams(location.search).get('d') || '';
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : Date.now() + '-' + Math.random().toString(16).slice(2));
  const saved = { get: () => { try { return localStorage.getItem('door.contact') || ''; } catch { return ''; } },
                  set: v => { try { v ? localStorage.setItem('door.contact', v) : localStorage.removeItem('door.contact'); } catch { /* private mode */ } } };

  async function post(path, body) {
    let r;
    try {
      r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ door, ...body }) });
    } catch { throw new Error('No connection. Check in at the front desk.'); }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || 'Please ask at the front desk.');
    return data;
  }
  const say = (html, err) => { root.innerHTML = `${title}${err ? `<p class="kiosk-offline">${esc(err)}</p>` : ''}${html}`; };

  // ---------- check in ----------
  function ask(err) {
    say(`<p>Type the phone number or email the gym has for you.</p>
      <form id="d-find" class="door-form"><input id="d-contact" type="text" inputmode="email" autocomplete="tel email" aria-label="Phone or email" value="${esc(saved.get())}" required>
      <label class="door-check"><input type="checkbox" id="d-keep" ${saved.get() ? 'checked' : ''}> Remember me on this phone</label>
      <button class="primary" type="submit">Find me</button></form>`, err);
    document.getElementById('d-find').onsubmit = ev => { ev.preventDefault(); find(document.getElementById('d-contact').value.trim(), document.getElementById('d-keep').checked); };
  }
  async function find(contact, keep) {
    let r;
    try { r = await post('api/door/find', { contact }); } catch (e) { return ask(e.message); }
    saved.set(keep ? contact : '');
    const classes = r.classes;
    const pick = classes.length > 1 ? `<p>Which class?</p><div class="kiosk-classes">${classes.map((c, i) => `<button type="button" data-class="${i}" aria-pressed="${i === 0}">${esc(c.time)} ${esc(c.name)}</button>`).join('')}</div>` : '';
    let chosen = classes[0] || null;
    say(`<p>${classes.length ? `Checking in to <b>${esc(chosen.time)} ${esc(chosen.name)}</b>${classes.length > 1 ? ' (or pick below)' : ''}.` : 'No class right now: you will be checked in to open mat.'}</p>
      ${pick}<p>Tap who is training:</p><div class="kiosk-names">${r.members.map(m => `<button type="button" class="primary" data-id="${esc(m.id)}">${esc(m.name)}</button>`).join('')}</div>
      <p><button type="button" id="d-other">Not you?</button></p>`);
    root.querySelectorAll('[data-class]').forEach(b => b.onclick = () => {
      chosen = classes[Number(b.dataset.class)];
      root.querySelectorAll('[data-class]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
    });
    root.querySelectorAll('[data-id]').forEach(b => b.onclick = () => checkin(contact, b.dataset.id, b.textContent, chosen));
    document.getElementById('d-other').onclick = () => { saved.set(''); ask(); };
  }
  async function checkin(contact, id, name, session) {
    let r;
    try { r = await post('api/door/checkin', { contact, contact_id: id, session_id: session ? session.id : '', request_id: uuid() }); }
    catch (e) { return ask(e.message); }
    const card = r.update_card ? '<p class="kiosk-card">Quick note: please update your card on file. The front desk can help.</p>' : '';
    const waiver = r.needs_waiver ? `<p class="kiosk-card">Before you train: please sign the waiver. It takes a minute.</p>
      <p><a class="gym-btn primary door-big" href="waiver?d=${encodeURIComponent(door)}">Sign the waiver</a></p>` : '';
    say(`<p class="kiosk-done">${r.already ? 'Already checked in' : "You're checked in"}, ${esc(name.split(' ')[0])}.</p>
      <p>${session ? esc(session.name) : 'Open mat'}. Have a good class.</p>${card}${waiver}
      <p><button type="button" id="d-again">Check in someone else</button></p>`);
    document.getElementById('d-again').onclick = () => find(contact, !!saved.get());
  }

  // ---------- the waiver ----------
  async function waiverPage() {
    let w;
    try {
      const r = await fetch('api/door/waiver', { cache: 'no-store' });
      w = await r.json();
      if (!r.ok) throw new Error(w.error || 'No waiver here.');
    } catch (e) { return say('', e.message); }
    say(`<h2 class="door-title">${esc(w.title)}</h2>
      ${w.sample ? '<p class="kiosk-card">Sample waiver for the demo. Your gym\'s own waiver goes here.</p>' : ''}
      <div class="door-text" tabindex="0" aria-label="Waiver text">${esc(w.text)}</div>
      <form id="w-form" class="door-form door-left">
        <label>Full name of the person training<input name="name" required autocomplete="name"></label>
        <label>Birth date<input name="birth_date" type="date" required></label>
        <label>Email<input name="email" type="email" autocomplete="email"></label>
        <label>Phone<input name="phone" type="tel" autocomplete="tel"></label>
        <div id="w-minor" hidden><label>Parent or guardian's full name<input name="guardian_name" autocomplete="name"></label>
          <p class="door-hint">Under ${w.adult_age}: a parent or guardian signs.</p></div>
        <label class="door-check"><input type="checkbox" name="agree" required> I have read this waiver and agree to it.</label>
        <label>Signature: type <span id="w-who">your</span> full name<input name="signature" required autocomplete="off"></label>
        <p id="w-err" class="kiosk-offline" role="alert" hidden></p>
        <button class="primary door-big" type="submit">Sign the waiver</button></form>`);
    const f = document.getElementById('w-form');
    const minor = () => {
      const b = f.birth_date.value ? new Date(f.birth_date.value + 'T12:00:00') : null, t = new Date();
      const age = b ? t.getFullYear() - b.getFullYear() - ((t.getMonth() < b.getMonth() || (t.getMonth() === b.getMonth() && t.getDate() < b.getDate())) ? 1 : 0) : 99;
      const kid = age < w.adult_age;
      document.getElementById('w-minor').hidden = !kid;
      f.guardian_name.required = kid;
      document.getElementById('w-who').textContent = kid ? "the parent or guardian's" : 'your';
    };
    f.birth_date.oninput = minor;
    f.onsubmit = async ev => {
      ev.preventDefault();
      const btn = f.querySelector('button[type=submit]'), err = document.getElementById('w-err');
      btn.disabled = true; err.hidden = true;
      try {
        const v = Object.fromEntries(new FormData(f));
        const r = await post('api/door/waiver/sign', { ...v, agree: f.agree.checked, version: w.version, request_id: f.dataset.rid || (f.dataset.rid = uuid()) });
        say(`<p class="kiosk-done">Signed. Thank you, ${esc(r.name)}.</p><p>The gym has your waiver on file.</p>
          <p><a class="gym-btn door-big" href="checkin?d=${encodeURIComponent(door)}">Check in now</a></p>`);
        scrollTo(0, 0);
      } catch (e) { err.textContent = e.message; err.hidden = false; btn.disabled = false; }
    };
  }

  if (!door) say('', 'Scan the code at the front desk to open this page.');
  else if (mode === 'waiver') waiverPage();
  else if (saved.get()) find(saved.get(), true);
  else ask();
})();
