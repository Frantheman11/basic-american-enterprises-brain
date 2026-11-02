/* Freshness, not "real time".
 *
 * The page asks /api/view with If-None-Match. When nothing has changed the server
 * answers 304 and this costs one header exchange and no rendering. When something has
 * changed, the view's HTML is swapped in place and the open/closed state of every
 * <details> is restored, so a poll never collapses what you were reading.
 *
 * No websocket, no dependency, and it cannot wedge. Revisit only if a measured need
 * appears. */

(function () {
  "use strict";
  var live = document.getElementById("cc-live");
  var host = document.getElementById("cc-view");
  if (!live || !host) return;

  var view = live.dataset.view;
  var etag = live.dataset.etag;
  var every = 4000;
  var misses = 0;

  function openIds() {
    var out = [];
    host.querySelectorAll("details[open]").forEach(function (d) { if (d.id) out.push(d.id); });
    return out;
  }

  function restore(ids) {
    ids.forEach(function (id) {
      var el = host.querySelector('[id="' + (window.CSS && CSS.escape ? CSS.escape(id) : id) + '"]');
      if (el && el.tagName === "DETAILS") el.open = true;
    });
  }

  function tick() {
    fetch("/api/view?view=" + encodeURIComponent(view), {
      headers: { "If-None-Match": etag }, cache: "no-store"
    }).then(function (r) {
      if (r.status === 304) { misses = 0; live.className = "cc-live mono"; return null; }
      if (!r.ok) throw new Error(r.status);
      var tag = r.headers.get("ETag");
      if (tag) etag = tag.replace(/"/g, "");
      return r.text();
    }).then(function (html) {
      if (html === null || html === undefined) return;
      // A canvas view draws into its own DOM and refreshes itself. Swapping the HTML
      // out from under it would detach every node board.js holds a reference to, and
      // the board would quietly stop updating while still looking live.
      if (host.querySelector(".cc-board")) { misses = 0; live.className = "cc-live mono"; return; }
      var was = openIds();
      host.innerHTML = html;
      restore(was);
      misses = 0;
      live.className = "cc-live mono";
    }).catch(function () {
      misses += 1;
      live.className = misses > 2 ? "cc-live mono off" : "cc-live mono stale";
    });
  }

  setInterval(tick, every);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) tick();   // a phone coming back from sleep should not wait
  });

  /* The approvals queue. A decision records a STATUS — it never performs the action, so
   * there is no confirm step and no undo to build: the next run reads the status, and a
   * wrong decision is corrected by pressing a different button. */
  host.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-decide]");
    if (!btn) return;
    e.preventDefault();
    var box = btn.closest(".cc-decide");
    var msg = box ? box.querySelector(".cc-decide-msg") : null;
    var all = box ? box.querySelectorAll("[data-decide]") : [btn];
    all.forEach(function (b) { b.disabled = true; });
    if (msg) msg.textContent = "saving…";

    fetch("/api/decide", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        record: btn.dataset.record,
        id: btn.dataset.id,
        decision: btn.dataset.decide
      })
    }).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, body: j }; });
    }).then(function (res) {
      if (!res.ok) throw new Error(res.body.error || "refused");
      if (msg) msg.textContent = "saved — " + btn.dataset.decide.replace("-", " ");
      etag = "";      // force the next poll to re-render rather than answer 304
      tick();
    }).catch(function (err) {
      // A refusal names the field and says who owns it. Show it rather than swallow it.
      if (msg) msg.textContent = String(err.message || err);
      all.forEach(function (b) { b.disabled = false; });
    });
  });
})();
