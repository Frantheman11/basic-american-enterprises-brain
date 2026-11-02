"""The Pipeline — the CRM, as stages a lead moves through and a person who owns it.

Shape taken from `knowledge/techniques/professor-glitch-kb.md` §5.4, which is the proven recipe:
**form → enrich → score fit → route to the right rep → follow up.** The first two already
happen in the scanner, so a lead here arrives banded with its evidence attached and nobody
types a company name. This view is the routing and the following-up.

⭐ **Their pattern, our plumbing.** The KB's version assumes n8n plus a hosted CRM plus
Slack. That would put a client's customer list on a third party under our account, which
fails the two-repo rule, and an API between two systems IS the sync job the architecture
already refused. So: records are files, `records.py` is the one writer, and the status
dropdown the KB keeps describing is the approvals queue we already have.

⚠️ **Stages come from the profile, never from here.** A client's pipeline is theirs, and a
fixed stage list in engine code is a fork per business waiting to happen.

⚠️ **Leads come from `ctx["records"]`, never from `records.read`.** That reader is bound to
the signed-in person and filtered by `access.can_see`, so a rep's pipeline holds their own
leads and nothing else. The owner filter on the canvas is a convenience for an admin; it
was never the rule, and a rep's page never receives a lead to filter out.
"""

from ..access import actor_label
from ..schema import node

ID = "pipeline"
TITLE = "Pipeline"
BLURB = "Every lead, the stage it is in, and the person it belongs to."
# Built from records alone, so a role that sees only its own records may have it.
SCOPE = "records"

# A stage's colour is about momentum, not about the stage's name — so a client can rename
# every stage in their profile and the board still reads correctly.
TERMINAL_STATUS = {"won": "done", "lost": "locked"}


def _clean(d):
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def _last_change(prof, rec):
    """The latest human change, in words. `human:operator` reads as a person before sign-in."""
    last = (rec.get("provenance") or {}).get("last_human_write") or {}
    if not last.get("field"):
        return ""
    return (f"{last['field']} by {actor_label(prof, last.get('by'))}"
            + (f", {last['at'][:10]}" if last.get("at") else ""))


def _lead_node(rec, rid, nid, who, stage_id, changed=""):
    band = rec.get("band") or ""
    conf = rec.get("band_confidence")
    owner = rec.get("owner") or ""
    person = who.get(owner)
    failed = rec.get("failed_checks") or []
    return node(
        f"{nid}.{rid}", rec.get("display_name") or rid, "record",
        status=TERMINAL_STATUS.get(stage_id, "ready"),
        source=rec.get("from_source") or rec.get("source") or "",
        meta=rec.get("domain") or "",
        # ⚠️ An unassigned lead says so on its face. A card with no owner in a pipeline is
        # the single most common way a lead dies quietly, so it is not left blank.
        badge=(person["name"] if person else (owner or "unassigned")),
        # The owner as DATA, not only as a badge. A filter that reads a label is a filter
        # that breaks the day somebody's name changes.
        owner=owner,
        detail=_clean({
            "summary": rec.get("worst_finding") or "",
            "fields": _clean({
                "owner": person["name"] if person else (owner or "NOBODY — assign this"),
                "band": band,
                "why that band": "; ".join(rec.get("band_why") or []),
                "confidence": f"{int(conf * 100)}%" if conf else "",
                "site": rec.get("url") or "",
                "captured": (rec.get("captured_at") or "")[:10],
                # ⚠️ `said` is what THEY said. A discovered prospect has said nothing yet,
                # and the honest empty is better than filling it with our own words to
                # make a schema check go green.
                "what they said": rec.get("said") or "nothing yet — we approached them",
                "urgency": rec.get("urgency") or "not known yet",
                "last change": changed,
            }),
            "bullets": [f.replace(":", " — ").replace("_", " ") for f in failed[:8]],
        }))


def build(ctx):
    prof, root = ctx["profile"], ctx["root"]
    from .. import records

    stages = records.stages(prof)
    who = {m["id"]: m for m in records.people(prof)}
    nid = "pipeline"

    if not stages:
        return {"nodes": [], "stats": [], "notes": [
            "No pipeline stages are configured. The profile's `pipeline.stages` block is "
            "what gives a business its own pipeline — the engine deliberately ships none."]}

    # Read every lead this person may see, once, then bucket. A lead whose stage is not in
    # the profile is put in the first stage rather than dropped, because a lead you cannot
    # see is worse than a lead in the wrong column.
    leads = dict(ctx["records"].all(records.LEAD_TYPE))
    everything = ctx.get("sees") == "all"

    valid = {s["id"] for s in stages}
    nodes, unassigned, live = [], 0, 0
    for s in stages:
        sid = s["id"]
        mine = [(rid, r) for rid, r in sorted(leads.items())
                if (r.get("stage") if r.get("stage") in valid else stages[0]["id"]) == sid]
        kids = [_lead_node(r, rid, f"{nid}.{sid}", who, sid, _last_change(prof, r))
                for rid, r in mine]
        for _, r in mine:
            if not r.get("owner"):
                unassigned += 1
            if sid not in TERMINAL_STATUS:
                live += 1
        fires = s.get("fires") or []
        nodes.append(node(
            f"{nid}.{sid}", s.get("label") or sid, "section",
            status=TERMINAL_STATUS.get(sid, "active" if kids else "ready"),
            source=prof["_path"], badge=str(len(kids)) if kids else "",
            meta=s.get("plain_english", ""),
            detail=_clean({
                "summary": s.get("plain_english", ""),
                # ⚠️ Named "queues", not "does". Moving a card writes a status; a human
                # clears the queued action. KB §6 — never send, publish, pay or delete
                # without approval, and a pipeline that emails on a drag turns one
                # mis-click into a real apology to a real business.
                "fields": _clean({"moving a lead here queues": ", ".join(fires),
                                  "and sends": "nothing, ever"}),
            }),
            children=kids))

    # `title` is the job description; `role` is the access role and is not for display.
    people_rows = [m["name"] + (f" — {m['title']}" if m.get("title") else "")
                   + (f", works the {m['focus']} band" if m.get("focus") else "")
                   for m in who.values()] if everything else []
    notes = []
    if not leads:
        notes.append("No leads yet. A prospect on the Marketing board becomes a lead here, "
                     "already scanned and banded — which is why nobody types a company name."
                     if everything else
                     "No leads are yours yet. When one is assigned to you it appears here.")
    if unassigned:
        notes.append(f"{unassigned} lead(s) belong to nobody. An unowned lead is the most "
                     "common way one dies quietly.")

    # The team-wide figures only for somebody who sees the whole team. A rep's "unassigned
    # 0" would be a true count of what they can see and a false statement about the inbox.
    stats = [("leads", len(leads), prof["_path"] if everything else "your leads"),
             ("in play", live, "everything not won or lost")]
    if everything:
        stats += [("unassigned", unassigned, "computed from the lead records"),
                  ("people", len(who), prof["_path"])]
    return {
        "nodes": nodes,
        "canvas": True,
        "stats": stats,
        "notes": notes + ([" · ".join(people_rows)] if people_rows else []),
    }
