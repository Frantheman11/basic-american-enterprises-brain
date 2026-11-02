/* The Board — a spine of departments, one expanded onto a pan/zoom canvas.
 *
 * The shape is Beam's command center, because Beam's is proved: roots down the left, the
 * selected one expanded left-to-right, status and money ON the nodes, AI recommendations
 * drawn as ghost branches on the same tree instead of in a separate list, and a side panel
 * whose last element is an action.
 *
 * The server decides what the tree IS and hands it over as JSON on /api/state. This file
 * only decides where to put it. That split is why there is no second store: the board is
 * one more rendering of the same files every other view reads.
 */

(function () {
  "use strict";

  var board = document.getElementById("cc-board");
  if (!board) return;

  var svg = document.getElementById("cc-canvas");
  var vp = document.getElementById("cc-viewport");
  var edges = document.getElementById("cc-edges");
  var nodes = document.getElementById("cc-nodes");
  var spine = document.getElementById("cc-spine");
  var panel = document.getElementById("cc-panel");
  var empty = document.getElementById("cc-empty");
  var hud = document.getElementById("cc-hud");
  var NS = "http://www.w3.org/2000/svg";

  // Column geometry. Three columns is what the data actually has depth for; a fourth
  // level is reached by expanding a node in place rather than by another column, which
  // is what keeps the canvas readable instead of endlessly wide.
  var W = [270, 250, 300], H = [78, 60, 56], GAPX = 78, GAPY = 12;

  var state = { tree: [], pick: null, open: {}, sel: null, who: null, k: 1, x: 0, y: 0 };

  function el(tag, attrs) {
    var n = document.createElementNS(NS, tag);
    for (var k in attrs) if (attrs[k] !== undefined && attrs[k] !== "") n.setAttribute(k, attrs[k]);
    return n;
  }
  function txt(cls, x, y, s) {
    var t = el("text", { class: cls, x: x, y: y });
    t.textContent = s;
    return t;
  }
  function clip(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; }

  /* ---- data ------------------------------------------------------------------ */

  // `first` fits the view; a refresh must not, or the canvas jumps under someone reading
  // it. Nothing else is saved or restored here: a refresh replaces the TREE, and which
  // department is picked, what is expanded and what is selected are all view state that
  // load() has no business touching.
  //
  // ⚠️ An earlier version captured and restored those three around the fetch. It looked
  // careful and was wrong: drawSpine() ran inside load() with the live pick, then the
  // restore overwrote state.pick without redrawing the spine, so the highlighted
  // department and the drawn tree disagreed until the next click.
  function load(first) {
    return fetch("/api/state", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (s) {
        var v = s.views && s.views[board.dataset.view];
        state.tree = (v && v.nodes) || [];
        if (!state.pick && state.tree.length) {
          // Open on the department with something stuck, else the first with any work.
          var stuck = state.tree.filter(function (d) { return count(d, "blocked") > 0; });
          var work = state.tree.filter(function (d) { return (d.children || []).length; });
          state.pick = (stuck[0] || work[0] || state.tree[0]).id;
        }
        drawSpine();
        drawFilter();
        drawTree(!!first);
      })
      .catch(function () { if (empty) empty.textContent = "Could not load the board."; });
  }

  function walk(n, fn) {
    fn(n);
    (n.children || []).forEach(function (k) { walk(k, fn); });
  }
  function count(n, status) {
    var c = 0;
    walk(n, function (k) { if (k !== n && k.status === status) c++; });
    return c;
  }
  function find(id) {
    var hit = null;
    state.tree.forEach(function (d) { walk(d, function (n) { if (n.id === id) hit = n; }); });
    return hit;
  }

  /* ---- spine ------------------------------------------------------------------ */

  function drawFilter() {
    var host = document.getElementById("cc-filter");
    if (!host) return;
    var seen = {};
    state.tree.forEach(function (d) {
      walk(d, function (n) { if (n.owner) seen[n.owner] = (seen[n.owner] || 0) + 1; });
    });
    var ids = Object.keys(seen);
    if (!ids.length) { host.hidden = true; return; }
    host.hidden = false;
    var opts = ['<option value="">Everyone (' + ids.reduce(function (a, k) { return a + seen[k]; }, 0) + ")</option>"];
    ids.sort().forEach(function (k) {
      opts.push('<option value="' + esc(k) + '"' + (state.who === k ? " selected" : "") +
                ">" + esc(k) + " (" + seen[k] + ")</option>");
    });
    host.innerHTML = '<label for="cc-who">Showing</label><select id="cc-who">' + opts.join("") + "</select>";
    document.getElementById("cc-who").addEventListener("change", function (e) {
      state.who = e.target.value || null;
      drawSpine(); drawTree(true);
    });
  }

  // Filtering happens at DRAW time against a copy, never by editing state.tree — the tree
  // is what the server said, and a filter that mutates it makes the next refresh the only
  // way to get your other leads back.
  function visible(n) {
    if (!state.who) return n;
    var kids = (n.children || []).map(visible).filter(Boolean);
    if (n.owner && n.owner !== state.who && !kids.length) return null;
    if (!n.owner && !kids.length && (n.children || []).length) return null;
    return kids.length === (n.children || []).length ? n : Object.assign({}, n, { children: kids });
  }

  function drawSpine() {
    spine.innerHTML = "";
    state.tree.forEach(function (d) {
      var kids = (d.children || []).length;
      var b = document.createElement("button");
      b.type = "button";
      b.className = "cc-sp" + (d.id === state.pick ? " cc-sp-on" : "") + (kids ? "" : " cc-sp-off");
      var stuck = count(d, "blocked"), rec = count(d, "ghost"), run = count(d, "active") + count(d, "in_progress");
      var bits = [];
      // The count comes first and always. Without it a department full of ready work
      // read "nothing filed yet", which is the collapsed row lying about an empty state.
      if (kids) bits.push(kids + (kids === 1 ? " item" : " items"));
      if (run) bits.push(run + " running");
      if (stuck) bits.push('<i class="cc-dot cc-d-blocked"></i>' + stuck + " stuck");
      if (rec) bits.push('<i class="cc-dot cc-d-ghost"></i>' + rec + " rec");
      b.innerHTML = '<span class="cc-sp-n">' + esc(d.label) + "</span>" +
        '<span class="cc-sp-m">' + (bits.join(" · ") || "nothing filed yet") + "</span>";
      b.addEventListener("click", function () {
        state.pick = d.id; state.sel = null; panel.innerHTML = "";
        drawSpine(); drawTree(true);
      });
      spine.appendChild(b);
    });
  }

  function esc(s) {
    return String(s === undefined || s === null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  /* ---- layout ----------------------------------------------------------------- */

  // Children of an open node are laid out under it; a closed node takes one row. The
  // parent centres on its children, which is what makes the bezier read as a branch
  // rather than a wire.
  function layout(n, depth, cursor) {
    var h = H[Math.min(depth, H.length - 1)];
    var open = state.open[n.id] && (n.children || []).length;
    if (!open) {
      var box = { n: n, depth: depth, y: cursor.y, h: h, kids: [] };
      cursor.y += h + GAPY;
      return box;
    }
    var kids = (n.children || []).map(function (k) { return layout(k, depth + 1, cursor); });
    var first = kids[0], last = kids[kids.length - 1];
    var mid = (first.y + last.y + last.h) / 2 - h / 2;
    return { n: n, depth: depth, y: mid, h: h, kids: kids };
  }

  function drawTree(refit) {
    edges.innerHTML = ""; nodes.innerHTML = "";
    var dept = find(state.pick);
    if (!dept || !(dept.children || []).length) {
      if (empty) { empty.hidden = false; empty.textContent = dept ? (dept.label + " has nothing filed yet.") : "Pick a department."; }
      if (hud) hud.hidden = true;
      return;
    }
    if (empty) empty.hidden = true;

    var cursor = { y: 0 };
    var shown = (dept.children || []).map(visible).filter(Boolean);
    if (!shown.length) {
      if (empty) { empty.hidden = false; empty.textContent = "Nothing here for " + state.who + "."; }
      return;
    }
    var roots = shown.map(function (c) { return layout(c, 0, cursor); });
    var total = Math.max(cursor.y - GAPY, H[0]);

    if (hud) {
      hud.hidden = false;
      document.getElementById("cc-hud-t").textContent = dept.label;
      var run = count(dept, "active") + count(dept, "in_progress");
      document.getElementById("cc-hud-s").textContent =
        run + " running · " + count(dept, "blocked") + " stuck · " +
        count(dept, "ghost") + " recommended · " + count(dept, "done") + " done";
    }

    roots.forEach(function (b) { paint(b, 0); });

    var wide = 0;
    (function deepest(b) {
      wide = Math.max(wide, xOf(b.depth) + W[Math.min(b.depth, W.length - 1)]);
      b.kids.forEach(deepest);
    })({ depth: 0, kids: roots });

    if (refit !== false) fit(wide, total);
  }

  function xOf(depth) {
    var x = 0;
    for (var i = 0; i < depth; i++) x += W[Math.min(i, W.length - 1)] + GAPX;
    return x;
  }

  function paint(box, depth) {
    var n = box.n, d = Math.min(depth, W.length - 1);
    var x = xOf(depth), w = W[d], h = box.h;
    var hasKids = (n.children || []).length;
    var open = !!state.open[n.id];

    box.kids.forEach(function (kb) {
      var kx = xOf(depth + 1);
      var mx = (x + w + kx) / 2;
      var p = el("path", {
        class: "cc-edge" + (kb.n.status === "ghost" ? " cc-e-ghost" : (kb.n.status === "done" ? " cc-e-done" : "")),
        d: "M " + (x + w) + " " + (box.y + h / 2) + " C " + mx + " " + (box.y + h / 2) +
           ", " + mx + " " + (kb.y + kb.h / 2) + ", " + kx + " " + (kb.y + kb.h / 2)
      });
      edges.appendChild(p);
    });

    var g = el("g", {
      class: "cc-n cc-s-" + (n.status || "ready") + (state.sel === n.id ? " cc-n-sel" : ""),
      transform: "translate(" + x + "," + box.y + ")", tabindex: "0", role: "button"
    });
    g.appendChild(el("rect", { class: "cc-n-box", width: w, height: h, rx: 10 }));

    var pad = 13, ty = depth === 0 ? 26 : 23;
    // The title gets the full width: the badge sits on the meta line below it. When the
    // badge shared the title line it overlapped the name, and a clipped name on the one
    // row you read first is the worst place to save space.
    g.appendChild(txt("cc-n-t", pad, ty, clip(n.label, Math.floor((w - 2 * pad) / 7.3))));
    // The badge shares this line, so the meta has to stop where the badge starts —
    // otherwise a long "Website + SEO + the dashboard and CRM" runs straight under a
    // count and both become unreadable.
    var badgeW = n.badge ? Math.min(String(n.badge).length * 6.4 + 14, w / 2) + 8 : 0;
    var line2 = n.meta || "";
    if (line2) g.appendChild(txt("cc-n-m", pad, ty + 17,
                                 clip(line2, Math.max(8, Math.floor((w - 2 * pad - badgeW) / 6.1)))));
    if (n.money) g.appendChild(txt("cc-n-$", pad, ty + 35, clip(n.money, 30)));
    else if (n.status === "ghost" && n.confidence)
      g.appendChild(txt("cc-n-c", pad, ty + 34, "AI " + Math.round(n.confidence * 100) + "% confidence"));

    if (n.badge) {
      var bw = Math.min(String(n.badge).length * 6.4 + 14, w / 2);
      if (!n.meta) bw = Math.min(bw, w - 2 * pad);
      var bg = el("g", { class: "cc-n-b", transform: "translate(" + (w - pad - bw) + "," + (ty + 5) + ")" });
      bg.appendChild(el("rect", { width: bw, height: 18, rx: 9 }));
      bg.appendChild(txt("", bw / 2, 13, clip(n.badge, Math.floor(bw / 6.4))));
      bg.querySelector("text").setAttribute("text-anchor", "middle");
      g.appendChild(bg);
    }

    if (hasKids) {
      var cx = w - 15, cy = h - 13;
      var t = el("g", { class: "cc-n-x" + (open ? " cc-n-x-on" : ""), transform: "translate(" + cx + "," + cy + ")" });
      t.appendChild(el("circle", { r: 9 }));
      t.appendChild(txt("", 0, 4, open ? "−" : "+"));
      t.querySelector("text").setAttribute("text-anchor", "middle");
      g.appendChild(t);
    }

    function activate(e) {
      e.stopPropagation();
      state.sel = n.id;
      // Expanding changes the tree's extent, so the view has to be refitted or the
      // branches unfold past the edge of the canvas and the board looks empty. Pure
      // selection must NOT refit — moving the canvas under someone who only clicked to
      // read something is worse than a tight fit.
      var structural = !!hasKids;
      if (structural) state.open[n.id] = !state.open[n.id];
      drawTree(structural);
      showPanel(n);
      // The panel takes a column when it opens, which changes the canvas width. Refit on
      // the next frame, once the grid has actually resized.
      if (structural) requestAnimationFrame(function () { drawTree(true); });
    }
    g.addEventListener("click", activate);
    g.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(e); }
    });
    nodes.appendChild(g);

    box.kids.forEach(function (kb) { paint(kb, depth + 1); });
  }

  /* ---- side panel -------------------------------------------------------------- */

  // ⚠️ `ready` is not one word. On a task it means "not started"; on a skill or a note it
  // means the thing exists and is fine — and it read "not started" over an index of 47
  // notes, which is the status lying about a shelf that is full.
  var LABEL = {
    active: "running", in_progress: "in progress", blocked: "stuck", ghost: "recommended",
    done: "done", ready: "not started", locked: "gated"
  };
  var READY = { task: "not started", section: "ready", skill: "in the kit",
                memory: "written down", knowledge: "written down", tool: "ready to run",
                workflow: "not built yet", metric: "passing", record: "open",
                department: "ready", doc: "written", agent: "idle", artifact: "filed",
                system: "live", data: "on disk", asset: "on disk" };

  function statusLabel(n) {
    if (n.status === "ready") return READY[n.kind] || "ready";
    return LABEL[n.status] || n.status;
  }

  function showPanel(n) {
    var p = n.detail || {};
    var h = ['<button class="cc-x" id="cc-x" type="button" aria-label="Close">×</button>'];
    h.push('<span class="cc-pill cc-s-' + esc(n.status) + '">' + esc(statusLabel(n)) + "</span>");
    h.push("<h2>" + esc(n.label) + "</h2>");
    if (n.meta) h.push('<p class="cc-crumb">' + esc(n.meta) + "</p>");
    if (n.money) h.push('<p class="cc-money">' + esc(n.money) + "</p>");
    if (n.status === "ghost") {
      h.push("<h3>Why this is recommended</h3>");
      h.push('<p class="cc-d">' + esc(n.rationale || p.summary || "") + "</p>");
      if (n.confidence) h.push('<p class="cc-conf mono">AI ' + Math.round(n.confidence * 100) + "% confidence</p>");
    } else if (p.summary) {
      h.push('<p class="cc-d">' + esc(p.summary) + "</p>");
    }
    if (p.fields) {
      h.push('<dl class="cc-fl">');
      for (var k in p.fields) h.push("<dt>" + esc(k) + "</dt><dd>" + esc(p.fields[k]) + "</dd>");
      h.push("</dl>");
    }
    if (p.bullets && p.bullets.length) {
      h.push("<ul class='cc-bl'>");
      p.bullets.forEach(function (b) { h.push("<li>" + esc(b) + "</li>"); });
      h.push("</ul>");
    }

    // The action. ⚠️ It never performs anything — it writes a status, and the next run
    // reads it. That separation is what makes this safe on a phone, and it is why there
    // is no "send it" button anywhere on this board.
    var act = actionFor(n);
    if (act) {
      h.push('<div class="cc-act"><button class="cc-go" type="button" id="cc-go">' + esc(act.label) + "</button>");
      h.push('<p class="cc-fine">' + esc(act.note) + "</p></div>");
    }
    if (n.source) h.push('<p class="cc-src mono">' + esc(n.source) + "</p>");
    panel.innerHTML = h.join("");
    panel.classList.add("cc-panel-on");
    var x = document.getElementById("cc-x");
    if (x) x.addEventListener("click", function () {
    panel.classList.remove("cc-panel-on"); panel.innerHTML = ""; state.sel = null;
    requestAnimationFrame(function () { drawTree(true); });
  });
    var go = document.getElementById("cc-go");
    if (go) go.addEventListener("click", function () { doAction(n, act, go); });
  }

  function actionFor(n) {
    if (n.status === "ghost")
      return { kind: "queue", label: "⊕ Add to the plan", note: "Marks it for the next run. Nothing is built or sent by pressing this." };
    if (n.status === "blocked")
      return { kind: "queue", label: "⊕ Put it on the list", note: "Adds it to the queue so it stops living only in your head. Nothing is sent or built." };
    return null;
  }

  function doAction(n, act, btn) {
    btn.disabled = true;
    btn.textContent = "…";
    // Both routes go through records.py. A board that wrote its own files would be a
    // second writer, which is exactly what one-writer-per-field forbids — and a test
    // asserts no view, renderer or shell touches disk.
    fetch("/api/queue", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        node: n.id, label: n.label, source: n.source,
        kind: n.kind, why: n.rationale || (n.detail && n.detail.summary) || ""
      })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        btn.textContent = res.ok ? "✓ recorded" : "not recorded";
        if (!res.ok) {
          var fine = btn.parentNode.querySelector(".cc-fine");
          if (fine) fine.textContent = (res.j && res.j.error) || "The writer refused it.";
        }
      })
      .catch(function () { btn.textContent = "failed"; btn.disabled = false; });
  }

  /* ---- pan / zoom -------------------------------------------------------------- */

  function apply() {
    vp.setAttribute("transform", "translate(" + state.x + "," + state.y + ") scale(" + state.k + ")");
  }
  // The HUD sits top-left and does not scroll, so the tree has to start below it or the
  // first node lands on top of the one line that says what you are looking at.
  var TOP = 62, PAD = 44;
  function fit(w, h) {
    var r = svg.getBoundingClientRect();
    if (!r.width || !w) return;
    var k = Math.min((r.width - PAD * 2) / w, (r.height - TOP - PAD) / h, 1);
    // ⚠️ A floor, not a best fit. Twenty-one cards squeezed to 28% is a PICTURE of a
    // pipeline rather than a pipeline — you cannot read a name. Below this the view stops
    // shrinking and starts scrolling, which is the honest trade.
    state.k = Math.max(0.62, Math.min(k, 1));
    state.x = PAD;
    state.y = Math.max(TOP, TOP + (r.height - TOP - PAD - h * state.k) / 2);
    apply();
  }

  svg.addEventListener("wheel", function (e) {
    e.preventDefault();
    var r = svg.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
    var k = Math.max(0.2, Math.min(2.2, state.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
    state.x = mx - (mx - state.x) * (k / state.k);
    state.y = my - (my - state.y) * (k / state.k);
    state.k = k; apply();
  }, { passive: false });

  var drag = null;
  svg.addEventListener("pointerdown", function (e) {
    if (e.target.closest(".cc-n")) return;
    drag = { x: e.clientX - state.x, y: e.clientY - state.y };
    svg.classList.add("cc-grab");
    svg.setPointerCapture(e.pointerId);
  });
  svg.addEventListener("pointermove", function (e) {
    if (!drag) return;
    state.x = e.clientX - drag.x; state.y = e.clientY - drag.y; apply();
  });
  ["pointerup", "pointercancel"].forEach(function (ev) {
    svg.addEventListener(ev, function () { drag = null; svg.classList.remove("cc-grab"); });
  });

  var zoom = document.getElementById("cc-zoom");
  if (zoom) zoom.addEventListener("click", function (e) {
    var b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.z === "fit") return drawTree(true);
    var k = Math.max(0.2, Math.min(2.2, state.k * (b.dataset.z === "in" ? 1.2 : 1 / 1.2)));
    state.k = k; apply();
  });

  window.addEventListener("resize", function () { drawTree(true); });

  load(true);
  // The board follows the same freshness rule as every other view: ask, and do nothing
  // when nothing changed.
  setInterval(function () { load(false); }, 15000);
})();
