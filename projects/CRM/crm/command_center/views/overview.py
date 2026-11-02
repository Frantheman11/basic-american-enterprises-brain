"""Today — the one screen that has to be legible before anything is expanded.

This is where the collapsed-state rule is tested hardest: if this page does not answer
anything at a glance, on a phone, the design has failed and no amount of depth rescues it.

⚠️ **It used to count files.** Skills, notes, tools — true numbers about the repository and
useless to somebody running a business, which is what Sam meant by *"it just looks like a
bunch of files"*. A business opens this to ask four questions, in this order:

    what needs me · how is the pipeline · what is running · what is stuck

So those are the four things it carries, each a summary of a view rather than a copy of it,
and each with a `source` — an at-a-glance screen is exactly where an unsourced number does
the most damage, because it is the screen people quote from.

⚠️ **Today is per person** (CRM B1). Leads come from `ctx["records"]`, which is filtered by
`access.can_see`. For a person who sees only their own records, the context carries no
brain index and no work trees (`None`, not empty), so "what is running" and "what the index
says is missing" are left off rather than drawn as a zero that is not true.
"""

from ..schema import ghost, node

ID = "today"
TITLE = "Today"
BLURB = "What needs you, where the pipeline stands, and what is running."
# Built from records alone (the repo-wide parts drop out when the context lacks them), so
# a role that sees only its own records may have it.
SCOPE = "records"


def _clean(d):
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def _pipeline(prof, reader):
    """Leads by stage, and who owns them. Read from the same files the Pipeline draws —
    there is no second store, so these cannot disagree — through the same person-bound
    reader, so a rep's counts are counts of their own leads."""
    from .. import records
    stages = records.stages(prof)
    if not stages:
        return None
    by_stage, by_owner, total, unowned = {}, {}, 0, 0
    valid = {s["id"] for s in stages}
    for _rid, rec in reader.all(records.LEAD_TYPE):
        total += 1
        st = rec.get("stage") if rec.get("stage") in valid else stages[0]["id"]
        by_stage[st] = by_stage.get(st, 0) + 1
        own = rec.get("owner") or ""
        if own:
            by_owner[own] = by_owner.get(own, 0) + 1
        else:
            unowned += 1
    return {"stages": stages, "by_stage": by_stage, "by_owner": by_owner,
            "total": total, "unowned": unowned}


def build(ctx):
    prof, idx = ctx["profile"], ctx["brain_index"]
    recs = (idx or {}).get("recommendations", [])
    # None: this person's role cannot decide anything, so nothing is waiting on THEM.
    decides = ctx.get("waiting_on_human") is not None
    waiting = ctx.get("waiting_on_human") or []
    # None means "not in this person's context", which is different from "nothing there".
    work = ctx.get("work")
    everything = ctx.get("sees", "all") == "all"
    nodes = []

    # ---- 1. What needs me -------------------------------------------------------
    # No queue_status chip while the queue is empty: "waiting human" on a row that says
    # nothing is waiting reads as a contradiction, and a chip that lies once is ignored.
    # Left off entirely for a person who cannot decide: an item is not waiting on them.
    nodes.extend([node(
        "today.waiting", "Waiting on you", "section",
        # `ready`, not `done`. An empty queue that has never held anything is not one
        # somebody finished, and `done` rendered a green chip saying so.
        status="blocked" if waiting else "ready",
        queue_status="waiting_human" if waiting else None,
        source=prof["_path"], badge=str(len(waiting)) if waiting else "",
        detail={"summary": (f"{len(waiting)} item(s) need a decision before the next run can act."
                            if waiting else
                            "Nothing is waiting. When a workflow drafts something it lands "
                            "here and can be cleared from a phone.")},
        children=[node(f"today.waiting.{i}", w.get("what") or f"{w['record']} {w['id']}",
                       "record", status="blocked", source=w.get("source") or prof["_path"],
                       meta=f"{w['record']} · {w['id']}"[:60])
                  for i, w in enumerate(waiting[:8])])] if decides else [])

    # ---- 2. The pipeline --------------------------------------------------------
    pipe = _pipeline(prof, ctx["records"])
    if pipe and pipe["total"]:
        from .. import records
        people = {m["id"]: m for m in records.people(prof)}
        kids = [node(f"today.pipeline.{s['id']}", s.get("label") or s["id"], "section",
                     status="active" if pipe["by_stage"].get(s["id"]) else "ready",
                     source=prof["_path"], badge=str(pipe["by_stage"].get(s["id"], 0)),
                     meta=s.get("plain_english", ""))
                for s in pipe["stages"] if pipe["by_stage"].get(s["id"])]
        owners = " · ".join(f"{people.get(o, {}).get('name', o)} {n}"
                            for o, n in sorted(pipe["by_owner"].items(), key=lambda x: -x[1]))
        nodes.append(node(
            "today.pipeline", "The pipeline", "section",
            status="active", source=prof["_path"], badge=str(pipe["total"]),
            meta=f"{pipe['total']} leads" + (f" · {pipe['unowned']} unowned" if pipe["unowned"] else ""),
            detail=_clean({
                "summary": f"{pipe['total']} leads in play.",
                "fields": _clean({"by owner": owners,
                                  "unowned": str(pipe["unowned"]) if pipe["unowned"] else ""}),
            }),
            children=kids))

    # ---- 3. What is running -----------------------------------------------------
    # ⚠️ Counted from the agent manifest, so a department with nothing running is visible
    # here rather than only on the board. The whole point is that a gap surfaces.
    seen, built, rec_agents, silent = set(), 0, 0, []
    manifest_src = "the agent manifest"  # the profile names the file; the engine never does
    for d in (prof["departments"] if work is not None else []):
        got = 0
        for item in work.get(d["id"], []):
            if item.get("shape") != "agent_manifest":
                continue
            manifest_src = item.get("source") or manifest_src
            for a in item["agents"]:
                if a["id"] in seen:
                    continue
                seen.add(a["id"])
                if a["status"] == "built":
                    built += 1
                    got += 1
                elif a["status"] == "recommended":
                    rec_agents += 1
        if not got and not d.get("cross_cutting"):
            silent.append(d["label"])
    if seen:
        nodes.append(node(
            "today.running", "What is running", "section",
            status="active" if built else "blocked",
            source=manifest_src,
            badge=str(built),
            meta=(f"{built} running · {rec_agents} recommended"
                  + (f" · nothing in {', '.join(silent)}" if silent else "")),
            detail=_clean({
                "summary": (f"{built} automations and agents run for this business."
                            + (f" Nothing runs in {', '.join(silent)}." if silent else "")),
                "fields": _clean({"built": str(built), "recommended": str(rec_agents),
                                  "departments with nothing running": ", ".join(silent)}),
            })))

    # ---- 4. What the index says is missing ---------------------------------------
    if recs:
        nodes.append(node(
            "today.gaps", "What the index says is missing", "section",
            source="computed from the brain index", badge=str(len(recs)),
            detail={"summary": "Each row is computed from files. It carries a confidence, "
                               "so it is an argument rather than a measurement."},
            children=[ghost(f"today.gaps.{r['id']}", r["label"], r["kind"], r["confidence"],
                            r["rationale"], r["evidence"], r["source"]) for r in recs]))

    # ---- stats -------------------------------------------------------------------
    stats = [("waiting on you", len(waiting),
              "the approvals queue" if waiting else "nothing has been drafted yet")
             ] if decides else []
    if pipe:
        stats.append(("leads in play", pipe["total"], prof["records"]["lead"]["path"]))
        # Team-wide, so only for somebody who sees the whole team. A rep cannot see the
        # inbox, and "unowned 0" would be a true count and a false statement.
        if everything:
            stats.append(("unowned", pipe["unowned"], "computed from the lead records"))
    if work is not None:
        stats.append(("agents running", built, manifest_src))

    notes = []
    if pipe and pipe["unowned"]:
        notes.append(f"{pipe['unowned']} lead(s) belong to nobody. An unowned lead is the "
                     "most common way one dies quietly.")
    if silent:
        notes.append("Nothing runs in " + ", ".join(silent)
                     + ". That is the gap, not a reporting error.")
    # 🔴 The trend Sam asked for — views and leads, week over week. GA4 is wired on the
    # site (G-BLG90ZZXXH) but reading it needs the Data API and a service account, which
    # is an errand only Sam can run. Named here rather than drawn empty, because a chart
    # with no data is the one thing worse than no chart.
    if work is not None:
        notes.append("No trend line yet: reading GA4 needs a service account. Until then "
                     "this screen answers what is true now, not what is improving.")

    return {"nodes": nodes, "stats": stats, "notes": notes}
