"""The one writer. Every human edit to a record goes through this module and nowhere else.

Not a helper a view calls when convenient — **the only writer**. A view that writes is how
"one writer per field" stops being true, and it stops being true silently.

Four rules, each enforced here rather than remembered:

  1. **One writer per FIELD, not per record** (architecture A1.3). This module accepts only
     the fields the profile lists as `human_owned` for that record type, and refuses
     everything else by name. An agent-owned field cannot be written from a human surface
     even by a caller that wants to.

  2. **A writer creates a NEW file, it never edits one another writer may be touching**
     (architecture A1.4). A human edit is written as an *overlay*: one small file per
     change, under `_human/<record_id>/`. Nothing ever rewrites the base record, so two
     writers in the same second cannot lose each other's work. Reading a record replays
     the overlays over the base in timestamp order.

  3. **Every write carries provenance** (architecture A1.5): what changed, to what, by
     whom, when, and from which surface.

  4. **Setting a status is not performing the action.** This module writes a field. It
     never sends, publishes, pays or deletes — the next run reads the status and acts.
     That separation is what makes an approvals queue safe to put on a phone.

  5. **The signed-in person's role is checked HERE, inside the writer** (CRM B1). Every
     write takes `person=`; `access.why_not_write` decides, and a refusal is a
     `WriteRefused` that names the field and the reason, like every other refusal. Because
     the check is in the writer, no route can skip it. With a person, `by` is built from
     the person (`human:<id>`), and whatever `by` the caller passed is ignored.
     `person=None` is the in-process operator (tests, tools): full access, `by` as given.
     The HTTP server always passes a person.

⚠️ Do not confuse this with `command_center/views/records.py`, which only *draws* records.
This module is `command_center.records`, and it is the one that touches disk.
"""

import json
import re
from datetime import datetime, timezone
from uuid import uuid4
from pathlib import Path

from . import access, profiles
from .schema import SchemaError

SCHEMA_VERSION = 1

# Where a record type's human overlays live, relative to that record type's own folder.
OVERLAY_DIR = "_human"

# A record id has to be safe as a folder name and has to round-trip. Anything else is
# refused rather than sanitised — quietly rewriting an id is how a write lands on the
# wrong record.
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
SAFE_FIELD = re.compile(r"^[a-z][a-z0-9_.]{0,60}$")


class WriteRefused(SchemaError):
    """A write this module will not perform, and the reason, by name."""


def _now():
    """ISO 8601 UTC, to the microsecond. Provenance — NOT the replay order.

    ⚠️ The precision in this string is not the precision of the clock behind it. Measured
    2026-09-21 on the Windows box this was built on, `time.get_clock_info("time")
    .resolution` is 0.015625s — so 2000 consecutive calls returned SEVEN distinct values.
    A timestamp formatted to the microsecond off a clock that ticks every 15.6ms reads as
    precise and is not. The engine gets copied into client repos on other platforms, so
    do not re-derive ordering from this on the grounds that some other clock is finer:
    the point is that a write order must not depend on a clock at all.

    So ordering does not come from here. It comes from `_next_seq`, and the two jobs are
    kept apart on purpose: the clock answers *when did this happen*, the sequence answers
    *what happened after what*. Making the clock carry both is what broke replay twice.
    """
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


# An overlay filename opens with a zero-padded ordinal, and that ordinal is the replay
# order. It is read back off the directory rather than kept in memory, so it survives a
# restart and is shared by every process writing the same record.
SEQ = re.compile(r"^(\d{6})-")
SEQ_WIDTH = 6


def _next_seq(d):
    """One past the highest ordinal already filed for this record.

    Two writers racing can both read the same highest and both claim it. They then tie —
    but they were genuinely concurrent, which is the one case where either order is
    defensible. What this guarantees is the case that actually matters and that the clock
    could not: a write which lands AFTER another has been filed never sorts before it.
    """
    top = 0
    try:
        for f in d.iterdir():
            m = SEQ.match(f.name)
            if m:
                top = max(top, int(m.group(1)))
    except OSError:
        pass
    return top + 1


def _spec(prof, record_type):
    spec = (prof.get("records") or {}).get(record_type)
    if not isinstance(spec, dict):
        raise WriteRefused(
            f"unknown record type {record_type!r} — the profile defines "
            f"{', '.join(k for k, v in (prof.get('records') or {}).items() if isinstance(v, dict))}")
    return spec


def human_fields(prof, record_type):
    return list(_spec(prof, record_type).get("human_owned", []))


def base_path(prof, record_type, record_id, root="."):
    return Path(root) / _spec(prof, record_type)["path"] / f"{record_id}.json"


def overlay_dir(prof, record_type, record_id, root="."):
    return Path(root) / _spec(prof, record_type)["path"] / OVERLAY_DIR / record_id


# ---------------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------------

def read(prof, record_type, record_id, root="."):
    """A record as it currently stands: the base file with every human overlay replayed
    over it, oldest first. Returns None when there is no base and no overlay.

    ⚠️ This is the RAW read: no person, no rule. The writer needs it, and so does
    `access`. Anything that shows a record to a person reads through `access.Reader`.
    """
    # The id becomes part of a path. Refused rather than sanitised, as on the write side.
    if not SAFE_ID.match(str(record_id or "")):
        raise WriteRefused(f"unsafe record id {record_id!r}")
    base = base_path(prof, record_type, record_id, root)
    rec = None
    if base.is_file():
        try:
            rec = json.loads(base.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise WriteRefused(f"{base} is not readable JSON: {e}") from e
    if not isinstance(rec, dict):
        rec = None
    for ov in overlays(prof, record_type, record_id, root):
        if rec is None:
            rec = {"record_id": record_id, "schema_version": SCHEMA_VERSION}
        _apply(rec, ov["field"], ov["value"])
        prov = rec.setdefault("provenance", {})
        prov["last_human_write"] = {
            "field": ov["field"], "by": ov["by"], "at": ov["at"], "surface": ov["surface"]}
        # Per FIELD, not just the last write overall — architecture A1.5 is "who, when and
        # from which surface" for a change, and one writer per field makes that per field.
        # The queue needs it: without it "waiting since" fell back to the record's own
        # `updated` date, so an item queued two minutes ago read as waiting since the 7th.
        prov.setdefault("fields", {})[ov["field"]] = {
            "by": ov["by"], "at": ov["at"], "surface": ov["surface"]}
    return rec


def overlays(prof, record_type, record_id, root="."):
    """Every human edit to one record, oldest first."""
    d = overlay_dir(prof, record_type, record_id, root)
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.json")):
        try:
            ov = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue          # a half-written overlay is skipped, never guessed at
        if isinstance(ov, dict) and ov.get("field"):
            ov["_file"] = str(f).replace("\\", "/")
            m = SEQ.match(f.name)
            ov["_seq"] = int(m.group(1)) if m else 0
            out.append(ov)
    # Sorted by the ordinal, NOT by `at`. The clock behind `at` is coarse enough that a
    # burst of writes shares one value, and sorting on it hands the order to whatever the
    # tiebreak happens to be. A file with no ordinal predates the scheme, so it is oldest.
    out.sort(key=lambda o: (o["_seq"], o["_file"]))
    return out


def _apply(rec, field, value):
    """Set a dotted field path without disturbing its siblings."""
    parts = field.split(".")
    node = rec
    for p in parts[:-1]:
        nxt = node.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            node[p] = nxt
        node = nxt
    node[parts[-1]] = value


def ids(prof, record_type, root="."):
    """Every record id of a type — base files plus any that exist only as overlays."""
    spec = _spec(prof, record_type)
    d = Path(root) / spec["path"]
    out = set()
    if d.is_dir():
        out |= {f.stem for f in d.glob("*.json")}
        ov = d / OVERLAY_DIR
        if ov.is_dir():
            out |= {p.name for p in ov.iterdir() if p.is_dir()}
    return sorted(out)


# ---------------------------------------------------------------------------------
# Write — the only one
# ---------------------------------------------------------------------------------

def set_field(business_id, record_type, record_id, field, value, by=None,
              root=".", surface="command-center", prof=None, person=None):
    """Write one human-owned field. Returns the path of the overlay file it created.

    Refuses, by name and without writing anything:
      * an unknown record type
      * an unsafe record id or field name
      * a record the person cannot see, worded exactly like a record that does not exist
      * a field the profile does not list as human-owned for that record type
      * a field or an action the person's role does not allow
      * a value that will not round-trip through JSON
    """
    return _write(business_id, record_type, record_id, field, value, by, root=root,
                  surface=surface, prof=prof, person=person, action="set_field")


def _write(business_id, record_type, record_id, field, value, by, root=".",
           surface="command-center", prof=None, person=None, action="set_field"):
    """The one place a field is written. `action` is what the role must be allowed to do;
    only the functions in this module choose it, never a caller over the network."""
    prof = prof or profiles.load(business_id, root)
    spec = _spec(prof, record_type)

    if not SAFE_ID.match(str(record_id or "")):
        raise WriteRefused(f"unsafe record id {record_id!r}")
    if not SAFE_FIELD.match(str(field or "")):
        raise WriteRefused(f"unsafe field name {field!r}")

    exists = (base_path(prof, record_type, record_id, root).is_file()
              or overlay_dir(prof, record_type, record_id, root).is_dir())
    # ⚠️ Visibility BEFORE existence, with one wording for both. A rep who names a
    # peer's lead gets the same answer as for a lead that was never created, so asking
    # cannot be used to learn which leads exist.
    if person is not None and access.sees(prof, person) != "all":
        rec = read(prof, record_type, record_id, root) if exists else None
        if not access.can_see(prof, person, record_type, rec, root):
            raise WriteRefused(
                f"no {record_type} {record_id!r} that you can see. A human surface sets "
                "fields on records; it does not create them.")

    # A human sets fields on a record that exists. Creating one is the fast path's job, or
    # an agent's. Without this check a typo in an id silently creates an approval for a
    # record nobody will ever look at — which is how a queue quietly fills with ghosts.
    if not exists:
        raise WriteRefused(
            f"no {record_type} {record_id!r} exists. A human surface sets fields on records; "
            "it does not create them.")

    allowed = spec.get("human_owned", [])
    if field not in allowed:
        raise WriteRefused(
            f"{field!r} is not yours to set on a {record_type}. Human-owned fields are: "
            f"{', '.join(allowed) or 'none'}. Everything else is written by the run that "
            "computed it — see architecture A1.3, one writer per field.")

    if person is not None:
        # The role on top of the ceiling: the action, the record, the field. Read fresh,
        # so the check judges the record's CURRENT owner, never a remembered one.
        why = access.why_not_write(prof, person, record_type,
                                   read(prof, record_type, record_id, root), field,
                                   action, root)
        if why:
            raise WriteRefused(f"{field!r} was not written: {why}")
        by = person.by          # provenance is the verified person; a passed `by` is ignored
    if not by:
        raise WriteRefused("a write needs a `by`: who made it")

    try:
        json.dumps(value)
    except (TypeError, ValueError) as e:
        raise WriteRefused(f"value for {field!r} is not JSON-serialisable: {e}") from e

    at = _now()
    entry = {
        "schema_version": SCHEMA_VERSION,
        "record_type": record_type,
        "record_id": record_id,
        "field": field,
        "value": value,
        "by": by,
        "at": at,
        "surface": surface,
    }
    d = overlay_dir(prof, record_type, record_id, root)
    d.mkdir(parents=True, exist_ok=True)
    # The filename opens with the ordinal, and THAT is what makes lexical filename order
    # equal write order. The timestamp after it is provenance a human can read at a
    # glance; the random tail only stops two racing writers from picking one filename.
    # ⚠️ The ordinal leads for a reason. When this was stamp-first, the coarse clock tied
    # two writes and the tiebreak fell to that random tail — so replay order was a coin
    # flip, and the test for it failed about 40% of the time instead of every time.
    # Nothing here is ever overwritten.
    stamp = at.replace(":", "").replace("-", "").replace(".", "")
    tail = uuid4().hex[:6]
    path = d / f"{_next_seq(d):0{SEQ_WIDTH}d}-{stamp}-{field.replace('.', '_')}-{tail}.json"
    # `x` fails rather than clobbering, on the astronomically unlikely collision.
    with path.open("x", encoding="utf-8") as fh:
        json.dump(entry, fh, indent=2)
        fh.write("\n")
    return str(path).replace("\\", "/")


# ---------------------------------------------------------------------------------
# The approvals queue — the durable answer to "what is waiting on me"
# ---------------------------------------------------------------------------------

# A decision a human can record. Setting one of these does NOT perform the action; the
# next run reads the status and acts. That is what makes this safe on a phone.
DECISIONS = ("approved", "needs-revision", "escalate")

# The record type a board queues into. Named here rather than in a view, because the
# writer is the only thing allowed to know where a record lands.
QUEUE_TYPE = "queue_item"
PENDING = (None, "", "pending")


def _queued_at(rec):
    """When a human write put this record into the queue, from the overlay that did it."""
    fields = ((rec.get("provenance") or {}).get("fields") or {})
    for field in ("approval.decision", "queue_status"):
        at = (fields.get(field) or {}).get("at")
        if at:
            return at
    return ""


def _waiting_since(rec, approval):
    return ((approval or {}).get("requested_at") or _queued_at(rec)
            or rec.get("updated") or "")


def pending(prof, root=".", person=None):
    """Everything waiting on a human, across every record type the profile names.

    A record is waiting when it carries an `approval` block whose decision is unset, or a
    `queue_status` of `waiting_human`. Both are human-owned fields, which is exactly why
    this needs no sync with anything: the agent writes the draft, the human writes the
    decision, and neither one ever writes the other's field.

    With a person, only what that person can see (`access.can_see`). The queue is a read
    path, and a read path is filtered like one: EspoCRM filters its reports the same way.
    """
    out = []
    memo = {}
    for record_type, spec in (prof.get("records") or {}).items():
        if not isinstance(spec, dict):
            continue
        for rid in ids(prof, record_type, root):
            try:
                rec = read(prof, record_type, rid, root)
            except WriteRefused:
                continue
            if not rec:
                continue
            if person is not None and not access.can_see(prof, person, record_type, rec,
                                                         root, memo):
                continue
            # An approval block, when there is one, is AUTHORITATIVE. `queue_status` is the
            # coarser signal, for records that carry no approval at all — and it is set by
            # whoever created the draft, so it goes stale the moment a decision is made.
            # Letting it outvote the decision leaves a cleared item sitting in the queue,
            # which is the one failure that makes a person stop trusting the queue.
            approval = rec.get("approval")
            if isinstance(approval, dict) and approval:
                waiting = approval.get("decision") in PENDING
            else:
                waiting = rec.get("queue_status") == "waiting_human"
            if not waiting:
                continue
            out.append({
                "record": record_type,
                "id": rid,
                "source": f"{spec['path']}/{rid}.json",
                # ⚠️ Whatever this record calls its human-readable name, in order of how
                # specific it is. Without `label` and `display_name` a queued item fell
                # through to its id, so the queue read "queue_item board.marketing.<a long
                # dotted node id>" — a code as the primary label, which is the one naming
                # rule this repo has.
                "what": ((rec.get("draft") or {}).get("summary") or rec.get("summary")
                         or rec.get("label") or rec.get("display_name") or ""),
                "kind": (rec.get("draft") or {}).get("kind") or "",
                # When it started WAITING, which is not when the record was last touched.
                # `requested_at` is the draft's own answer; failing that, the overlay that
                # put it in the queue knows exactly when it did so. Falling through to the
                # record's `updated` is the last resort and is flagged, because "waiting
                # since the 7th" on an item queued two minutes ago is the number somebody
                # would triage by.
                "since": _waiting_since(rec, approval),
                "since_is_approximate": not ((approval or {}).get("requested_at")
                                             or _queued_at(rec)),
                "decisions": list(DECISIONS),
            })
    out.sort(key=lambda r: (r["since"] or "", r["id"]))
    return out


def decide(business_id, record_type, record_id, decision, by=None, root=".", prof=None,
           surface="command-center", person=None):
    """Record a decision on a draft. Writes `approval.decision` and nothing else.

    It does not send, publish, pay or delete. The next run reads the status and acts —
    which is the whole reason a human can safely clear this queue from a phone.
    """
    if decision not in DECISIONS:
        raise WriteRefused(f"{decision!r} is not a decision — one of {', '.join(DECISIONS)}")
    return _write(business_id, record_type, record_id, "approval.decision", decision,
                  by, root=root, surface=surface, prof=prof, person=person, action="decide")


# ---------------------------------------------------------------------------------
# The queue — putting something ON the list, which is a create, not an edit
# ---------------------------------------------------------------------------------

def queue_add(business_id, node_id, label, source, by=None, root=".", prof=None,
              surface="command-center", why="", kind="", person=None):
    """Put a board node on the queue. Creates ONE new file and touches nothing else.

    ⚠️ `set_field` refuses a record that does not exist, on purpose: a typo in an id was
    silently creating approvals for ghost records. But *adding to a queue is a create*,
    and the two are different acts. A create needs no overlay and cannot lose a
    concurrent write, because the file it writes did not exist a moment ago — `x` mode
    makes that a fact rather than an assumption.

    Everything else holds: one writer module, provenance on the row, and **setting a
    status is not performing the action**. This writes `queued` and returns. The run that
    picks it up is what builds anything.
    """
    prof = prof or profiles.load(business_id, root)
    spec = (prof.get("records") or {}).get(QUEUE_TYPE)
    if not isinstance(spec, dict) or not spec.get("path"):
        raise WriteRefused(
            f"this business has no {QUEUE_TYPE!r} record type — a board can only queue "
            "into a path its profile declares")
    if person is not None:
        why_not = access.why_not_create(prof, person, QUEUE_TYPE, "queue")
        if why_not:
            raise WriteRefused(f"nothing was queued: {why_not}")
        by = person.by          # provenance is the verified person; a passed `by` is ignored
    if not by:
        raise WriteRefused("a write needs a `by`: who made it")

    node_id = str(node_id or "")
    if not node_id or len(node_id) > 200:
        raise WriteRefused("a queue item needs the id of the node it came from")
    rid = "".join(c if (c.isalnum() or c in "._-") else "-" for c in node_id).strip("-")[:120]
    if not SAFE_ID.match(rid):
        raise WriteRefused(f"unsafe queue id from node {node_id!r}")

    d = Path(root) / spec["path"]
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{rid}.json"
    if path.exists():
        raise WriteRefused(f"{label or node_id!r} is already on the queue")

    entry = {
        "schema_version": SCHEMA_VERSION,
        "queue_id": rid,
        "node_id": node_id,
        "label": label or node_id,
        "kind": kind or "",
        "why": why or "",
        "from_source": source or "",
        "queue_status": "queued",
        "approval": {"decision": None, "requested_at": _now()},
        "provenance": {"created": {"by": by, "at": _now(), "surface": surface}},
    }
    with path.open("x", encoding="utf-8") as fh:
        json.dump(entry, fh, indent=2)
        fh.write("\n")
    return str(path).replace("\\", "/")


# ---------------------------------------------------------------------------------
# The pipeline — a lead is a CREATE, and it arrives already enriched
# ---------------------------------------------------------------------------------

LEAD_TYPE = "lead"


def stages(prof):
    return [s for s in (prof.get("pipeline") or {}).get("stages", []) if s.get("id")]


def stage_ids(prof):
    return [s["id"] for s in stages(prof)]


def people(prof):
    return [m for m in (prof.get("people") or {}).get("members", []) if m.get("id")]


def promote(business_id, prospect, by, root=".", prof=None, owner=None,
            surface="command-center", stage="researched"):
    """Turn a scanned prospect into a lead, assigned to a person.

    ⭐ The point of promoting rather than typing: the lead arrives **already enriched** —
    its scan, its band, the evidence for that band and the source file all ride across.
    The knowledge base's CRM recipe (§5.4) is enrich → score fit → route to a rep, and the
    first two already happened in the scanner. This is the routing step.

    ⚠️ A create, like `queue_add` — a record that did not exist a moment ago cannot lose a
    concurrent write, and `x` mode makes that a fact. Every later change is an overlay.
    """
    prof = prof or profiles.load(business_id, root)
    spec = _spec(prof, LEAD_TYPE)
    valid = stage_ids(prof)
    if valid and stage not in valid:
        raise WriteRefused(f"{stage!r} is not a stage — the profile defines {', '.join(valid)}")
    if owner is not None and owner not in [m["id"] for m in people(prof)]:
        raise WriteRefused(
            f"{owner!r} is not one of this business's people — "
            f"{', '.join(m['id'] for m in people(prof)) or 'none are defined'}")

    pid = str((prospect or {}).get("id") or "")
    if not SAFE_ID.match(pid):
        raise WriteRefused(f"unsafe prospect id {pid!r}")

    d = Path(root) / spec["path"]
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{pid}.json"
    if path.exists():
        raise WriteRefused(f"{prospect.get('name') or pid} is already in the pipeline")

    now = _now()
    entry = {
        "schema_version": SCHEMA_VERSION,
        # The architecture's required-on-create list, every field real.
        "lead_id": pid,
        "captured_at": now,
        "source": (prospect.get("source")
                   or "discovered by the Places API and scanned by the web-presence engine"),
        # ⚠️ `said` is what THEY said, and a discovered prospect has not said anything yet.
        # It is empty and the conformance line will say so, rather than being filled with
        # our own words to make a schema check go green.
        "said": "",
        "urgency": "",
        "display_name": prospect.get("name") or pid,
        "domain": prospect.get("domain") or "",
        "url": prospect.get("url") or "",
        "stage": stage,
        "owner": owner or "",
        "band": prospect.get("band") or "",
        "band_why": list(prospect.get("band_why") or []),
        "band_confidence": prospect.get("band_confidence"),
        "grades": dict(prospect.get("grades") or {}),
        "failed_checks": list(prospect.get("failed") or []),
        "worst_finding": prospect.get("worst_finding") or "",
        "from_source": prospect.get("from_source") or "",
        "queue_status": "unqueued",
        "provenance": {"created": {"by": by, "at": now, "surface": surface}},
    }
    with path.open("x", encoding="utf-8") as fh:
        json.dump(entry, fh, indent=2)
        fh.write("\n")
    return str(path).replace("\\", "/")


def set_stage(business_id, record_id, stage, by=None, root=".", prof=None,
              surface="command-center", person=None):
    """Move a lead along the pipeline.

    ⚠️ **This writes a field. It fires nothing.** The stage's `fires` list is what the next
    run should QUEUE for a human, and a queued action is cleared by a person. KB §6: never
    let it send, publish, pay or delete without approval — and a pipeline that emails on a
    drag turns one mis-click into a real apology to a real business.
    """
    prof = prof or profiles.load(business_id, root)
    valid = stage_ids(prof)
    if stage not in valid:
        raise WriteRefused(f"{stage!r} is not a stage — the profile defines {', '.join(valid)}")
    return _write(business_id, LEAD_TYPE, record_id, "stage", stage, by,
                  root=root, surface=surface, prof=prof, person=person, action="set_field")


def assign(business_id, record_id, owner, by=None, root=".", prof=None,
           surface="command-center", person=None):
    """Route a lead to a person. KB §5.4's "route to the right rep".

    Giving an unowned lead an owner is `assign`; moving an owned one is `reassign`. A role
    must be allowed the one it is doing. Access moves with the owner at once, because
    `access.can_see` reads the current owner and nothing else was ever granted.
    """
    prof = prof or profiles.load(business_id, root)
    ids = [m["id"] for m in people(prof)]
    if owner not in ids:
        raise WriteRefused(f"{owner!r} is not one of this business's people — {', '.join(ids)}")
    action = "assign"
    if person is not None and SAFE_ID.match(str(record_id or "")):
        current = read(prof, LEAD_TYPE, record_id, root) or {}
        if current.get("owner"):
            action = "reassign"
    return _write(business_id, LEAD_TYPE, record_id, "owner", owner, by,
                  root=root, surface=surface, prof=prof, person=person, action=action)
