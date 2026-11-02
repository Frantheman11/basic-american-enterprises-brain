// Demo layer (demo_mode.py): a line that says this is a demo, and a guided tour whose steps are
// profile data (/api/demo). It knows nothing about gyms: each step is a title, a sentence, a link and
// an optional target (CSS selectors, first match wins) that the step scrolls into view and outlines.
// On the step's own page the tour shows; anywhere else it folds to a "Resume tour" chip.
(() => {
  'use strict';
  const KEY = 'demo.step';
  const read = () => { try { return sessionStorage.getItem(KEY); } catch { return null; } };
  const write = v => { try { v === null ? sessionStorage.removeItem(KEY) : sessionStorage.setItem(KEY, String(v)); } catch { /* private mode */ } };
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const page = u => u.origin + u.pathname + u.hash;  // a query string (a cache-buster, a tracking tag) is the same page
  const here = href => { try { return page(new URL(href, document.baseURI)) === page(location); } catch { return false; } };
  const calm = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

  fetch('api/demo', { cache: 'no-store' }).then(r => r.json()).then(demo => {
    // The door pages (a member's own phone) show the demo line, never the staff tour over their buttons.
    const steps = document.body.classList.contains('door') ? [] : demo.tour || [];
    const banner = document.createElement('div');
    banner.className = 'demo-banner';
    // On the kiosk the demo line carries the way back: a real front-door tablet never shows one.
    const back = document.getElementById('kiosk') && demo.home ? `<a class="demo-back" href="${esc(demo.home)}">&larr; Dashboard</a>` : '';
    banner.innerHTML = `${back}<b>Demo</b> <span class="demo-line">${esc(demo.banner)}</span>${steps.length ? '<button type="button" class="demo-open">Guided tour</button>' : ''}`;
    document.body.prepend(banner);

    const card = document.createElement('aside');
    card.className = 'demo-tour';
    card.setAttribute('aria-label', 'Guided tour');
    card.hidden = true;
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'demo-resume';
    chip.hidden = true;
    document.body.append(card, chip);

    let step = -1, marked = null, scrolledTo = null, timer;

    // The tour card's height is the room it takes at the bottom: the page gets that much padding.
    const room = () => { document.body.style.paddingBottom = card.hidden ? '' : (card.offsetHeight + 16) + 'px'; };
    if (window.ResizeObserver) new ResizeObserver(room).observe(card);

    function find(target) {
      for (const sel of String(target || '').split(',').map(x => x.trim()).filter(Boolean)) {
        try { const el = document.querySelector(sel); if (el && el.getClientRects().length) return el; } catch { /* a bad selector is skipped */ }
      }
      return null;
    }
    // Scroll so the target sits between the sticky header and the tour card, then outline it.
    function point() {
      const s = steps[step];
      const el = s && !card.hidden ? find(s.target) : null;
      if (marked && marked !== el) marked.classList.remove('demo-target');
      marked = el;
      if (!el) return;
      el.classList.add('demo-target');
      if (scrolledTo === el) return;
      scrolledTo = el;
      const top = [...document.querySelectorAll('body *')].reduce((m, x) => {  // what sticks to the top of the screen
        if (x === card || x === chip) return m;
        const cs = getComputedStyle(x);
        return /sticky|fixed/.test(cs.position) && cs.top !== 'auto' ? Math.max(m, (parseFloat(cs.top) || 0) + x.offsetHeight) : m;
      }, 0);
      const bottom = card.hidden ? 0 : card.getBoundingClientRect().height;
      const r = el.getBoundingClientRect(), space = innerHeight - top - bottom - 24;
      let y = scrollY + r.top - top - 12 - (r.height < space ? (space - r.height) / 2 : 0);
      // When the page title and the target fit on one screen, start at the title: the owner keeps
      // seeing whose page this is.
      const h1 = document.querySelector('main h1');
      if (h1 && h1 !== el && r.bottom - h1.getBoundingClientRect().top <= space) y = scrollY + h1.getBoundingClientRect().top - top - 12;
      scrollTo({ top: Math.max(0, y), behavior: calm() ? 'auto' : 'smooth' });
    }
    const later = () => { clearTimeout(timer); timer = setTimeout(point, 120); };
    new MutationObserver(ms => { if (ms.some(m => !card.contains(m.target))) later(); }).observe(document.body, { childList: true, subtree: true });

    function show(i) {
      const s = steps[i];
      if (i !== step) scrolledTo = null;
      step = i;
      write(i);
      const onPage = !s.href || here(s.href);
      chip.hidden = onPage;
      chip.textContent = `Resume tour: step ${i + 1} of ${steps.length}`;
      card.hidden = !onPage;
      if (onPage) {
        const last = i === steps.length - 1;
        card.innerHTML = `<div class="demo-tour-head"><span>Step ${i + 1} of ${steps.length}<span class="demo-short">: ${esc(s.title)}</span></span>
            <button type="button" class="demo-close">Explore on my own</button></div>
          <h2>${esc(s.title)}</h2><p>${esc(s.text)}</p>
          <ol class="demo-dots">${steps.map((x, n) => `<li${n === i ? ' aria-current="step"' : ''}><button type="button" data-go="${n}" aria-label="Step ${n + 1}: ${esc(x.title)}"></button></li>`).join('')}</ol>
          <div class="demo-actions">${i ? '<button type="button" data-go="' + (i - 1) + '">Back</button>' : '<span></span>'}
            ${last ? '<button type="button" class="primary demo-done">Finish</button>'
                   : `<button type="button" class="primary" data-go="${i + 1}">Next<span class="demo-long">: ${esc(steps[i + 1].title)}</span></button>`}</div>`;
        card.querySelectorAll('[data-go]').forEach(b => b.onclick = () => go(Number(b.dataset.go)));
        card.querySelector('.demo-close').onclick = close;
        const done = card.querySelector('.demo-done');
        if (done) done.onclick = close;
      }
      document.body.classList.toggle('demo-touring', onPage);
      room();
      later();
    }
    function go(i) {
      if (steps[i].href && !here(steps[i].href)) {  // a hash link lands on hashchange, a page link on load
        step = i; scrolledTo = null; write(i);
        location.href = new URL(steps[i].href, document.baseURI).href;
        return;
      }
      show(i);
    }
    function close() {
      write('off');
      step = -1;
      card.hidden = chip.hidden = true;
      if (marked) marked.classList.remove('demo-target');
      marked = null;
      document.body.classList.remove('demo-touring');
      room();
    }
    chip.onclick = () => go(step);
    const open = banner.querySelector('.demo-open');
    if (open) open.onclick = () => go(Math.max(0, Number(read()) || 0));
    // A hash link does not reload the page: re-check whether this is the step's page.
    addEventListener('hashchange', () => { if (step >= 0) show(step); });
    const saved = read();
    if (steps.length && saved !== 'off') show(saved === null ? 0 : Math.min(Number(saved) || 0, steps.length - 1));

    // The kiosk asks for the device PIN: in a demo, fill it in and say so.
    const kiosk = document.getElementById('kiosk');
    if (kiosk && demo.kiosk_pin) {
      const fill = () => {
        const input = document.getElementById('k-pin-input');
        if (!input || input.dataset.demo) return;
        input.dataset.demo = '1';
        input.value = demo.kiosk_pin;
        const hint = document.createElement('p');
        hint.className = 'demo-hint';
        hint.textContent = `Demo PIN ${demo.kiosk_pin} is filled in. Tap "Open check-in".`;
        input.closest('form').after(hint);
      };
      new MutationObserver(fill).observe(kiosk, { childList: true, subtree: true });
      fill();
    }
  }).catch(() => { /* the demo layer is a help, never a blocker */ });
})();
