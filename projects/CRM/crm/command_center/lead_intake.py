"""Public lead intake: the one write that does not ask who you are (2026-09-29).

A business's own website posts its form leads here. Everything else in the CRM keeps its
sign-in. This route can only ADD a lead; it answers with a receipt (`lead_id` and the alert
state) and never with a record, a list or a count.

Off by default. A profile with no `crm.intake` block exposes nothing: the route answers 404
exactly like a route that does not exist. The block names every business-specific value:

    "intake": {
      "contract": "<payload schema id the website sends>",
      "origins": ["https://www.example.com"],        # CORS: these browser origins only
      "owner": "<member id>",                        # who owns a web lead
      "stage": "<open stage id>",                    # where its deal opens
      "source_channel": "Website",
      "task": {"title": "Call back", "due_minutes": 60, "urgent_due_minutes": 15,
               "category": "<task category id>"},
      "alert": {"to": ["<member id>"]},              # queued in the outbox, never sent here
      "max_bytes": 32000,
      "rate_limit": {"requests": 10, "window_seconds": 600},
      "client_ip_header": "CF-Connecting-IP",        # only behind a proxy that sets it
      "honeypot": "<field name>",                    # optional; the contract may have none
      "screen": {"country": "US"}                    # optional; the lead screen (lead_screen.py)
    }

Payload shape (lead v1): `contract`, `submission_id`, `submitted_at`, `form {id,label}`,
`contact {name,email,phone,company}`, `request {interest,timing,urgent,message,details}`,
`attribution {source_label,page_url,page_path,referrer,landing_page,landed_at,utm,click_ids}`,
`timing {form_loaded_at,elapsed_ms,attempt}`, `assessment`. The `Idempotency-Key` header must
equal `submission_id`. A repeated key answers 409 with the first `lead_id` and writes nothing:
the key lives in SQLite (`intake_leads`), so a restart keeps it. The payload's `attempt`
changes on a retry, so a repeat is matched on the key alone, never on a digest.

One commit writes: the person (and their company), a deal at the profile's stage, a follow-up
task (sooner when urgent), a note with the full context (form, message, answers, page, UTM,
click ids, timing), one audit row per record with actor `system:web-intake`, an outbox event
per record, and a `lead.alert` outbox event. The alert is QUEUED; no email leaves this engine.
`lead_alert.py` sends it; a send that fails for good sets that row to `failed`, and
`approvals.read` puts it in the administrator's "Waiting for you" list. `python -m command_center.lead_intake status`
lists queued and failed alerts.

The lead screen (2026-10-21). With a `screen` block, `lead_screen.check` gives each new lead a
verdict: `ok`, `review` or `spam`. Only an `ok` lead gets the call-back task and the `lead.alert`.
A held lead is still saved whole (person, company, deal, note), its deal title starts "On hold:",
the note says why, and it waits in "Waiting for you" until an administrator settles it:
`lead.release` creates the task and queues the alert then; `lead.dismiss` archives the person
and moves the deal to the lost stage. The website gets the same receipt either way, so a sender
learns nothing about the screen. `python -m command_center.lead_intake held|release|dismiss`.
"""
import argparse
import json
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from . import lead_screen, territory

PATH = "/api/intake/lead"
ACTOR = "system:web-intake"
ACTOR_NAME = "Website form"
EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
TOP = {"contract", "submission_id", "submitted_at", "form", "contact", "request", "attribution",
       "timing", "assessment"}
INTERESTS = ("sales", "support", "general")
HOLD = "On hold: "  # the deal title of a lead the screen held, until it is released
SCREEN_NAME = "Lead screen"


class Invalid(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


# ---- the profile block ---------------------------------------------------------------
def config(prof):
    """The checked intake block, or None when the profile has none. Raises ValueError on a
    block that is present but wrong, so a server refuses to start instead of losing leads."""
    block = (prof.get("crm") or {}).get("intake")
    if block is None:
        return None
    if not isinstance(block, dict):
        raise ValueError("crm.intake must be an object")
    members = {m.get("id") for m in (prof.get("people") or {}).get("members", [])}
    origins = [o.rstrip("/") for o in block.get("origins") or [] if isinstance(o, str)]
    if not origins or any(not re.fullmatch(r"https?://[A-Za-z0-9.-]+(:\d+)?", o) for o in origins):
        raise ValueError("crm.intake.origins must list the website origins, like https://www.example.com")
    if not isinstance(block.get("contract"), str) or not block["contract"]:
        raise ValueError("crm.intake.contract must name the payload schema id")
    if block.get("owner") not in members:
        raise ValueError("crm.intake.owner must be one of the business's people")
    alert = block.get("alert") or {}
    to = alert.get("to") or []
    if not isinstance(to, list) or any(t not in members for t in to):
        raise ValueError("crm.intake.alert.to must list the business's people")
    task, rate = block.get("task") or {}, block.get("rate_limit") or {}
    number = lambda v, d: v if type(v) is int and v > 0 else d
    return {
        "contract": block["contract"], "origins": origins, "owner": block["owner"],
        "stage": block.get("stage") if isinstance(block.get("stage"), str) else "",
        "source_channel": str(block.get("source_channel") or "Website")[:120],
        "task_title": str(task.get("title") or "Call back web lead")[:120],
        "due_minutes": number(task.get("due_minutes"), 60),
        "urgent_due_minutes": number(task.get("urgent_due_minutes"), 15),
        "category": task.get("category") if isinstance(task.get("category"), str) else "",
        "alert_to": to, "max_bytes": number(block.get("max_bytes"), 32_000),
        "rate_requests": number(rate.get("requests"), 10),
        "rate_window": number(rate.get("window_seconds"), 600),
        "ip_header": block.get("client_ip_header") if isinstance(block.get("client_ip_header"), str) else "",
        "honeypot": block.get("honeypot") if isinstance(block.get("honeypot"), str) else "",
        "screen": lead_screen.config(block.get("screen")),
        "reviewers": _reviewers(block.get("screen"), members),
    }


def _reviewers(screen, members):
    """Who sees and settles a held lead (Sam, 2026-10-22: the client's own admins never see
    junk). Empty means every administrator. The first reviewer owns a lead while it is held."""
    names = (screen or {}).get("reviewers") or [] if isinstance(screen, dict) else []
    if not isinstance(names, list) or any(n not in members for n in names):
        raise ValueError("crm.intake.screen.reviewers must list the business's people")
    return names


def cors_headers(cfg, origin):
    """CORS answers for an allowed origin; an empty dict (no CORS at all) otherwise."""
    if not cfg or not origin or origin.rstrip("/") not in cfg["origins"]:
        return {}
    return {"Access-Control-Allow-Origin": origin, "Vary": "Origin",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Idempotency-Key",
            "Access-Control-Max-Age": "600"}


class RateLimit:
    """Per-address sliding window, in memory. A spam guard, not a ledger: a restart clears it,
    and a repeated Idempotency-Key is answered before this is ever asked."""

    def __init__(self):
        self.lock, self.hits = threading.Lock(), {}

    def allow(self, address, limit, window, clock=time.monotonic):
        at = clock()
        with self.lock:
            recent = [t for t in self.hits.get(address, []) if at - t < window]
            if len(self.hits) > 10_000:  # a flood of addresses cannot grow this forever
                self.hits = {a: h for a, h in self.hits.items() if h and at - h[-1] < window}
            allowed = len(recent) < limit
            if allowed:
                recent.append(at)
            self.hits[address] = recent
            return allowed


def client_address(cfg, headers, peer):
    """The visitor's address: the proxy's header only when the profile names one."""
    named = cfg["ip_header"] and (headers.get(cfg["ip_header"]) or "").strip()
    return named or peer


# ---- the payload ---------------------------------------------------------------------
def _s(value, label, limit, required=False):
    if value is None and not required:
        return ""
    if not isinstance(value, str) or len(value) > limit:
        raise Invalid(f"{label} must be text, at most {limit} characters")
    value = value.strip()
    if required and not value:
        raise Invalid(f"{label} is required")
    return value


def _obj(value, label, required=False):
    if value is None and not required:
        return {}
    if not isinstance(value, dict):
        raise Invalid(f"{label} must be an object")
    return value


def validate(body, key, cfg):
    """The normalized lead, or Invalid. Mirrors what the website itself checks: a name and a
    valid email are required; everything else is optional context."""
    if not isinstance(body, dict):
        raise Invalid("The lead must be a JSON object", 400)
    extra = set(body) - TOP - ({cfg["honeypot"]} if cfg["honeypot"] else set())
    if extra:
        raise Invalid("Unexpected fields: " + ", ".join(sorted(extra))[:200])
    if body.get("contract") != cfg["contract"]:
        raise Invalid("Unknown contract")
    sid = _s(body.get("submission_id"), "submission_id", 80, True)
    if not re.fullmatch(r"[A-Za-z0-9-]{8,80}", sid) or key != sid:
        raise Invalid("Idempotency-Key must equal submission_id", 400)
    form, contact = _obj(body.get("form"), "form", True), _obj(body.get("contact"), "contact", True)
    request, attr = _obj(body.get("request"), "request"), _obj(body.get("attribution"), "attribution")
    timing = _obj(body.get("timing"), "timing")
    email = _s(contact.get("email"), "Email", 254, True)
    if not EMAIL.fullmatch(email):
        raise Invalid("Enter a valid email address")
    interest = request.get("interest")
    if interest is not None and interest not in INTERESTS:
        raise Invalid("request.interest must be sales, support, general or null")
    urgent = request.get("urgent", False)
    if type(urgent) is not bool:
        raise Invalid("request.urgent must be true or false")
    details = request.get("details") or []
    if not isinstance(details, list) or len(details) > 50 or any(
            not isinstance(d, list) or len(d) != 2 or any(not isinstance(x, str) or len(x) > 1000 for x in d)
            for d in details):
        raise Invalid("request.details must be up to 50 [label, value] text pairs")
    utm, clicks = _obj(attr.get("utm"), "attribution.utm"), _obj(attr.get("click_ids"), "attribution.click_ids")
    if len(utm) > 10 or len(clicks) > 10:
        raise Invalid("Too many attribution values")
    for k, v in list(utm.items()) + list(clicks.items()):
        if not isinstance(k, str) or len(k) > 40 or (v is not None and (not isinstance(v, str) or len(v) > 500)):
            raise Invalid("Attribution values must be short text")
    for k in ("elapsed_ms", "attempt"):
        if timing.get(k) is not None and type(timing.get(k)) is not int:
            raise Invalid(f"timing.{k} must be a whole number")
    return {
        "submission_id": sid, "submitted_at": _s(body.get("submitted_at"), "submitted_at", 40),
        "form_id": _s(form.get("id"), "form.id", 60, True), "form_label": _s(form.get("label"), "form.label", 120),
        "name": _s(contact.get("name"), "Name", 200, True), "email": email,
        "phone": _s(contact.get("phone"), "Phone", 60), "company": _s(contact.get("company"), "Company", 200),
        # The state routes the lead to its territory (territory.py). A state we cannot read is left
        # empty, never a refused lead: the owner still gets it.
        "state": territory.code(_s(contact.get("state"), "State", 40)) or "",
        "interest": interest or "", "timing": _s(request.get("timing"), "request.timing", 120), "urgent": urgent,
        "message": _s(request.get("message"), "request.message", 5000),
        "details": [[a.strip(), b.strip()] for a, b in details],
        "source_label": _s(attr.get("source_label"), "attribution.source_label", 200),
        **{k: _s(attr.get(k), f"attribution.{k}", 2000) for k in ("page_url", "page_path", "referrer", "landing_page")},
        "landed_at": _s(attr.get("landed_at"), "attribution.landed_at", 40),
        "utm": {k: v for k, v in utm.items() if v}, "click_ids": {k: v for k, v in clicks.items() if v},
        "form_loaded_at": _s(timing.get("form_loaded_at"), "timing.form_loaded_at", 40),
        "elapsed_ms": timing.get("elapsed_ms"), "attempt": timing.get("attempt"),
        "assessment": _s(body.get("assessment"), "assessment", 20_000),
    }


def is_spam(body, cfg):
    return bool(cfg["honeypot"] and isinstance(body, dict) and body.get(cfg["honeypot"]))


def note(lead):
    """Everything the form carried, readable on the contact's history."""
    lines = [f"Website form: {lead['form_label'] or lead['form_id']} ({lead['form_id']})"]
    if lead["urgent"]:
        lines.append("URGENT")
    for label, key in (("Interest", "interest"), ("Timing", "timing"), ("Company", "company")):
        if lead[key]:
            lines.append(f"{label}: {lead[key]}")
    if lead["message"]:
        lines += ["", "Message:", lead["message"]]
    if lead["details"]:
        lines += ["", "Answers:"] + [f"- {a}: {b}" for a, b in lead["details"]]
    lines += ["", "Where it came from:"]
    for label, key in (("Source", "source_label"), ("Page", "page_url"), ("Path", "page_path"),
                       ("Referrer", "referrer"), ("Landing page", "landing_page"), ("Landed", "landed_at"),
                       ("Submitted", "submitted_at"), ("Form opened", "form_loaded_at")):
        if lead[key]:
            lines.append(f"- {label}: {lead[key]}")
    lines += [f"- utm_{k}: {v}" for k, v in lead["utm"].items()]
    lines += [f"- {k}: {v}" for k, v in lead["click_ids"].items()]
    if lead["elapsed_ms"] is not None:
        lines.append(f"- Time on form: {round(lead['elapsed_ms'] / 1000)} s (attempt {lead['attempt']})")
    if lead["assessment"]:
        lines += ["", "Self-assessment (as sent):", lead["assessment"][:4000]]
    lines += ["", f"Submission ID: {lead['submission_id']}"]
    return "\n".join(lines)


# ---- the store -----------------------------------------------------------------------
def migrate(db):
    db.execute("""CREATE TABLE IF NOT EXISTS intake_leads (
        id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, contract TEXT NOT NULL,
        contact_id TEXT NOT NULL REFERENCES contacts(id), deal_id TEXT, task_id TEXT,
        alert_id TEXT REFERENCES outbox(id), payload TEXT NOT NULL, received_at TEXT NOT NULL)""")
    # Version 25: the lead screen's verdict, and who settled a held lead. `hold` is '' for a lead
    # that was never held, then 'held', then 'released' or 'dismissed'.
    columns = {r[1] for r in db.execute("PRAGMA table_info(intake_leads)")}
    for column, kind in (("verdict", "TEXT NOT NULL DEFAULT 'ok'"), ("reasons", "TEXT NOT NULL DEFAULT '[]'"),
                         ("hold", "TEXT NOT NULL DEFAULT ''"), ("settled_by", "TEXT"), ("settled_at", "TEXT")):
        if column not in columns:
            db.execute(f"ALTER TABLE intake_leads ADD COLUMN {column} {kind}")


def _notification(db, alert_id):
    if alert_id is None:
        return "queued"  # a held lead: the website is told nothing about the screen
    row = alert_id and db.execute("SELECT status FROM outbox WHERE id=?", (alert_id,)).fetchone()
    status = row["status"] if row else "failed"
    return {"sent": "sent", "failed": "failed"}.get(status, "queued")


def existing(store, key):
    """The receipt for an already-saved key, or None. Read-only; used before the rate limit."""
    with store.connection() as db:
        row = db.execute("SELECT id, alert_id FROM intake_leads WHERE idempotency_key=?", (key,)).fetchone()
        return {"lead_id": row["id"], "notification": _notification(db, row["alert_id"])} if row else None


def _record(db, command, actor, stamp, action, entity_id, data, event_type=None):
    """One audit row and one outbox event for a record this file writes."""
    eid = str(uuid4())
    payload = json.dumps({"entity_id": entity_id, "data": data})
    db.execute("INSERT INTO audit VALUES (?,?,?,?,?,?,?)", (eid, command, actor, action, entity_id, payload, stamp))
    db.execute("INSERT INTO outbox (id,event_type,payload,created_at) VALUES (?,?,?,?)",
               (eid, event_type or action, payload, stamp))
    return eid


def _follow_up(db, prof, cfg, record, stamp, lead_id, cid, did, name, urgent, form):
    """The call-back task and the queued `lead.alert`: what an ok lead gets at once, and a held
    lead gets when it is released. Returns (task_id, alert_id)."""
    from . import crm_store
    minutes = cfg["urgent_due_minutes"] if urgent else cfg["due_minutes"]
    due = (datetime.fromisoformat(stamp) + timedelta(minutes=minutes)).isoformat(timespec="microseconds")
    tid = str(uuid4())
    task_title = (("URGENT: " if urgent else "") + f"{cfg['task_title']}: {name}")[:200]
    valid = {c["id"] for c in crm_store.categories(prof)}
    category = cfg["category"] if cfg["category"] in valid else ""
    db.execute("INSERT INTO tasks (id,contact_id,deal_id,title,due_at,status,completed_at,revision,created_at,"
               "updated_at,category,action,action_detail) VALUES (?,?,?,?,?,'open',NULL,1,?,?,?,'none','')",
               (tid, cid, did, task_title, due, stamp, stamp, category))
    record("task.create", tid, {"contact_id": cid, "deal_id": did, "title": task_title, "due_at": due})
    people = {m["id"]: m for m in (prof.get("people") or {}).get("members", [])}
    alert = record("lead.alert", lead_id, {
        "lead_id": lead_id, "contact_id": cid, "deal_id": did, "task_id": tid, "urgent": urgent,
        "subject": ("URGENT " if urgent else "") + f"New website lead: {name}", "form": form,
        "to": [{"id": p, "emails": people[p].get("emails", [])} for p in cfg["alert_to"]]})
    return tid, alert


def receive(store, prof, cfg, lead, raw):
    """Save one lead. Returns (status, receipt): 201 for a new lead, 409 for a known key."""
    from . import crm_store, contact_channels
    stamp = crm_store.now()
    command = "intake:" + lead["submission_id"]
    screen = lead_screen.check(lead, cfg.get("screen"), cfg.get("origins") or ())  # email_list has no screen
    held = screen["verdict"] != "ok"

    def record(action, entity_id, data, event_type=None):
        return _record(db, command, ACTOR, stamp, action, entity_id, data, event_type)

    try:
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id, alert_id FROM intake_leads WHERE idempotency_key=?",
                             (lead["submission_id"],)).fetchone()
            if row:
                return 409, {"lead_id": row["id"], "notification": _notification(db, row["alert_id"])}
            owner, currency = cfg["owner"], (prof.get("crm") or {}).get("currency", "USD")
            if held and cfg.get("reviewers"):
                owner = cfg["reviewers"][0]  # off the client's list until it is released
            company_id = None
            if lead["company"]:
                match = next((c for c in db.execute(
                    "SELECT id,name FROM contacts WHERE kind='company' AND owner=? AND deleted_at IS NULL", (owner,))
                    if c["name"].casefold() == lead["company"].casefold()), None)
                company_id = match["id"] if match else str(uuid4())
                if not match:
                    db.execute("INSERT INTO contacts (id,owner,kind,name,created_at,updated_at,territory) VALUES (?,?,'company',?,?,?,?)",
                               (company_id, owner, lead["company"], stamp, stamp, lead.get("state", "")))
                    record("contact.create", company_id, {"name": lead["company"], "kind": "company", "owner": owner})
            cid = str(uuid4())
            clicks = "; ".join(f"{k}: {v}" for k, v in lead["click_ids"].items())
            source = (cfg["source_channel"], (lead["utm"].get("campaign") or lead["source_label"])[:120],
                      (clicks or lead["utm"].get("content") or "")[:120])
            db.execute("INSERT INTO contacts (id,owner,kind,company_id,name,created_at,updated_at,source_channel,"
                       "source_campaign,source_ad,territory) VALUES (?,?,'person',?,?,?,?,?,?,?,?)",
                       (cid, owner, company_id, lead["name"], stamp, stamp, *source, lead.get("state", "")))
            contact_channels.write(db, cid, [{"kind": "email", "label": "Web form", "value": lead["email"]}] +
                                   ([{"kind": "phone", "label": "Web form", "value": lead["phone"]}] if lead["phone"] else []), stamp)
            record("contact.create", cid, {"name": lead["name"], "email": lead["email"], "phone": lead["phone"],
                                           "company_id": company_id, "owner": owner, "source": source})
            stages = store.stages(prof, db)
            open_ids = [s["id"] for s in stages if not s.get("outcome")]
            stage = cfg["stage"] if cfg["stage"] in open_ids else (open_ids[0] if open_ids else stages[0]["id"])
            did = str(uuid4())
            title = ((HOLD if held else "") + f"{lead['form_label'] or 'Website lead'}: {lead['company'] or lead['name']}")[:200]
            db.execute("INSERT INTO deals (id,contact_id,title,stage,amount_cents,currency,lost_reason,revision,"
                       "created_at,updated_at) VALUES (?,?,?,?,0,?,'',1,?,?)", (did, cid, title, stage, currency, stamp, stamp))
            record("deal.create", did, {"contact_id": cid, "title": title, "stage": stage})
            body = note(lead)
            if held:
                body = "\n".join([f"{SCREEN_NAME}: held for review ({screen['verdict']}). No alert was sent.",
                                  "Do not call until an administrator releases it.",
                                  *[f"- {r['text']}" for r in screen["reasons"]], "", body])
            aid = str(uuid4())
            db.execute("INSERT INTO activities (id,contact_id,kind,body,actor,created_at) VALUES (?,?,'note',?,?,?)",
                       (aid, cid, body, ACTOR, stamp))
            record("activity.add", aid, {"contact_id": cid, "kind": "note"})
            lead_id = str(uuid4())
            tid = alert = None
            if not held:
                tid, alert = _follow_up(db, prof, cfg, record, stamp, lead_id, cid, did, lead["name"], lead["urgent"],
                                        lead["form_label"] or lead["form_id"])
            db.execute("INSERT INTO intake_leads (id,idempotency_key,contract,contact_id,deal_id,task_id,alert_id,"
                       "payload,received_at,verdict,reasons,hold) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (lead_id, lead["submission_id"], cfg["contract"], cid, did, tid, alert, raw, stamp,
                        screen["verdict"], json.dumps(screen["reasons"]), "held" if held else ""))
            return 201, {"lead_id": lead_id, "notification": "queued"}
    except Exception as e:  # noqa: BLE001
        import sqlite3
        if isinstance(e, sqlite3.IntegrityError) and "idempotency_key" in str(e):
            found = existing(store, lead["submission_id"])  # a racing twin committed first
            if found:
                return 409, found
        raise


def settle(store, db, prof, person, data, stamp, command):
    """`lead.release` or `lead.dismiss` on a held lead, inside the CRM command's transaction.
    Only an administrator (sees everything, may assign) settles one. Release creates the task
    and queues the alert now; dismiss archives the person and moves the deal to lost. A
    dismissed lead can still be released, which reopens both."""
    from . import access, crm_store
    CRMError = crm_store.CRMError
    if set(data) - {"id", "step"}:
        raise CRMError("Unexpected fields in command")
    if not (access.sees(prof, person) == "all" and access.can_do(prof, person, "crm_assign")):
        raise CRMError("Only an administrator releases a held website lead", 403)
    cfg = config(prof)
    if cfg is None:
        raise CRMError("This business has no website form", 404)
    lid = crm_store.text(data.get("id"), "Lead", 80, True)
    row = db.execute("SELECT * FROM intake_leads WHERE id=?", (lid,)).fetchone()
    if row is None:
        raise CRMError("Record not found", 404)
    step = data.get("step")
    if step not in ("release", "dismiss"):
        raise CRMError("Choose release or dismiss")
    if row["hold"] not in (("held", "dismissed") if step == "release" else ("held",)):
        raise CRMError("This lead is not on hold", 409)
    if cfg["reviewers"] and person.id not in cfg["reviewers"]:
        raise CRMError("Only the lead screen's reviewers settle a held lead", 403)
    actor = "human:" + person.id
    names = {m["id"]: m.get("name", m["id"]) for m in (prof.get("people") or {}).get("members", [])}

    def record(action, entity_id, payload, event_type=None):
        return _record(db, command, actor, stamp, action, entity_id, payload, event_type)

    contact = db.execute("SELECT * FROM contacts WHERE id=?", (row["contact_id"],)).fetchone()
    deal = row["deal_id"] and db.execute("SELECT * FROM deals WHERE id=?", (row["deal_id"],)).fetchone()
    stages = store.stages(prof, db)
    if step == "release":
        body = json.loads(row["payload"])
        form = body.get("form") or {}
        tid, alert = row["task_id"], row["alert_id"]
        if alert is None:
            tid, alert = _follow_up(db, prof, cfg, record, stamp, row["id"], row["contact_id"], row["deal_id"],
                                    contact["name"], (body.get("request") or {}).get("urgent") is True,
                                    form.get("label") or form.get("id") or "Website form")
        if contact["lifecycle"] == "archived" or contact["owner"] != cfg["owner"]:
            # back on the client's list: the intake owner takes it from the reviewer
            db.execute("UPDATE contacts SET lifecycle='lead',owner=?,revision=revision+1,updated_at=? WHERE id=?",
                       (cfg["owner"], stamp, contact["id"]))
            record("contact.update", contact["id"], {"lifecycle": "lead", "owner": cfg["owner"]})
            company = contact["company_id"] and db.execute(
                "SELECT id, owner FROM contacts WHERE id=?", (contact["company_id"],)).fetchone()
            if company and company["owner"] != cfg["owner"] and company["owner"] in cfg["reviewers"]:
                db.execute("UPDATE contacts SET owner=?,revision=revision+1,updated_at=? WHERE id=?",
                           (cfg["owner"], stamp, company["id"]))
                record("contact.update", company["id"], {"owner": cfg["owner"]})
        if deal:
            open_ids = [s["id"] for s in stages if not s.get("outcome")]
            stage = deal["stage"] if deal["stage"] in open_ids else (
                cfg["stage"] if cfg["stage"] in open_ids else (open_ids[0] if open_ids else deal["stage"]))
            title = deal["title"].removeprefix(HOLD)
            db.execute("UPDATE deals SET title=?,stage=?,lost_reason='',revision=revision+1,updated_at=? WHERE id=?",
                       (title, stage, stamp, deal["id"]))
            record("deal.update", deal["id"], {"title": title, "stage": stage})
        text = (f"{SCREEN_NAME}: released by {names.get(person.id, person.id)}. "
                "The call-back task is open and the alert is queued.")
        db.execute("UPDATE intake_leads SET hold='released',task_id=?,alert_id=?,settled_by=?,settled_at=? WHERE id=?",
                   (tid, alert, actor, stamp, row["id"]))
    else:
        db.execute("UPDATE contacts SET lifecycle='archived',revision=revision+1,updated_at=? WHERE id=?",
                   (stamp, contact["id"]))
        record("contact.update", contact["id"], {"lifecycle": "archived"})
        lost = next((s["id"] for s in stages if s.get("outcome") == "lost"), None)
        if deal and lost:
            reason = "Not a real lead (lead screen)"
            db.execute("UPDATE deals SET stage=?,lost_reason=?,revision=revision+1,updated_at=? WHERE id=?",
                       (lost, reason, stamp, deal["id"]))
            record("deal.update", deal["id"], {"stage": lost, "lost_reason": reason})
        text = f"{SCREEN_NAME}: set aside by {names.get(person.id, person.id)} as not a real lead. No alert was sent."
        db.execute("UPDATE intake_leads SET hold='dismissed',settled_by=?,settled_at=? WHERE id=?",
                   (actor, stamp, row["id"]))
    aid = str(uuid4())
    db.execute("INSERT INTO activities (id,contact_id,kind,body,actor,created_at) VALUES (?,?,'note',?,?,?)",
               (aid, row["contact_id"], text, actor, stamp))
    record("activity.add", aid, {"contact_id": row["contact_id"], "kind": "note"})
    return {"ok": True, "id": row["id"], "hold": "released" if step == "release" else "dismissed"}


def held(db):
    """Website leads the screen is holding, oldest first."""
    rows = db.execute("SELECT i.id, i.contact_id, i.verdict, i.reasons, i.received_at, c.name FROM intake_leads i "
                      "JOIN contacts c ON c.id=i.contact_id WHERE i.hold='held' ORDER BY i.received_at").fetchall()
    return [{"id": r["id"], "contact_id": r["contact_id"], "name": r["name"], "verdict": r["verdict"],
             "reasons": json.loads(r["reasons"]), "at": r["received_at"]} for r in rows]


def alerts(db, status=None):
    """Lead alerts in the outbox, newest first; `status` narrows to queued or failed ones."""
    rows = db.execute("SELECT o.id, o.status, o.created_at, o.payload FROM outbox o "
                      "WHERE o.event_type='lead.alert' ORDER BY o.created_at DESC").fetchall()
    out = [{"id": r["id"], "status": r["status"], "at": r["created_at"],
            **json.loads(r["payload"])["data"]} for r in rows]
    return [a for a in out if status is None or a["status"] == status]


def waiting(db, prof=None, person=None):
    """Failed alerts, and alerts the worker stopped in the middle of, for the administrator's
    "Waiting for you" list (approvals.py). The reason comes from the worker (lead_alert.py).
    Held leads show only to the screen's reviewers when the profile names them."""
    from . import lead_alert
    stuck = set(lead_alert.stuck(db))
    out = []
    for a in alerts(db):
        if a["status"] == "failed":
            why = lead_alert.reason(db, a["id"])
            title = f"Alert not delivered: {a['subject']}" + (f" ({why})" if why else "")
        elif a["id"] in stuck:
            title = f"Alert may not have been delivered: {a['subject']} (the sender stopped mid-send)"
        else:
            continue
        out.append({"kind": "alert", "id": a["id"], "contact_id": a["contact_id"],
                    "title": title[:300], "by": ACTOR_NAME, "at": a["at"]})
    reviewers = ((config(prof) or {}).get("reviewers") or []) if prof else []
    if reviewers and (person is None or person.id not in reviewers):
        return out
    for h in held(db):  # a lead the screen held: release it to the client, or set it aside
        label = "Looks like spam" if h["verdict"] == "spam" else "Check this lead"
        why = " ".join(r["text"] for r in h["reasons"])
        out.append({"kind": "lead", "id": h["id"], "contact_id": h["contact_id"], "verdict": h["verdict"],
                    "title": f"{label}, no alert sent yet: {h['name']}. {why}"[:400],
                    "by": SCREEN_NAME, "at": h["at"]})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Website leads: alert status, and leads the screen holds.")
    ap.add_argument("command", choices=("status", "held", "release", "dismiss"))
    ap.add_argument("--business", required=True)
    ap.add_argument("--crm-db", required=True)
    ap.add_argument("--lead", help="release / dismiss: the held lead's id (from `held`)")
    ap.add_argument("--as", dest="person", help="release / dismiss: the administrator's member id")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent), help="where data/ lives")
    a = ap.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from command_center import crm_store
    store = crm_store.Store(a.crm_db, a.business)
    if a.command in ("release", "dismiss"):
        if not a.lead or not a.person:
            ap.error(f"{a.command} needs --lead and --as")
        from command_center import identity, profiles
        prof = profiles.load(a.business, Path(a.root))
        member = next((m for m in (prof.get("people") or {}).get("members", []) if m.get("id") == a.person), None)
        if member is None:
            ap.error(f"{a.person} is not one of the business's people")
        person = identity.Person(member["id"], member.get("name", member["id"]), member.get("role", ""))
        result = store.execute(prof, person, {"action": "lead." + a.command, "request_id": str(uuid4()),
                                              "data": {"id": a.lead, "step": a.command}})
        print(f"lead {result['id']}: {result['hold']}")
        return
    with store.connection() as db:
        if a.command == "held":
            rows = held(db)
            print(f"website leads on hold: {len(rows)}")
            for h in rows:
                print(f"  {h['at']}  {h['verdict']:<6} {h['id']}  {h['name']}")
                for r in h["reasons"]:
                    print(f"      - {r['text']}")
            return
        rows = alerts(db)
        leads = db.execute("SELECT count(*) FROM intake_leads").fetchone()[0]
        on_hold = db.execute("SELECT count(*) FROM intake_leads WHERE hold='held'").fetchone()[0]
    print(f"website leads saved: {leads} (on hold: {on_hold})")
    for status in ("pending", "sending", "failed", "sent"):
        print(f"alerts {status}: {sum(1 for r in rows if r['status'] == status)}")
    for r in rows:
        if r["status"] != "sent":
            print(f"  {r['at']}  {r['status']:<8} {r['subject']}")


if __name__ == "__main__":
    main()
