"""Records — the four record types, and which fields a human is allowed to change.

The four records are lead, customer, conversation and run
(`docs/products/cta-buildout-architecture-v1.md` §1). Two rules from that document are made
visible here rather than merely documented:

  * **One writer per FIELD, not per record.** Human-owned fields are marked as editable;
    everything an agent computes is read-only in the UI. The schema rule becomes a thing
    you can see.
  * **A writer creates a new file, it never edits a contended one.** Any write this view
    grows must follow that, which is also why there is no edit control in this phase —
    the write path is P3 and it is built once, in one module.

🔴 **Client records never live in this repo.** This view reads only the record paths the
profile names, and the boundary test asserts no client record field appears under this
repo's client folders. A client's records live in that client's own repo, from its first
commit, with the engine copied in and version-stamped.
"""

import json
from pathlib import Path

from ..schema import node

ID = "records"
TITLE = "Records"
BLURB = "Lead, customer, conversation, run — and who is allowed to write each field."

ORDER = ("lead", "customer", "conversation", "run")
BLURBS = {
    "lead": "Where they came from, what they said, how urgent. Created by the fast path.",
    "customer": "Who they are across every system that names them. Aliases are the whole trick.",
    "conversation": "An append-only event. A correction is a new event, never an edit.",
    "run": "One row per run: what fired, what it did, what it cost, whether a human edited it.",
}


def _sample_fields(path, glob="*.json", limit=6):
    """The field names actually present on disk, so the view describes the real records
    rather than the schema document's example."""
    if not path.is_dir():
        return [], 0
    rows = sorted(path.glob(glob))
    keys = []
    for f in rows[:limit]:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(rec, dict):
            for k in rec:
                if k not in keys:
                    keys.append(k)
    return keys, len(rows)


def build(ctx):
    prof, root = ctx["profile"], ctx["root"]
    cfg = prof.get("records", {})
    nodes, total = [], 0
    for name in ORDER:
        spec = cfg.get(name)
        if not spec:
            continue
        d = Path(root) / spec["path"]
        keys, count = _sample_fields(d)
        total += count
        human = set(spec.get("human_owned", []))
        # Does what is on disk actually match the schema it is filed under? A count with
        # no conformance line lets 31 anonymous sessions read as 31 leads.
        required = spec.get("required", [])
        missing = [f for f in required if f not in keys] if count else required
        kids = []
        for k in keys:
            owned = k in human
            kids.append(node(
                f"records.{name}.field-{k}", k, "metric",
                status="ready" if owned else "locked",
                source=spec["path"],
                badges=["needs_review"] if owned else [],
                detail={"summary": "Yours to set. A human surface may write this field."
                                   if owned else
                                   "Written by an agent. Read-only here, on purpose."}))
        conforms = count and not missing
        nodes.append(node(
            f"records.{name}", name.title(), "record",
            status="ready" if conforms else ("blocked" if count else "locked"),
            source=spec["path"],
            badges=[] if conforms or not count else ["needs_review"],
            detail={"summary": BLURBS.get(name, ""),
                    "fields": _clean({
                        "on disk": count,
                        "folder": spec["path"],
                        "fields you own": ", ".join(sorted(human)) or "none",
                        "matches the schema": ("yes" if conforms else
                                               f"NO - missing {', '.join(missing)}" if count
                                               else ""),
                        "state": "" if count else "no records written yet",
                        "note": spec.get("note", ""),
                    })},
            children=kids))

    pending = ctx.get("waiting_on_human") or []
    queue = [node(
        f"records.approvals.{p['record']}-{p['id']}",
        p.get("what") or f"{p['record']} {p['id']}", "record",
        status="blocked", queue_status="waiting_human",
        source=p["source"],
        detail={"summary": p.get("what") or "",
                "fields": _clean({"record": p["record"], "id": p["id"],
                                  "kind": p.get("kind"),
                                  # Named for what it is. When nothing recorded the moment
                                  # this entered the queue, the date is the record's own
                                  # last-updated and says so — rather than presenting a
                                  # fallback as the answer somebody would triage by.
                                  ("last updated (not queued at)"
                                   if p.get("since_is_approximate") else "waiting since"):
                                      p.get("since")}),
                "decide": {"record": p["record"], "id": p["id"],
                           "options": p.get("decisions", [])}})
        for p in pending]
    waiting = node(
        "records.approvals", "Waiting on you", "section",
        # `ready`, not `locked` — nothing gates this queue, it has simply never been
        # written to. It said `locked` here and `done` on the Overview: one fact, two
        # views, two different wrong words. See the note in views/overview.py.
        status="blocked" if pending else "ready",
        queue_status="waiting_human" if pending else None,
        source=prof["_path"],
        detail={"summary": (f"{len(pending)} item(s) need a decision before the next run can act."
                            if pending else
                            "The durable answer to 'what is waiting on me' — an agent drafts, "
                            "you set approved / needs revision / escalate from a phone, and the "
                            "next run reads the status. It writes a human-owned field only, "
                            "which is why it needs no sync. Empty until a workflow drafts "
                            "something.")},
        children=queue)

    stats = [
        ("record types", len([n for n in nodes]), prof["_path"]),
        ("records on disk", total, "the profile record paths"),
        ("waiting on you", len(pending),
         "no workflow has drafted anything yet" if not pending else "the approvals queue"),
    ]
    return {"nodes": [waiting] + nodes, "stats": stats, "notes": [
        "Client records never live in this repo. A client instance reads its own repo, with "
        "the engine copied in and version-stamped."]}


def _clean(d):
    return {k: v for k, v in d.items() if v not in ("", None)}
