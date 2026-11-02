"""Research lead claims (docs/crm/crm-research-section-brief-2026-09-26.md §6 Q2, and Sam's
rules of 2026-09-27 in docs/crm/crm-cta-go-live-and-rebrand-brief-2026-09-27.md §7).

A rep takes a lead from the shared pool. It is theirs for `claim_days`; each recorded
outcome resets the clock; with no outcome it goes back to the pool by itself. Expiry is
computed on read, so nothing runs on a timer. A rep holds at most `claim_cap` open claims.
The numbers come from the profile (`crm.research`), because they change after a month of use.

Taking a lead makes a company contact owned by the rep and, when the profile names a
`claim_stage`, a pipeline card in that stage (Sam: "it goes into their pipeline as contacted").
The card has no amount: it is a lead, not a priced deal. When someone else takes the lead
later, the same contact moves to them with its whole history, so a company is never two
contacts and never worked by two people at once.

Outcomes move the card (`outcome_stages`). Interested, quoted or won make the lead a
relationship that no longer expires. Not interested (`refused`) or lost takes the lead off
the Research list for `rest_days.not_interested`. `max_touches` answers in a row with no
reply take it off for `rest_days.no_reply`. A resting lead cannot be taken; it comes back
by itself when the rest ends, with its history.

Called only from `crm_store.Store`, inside its transaction. Kept apart so the store stays
one readable file.
"""
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from . import access, crm_store, research

# The outcome ledger's closed vocabulary (auditor/ledger.py), so Research outcomes and the
# ledger feed the same 20-outcome re-rank. A copy on purpose: this package is copied into
# client repos and must not import the auditor. The screen words live in crm.js: `refused`
# reads "Not interested", `no_answer` reads "No answer yet (message left, wrong person)".
OUTCOMES = ("no_answer", "refused", "interested", "quoted", "won", "lost")
CHANNELS = ("in_person", "call", "email", "text")
OFFERS = ("A", "B", "both")
CONVERTS = ("interested", "quoted", "won")
DROPS = {"refused": "Not interested", "lost": "Lost after interest"}
WORDS = {"no_answer": "No answer yet", "refused": "Not interested", "interested": "Interested",
         "quoted": "Quoted", "won": "Won", "lost": "Lost"}
HOW = {"in_person": "in person", "call": "call", "email": "email", "text": "text"}


def migrate(db):
    """Version 8: the pipeline card and the rest. ALTER keeps every existing row."""
    columns = {r["name"] for r in db.execute("PRAGMA table_info(research_claims)")}
    if "deal_id" not in columns:
        db.execute("ALTER TABLE research_claims ADD COLUMN deal_id TEXT")
    if "rest_until" not in columns:
        db.execute("ALTER TABLE research_claims ADD COLUMN rest_until TEXT")
        db.execute("ALTER TABLE research_claims ADD COLUMN rest_reason TEXT NOT NULL DEFAULT ''")
    db.execute("CREATE INDEX IF NOT EXISTS research_claims_contact ON research_claims(contact_id)")


def policy(prof):
    spec = (prof.get("crm") or {}).get("research") or {}
    days, cap = spec.get("claim_days"), spec.get("claim_cap")
    if type(days) is not int or type(cap) is not int or days < 1 or cap < 1:
        return None
    rest = spec.get("rest_days") or {}
    touches = spec.get("max_touches")
    return {"days": days, "cap": cap,
            "stage": spec.get("claim_stage") if isinstance(spec.get("claim_stage"), str) else None,
            "moves": {k: v for k, v in (spec.get("outcome_stages") or {}).items()
                      if k in OUTCOMES and isinstance(v, str)},
            "max_touches": touches if type(touches) is int and touches > 0 else None,
            "rest": {k: rest.get(k) if type(rest.get(k)) is int and rest.get(k) > 0 else 0
                     for k in ("not_interested", "no_reply")}}


def live(row, rule, at):
    """Whether a claim row still holds the lead at time `at`."""
    if row is None or row["status"] == "released":
        return False
    if row["status"] == "converted":
        return True
    return datetime.fromisoformat(row["touched_at"]) + timedelta(days=rule["days"]) > at


def resting(row, at):
    """Off the Research list until `rest_until`: not interested, or no reply too many times."""
    return bool(row is not None and row["rest_until"]
                and datetime.fromisoformat(row["rest_until"]) > at)


def _root(prof):
    return Path(prof["_path"]).resolve().parents[2]


def _names(prof):
    return {m["id"]: m.get("name", m["id"]) for m in (prof.get("people") or {}).get("members", [])}


def _admin(prof, person):
    return access.sees(prof, person) == "all" and access.can_do(prof, person, "crm_assign")


def _touches(outcomes):
    """place_id -> (all outcomes, no-answer outcomes in a row since the last real answer)."""
    out = {}
    for o in sorted(outcomes, key=lambda o: o["created_at"]):
        total, streak = out.get(o["place_id"], (0, 0))
        out[o["place_id"]] = (total + 1, streak + 1 if o["outcome"] == "no_answer" else 0)
    return out


def read(store, prof, person):
    """Who holds which lead, and the history on each. Everyone sees the same pool."""
    rule = policy(prof)
    if rule is None:
        return {"claims": [], "outcomes": [], "touches": {}, "policy": None}
    at = datetime.fromisoformat(crm_store.now())
    names = _names(prof)
    with store.connection() as db:
        db.execute("BEGIN")
        rows = [dict(r) for r in db.execute("SELECT * FROM research_claims")]
        outcomes = [dict(r) for r in db.execute(
            "SELECT * FROM research_outcomes ORDER BY created_at DESC")]
    claims = []
    for r in rows:
        on = live(r, rule, at)
        expires = (None if r["status"] == "converted" else
                   (datetime.fromisoformat(r["touched_at"]) + timedelta(days=rule["days"])).isoformat())
        claims.append({**r, "live": on, "expires_at": expires, "resting": resting(r, at),
                       "owner_name": names.get(r["owner"], r["owner"]),
                       "mine": on and r["owner"] == person.id})
    for o in outcomes:
        o["actor_name"] = names.get(o["actor"].removeprefix("human:"), o["actor"])
    mine = sum(1 for c in claims if c["mine"] and c["status"] == "open")
    return {"claims": claims, "outcomes": outcomes,
            "touches": {k: v[0] for k, v in _touches(outcomes).items()},
            "policy": {"days": rule["days"], "cap": rule["cap"], "my_open": mine,
                       "max_touches": rule["max_touches"], "rest": rule["rest"],
                       "stage": rule["stage"], "can_reassign": _admin(prof, person),
                       "members": [{"id": m, "name": n} for m, n in names.items()]
                       if access.sees(prof, person) == "all" else []}}


def prune(snap, claims):
    """Take resting leads off the Research list and out of the counts. The history stays in
    the database; the lead comes back by itself when its rest ends."""
    rest = {c["place_id"]: c for c in claims.get("claims", []) if c.get("resting")}
    snap["resting"] = []
    if not rest:
        return snap
    lanes = [l["id"] for l in snap.get("lanes", [])]
    snap["totals"] = {k: 0 for k in lanes}
    for i in snap["industries"]:
        keep = []
        for c in i["companies"]:
            if c["id"] in rest:
                snap["resting"].append({"id": c["id"], "name": c["name"], "industry": i["label"],
                                        "until": rest[c["id"]]["rest_until"],
                                        "reason": rest[c["id"]]["rest_reason"]})
            else:
                keep.append(c)
        i["companies"] = keep
        i["counts"] = {k: sum(1 for c in keep if c["lane"] == k) for k in lanes}
        for k, v in i["counts"].items():
            snap["totals"][k] = snap["totals"].get(k, 0) + v
    return snap


def links(db, prof, contact_ids, at):
    """contact_id -> the Research lead behind it, for the pipeline: still theirs, or back in
    the pool (lapsed, released) so the card can say so instead of lingering as if worked."""
    rule = policy(prof)
    if rule is None or not contact_ids:
        return {}
    out = {}
    for r in db.execute("SELECT * FROM research_claims WHERE contact_id IS NOT NULL"):
        if r["contact_id"] in contact_ids:
            out[r["contact_id"]] = {"place_id": r["place_id"], "live": live(r, rule, at),
                                    "status": r["status"], "resting": resting(r, at),
                                    "rest_reason": r["rest_reason"], "rest_until": r["rest_until"],
                                    "deal_id": r["deal_id"], "expires_at": None if r["status"] == "converted" else
                                    (datetime.fromisoformat(r["touched_at"]) + timedelta(days=rule["days"])).isoformat()}
    return out


def touch(db, prof, contact_id, stamp):
    """A call, meeting or email logged on a Research lead's record keeps the lead: it resets
    the 14-day clock like an outcome does (sales rep review 2026-09-27: the two ways of
    logging a call did different things, so leads lapsed without warning)."""
    rule = policy(prof)
    if rule is None or not contact_id:
        return
    company = db.execute("SELECT company_id FROM contacts WHERE id=?", (contact_id,)).fetchone()
    ids = [contact_id] + ([company["company_id"]] if company and company["company_id"] else [])
    at = datetime.fromisoformat(stamp)
    for cid in ids:
        row = db.execute("SELECT * FROM research_claims WHERE contact_id=?", (cid,)).fetchone()
        if row is not None and row["status"] == "open" and live(row, rule, at):
            db.execute("UPDATE research_claims SET touched_at=?,revision=revision+1,updated_at=? "
                       "WHERE place_id=?", (stamp, stamp, row["place_id"]))


def _move(db, contact_id, owner, stamp):
    """A company and the people linked to it move together, as `contact.assign` does."""
    db.execute("UPDATE contacts SET owner=?,revision=revision+1,updated_at=? WHERE id=? OR company_id=?",
               (owner, stamp, contact_id, contact_id))


def _industry(prof, iid):
    try:
        rows = research.industry_file(prof, _root(prof))["industries"]
    except research.ResearchUnavailable:
        rows = []
    return next((i.get("label") or iid for i in rows if i.get("id") == iid), iid or "no industry")


def _lane(prof, lid):
    try:
        rows = research.industry_file(prof, _root(prof)).get("lanes") or []
    except research.ResearchUnavailable:
        rows = []
    return next((l.get("label") or lid for l in rows if l.get("id") == lid), lid or "")


def _stage_ok(store, prof, stage, db=None):
    return bool(stage) and any(s["id"] == stage for s in store.stages(prof, db))


def _card(store, db, prof, person, deal_id, stage, reason=""):
    """Move the pipeline card through the store's own deal rules (won credit, loss reason).
    Forward only, except to a closing stage, so a card a rep moved ahead is never pulled back."""
    if not deal_id or not _stage_ok(store, prof, stage, db):
        return
    deal = db.execute("SELECT * FROM deals WHERE id=? AND deleted_at IS NULL", (deal_id,)).fetchone()
    if deal is None:
        return
    stages = store.stages(prof, db)
    order = [s["id"] for s in stages]
    spec = next(s for s in stages if s["id"] == stage)
    now_at = order.index(deal["stage"]) if deal["stage"] in order else -1
    if deal["stage"] == stage or (not spec["terminal"] and order.index(stage) <= now_at):
        return
    data = {"id": deal_id, "revision": deal["revision"], "stage": stage}
    if reason or spec["outcome"] == "lost":
        data["lost_reason"] = reason or "Closed from Research"
    store._change(db, prof, person, "deal.update", data)


def change(store, db, prof, person, action, data, stamp):
    """One research.* command inside the store's transaction. Returns (place_id, created_company)."""
    CRMError = crm_store.CRMError
    rule = policy(prof)
    if rule is None:
        raise CRMError("Lead claims are not configured for this business", 404)
    pid = crm_store.text(data.get("place_id"), "Lead", 200, True)
    row = db.execute("SELECT * FROM research_claims WHERE place_id=?", (pid,)).fetchone()
    at = datetime.fromisoformat(stamp)
    on = live(row, rule, at)
    admin = _admin(prof, person)

    if action == "research.claim":
        if on:
            who = "you" if row["owner"] == person.id else _names(prof).get(row["owner"], row["owner"])
            raise CRMError(f"This lead is already taken by {who}", 409)
        if resting(row, at):
            raise CRMError(f"This lead is off the list until {row['rest_until'][:10]}: "
                           f"{row['rest_reason'] or 'resting'}", 409)
        found = research.find(prof, _root(prof), pid)
        if found is None:
            raise CRMError("Lead not found in the research pool", 404)
        if found["lane"] == "disqualified":
            raise CRMError("This lead is disqualified: " + "; ".join(found["reasons"]), 409)
        held = [r for r in db.execute("SELECT * FROM research_claims WHERE owner=? AND status='open'",
                                      (person.id,)) if live(r, rule, at)]
        if len(held) >= rule["cap"]:
            raise CRMError(f"You already hold {rule['cap']} open leads. Record an outcome "
                           "or release one first.", 409)
        created = None
        contact_id = row["contact_id"] if row else None
        if contact_id and db.execute("SELECT 1 FROM contacts WHERE id=? AND deleted_at IS NULL", (contact_id,)).fetchone():
            # Taken before: the same contact, with its history, moves to the new holder.
            _move(db, contact_id, person.id, stamp)
            db.execute("UPDATE contacts SET lifecycle='lead' WHERE id=? AND lifecycle='archived'",
                       (contact_id,))
        else:
            contact_id = str(uuid4())
            db.execute("INSERT INTO contacts (id,owner,kind,name,phone,created_at,updated_at,source_channel,"
                       "source_campaign,source_ad,address,website) VALUES (?,?,'company',?,?,?,?,'Research',?,?,?,?)",
                       (contact_id, person.id, found["name"], (found.get("phone") or "")[:60], stamp, stamp,
                        _industry(prof, found["industry"]), _lane(prof, found["lane"]),
                        (found.get("address") or "")[:300],
                        found["url"][:300] if str(found.get("url") or "").startswith(("http://", "https://")) else ""))
            created = {"id": contact_id, "name": found["name"], "kind": "company", "owner": person.id}
        db.execute("INSERT INTO activities (id,contact_id,kind,body,actor,created_at) VALUES (?,?,?,?,?,?)",
                   (str(uuid4()), contact_id, "outcome",
                    f"Taken from Research ({_industry(prof, found['industry'])}). "
                    + "; ".join(found["reasons"][:3]), "human:" + person.id, stamp))
        deal_id = row["deal_id"] if row else None
        if deal_id and not db.execute("SELECT 1 FROM deals WHERE id=? AND deleted_at IS NULL", (deal_id,)).fetchone():
            deal_id = None
        if _stage_ok(store, prof, rule["stage"], db):
            if deal_id:
                db.execute("UPDATE deals SET stage=?,lost_reason='',won_at=NULL,won_by=NULL,"
                           "revision=revision+1,updated_at=? WHERE id=?", (rule["stage"], stamp, deal_id))
            else:
                deal_id = str(uuid4())
                db.execute("INSERT INTO deals (id,contact_id,title,stage,amount_cents,currency,lost_reason,"
                           "revision,created_at,updated_at) VALUES (?,?,?,?,0,?,'',1,?,?)",
                           (deal_id, contact_id, found["name"], rule["stage"],
                            (prof.get("crm") or {}).get("currency", "USD"), stamp, stamp))
        revision = (row["revision"] + 1) if row else 1
        db.execute("INSERT INTO research_claims (place_id,owner,name,industry,status,claimed_at,touched_at,"
                   "contact_id,revision,updated_at,deal_id,rest_until,rest_reason) "
                   "VALUES (?,?,?,?,'open',?,?,?,?,?,?,NULL,'') "
                   "ON CONFLICT(place_id) DO UPDATE SET owner=excluded.owner,name=excluded.name,"
                   "industry=excluded.industry,status='open',claimed_at=excluded.claimed_at,"
                   "touched_at=excluded.touched_at,contact_id=excluded.contact_id,"
                   "revision=excluded.revision,updated_at=excluded.updated_at,"
                   "deal_id=excluded.deal_id,rest_until=NULL,rest_reason=''",
                   (pid, person.id, found["name"], found["industry"], stamp, stamp, contact_id,
                    revision, stamp, deal_id))
        from . import research_package  # the tailored script is written before the first call
        research_package.request_document(db, pid, person.id, stamp)
        return pid, created

    if action == "research.release.undo":
        # Undo right after Release (batch 2): the same claim, same clock, if nobody took it since.
        if not row or row["status"] != "released" or row["rest_until"] or (row["owner"] != person.id and not admin):
            raise CRMError("Too late to undo: the lead is not your released lead any more", 409)
        store._revision(row, data)
        if (at - datetime.fromisoformat(row["updated_at"])).total_seconds() > 600 or not live({**row, "status": "open"}, rule, at):
            raise CRMError("Too late to undo. Take the lead again from Research.", 409)
        held = [r for r in db.execute("SELECT * FROM research_claims WHERE owner=? AND status='open'",
                                      (row["owner"],)) if live(r, rule, at)]
        if len(held) >= rule["cap"]:
            raise CRMError(f"{_names(prof).get(row['owner'], row['owner'])} already holds {rule['cap']} open leads.", 409)
        db.execute("UPDATE research_claims SET status='open',revision=revision+1,updated_at=? WHERE place_id=?", (stamp, pid))
        return pid, None

    if not on:
        raise CRMError("This lead is not taken. Take it first.", 409)
    store._revision(row, data)

    if action == "research.reassign":
        if not admin:
            raise CRMError("Only an administrator can reassign a lead", 403)
        if row["status"] == "converted":
            raise CRMError("This lead is already a contact. Reassign the contact instead.", 409)
        owner = crm_store.text(data.get("owner"), "Owner", 80, True)
        if owner not in [m["id"] for m in prof["people"]["members"]]:
            raise CRMError("Choose a team member")
        db.execute("UPDATE research_claims SET owner=?,touched_at=?,revision=revision+1,updated_at=? "
                   "WHERE place_id=?", (owner, stamp, stamp, pid))
        if row["contact_id"]:
            _move(db, row["contact_id"], owner, stamp)
        return pid, None

    if row["owner"] != person.id and not admin:
        raise CRMError("This lead belongs to someone else", 403)

    if action == "research.release":
        if row["status"] == "converted":
            raise CRMError("This lead is already a contact and stays with its owner", 409)
        db.execute("UPDATE research_claims SET status='released',revision=revision+1,updated_at=? "
                   "WHERE place_id=?", (stamp, pid))
        return pid, None

    # research.outcome
    outcome, channel, offer = data.get("outcome"), data.get("channel", "in_person"), data.get("offer", "A")
    if outcome not in OUTCOMES:
        raise CRMError("Choose an outcome")
    if channel not in CHANNELS:
        raise CRMError("Choose how you approached them")
    if offer not in OFFERS:
        raise CRMError("Choose the offer")
    notes = crm_store.text(data.get("notes", ""), "Notes", 4000)
    actor = "human:" + person.id
    db.execute("INSERT INTO research_outcomes (id,place_id,actor,outcome,channel,offer,notes,created_at) "
               "VALUES (?,?,?,?,?,?,?,?)",
               (str(uuid4()), pid, actor, outcome, channel, offer, notes, stamp))
    history = [dict(r) for r in db.execute(
        "SELECT place_id,outcome,created_at FROM research_outcomes WHERE place_id=?", (pid,))]
    total, streak = _touches(history).get(pid, (1, 0))
    status, contact_id, created = row["status"], row["contact_id"], None
    if contact_id is None:  # a claim taken before version 8: make its contact now
        contact_id = str(uuid4())
        found = research.find(prof, _root(prof), pid) or {}
        db.execute("INSERT INTO contacts (id,owner,kind,name,phone,created_at,updated_at) "
                   "VALUES (?,?,'company',?,?,?,?)",
                   (contact_id, row["owner"], row["name"], (found.get("phone") or "")[:60], stamp, stamp))
        created = {"id": contact_id, "name": row["name"], "kind": "company", "owner": row["owner"]}
    db.execute("INSERT INTO activities (id,contact_id,kind,body,actor,created_at) VALUES (?,?,?,?,?,?)",
               (str(uuid4()), contact_id, "outcome",
                f"{WORDS[outcome]} ({HOW[channel]}, offer {offer}). Contact number {total}."
                + (f" {notes}" if notes else ""), actor, stamp))
    rest_until, rest_reason, stage, reason = None, "", rule["moves"].get(outcome), ""
    if outcome in CONVERTS:
        status = "converted"
    elif outcome in DROPS:
        status, rest_reason = "released", DROPS[outcome]
        rest_until = (at + timedelta(days=rule["rest"]["not_interested"])).isoformat()
        reason = rest_reason + (f": {notes}" if notes else "")
    elif rule["max_touches"] and streak >= rule["max_touches"] and status == "open":
        status, rest_reason = "released", f"No reply after {streak} tries"
        rest_until = (at + timedelta(days=rule["rest"]["no_reply"])).isoformat()
        stage, reason = rule["moves"].get("lost"), rest_reason
    if rest_until and rest_until <= stamp:
        rest_until = None  # a rest of 0 days: straight back to the pool
    _card(store, db, prof, person, row["deal_id"], stage, reason)
    if status == "released":
        db.execute("UPDATE contacts SET lifecycle='archived',revision=revision+1,updated_at=? "
                   "WHERE id=? AND lifecycle='lead'", (stamp, contact_id))
        # A dropped lead needs no callback: its open follow-ups close with it.
        db.execute("UPDATE tasks SET status='cancelled',revision=revision+1,updated_at=? "
                   "WHERE contact_id=? AND status='open'", (stamp, contact_id))
    elif data.get("next_at"):
        # The next step in the same form (sales rep review 2026-09-27): a to-do on the lead.
        due = crm_store.date_time(data["next_at"])
        title = crm_store.text(data.get("next_title") or f"Follow up with {row['name']}", "Next step", 200, True)
        db.execute("INSERT INTO tasks (id,contact_id,deal_id,title,due_at,status,completed_at,revision,"
                   "created_at,updated_at,category,action,action_detail) "
                   "VALUES (?,?,?,?,?,'open',NULL,1,?,?,'','none','')",
                   (str(uuid4()), contact_id, row["deal_id"], title, due, stamp, stamp))
    db.execute("UPDATE research_claims SET touched_at=?,status=?,contact_id=?,rest_until=?,"
               "rest_reason=?,revision=revision+1,updated_at=? WHERE place_id=?",
               (stamp, status, contact_id, rest_until, rest_reason, stamp, pid))
    return pid, created
