"""Phone calls and website chats: the one writer for what the AI receptionist hands the CRM (2026-10-09).

The receptionist (modules/voice-receptionist) answers the phone. When a call ends it sends
this engine one call record: who called, the recap, the full transcript, the outcome and the
reply times. This file saves it. It is the only writer of the `calls` table, and the only
path by which a phone call becomes a contact.

Website chats (C01, 2026-10-09) come the same way, with `channel: "chat"`. A chat has no caller
ID: the contact is found by the phone OR the email the visitor typed, or created from either.
A chat where the visitor left no way to reach them is a row only, never a contact.

Off by default. A profile with no `crm.calls` block refuses every call and shows no Calls page.

    "calls": {
      "owner": "<member id>",          # who owns a contact the phone creates
      "source_channel": "Phone",       # where a phone contact came from (optional)
      "chat_source_channel": "Website chat"   # where a chat contact came from (optional)
    }

What one call writes, in one commit:
  - always: one `calls` row (keyed by the phone company's call id, so a resend writes nothing)
  - a real caller: the contact found by phone number (caller ID or the number they confirmed),
    or a new one. A phone-only contact is allowed here: this path never touches the website
    form's email rule (lead_intake.py). Then one activity of kind `call` with the recap.
  - spam, robocalls, sales reps and calls with no caller speech: the row only, marked. They
    never create or touch a contact.
  - audit and outbox rows, actor `system:receptionist`, like every other writer.

    python -m command_center.call_log receive --business <id> --crm-db <db> < call.json
    python -m command_center.call_log status  --business <id> --crm-db <db>
"""
import argparse
import json
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

ACTOR = "system:receptionist"
ACTOR_NAME = "AI receptionist"
CONTRACT = "receptionist-call-v1"
SPAM = ("robocall", "solicitor", "spam", "blocked")
EMPTY = ("no_speech",)
OUTCOMES = ("booked", "callback", "transferred", "voicemail", "answered", "hung_up", "to_booking") + SPAM + EMPTY
CHANNELS = ("phone", "chat")
EMAIL = re.compile(r"[^@\s<>]+@[^@\s<>]+\.[A-Za-z]{2,}")
WORDS = {"booked": "Booked a call", "callback": "Callback requested", "transferred": "Transferred",
         "voicemail": "Voicemail", "answered": "Questions answered", "hung_up": "Hung up early",
         "robocall": "Robocall", "solicitor": "Sales call", "spam": "Spam", "blocked": "Blocked",
         "no_speech": "No caller speech", "to_booking": "Sent to booking"}


class Invalid(ValueError):
    pass


def config(prof):
    """The checked `crm.calls` block, or None when the profile has none."""
    block = (prof.get("crm") or {}).get("calls")
    if block is None:
        return None
    members = {m.get("id") for m in (prof.get("people") or {}).get("members", [])}
    if not isinstance(block, dict) or block.get("owner") not in members:
        raise ValueError("crm.calls.owner must be one of the business's people")
    return {"owner": block["owner"], "source_channel": str(block.get("source_channel") or "Phone")[:120],
            "chat_source_channel": str(block.get("chat_source_channel") or "Website chat")[:120]}


def digits(phone):
    """The last ten digits, so +1 (714) 555-0142 and 7145550142 are the same number."""
    d = re.sub(r"\D", "", str(phone or ""))
    return d[-10:] if len(d) >= 10 else d


def spoken_number(phone):
    d = digits(phone)
    return f"({d[:3]}) {d[3:6]}-{d[6:]}" if len(d) == 10 else str(phone or "")


# ---- the payload ---------------------------------------------------------------------
def _s(value, label, limit):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise Invalid(f"{label} must be text")
    return value.strip()[:limit]


def validate(body):
    if not isinstance(body, dict) or body.get("contract") != CONTRACT:
        raise Invalid(f"A call record must be an object with contract {CONTRACT}")
    call_id = _s(body.get("call_id"), "call_id", 80)
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,80}", call_id):
        raise Invalid("call_id must be the phone company's call id")
    outcome = body.get("outcome")
    if outcome not in OUTCOMES:
        raise Invalid("outcome must be one of: " + ", ".join(OUTCOMES))
    transcript = body.get("transcript") or []
    if not isinstance(transcript, list) or any(
            not isinstance(t, dict) or not isinstance(t.get("text"), str) for t in transcript):
        raise Invalid("transcript must be a list of {role, text}")
    phones = [p for p in body.get("phones") or [] if isinstance(p, str) and len(digits(p)) >= 7]
    emails = [e.strip().lower() for e in body.get("emails") or [] if isinstance(e, str) and EMAIL.fullmatch(e.strip())]
    channel = body.get("channel") or "phone"
    if channel not in CHANNELS:
        raise Invalid("channel must be one of: " + ", ".join(CHANNELS))
    notes = body.get("notes") if isinstance(body.get("notes"), dict) else {}
    ms = body.get("reply_ms_median")
    return {
        "call_id": call_id, "outcome": outcome,
        "requires_follow_up": body.get("requires_follow_up") is True,
        "spam": bool(body.get("spam")) or outcome in SPAM,
        "started_at": _s(body.get("started_at"), "started_at", 40),
        "ended_at": _s(body.get("ended_at"), "ended_at", 40),
        "caller_id": _s(body.get("caller_id"), "caller_id", 40),
        "phones": phones[:3], "emails": list(dict.fromkeys(emails))[:3], "channel": channel,
        "name": _s(body.get("name"), "name", 120),
        "business": _s(body.get("business"), "business", 200),
        "recap": _s(body.get("recap"), "recap", 4000),
        "language": _s(body.get("language"), "language", 10) or "en",
        "reply_ms_median": ms if type(ms) is int and 0 <= ms < 60_000 else None,
        "transcript": [{"role": str(t.get("role", ""))[:20], "text": t["text"][:4000]} for t in transcript][:400],
        "notes": {str(k)[:40]: v for k, v in list(notes.items())[:20] if isinstance(v, (str, bool, int))},
    }


def seconds(call):
    try:
        a = datetime.fromisoformat(call["started_at"])
        b = datetime.fromisoformat(call["ended_at"])
        return max(0, round((b - a).total_seconds()))
    except (ValueError, TypeError):
        return None


def is_real(call):
    """A person said something and it was not spam: only then does the call touch a contact."""
    return (not call["spam"] and call["outcome"] not in EMPTY
            and any(t["role"] == "caller" and t["text"].strip() and not t["text"].startswith("[")
                    for t in call["transcript"]))


def activity_body(call):
    length = seconds(call)
    kind = "Website chat" if call.get("channel") == "chat" else "Phone call"
    head = f"{kind} · {WORDS.get(call['outcome'], call['outcome'])}"
    if length is not None:
        head += f" · {max(1, round(length / 60))} min"
    return f"{head}\n\n{call['recap'] or 'No recap was made for this call.'}"


# ---- the store -----------------------------------------------------------------------
def migrate(db):
    """Version 15: one row per phone call. The transcript lives here, not on the timeline."""
    db.execute("""CREATE TABLE IF NOT EXISTS calls (
        id TEXT PRIMARY KEY, received_at TEXT NOT NULL, started_at TEXT NOT NULL DEFAULT '',
        ended_at TEXT NOT NULL DEFAULT '', seconds INTEGER, caller_id TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL DEFAULT '', business TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL,
        spam INTEGER NOT NULL DEFAULT 0, contact_id TEXT REFERENCES contacts(id),
        activity_id TEXT REFERENCES activities(id), recap TEXT NOT NULL DEFAULT '',
        transcript TEXT NOT NULL DEFAULT '[]', notes TEXT NOT NULL DEFAULT '{}',
        language TEXT NOT NULL DEFAULT 'en', reply_ms_median INTEGER)""")
    db.execute("CREATE INDEX IF NOT EXISTS calls_received ON calls(received_at)")
    db.execute("CREATE INDEX IF NOT EXISTS calls_contact ON calls(contact_id)")
    # C01 (2026-10-09): website chats share the table. Additive: an old row is a phone call.
    columns = {r[1] for r in db.execute("PRAGMA table_info(calls)")}
    if "channel" not in columns:
        db.execute("ALTER TABLE calls ADD COLUMN channel TEXT NOT NULL DEFAULT 'phone'")


def find_contact(db, phones, emails=()):
    """The live contact with one of these numbers or emails, a person before a company, newest first."""
    wanted = {digits(p) for p in phones if len(digits(p)) >= 7}
    mails = {e.strip().lower() for e in emails if e}
    if not wanted and not mails:
        return None
    rows = db.execute(
        "SELECT c.id, c.kind, c.name, c.updated_at, x.kind AS via, x.value FROM contact_channels x "
        "JOIN contacts c ON c.id=x.contact_id WHERE c.deleted_at IS NULL "
        "AND c.kind IN ('person','company')").fetchall()
    hits = [r for r in rows if (r["via"] == "phone" and digits(r["value"]) in wanted)
            or (r["via"] == "email" and r["value"].strip().lower() in mails)]
    if not hits:
        return None
    people = [r for r in hits if r["kind"] == "person"] or hits
    return max(people, key=lambda r: r["updated_at"])


def website_of(text):
    """The caller's website as the CRM keeps it (https://host/...), or "" when it is not one."""
    site = str(text or "").strip().rstrip(".").lower()
    if not site:
        return ""
    site = site if re.match(r"https?://", site) else "https://" + site
    host = site.split("://", 1)[1].split("/", 1)[0]
    return site[:300] if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}(:\d+)?", host) else ""


def fill_business(db, cid, owner, notes, stamp, record):
    """The business the caller named, onto their contact (2026-10-11): linked to a company of that
    name (found, or made, the way a web-form lead's company is), and the website on the company
    (on the person when there is no company). It only fills what is empty: never overwrites."""
    name = str(notes.get("business_name") or "").strip()[:200]
    site = website_of(notes.get("website"))
    row = db.execute("SELECT kind, company_id, website FROM contacts WHERE id=?", (cid,)).fetchone()
    if not row or row["kind"] != "person" or not (name or site):
        return
    company = row["company_id"]
    if name and not company:
        match = next((c["id"] for c in db.execute(
            "SELECT id,name FROM contacts WHERE kind='company' AND owner=? AND deleted_at IS NULL", (owner,))
            if c["name"].casefold() == name.casefold()), None)
        company = match or str(uuid4())
        if not match:
            db.execute("INSERT INTO contacts (id,owner,kind,name,created_at,updated_at,source_channel,source_campaign,"
                       "source_ad) VALUES (?,?,'company',?,?,?,'','AI receptionist','')",
                       (company, owner, name, stamp, stamp))
            record(db, "contact.create", company, {"name": name, "kind": "company", "owner": owner})
        db.execute("UPDATE contacts SET company_id=?, updated_at=?, revision=revision+1 WHERE id=?", (company, stamp, cid))
        record(db, "contact.update", cid, {"company_id": company})
    if site:
        target = company or cid
        have = db.execute("SELECT website FROM contacts WHERE id=?", (target,)).fetchone()
        if have is not None and not have["website"]:
            db.execute("UPDATE contacts SET website=?, updated_at=?, revision=revision+1 WHERE id=?", (site, stamp, target))
            record(db, "contact.update", target, {"website": site})


def receive(store, prof, cfg, call):
    """Save one call. Returns (status, receipt): 201 new, 200 already saved (nothing written).
    receipt = {"call_id", "contact_id", "activity_id", "created_contact", "new"}."""
    from . import crm_store, contact_channels, chat_handoffs
    stamp = crm_store.now()
    command = "call:" + call["call_id"]

    def record(db, action, entity_id, data):
        eid = str(uuid4())
        payload = json.dumps({"entity_id": entity_id, "data": data})
        db.execute("INSERT INTO audit VALUES (?,?,?,?,?,?,?)", (eid, command, ACTOR, action, entity_id, payload, stamp))
        db.execute("INSERT INTO outbox (id,event_type,payload,created_at) VALUES (?,?,?,?)",
                   (eid, action, payload, stamp))

    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT contact_id, activity_id FROM calls WHERE id=?", (call["call_id"],)).fetchone()
        if row:
            handoff = chat_handoffs.ensure(store, db, prof, call, row["contact_id"], stamp, record)
            return 200, {"call_id": call["call_id"], "contact_id": row["contact_id"],
                         "activity_id": row["activity_id"], "created_contact": False, "new": False, "handoff": handoff}
        cid = aid = None
        created = False
        chat = call.get("channel") == "chat"
        phones = [p for p in [call["caller_id"], *call["phones"]] if p]
        reachable = bool(phones or call.get("emails"))
        if is_real(call) and (reachable or not chat):   # a chat with no phone or email stays a row
            found = find_contact(db, phones, call.get("emails") or ())
            if call["name"]:
                name = call["name"]
            elif chat and not phones:
                name = f"Website visitor {call['emails'][0]}"
            else:
                name = f"Caller {spoken_number(call['caller_id'] or (phones or [''])[0])}"
            if found:
                cid = found["id"]
                placeholder = found["name"].startswith(("Caller ", "Website visitor"))
                if call["name"] and placeholder and found["kind"] == "person":
                    db.execute("UPDATE contacts SET name=?, updated_at=?, revision=revision+1 WHERE id=?",
                               (call["name"], stamp, cid))
                    record(db, "contact.update", cid, {"name": call["name"]})
            else:
                cid, created = str(uuid4()), True
                source = cfg["chat_source_channel"] if chat else cfg["source_channel"]
                db.execute("INSERT INTO contacts (id,owner,kind,name,created_at,updated_at,source_channel,"
                           "source_campaign,source_ad) VALUES (?,?,'person',?,?,?,?,?,'')",
                           (cid, cfg["owner"], name, stamp, stamp, source, "AI receptionist"))
                given = "Given in the chat" if chat else "Given on the call"
                seen, items = set(), []
                for label, p in [("Caller ID", call["caller_id"])] + [(given, p) for p in call["phones"]]:
                    if p and digits(p) not in seen:
                        seen.add(digits(p))
                        items.append({"kind": "phone", "label": label, "value": spoken_number(p)})
                items += [{"kind": "email", "label": given, "value": e} for e in call.get("emails") or ()]
                contact_channels.write(db, cid, items, stamp)
                first = {}
                for x in items:
                    first.setdefault(x["kind"], x["value"])
                record(db, "contact.create", cid, {"name": name, "phone": first.get("phone", ""),
                                                   "email": first.get("email", ""), "owner": cfg["owner"],
                                                   "source": source})
            fill_business(db, cid, cfg["owner"], call["notes"], stamp, record)
            aid = str(uuid4())
            db.execute("INSERT INTO activities (id,contact_id,kind,body,actor,created_at) VALUES (?,?,'call',?,?,?)",
                       (aid, cid, activity_body(call), ACTOR, stamp))
            record(db, "activity.add", aid, {"contact_id": cid, "kind": "call", "call_id": call["call_id"]})
        db.execute("INSERT INTO calls (id,received_at,started_at,ended_at,seconds,caller_id,name,business,"
                   "outcome,spam,contact_id,activity_id,recap,transcript,notes,language,reply_ms_median,channel) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                       call["call_id"], stamp, call["started_at"], call["ended_at"], seconds(call), call["caller_id"],
                       call["name"], call["business"], call["outcome"], int(call["spam"]), cid, aid, call["recap"],
                       json.dumps(call["transcript"], ensure_ascii=False), json.dumps(call["notes"], ensure_ascii=False),
                       call["language"], call["reply_ms_median"], call.get("channel") or "phone"))
        record(db, "call.receive", call["call_id"], {"outcome": call["outcome"], "spam": call["spam"],
                                                     "contact_id": cid, "channel": call.get("channel") or "phone"})
        handoff = chat_handoffs.ensure(store, db, prof, call, cid, stamp, record)
        return 201, {"call_id": call["call_id"], "contact_id": cid, "activity_id": aid,
                     "created_contact": created, "new": True, "handoff": handoff}


# ---- reading -------------------------------------------------------------------------
def _visible(store, prof, person, db):
    """All calls for someone who sees every record; otherwise only calls on their own contacts.
    Spam and empty calls have no contact, so only the all-records people see them."""
    from . import access
    if access.sees(prof, person) == "all":
        return "", ()
    scope, params = access.contact_scope(prof, person, "vc")
    return " WHERE c.contact_id IN (SELECT vc.id FROM contacts vc WHERE 1=1" + scope + ")", params


COLUMNS = ("c.id, c.received_at, c.started_at, c.ended_at, c.seconds, c.caller_id, c.name, c.business, "
           "c.outcome, c.spam, c.contact_id, c.activity_id, c.recap, c.language, c.reply_ms_median, c.channel")


def _row(r):
    out = dict(r)
    out["spam"] = bool(out["spam"])
    out["outcome_label"] = WORDS.get(out["outcome"], out["outcome"])
    if out.get("channel") == "chat" and out["outcome"] == "hung_up":
        out["outcome_label"] = "Left early"
    return out


def numbers(rows, at, days=7):
    """The strip on the Calls page: computed from the rows, nothing stored."""
    since = (at - timedelta(days=days)).isoformat()
    week = [r for r in rows if r["received_at"] >= since]
    real = [r for r in week if not r["spam"] and r["outcome"] not in EMPTY]
    waits = [r["reply_ms_median"] for r in week if r["reply_ms_median"] is not None]
    return {"days": days, "calls": len(week), "real": len(real), "spam": len(week) - len(real),
            "phone": sum(r.get("channel", "phone") != "chat" for r in week),
            "chats": sum(r.get("channel") == "chat" for r in week),
            "to_booking": sum(r["outcome"] == "to_booking" for r in week),
            "booked": sum(r["outcome"] == "booked" for r in week),
            "callbacks": sum(r["outcome"] == "callback" for r in week),
            "transfers": sum(r["outcome"] == "transferred" for r in week),
            "reply_ms_median": round(statistics.median(waits)) if waits else None}


def listing(store, prof, person, at=None, limit=200):
    store.authorize(prof, person)
    at = at or datetime.now(timezone.utc)
    with store.connection() as db:
        where, params = _visible(store, prof, person, db)
        rows = [_row(r) for r in db.execute(
            f"SELECT {COLUMNS} FROM calls c{where} ORDER BY c.received_at DESC", params)]
    return {"calls": rows[:limit], "numbers": numbers(rows, at), "enabled": config(prof) is not None}


def one(store, prof, person, call_id):
    from . import crm_store
    store.authorize(prof, person)
    with store.connection() as db:
        where, params = _visible(store, prof, person, db)
        joiner = " AND" if where else " WHERE"
        r = db.execute(f"SELECT {COLUMNS}, c.transcript, c.notes FROM calls c{where}{joiner} c.id=?",
                       params + (str(call_id)[:80],)).fetchone()
    if not r:
        raise crm_store.CRMError("Call not found", 404)
    out = _row(r)
    out["transcript"], out["notes"] = json.loads(r["transcript"]), json.loads(r["notes"])
    return out


def by_activity(db, activity_ids):
    """activity id -> call id, so a contact's timeline can open the full call."""
    ids = list(activity_ids)
    out = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = "SELECT id, activity_id FROM calls WHERE activity_id IN (%s)" % ",".join("?" * len(chunk))
        out.update({r["activity_id"]: r["id"] for r in db.execute(q, chunk)})
    return out


def purge(store, months=12, at=None, dry=False):
    """The 12-month promise (privacy page): blank the transcript, recap and notes of every call older
    than the period, and the recap on its timeline entry. The row stays (date, outcome, caller ID),
    so the numbers and the contact's history keep their shape; the contact is a customer record,
    deleted only on request. Returns how many calls were blanked."""
    from . import crm_store
    at = at or datetime.now(timezone.utc)
    limit = (at - timedelta(days=round(months * 30.44))).isoformat()
    stamp = crm_store.now()
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("SELECT id, activity_id, received_at FROM calls WHERE received_at < ? AND "
                          "(transcript != '[]' OR recap != '' OR notes != '{}')", (limit,)).fetchall()
        if dry:
            db.execute("ROLLBACK")
            return len(rows)
        for r in rows:
            db.execute("UPDATE calls SET transcript='[]', recap='', notes='{}' WHERE id=?", (r["id"],))
            if r["activity_id"]:
                db.execute("UPDATE activities SET body=? WHERE id=?", (
                    f"Call on {r['received_at'][:10]}. The transcript and summary were deleted after "
                    f"{months} months, as our privacy policy says.", r["activity_id"]))
            eid = str(uuid4())
            payload = json.dumps({"entity_id": r["id"], "data": {"months": months}})
            db.execute("INSERT INTO audit VALUES (?,?,?,?,?,?,?)",
                       (eid, "purge:" + r["id"], ACTOR, "call.purge", r["id"], payload, stamp))
            db.execute("INSERT INTO outbox (id,event_type,payload,created_at) VALUES (?,?,?,?)",
                       (eid, "call.purge", payload, stamp))
        return len(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Save a phone call from the AI receptionist, list calls, or purge old ones.")
    ap.add_argument("command", choices=("receive", "status", "purge"))
    ap.add_argument("--months", type=int, default=12, help="purge: transcripts older than this are blanked")
    ap.add_argument("--dry-run", action="store_true", help="purge: count only")
    ap.add_argument("--business", required=True)
    ap.add_argument("--crm-db", required=True)
    ap.add_argument("--root", help="the folder that holds data/command-center (default: this engine's repo)")
    a = ap.parse_args(argv)
    root = Path(a.root) if a.root else Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from command_center import crm_store, profiles
    prof = profiles.load(a.business, str(root))
    store = crm_store.Store(a.crm_db, a.business)
    if a.command == "purge":
        if a.months < 12:
            print(json.dumps({"error": "Sam decided 12 months (privacy page): refusing a shorter period"}))
            return 2
        print(json.dumps({"purged": purge(store, a.months, dry=a.dry_run), "dry_run": a.dry_run}))
        return 0
    if a.command == "status":
        with store.connection() as db:
            rows = [_row(r) for r in db.execute(f"SELECT {COLUMNS} FROM calls c ORDER BY c.received_at DESC")]
        print(json.dumps(numbers(rows, datetime.now(timezone.utc))))
        for r in rows[:20]:
            print(f"  {r['received_at'][:16]}  {r['outcome_label']:<20} {r['name'] or r['caller_id']}")
        return 0
    cfg = config(prof)
    if cfg is None:
        print(json.dumps({"error": "crm.calls is not set in this profile"}))
        return 2
    try:
        call = validate(json.loads(sys.stdin.buffer.read().decode("utf-8")))
    except (Invalid, ValueError) as e:
        print(json.dumps({"error": str(e)}))
        return 2
    status, receipt = receive(store, prof, cfg, call)
    print(json.dumps({"status": status, **receipt}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
