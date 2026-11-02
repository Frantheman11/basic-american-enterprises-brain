"""The "All businesses" view (Portfolio CRM P2): every business side by side, read only.

For each business the person can see, this reads that business's own `Store.snapshot` as that
person: the same read, and the same visibility rules, as the business's own tab. A rep sees only
their own records here because the snapshot already gives them only their own records. The
numbers are added up in Python. No copy of the data, no new table, no query across databases,
no business_id column. Nothing is written.

Definitions, kept the same as the CRM page (www/crm.js):
- an open deal is a deal whose stage has no `won` or `lost` outcome
- an open task has status `open`; it is overdue when `due_at` is before now
- due today = open, not overdue, and due before the end of today in the portfolio's timezone
- a new contact was created in the last 7 days (the business's own record is not a contact)
"""
import html
from datetime import datetime, timedelta, timezone

from command_center import crm_store, portfolio_links, profiles

TASK_LIMIT = 100  # rows in the task list; the counts are never capped
NEW_DAYS = 7


def zone(name):
    """The portfolio's timezone, or UTC when it is not set or this machine has no tz data."""
    if not name:
        return timezone.utc, "UTC"
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name), name
    except Exception:  # noqa: BLE001 — Windows without tzdata: say UTC, never guess
        return timezone.utc, "UTC"


def _when(value):
    at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def business(entry, snap, now, end_of_today):
    """One business's column, from its own snapshot. Pure: the tests call it directly."""
    stages = snap["stages"]
    closed = {s["id"] for s in stages if s.get("outcome") in ("won", "lost")}
    open_deals = [d for d in snap["deals"] if d["stage"] not in closed]
    lanes = []
    for s in stages:
        if s["id"] in closed:
            continue
        here = [d for d in open_deals if d["stage"] == s["id"]]
        lanes.append({"id": s["id"], "label": s.get("label") or s["id"], "count": len(here),
                      "value_cents": sum(d["amount_cents"] for d in here), "pipeline": s.get("pipeline", "")})
    known = {s["id"] for s in stages}
    stray = [d for d in open_deals if d["stage"] not in known]
    if stray:  # a stage removed from the list: still open, still counted
        lanes.append({"id": "", "label": "Other stage", "count": len(stray),
                      "value_cents": sum(d["amount_cents"] for d in stray), "pipeline": ""})
    # More than one pipeline (P3): the open deals of each, so BAE shows investors and distribution apart.
    lines = [{"id": p["id"], "label": p["label"],
              "count": sum(l["count"] for l in lanes if l["pipeline"] == p["id"]),
              "value_cents": sum(l["value_cents"] for l in lanes if l["pipeline"] == p["id"])}
             for p in snap.get("pipelines") or []]
    names = {c["id"]: c["name"] for c in snap["contacts"]}
    since = now - timedelta(days=NEW_DAYS)
    rows = []
    overdue = due_today = 0
    for t in snap["tasks"]:
        if t["status"] != "open":
            continue
        due = _when(t["due_at"])
        late = due < now
        if not late and due >= end_of_today:
            continue
        overdue += late
        due_today += not late
        rows.append({"business": entry["business_id"], "id": t["id"], "title": t["title"],
                     "due_at": t["due_at"], "late": late, "contact": names.get(t["contact_id"], "")})
    return {"id": entry["business_id"], "label": entry["label"], "color": entry["color"],
            "currency": snap.get("currency") or "USD",
            "deals": {"count": len(open_deals), "value_cents": sum(d["amount_cents"] for d in open_deals),
                      "stages": lanes, "pipelines": lines},
            "tasks": {"overdue": overdue, "due_today": due_today},
            "new_contacts": sum(1 for c in snap["contacts"]
                                if c.get("kind") != "business" and _when(c["created_at"]) >= since),
            # Fran 2026-11-01 (local change, not in Sam's release): every contact, not only the last 7 days
            "contacts": sum(1 for c in snap["contacts"] if c.get("kind") != "business"),
            "_rows": rows}


def read(pf, seen, now=None):
    """The roll-up for one sign-in. `seen` is [(entry, person)] from Portfolio.visible."""
    now = now or datetime.now(timezone.utc)
    tz, tz_name = zone(pf.data.get("timezone"))
    local = now.astimezone(tz)
    end_of_today = (local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).astimezone(timezone.utc)
    cols, rows, visible = [], [], {}
    for entry, person in seen:
        prof = profiles.load(entry["business_id"], pf.root)
        try:
            snap = pf.configs[entry["business_id"]].crm.snapshot(prof, person)
        except crm_store.CRMError:  # signed in there, but no CRM reading: adds nothing
            continue
        visible[entry["business_id"]] = {c["id"] for c in snap["contacts"]}
        col = business(entry, snap, now, end_of_today)
        rows += col.pop("_rows")
        cols.append(col)
    rows.sort(key=lambda r: (not r["late"], _when(r["due_at"])))
    currencies = {c["currency"] for c in cols}
    same = len(currencies) == 1
    return {"name": pf.data.get("name") or pf.id, "timezone": tz_name,
            "server_time": now.isoformat(timespec="seconds"), "businesses": cols,
            "totals": {"currency": next(iter(currencies)) if same else None,
                       "value_cents": sum(c["deals"]["value_cents"] for c in cols) if same else None,
                       "deals": sum(c["deals"]["count"] for c in cols),
                       "overdue": sum(c["tasks"]["overdue"] for c in cols),
                       "due_today": sum(c["tasks"]["due_today"] for c in cols),
                       "new_contacts": sum(c["new_contacts"] for c in cols),
                       "contacts": sum(c["contacts"] for c in cols),
                       # P4: people linked across businesses, counting only records this person sees
                       "linked_people": portfolio_links.count_linked(pf, visible)},
            "tasks": rows[:TASK_LIMIT], "tasks_more": max(0, len(rows) - TASK_LIMIT)}


# ---------------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------------

def money(cents, currency):
    sign = "$" if currency == "USD" else ""
    whole = f"{sign}{cents / 100:,.0f}"
    return whole if sign else f"{whole} {currency}"


def _plural(n, one, many):
    return f"{n} {one if n == 1 else many}"


def render(data, prof, base, theme_key):
    """The page in the unified shell. `prof` is a stand-in profile for the header: the
    portfolio's name and the tab strip. Links are relative to `base` (one business mount)."""
    from command_center import crm_page, workspace_shell
    esc = lambda s: html.escape(str(s), quote=True)
    tot, cols = data["totals"], data["businesses"]
    by_id = {c["id"]: c for c in cols}
    tz = zone(data["timezone"])[0]
    if tot["currency"]:
        value = f'<strong>{esc(money(tot["value_cents"], tot["currency"]))}</strong><small>open deal value, {esc(_plural(tot["deals"], "deal", "deals"))}</small>'
    else:
        value = (f'<strong>{esc(_plural(tot["deals"], "open deal", "open deals"))}</strong>'
                 '<small>No total: the businesses use different currencies. Each total is below.</small>')
    summary = (f'<div class="pr-totals"><div>{value}</div>'
               f'<div><strong>{tot["overdue"]}</strong><small>tasks overdue</small></div>'
               f'<div><strong>{tot["due_today"]}</strong><small>tasks due today</small></div>'
               f'<div><strong>{tot["contacts"]:,}</strong><small>contacts in all</small></div>'
               f'<div><strong>{tot["new_contacts"]:,}</strong><small>new contacts, last {NEW_DAYS} days</small></div></div>')
    cards = []
    for c in cols:
        d = c["deals"]
        row = lambda s: f'<li><span>{esc(s["label"])}</span><span>{s["count"]}</span><span>{esc(money(s["value_cents"], c["currency"]))}</span></li>'
        if len(d["pipelines"]) > 1:
            lanes = "".join(f'<li class="pr-line" data-pipeline="{esc(p["id"])}"><a href="../{esc(c["id"])}/?view=deals&amp;pipeline={esc(p["id"])}">{esc(p["label"])}</a>'
                            f'<span>{p["count"]}</span><span>{esc(money(p["value_cents"], c["currency"]))}</span></li>'
                            + "".join(row(s) for s in d["stages"] if s["pipeline"] == p["id"]) for p in d["pipelines"])
            lanes += "".join(row(s) for s in d["stages"] if s["pipeline"] not in {p["id"] for p in d["pipelines"]})
        else:
            lanes = "".join(row(s) for s in d["stages"])
        cards.append(
            f'<section class="pr-card" style="--tab:{esc(c["color"])}" data-business="{esc(c["id"])}">'
            f'<h2><a href="../{esc(c["id"])}/"><span class="ws-biz-dot" aria-hidden="true"></span>{esc(c["label"])}</a></h2>'
            f'<p class="pr-big"><a href="../{esc(c["id"])}/?view=deals"><strong data-k="deal-value">{esc(money(d["value_cents"], c["currency"]))}</strong>'
            f'<small><span data-k="deal-count">{d["count"]}</span> open {"deal" if d["count"] == 1 else "deals"}</small></a></p>'
            f'<ul class="pr-lanes{" lined" if len(d["pipelines"]) > 1 else ""}" aria-label="Open deals by stage">{lanes}</ul>'
            f'<dl class="pr-stats"><div><dt>Overdue</dt><dd data-k="overdue" class="{"late" if c["tasks"]["overdue"] else ""}">{c["tasks"]["overdue"]}</dd></div>'
            f'<div><dt>Due today</dt><dd data-k="due-today">{c["tasks"]["due_today"]}</dd></div>'
            f'<div><dt>Contacts</dt><dd data-k="contacts">{c["contacts"]:,}</dd></div>'
            f'<div><dt>New, {NEW_DAYS} days</dt><dd data-k="new-contacts">{c["new_contacts"]:,}</dd></div></dl></section>')
    items = []
    for t in data["tasks"]:
        c = by_id[t["business"]]
        when = _when(t["due_at"]).astimezone(tz)
        items.append(
            f'<li style="--tab:{esc(c["color"])}"><a href="../{esc(c["id"])}/?view=tasks&amp;task={esc(t["id"])}">'
            f'<span class="pr-tag"><span class="ws-biz-dot" aria-hidden="true"></span>{esc(c["label"])}</span>'
            f'<strong>{esc(t["title"])}</strong><small>{esc(t["contact"])}{" · " if t["contact"] else ""}'
            f'<span class="{"late" if t["late"] else ""}">{"Overdue · " if t["late"] else "Due "}<time datetime="{esc(t["due_at"])}">{esc(when.strftime("%b %d, ") + when.strftime("%I:%M %p").lstrip("0"))}</time></span></small></a></li>')
    more = f'<p class="muted">And {data["tasks_more"]} more. Open each business to see them all.</p>' if data["tasks_more"] else ""
    linked = (f'<p class="muted pr-linked" data-k="linked-people">{_plural(tot["linked_people"], "person is", "people are")} '
              'in two or more of your businesses.</p>') if tot.get("linked_people") else ""
    tasks = (f'<ul class="pr-tasks">{"".join(items)}</ul>{more}' if items
             else '<p class="muted">Nothing overdue or due today in any business.</p>')
    page = f'''<!doctype html>
<html lang="en" data-theme-key="{esc(theme_key)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<base href="{esc(base)}"><meta name="robots" content="noindex,nofollow"><title>All businesses | {esc(data["name"])}</title>
<link rel="stylesheet" href="crm.css"><link rel="stylesheet" href="workspace-design.css"><script defer src="workspace-design.js"></script><link rel="stylesheet" href="workspace-shell.css"><script defer src="workspace-shell.js"></script><link rel="stylesheet" href="portfolio-rollup.css"></head>
<body class="workspace-design portfolio-rollup"><a class="skip" href="#main">Skip to the roll-up</a>
{workspace_shell.header(prof, live=True, subtitle="All businesses")}
<main id="main" class="pr-main"><div class="pr-head"><h1>All businesses</h1><p class="muted">Each business as you see it in its own tab. Times in {esc(data["timezone"])}.</p></div>
{summary}{linked}
<div class="pr-cards">{"".join(cards)}</div>
<section class="pr-due"><h2>Overdue and due today</h2>{tasks}</section></main></body></html>'''
    return crm_page.version_assets(page)
