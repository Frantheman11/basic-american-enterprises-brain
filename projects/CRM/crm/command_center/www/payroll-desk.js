// Prevailing Wage Desk: the page is rendered on the server; this only sends its forms as JSON and reloads.
(function () {
  "use strict";
  var msg = document.getElementById("pw-msg");
  var paths = {clear: "api/payroll-desk/clear", review: "api/payroll-desk/review", filed: "api/payroll-desk/filed",
               "import": "api/payroll-desk/import"};

  function send(path, body) {
    return fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
                        credentials: "same-origin"})
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) { throw new Error(j.error || "Failed"); } return j; }); });
  }

  function fields(form) {
    var out = {};
    Array.prototype.forEach.call(form.elements, function (el) { if (el.name && el.type !== "file") { out[el.name] = el.value; } });
    return out;
  }

  document.addEventListener("submit", function (ev) {
    var form = ev.target, kind = form.getAttribute("data-pw");
    if (!kind) { return; }
    ev.preventDefault();
    var go = function (body) {
      form.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
      send(paths[kind], body).then(function () { location.reload(); }).catch(function (err) {
        msg.textContent = err.message;
        form.querySelectorAll("button").forEach(function (b) { b.disabled = false; });
      });
    };
    if (kind === "import") {
      var file = form.elements.file.files[0];
      if (!file) { return; }
      file.text().then(function (text) { go({kind: form.elements.kind.value, text: text, name: file.name}); });
    } else {
      go(fields(form));
    }
  });

  document.addEventListener("click", function (ev) {
    var b = ev.target.closest("[data-copy]");
    if (!b) { return; }
    var box = b.closest("details");
    var text = box.querySelector("p").innerText + "\n\n" + box.querySelector("pre").innerText;
    navigator.clipboard.writeText(text).then(function () { b.textContent = "Copied"; });
  });
})();
