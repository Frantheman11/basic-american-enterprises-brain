// The check-in kiosk: PIN once per day, then search your name, tap, done.
// When the server cannot be reached it says so in plain words. It never shows a spinner.
(() => {
  'use strict';
  const root = document.getElementById('kiosk');
  const title = root.querySelector('h1').outerHTML;
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : Date.now() + '-' + Math.random().toString(16).slice(2));
  const store = { get: () => { try { return localStorage.getItem('kiosk.token') || ''; } catch { return ''; } },
                  set: v => { try { v ? localStorage.setItem('kiosk.token', v) : localStorage.removeItem('kiosk.token'); } catch { /* private mode */ } } };
  let token = store.get();
  let classes = [];
  let idle;

  class Offline extends Error {}
  class Locked extends Error {}

  async function call(path, body) {
    let r;
    try {
      r = await fetch(path, { method: body ? 'POST' : 'GET', cache: 'no-store',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token }, body: body ? JSON.stringify(body) : undefined });
    } catch { throw new Offline(); }
    const data = await r.json().catch(() => ({}));
    if (r.status === 401 && path !== 'api/kiosk/unlock') throw new Locked();
    if (r.status >= 500) throw new Offline();
    if (!r.ok) throw new Error(data.error || 'Please see the front desk.');
    return data;
  }

  function offline() {
    root.innerHTML = `${title}<p class="kiosk-offline">Check-in is offline, tell your coach.</p>
      <p>Your coach will mark you on the paper roster.</p><button id="k-retry">Try again</button>`;
    document.getElementById('k-retry').onclick = home;
  }

  function fail(e) {
    if (e instanceof Offline) return offline();
    if (e instanceof Locked) { token = ''; store.set(''); return pin(); }
    root.innerHTML = `${title}<p class="kiosk-offline">${esc(e.message)}</p><button id="k-back">Back</button>`;
    document.getElementById('k-back').onclick = home;
    later(home, 6000);
  }

  function later(fn, ms) { clearTimeout(idle); idle = setTimeout(fn, ms); }

  function pin(message) {
    root.innerHTML = `${title}<p>Staff: enter the kiosk PIN to open check-in for today.</p>
      <form class="kiosk-pin" id="k-pin"><input id="k-pin-input" type="password" inputmode="numeric" autocomplete="off" aria-label="Kiosk PIN" required>
      <button class="primary" type="submit">Open check-in</button></form><p id="k-msg" role="alert">${esc(message || '')}</p>`;
    document.getElementById('k-pin').onsubmit = async ev => {
      ev.preventDefault();
      try {
        const r = await call('api/kiosk/unlock', { pin: document.getElementById('k-pin-input').value });
        token = r.token; store.set(token); home();
      } catch (e) {
        if (e instanceof Offline) return offline();
        document.getElementById('k-msg').textContent = e.message;
        document.getElementById('k-pin-input').value = '';
      }
    };
    document.getElementById('k-pin-input').focus();
  }

  async function home() {
    clearTimeout(idle);
    if (!token) return pin();
    if (!document.getElementById('k-q')) root.innerHTML = `${title}<p>Opening check-in&hellip;</p>`;  // never a blank screen while it loads
    try { classes = (await call('api/kiosk/now')).classes; } catch (e) { return fail(e); }
    const now = classes.length ? `Checking in to ${classes.map(c => esc(c.time) + ' ' + esc(c.name)).join(' or ')}` : 'No class right now: you will be checked in to open mat.';
    root.innerHTML = `${title}<p>${now}</p><input id="k-q" type="search" placeholder="Type your name" autocomplete="off" aria-label="Your name">
      <div class="kiosk-names" id="k-names"></div>`;
    const q = document.getElementById('k-q');
    let seq = 0;
    q.oninput = async () => {
      const mine = ++seq;
      later(home, 60000);
      if (q.value.trim().length < 2) { document.getElementById('k-names').innerHTML = ''; return; }
      let found;
      try { found = await call('api/kiosk/find?q=' + encodeURIComponent(q.value.trim())); } catch (e) { return fail(e); }
      if (mine !== seq) return;
      document.getElementById('k-names').innerHTML = found.members.map(m => `<button data-id="${esc(m.id)}" data-name="${esc(m.name)}">${esc(m.name)}</button>`).join('')
        || '<p>No match. Check the spelling, or see the front desk.</p>';
      document.querySelectorAll('#k-names button').forEach(b => b.onclick = () => pick(b.dataset.id, b.dataset.name));
    };
    q.focus();
  }

  function pick(id, name) {
    if (classes.length <= 1) return checkin(id, name, classes[0]);
    root.innerHTML = `${title}<p>${esc(name)}, which class?</p><div class="kiosk-classes">${classes.map(c => `<button data-s="${esc(c.id)}">${esc(c.time)} ${esc(c.name)}</button>`).join('')}</div><button id="k-back">Back</button>`;
    document.querySelectorAll('[data-s]').forEach(b => b.onclick = () => checkin(id, name, classes.find(c => c.id === b.dataset.s)));
    document.getElementById('k-back').onclick = home;
    later(home, 30000);
  }

  async function checkin(id, name, session) {
    const body = { contact_id: id, request_id: uuid() };
    if (session) body.session_id = session.id;
    try {
      const r = await call('api/kiosk/checkin', body);
      // A failed payment: a gentle note, never the reason (the tablet is in a shared doorway).
      const card = r.update_card ? '<p class="kiosk-card">Quick note: please update your card on file. The front desk can help, or use the link we text you.</p>' : '';
      const waiver = r.needs_waiver ? '<p class="kiosk-card">Before you train: please sign the waiver. Scan the waiver code at the desk.</p>' : '';
      root.innerHTML = `${title}<p class="kiosk-done">${r.already ? 'Already checked in' : "You're checked in"}, ${esc(name.split(' ')[0])}.</p><p>${session ? esc(session.name) : 'Open mat'}. Have a good class.</p>${card}${waiver}`;
      later(home, card || waiver ? 9000 : 4000);
    } catch (e) { fail(e); }
  }

  home();
})();
