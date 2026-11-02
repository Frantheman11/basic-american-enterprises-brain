/* Shared live notifications. Events contain only an opaque, permission-scoped revision. */
window.CRMSync = (() => {
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (!link) return;
    const raw = link.getAttribute('href');
    if (!raw.startsWith('#') && raw !== 'strategy#workspace') return;
    const id = raw.slice(raw.indexOf('#') + 1), target = document.getElementById(id);
    if (!target) return;
    event.preventDefault();
    const url = new URL(location.href); url.hash = id; history.replaceState(null, '', url);
    if (!target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1');
    target.focus({preventScroll:true}); target.scrollIntoView({block:'start'});
  });
  let source, timer, hook, version = '', beat = 0, refreshed = 0, running = false, again = false, stopped = false, failed = false;
  let channel;
  function status(text) {
    let el = document.getElementById('crm-sync-status');
    if (!el) {
      el = document.createElement('span'); el.id = 'crm-sync-status'; el.setAttribute('role', 'status');
      el.setAttribute('aria-live', 'polite');
      (document.querySelector('.st-environment') || document.querySelector('.ws-header-tools') || document.querySelector('.header-end') || document.body).append(el);
    }
    if (el.textContent !== text) el.textContent = text;
  }
  async function refresh() {
    if (stopped) return;
    if (running) { again = true; return; }
    running = true;
    try {
      do { again = false; await hook(); await window.TaskEditor?.observe(); document.dispatchEvent(new Event('crm:updated')); } while (again);
      failed = false;
      refreshed = Date.now();
      status('Live updates on');
    } catch { failed = true; status('Updates interrupted · reconnecting…'); version = ''; }
    finally { running = false; }
  }
  function changed(next) {
    if (next !== version) { version = next; refresh(); }
  }
  function connect() {
    if (stopped || document.hidden || !window.EventSource || source) return;
    source = new EventSource('api/crm/events');
    source.addEventListener('heartbeat', () => { beat = Date.now(); if(!failed)status('Live updates on'); });
    source.addEventListener('change', event => { beat = Date.now(); changed(JSON.parse(event.data).revision); });
    source.addEventListener('unavailable', () => { beat = 0; status('Updates interrupted · reconnecting…'); source.close(); source = null; });
    source.onerror = () => { status('Reconnecting live updates…'); };
  }
  async function check() {
    if (stopped || document.hidden) return;
    connect();
    if (failed || Date.now()-refreshed>=60000) refresh();
    if (Date.now() - beat < 5000) return;
    try {
      const response = await fetch('api/crm/revision', {cache: 'no-store', signal: AbortSignal.timeout(10000)});
      if (!response.ok) throw Error();
      changed((await response.json()).revision);
      if(!failed)status('Connected · checking for updates');
    } catch { status('Updates interrupted · reconnecting…'); }
  }
  function start(onChange) {
    if (hook) return;
    hook = onChange; status('Connecting live updates…');
    try { channel = new BroadcastChannel('crm:' + document.baseURI); channel.onmessage = () => refresh(); } catch {}
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) { source?.close(); source = null; }
      else { beat = 0; refresh(); connect(); }
    });
    window.addEventListener('online', () => { beat = 0; check(); });
    window.addEventListener('pagehide', () => { source?.close(); source = null; });
    window.addEventListener('pageshow', () => { connect(); });
    window.addEventListener('focus', () => { check(); });
    timer = setInterval(check, 3000); connect();
  }
  function saved() { channel?.postMessage('changed'); return refresh(); }
  function preserve(root, render) {
    const active = document.activeElement, id = active?.id;
    const attrs = [...(active?.attributes || [])].filter(a=>a.name.startsWith('data-'));
    const selector = !id && root.contains(active) && attrs.length ? active.tagName + attrs.map(a=>`[${a.name}="${CSS.escape(a.value)}"]`).join('') : null;
    const selection = ['INPUT', 'TEXTAREA'].includes(active?.tagName) ? [active.selectionStart, active.selectionEnd] : null;
    const key = el => el.id || el.dataset.group || el.dataset.dailyDisclosure || el.querySelector('summary')?.textContent;
    const opened = new Set([...root.querySelectorAll('details[open]')].map(key));
    const x = scrollX, y = scrollY;
    render();
    root.querySelectorAll('details').forEach(el => { el.open = opened.has(key(el)); });
    if (id && root.contains(document.getElementById(id))) {
      const next = document.getElementById(id); next.focus({preventScroll: true});
      if (selection && selection[0] !== null) { try { next.setSelectionRange(...selection); } catch {} }
    }
    else if(selector)root.querySelector(selector)?.focus({preventScroll:true});
    window.scrollTo(x, y);
  }
  return {start, saved, refresh, preserve};
})();
