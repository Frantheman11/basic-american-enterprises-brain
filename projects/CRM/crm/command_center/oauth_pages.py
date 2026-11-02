"""The OAuth door's three pages: consent, Connected apps, and a plain refusal (oauth.py).

Plain HTML, no script, a strict Content-Security-Policy, and nothing from another host. Every
value from an app (its name, its address) is escaped: an app names itself, so its name is data.
"""
import html
from urllib.parse import urlparse

AUTHORIZE, APPS = "/oauth/authorize", "/oauth/apps"
SCOPE_TEXT = {"crm:read": "Read your contacts, deals, tasks, notes and the daily brief",
              "crm:write": "Add contacts, notes and tasks, and complete tasks",
              "brain:read": "Search and read your brain repo (your rules, prices and processes). Read only"}


STYLE = """:root{--canvas:#eef0f4;--surface:#fafbfd;--ink:#16181d;--muted:#535b69;--line:#cdd2dd;--blue:#2f54e0;--warn-bg:#fff1d7;--warn:#7a4e0f;color-scheme:light}
@media(prefers-color-scheme:dark){:root{--canvas:#08090c;--surface:#11141a;--ink:#eef0f5;--muted:#a3abba;--line:#323947;--blue:#5b78ff;--warn-bg:#3a2a0c;--warn:#f3cf8a;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--canvas);color:var(--ink);font:400 18px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:620px;margin:6vh auto;padding:0 16px}.card{background:var(--surface);border:1px solid var(--line);border-top:3px solid var(--blue);border-radius:10px;padding:28px 24px;box-shadow:0 10px 30px rgba(0,0,0,.08)}
.eyebrow{font-size:14px;font-weight:600;letter-spacing:.1em;color:var(--muted);text-transform:uppercase;margin:0}h1{font-size:clamp(26px,5vw,34px);line-height:1.15;margin:10px 0 18px}
p{margin:0 0 14px}.muted{color:var(--muted)}dl{display:grid;grid-template-columns:max-content 1fr;gap:6px 16px;margin:18px 0;padding:16px;background:var(--canvas);border:1px solid var(--line);border-radius:8px}
dt{color:var(--muted)}dd{margin:0;overflow-wrap:anywhere;font-weight:600}fieldset{border:1px solid var(--line);border-radius:8px;margin:18px 0;padding:12px 16px}legend{font-weight:600;padding:0 6px}
label{display:flex;gap:12px;align-items:flex-start;padding:8px 0}input[type=checkbox]{width:20px;height:20px;margin-top:4px;accent-color:var(--blue);flex-shrink:0}small{display:block;color:var(--muted);font-size:16px}
.warn{background:var(--warn-bg);color:var(--warn);border-left:3px solid currentColor;padding:12px 14px}.never{font-size:16px;color:var(--muted)}
.actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:22px}button{font:600 17px system-ui,sans-serif;min-height:46px;padding:0 22px;border:1px solid var(--blue);border-radius:8px;cursor:pointer}
.allow{background:var(--blue);color:#fff}.deny{background:var(--surface);color:var(--ink);border-color:var(--line)}button:focus-visible{outline:3px solid #7d95ff;outline-offset:3px}
table{width:100%;border-collapse:collapse;margin:12px 0}td,th{text-align:left;padding:10px 6px;border-bottom:1px solid var(--line);vertical-align:top;font-size:16px}th{color:var(--muted);font-weight:600}
td form{margin:0}td button{min-height:38px;padding:0 14px;font-size:15px}.empty{color:var(--muted)}"""


def _e(value):
    return html.escape(str(value), quote=True)


def page(title, body, status=200, form_target="", extra=None):
    csp = (f"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'{(' ' + form_target) if form_target else ''}; "
           "frame-ancestors 'none'; base-uri 'none'")
    headers = {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
               "Referrer-Policy": "same-origin", "X-Frame-Options": "DENY", "Content-Security-Policy": csp, **(extra or {})}
    doc = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<meta name="robots" content="noindex"><title>{_e(title)}</title><style>{STYLE}</style></head>'
           f'<body><main><div class="card">{body}</div></main></body></html>')
    return status, headers, doc.encode("utf-8")


def problem(prof, title, message, status=400):
    return page(title, f'<p class="eyebrow">{_e(prof.get("name", "CRM"))}</p><h1>{_e(title)}</h1><p>{_e(message)}</p>'
                       '<p class="muted">You can close this window and start again from your Claude app.</p>', status)


def _consent_html(prof, d, req, client_name, handle, error=""):
    redirect_host = urlparse(req["redirect_uri"]).hostname or ""
    loopback = redirect_host in ("127.0.0.1", "localhost", "::1")
    boxes = "".join(f'<label><input type="checkbox" name="scope" value="{_e(s)}" checked><span>{_e(SCOPE_TEXT[s])}'
                    f'<small>{_e(s)}</small></span></label>' for s in req["scopes"])
    return (f'<p class="eyebrow">{_e(prof.get("name", "CRM"))} · Connect an app</p>'
            f'<h1>Allow {_e(client_name)} to use your CRM?</h1>'
            '<p class="muted">This app named itself. The name is not verified.</p>'
            f'<dl><dt>Business</dt><dd>{_e(prof.get("name", ""))}</dd><dt>Signed in as</dt><dd>{_e(req["person_name"])}</dd>'
            f'<dt>Access goes to</dt><dd>{_e(redirect_host)}</dd></dl>'
            + ('<p class="warn"><strong>This sends access to an app on your computer.</strong> Continue only if you just '
               'started connecting from Claude Code or another app on this computer.</p>' if loopback else '')
            + (f'<p class="warn" role="alert">{_e(error)}</p>' if error else '')
            + f'<form method="post" action="{_e(d.path(AUTHORIZE))}"><input type="hidden" name="handle" value="{_e(handle)}">'
            f'<fieldset><legend>What it can do</legend>{boxes}</fieldset>'
            '<p class="never">It acts as you, with what you can already do here. It can never delete, assign, approve '
            'or publish. You can remove it any time under Connected apps.</p>'
            '<div class="actions"><button class="allow" name="decision" value="approve">Allow</button>'
            '<button class="deny" name="decision" value="deny">Deny</button></div></form>')



def apps_page(prof, d, person, rows, notice=""):
    names = {m["id"]: m.get("name", m["id"]) for m in (prof.get("people") or {}).get("members", []) if isinstance(m, dict)}
    body = "".join(
        f'<tr><td><strong>{_e(r["client_name"])}</strong><small>{_e(r["redirect_host"])}</small></td>'
        f'<td>{_e(names.get(r["person_id"], r["person_id"]))}</td><td>{_e(r["scopes"])}</td>'
        f'<td>{_e((r["last_used_at"] or "never")[:10])}</td>'
        f'<td><form method="post" action="{_e(d.path(APPS))}"><input type="hidden" name="id" value="{_e(r["id"])}">'
        f'<button class="deny">Remove</button></form></td></tr>' for r in rows)
    table = (f'<table><thead><tr><th>App</th><th>Person</th><th>Can</th><th>Last used</th><th></th></tr></thead><tbody>{body}</tbody></table>'
             if rows else '<p class="empty">No apps are connected.</p>')
    return page("Connected apps",
                f'<p class="eyebrow">{_e(prof.get("name", "CRM"))}</p><h1>Connected apps</h1>'
                + (f'<p class="warn" role="status">{_e(notice)}</p>' if notice else '')
                + '<p>Claude apps you allowed to use this CRM. Remove one and it stops at once.</p>'
                f'{table}<p class="muted">To connect Claude, add <strong>{_e(d.resource)}</strong> as a custom connector '
                'in Claude (Settings, Connectors), then sign in and press Allow.</p>'
                f'<p><a href="{_e(d.path("/"))}">Back to the CRM</a></p>')
