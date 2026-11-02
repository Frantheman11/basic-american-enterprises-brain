/* Product portfolio and focused connection map. Catalog facts stay separate from plans. */
window.StrategyProducts = (() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icon = key => StrategyArt.icon(key);
  const status = n => n.kind === 'suggestion' ? 'Suggested next' : n.kind === 'combo' ? ({live:'Works today',partial:'Partly today'})[n.status] || 'Planned' : n.kind === 'product' ? 'Planned' : n.status === 'built' ? 'Built' : 'To build';
  const linkStatus = edge => edge.type === 'implemented' ? 'Implemented' : edge.type === 'unverified' ? 'Verify build status' : edge.type === 'requires' ? 'Needed first' : edge.type === 'boosts' ? 'Works better with it' : 'Proposed link';
  const pick = (n, contents, attrs = '') => `<button type="button" data-action="product" data-id="${esc(n.id)}" ${attrs}>${contents}</button>`;
  const sources = rows => (rows || []).map(s => `<p class="st-source">${esc(s.path)}</p>`).join('');
  function compatibility(n){
    if(n.kind!=='module')return '';
    const c=n.connectivity||{},states={built:'Built',partial:'Partial',none:'Not built',na:'Not applicable'};
    const lanes=[['mcp','AI assistant · MCP'],['http_api','Software · API'],['identity','Program access'],['events_out','Events out']];
    return `<section class="st-product-compatibility" aria-label="Assistant and software access"><h3>Use with your second brain</h3><p>Recorded implementation, not a verified connection to your assistant.</p><dl>${lanes.map(([key,label])=>`<div><dt>${label}</dt><dd>${states[c[key]]||'Not assessed'}</dd></div>`).join('')}</dl><p>${esc(c.note||'Connection support has not been assessed for this module. Ask for a compatibility check.')}</p><small>Confirm the assistant, sign-in method and supported actions for each installation.</small></section>`;
  }

  /* Tech tree: what makes what possible. One fit-to-screen map, one expand/collapse outline.
     State lives here so a toggle re-renders only this section. */
  const TREE = {mode: window.innerWidth < 720 ? 'outline' : 'map', open: null, links: false, last: null, selected: ''};
  const stateClass = n => n.kind === 'suggestion' ? 'is-planned' : ['built', 'live'].includes(n.status) ? 'is-built' : n.status === 'partial' ? 'is-partial' : n.kind === 'module' ? 'is-future' : 'is-planned';
  function shape(tree) {
    const t = tree.tree || {}, byId = Object.fromEntries(tree.nodes.map(n => [n.id, n]));
    const ids = Object.keys(t.parent || {}).filter(id => byId[id]);
    const order = (a, b) => (stateClass(byId[a]) === 'is-built' ? 0 : 1) - (stateClass(byId[b]) === 'is-built' ? 0 : 1) || byId[a].name.localeCompare(byId[b].name);
    const children = {};
    ids.forEach(id => { const p = t.parent[id]; if (p) (children[p] = children[p] || []).push(id); });
    Object.values(children).forEach(list => list.sort(order));
    const also = {};
    Object.entries(t.requires || {}).forEach(([id, parents]) => parents.slice(1).forEach(p => (also[p] = also[p] || []).push(id)));
    return {t, byId, ids, children, also, order, roots: ids.filter(id => !t.parent[id]).sort(order)};
  }
  function chip(n) {
    return pick(n, `${icon(n.id)}<span><strong>${esc(n.name)}</strong><small>${esc(status(n))}</small></span>`,
      `class="st-unlock-node ${stateClass(n)}" data-unlock-node="${esc(n.id)}" aria-pressed="${n.id === TREE.selected}"`);
  }
  function outline(s, categories) {
    if (!TREE.open) TREE.open = new Set(s.roots.filter(id => s.children[id]));
    const name = id => esc(s.byId[id].name);
    const row = id => {
      const kids = s.children[id] || [], open = TREE.open.has(id), n = s.byId[id];
      const needs = (s.t.requires[id] || []).slice(1), better = s.t.boosts[id] || [];
      return `<li role="treeitem" ${kids.length ? `aria-expanded="${open}"` : ''}><div class="st-unlock-row">${kids.length
        ? `<button type="button" class="st-unlock-toggle" data-tree="toggle" data-id="${esc(id)}" aria-label="${open ? 'Collapse' : 'Expand'} ${name(id)}">${open ? '▾' : '▸'}<small>${kids.length}</small></button>`
        : '<span class="st-unlock-toggle is-leaf" aria-hidden="true">·</span>'}${chip(n)}
        <span class="st-unlock-notes">${needs.map(p => `<span class="st-unlock-need">+ needs ${name(p)}</span>`).join('')}${better.map(p => `<span class="st-unlock-boost">better with ${name(p)}</span>`).join('')}${(s.also[id] || []).map(k => `<button type="button" data-action="product" data-id="${esc(k)}" class="st-unlock-also">also unlocks ${name(k)} <small>with ${name(s.t.requires[k][0])}</small></button>`).join('')}</span></div>
        ${open && kids.length ? `<ul role="group">${kids.map(row).join('')}</ul>` : ''}</li>`;
    };
    return `<div class="st-unlock-outline">${categories.map(c => {
      const roots = s.roots.filter(id => s.byId[id].category === c.id);
      return roots.length ? `<section><h3>${esc(c.label)}</h3><ul role="tree" aria-label="${esc(c.label)}">${roots.map(row).join('')}</ul></section>` : '';
    }).join('')}</div>`;
  }
  function overview(s, categories) {
    const depths = [...new Set(s.ids.map(id => s.t.depth[id]))].sort((a, b) => a - b);
    const head = d => d === 0 ? 'Start here · works on its own' : d === 1 ? 'Unlocked by one step' : `Unlocked by ${d} steps`;
    const rows = categories.filter(c => s.ids.some(id => s.byId[id].category === c.id));
    return `<div class="st-unlock-map" id="unlock-map"><div class="st-unlock-canvas" style="grid-template-columns:150px ${depths.map(d => d < 2 ? '2fr' : '1fr').join(' ')}"><svg class="st-unlock-wires" aria-hidden="true"></svg>
      <span></span>${depths.map(d => `<h3>${head(d)}</h3>`).join('')}
      ${rows.map(c => `<h3 class="st-unlock-family">${esc(c.label)}</h3>${depths.map(d => `<div class="st-unlock-cell">${s.ids.filter(id => s.byId[id].category === c.id && s.t.depth[id] === d).sort(s.order).map(id => chip(s.byId[id])).join('')}</div>`).join('')}`).join('')}
    </div></div>`;
  }
  function unlockBody(tree) {
    const s = shape(tree), cats = tree.categories;
    const built = s.ids.filter(id => stateClass(s.byId[id]) === 'is-built').length;
    const flags = Object.entries(s.t.review_notes || {});
    return `<div class="st-unlock-controls"><div class="st-actions" role="group" aria-label="Tech tree view">
        <button type="button" data-tree="mode" data-id="map" aria-pressed="${TREE.mode === 'map'}">Whole tree</button>
        <button type="button" data-tree="mode" data-id="outline" aria-pressed="${TREE.mode === 'outline'}">Expand &amp; collapse</button></div>
      ${TREE.mode === 'outline' ? `<div class="st-actions"><button type="button" data-tree="all-open">Expand all</button><button type="button" data-tree="all-closed">Collapse all</button></div>`
        : `<label class="st-unlock-check"><input type="checkbox" data-tree="links" ${TREE.links ? 'checked' : ''}> Show "works better with" links</label>`}
      <div class="st-unlock-key"><span class="is-built">Built / works today</span><span class="is-partial">Partly today</span><span class="is-future">To build</span><span class="is-planned">Planned</span><span>━ needed first</span><span>┄ works better with</span></div></div>
      ${TREE.mode === 'map' ? overview(s, cats) : outline(s, cats)}
      <p class="st-unlock-foot">${s.ids.length} items · ${built} built or working today · ${esc(s.t.note || '')}</p>
      ${flags.length ? `<p class="st-callout">${flags.map(([id, note]) => `<strong>${esc(s.byId[id].name)}:</strong> ${esc(note)}`).join('<br>')}</p>` : ''}
      ${s.t.unlinked?.length ? `<p class="st-callout">Not connected to anything yet: ${s.t.unlinked.map(id => esc(s.byId[id]?.name || id)).join(', ')}.</p>` : ''}`;
  }
  function unlockTree(tree, selected) {
    TREE.last = tree; TREE.selected = selected;
    if (!tree.tree) return '';
    return `<section class="st-unlock" aria-labelledby="unlock-heading"><div class="st-product-section-head"><div><h2 id="unlock-heading">Tech tree</h2><small>What makes what possible. Each box builds on the ones to its left. Click any box to see its connections below.</small></div></div>
      <div id="unlock-body">${unlockBody(tree)}</div></section>`;
  }
  function drawTree() {
    const map = document.getElementById('unlock-map');
    if (!map || !TREE.last) return;
    const canvas = map.firstElementChild, svg = canvas.querySelector('svg');
    canvas.style.zoom = '1';
    const box = canvas.getBoundingClientRect(), W = canvas.offsetWidth, H = canvas.offsetHeight;
    const at = id => canvas.querySelector(`[data-unlock-node="${CSS.escape(id)}"]`)?.getBoundingClientRect();
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('width', W); svg.setAttribute('height', H);
    svg.innerHTML = TREE.last.edges.filter(e => e.need === 'requires' || (TREE.links && e.need === 'boosts')).map(e => {
      const a = at(e.from), b = at(e.to);
      if (!a || !b) return '';
      const forward = b.left > a.right - 4;
      const x1 = (forward ? a.right : a.left + a.width / 2) - box.left, y1 = (forward ? a.top + a.height / 2 : a.bottom) - box.top;
      const x2 = (forward ? b.left : b.left + b.width / 2) - box.left, y2 = (forward ? b.top + b.height / 2 : b.top) - box.top;
      const d = forward ? `M${x1} ${y1} C${(x1 + x2) / 2} ${y1},${(x1 + x2) / 2} ${y2},${x2} ${y2}` : `M${x1} ${y1} C${x1} ${y1 + 30},${x2} ${y2 - 30},${x2} ${y2}`;
      return `<path class="${e.need}" data-from="${esc(e.from)}" data-to="${esc(e.to)}" d="${d}"/>`;
    }).join('');
    // Fit the whole tree in one screen: the width of the panel and the height of the window.
    const scale = Math.max(0.5, Math.min(1, (map.clientWidth - 2) / W, (window.innerHeight - 140) / H));
    canvas.style.zoom = String(scale);
  }
  function light(id) {
    const map = document.getElementById('unlock-map');
    if (!map || !TREE.last) return;
    const near = new Set(id ? [id] : []);
    if (id) TREE.last.edges.filter(e => e.need && (e.from === id || e.to === id)).forEach(e => { near.add(e.from); near.add(e.to); });
    map.querySelectorAll('.st-unlock-wires path').forEach(p => p.classList.toggle('is-lit', !!id && (p.dataset.from === id || p.dataset.to === id)));
    map.classList.toggle('is-lighting', !!id);
    map.querySelectorAll('[data-unlock-node]').forEach(b => b.classList.toggle('is-lit', near.has(b.dataset.unlockNode)));
  }
  function refresh() {
    const body = document.getElementById('unlock-body');
    if (!body || !TREE.last) return;
    body.innerHTML = unlockBody(TREE.last); drawTree();
  }
  document.addEventListener('click', event => {
    const b = event.target.closest('[data-tree]');
    if (!b || b.tagName === 'INPUT' || !TREE.last) return;
    const kind = b.dataset.tree;
    if (kind === 'mode') TREE.mode = b.dataset.id;
    if (kind === 'toggle') TREE.open.has(b.dataset.id) ? TREE.open.delete(b.dataset.id) : TREE.open.add(b.dataset.id);
    if (kind === 'all-open') TREE.open = new Set(Object.keys(shape(TREE.last).children));
    if (kind === 'all-closed') TREE.open = new Set();
    refresh();
    document.querySelector(`#unlock-body [data-tree="${kind}"]${b.dataset.id ? `[data-id="${CSS.escape(b.dataset.id)}"]` : ''}`)?.focus({preventScroll: true});
  });
  document.addEventListener('change', event => { if (event.target.matches?.('[data-tree="links"]')) { TREE.links = event.target.checked; drawTree(); } });
  ['mouseover', 'focusin'].forEach(type => document.addEventListener(type, event => { const n = event.target.closest?.('#unlock-map [data-unlock-node]'); light(n ? n.dataset.unlockNode : ''); }));

  function render(tree, state) {
    const {category, selected, all, search, filter} = state;
    const nodes = tree.nodes, modules = nodes.filter(n => n.kind === 'module');
    const n = nodes.find(n => n.id === selected) || nodes[0];
    if (!n) return '<div class="st-empty">No modules have been catalogued yet.</div>';
    const ranked = (tree.recommendations || []).map(id => nodes.find(n => n.id === id)).filter(Boolean);
    const related = tree.edges.filter(e => e.from === n.id || e.to === n.id);
    const lane = incoming => {
      const edges = related.filter(e => (incoming ? e.to : e.from) === n.id);
      return `<div class="st-product-lane"><h3>${incoming ? 'Connects into this' : 'Connects onward'} <span aria-hidden="true">→</span></h3>${edges.map(edge => {
        const other = nodes.find(m => m.id === (incoming ? edge.from : edge.to));
        return pick(other, `${icon(other.id)}<span><strong>${esc(other.name)}</strong><small>${esc(status(other))}</small><span class="st-product-link-label">${esc(edge.label || 'Suggested combination')}</span><span class="st-product-link-state ${edge.type}">${linkStatus(edge)}</span>${edge.need && edge.need !== edge.type ? `<span class="st-product-link-state ${edge.need}">${linkStatus({type: edge.need})}</span>` : ''}</span>`, `class="st-product-link" data-product-node="${esc(other.id)}"`);
      }).join('') || '<p class="st-product-empty">No connection mapped yet.</p>'}</div>`;
    };
    const query = search.trim().toLowerCase();
    const rows = modules.filter(m => (category === 'all' || m.category === category)
      && (filter === 'all' || m.status === filter)
      && (!query || `${m.name} ${m.job} ${m.summary || ''}`.toLowerCase().includes(query)));
    const shown = all || query || category !== 'all' || filter !== 'all' ? rows : rows.filter(m => m.featured);
    const categoryName = id => tree.categories.find(c => c.id === id)?.label || 'Other modules';
    return `<section class="st-product-home" aria-label="Our product technology">
      <div class="st-product-summary"><span><strong>${modules.filter(m => m.status === 'built').length}</strong> built modules</span><span><strong>${modules.filter(m => m.status !== 'built').length}</strong> to build</span><span><strong>${ranked.length}</strong> next opportunities</span><small>Built from the catalog · installation and live health are separate</small></div>
      ${tree.missing_catalog_modules?.length ? `<p class="st-callout">Catalog records needed: ${tree.missing_catalog_modules.map(id => esc(id.replaceAll('-', ' '))).join(', ')}. Their connections will appear when those records are available.</p>` : ''}
      ${unlockTree(tree, n.id)}
      ${ranked.length ? `<section class="st-product-next" aria-labelledby="product-next-heading"><div class="st-product-section-head"><h2 id="product-next-heading">Build next</h2><small>Suggested order · click to explore</small></div><div class="st-product-recommendations">${ranked.map((m, i) => pick(m, `<span class="st-product-rank">${i + 1}</span><span><small>${esc(m.rank_label || 'Suggested build')}</small><strong>${esc(m.name)}</strong><span>${esc(m.summary || m.job)}</span></span><span aria-hidden="true">↗</span>`, `class="st-product-recommendation ${i === 0 ? 'is-first' : ''}" aria-pressed="${m.id === n.id}"`)).join('')}</div></section>` : ''}
      <section class="st-product-focus" aria-labelledby="product-focus-heading"><div class="st-product-section-head"><div><h2 id="product-focus-heading" tabindex="-1">How it connects</h2><small>Select any module to follow its connections.</small></div><div class="st-product-key"><span>━ Implemented</span><span>┄ Proposed</span>${related.some(e => e.type === "unverified") ? "<span>·· Verify build status</span>" : ""}</div></div>
        <div class="st-product-map" id="product-map"><svg class="st-product-wires" aria-hidden="true"></svg>${lane(true)}
          <article class="st-product-selected" data-product-node="${esc(n.id)}"><div class="st-product-selected-title">${icon(n.id)}<span class="st-pill ${n.status === 'built' ? 'st-good' : ''}">${status(n)}${n.priority ? ` · #${n.priority}` : ''}</span></div><h3>${esc(n.name)}</h3><p>${esc(n.summary || n.job)}</p>
            ${n.readiness_note ? `<p class="st-product-caution">${esc(n.readiness_note)}</p>` : ''}
            ${n.why ? `<h4>Why this next</h4><p>${esc(n.why)}</p><h4>First useful step</h4><p>${esc(n.next_step || n.job)}</p>` : `<small>${esc(categoryName(n.category))}</small>`}
          </article>${lane(false)}</div>
        ${compatibility(n)}
        <details class="st-product-proof"><summary>${n.kind === 'suggestion' ? 'Success check & planning evidence' : 'Build evidence & connection details'}</summary>
          ${n.acceptance ? `<h3>What success looks like</h3><p>${esc(n.acceptance)}</p><p>Suggested work; not committed or installed.</p>` : `<p>${esc(n.job)}</p><p>Version ${esc(n.version || 'not recorded')} · Gate: ${esc(n.gate?.last_run || 'No verification recorded')}</p><p>${esc(n.gate?.instruction || '')}</p>`}${sources(n.sources)}
          ${related.map(edge => `<div><h3>${esc(nodes.find(m => m.id === edge.from)?.name)} → ${esc(nodes.find(m => m.id === edge.to)?.name)}</h3><p>${linkStatus(edge)} · ${esc(edge.label || 'Suggested combination')}</p>${edge.review_note ? `<p>${esc(edge.review_note)}</p>` : ''}${sources(edge.sources)}</div>`).join('')}
          <small>${esc(tree.basis)}</small></details>
      </section>
      <section class="st-product-library" aria-labelledby="product-library-heading"><div class="st-product-section-head"><h2 id="product-library-heading">Explore the modules</h2><small>${shown.length} of ${modules.length} shown</small></div>
        <div class="st-controls"><label>Product family<select id="product-category"><option value="all">All systems</option>${tree.categories.map(c => `<option value="${esc(c.id)}" ${c.id === category ? 'selected' : ''}>${esc(c.label)}</option>`).join('')}</select></label><label>Build status<select id="product-status"><option value="all">All statuses</option><option value="built" ${filter === 'built' ? 'selected' : ''}>Built</option><option value="buildable" ${filter === 'buildable' ? 'selected' : ''}>To build</option></select></label><label>Find a module<input id="product-search" type="search" placeholder="Name or purpose" value="${esc(search)}"></label>${category === 'all' && filter === 'all' && !query ? `<button type="button" data-action="frontier" aria-pressed="${all}">${all ? 'Featured modules' : 'Show every module'}</button>` : ''}</div>
        <div class="st-product-catalog">${shown.map(m => pick(m, `${icon(m.id)}<span><strong>${esc(m.name)}</strong><small>${esc(categoryName(m.category))}</small></span><span class="st-product-build-state ${m.status === 'built' ? 'is-built' : ''}">${status(m)}</span>`, `class="st-product-tile ${m.status !== 'built' ? 'is-future' : ''}" aria-pressed="${m.id === n.id}"`)).join('') || '<p class="st-empty">No matching modules. Try another family or search.</p>'}</div>
      </section>
    </section>`;
  }

  function draw(tree, selected) {
    drawTree();
    const map = document.getElementById('product-map');
    if (!map) return;
    selected = map.querySelector('.st-product-selected')?.dataset.productNode || selected;
    const svg = map.querySelector('svg'), rect = map.getBoundingClientRect();
    svg.setAttribute('viewBox', `0 0 ${rect.width} ${rect.height}`);
    svg.innerHTML = tree.edges.filter(e => e.from === selected || e.to === selected).map(edge => {
      const a = map.querySelector(`[data-product-node="${CSS.escape(edge.from)}"]`);
      const b = map.querySelector(`[data-product-node="${CSS.escape(edge.to)}"]`);
      if (!a || !b) return '';
      const x = a.getBoundingClientRect(), y = b.getBoundingClientRect();
      const x1 = x.right - rect.left, y1 = x.top + x.height / 2 - rect.top;
      const x2 = y.left - rect.left, y2 = y.top + y.height / 2 - rect.top;
      const mid = (x1 + x2) / 2;
      return `<path class="${esc(edge.type)}" d="M${x1} ${y1} C${mid} ${y1},${mid} ${y2},${x2} ${y2}"/>`;
    }).join('');
  }
  return {render, draw};
})();
