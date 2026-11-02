"""Prevailing Wage Desk, v1: review weekly payroll on public works against the wage rates the client entered.

Brief: docs/handoffs/industrial/PW01-prevailing-wage-desk-v1.md. On only when the profile says
`payroll_desk.enabled: true`. Without it no table is created and no route answers.

This file is pure: inputs in, a review out. No database, no network, no clock (the caller passes
`as_of`). The store (payroll_desk_store.py) keeps imports, cleared flags, officer reviews and filings.

What it does: flags payroll lines for a person to review, prepares a review packet and a CSV staging
file. What it never does: decide a classification, answer a coverage question, sign, certify or submit.
The client's named officer certifies and submits on the DIR portal. Wage rates are data the client
enters from the determination, with its source and date; this engine never fetches or vouches for them.

Inputs (layout in docs/products/payroll-desk/input-layout.md):
- payroll lines: a CSV in the generic layout below, one line per worker, classification, project and week
- projects: awarding body, DIR project id, contract value, first and last day of work, crafts that
  carry the apprentice ratio check, DAS 140 and DAS 142 records, earlier filings, subcontractors
- determinations: rates per classification (base, fringe, overtime, double time; apprentice periods)
- subcontractors: who is on which project in which weeks, and which weekly reports came in
"""
import csv
import hashlib
import io
import json
from datetime import date, timedelta

COLUMNS = ("week_ending", "project_id", "worker_id", "worker_name", "classification", "apprentice_period",
           "st_hours", "ot_hours", "dt_hours", "base_rate", "ot_rate", "dt_rate", "fringe_plan", "fringe_cash",
           "check_no")
REQUIRED = ("week_ending", "project_id", "worker_id", "worker_name", "st_hours", "base_rate")
NUMBERS = ("st_hours", "ot_hours", "dt_hours", "base_rate", "ot_rate", "dt_rate", "fringe_plan", "fringe_cash")
APPRENTICE_THRESHOLD = 30000  # Labor Code 1777.5 duty: contracts of $30,000 or more (DIR, Apprentices page)
ECPR_THRESHOLD = 25000        # DIR FAQ: new construction etc. up to $25,000 is exempt from eCPR
RATIO = 5                     # one apprentice hour for every five journeyperson hours
CENT = 0.005
COUNSEL = "Classification and coverage questions go to your counsel or DIR."
OFFICER_LINE = ("Your officer certifies and submits the payroll records on the DIR portal. This desk "
                "prepares and flags for review; it never signs or submits. " + COUNSEL)
XML_NOTE = ("The DIR eCPR XML upload file is planned for v2. v1 prepares a CSV staging file with the same "
            "lines, for your own entry or upload step.")
# Every flag the desk raises: code -> (title, who decides).
CHECKS = {
    "project_unknown": ("Project not on the project list", "client"),
    "classification_missing": ("No classification on the line", "client"),
    "classification_unknown": ("Classification not on the determination entered for this project", "counsel or DIR"),
    "base_under": ("Base rate below the determination", "client"),
    "fringe_under": ("Fringe below the determination", "client"),
    "ot_under": ("Overtime rate below the determination", "client"),
    "dt_under": ("Double-time rate below the determination", "client"),
    "apprentice_period_missing": ("Apprentice line without an apprentice period", "client"),
    "apprentice_period_unknown": ("Apprentice period not on the determination", "client"),
    "weekly_over_40": ("More than 40 straight-time hours in the week", "client"),
    "ratio_short": ("Apprentice hours below 1 to 5 for the craft this week", "client"),
    "das140_missing": ("No DAS 140 on file for a craft with hours", "client"),
    "das142_missing": ("Ratio short and no DAS 142 request on file", "client"),
    "sub_report_missing": ("Subcontractor weekly payroll report not received", "client"),
}


class InputError(ValueError):
    """A file that cannot be read. The message says which line and field."""


def config(prof):
    cfg = prof.get("payroll_desk")
    return cfg if isinstance(cfg, dict) and cfg.get("enabled") is True else None


def enabled(prof):
    return config(prof) is not None


def officers(prof):
    """Person ids the profile names as officers who may mark a packet reviewed."""
    return [o for o in (config(prof) or {}).get("officers", []) if isinstance(o, str)]


def as_of(prof, today=None):
    """The desk's date: the profile's `as_of` (a sample's story date) or today."""
    fixed = (config(prof) or {}).get("as_of")
    return day(fixed, "as_of") if fixed else (today or date.today())


def day(value, field="date"):
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        raise InputError(f"{field}: '{value}' is not a date (YYYY-MM-DD)") from None


def money(x):
    return f"${x:,.2f}"


# ---------- reading the inputs ----------

def parse_payroll(text):
    """The generic CSV layout -> clean lines. Raises InputError naming the row and the field."""
    rows = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    head = [h.strip() for h in rows.fieldnames or []]
    missing = [c for c in REQUIRED if c not in head]
    if missing:
        raise InputError("Payroll file is missing columns: " + ", ".join(missing))
    lines = []
    for n, raw in enumerate(rows, start=2):
        row = {k.strip(): (v or "").strip() for k, v in raw.items() if k}
        line = {c: row.get(c, "") for c in COLUMNS}
        for c in REQUIRED:
            if not line[c]:
                raise InputError(f"Payroll row {n}: {c} is empty")
        line["week_ending"] = day(line["week_ending"], f"Payroll row {n}: week_ending").isoformat()
        for c in NUMBERS:
            try:
                line[c] = round(float(line[c] or 0), 4)
            except ValueError:
                raise InputError(f"Payroll row {n}: {c} '{row.get(c)}' is not a number") from None
            if line[c] < 0:
                raise InputError(f"Payroll row {n}: {c} is negative")
        lines.append(line)
    if not lines:
        raise InputError("Payroll file has no lines")
    return lines


def line_key(line):
    """One line per week, project, worker, classification and apprentice period: a re-import replaces it."""
    return "|".join(str(line[c]) for c in ("week_ending", "project_id", "worker_id", "classification", "apprentice_period"))


def parse_json(text, kind):
    try:
        body = json.loads(text)
    except json.JSONDecodeError as e:
        raise InputError(f"{kind} file is not valid JSON (line {e.lineno})") from None
    if not isinstance(body, dict) or not isinstance(body.get(kind), list):
        raise InputError(f"{kind} file needs a top-level list called '{kind}'")
    for i, item in enumerate(body[kind]):
        if not isinstance(item, dict) or not item.get("id"):
            raise InputError(f"{kind} item {i + 1} has no id")
    if kind == "determinations":
        for d in body[kind]:
            if not d.get("source") or not d.get("entered_on"):
                raise InputError(f"Determination {d['id']} needs a source and an entered_on date: "
                                 "rates are entered by you from the determination, with where and when")
            day(d["entered_on"], f"Determination {d['id']} entered_on")
    return body


# ---------- the review ----------

def _key(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _flag(code, project_id, week, detail, worker="", gap=0.0, ident=()):
    title, decides = CHECKS[code]
    return {"key": _key(code, project_id, week, *ident), "code": code, "title": title, "decides": decides,
            "project_id": project_id, "week_ending": week, "worker": worker, "detail": detail,
            "gap": round(gap, 2)}


def classes_of(det):
    return {c["name"]: c for c in (det or {}).get("classifications", []) if isinstance(c, dict) and c.get("name")}


def rate_for(cls, period):
    """(base, fringe, ot, dt) the determination sets for this class and apprentice period, or None."""
    if cls.get("apprentice"):
        p = (cls.get("periods") or {}).get(str(period))
        if not p:
            return None
        base, fringe = float(p["base"]), float(p["fringe"])
        return base, fringe, float(p.get("ot_rate") or base * 1.5), float(p.get("dt_rate") or base * 2)
    base = float(cls["base"])
    return base, float(cls["fringe"]), float(cls.get("ot_rate") or base * 1.5), float(cls.get("dt_rate") or base * 2)


def check_line(line, project, det):
    """Flags on one payroll line."""
    pid, week, who = line["project_id"], line["week_ending"], line["worker_name"]
    ident = (line["worker_id"], line["classification"], line["apprentice_period"])
    if project is None:
        return [_flag("project_unknown", pid, week, f"Project {pid} is not on the project list.", who, ident=ident)]
    if not line["classification"]:
        return [_flag("classification_missing", pid, week, f"{who}: the line has no classification. "
                      "Pick one from the determination. " + COUNSEL, who, ident=ident)]
    cls = classes_of(det).get(line["classification"])
    if cls is None:
        return [_flag("classification_unknown", pid, week, f"{who}: '{line['classification']}' is not on "
                      f"determination {project.get('determination', '(none)')}. " + COUNSEL, who, ident=ident)]
    period = line["apprentice_period"]
    if cls.get("apprentice") and not period:
        return [_flag("apprentice_period_missing", pid, week, f"{who}: apprentice classification with no "
                      "period. Add the period from the apprentice's agreement.", who, ident=ident)]
    rates = rate_for(cls, period) if cls.get("apprentice") else rate_for(cls, None)
    if rates is None:
        return [_flag("apprentice_period_unknown", pid, week, f"{who}: period {period} is not on the "
                      "determination for this class.", who, ident=ident)]
    base, fringe, ot, dt = rates
    hours = line["st_hours"] + line["ot_hours"] + line["dt_hours"]
    flags = []
    if line["base_rate"] + CENT < base:
        gap = (base - line["base_rate"]) * line["st_hours"]
        flags.append(_flag("base_under", pid, week, f"{who}: paid {money(line['base_rate'])}/hr, determination "
                           f"{money(base)}/hr, {line['st_hours']:g} hours: {money(gap)} short.", who, gap, ident))
    paid_fringe = line["fringe_plan"] + line["fringe_cash"]
    if paid_fringe + CENT < fringe and hours:
        gap = (fringe - paid_fringe) * hours
        flags.append(_flag("fringe_under", pid, week, f"{who}: fringe {money(paid_fringe)}/hr (plan plus cash), "
                           f"determination {money(fringe)}/hr, {hours:g} hours: {money(gap)} short.", who, gap, ident))
    for code, field, hrs, want in (("ot_under", "ot_rate", "ot_hours", ot), ("dt_under", "dt_rate", "dt_hours", dt)):
        if line[hrs] and line[field] + CENT < want:
            gap = (want - line[field]) * line[hrs]
            flags.append(_flag(code, pid, week, f"{who}: {line[hrs]:g} hours at {money(line[field])}/hr, "
                               f"determination {money(want)}/hr: {money(gap)} short.", who, gap, ident))
    return flags


def craft_of(line, det):
    cls = classes_of(det).get(line["classification"])
    return (cls or {}).get("craft", ""), bool((cls or {}).get("apprentice"))


def review(inputs, today, clears=None, reviews=None, filings=None):
    """The whole desk on one date. `inputs`: {"lines", "projects", "determinations", "subcontractors"}.
    `clears`: flag key -> {note, by, at}. `reviews`: (project, weeks) -> officer review.
    `filings`: recorded filings, added to the projects' own history."""
    clears, reviews = clears or {}, reviews or {}
    projects = {p["id"]: p for p in inputs.get("projects") or []}
    dets = {d["id"]: d for d in inputs.get("determinations") or []}
    lines = sorted(inputs.get("lines") or [], key=lambda l: (l["week_ending"], l["project_id"], l["worker_name"]))
    det_of = lambda pid: dets.get((projects.get(pid) or {}).get("determination"))
    filed = {pid: list(p.get("filings") or []) for pid, p in projects.items()}
    for f in filings or []:
        filed.setdefault(f["project_id"], []).append(f)
    flags = []
    for line in lines:
        flags += check_line(line, projects.get(line["project_id"]), det_of(line["project_id"]))
    # A worker's straight time across every project in the file, per week.
    st = {}
    for line in lines:
        st.setdefault((line["worker_id"], line["week_ending"]), []).append(line)
    for (wid, week), ls in st.items():
        total = sum(l["st_hours"] for l in ls)
        if total > 40 + CENT:
            where = ", ".join(f"{l['project_id']} {l['st_hours']:g}h" for l in ls)
            flags.append(_flag("weekly_over_40", ls[-1]["project_id"], week, f"{ls[0]['worker_name']}: {total:g} "
                               f"straight-time hours ({where}). Hours over 40 are usually overtime; check the "
                               "split.", ls[0]["worker_name"], ident=(wid,)))
    ratios = ratio_table(lines, projects, det_of)
    for r in ratios:
        if r["short"]:
            flags.append(_flag("ratio_short", r["project_id"], r["week_ending"], f"{r['craft']}: "
                               f"{r['apprentice']:g} apprentice hours for {r['journey']:g} journeyperson hours "
                               f"(1 to 5 needs {r['journey'] / RATIO:g}).", ident=(r["craft"],)))
            asked = [d for d in projects[r["project_id"]].get("das142") or []
                     if d.get("craft") == r["craft"] and str(d.get("requested_on", "9999")) <= r["week_ending"]]
            if not asked:
                flags.append(_flag("das142_missing", r["project_id"], r["week_ending"], f"{r['craft']}: no "
                                   "DAS 142 dispatch request on file before this week. Request dispatch at "
                                   "least three business days before apprentices are needed.", ident=(r["craft"],)))
    for pid, p in projects.items():
        if float(p.get("contract_value") or 0) < APPRENTICE_THRESHOLD:
            continue
        sent = {d.get("craft") for d in p.get("das140") or []}
        worked = sorted({craft_of(l, det_of(pid))[0] for l in lines if l["project_id"] == pid}
                        & set(p.get("ratio_crafts") or []))  # the crafts the project lists as apprenticeable
        for craft in worked:
            if craft not in sent:
                first = min(l["week_ending"] for l in lines if l["project_id"] == pid and craft_of(l, det_of(pid))[0] == craft)
                flags.append(_flag("das140_missing", pid, first, f"{craft}: hours on {pid} and no DAS 140 "
                                   "(contract award information) on file.", ident=(craft,)))
    subs = sub_status(inputs.get("subcontractors") or [], projects, today)
    for s in subs:
        for week in s["late"]:
            flags.append(_flag("sub_report_missing", s["project_id"], week, f"{s['name']}: no weekly payroll "
                               f"report for the week ending {week}.", s["name"], ident=(s["id"],)))
    for f in flags:
        c = clears.get(f["key"])
        f["cleared"] = c or None
    filed_weeks = {pid: {w for f in fs for w in f.get("weeks") or []} for pid, fs in filed.items()}
    packets = [packet_view(pid, p, lines, flags, filed_weeks.get(pid, set()), reviews) for pid, p in projects.items()]
    return {"as_of": today.isoformat(), "projects": list(projects.values()), "determinations": list(dets.values()),
            "lines": lines, "flags": flags, "ratios": ratios, "subs": subs, "packets": packets,
            "deadlines": deadlines(projects, filed, lines, today), "chase": chase_emails(subs, projects),
            "kpis": kpis(lines, flags, filed, subs)}


def ratio_table(lines, projects, det_of):
    """Apprentice and journeyperson hours per project, craft and week, for the crafts the project
    lists under `ratio_crafts`, on contracts of $30,000 or more."""
    out = {}
    for l in lines:
        p = projects.get(l["project_id"])
        if p is None or float(p.get("contract_value") or 0) < APPRENTICE_THRESHOLD:
            continue
        craft, appr = craft_of(l, det_of(l["project_id"]))
        if craft not in (p.get("ratio_crafts") or []):
            continue
        r = out.setdefault((l["project_id"], craft, l["week_ending"]),
                           {"project_id": l["project_id"], "craft": craft, "week_ending": l["week_ending"],
                            "journey": 0.0, "apprentice": 0.0})
        r["apprentice" if appr else "journey"] += l["st_hours"] + l["ot_hours"] + l["dt_hours"]
    for r in out.values():
        r["short"] = r["journey"] > 0 and r["apprentice"] * RATIO + CENT < r["journey"]
    return sorted(out.values(), key=lambda r: (r["project_id"], r["craft"], r["week_ending"]))


def week_endings(start, end, weekday):
    """Week-ending dates (on `weekday`, Monday 0) from the week holding `start` to the one holding `end`."""
    d = start + timedelta(days=(weekday - start.weekday()) % 7)
    while d - timedelta(days=6) <= end:
        yield d
        d += timedelta(days=7)


def sub_status(subs, projects, today):
    """Per subcontractor and project: weeks on site, reports in, late (past due) and due soon."""
    out = []
    for s in subs:
        due_days = int(s.get("report_due_days", 7))
        for pid, site in (s.get("projects") or {}).items():
            start = day(site["on_site_from"])
            end = min(day(site["on_site_to"]) if site.get("on_site_to") else today, today)
            weekday = int((projects.get(pid) or {}).get("week_ends_on", 5))  # Saturday
            weeks = [w.isoformat() for w in week_endings(start, end, weekday) if w <= today]  # a finished week
            got = set((s.get("reports") or {}).get(pid) or [])
            late = [w for w in weeks if w not in got and day(w) + timedelta(days=due_days) < today]
            soon = [w for w in weeks if w not in got and w not in late]
            out.append({"id": s["id"], "name": s["name"], "contact": s.get("contact", ""), "email": s.get("email", ""),
                        "trade": s.get("trade", ""), "project_id": pid, "weeks": weeks, "received": sorted(got),
                        "late": late, "soon": soon, "due_days": due_days})
    return out


def chase_emails(subs, projects):
    """A drafted email per subcontractor and project with late reports. Drafts only: a person sends them."""
    drafts = []
    for s in subs:
        if not s["late"]:
            continue
        p = projects.get(s["project_id"]) or {}
        weeks = ", ".join(s["late"])
        first = (s["contact"] or "there").split()[0]
        drafts.append({"to": s["email"], "sub": s["name"], "project_id": s["project_id"],
                       "subject": f"{p.get('name', s['project_id'])}: weekly payroll report for {weeks}",
                       "body": (f"Hi {first},\n\nWe have not received your certified payroll records for "
                                f"{p.get('name', s['project_id'])} (DIR project {p.get('dir_project_id', '')}) for "
                                f"the week(s) ending {weeks}.\n\nPlease send them, or a note that no one from "
                                f"{s['name']} worked on the job those weeks, by reply to this email. If you file on "
                                "the DIR portal yourselves, a copy of what you filed is fine.\n\nThank you.")})
    return drafts


def deadlines(projects, filed, lines, today):
    """Per project: the next eCPR filing by the client's officer, the final one, and the open weeks."""
    out = []
    for pid, p in projects.items():
        every = int(p.get("cpr_every_days", 30))
        fs = sorted(filed.get(pid) or [], key=lambda f: f["filed_on"])
        done = {w for f in fs for w in f.get("weeks") or []}
        open_weeks = sorted({l["week_ending"] for l in lines if l["project_id"] == pid} - done)
        anchor = day(fs[-1]["filed_on"]) if fs else day(p["first_work_on"]) if p.get("first_work_on") else None
        nxt = anchor + timedelta(days=every) if anchor else None
        final = day(p["last_work_on"]) + timedelta(days=30) if p.get("last_work_on") else None
        under = float(p.get("contract_value") or 0) <= ECPR_THRESHOLD
        out.append({"project_id": pid, "name": p.get("name", pid), "every_days": every,
                    "last_filed": fs[-1]["filed_on"] if fs else "", "next_due": nxt.isoformat() if nxt else "",
                    "days_left": (nxt - today).days if nxt else None, "final_due": final.isoformat() if final else "",
                    "open_weeks": open_weeks, "under_threshold": under})
    return sorted(out, key=lambda d: d["next_due"] or "9999")


def kpis(lines, flags, filed, subs):
    gaps = [(day(f["filed_on"]) - day(w)).days for fs in filed.values() for f in fs for w in f.get("weeks") or []]
    cleared = sum(1 for f in flags if f["cleared"])
    return {"lines_checked": len(lines), "flags_found": len(flags), "flags_cleared": cleared,
            "flags_open": len(flags) - cleared,
            "open_gap": round(sum(f["gap"] for f in flags if not f["cleared"]), 2),
            "days_to_file": round(sum(gaps) / len(gaps), 1) if gaps else None,
            "sub_reports_missing": sum(len(s["late"]) for s in subs)}


# ---------- the packet and the staging file ----------

def packet_view(pid, project, lines, flags, filed_weeks, reviews):
    """The project's unfiled weeks: what the officer reviews before filing them."""
    mine = [l for l in lines if l["project_id"] == pid and l["week_ending"] not in filed_weeks]
    weeks = sorted({l["week_ending"] for l in mine})
    in_packet = [f for f in flags if f["project_id"] == pid and (f["week_ending"] in weeks or f["code"] == "das140_missing")]
    digest = packet_digest(mine)
    rv = reviews.get((pid, ",".join(weeks)))
    state = "empty" if not weeks else "reviewed" if rv and rv["digest"] == digest else "changed" if rv else "open"
    return {"project_id": pid, "name": project.get("name", pid), "weeks": weeks, "lines": mine, "flags": in_packet,
            "open": sum(1 for f in in_packet if not f["cleared"]), "digest": digest, "review": rv, "state": state}


def packet_digest(lines):
    return hashlib.sha256(json.dumps([lines], sort_keys=True).encode()).hexdigest()


def gross(line):
    return round(line["st_hours"] * line["base_rate"] + line["ot_hours"] * line["ot_rate"]
                 + line["dt_hours"] * line["dt_rate"]
                 + (line["st_hours"] + line["ot_hours"] + line["dt_hours"]) * line["fringe_cash"], 2)


STAGING = ("dir_project_id", "awarding_body", "project_id", "week_ending", "worker_id", "worker_name", "classification",
           "apprentice_period", "st_hours", "ot_hours", "dt_hours", "base_rate", "ot_rate", "dt_rate", "fringe_plan",
           "fringe_cash", "gross_this_project", "check_no", "reviewed_by", "reviewed_on")


def staging_csv(packet, project):
    """The CSV staging file for one reviewed packet. No social security numbers: the desk never holds them;
    they are added in the client's own filing step."""
    if packet["state"] != "reviewed":
        raise InputError("The officer has not marked this packet reviewed")
    rv = packet["review"]
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(STAGING)
    for l in packet["lines"]:
        w.writerow([project.get("dir_project_id", ""), project.get("awarding_body", ""), l["project_id"], l["week_ending"],
                    l["worker_id"], l["worker_name"], l["classification"], l["apprentice_period"],
                    *(f"{l[c]:g}" for c in ("st_hours", "ot_hours", "dt_hours")),
                    *(f"{l[c]:.2f}" for c in ("base_rate", "ot_rate", "dt_rate", "fringe_plan", "fringe_cash")),
                    f"{gross(l):.2f}", l["check_no"], rv["officer_name"], rv["at"][:10]])
    return out.getvalue()
