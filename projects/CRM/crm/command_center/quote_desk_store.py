"""Quote Desk records and the approval gate. The pure reading and pricing is quote_desk.py.

Tables live in the business's own CRM database, created the first time a desk-enabled profile uses
it (`qd_` tables, never touched otherwise):

    qd_jobs       the shop's past quotes: what was quoted, at what price, won or lost (imported)
    qd_rfqs       every email the desk sorted, what it read, what is missing, the ITAR stop
    qd_quotes     one draft per RFQ: the priced lines, the approval, the send, won or lost
    qd_followups  day 3 and day 7 (the profile sets the days), each a draft a person sends
    qd_outbox     everything that left (or, in v1 and the demo, was logged to leave), and who said so
    qd_log        who did what, when

THE GATE. Nothing leaves without a person. `send` refuses a quote that is not approved, and refuses
one whose draft changed after the approval (the approval is a fingerprint of the prices, the words
and the address, quote_desk.fingerprint). An edit after approval puts the quote back to draft. A
question to the buyer and a follow-up go out only on a person's click too. The desk itself never
calls `send`: there is no timer and no rule that sends.

THE DRAWING STAYS HERE. The ITAR check (inbox_core.stop_marks) is a local pattern match on the raw
email and attachments, before any reader. The demo reader runs on this machine. The AI reader, when
a profile turns it on, gets the email body only: drawings and attachments are read by the local
reader and merged in, so in v1 no drawing leaves the shop's own machine.

THE GENERIC STEPS (sorting, the stop, the sender, the gate, delivery, the log, the corrections log,
KPI counting) are inbox_core.py functions with no machining in them, so an Order Desk reuses them.

THE MAILER. v1 logs the message in qd_outbox (`quote_desk.mail: "log"`), and the estimator sends it
from the shop's own mailbox. Sending through the shop's Gmail or Microsoft 365 is the pilot's first
connector, and it plugs in at `deliver`, behind the same gate.
"""
import json
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from uuid import uuid4

from . import crm_store, inbox_core, quote_desk as qd

TABLES = (
    "CREATE TABLE IF NOT EXISTS qd_jobs (id TEXT PRIMARY KEY, quoted_on TEXT NOT NULL, customer TEXT NOT NULL, "
    "customer_domain TEXT NOT NULL DEFAULT '', part_number TEXT NOT NULL DEFAULT '', revision TEXT NOT NULL DEFAULT '', "
    "description TEXT NOT NULL DEFAULT '', material TEXT NOT NULL DEFAULT '', size TEXT, machine TEXT NOT NULL DEFAULT '', "
    "setup_hours REAL, cycle_minutes REAL, processes TEXT NOT NULL DEFAULT '[]', quantities TEXT NOT NULL DEFAULT '[]', "
    "unit_prices TEXT NOT NULL DEFAULT '[]', outcome TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '')",
    "CREATE TABLE IF NOT EXISTS qd_rfqs (id TEXT PRIMARY KEY, received_at TEXT NOT NULL, sender TEXT NOT NULL, "
    "sender_name TEXT NOT NULL DEFAULT '', domain TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL DEFAULT '', "
    "body TEXT NOT NULL DEFAULT '', attachments TEXT NOT NULL DEFAULT '[]', kind TEXT NOT NULL, status TEXT NOT NULL, "
    "itar TEXT NOT NULL DEFAULT '[]', items TEXT NOT NULL DEFAULT '[]', gaps TEXT NOT NULL DEFAULT '[]', "
    "question TEXT NOT NULL DEFAULT '', reader TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'inbox', "
    "note TEXT NOT NULL DEFAULT '')",
    "CREATE TABLE IF NOT EXISTS qd_quotes (id TEXT PRIMARY KEY, rfq_id TEXT NOT NULL UNIQUE REFERENCES qd_rfqs(id), "
    "number TEXT NOT NULL, status TEXT NOT NULL, draft TEXT NOT NULL, approved_fp TEXT NOT NULL DEFAULT '', "
    "approved_by TEXT NOT NULL DEFAULT '', approved_at TEXT NOT NULL DEFAULT '', sent_at TEXT NOT NULL DEFAULT '', "
    "sent_by TEXT NOT NULL DEFAULT '', value REAL NOT NULL DEFAULT 0, outcome TEXT NOT NULL DEFAULT '', "
    "reason TEXT NOT NULL DEFAULT '', decided_at TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS qd_followups (id TEXT PRIMARY KEY, quote_id TEXT NOT NULL REFERENCES qd_quotes(id), "
    "n INTEGER NOT NULL, due_on TEXT NOT NULL, status TEXT NOT NULL, text TEXT NOT NULL, sent_at TEXT NOT NULL DEFAULT '', "
    "sent_by TEXT NOT NULL DEFAULT '')",
    "CREATE TABLE IF NOT EXISTS qd_outbox (id TEXT PRIMARY KEY, at TEXT NOT NULL, who TEXT NOT NULL, kind TEXT NOT NULL, "
    "rfq_id TEXT NOT NULL, quote_id TEXT NOT NULL DEFAULT '', to_addr TEXT NOT NULL, subject TEXT NOT NULL, "
    "body TEXT NOT NULL, delivery TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS qd_log (at TEXT NOT NULL, who TEXT NOT NULL, action TEXT NOT NULL, ref TEXT NOT NULL, "
    "detail TEXT NOT NULL DEFAULT '')",
)
OUTCOMES = ("won", "lost", "no_answer")
REASONS = ("Price", "Lead time", "Went with incumbent", "Buyer cancelled", "No reply", "Spec change", "Other")
MAX_TEXT = 20000


def migrate(db):
    for sql in TABLES:  # one at a time: executescript would commit the caller's open transaction
        db.execute(sql)


@contextmanager
def tx(store, prof):
    qd.require(prof)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        migrate(db)
        yield db


def _log(db, who, action, ref, detail=""):
    inbox_core.log(db, "qd_log", who, action, ref, detail)


def _j(s, default=None):
    return json.loads(s) if s else default


def _id(prefix):
    return prefix + uuid4().hex[:10]


# ---- history -----------------------------------------------------------------------------------

def import_jobs(store, prof, jobs, who="setup"):
    """The shop's past quotes, from its spreadsheet or ERP export, already mapped to these keys."""
    with tx(store, prof) as db:
        for j in jobs:
            db.execute("INSERT OR REPLACE INTO qd_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                j["id"], j["quoted_on"], j["customer"], j.get("customer_domain", ""), j.get("part_number", ""),
                j.get("revision", ""), j.get("description", ""), j.get("material", ""),
                json.dumps(j.get("size")) if j.get("size") else None, j.get("machine", ""), j.get("setup_hours"),
                j.get("cycle_minutes"), json.dumps(j.get("processes") or []), json.dumps(j.get("quantities") or []),
                json.dumps(j.get("unit_prices") or []), j.get("outcome", ""), j.get("reason", "")))
        _log(db, who, "jobs.import", "", f"{len(jobs)} past jobs")
    return {"imported": len(jobs)}


def _jobs(db):
    out = []
    for r in db.execute("SELECT * FROM qd_jobs ORDER BY quoted_on DESC"):
        j = dict(r)
        for k in ("processes", "quantities", "unit_prices"):
            j[k] = _j(j[k], [])
        j["size"] = _j(j["size"])
        out.append(j)
    return out


# ---- an email in -------------------------------------------------------------------------------

_sender = inbox_core.parse_sender


def receive(store, prof, email, who="inbox", reader=None):
    """One email in: sort it, and when it is an RFQ, read it, flag it and draft the quote.
    `email`: sender ("Name <a@b>"), subject, body, attachments ([{name, text}]), received_at (optional)."""
    name, addr, domain = _sender(email.get("sender"))
    subject = crm_store.text(email.get("subject", ""), "Subject", 300)
    body = crm_store.text(email.get("body", ""), "Body", MAX_TEXT, required=True)
    atts = [{"name": str(a.get("name", ""))[:200], "text": str(a.get("text", ""))[:MAX_TEXT]}
            for a in email.get("attachments") or [] if isinstance(a, dict)][:10]
    at = email.get("received_at") or crm_store.now()
    kind = qd.classify(subject, body + "\n" + "\n".join(a["name"] for a in atts))
    rid = _id("rfq_")
    with tx(store, prof) as db:
        db.execute("INSERT INTO qd_rfqs (id, received_at, sender, sender_name, domain, subject, body, attachments, kind, "
                   "status, source) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (rid, at, addr, name, domain, subject, body, json.dumps(atts), kind,
                    "new" if kind == "rfq" else "not_rfq", email.get("source", "inbox")))
        _log(db, who, "rfq.receive", rid, kind)
    if kind == "rfq":
        process(store, prof, rid, who, reader)
    return rid


def process(store, prof, rid, who="desk", reader=None):
    """Read the RFQ and draft its quote. The ITAR check runs first, on the raw text, and a marked
    RFQ stops here: nothing is read, matched, priced or sent to a model."""
    cfg = qd.require(prof)
    with tx(store, prof) as db:
        r = db.execute("SELECT * FROM qd_rfqs WHERE id=?", (rid,)).fetchone()
        if r is None:
            raise crm_store.CRMError("No such RFQ", 404)
        atts = _j(r["attachments"], [])
        text = r["body"] + "".join(f"\n\n--- {a['name']} ---\n{a['text']}" for a in atts)
        marks = qd.itar_marks(r["subject"], text, *(a["name"] for a in atts))
        if marks:
            db.execute("UPDATE qd_rfqs SET status='stopped_itar', itar=?, items='[]', gaps='[]', question='' WHERE id=?",
                       (json.dumps(marks), rid))
            _log(db, who, "rfq.itar_stop", rid, ", ".join(marks))
            return {"status": "stopped_itar", "itar": marks}
        jobs = _jobs(db)
        received = qd.local_date(prof, r["received_at"])
    name = reader or cfg.get("extractor") or "demo"
    if name == "demo":
        raw = qd.demo_extract(prof, text, received)
    elif name == "model":  # the body only; the drawings are read here, by the local reader
        raw = _merge(qd.normalize(prof, qd.model_extract(prof, r["body"], received)),
                     qd.normalize(prof, qd.demo_extract(prof, text, received)))
    elif callable(name):
        raw, name = name(prof, text, received), "custom"
    else:
        raise crm_store.CRMError(f"Unknown RFQ reader '{name}'", 503)
    items = qd.normalize(prof, raw)
    with tx(store, prof) as db:
        rfq = dict(db.execute("SELECT * FROM qd_rfqs WHERE id=?", (rid,)).fetchone())
        draft_items = []
        for it in items:
            matches = qd.similar(prof, it, rfq["domain"], jobs)
            inputs = qd.inputs_for(prof, it, matches)
            draft_items.append({**it, "matches": [_match(m) for m in matches], "inputs": inputs})
        gaps = [(it.get("part_number", ""), qd.missing(prof, it)) for it in draft_items]
        gaps = [(pn, g) for pn, g in gaps if g]
        db.execute("UPDATE qd_rfqs SET items=?, gaps=?, question=?, reader=?, status=? WHERE id=?",
                   (json.dumps(items), json.dumps(gaps), qd.question_text(prof, rfq, gaps) if gaps else "",
                    str(name), "needs_info" if gaps else "drafted", rid))
        q = db.execute("SELECT * FROM qd_quotes WHERE rfq_id=?", (rid,)).fetchone()
        draft = {"to": rfq["sender"], "items": draft_items, "note": ""}
        draft = _priced(prof, rfq, _number(db, prof) if q is None else q["number"], draft, jobs)
        now = crm_store.now()
        if q is None:
            db.execute("INSERT INTO qd_quotes (id, rfq_id, number, status, draft, value, created_at, updated_at) "
                       "VALUES (?,?,?,?,?,?,?,?)", (_id("q_"), rid, draft["number"], "draft", json.dumps(draft),
                                                    _value(draft), now, now))
        else:
            db.execute("UPDATE qd_quotes SET status='draft', draft=?, value=?, approved_fp='', updated_at=? WHERE id=?",
                       (json.dumps(draft), _value(draft), now, q["id"]))
        _log(db, who, "rfq.read", rid, f"{name}: {len(items)} line(s), {sum(len(g) for _, g in gaps)} gap(s)")
    return {"status": "needs_info" if gaps else "drafted", "items": len(items)}


def _merge(model, local):
    """The AI reader's lines, with every field it left empty filled from the local read (which saw
    the drawings). A part only the local reader found is added."""
    left = list(local)
    out = []
    for it in model:
        loc = next((i for i in left if i.get("part_number") == it.get("part_number")), None)
        if loc is None and len(model) == 1 and len(left) == 1:  # one part each: the same part
            loc = left[0]
        if loc is not None:
            left.remove(loc)
        out.append({k: v if v not in ("", [], None) else (loc or {}).get(k, v) for k, v in it.items()})
    return out + [i for i in left if i.get("part_number")]


def _match(m):
    j = m["job"]
    return {"id": j["id"], "score": m["score"], "why": m["why"], "customer": j["customer"],
            "part_number": j["part_number"], "description": j["description"], "material": j["material"],
            "quoted_on": j["quoted_on"], "quantities": j["quantities"], "unit_prices": j["unit_prices"],
            "outcome": j["outcome"], "reason": j["reason"], "setup_hours": j["setup_hours"],
            "cycle_minutes": j["cycle_minutes"], "machine": j["machine"]}


def _number(db, prof):
    prefix = qd.require(prof).get("quote_prefix") or "Q"
    stamp = crm_store.now()[2:4] + crm_store.now()[5:7]
    n = db.execute("SELECT count(*) FROM qd_quotes WHERE number LIKE ?", (f"{prefix}-{stamp}-%",)).fetchone()[0]
    return f"{prefix}-{stamp}-{n + 1:03d}"


def _priced(prof, rfq, number, draft, jobs):
    by_id = {j["id"]: j for j in jobs}
    for it in draft["items"]:
        it["missing"] = qd.missing(prof, it)
        p = qd.price(prof, it, it["inputs"], by_id.get(it["inputs"].get("basis")))
        it.update(lines=p["lines"], sources=p["sources"])
        it["lead_days"] = qd.lead_days(prof, it)
    lead = int(draft.get("lead_override") or max([i["lead_days"] for i in draft["items"]] or [0]))
    text = qd.draft_text(prof, rfq, number, draft["items"], lead)
    if draft.get("note"):
        text["body"] = text["body"].replace("\nLead time:", "\n" + draft["note"].strip() + "\n\nLead time:", 1)
    late = [i["part_number"] for i in draft["items"] if i.get("due_date") and
            _workdays_after(qd.local_date(prof, rfq["received_at"]), lead) > date.fromisoformat(i["due_date"])]
    return {**draft, **text, "number": number, "lead_days": lead,
            "warnings": [f"{pn or 'This part'}: our lead time ({lead} working days) is past the buyer's due date"
                         for pn in late]}


def _workdays_after(start, n):
    d = start
    while n > 0:
        d += timedelta(days=1)
        n -= d.weekday() < 5
    return d


def _value(draft):
    """The quote's value for the KPIs: the first quantity break of every line (what the buyer asked first)."""
    return round(sum(i["lines"][0]["total"] for i in draft["items"] if i.get("lines")), 2)


# ---- the estimator's hands ---------------------------------------------------------------------

EDITABLE = {"machine", "setup_hours", "cycle_minutes", "markup_pct", "material_each"}
FILLABLE = {"material", "finish", "due_date", "quantities", "revision", "description", "processes"}


def edit(store, prof, who, quote_id, changes):
    """The estimator changes any number, fills a gap, or adds a note. A quote that was approved goes
    back to draft: the approval was for the old numbers."""
    with tx(store, prof) as db:
        q, rfq = _quote(db, quote_id)
        if q["status"] not in ("draft", "approved"):
            raise crm_store.CRMError("This quote has gone out. Record won or lost instead.", 409)
        draft = _j(q["draft"])
        cfg = qd.require(prof)
        for ch in changes.get("items") or []:
            i = ch.get("index")
            if type(i) is not int or not 0 <= i < len(draft["items"]):
                raise crm_store.CRMError("No such line", 400)
            it = draft["items"][i]
            for k, v in ch.items():
                if k in EDITABLE:
                    if k == "machine":
                        if v not in (cfg.get("machines") or {}):
                            raise crm_store.CRMError("That machine is not in the shop rates", 400)
                        it["inputs"]["machine"] = v
                    elif v is None and k == "material_each":
                        it["inputs"][k] = None
                    else:
                        num = float(v)
                        if not 0 <= num < 100000:
                            raise crm_store.CRMError(f"{k} is out of range", 400)
                        it["inputs"][k] = num
                elif k == "overrides":
                    it["inputs"]["overrides"] = {str(int(qty)): float(u) for qty, u in (v or {}).items()
                                                 if u not in ("", None) and float(u) >= 0}
                elif k in FILLABLE:
                    filled = qd.normalize(prof, [{**{f: it.get(f) for f in qd.FIELDS},
                                                  "processes": it.get("processes"), k: v}])[0]
                    inbox_core.correction(db, "qd_log", who, f"{quote_id}#{i}", k, it.get(k), filled[k])
                    it[k] = filled[k]
                    if k in ("material",) and it["inputs"].get("material_each") is None:
                        it["inputs"]["material_each"] = qd.material_each(prof, it["material"], it.get("size"))
        if "note" in changes:
            draft["note"] = crm_store.text(changes.get("note") or "", "Note", 2000)
        if "lead_days" in changes:
            draft["lead_override"] = int(changes["lead_days"]) if changes["lead_days"] not in ("", None) else None
        draft = _priced(prof, dict(rfq), q["number"], draft, _jobs(db))
        was = q["status"]
        db.execute("UPDATE qd_quotes SET draft=?, value=?, status='draft', approved_fp='', approved_by='', approved_at='', "
                   "updated_at=? WHERE id=?", (json.dumps(draft), _value(draft), crm_store.now(), quote_id))
        gaps = [(i.get("part_number", ""), i["missing"]) for i in draft["items"] if i["missing"]]
        if rfq["status"] in ("needs_info", "drafted"):
            db.execute("UPDATE qd_rfqs SET status=?, gaps=? WHERE id=?",
                       ("needs_info" if gaps else "drafted", json.dumps(gaps), rfq["id"]))
        _log(db, who, "quote.edit", quote_id, "approval withdrawn" if was == "approved" else "")
    return quote(store, prof, quote_id)


def approve(store, prof, who, quote_id):
    """The human approval point. Approves exactly this draft: its fingerprint is kept."""
    with tx(store, prof) as db:
        q, rfq = _quote(db, quote_id)
        if q["status"] != "draft":
            raise crm_store.CRMError(f"Only a draft can be approved (this one is {q['status']})", 409)
        draft = _j(q["draft"])
        gaps = [g for i in draft["items"] for g in i["missing"]]
        if gaps:
            raise crm_store.CRMError("Fill the missing specs first, or ask the buyer: " + "; ".join(gaps), 409)
        if not any(i.get("lines") for i in draft["items"]):
            raise crm_store.CRMError("There is no priced line to approve", 409)
        db.execute("UPDATE qd_quotes SET status='approved', approved_fp=?, approved_by=?, approved_at=?, updated_at=? "
                   "WHERE id=?", (qd.fingerprint(draft), who, crm_store.now(), crm_store.now(), quote_id))
        db.execute("UPDATE qd_rfqs SET status='approved' WHERE id=?", (rfq["id"],))
        _log(db, who, "quote.approve", quote_id, q["number"])
    return quote(store, prof, quote_id)


def deliver(prof, msg):
    """Where an approved message goes (inbox_core.deliver). v1: the outbox log only; the estimator
    sends it from the shop's own mailbox. A real connector plugs in there, behind the same gate."""
    return inbox_core.deliver(qd.require(prof).get("mail"), (prof.get("demo") or {}).get("fictional") is True)


def _out(db, prof, who, kind, rfq_id, quote_id, to, subject, body):
    msg = {"to": to, "subject": subject, "body": body}
    delivery = deliver(prof, msg)
    db.execute("INSERT INTO qd_outbox VALUES (?,?,?,?,?,?,?,?,?,?)",
               (_id("out_"), crm_store.now(), who, kind, rfq_id, quote_id, to, subject, body, delivery))
    return delivery


def send(store, prof, who, quote_id):
    """Send an approved quote. Refused unless approved, and unless the draft is exactly what was
    approved. Schedules the follow-ups as drafts."""
    cfg = qd.require(prof)
    with tx(store, prof) as db:
        q, rfq = _quote(db, quote_id)
        draft = _j(q["draft"])
        inbox_core.check_gate(q["status"], q["approved_fp"], qd.fingerprint(draft))
        if json.loads(rfq["itar"] or "[]"):
            raise crm_store.CRMError("This RFQ has an ITAR mark. The desk does not send it.", 409)
        now = crm_store.now()
        delivery = _out(db, prof, who, "quote", rfq["id"], quote_id, draft["to"], draft["subject"], draft["body"])
        db.execute("UPDATE qd_quotes SET status='sent', sent_at=?, sent_by=?, updated_at=? WHERE id=?",
                   (now, who, now, quote_id))
        db.execute("UPDATE qd_rfqs SET status='quoted' WHERE id=?", (rfq["id"],))
        sent_on = qd.local_date(prof, now)
        first = (rfq["sender_name"] or "").split(" ")[0] or "there"
        for n, days in enumerate(cfg.get("follow_up_days") or [3, 7], 1):
            text = (cfg.get("follow_up_text") or "Hi {first},\n\nChecking in on our quote {number} for {subject}. "
                    "Any questions on price or lead time? We can hold the lead time for a few more days.\n\n{signature}"
                    ).format(first=first, number=q["number"], subject=rfq["subject"], signature=cfg.get("signature", ""))
            db.execute("INSERT INTO qd_followups VALUES (?,?,?,?,?,?,?,?)",
                       (_id("fu_"), quote_id, n, (sent_on + timedelta(days=int(days))).isoformat(), "pending", text, "", ""))
        _log(db, who, "quote.send", quote_id, delivery)
    return quote(store, prof, quote_id)


def ask(store, prof, who, rfq_id, text=None):
    """Send the missing-specs question to the buyer, on a person's click."""
    with tx(store, prof) as db:
        r = db.execute("SELECT * FROM qd_rfqs WHERE id=?", (rfq_id,)).fetchone()
        if r is None:
            raise crm_store.CRMError("No such RFQ", 404)
        if r["status"] != "needs_info":
            raise crm_store.CRMError("This RFQ is not waiting on missing specs", 409)
        body = crm_store.text(text or r["question"], "Question", 5000, required=True)
        delivery = _out(db, prof, who, "question", rfq_id, "", r["sender"], "Re: " + r["subject"], body)
        db.execute("UPDATE qd_rfqs SET status='asked' WHERE id=?", (rfq_id,))
        _log(db, who, "rfq.ask", rfq_id, delivery)
    return {"status": "asked", "delivery": delivery}


def decline(store, prof, who, rfq_id, reason):
    """No bid. Recorded, so the KPIs count it as answered by a decision, not forgotten."""
    with tx(store, prof) as db:
        if db.execute("UPDATE qd_rfqs SET status='declined', note=? WHERE id=? AND status NOT IN ('quoted')",
                      (crm_store.text(reason or "No bid", "Reason", 300), rfq_id)).rowcount != 1:
            raise crm_store.CRMError("No such open RFQ", 404)
        _log(db, who, "rfq.decline", rfq_id, reason or "")
    return {"status": "declined"}


def followup_send(store, prof, who, followup_id, text=None):
    """A follow-up goes out only on a person's click, and only while the quote is still open."""
    with tx(store, prof) as db:
        f = db.execute("SELECT * FROM qd_followups WHERE id=?", (followup_id,)).fetchone()
        if f is None or f["status"] != "pending":
            raise crm_store.CRMError("No such pending follow-up", 404)
        q, rfq = _quote(db, f["quote_id"])
        if q["status"] != "sent":
            raise crm_store.CRMError("The quote is closed. No follow-up needed.", 409)
        body = crm_store.text(text or f["text"], "Follow-up", 5000, required=True)
        delivery = _out(db, prof, who, "follow_up", rfq["id"], q["id"], rfq["sender"],
                        f"Re: Quote {q['number']}", body)
        db.execute("UPDATE qd_followups SET status='sent', sent_at=?, sent_by=?, text=? WHERE id=?",
                   (crm_store.now(), who, body, followup_id))
        _log(db, who, "followup.send", followup_id, delivery)
    return {"status": "sent", "delivery": delivery}


def outcome(store, prof, who, quote_id, result, reason=""):
    """Won, lost or no answer, with a reason. Closes the follow-ups still pending."""
    if result not in OUTCOMES:
        raise crm_store.CRMError("Outcome is won, lost or no_answer", 400)
    if result == "lost" and not reason:
        raise crm_store.CRMError("Say why it was lost: that is how pricing learns", 400)
    with tx(store, prof) as db:
        q, rfq = _quote(db, quote_id)
        if q["status"] not in ("sent", "won", "lost", "no_answer"):
            raise crm_store.CRMError("Only a quote that went out can be won or lost", 409)
        db.execute("UPDATE qd_quotes SET status=?, outcome=?, reason=?, decided_at=?, updated_at=? WHERE id=?",
                   (result, result, crm_store.text(reason or "", "Reason", 300), crm_store.now(), crm_store.now(), quote_id))
        db.execute("UPDATE qd_followups SET status='cancelled' WHERE quote_id=? AND status='pending'", (quote_id,))
        _log(db, who, "quote.outcome", quote_id, f"{result}: {reason}")
    return quote(store, prof, quote_id)


def _quote(db, quote_id):
    q = db.execute("SELECT * FROM qd_quotes WHERE id=?", (quote_id,)).fetchone()
    if q is None:
        raise crm_store.CRMError("No such quote", 404)
    return q, db.execute("SELECT * FROM qd_rfqs WHERE id=?", (q["rfq_id"],)).fetchone()


# ---- the quote log, for a pilot's baseline ---------------------------------------------------------

def import_log(store, prof, rows, who="setup"):
    """Past RFQs from the shop's own quote log: received, sent, value, outcome, follow-ups. So the
    KPI page has a baseline from day one. Each row: id, received_at, sender, subject, and when quoted:
    number, sent_at, value, outcome, reason, decided_at, followups (how many were sent)."""
    with tx(store, prof) as db:
        for r in rows:
            name, addr, domain = _sender(r["sender"])
            status = "quoted" if r.get("sent_at") else r.get("status", "new")
            db.execute("INSERT INTO qd_rfqs (id, received_at, sender, sender_name, domain, subject, body, kind, status, "
                       "source) VALUES (?,?,?,?,?,?,?,?,?,?)", (r["id"], r["received_at"], addr, name, domain,
                                                                r.get("subject", ""), r.get("body", "(from the quote log)"),
                                                                "rfq", status, "log"))
            if r.get("sent_at"):
                qid = "q_" + r["id"]
                st = r.get("outcome") or "sent"
                db.execute("INSERT INTO qd_quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (qid, r["id"], r.get("number", r["id"]), st, json.dumps({"items": [], "to": addr}), "log",
                            "log", r["sent_at"], r["sent_at"], "log", float(r.get("value") or 0), r.get("outcome", ""),
                            r.get("reason", ""), r.get("decided_at", ""), r["received_at"], r["sent_at"]))
                for n in range(int(r.get("followups") or 0)):
                    db.execute("INSERT INTO qd_followups VALUES (?,?,?,?,?,?,?,?)",
                               (_id("fu_"), qid, n + 1, r["sent_at"][:10], "sent", "(from the quote log)",
                                r["sent_at"], "log"))
        _log(db, who, "log.import", "", f"{len(rows)} RFQs")
    return {"imported": len(rows)}


# ---- reads -------------------------------------------------------------------------------------

def inbox(store, prof):
    with tx(store, prof) as db:
        rows = db.execute("SELECT r.id, r.received_at, r.sender, r.sender_name, r.domain, r.subject, r.kind, r.status, "
                          "r.itar, r.gaps, q.id AS quote_id, q.number, q.status AS quote_status, q.value FROM qd_rfqs r "
                          "LEFT JOIN qd_quotes q ON q.rfq_id=r.id WHERE r.source!='log' ORDER BY r.received_at DESC").fetchall()
        customers = _customers(db)
    out = []
    for r in rows:
        d = dict(r)
        d["itar"], d["gaps"] = _j(d["itar"], []), _j(d["gaps"], [])
        d["customer"] = customers.get(d["domain"], "")
        out.append(d)
    return {"rfqs": out, "counts": {k: sum(1 for r in out if r["status"] == k) for k in
                                    ("drafted", "needs_info", "approved", "stopped_itar", "quoted", "not_rfq", "asked")}}


def _customers(db):
    return {r[0]: r[1] for r in db.execute("SELECT customer_domain, customer FROM qd_jobs GROUP BY customer_domain")}


def rfq(store, prof, rfq_id):
    with tx(store, prof) as db:
        r = db.execute("SELECT * FROM qd_rfqs WHERE id=?", (rfq_id,)).fetchone()
        if r is None:
            raise crm_store.CRMError("No such RFQ", 404)
        q = db.execute("SELECT * FROM qd_quotes WHERE rfq_id=?", (rfq_id,)).fetchone()
        out = [dict(o) for o in db.execute("SELECT * FROM qd_outbox WHERE rfq_id=? ORDER BY at", (rfq_id,))]
        customers = _customers(db)
        fus = [dict(f) for f in db.execute("SELECT * FROM qd_followups WHERE quote_id=? ORDER BY n", (q["id"],))] if q else []
    d = dict(r)
    for k, dflt in (("attachments", []), ("itar", []), ("items", []), ("gaps", [])):
        d[k] = _j(d[k], dflt)
    d["customer"] = customers.get(d["domain"], "")
    d["quote"] = _quote_out(q) if q else None
    d["outbox"], d["followups"] = out, fus
    cfg = qd.require(prof)
    d["machines"] = {k: {"label": m.get("label", k), "rate": m["rate"]} for k, m in (cfg.get("machines") or {}).items()}
    d["materials"] = sorted(cfg.get("materials") or {})
    d["reasons"] = list(REASONS)
    return d


def _quote_out(q):
    d = dict(q)
    d["draft"] = _j(d["draft"], {})
    d["approval_current"] = bool(d["approved_fp"]) and qd.fingerprint(d["draft"]) == d["approved_fp"]
    return d


def quote(store, prof, quote_id):
    with tx(store, prof) as db:
        q, _ = _quote(db, quote_id)
    return _quote_out(q)


def log(store, prof):
    with tx(store, prof) as db:
        rows = db.execute("SELECT q.id, q.number, q.status, q.value, q.sent_at, q.outcome, q.reason, q.created_at, "
                          "r.id AS rfq_id, r.subject, r.domain, r.received_at, r.sender, r.source, "
                          "(SELECT count(*) FROM qd_followups f WHERE f.quote_id=q.id AND f.status='sent') AS followups "
                          "FROM qd_quotes q JOIN qd_rfqs r ON r.id=q.rfq_id ORDER BY coalesce(nullif(q.sent_at,''), "
                          "q.created_at) DESC").fetchall()
        customers = _customers(db)
    return {"quotes": [{**dict(r), "customer": customers.get(r["domain"], r["domain"])} for r in rows],
            "reasons": list(REASONS)}


def followups(store, prof):
    today = qd.local_date(prof, crm_store.now()).isoformat()
    with tx(store, prof) as db:
        rows = db.execute("SELECT f.*, q.number, q.value, q.sent_at, r.subject, r.sender, r.sender_name, r.domain, r.id AS rfq_id "
                          "FROM qd_followups f JOIN qd_quotes q ON q.id=f.quote_id JOIN qd_rfqs r ON r.id=q.rfq_id "
                          "WHERE f.status='pending' AND q.status='sent' ORDER BY f.due_on, f.n").fetchall()
        customers = _customers(db)
    out = [{**dict(r), "customer": customers.get(r["domain"], r["domain"])} for r in rows]
    return {"today": today, "due": [r for r in out if r["due_on"] <= today], "upcoming": [r for r in out if r["due_on"] > today]}


def kpis(store, prof, weeks=4):
    cfg = qd.require(prof)
    with tx(store, prof) as db:
        rows = db.execute("SELECT r.id, r.received_at, r.status AS rfq_status, q.sent_at, q.value, q.outcome, q.decided_at, "
                          "(SELECT count(*) FROM qd_followups f WHERE f.quote_id=q.id AND f.status='sent') AS followups "
                          "FROM qd_rfqs r LEFT JOIN qd_quotes q ON q.rfq_id=r.id WHERE r.kind='rfq'").fetchall()
    data = [{**dict(r), "sent_at": r["sent_at"] or "", "closed": r["rfq_status"] in ("declined", "stopped_itar")}
            for r in rows]
    out = qd.kpis(data, datetime.fromisoformat(crm_store.now()), weeks, int(cfg.get("unanswered_after_hours", 48)),
                  int((cfg.get("follow_up_days") or [3])[0]))
    out["itar_stopped"] = sum(1 for r in data if r["rfq_status"] == "stopped_itar")
    return out


# ---- the export: the desk sits beside the shop's own tools, it does not replace them --------------

EXPORT_COLUMNS = ("quote_number", "status", "customer", "customer_email", "rfq_received", "quote_sent",
                  "part_number", "revision", "description", "material", "finish", "heat_treat", "plating",
                  "certs", "quantity", "unit_price", "line_total", "lead_days", "due_date", "approved_by",
                  "approved_at", "outcome", "reason")


def export_rows(store, prof, statuses=("approved", "sent", "won", "lost", "no_answer")):
    """One row per quantity break of every approved quote: a flat table that Excel, a quoting tool or
    an ERP (JobBOSS, E2, Paperless Parts, QuickBooks) can import. Drafts are never exported."""
    with tx(store, prof) as db:
        rows = db.execute("SELECT q.*, r.sender, r.domain, r.received_at FROM qd_quotes q JOIN qd_rfqs r ON r.id=q.rfq_id "
                          f"WHERE q.status IN ({','.join('?' * len(statuses))}) ORDER BY q.number",
                          tuple(statuses)).fetchall()
        customers = _customers(db)
    out = []
    for q in rows:
        d = _j(q["draft"], {})
        for it in d.get("items") or []:
            for ln in it.get("lines") or []:
                out.append({"quote_number": q["number"], "status": q["status"],
                            "customer": customers.get(q["domain"], q["domain"]), "customer_email": q["sender"],
                            "rfq_received": q["received_at"], "quote_sent": q["sent_at"],
                            "part_number": it.get("part_number", ""), "revision": it.get("revision", ""),
                            "description": it.get("description", ""), "material": it.get("material", ""),
                            "finish": it.get("finish", ""), "heat_treat": it.get("heat_treat", ""),
                            "plating": it.get("plating", ""), "certs": "; ".join(it.get("certs") or []),
                            "quantity": ln["qty"], "unit_price": f"{ln['unit']:.2f}", "line_total": f"{ln['total']:.2f}",
                            "lead_days": d.get("lead_days", ""), "due_date": it.get("due_date", ""),
                            "approved_by": q["approved_by"], "approved_at": q["approved_at"],
                            "outcome": q["outcome"], "reason": q["reason"]})
    return out


def export_csv(store, prof):
    import csv
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, lineterminator="\r\n")
    w.writeheader()
    w.writerows(export_rows(store, prof))
    return buf.getvalue()
