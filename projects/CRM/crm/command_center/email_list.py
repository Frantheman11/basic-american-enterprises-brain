"""Freebie leads and the email list: the one writer for email consent (2026-10-09, brief P07).

The website gives two things away for an email: three posts written from the visitor's website
(`/free-posts`) and the Second Brain install guide (`/second-brain/guide`). Each request becomes a
lead the same way a website form does (`lead_intake.receive`: contact, deal, follow-up task, note,
queued `lead.alert`), and this file adds the one thing a form lead does not carry: whether the
person said yes to the weekly email, and the token that lets them take it back.

The email address lives here and nowhere else (the brief's rule). The site server never writes it
to disk; it hands it to this writer and keeps it in memory only until the delivery email is sent.

Off by default. A profile with no `crm.email_list` block refuses every request.

    "email_list": {
      "owner": "<member id>",                 # who owns a freebie lead
      "alert_to": ["<member id>"],            # who the lead alert goes to
      "stage": "<open stage id>",             # optional; first open stage otherwise
      "source_channel": "Website freebie",    # optional
      "task": {"title": "Follow up", "due_minutes": 1440, "category": "<id>"}   # optional
    }

The table `email_consent` (schema 16) holds one row per email address:
  email (lowercased) · contact_id (the latest lead) · consent 0/1 · token (unique, random, the
  unsubscribe key) · source · consented_at · unsubscribed_at · updated_at.

Consent rules: a ticked box turns consent on. An unticked box never turns it on and never turns
it off (the delivery they asked for is not marketing). The unsubscribe link turns it off, with a
note on the contact. The token never changes for an address, so an old email's link still works.

    python -m command_center.email_list receive     --business <id> --crm-db <db> < request.json
    python -m command_center.email_list unsubscribe --business <id> --crm-db <db> --token <t>
    python -m command_center.email_list status      --business <id> --crm-db <db>
"""
import argparse
import json
import re
import secrets
import sys
from pathlib import Path
from uuid import uuid4

# Acquisition describes the visit, never the referral/commission code above.
UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
PRODUCTS = {"websites", "crm", "content", "second-brain", "receptionist", "custom-builds", "gym",
            "quote-desk", "order-desk", "payroll-desk"}
LANDING_PATHS = {"/", "/links", "/free", "/book", "/free-posts", "/second-brain/guide", "/sample/dashboard", "/sample/listings", "/questionnaire",
                 "/diagnostic"} | {
    "/" + product for product in PRODUCTS}


def clean_utm(raw):
    """Five bounded campaign labels only: no arbitrary query strings or objects."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key in UTM_KEYS:
        value = raw.get(key)
        if isinstance(value, str):
            value = re.sub(r"[^A-Za-z0-9 ._:/-]", "", value[:1000]).strip()[:200]
            if value:
                out[key] = value
    return out


def clean_acquisition(raw):
    """Sanitize optional, visitor-supplied acquisition context at every write boundary."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    utm = clean_utm(raw.get("utm"))
    if utm:
        out["utm"] = utm
    path = raw.get("landing_path")
    if isinstance(path, str) and path in LANDING_PATHS:
        out["landing_path"] = path
    product = raw.get("product")
    if isinstance(product, str) and product in PRODUCTS:
        out["product"] = product
    return out


ACTOR = "system:email-list"
ACTOR_NAME = "Website freebie"
CONTRACT = "freebie-v1"
SOURCES = {"free-posts": "Free: 3 posts from your website",
           "free-guide": "Free: Second Brain install guide",
           "email-list": "Weekly email sign-up",
           "gym-founding": "Gym system: founding gym application",
           "quote-desk": "Quote Desk: call or diagnostic request",
           "order-desk": "Order Desk: call or diagnostic request",
           "payroll-desk": "Prevailing Wage Desk: call or diagnostic request",
           "diagnostic": "Desk diagnostic request"}
# Applications carry a name, phone, company and short answers; a freebie carries only the email.
APPLICATIONS = {"gym-founding", "quote-desk", "order-desk", "payroll-desk", "diagnostic"}
EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
TOKEN = re.compile(r"[A-Za-z0-9_-]{20,64}")


class Invalid(ValueError):
    pass


def config(prof):
    """The `crm.email_list` block, shaped like lead_intake's config so its writer can save the
    lead. None when the profile has no block."""
    block = (prof.get("crm") or {}).get("email_list")
    if block is None:
        return None
    members = {m.get("id") for m in (prof.get("people") or {}).get("members", [])}
    if not isinstance(block, dict) or block.get("owner") not in members:
        raise ValueError("crm.email_list.owner must be one of the business's people")
    to = block.get("alert_to") or []
    if not isinstance(to, list) or any(t not in members for t in to):
        raise ValueError("crm.email_list.alert_to must list the business's people")
    task = block.get("task") or {}
    number = lambda v, d: v if type(v) is int and v > 0 else d
    return {"contract": CONTRACT, "owner": block["owner"], "alert_to": to,
            "stage": block.get("stage") if isinstance(block.get("stage"), str) else "",
            "source_channel": str(block.get("source_channel") or "Website freebie")[:120],
            "task_title": str(task.get("title") or "Follow up freebie lead")[:120],
            "due_minutes": number(task.get("due_minutes"), 1440),
            "urgent_due_minutes": number(task.get("due_minutes"), 1440),
            "category": task.get("category") if isinstance(task.get("category"), str) else ""}


def migrate(db):
    """Version 16: one consent row per email address."""
    db.execute("""CREATE TABLE IF NOT EXISTS email_consent (
        email TEXT PRIMARY KEY, contact_id TEXT REFERENCES contacts(id), consent INTEGER NOT NULL DEFAULT 0,
        token TEXT NOT NULL UNIQUE, source TEXT NOT NULL, consented_at TEXT, unsubscribed_at TEXT,
        updated_at TEXT NOT NULL)""")


# ---- the request ---------------------------------------------------------------------
def validate(body):
    """The checked request, or Invalid. The site checks the same things first."""
    if not isinstance(body, dict):
        raise Invalid("The request must be a JSON object")
    sid = str(body.get("submission_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9-]{8,80}", sid):
        raise Invalid("submission_id is required")
    email = str(body.get("email") or "").strip()
    if len(email) > 254 or not EMAIL.fullmatch(email):
        raise Invalid("Enter a valid email address")
    source = body.get("source")
    if source not in SOURCES:
        raise Invalid("source must be one of " + ", ".join(SOURCES))
    if type(body.get("consent")) is not bool:
        raise Invalid("consent must be true or false")
    utm = body.get("utm") or {}
    if not isinstance(utm, dict) or len(utm) > 10 or any(
            not isinstance(k, str) or len(k) > 40 or not isinstance(v, str) or len(v) > 200 for k, v in utm.items()):
        raise Invalid("utm must be up to ten short text values")
    text = lambda k, n: str(body.get(k) or "").strip()[:n]
    context = clean_acquisition(body.get("acquisition"))
    utm = context.get("utm") or clean_utm({"utm_" + k.removeprefix("utm_"): v for k, v in utm.items()})
    answers = body.get("answers") or []
    if source not in APPLICATIONS:
        answers = []
    if not isinstance(answers, list) or len(answers) > 8 or any(
            not isinstance(a, list) or len(a) != 2 or not all(isinstance(x, str) for x in a) for a in answers):
        raise Invalid("answers must be up to eight [label, value] pairs")
    if source in APPLICATIONS and not (text("name", 120) and text("company", 200)):
        raise Invalid("An application needs a name and a business name")
    return {"submission_id": sid, "email": email, "source": source, "consent": body["consent"],
            "website": text("website", 300), "page_url": text("page_url", 500), "note": text("note", 2000),
            "acquisition": context, "application": source in APPLICATIONS,
            "name": text("name", 120) if source in APPLICATIONS else "",
            "phone": text("phone", 40) if source in APPLICATIONS else "",
            "company": text("company", 200) if source in APPLICATIONS else "",
            "answers": [[a[0].strip()[:60], a[1].strip()[:200]] for a in answers if a[1].strip()],
            "src": text("src", 120), "utm": {k.removeprefix("utm_"): v for k, v in utm.items() if v}}


def _domain(website):
    host = re.sub(r"^https?://", "", website or "", flags=re.I).split("/")[0].lower()
    return host.removeprefix("www.")


def lead_for(req):
    """The request in lead_intake's normalized shape, so one writer saves every website lead."""
    details = [["Form" if req.get("application") else "Freebie", SOURCES[req["source"]]],
               ["Weekly email", "Yes, ticked the box" if req["consent"] else "No, box not ticked"]]
    if req["website"]:
        details.insert(1, ["Website", req["website"]])
    acquisition = req.get("acquisition") or {}
    details[1:1] = req.get("answers") or []
    if acquisition.get("product"):
        details.append(["Product viewed", acquisition["product"]])
    blank = {k: "" for k in ("phone", "interest", "timing", "referrer", "landing_page", "landed_at",
                             "submitted_at", "form_loaded_at", "assessment", "page_path")}
    return {**blank, "submission_id": req["submission_id"], "form_id": req["source"],
            "form_label": SOURCES[req["source"]], "name": req.get("name") or req["email"], "email": req["email"],
            "phone": req.get("phone") or "", "interest": "sales" if req.get("application") else "",
            "company": req.get("company") or _domain(req["website"]), "urgent": False, "message": req["note"], "details": details,
            "source_label": req["src"] or req["source"], "page_url": req["page_url"],
            "landing_page": acquisition.get("landing_path", ""), "utm": req["utm"],
            "click_ids": {}, "elapsed_ms": None, "attempt": None}


# ---- the writes ----------------------------------------------------------------------
def receive(store, prof, body):
    """Save one freebie request: the lead (via lead_intake) and the consent row. Returns the
    receipt {lead_id, token, consent, repeat}. A repeated submission_id writes nothing new."""
    from . import crm_store, lead_intake
    cfg = config(prof)
    if cfg is None:
        raise Invalid("This CRM does not take freebie leads (no crm.email_list block)")
    req = validate(body)
    if req["application"]:   # a person asked to be called back, not for a download
        cfg = {**cfg, "task_title": "Call back: " + SOURCES[req["source"]]}
    safe_body = {**body, "acquisition": req["acquisition"], "utm": req["utm"]}
    status, receipt = lead_intake.receive(store, prof, cfg, lead_for(req), json.dumps(safe_body)[:20_000])
    key = req["email"].lower()
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        stamp = crm_store.now()
        row = db.execute("SELECT * FROM email_consent WHERE email=?", (key,)).fetchone()
        contact = db.execute("SELECT contact_id FROM intake_leads WHERE id=?", (receipt["lead_id"],)).fetchone()
        cid = contact["contact_id"] if contact else (row["contact_id"] if row else None)
        if row is None:
            token = secrets.token_urlsafe(24)
            db.execute("INSERT INTO email_consent VALUES (?,?,?,?,?,?,?,?)",
                       (key, cid, int(req["consent"]), token, req["source"],
                        stamp if req["consent"] else None, None, stamp))
            consent = req["consent"]
        else:
            token, consent = row["token"], bool(row["consent"]) or req["consent"]
            if status == 201:
                db.execute("UPDATE email_consent SET contact_id=?, consent=?, consented_at=?, unsubscribed_at=?, "
                           "updated_at=? WHERE email=?",
                           (cid, int(consent), stamp if req["consent"] else row["consented_at"],
                            None if req["consent"] else row["unsubscribed_at"], stamp, key))
        if status == 201 and req["consent"]:
            _record(db, "email_list.subscribe", cid, {"source": req["source"]}, stamp)
    return {"lead_id": receipt["lead_id"], "token": token, "consent": consent, "repeat": status == 409}


def _record(db, action, cid, data, stamp):
    eid = str(uuid4())
    payload = json.dumps({"entity_id": cid, "data": data})
    db.execute("INSERT INTO audit VALUES (?,?,?,?,?,?,?)", (eid, f"{action}:{eid}", ACTOR, action, cid, payload, stamp))
    db.execute("INSERT INTO outbox (id,event_type,payload,created_at) VALUES (?,?,?,?)", (eid, action, payload, stamp))


def unsubscribe(store, token):
    """Turn consent off for the address that owns this token. Returns {"ok", "already"}; an
    unknown token is {"ok": False}. Never says which address it was."""
    from . import crm_store
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        return {"ok": False}
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM email_consent WHERE token=?", (token,)).fetchone()
        if row is None:
            return {"ok": False}
        if not row["consent"] and row["unsubscribed_at"]:
            return {"ok": True, "already": True}
        stamp = crm_store.now()
        db.execute("UPDATE email_consent SET consent=0, unsubscribed_at=?, updated_at=? WHERE token=?",
                   (stamp, stamp, token))
        if row["contact_id"]:
            aid = str(uuid4())
            db.execute("INSERT INTO activities (id,contact_id,kind,body,actor,created_at) VALUES (?,?,'note',?,?,?)",
                       (aid, row["contact_id"], "Unsubscribed from the weekly email (the link in our email). "
                        "Do not send marketing email.", ACTOR, stamp))
            _record(db, "email_list.unsubscribe", row["contact_id"], {"activity_id": aid}, stamp)
    return {"ok": True, "already": False}


def may_email(store, email):
    """True only when this address said yes and has not taken it back. Every marketing send asks."""
    with store.connection() as db:
        row = db.execute("SELECT consent FROM email_consent WHERE email=?", (str(email).lower(),)).fetchone()
    return bool(row and row["consent"])


def counts(store):
    with store.connection() as db:
        r = db.execute("SELECT count(*) n, coalesce(sum(consent),0) yes FROM email_consent").fetchone()
        by = db.execute("SELECT source, count(*) n FROM email_consent GROUP BY source").fetchall()
    return {"addresses": r["n"], "consented": r["yes"], "by_source": {x["source"]: x["n"] for x in by}}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Freebie leads and email consent.")
    ap.add_argument("command", choices=("receive", "unsubscribe", "status"))
    ap.add_argument("--business", required=True)
    ap.add_argument("--crm-db", required=True)
    ap.add_argument("--root", help="where data/command-center lives (default: this repo)")
    ap.add_argument("--token")
    a = ap.parse_args(argv)
    here = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(here))
    from command_center import crm_store, profiles
    store = crm_store.Store(a.crm_db, a.business)
    try:
        if a.command == "receive":
            prof = profiles.load(a.business, a.root or str(here))
            body = json.loads(sys.stdin.buffer.read().decode("utf-8"))
            print(json.dumps(receive(store, prof, body)))
        elif a.command == "unsubscribe":
            print(json.dumps(unsubscribe(store, a.token or "")))
        else:
            print(json.dumps(counts(store)))
    except (Invalid, ValueError) as e:
        print(json.dumps({"error": str(e)}))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
