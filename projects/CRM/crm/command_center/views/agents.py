"""Agents and runs — what ran, what it did, what it cost, and whether a human edited it.

⚠️ **The honest state of this view today.** The architecture specifies a run ledger where
`cost_usd` and `human.edited` are required fields, and edit rate is the number that decides
whether an agent graduates from draft-only. No ledger in this repo carries those fields
yet, because no agent workflow has run here. So this view renders the run stores that DO
exist and says plainly which required figures are missing.

That is the correct behaviour, not a placeholder. The alternative — showing an edit rate
computed from nothing — is precisely the failure the "every number traces to a file"
rule exists to prevent.
"""

import json
from pathlib import Path

from ..schema import node

ID = "agents"
TITLE = "Agents & runs"
BLURB = "What ran, what it did, what it cost, and whether a human had to fix it."

# The two figures the whole commercial model rests on (architecture A1.6). Named here so
# the view can say exactly what it cannot yet show.
REQUIRED_FIGURES = ("cost_usd", "human.edited")


def _read(p):
    """One run's record.

    Two shapes exist on disk and both are legitimate: a whole-file JSON object, and JSON
    Lines, where the run is an append-only event log and the LAST line is its current
    state. Reading only `.json` quietly reported 89 questionnaire runs and 22 scans as
    zero, which is worse than an error because it looks like an answer.
    """
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    lines = [ln for ln in text.splitlines() if ln.strip()]
    for ln in reversed(lines):
        try:
            rec = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            rec["_events"] = len(lines)
            return rec
    return None


def _plain_label(started, status, business, run_id):
    """A run row a person can read, with the code moved to a column.

    ⚠️ Standing rule: a code is never the primary label. This view drew forty rows reading
    `book-sheet-17d1857f73` — forty hashes, in date order, with the date sitting unused in
    the row's own detail. "2026-09-07 · book" answers what it was and when; the run id is
    still there, one line down, for anyone who needs to grep for it.
    """
    when = str(started or "")[:10]
    # Falls back through what the row actually offers, and ends at the id rather than at
    # nothing: a ledger of 22 scans labelled by date alone cannot be told apart, which
    # trades one unreadable label for another.
    what = (business or (status if status and status not in ("unknown", "") else "")
            or run_id)
    parts = [p for p in (when, what) if p]
    return " · ".join(parts) if parts else run_id


def _ledger_node(led, root):
    d = Path(root) / led["path"]
    rows = sorted(d.glob(led.get("glob", "*.json"))) if d.is_dir() else []
    kids, costed, edited, statuses = [], 0, 0, {}
    for f in rows[-40:]:
        rec = _read(f)

        if rec is None:
            continue
        status = rec.get("status") or rec.get("state") or rec.get("event") or "unknown"
        statuses[status] = statuses.get(status, 0) + 1
        if rec.get("cost_usd") is not None:
            costed += 1
        if isinstance(rec.get("human"), dict) and rec["human"].get("edited") is not None:
            edited += 1
        started = (rec.get("created") or rec.get("started_at")
                   or rec.get("scanned_at") or rec.get("checked_at") or rec.get("t"))
        run_id = rec.get("run_id") or rec.get("business_id") or f.stem
        kids.append(node(
            f"agents.{led['id']}.{f.stem}",
            _plain_label(started, status, rec.get("business_id"), run_id),
            "record",
            status="done" if status in ("ok", "done", "complete") else "ready",
            source=f"{led['path']}/{f.name}",
            detail={"fields": _clean({
                "started": started,
                "status": status,
                "business": rec.get("business_id"),
                "run id": run_id,
                "events logged": rec.get("_events"),
                "cost (usd)": rec.get("cost_usd"),
            })}))
    missing = [x for x in REQUIRED_FIGURES
               if (x == "cost_usd" and not costed) or (x == "human.edited" and not edited)]
    summary = f"{len(rows)} rows on disk."
    if missing and rows:
        summary += (" These rows carry none of " + ", ".join(missing) +
                    " — they predate the run schema, so no cost or edit rate can be shown "
                    "for them without inventing one.")
    capped = len(rows) > len(kids)
    return node(
        f"agents.{led['id']}", led["label"], "section",
        status="ready" if rows else "locked",
        source=led["path"],
        # The collapsed row must state the rows ON DISK, not how many of them this view
        # chose to draw. Without this the rollup counted its own children and a ledger of
        # 103 runs collapsed to "40 records" — the display cap reported as a fact about
        # the business. The body says "rows 103 / showing latest 40"; the one line you see
        # before expanding said 40 and nothing else.
        rollup_counts={"record": len(rows)},
        detail={"summary": summary,
                "fields": _clean({
                    "rows": len(rows),
                    "showing": f"latest {len(kids)}" if capped else "",
                    # Named for what it actually covers. Counted over the drawn sample, so
                    # an unqualified "by status" would be a second capped number reading
                    # as a total.
                    (f"by status (latest {len(kids)})" if capped else "by status"):
                        ", ".join(f"{k}: {v}" for k, v in sorted(statuses.items()))})},
        children=kids)


def build(ctx):
    prof, root, idx = ctx["profile"], ctx["root"], ctx["brain_index"]
    nodes, total = [], 0

    workflows = (idx or {}).get("sections", {}).get("workflows", [])
    if workflows:
        nodes.append(node(
            "agents.workflows", "Workflows", "section", source=prof.get("workflows_path", ""),
            children=[node(f"agents.workflows.{w['name']}", w["name"], "workflow",
                           source=w["source"]) for w in workflows]))
    else:
        nodes.append(node(
            "agents.workflows", "Workflows", "section", status="locked",
            source=prof.get("workflows_path", "data/command-center/workflows"),
            detail={"summary": "No workflow manifest exists yet. The workflow is the unit of "
                               "sale and the architecture specifies one manifest each; until "
                               "one is stamped out there is no agent here to draw."}))

    for led in prof.get("run_ledgers", []):
        n = _ledger_node(led, root)
        total += (n["detail"]["fields"].get("rows") or 0)
        nodes.append(n)

    stats = [
        ("workflows", len(workflows), prof.get("workflows_path", "")),
        ("runs on disk", total, "the profile run_ledgers"),
        ("edit rate", "not measured", "no ledger row carries human.edited yet"),
        ("cost to date", "not measured", "no ledger row carries cost_usd yet"),
    ]
    return {"nodes": nodes, "stats": stats, "notes": [
        "Edit rate is the gate that lifts an agent from draft-only, and cost is what decides "
        "whether the shape survives client three. Both are required fields on a run row. "
        "Neither is shown as a number here, because no row on disk carries one."]}


def _clean(d):
    return {k: v for k, v in d.items() if v not in ("", None)}
