"""Order Desk v1: emailed purchase orders into draft orders a rep approves. One engine; one profile
per distributor. On only when the profile says `order_desk.enabled: true`. Without it no table is
made and no route answers, so any other business runs exactly as before.

The flow (docs/products/order-desk-v1-design.md):
  1. Watch the shared inbox: v1 reads a folder of .eml files that a mailbox rule fills (`inbox_dir`).
  2. Sort each email: order, change, quote, status, other (order_desk_read.py).
  3. Read the PO, match the customer, match each line to a SKU, check it (order_desk_match.py).
  4. Show the draft beside the PO with flags. Red and yellow flags each need a rep's click.
  5. THE GATE: nothing reaches the ERP or the customer without `order.approve` by a person with the
     "approve" right. Approval writes the ERP import file and a confirmation DRAFT; sending it is a
     second click (`order.send_confirmation`). v1 never sends mail: a pilot sends from the client's
     own mailbox, and the demo writes to a sent folder.
  6. Learn: a rep's SKU correction, and every approved non-table match, goes into the cross-reference.
  7. Draft "where is my order" replies (`reply.send` is the rep's click).

Profile block:
    "order_desk": {"enabled": true, "name": "...", "timezone": "America/Los_Angeles", "cutoff": "15:00",
      "data_dir": "erp-export", "inbox_dir": "inbox", "out_dir": "private/order-desk", "ocr_dir": "",
      "reader": "demo" | "claude", "ai": {"key_env": "...", "model": "..."},
      "layouts": ["generic", "qbo"], "first_number": 5001, "from_address": "...", "signature": "...",
      "rights": {"admin": ["read", "approve", "admin"], "rep": ["read", "approve"]}}
Paths are relative to the business root. Rights are per CRM role; no role list changes in profiles.py.

Storage: `od_*` tables in the CRM's SQLite file, made the first time an enabled profile uses it, and
the desk's own event log (`od_events`: who, what, when, the request id; a repeated request id
returns the first answer and changes nothing).
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import crm_store, inbox_core, order_desk_erp, order_desk_match, order_desk_read, order_desk_view

SCHEMA = 1
INBOX_ACTOR = "system:inbox"
RIGHTS = ("read", "approve", "admin")
ACTIONS = {"inbox.check": "approve", "order.open": "read", "line.accept": "approve", "line.apply": "approve",
           "line.set": "approve", "line.remove": "approve", "order.set": "approve", "order.accept": "approve",
           "order.approve": "approve", "order.reject": "approve", "order.send_confirmation": "approve",
           "order.mark_error": "admin", "reply.send": "approve", "message.dismiss": "approve"}
OPEN = inbox_core.OPEN


def config(prof):
    cfg = prof.get("order_desk")
    return cfg if isinstance(cfg, dict) and cfg.get("enabled") is True else None


def enabled(prof):
    return config(prof) is not None


def require(prof):
    cfg = config(prof)
    if cfg is None:
        raise crm_store.CRMError("The order desk is not enabled for this business", 404)
    return cfg


def rights(prof, person):
    table = require(prof).get("rights") or {"admin": list(RIGHTS)}
    return {r for r in table.get(getattr(person, "role", ""), []) if r in RIGHTS}


def check(prof, person, right):
    if right not in rights(prof, person):
        raise crm_store.CRMError("You do not have permission for this order desk action", 403)


def zone(prof):
    return ZoneInfo(require(prof).get("timezone") or "UTC")


def local_now(prof):
    return datetime.fromisoformat(crm_store.now()).astimezone(zone(prof))


def path(root, prof, keyname, default=""):
    p = require(prof).get(keyname) or default
    return str(Path(root) / p) if p else ""


def migrate(db):
    row = db.execute("SELECT value FROM metadata WHERE key='order_desk_schema'").fetchone()
    if row and row[0] == str(SCHEMA):
        return
    for statement in TABLES.split(";"):
        if statement.strip():
            db.execute(statement)
    db.execute("INSERT OR REPLACE INTO metadata VALUES ('order_desk_schema', ?)", (str(SCHEMA),))


TABLES = """
CREATE TABLE IF NOT EXISTS od_messages (
    id TEXT PRIMARY KEY, message_id TEXT NOT NULL UNIQUE, received_at TEXT NOT NULL, sender TEXT NOT NULL,
    from_name TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', docs TEXT NOT NULL DEFAULT '[]',
    files TEXT NOT NULL DEFAULT '[]', reply TEXT, reply_sent_at TEXT, reply_sent_by TEXT, ingested_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS od_orders (
    id TEXT PRIMARY KEY, message_id TEXT NOT NULL REFERENCES od_messages(id), number INTEGER UNIQUE,
    customer_id TEXT, customer_how TEXT NOT NULL DEFAULT '', po_number TEXT NOT NULL DEFAULT '',
    need_by TEXT NOT NULL DEFAULT '', ship_to TEXT NOT NULL DEFAULT '', bill_to TEXT NOT NULL DEFAULT '',
    reader TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'draft',
    flags TEXT NOT NULL DEFAULT '[]', revision INTEGER NOT NULL DEFAULT 1, edits INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, opened_at TEXT, opened_by TEXT, approved_at TEXT, approved_by TEXT,
    rejected_at TEXT, reject_reason TEXT, confirmation TEXT, confirmation_sent_at TEXT, confirmation_sent_by TEXT,
    files TEXT NOT NULL DEFAULT '{}', error_at TEXT, error_note TEXT);
CREATE TABLE IF NOT EXISTS od_lines (
    order_id TEXT NOT NULL REFERENCES od_orders(id), n INTEGER NOT NULL, customer_part TEXT NOT NULL DEFAULT '',
    our_part TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', po_qty REAL, po_uom TEXT NOT NULL DEFAULT '',
    po_price REAL, sku TEXT, source TEXT NOT NULL, first_source TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 0,
    alternatives TEXT NOT NULL DEFAULT '[]', qty REAL, uom TEXT NOT NULL DEFAULT '', price REAL, expected_price REAL,
    price_basis TEXT NOT NULL DEFAULT '', flags TEXT NOT NULL DEFAULT '[]', edited INTEGER NOT NULL DEFAULT 0,
    removed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (order_id, n));
CREATE TABLE IF NOT EXISTS od_xref (
    customer_id TEXT NOT NULL, part_key TEXT NOT NULL, customer_part TEXT NOT NULL, sku TEXT NOT NULL,
    how TEXT NOT NULL, taught_by TEXT NOT NULL, taught_at TEXT NOT NULL, PRIMARY KEY (customer_id, part_key));
CREATE TABLE IF NOT EXISTS od_senders (
    email TEXT PRIMARY KEY, customer_id TEXT NOT NULL, taught_by TEXT NOT NULL, taught_at TEXT NOT NULL);
""" + inbox_core.events_table("od_events")


class Desk(order_desk_view.Views):
    """One distributor's desk: the CRM store (its SQLite file), the profile and the business root."""

    def __init__(self, store, prof, root):
        self.store, self.prof, self.root, self.cfg = store, prof, str(root), require(prof)
        self.out = Path(path(root, prof, "out_dir", "private/order-desk"))

    # ------------------------------------------------------------ plumbing

    def db(self):
        return self.store.connection()

    def book(self, db):
        learned = [dict(r) for r in db.execute("SELECT customer_id, customer_part, sku FROM od_xref")]
        senders = [dict(r) for r in db.execute("SELECT email, customer_id FROM od_senders")]
        return order_desk_match.Book(path(self.root, self.prof, "data_dir", "erp-export"), learned, senders)

    def _event(self, db, actor, action, order_id=None, message_id=None, request_id=None, detail=None, result=None):
        inbox_core.log_event(db, "od_events", crm_store.now(), actor, action, order_id, message_id, request_id, detail, result)

    # ------------------------------------------------------------ 1-3: watch, sort, read, match

    def check_inbox(self, actor=INBOX_ACTOR):
        """Read every new .eml in the inbox folder. Returns how many were new, by kind."""
        new = {}
        for _, raw in inbox_core.new_messages(path(self.root, self.prof, "inbox_dir", "inbox")):
            kind = self.ingest(raw, actor)
            if kind:
                new[kind] = new.get(kind, 0) + 1
        return {"new": sum(new.values()), "kinds": new}

    def ingest(self, raw, actor=INBOX_ACTOR):
        """One email in. Returns its kind, or None when it was already read."""
        mail = inbox_core.parse_email(raw)
        mid = hashlib.sha256(mail["message_id"].encode()).hexdigest()[:16]
        with self.db() as db:
            migrate(db)
            if db.execute("SELECT 1 FROM od_messages WHERE id=?", (mid,)).fetchone():
                return None
            ref = local_now(self.prof).date()
            got = order_desk_read.read(mail, self.cfg, path(self.root, self.prof, "ocr_dir"), ref)
            files = []
            folder = self.out / "files" / mid
            for i, f in enumerate(mail["files"]):
                folder.mkdir(parents=True, exist_ok=True)
                safe = f"{i}-" + "".join(c for c in f["name"] if c.isalnum() or c in "._-")[-80:]
                (folder / safe).write_bytes(f["data"])
                files.append({"name": f["name"], "stored": safe, "type": f["type"]})
            docs = [{**d, "file": i} for i, d in enumerate(got["docs"])]
            db.execute("INSERT INTO od_messages (id, message_id, received_at, sender, from_name, subject, body, kind, docs, "
                       "files, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (mid, mail["message_id"], mail["received_at"] or crm_store.now(), mail["from"], mail["from_name"],
                        mail["subject"], mail["body"], got["kind"], json.dumps(docs), json.dumps(files), crm_store.now()))
            book = self.book(db)
            if got["po"] is not None:
                self._draft(db, book, mid, mail, got["kind"], got["po"])
            elif got["kind"] == "status":
                cid, _ = book.match_customer(mail["from"])
                msg = {"subject": mail["subject"], "body": mail["body"], "from_name": mail["from_name"], "sender": mail["from"]}
                reply = order_desk_erp.status_reply(msg, book.customers.get(cid), book, self._desk_orders(db), self._desk_facts())
                db.execute("UPDATE od_messages SET reply=? WHERE id=?", (json.dumps(reply), mid))
            elif got["kind"] in ("quote", "other"):
                pass  # listed with its kind; the Quote Desk takes quotes, a person files the rest
            self._event(db, actor, "message.ingest", message_id=mid, detail={"kind": got["kind"], "subject": mail["subject"]})
        return got["kind"]

    def _draft(self, db, book, mid, mail, kind, po):
        cid, how = book.match_customer(mail["from"], po.get("bill_to", ""))
        oid = "od_" + mid
        db.execute("INSERT INTO od_orders (id, message_id, customer_id, customer_how, po_number, need_by, ship_to, bill_to, "
                   "reader, source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (oid, mid, cid, how, po.get("po_number", ""), po.get("need_by", ""), po.get("ship_to", ""),
                    po.get("bill_to", ""), po.get("reader", ""), po.get("source", ""), crm_store.now()))
        for n, ln in enumerate(po["lines"], 1):
            db.execute("INSERT INTO od_lines (order_id, n, customer_part, our_part, description, po_qty, po_uom, po_price, "
                       "source, first_source) VALUES (?,?,?,?,?,?,?,?,'none','none')",
                       (oid, n, ln.get("customer_part", ""), ln.get("our_part", ""), ln.get("description", ""),
                        ln.get("qty"), ln.get("uom", ""), ln.get("price")))
        self._rematch(db, book, oid, kind, first=True)

    def _rematch(self, db, book, oid, kind=None, first=False):
        """Match and check every line that a rep has not edited, then the order-level flags."""
        o = db.execute("SELECT o.*, m.kind FROM od_orders o JOIN od_messages m ON m.id=o.message_id WHERE o.id=?", (oid,)).fetchone()
        cid = o["customer_id"]
        for ln in db.execute("SELECT * FROM od_lines WHERE order_id=? AND edited=0 AND removed=0", (oid,)).fetchall():
            po_line = {"customer_part": ln["customer_part"], "our_part": ln["our_part"], "description": ln["description"],
                       "qty": ln["po_qty"], "uom": ln["po_uom"], "price": ln["po_price"]}
            match = book.match_line(cid, po_line) if cid else {"sku": None, "source": "none", "confidence": 0, "alternatives": []}
            draft, flags = book.check_line(cid, po_line, match) if cid else ({"sku": None, "qty": ln["po_qty"], "uom": ln["po_uom"]}, [])
            db.execute("UPDATE od_lines SET sku=?, source=?, first_source=CASE WHEN ? THEN ? ELSE first_source END, confidence=?, "
                       "alternatives=?, qty=?, uom=?, price=?, expected_price=?, price_basis=?, flags=? WHERE order_id=? AND n=?",
                       (draft["sku"], match["source"], first, match["source"], match["confidence"],
                        json.dumps(match["alternatives"]), draft["qty"], draft["uom"], draft.get("price"),
                        draft.get("expected_price"), draft.get("price_basis", ""), json.dumps(flags), oid, ln["n"]))
        self._order_flags(db, book, oid)

    def _order_flags(self, db, book, oid):
        o = db.execute("SELECT o.*, m.kind FROM od_orders o JOIN od_messages m ON m.id=o.message_id WHERE o.id=?", (oid,)).fetchone()
        others = [(r["customer_id"], r["po_number"], f"desk order {r['number'] or 'draft'} (received {r['received_at'][:10]})")
                  for r in db.execute("SELECT o.customer_id, o.po_number, o.number, m.received_at FROM od_orders o JOIN od_messages m "
                                      "ON m.id=o.message_id WHERE o.id<>? AND o.status IN ('draft','approved') AND m.received_at<?",
                                      (oid, db.execute("SELECT received_at FROM od_messages WHERE id=?", (o["message_id"],)).fetchone()[0]))]
        po = {"po_number": o["po_number"], "need_by": o["need_by"]}
        fresh = book.check_order(o["customer_id"], po, o["kind"], others, local_now(self.prof).date().isoformat())
        kept = {f["code"]: f for f in json.loads(o["flags"]) if f.get("resolved")}
        for f in fresh:  # a flag the rep already cleared stays cleared while it says the same thing
            if f["code"] in kept and kept[f["code"]]["text"] == f["text"]:
                f.update(resolved=True, resolved_by=kept[f["code"]].get("resolved_by"))
        db.execute("UPDATE od_orders SET flags=? WHERE id=?", (json.dumps(fresh), oid))

    def _desk_orders(self, db):
        return [dict(r) for r in db.execute("SELECT customer_id, po_number, number, status, "
                                            "json_extract(confirmation, '$.ship_date') AS ship_date FROM od_orders "
                                            "WHERE status='approved'")]

    def _desk_facts(self):
        return {"name": self.cfg.get("name") or self.prof.get("name", ""), "cutoff": self.cfg.get("cutoff", "15:00"),
                "signature": self.cfg.get("signature", ""), "from_address": self.cfg.get("from_address", "")}

    # ------------------------------------------------------------ writes: every one through act()

    def act(self, person, body):
        """One rep action. body: {action, request_id, order?, revision?, line?, ...}. A repeated request id
        returns the first answer. An order action names the revision it saw; a stale one is refused."""
        if not isinstance(body, dict) or body.get("action") not in ACTIONS:
            raise crm_store.CRMError("Unknown order desk action", 400)
        action = body["action"]
        check(self.prof, person, ACTIONS[action])
        try:
            rid = inbox_core.request_id(body)
        except ValueError as e:
            raise crm_store.CRMError(str(e), 400) from None
        if action == "inbox.check":
            return self.check_inbox(f"person:{person.id}")
        with self.db() as db:
            migrate(db)
            db.commit()  # the migration's own write, so the action starts its own transaction
            db.execute("BEGIN IMMEDIATE")
            seen = inbox_core.replayed(db, "od_events", rid)
            if seen is not None:
                return seen
            book = self.book(db)
            actor = f"person:{person.id}"
            if action in ("reply.send", "message.dismiss"):
                result = self._message_act(db, person, action, body)
                self._event(db, actor, action, message_id=body.get("message"), request_id=rid, result=result)
                return result
            oid = body.get("order")
            o = db.execute("SELECT * FROM od_orders WHERE id=?", (oid,)).fetchone() if isinstance(oid, str) else None
            if not o:
                raise crm_store.CRMError("Order not found", 404)
            if action == "order.open":
                if not o["opened_at"]:
                    db.execute("UPDATE od_orders SET opened_at=?, opened_by=? WHERE id=?", (crm_store.now(), person.id, oid))
                result = {"ok": True}
                self._event(db, actor, action, order_id=oid, request_id=rid, result=result)
                return result
            if body.get("revision") != o["revision"]:
                raise crm_store.CRMError("This order changed since you opened it. Reload it.", 409)
            done_states = {"order.send_confirmation": "approved", "order.mark_error": "approved"}
            if o["status"] != done_states.get(action, "draft"):
                raise crm_store.CRMError(f"This order is {o['status']}; that action does not apply", 409)
            detail = {k: v for k, v in body.items() if k not in ("action", "request_id")}
            result = getattr(self, "_" + action.replace(".", "_"))(db, book, person, o, body)
            db.execute("UPDATE od_orders SET revision=revision+1 WHERE id=?", (oid,))
            result = {**(result or {}), "revision": o["revision"] + 1}
            self._event(db, actor, action, order_id=oid, request_id=rid, detail=detail, result=result)
            return result

    def _line(self, db, o, body):
        ln = db.execute("SELECT * FROM od_lines WHERE order_id=? AND n=?", (o["id"], body.get("line"))).fetchone()
        if not ln or ln["removed"]:
            raise crm_store.CRMError("Line not found", 404)
        return ln

    def _set_flags(self, db, o, n, flags):
        db.execute("UPDATE od_lines SET flags=? WHERE order_id=? AND n=?", (json.dumps(flags), o["id"], n))

    def _line_accept(self, db, book, person, o, body):
        """The rep read the flags on this line and keeps it as it is."""
        ln = self._line(db, o, body)
        flags = json.loads(ln["flags"])
        if any(f["code"] == "unknown_part" and not f["resolved"] for f in flags):
            raise crm_store.CRMError("Pick a SKU first: a line with no SKU cannot go to the ERP", 409)
        for f in flags:
            if not f["resolved"]:
                inbox_core.resolve(f, person.id, "accepted")
        self._set_flags(db, o, ln["n"], flags)

    def _line_apply(self, db, book, person, o, body):
        """Take the desk's suggestion on one flag (convert the unit, bill the contract price, round up)."""
        ln = self._line(db, o, body)
        flags = json.loads(ln["flags"])
        f = next((f for f in flags if f["code"] == body.get("flag") and f.get("suggest") and not f["resolved"]), None)
        if f is None:
            raise crm_store.CRMError("That flag has no suggestion to apply", 409)
        values = {k: v for k, v in f["suggest"].items() if k in ("qty", "uom", "price") and v is not None}
        if values:
            db.execute("UPDATE od_lines SET " + ", ".join(f"{k}=?" for k in values) + " WHERE order_id=? AND n=?",
                       (*values.values(), o["id"], ln["n"]))
        inbox_core.resolve(f, person.id, "applied")
        self._set_flags(db, o, ln["n"], flags)

    def _line_set(self, db, book, person, o, body):
        """A rep's edit: SKU, qty, unit or price. A new SKU for a customer part number is learned."""
        ln = self._line(db, o, body)
        changes = {}
        if "sku" in body:
            if body["sku"] not in book.items:
                raise crm_store.CRMError("No such SKU in the item master", 400)
            changes["sku"] = body["sku"]
        for k in ("qty", "price"):
            if k in body:
                v = body[k]
                if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0 or (k == "qty" and v == 0):
                    raise crm_store.CRMError(f"{k} must be a positive number", 400)
                changes[k] = float(v)
        if "uom" in body:
            changes["uom"] = str(body["uom"]).upper()[:4]
        if not changes:
            raise crm_store.CRMError("Nothing to change", 400)
        sku = changes.get("sku", ln["sku"])
        expected, basis = book.expected_price(o["customer_id"], sku) if sku and o["customer_id"] else (None, "")
        values = {"sku": sku, "qty": changes.get("qty", ln["qty"]), "uom": changes.get("uom", ln["uom"]),
                  "price": changes.get("price", expected if "sku" in changes else ln["price"])}
        if "sku" in changes and "uom" not in changes:
            values["uom"] = book.items[sku]["uom"]
        po_line = {"qty": values["qty"], "uom": values["uom"], "price": values["price"]}
        match = {"sku": sku, "source": "rep" if "sku" in changes else ln["source"], "confidence": 1.0, "alternatives": []}
        draft, flags = book.check_line(o["customer_id"], po_line, match)
        edited = set(changes)
        for f in flags:  # what the rep set by hand is decided; a backorder still needs its click
            if (f["code"] in ("unknown_part", "match_check") and "sku" in edited) or \
               (f["code"] in ("uom", "minimum", "multiple") and edited & {"qty", "uom"}) or \
               (f["code"] == "price_below" and "price" in edited):
                inbox_core.resolve(f, person.id, "edited")
        db.execute("UPDATE od_lines SET sku=?, source=?, confidence=1, qty=?, uom=?, price=?, expected_price=?, price_basis=?, "
                   "flags=?, edited=1 WHERE order_id=? AND n=?",
                   (sku, match["source"], draft["qty"], draft["uom"], values["price"], expected, basis, json.dumps(flags),
                    o["id"], ln["n"]))
        db.execute("UPDATE od_orders SET edits=edits+1 WHERE id=?", (o["id"],))
        if "sku" in changes and ln["customer_part"] and o["customer_id"]:
            self._learn(db, o["customer_id"], ln["customer_part"], sku, "corrected", person.id)
            return {"learned": {"customer_part": ln["customer_part"], "sku": sku}}

    def _line_remove(self, db, book, person, o, body):
        ln = self._line(db, o, body)
        db.execute("UPDATE od_lines SET removed=1 WHERE order_id=? AND n=?", (o["id"], ln["n"]))
        db.execute("UPDATE od_orders SET edits=edits+1 WHERE id=?", (o["id"],))

    def _order_set(self, db, book, person, o, body):
        """Set the customer (and teach the desk this sender), the PO number or the need-by date."""
        sets = {}
        if "customer_id" in body:
            if body["customer_id"] not in book.customers:
                raise crm_store.CRMError("No such customer", 400)
            sets.update(customer_id=body["customer_id"], customer_how="picked by a rep")
            sender = db.execute("SELECT sender FROM od_messages WHERE id=?", (o["message_id"],)).fetchone()[0]
            db.execute("INSERT OR REPLACE INTO od_senders VALUES (?,?,?,?)", (sender, body["customer_id"], person.id, crm_store.now()))
        for k in ("po_number", "need_by", "ship_to"):
            if k in body:
                sets[k] = crm_store.text(body[k], k, 200).upper() if k == "po_number" else crm_store.text(body[k], k, 200)
        if not sets:
            raise crm_store.CRMError("Nothing to change", 400)
        db.execute("UPDATE od_orders SET " + ", ".join(f"{k}=?" for k in sets) + ", edits=edits+1 WHERE id=?",
                   (*sets.values(), o["id"]))
        if "customer_id" in sets:
            self._rematch(db, self.book(db), o["id"])
        else:
            self._order_flags(db, book, o["id"])

    def _order_accept(self, db, book, person, o, body):
        """Clear one order-level flag ("enter it anyway", "no PO number")."""
        flags = json.loads(o["flags"])
        f = next((f for f in flags if f["code"] == body.get("flag") and not f["resolved"]), None)
        if f is None or f["code"] == "customer":
            raise crm_store.CRMError("Pick the customer; that flag cannot be accepted as it is" if f else "No such flag", 409)
        inbox_core.resolve(f, person.id, "accepted")
        db.execute("UPDATE od_orders SET flags=? WHERE id=?", (json.dumps(flags), o["id"]))

    def _order_approve(self, db, book, person, o, body):
        """THE GATE. A person approves; only then are the ERP file and the confirmation draft made."""
        lines = [dict(r, flags=json.loads(r["flags"])) for r in db.execute("SELECT * FROM od_lines WHERE order_id=? ORDER BY n", (o["id"],))]
        blocking = self._blocking(o, lines)
        if blocking:
            raise crm_store.CRMError(f"{len(blocking)} flag(s) still need a click: " + blocking[0], 409)
        live = [ln for ln in lines if not ln["removed"]]
        first = int(self.cfg.get("first_number") or 5001)
        number = max(first - 1, db.execute("SELECT coalesce(max(number), 0) FROM od_orders").fetchone()[0]) + 1
        at = crm_store.now()
        here = local_now(self.prof)
        c = book.customers[o["customer_id"]]
        m = db.execute("SELECT * FROM od_messages WHERE id=?", (o["message_id"],)).fetchone()
        order = {"number": number, "customer_id": o["customer_id"], "customer_name": c["name"], "terms_days": c["terms_days"],
                 "po_number": o["po_number"], "order_date": here.date().isoformat(), "need_by": o["need_by"],
                 "ship_to": o["ship_to"] or c.get("ship_to", ""), "approved_by": person.id, "approved_at": at,
                 "buyer": m["sender"], "buyer_name": m["from_name"], "message_id": m["message_id"],
                 "lines": [{"n": i, "sku": ln["sku"], "customer_part": ln["customer_part"],
                            "description": book.items[ln["sku"]]["description"], "qty": float(ln["qty"]), "uom": ln["uom"],
                            "price": float(ln["price"])} for i, ln in enumerate(live, 1)]}
        files = {}
        folder = self.out / "outbox"
        folder.mkdir(parents=True, exist_ok=True)
        for layout in self.cfg.get("layouts") or ["generic"]:
            name = f"order-{number}-{layout}.csv"
            (folder / name).write_bytes(order_desk_erp.import_file(order, layout))
            files[layout] = f"outbox/{name}"
        conf = order_desk_erp.confirmation(order, self._desk_facts(), book.stock, here)
        (self.out / "drafts").mkdir(parents=True, exist_ok=True)
        (self.out / "drafts" / f"order-{number}-confirmation.eml").write_bytes(conf.pop("eml"))
        db.execute("UPDATE od_orders SET status='approved', number=?, approved_at=?, approved_by=?, files=?, confirmation=?, "
                   "ship_to=? WHERE id=?", (number, at, person.id, json.dumps(files), json.dumps(conf), order["ship_to"], o["id"]))
        for ln in live:  # the rep checked every line: a match found any way but the table is now taught
            if ln["customer_part"] and ln["source"] in ("history", "description", "fax", "sku"):
                self._learn(db, o["customer_id"], ln["customer_part"], ln["sku"], "confirmed", person.id)
        return {"number": number, "files": files}

    def _order_reject(self, db, book, person, o, body):
        reason = crm_store.text(body.get("reason") or "", "Reason", 300, True)
        db.execute("UPDATE od_orders SET status='rejected', rejected_at=?, reject_reason=? WHERE id=?", (crm_store.now(), reason, o["id"]))

    def _order_send_confirmation(self, db, book, person, o, body):
        """The rep's second click. v1 sends nothing: the pilot hands the draft to the client's mailbox,
        the demo copies it to a sent folder. Either way it is recorded as sent by this person."""
        if o["confirmation_sent_at"]:
            raise crm_store.CRMError("The confirmation was already sent", 409)
        conf = json.loads(o["confirmation"])
        if isinstance(body.get("body"), str) and body["body"].strip():
            conf["body"] = crm_store.text(body["body"], "Email", 8000, True)
        (self.out / "sent").mkdir(parents=True, exist_ok=True)
        (self.out / "sent" / f"order-{o['number']}-confirmation.eml").write_bytes(
            order_desk_erp._eml(self.cfg.get("from_address", ""), conf["to"], conf["subject"], conf["body"]))
        db.execute("UPDATE od_orders SET confirmation=?, confirmation_sent_at=?, confirmation_sent_by=? WHERE id=?",
                   (json.dumps(conf), crm_store.now(), person.id, o["id"]))

    def _order_mark_error(self, db, book, person, o, body):
        """The owner logs a credit, return or re-ship caused by this entry. It feeds the error rate."""
        note = crm_store.text(body.get("note") or "", "Note", 300, True)
        db.execute("UPDATE od_orders SET error_at=?, error_note=? WHERE id=?", (crm_store.now(), note, o["id"]))

    def _message_act(self, db, person, action, body):
        m = db.execute("SELECT * FROM od_messages WHERE id=?", (body.get("message"),)).fetchone()
        if not m:
            raise crm_store.CRMError("Message not found", 404)
        if action == "message.dismiss":
            db.execute("UPDATE od_messages SET status='done' WHERE id=?", (m["id"],))
            return {"ok": True}
        if not m["reply"] or m["reply_sent_at"]:
            raise crm_store.CRMError("No reply draft to send" if not m["reply"] else "The reply was already sent", 409)
        reply = json.loads(m["reply"])
        if isinstance(body.get("body"), str) and body["body"].strip():
            reply["body"] = crm_store.text(body["body"], "Email", 8000, True)
        (self.out / "sent").mkdir(parents=True, exist_ok=True)
        (self.out / "sent" / f"reply-{m['id']}.eml").write_bytes(
            order_desk_erp._eml(self.cfg.get("from_address", ""), reply["to"], reply["subject"], reply["body"], m["message_id"]))
        db.execute("UPDATE od_messages SET reply=?, reply_sent_at=?, reply_sent_by=?, status='done' WHERE id=?",
                   (json.dumps(reply), crm_store.now(), person.id, m["id"]))
        return {"ok": True}

    def _learn(self, db, cid, part, sku, how, who):
        inbox_core.remember(db, "od_xref", {"customer_id": cid, "part_key": order_desk_match.key(part)},
                            {"customer_part": part, "sku": sku}, how, who, crm_store.now())
