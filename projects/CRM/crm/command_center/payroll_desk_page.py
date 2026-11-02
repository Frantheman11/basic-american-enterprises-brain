"""Prevailing Wage Desk: the screens, rendered on the server from payroll_desk_store.state().

Wording rule (tested on every rendered page, tests/test_payroll_desk.py): the desk reviews, flags and
prepares. It never calls anything compliant, approved or guaranteed, and "certif-" appears only in
"certified payroll" and in the line that says the client's officer certifies.
"""
import html

from . import crm_page, payroll_desk as pd

e = lambda v: html.escape(str(v if v is not None else ""), quote=True)
DECIDES = {"client": "You decide", "counsel or DIR": "Counsel or DIR"}


def _head(prof, title):
    name = e(prof["name"])
    prefix = e((prof.get("crm") or {}).get("path_prefix", "/"))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><base href="{prefix}">
<meta name="robots" content="noindex,nofollow"><title>{name} | {e(title)}</title>
<link rel="stylesheet" href="payroll-desk.css"><script defer src="payroll-desk.js"></script></head><body class="pw">"""


def _banner(prof):
    cfg = pd.config(prof) or {}
    sample = (f'<p class="pw-sample" role="note"><strong>Sample data.</strong> {e(cfg.get("sample_note") or "")}</p>'
              if cfg.get("sample") else "")
    return sample + f'<p class="pw-legal" role="note">{e(pd.OFFICER_LINE)}</p>'


def _kpis(k):
    days = "none yet" if k["days_to_file"] is None else f'{k["days_to_file"]:g}'
    tiles = [("Lines checked", k["lines_checked"], "payroll lines reviewed against the rates you entered"),
             ("Flags found", k["flags_found"], "every line or week the desk flagged"),
             ("Flags cleared", k["flags_cleared"], "cleared with a note by your team"),
             ("Flags open", k["flags_open"], f'{pd.money(k["open_gap"])} in open rate gaps'),
             ("Days to file", days, "average days from week end to your officer's filing"),
             ("Sub reports missing", k["sub_reports_missing"], "weekly reports past due from subcontractors")]
    return '<section class="pw-kpis" aria-label="Key numbers">' + "".join(
        f'<div class="pw-kpi"><span>{e(t)}</span><strong>{e(v)}</strong><small>{e(s)}</small></div>' for t, v, s in tiles) + "</section>"


def _flag_rows(flags, can_clear):
    rows = []
    for f in flags:
        if f["cleared"]:
            state = f'<span class="pw-ok">Cleared</span><small>{e(f["cleared"]["note"])} ({e(f["cleared"]["by"])})</small>'
        elif can_clear:
            state = (f'<form class="pw-clear" data-pw="clear"><input type="hidden" name="key" value="{e(f["key"])}">'
                     f'<label class="pw-sr" for="n{e(f["key"])}">Note</label><input id="n{e(f["key"])}" name="note" '
                     'placeholder="Why it can be cleared" required minlength="5"><button>Clear</button></form>')
        else:
            state = '<span class="pw-flag">Open</span>'
        gap = pd.money(f["gap"]) if f["gap"] else ""
        rows.append(f'<tr data-code="{e(f["code"])}"><td>{e(f["project_id"])}</td><td>{e(f["week_ending"])}</td>'
                    f'<td><strong>{e(f["title"])}</strong><small>{e(f["detail"])}</small></td><td>{e(gap)}</td>'
                    f'<td>{e(DECIDES[f["decides"]])}</td><td>{state}</td></tr>')
    return "".join(rows) or '<tr><td colspan="6">No flags.</td></tr>'


def _flags(view):
    flags = sorted(view["flags"], key=lambda f: (bool(f["cleared"]), f["project_id"], f["week_ending"], f["code"]))
    return (f'<section id="flags"><h2>Flags to review</h2><p class="pw-hint">Each flag is something for a person to '
            f'look at, not a finding. Fix the line in payroll and import again, or clear it with a note. {e(pd.COUNSEL)}</p>'
            '<div class="pw-scroll"><table><thead><tr><th>Project</th><th>Week ending</th><th>Flag</th><th>Gap</th>'
            f'<th>Who decides</th><th>Status</th></tr></thead><tbody>{_flag_rows(flags, view["can"]["review"])}</tbody>'
            '</table></div></section>')


def _deadlines(view):
    rows = []
    for d in view["deadlines"]:
        left = "" if d["days_left"] is None else f' <small>({d["days_left"]} days)</small>'
        note = ' <small>Contract value at or under $25,000 as entered: ask counsel or DIR whether eCPR applies.</small>' if d["under_threshold"] else ""
        rows.append(f'<tr><td><strong>{e(d["project_id"])}</strong><small>{e(d["name"])}</small>{note}</td>'
                    f'<td>{e(d["last_filed"] or "none yet")}</td><td>{e(d["next_due"] or "set first day of work")}{left}</td>'
                    f'<td>{e(d["final_due"] or "after the last day of work")}</td><td>{e(", ".join(d["open_weeks"]) or "none")}</td></tr>')
    return ('<section id="calendar"><h2>Filing calendar</h2><p class="pw-hint">Payroll records go to DIR at least every 30 '
            'days while work runs (more often if the contract says so) and within 30 days after the last day of work. '
            'Your officer files them; these dates are a reminder, worked out from the dates you entered.</p>'
            '<div class="pw-scroll"><table><thead><tr><th>Project</th><th>Last filing</th><th>Next filing by</th>'
            f'<th>Final filing by</th><th>Weeks not yet filed</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>')


def _apprentices(view):
    rows = "".join(f'<tr class="{"pw-short" if r["short"] else ""}"><td>{e(r["project_id"])}</td><td>{e(r["craft"])}</td>'
                   f'<td>{e(r["week_ending"])}</td><td>{r["journey"]:g}</td><td>{r["apprentice"]:g}</td>'
                   f'<td>{"Below 1 to 5" if r["short"] else "1 to 5 or better"}</td></tr>' for r in view["ratios"])
    das = []
    for p in view["projects"]:
        sent = ", ".join(f'{d["craft"]} ({d.get("sent_on", "")})' for d in p.get("das140") or []) or "none on file"
        asked = ", ".join(f'{d["craft"]} ({d.get("requested_on", "")})' for d in p.get("das142") or []) or "none on file"
        das.append(f'<tr><td>{e(p["id"])}</td><td>{e(sent)}</td><td>{e(asked)}</td></tr>')
    return ('<section id="apprentices"><h2>Apprentice hours</h2><p class="pw-hint">One apprentice hour for every five '
            'journeyperson hours, per craft, on contracts of $30,000 or more, for the crafts you listed on the project. '
            'Exemptions and which crafts apply are questions for counsel or DIR.</p><div class="pw-scroll"><table><thead>'
            '<tr><th>Project</th><th>Craft</th><th>Week ending</th><th>Journeyperson hours</th><th>Apprentice hours</th>'
            f'<th>Ratio</th></tr></thead><tbody>{rows or "<tr><td colspan=6>No crafts to count.</td></tr>"}</tbody></table></div>'
            '<h3>DAS records on file</h3><div class="pw-scroll"><table><thead><tr><th>Project</th><th>DAS 140 (contract award '
            f'information)</th><th>DAS 142 (dispatch requests)</th></tr></thead><tbody>{"".join(das)}</tbody></table></div></section>')


def _subs(view):
    rows = "".join(f'<tr><td><strong>{e(s["name"])}</strong><small>{e(s["trade"])}</small></td><td>{e(s["project_id"])}</td>'
                   f'<td>{len(s["received"])} of {len(s["weeks"])}</td><td>{e(", ".join(s["late"]) or "none")}</td>'
                   f'<td>{e(", ".join(s["soon"]) or "none")}</td></tr>' for s in view["subs"])
    drafts = "".join(f'<details class="pw-draft"><summary>Draft to {e(c["sub"])} ({e(c["project_id"])})</summary>'
                     f'<p><strong>To:</strong> {e(c["to"])}<br><strong>Subject:</strong> {e(c["subject"])}</p>'
                     f'<pre>{e(c["body"])}</pre><button type="button" data-copy>Copy text</button></details>' for c in view["chase"])
    return ('<section id="subs"><h2>Subcontractor reports</h2><p class="pw-hint">Weekly payroll reports each subcontractor '
            'owes you for the weeks they were on site. A report is past due after the days you set (7 by default).</p>'
            '<div class="pw-scroll"><table><thead><tr><th>Subcontractor</th><th>Project</th><th>Received</th><th>Past due</th>'
            f'<th>Not yet due</th></tr></thead><tbody>{rows}</tbody></table></div><h3>Chase emails (drafts)</h3>'
            f'<p class="pw-hint">Drafts only. A person on your team reads, edits and sends them.</p>{drafts or "<p>No reports past due.</p>"}</section>')


def _packets(view):
    cards = []
    for p in view["packets"]:
        if p["state"] == "empty":
            continue
        label = {"open": "Waiting for review", "reviewed": "Marked reviewed by your officer", "changed": "Changed since review"}[p["state"]]
        rv = p["review"] or {}
        who = f'<p>{e(rv.get("officer_name"))}, {e(rv.get("officer_title"))}, {e(str(rv.get("at", ""))[:10])}</p>' if p["state"] == "reviewed" else ""
        if p["state"] == "reviewed":
            act = (f'<a class="pw-btn" href="payroll-desk/staging.csv?project={e(p["project_id"])}" download>Download CSV staging file</a>'
                   + (f'<form data-pw="filed"><input type="hidden" name="project_id" value="{e(p["project_id"])}"><label>'
                      f'Your officer filed it on <input type="date" name="filed_on" required></label><button>Record filing</button></form>'
                      if view["can"]["officer"] else ""))
        elif view["can"]["officer"]:
            act = (f'<form data-pw="review"><input type="hidden" name="project_id" value="{e(p["project_id"])}">'
                   f'<label>Your title <input name="title" value="Officer" required></label>'
                   f'<button{" disabled" if p["open"] else ""}>Mark packet reviewed</button></form>'
                   + (f'<p class="pw-flag">{p["open"]} flag(s) still open: fix or clear each one first.</p>' if p["open"] else ""))
        else:
            act = '<p class="pw-hint">Only an officer named in the desk settings can mark this packet reviewed.</p>'
        cards.append(f'<article class="pw-packet" data-state="{e(p["state"])}"><h3>{e(p["project_id"])}: {e(p["name"])}</h3>'
                     f'<p><strong>{e(label)}</strong>. Weeks ending {e(", ".join(p["weeks"]))}. {len(p["lines"])} lines, '
                     f'{p["open"]} open flag(s).</p>{who}<p><a href="payroll-desk/packet?project={e(p["project_id"])}">Open the review packet</a></p>{act}</article>')
    return (f'<section id="packets"><h2>Review packets</h2><p class="pw-hint">{e(pd.XML_NOTE)} The staging file opens once '
            f'your named officer marks the packet reviewed.</p><div class="pw-cards">{"".join(cards) or "<p>No open weeks.</p>"}</div></section>')


def _import(view):
    if not view["can"]["review"]:
        return ""
    return ('<section id="import"><h2>Import</h2><p class="pw-hint">Weekly payroll export (CSV in the generic layout), the '
            'project list, the wage rates you entered from each determination (with source and date), and subcontractors (JSON). '
            'The layout: docs/products/payroll-desk/input-layout.md.</p><form data-pw="import"><label>File type <select name="kind">'
            '<option value="payroll">Payroll CSV</option><option value="projects">Projects JSON</option>'
            '<option value="determinations">Wage rates JSON</option><option value="subcontractors">Subcontractors JSON</option>'
            '</select></label><label>File <input type="file" name="file" required accept=".csv,.json,text/csv,application/json">'
            '</label><button>Import</button></form></section>')


def _audit(view):
    rows = "".join(f'<tr><td>{e(a["at"][:16].replace("T", " "))}</td><td>{e(a["actor"])}</td><td>{e(a["action"])}</td></tr>'
                   for a in view["audit"])
    return f'<section id="audit"><h2>Recent activity</h2><div class="pw-scroll"><table><tbody>{rows}</tbody></table></div></section>'


def desk(prof, view):
    nav = "".join(f'<a href="payroll-desk#{a}">{t}</a>' for a, t in (("flags", "Flags"), ("calendar", "Calendar"),
                  ("apprentices", "Apprentices"), ("subs", "Subcontractors"), ("packets", "Packets"), ("import", "Import")))
    return crm_page.version_assets(
        _head(prof, "Prevailing Wage Desk") +
        f'<header class="pw-top"><div><strong>{e(prof["name"])}</strong><small>Prevailing Wage Desk, as of {e(view["as_of"])}'
        f'</small></div><nav aria-label="Desk">{nav}</nav></header><main>{_banner(prof)}<p id="pw-msg" role="alert"></p>'
        f'{_kpis(view["kpis"])}{_flags(view)}{_packets(view)}{_deadlines(view)}{_apprentices(view)}{_subs(view)}'
        f'{_import(view)}{_audit(view)}</main></body></html>')


def packet_page(prof, view, p):
    """The printable review packet for one project's unfiled weeks."""
    project = next(x for x in view["projects"] if x["id"] == p["project_id"])
    det = next((d for d in view["determinations"] if d["id"] == project.get("determination")), {})
    lines = "".join(f'<tr><td>{e(l["week_ending"])}</td><td>{e(l["worker_name"])}</td><td>{e(l["classification"] or "(none)")}'
                    f'{" P" + e(l["apprentice_period"]) if l["apprentice_period"] else ""}</td><td>{l["st_hours"]:g}/{l["ot_hours"]:g}/{l["dt_hours"]:g}</td>'
                    f'<td>{pd.money(l["base_rate"])}</td><td>{pd.money(l["fringe_plan"] + l["fringe_cash"])}</td><td>{pd.money(pd.gross(l))}</td></tr>'
                    for l in p["lines"])
    rv = p["review"] if p["state"] == "reviewed" else None
    sign = (f'<p><strong>Marked reviewed</strong> by {e(rv["officer_name"])}, {e(rv["officer_title"])}, on {e(rv["at"][:10])}.</p>'
            f'<p>{e(rv["statement"])}</p>' if rv else
            '<p><strong>Not yet reviewed.</strong> Your named officer marks this packet reviewed on the desk once every flag is cleared.</p>')
    return crm_page.version_assets(
        _head(prof, f"Review packet {p['project_id']}") +
        f'<main class="pw-print">{_banner(prof)}<h1>Review packet: {e(project.get("name", p["project_id"]))}</h1>'
        f'<p>Awarding body: {e(project.get("awarding_body"))}. DIR project ID: {e(project.get("dir_project_id"))}. '
        f'Contract value: {pd.money(float(project.get("contract_value") or 0))}. Weeks ending {e(", ".join(p["weeks"]))}.</p>'
        f'<p>Wage rates used: {e(det.get("title", project.get("determination", "")))}. Source as entered: {e(det.get("source", "(none)"))}, '
        f'entered {e(det.get("entered_on", ""))} by {e(det.get("entered_by", ""))}. The desk compares lines with these rates; '
        'it does not check the rates themselves.</p>'
        f'<h2>Flags ({p["open"]} open)</h2><table><thead><tr><th>Project</th><th>Week ending</th><th>Flag</th><th>Gap</th>'
        f'<th>Who decides</th><th>Status</th></tr></thead><tbody>{_flag_rows(p["flags"], False)}</tbody></table>'
        '<h2>Payroll lines</h2><table><thead><tr><th>Week ending</th><th>Worker</th><th>Classification</th><th>Hours ST/OT/DT</th>'
        f'<th>Base</th><th>Fringe</th><th>Gross this project</th></tr></thead><tbody>{lines}</tbody></table>'
        f'<h2>Officer review</h2>{sign}<p class="pw-hint">{e(pd.XML_NOTE)}</p><p><a href="payroll-desk#packets">Back to the desk</a></p></main></body></html>')
