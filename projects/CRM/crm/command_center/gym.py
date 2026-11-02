"""Gym module, P1: members, plans, schedule, check-in and ranks. Records only, no money.

Design: docs/products/gym-system-p1-design.md. On only when the profile says `gym.enabled: true`.
Without it no gym table is created, no gym route answers and every gym command is refused, so a
business that is not a gym runs exactly as before (and its schema number never moves).

Every write is a command inside `Store.execute`: one transaction with the audit line, the outbox
event and the request id, the revision it read, and `Store.contact` (the `can_see` rule) for every
member it touches. The kiosk is the one writer that is not a person: it may only check a member in,
as `system:kiosk`, through `execute_as_kiosk` (the same transaction, audit and replay).

Time: a class is a local wall time in `gym.timezone`. Its start on a date is computed with zoneinfo,
so a 6:00 PM class is 6:00 PM on both sides of a daylight saving change. "Today" is the gym's date.
"""
import hashlib
import json
import re
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import crm_store

SCHEMA = 1
KIOSK_ACTOR = "system:kiosk"
CAPABILITIES = {
    "gym.member.save": "gym_manage", "gym.plan.save": "gym_manage", "gym.membership.save": "gym_manage",
    "gym.class.save": "gym_manage", "gym.session.cancel": "gym_manage", "gym.session.restore": "gym_manage",
    "gym.booking.add": "gym_checkin", "gym.booking.remove": "gym_checkin",
    "gym.checkin.add": "gym_checkin", "gym.checkin.remove": "gym_checkin",
    "gym.rank.award": "gym_promote",
}
FIELDS = {
    "gym.member.save": {"contact_id", "revision", "birth_date", "joined_on", "emergency_name",
                        "emergency_phone", "guardian_id", "program"},
    "gym.plan.save": {"id", "revision", "name", "interval", "program", "active", "amount_cents"},
    "gym.membership.save": {"id", "revision", "contact_id", "plan_id", "status", "started_on", "ends_on"},
    "gym.class.save": {"id", "revision", "name", "weekday", "start", "minutes", "instructor", "room",
                       "cap", "program", "active"},
    "gym.session.cancel": {"session_id", "reason"},
    "gym.session.restore": {"session_id"},
    "gym.booking.add": {"session_id", "contact_id"},
    "gym.booking.remove": {"session_id", "contact_id"},
    "gym.checkin.add": {"session_id", "date", "contact_id", "method"},
    "gym.checkin.remove": {"id"},
    "gym.rank.award": {"contact_id", "system_id", "belt", "stripes", "awarded_on", "note"},
}
STATUSES = ("active", "trial", "paused", "canceled")
LIVE = ("active", "trial")  # a membership that lets a member train
INTERVALS = ("month", "year", "once", "none")
METHODS = ("kiosk", "qr", "roster", "import")  # qr: from the member's own phone (gym_door.py)
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def config(prof):
    gym = prof.get("gym")
    return gym if isinstance(gym, dict) and gym.get("enabled") is True else None


def enabled(prof):
    return config(prof) is not None


def require(prof):
    cfg = config(prof)
    if cfg is None:
        raise crm_store.CRMError("The gym module is not enabled for this business", 404)
    return cfg


def zone(prof):
    try:
        return ZoneInfo(require(prof).get("timezone") or "UTC")
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise crm_store.CRMError("The gym time zone is not configured correctly", 503) from None


def local_now(prof):
    """The engine's clock (crm_store.now, which a test or a sample can move) in the gym's zone."""
    return datetime.fromisoformat(crm_store.now()).astimezone(zone(prof))


def today(prof):
    return local_now(prof).date()


def starts_at(day, hhmm, tz):
    """The UTC instant a local wall time happens on a local date. In the spring-forward gap the
    time does not exist: zoneinfo reads it with the offset from before the change, which lands one
    hour later (what a clock on the wall shows). In the fall-back overlap it is the first one."""
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.combine(day, time(h, m), tzinfo=tz).astimezone(timezone.utc)


def programs(prof):
    return [p for p in require(prof).get("programs") or [] if isinstance(p, dict) and isinstance(p.get("id"), str)]


def ranks(prof):
    """The belt ladders from the profile, checked. The engine names no belt."""
    out = []
    for s in require(prof).get("ranks") or []:
        if not isinstance(s, dict) or not isinstance(s.get("id"), str) or not isinstance(s.get("belts"), list):
            raise crm_store.CRMError("A gym rank system needs an id and a list of belts", 503)
        belts = []
        for b in s["belts"]:
            if not isinstance(b, dict) or not isinstance(b.get("id"), str) or type(b.get("stripes", 0)) is not int:
                raise crm_store.CRMError(f"Rank system {s['id']}: every belt needs an id and a whole number of stripes", 503)
            belts.append({"id": b["id"], "label": b.get("label", b["id"]), "stripes": b.get("stripes", 0),
                          "color": b.get("color", ""),
                          "due_after_classes": b.get("due_after_classes", s.get("due_after_classes"))})
        out.append({"id": s["id"], "label": s.get("label", s["id"]), "program": s.get("program", ""), "belts": belts})
    return out


def migrate(db):
    """Gym tables, created the first time a gym-enabled profile uses this database. One statement
    at a time on purpose: `executescript` would commit the caller's open transaction first."""
    row = db.execute("SELECT value FROM metadata WHERE key='gym_schema'").fetchone()
    if row and row[0] == str(SCHEMA):
        return
    for statement in TABLES.split(";"):
        if statement.strip():
            db.execute(statement)
    db.execute("INSERT OR REPLACE INTO metadata VALUES ('gym_schema', ?)", (str(SCHEMA),))


TABLES = ("""
        CREATE TABLE IF NOT EXISTS member_details (
            contact_id TEXT PRIMARY KEY REFERENCES contacts(id), birth_date TEXT NOT NULL DEFAULT '',
            joined_on TEXT NOT NULL DEFAULT '', emergency_name TEXT NOT NULL DEFAULT '',
            emergency_phone TEXT NOT NULL DEFAULT '', guardian_id TEXT REFERENCES contacts(id),
            program TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS plans (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE, interval TEXT NOT NULL,
            program TEXT NOT NULL DEFAULT '', amount_cents INTEGER, stripe_price_id TEXT,
            active INTEGER NOT NULL DEFAULT 1, revision INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memberships (
            id TEXT PRIMARY KEY, contact_id TEXT NOT NULL REFERENCES contacts(id),
            plan_id TEXT NOT NULL REFERENCES plans(id), status TEXT NOT NULL,
            started_on TEXT NOT NULL, ends_on TEXT NOT NULL DEFAULT '', stripe_subscription_id TEXT,
            revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS class_templates (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, weekday INTEGER NOT NULL, start TEXT NOT NULL,
            minutes INTEGER NOT NULL, instructor TEXT NOT NULL DEFAULT '', room TEXT NOT NULL DEFAULT '',
            cap INTEGER NOT NULL DEFAULT 0, program TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
            revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, template_id TEXT NOT NULL REFERENCES class_templates(id),
            local_date TEXT NOT NULL, name TEXT NOT NULL, starts_at TEXT NOT NULL, minutes INTEGER NOT NULL,
            program TEXT NOT NULL DEFAULT '', canceled INTEGER NOT NULL DEFAULT 0,
            cancel_reason TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS bookings (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
            contact_id TEXT NOT NULL REFERENCES contacts(id), created_at TEXT NOT NULL,
            UNIQUE(session_id, contact_id));
        CREATE TABLE IF NOT EXISTS checkins (
            id TEXT PRIMARY KEY, slot TEXT NOT NULL, session_id TEXT REFERENCES sessions(id),
            local_date TEXT NOT NULL, contact_id TEXT NOT NULL REFERENCES contacts(id),
            method TEXT NOT NULL, actor TEXT NOT NULL, at TEXT NOT NULL, UNIQUE(slot, contact_id));
        CREATE TABLE IF NOT EXISTS rank_systems (
            id TEXT PRIMARY KEY, label TEXT NOT NULL, program TEXT NOT NULL DEFAULT '',
            ladder TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS rank_awards (
            id TEXT PRIMARY KEY, contact_id TEXT NOT NULL REFERENCES contacts(id), system_id TEXT NOT NULL,
            belt TEXT NOT NULL, stripes INTEGER NOT NULL, awarded_on TEXT NOT NULL, awarded_by TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS kiosk_lock (
            id INTEGER PRIMARY KEY CHECK(id = 1), fails INTEGER NOT NULL, locked_until TEXT NOT NULL DEFAULT '');
        CREATE INDEX IF NOT EXISTS checkins_contact ON checkins(contact_id, local_date);
        CREATE INDEX IF NOT EXISTS checkins_date ON checkins(local_date);
        CREATE INDEX IF NOT EXISTS memberships_contact ON memberships(contact_id);
        CREATE INDEX IF NOT EXISTS rank_awards_contact ON rank_awards(contact_id, awarded_on);
""")


def ensure(store, prof):
    """Create the gym tables (once) and mirror the profile's belt ladders, so an export carries
    them and old awards keep their meaning after a profile edit."""
    require(prof)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        migrate(db)
        if isinstance(require(prof).get("billing"), dict):  # P2: the billing tables, only with a billing block
            from . import gym_billing
            gym_billing.migrate(db)
        for s in ranks(prof):
            ladder = json.dumps(s["belts"], sort_keys=True)
            row = db.execute("SELECT ladder, label FROM rank_systems WHERE id=?", (s["id"],)).fetchone()
            if not row or row["ladder"] != ladder or row["label"] != s["label"]:
                db.execute("INSERT OR REPLACE INTO rank_systems VALUES (?,?,?,?,?)",
                           (s["id"], s["label"], s["program"], ladder, crm_store.now()))


# ------------------------------------------------------------------ checks

def day(value, label, latest=None):
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError()
        parsed = date.fromisoformat(value)
    except ValueError:
        raise crm_store.CRMError(f"{label} must be a date like 2026-10-27") from None
    if latest is not None and parsed > latest:
        raise crm_store.CRMError(f"{label} cannot be in the future")
    return value


def optional_day(value, label, latest=None):
    return "" if value in (None, "") else day(value, label, latest)


def clock(value):
    if not isinstance(value, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
        raise crm_store.CRMError("Start must be a time like 18:30")
    return value


def whole(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise crm_store.CRMError(f"{label} must be a whole number from {low} to {high}")
    return value


def program(prof, value):
    value = crm_store.text(value or "", "Program", 40)
    if value and value not in [p["id"] for p in programs(prof)]:
        raise crm_store.CRMError("Choose a program from the gym's list")
    return value


def revision(row, data):
    if type(data.get("revision")) is not int or data["revision"] != row["revision"]:
        raise crm_store.CRMError("This record changed. Refresh it before saving again.", 409)


def member(store, db, prof, person, cid):
    row = store.contact(db, prof, person, cid)
    if row["kind"] != "person":
        raise crm_store.CRMError("A gym member is a person record")
    return row


# ------------------------------------------------------------------ sessions

def session_id(template_id, on):
    return f"{template_id}@{on}"


def split_session(sid):
    sid = crm_store.text(sid, "Class", 120, True)
    template_id, _, on = sid.rpartition("@")
    if not template_id:
        raise crm_store.CRMError("Choose a class")
    return template_id, day(on, "Class date")


def session(db, prof, sid, create=False, stamp=None):
    """One class on one date. A stored row wins (it keeps the name and start it had then);
    otherwise it is computed from the weekly template, and stored when `create` is set."""
    template_id, on = split_session(sid)
    row = db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if row:
        return dict(row)
    t = db.execute("SELECT * FROM class_templates WHERE id=?", (template_id,)).fetchone()
    on_date = date.fromisoformat(on)
    if not t or not t["active"] or t["weekday"] != on_date.weekday():
        raise crm_store.CRMError("That class is not on the schedule for that date", 404)
    row = {"id": sid, "template_id": t["id"], "local_date": on, "name": t["name"],
           "starts_at": starts_at(on_date, t["start"], zone(prof)).isoformat(), "minutes": t["minutes"],
           "program": t["program"], "canceled": 0, "cancel_reason": "", "revision": 1, "created_at": stamp or ""}
    if create:
        db.execute("INSERT INTO sessions VALUES (:id,:template_id,:local_date,:name,:starts_at,:minutes,"
                   ":program,:canceled,:cancel_reason,:revision,:created_at)", row)
    return row


# ------------------------------------------------------------------ commands

def change(store, db, prof, person, action, data, stamp=None):
    """One gym command, inside the caller's transaction. `person` is None only for the kiosk."""
    require(prof)
    migrate(db)
    if set(data) - FIELDS[action]:
        raise crm_store.CRMError("Unexpected fields in command")
    stamp = stamp or crm_store.now()
    actor = KIOSK_ACTOR if person is None else "human:" + person.id
    handler = HANDLERS[action]
    return handler(store, db, prof, person, data, stamp, actor)


def _member_save(store, db, prof, person, data, stamp, actor):
    contact = member(store, db, prof, person, data.get("contact_id"))
    row = db.execute("SELECT * FROM member_details WHERE contact_id=?", (contact["id"],)).fetchone()
    if row:
        revision(row, data)
    elif data.get("revision") != 0:
        raise crm_store.CRMError("This record changed. Refresh it before saving again.", 409)
    old = dict(row) if row else {}
    pick = lambda k: data.get(k, old.get(k, ""))
    guardian = data.get("guardian_id", old.get("guardian_id")) or None
    if guardian:
        if guardian == contact["id"]:
            raise crm_store.CRMError("A member cannot be their own guardian")
        guardian = member(store, db, prof, person, guardian)["id"]
    values = (optional_day(pick("birth_date"), "Birth date", today(prof)), optional_day(pick("joined_on"), "Joined"),
              crm_store.text(pick("emergency_name"), "Emergency contact", 120),
              crm_store.text(pick("emergency_phone"), "Emergency phone", 60), guardian,
              program(prof, pick("program")))
    if row:
        db.execute("UPDATE member_details SET birth_date=?,joined_on=?,emergency_name=?,emergency_phone=?,"
                   "guardian_id=?,program=?,revision=revision+1,updated_at=? WHERE contact_id=?",
                   (*values, stamp, contact["id"]))
    else:
        db.execute("INSERT INTO member_details VALUES (?,?,?,?,?,?,?,1,?,?)", (contact["id"], *values, stamp, stamp))
    return {"ok": True, "id": contact["id"]}


def _plan_save(store, db, prof, person, data, stamp, actor):
    name = crm_store.text(data.get("name"), "Plan name", 120, True)
    key = " ".join(name.casefold().split())
    interval = data.get("interval", "month")
    if interval not in INTERVALS:
        raise crm_store.CRMError("Choose month, year, once or none")
    active = data.get("active", True)
    if type(active) is not bool:
        raise crm_store.CRMError("Active must be true or false")
    clash = db.execute("SELECT id FROM plans WHERE name_key=?", (key,)).fetchone()
    if data.get("id"):
        rid = crm_store.text(data["id"], "Plan", 80, True)
        row = db.execute("SELECT * FROM plans WHERE id=?", (rid,)).fetchone()
        if not row:
            raise crm_store.CRMError("Plan not found", 404)
        revision(row, data)
        if clash and clash["id"] != rid:
            raise crm_store.CRMError("Another plan already has that name", 409)
        db.execute("UPDATE plans SET name=?,name_key=?,interval=?,program=?,active=?,revision=revision+1,updated_at=? "
                   "WHERE id=?", (name, key, interval, program(prof, data.get("program", row["program"])),
                                  int(active), stamp, rid))
        _price(db, prof, dict(row), rid, data)
        return {"ok": True, "id": rid}
    if clash:
        raise crm_store.CRMError("A plan with that name already exists", 409)
    rid = str(uuid4())
    db.execute("INSERT INTO plans (id,name,name_key,interval,program,active,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
               (rid, name, key, interval, program(prof, data.get("program")), int(active), stamp, stamp))
    _price(db, prof, None, rid, data)
    return {"ok": True, "id": rid}


def _price(db, prof, old, rid, data):
    """P2: a plan with an amount is a Stripe Price on the gym's account (gym_billing.price). A plan
    that changes interval keeps its amount on a new Price. No amount and no Price: nothing to do."""
    moved = bool(old and old["stripe_price_id"] and old["interval"] != data.get("interval", old["interval"]))
    if "amount_cents" not in data and not moved:
        return
    from . import gym_billing
    plan = dict(db.execute("SELECT * FROM plans WHERE id=?", (rid,)).fetchone())
    gym_billing.price(db, prof, old, plan, data.get("amount_cents", old["amount_cents"] if old else None))


def _membership_save(store, db, prof, person, data, stamp, actor):
    rid = data.get("id")
    row = None
    if rid:
        row = db.execute("SELECT * FROM memberships WHERE id=?", (crm_store.text(rid, "Membership", 80, True),)).fetchone()
        if not row:
            raise crm_store.CRMError("Membership not found", 404)
        revision(row, data)
    old = dict(row) if row else {}
    contact = member(store, db, prof, person, data.get("contact_id", old.get("contact_id")))
    if row and contact["id"] != row["contact_id"]:
        raise crm_store.CRMError("A membership cannot move to another member")
    plan_id = crm_store.text(data.get("plan_id", old.get("plan_id")), "Plan", 80, True)
    if not db.execute("SELECT 1 FROM plans WHERE id=?", (plan_id,)).fetchone():
        raise crm_store.CRMError("Choose a plan", 404)
    status = data.get("status", old.get("status", "active"))
    if status not in STATUSES:
        raise crm_store.CRMError("Choose active, trial, paused or canceled")
    started = day(data.get("started_on", old.get("started_on")), "Start date")
    ends = optional_day(data.get("ends_on", old.get("ends_on", "")), "End date")
    if ends and ends < started:
        raise crm_store.CRMError("The end date is before the start date")
    if not row:
        if status in LIVE and db.execute("SELECT 1 FROM memberships WHERE contact_id=? AND plan_id=? AND status IN "
                                         "('active','trial')", (contact["id"], plan_id)).fetchone():
            raise crm_store.CRMError("This member is already on that plan", 409)
        rid = str(uuid4())
        db.execute("INSERT INTO memberships (id,contact_id,plan_id,status,started_on,ends_on,created_at,updated_at) "
                   "VALUES (?,?,?,?,?,?,?,?)", (rid, contact["id"], plan_id, status, started, ends, stamp, stamp))
    else:
        db.execute("UPDATE memberships SET plan_id=?,status=?,started_on=?,ends_on=?,revision=revision+1,updated_at=? "
                   "WHERE id=?", (plan_id, status, started, ends, stamp, rid))
    if status in LIVE and contact["lifecycle"] != "customer":
        db.execute("UPDATE contacts SET lifecycle='customer',revision=revision+1,updated_at=? WHERE id=?",
                   (stamp, contact["id"]))
    return {"ok": True, "id": rid}


def _class_save(store, db, prof, person, data, stamp, actor):
    row = None
    if data.get("id"):
        rid = crm_store.text(data["id"], "Class", 80, True)
        row = db.execute("SELECT * FROM class_templates WHERE id=?", (rid,)).fetchone()
        if not row:
            raise crm_store.CRMError("Class not found", 404)
        revision(row, data)
    old = dict(row) if row else {}
    active = data.get("active", bool(old.get("active", 1)))
    if type(active) is not bool:
        raise crm_store.CRMError("Active must be true or false")
    values = (crm_store.text(data.get("name", old.get("name")), "Class name", 80, True),
              whole(data.get("weekday", old.get("weekday")), "Weekday", 0, 6),
              clock(data.get("start", old.get("start"))),
              whole(data.get("minutes", old.get("minutes", 60)), "Minutes", 15, 300),
              crm_store.text(data.get("instructor", old.get("instructor", "")), "Instructor", 80),
              crm_store.text(data.get("room", old.get("room", "")), "Room", 80),
              whole(data.get("cap", old.get("cap", 0)), "Cap", 0, 500),
              program(prof, data.get("program", old.get("program", ""))), int(active))
    if row:
        db.execute("UPDATE class_templates SET name=?,weekday=?,start=?,minutes=?,instructor=?,room=?,cap=?,program=?,"
                   "active=?,revision=revision+1,updated_at=? WHERE id=?", (*values, stamp, row["id"]))
        return {"ok": True, "id": row["id"]}
    rid = str(uuid4())
    db.execute("INSERT INTO class_templates VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?)", (rid, *values, stamp, stamp))
    return {"ok": True, "id": rid}


def _session_cancel(store, db, prof, person, data, stamp, actor):
    row = session(db, prof, data.get("session_id"), create=True, stamp=stamp)
    if row["canceled"]:
        raise crm_store.CRMError("This class is already canceled", 409)
    reason = crm_store.text(data.get("reason", ""), "Reason", 300)
    db.execute("UPDATE sessions SET canceled=1,cancel_reason=?,revision=revision+1 WHERE id=?", (reason, row["id"]))
    return {"ok": True, "id": row["id"]}


def _session_restore(store, db, prof, person, data, stamp, actor):
    row = session(db, prof, data.get("session_id"), create=True, stamp=stamp)
    if not row["canceled"]:
        raise crm_store.CRMError("This class is not canceled", 409)
    db.execute("UPDATE sessions SET canceled=0,cancel_reason='',revision=revision+1 WHERE id=?", (row["id"],))
    return {"ok": True, "id": row["id"]}


def _open_session(db, prof, sid, stamp):
    row = session(db, prof, sid, create=True, stamp=stamp)
    if row["canceled"]:
        raise crm_store.CRMError("This class is canceled", 409)
    return row


def _booking_add(store, db, prof, person, data, stamp, actor):
    contact = member(store, db, prof, person, data.get("contact_id"))
    row = _open_session(db, prof, data.get("session_id"), stamp)
    had = db.execute("SELECT id FROM bookings WHERE session_id=? AND contact_id=?", (row["id"], contact["id"])).fetchone()
    if had:
        return {"ok": True, "id": had["id"], "already": True}
    cap = (db.execute("SELECT cap FROM class_templates WHERE id=?", (row["template_id"],)).fetchone() or {"cap": 0})["cap"]
    if cap and db.execute("SELECT count(*) FROM bookings WHERE session_id=?", (row["id"],)).fetchone()[0] >= cap:
        raise crm_store.CRMError("This class is full", 409)
    rid = str(uuid4())
    db.execute("INSERT INTO bookings VALUES (?,?,?,?)", (rid, row["id"], contact["id"], stamp))
    return {"ok": True, "id": rid}


def _booking_remove(store, db, prof, person, data, stamp, actor):
    contact = member(store, db, prof, person, data.get("contact_id"))
    sid = crm_store.text(data.get("session_id"), "Class", 120, True)
    had = db.execute("SELECT id FROM bookings WHERE session_id=? AND contact_id=?", (sid, contact["id"])).fetchone()
    if not had:
        raise crm_store.CRMError("That member is not booked in this class", 404)
    db.execute("DELETE FROM bookings WHERE id=?", (had["id"],))
    return {"ok": True, "id": had["id"]}


def live_membership(db, cid):
    return db.execute("SELECT 1 FROM memberships WHERE contact_id=? AND status IN ('active','trial')", (cid,)).fetchone()


def _checkin_add(store, db, prof, person, data, stamp, actor):
    contact = member(store, db, prof, person, data.get("contact_id"))
    method = data.get("method", "roster")
    if method not in METHODS:
        raise crm_store.CRMError("Choose kiosk, roster or import")
    now = today(prof)
    if person is None:  # the kiosk or the door QR: today only, members on a live plan only
        method = "qr" if method == "qr" else "kiosk"
        if not live_membership(db, contact["id"]):
            raise crm_store.CRMError("Your membership is not active. Please see the front desk.", 403)
    if data.get("session_id"):
        row = _open_session(db, prof, data["session_id"], stamp)
        slot, on, sid = row["id"], row["local_date"], row["id"]
    else:
        on = day(data.get("date") or now.isoformat(), "Date")
        slot, sid = "open@" + on, None
    if date.fromisoformat(on) > now or (person is None and on != now.isoformat()):
        raise crm_store.CRMError("Check-in is for today's classes", 409)
    had = db.execute("SELECT id FROM checkins WHERE slot=? AND contact_id=?", (slot, contact["id"])).fetchone()
    if had:  # twice in one class counts once
        return {"ok": True, "id": had["id"], "already": True}
    rid = str(uuid4())
    db.execute("INSERT INTO checkins VALUES (?,?,?,?,?,?,?,?)", (rid, slot, sid, on, contact["id"], method, actor, stamp))
    return {"ok": True, "id": rid}


def _checkin_remove(store, db, prof, person, data, stamp, actor):
    row = db.execute("SELECT * FROM checkins WHERE id=?", (crm_store.text(data.get("id"), "Check-in", 80, True),)).fetchone()
    if not row:
        raise crm_store.CRMError("Check-in not found", 404)
    member(store, db, prof, person, row["contact_id"])
    db.execute("DELETE FROM checkins WHERE id=?", (row["id"],))
    return {"ok": True, "id": row["id"]}


def belt_of(prof, system_id, belt_id):
    system = next((s for s in ranks(prof) if s["id"] == system_id), None)
    if not system:
        raise crm_store.CRMError("Choose a rank system")
    belt = next((b for b in system["belts"] if b["id"] == belt_id), None)
    if not belt:
        raise crm_store.CRMError("Choose a belt from that rank system")
    return system, belt


def _rank_award(store, db, prof, person, data, stamp, actor):
    contact = member(store, db, prof, person, data.get("contact_id"))
    system, belt = belt_of(prof, data.get("system_id"), data.get("belt"))
    stripes = whole(data.get("stripes", 0), "Stripes", 0, belt["stripes"])
    on = day(data.get("awarded_on") or today(prof).isoformat(), "Award date", today(prof))
    note = crm_store.text(data.get("note", ""), "Note", 500)
    rid = str(uuid4())
    db.execute("INSERT INTO rank_awards VALUES (?,?,?,?,?,?,?,?,?)",
               (rid, contact["id"], system["id"], belt["id"], stripes, on, actor, note, stamp))
    body = f"Rank: {belt['label']} belt" + (f", {stripes} stripe{'s' if stripes != 1 else ''}" if stripes else "")
    body += f" ({system['label']}), awarded {on}." + (f"\n{note}" if note else "")
    db.execute("INSERT INTO activities (id,contact_id,kind,body,actor,created_at) VALUES (?,?,?,?,?,?)",
               (str(uuid4()), contact["id"], "note", body, actor, stamp))
    return {"ok": True, "id": rid}


HANDLERS = {
    "gym.member.save": _member_save, "gym.plan.save": _plan_save, "gym.membership.save": _membership_save,
    "gym.class.save": _class_save, "gym.session.cancel": _session_cancel, "gym.session.restore": _session_restore,
    "gym.booking.add": _booking_add, "gym.booking.remove": _booking_remove,
    "gym.checkin.add": _checkin_add, "gym.checkin.remove": _checkin_remove, "gym.rank.award": _rank_award,
}


def execute_as_kiosk(store, prof, data, key, method="kiosk"):
    """The kiosk's one write: a check-in, as `system:kiosk`, with the same transaction, audit,
    outbox and request-id replay as `Store.execute`. Nothing else can be written this way. The door
    QR (gym_door.py) uses it too, as method `qr`."""
    from . import crm_events
    action = "gym.checkin.add"
    if not isinstance(data, dict):
        raise crm_store.CRMError("Check-in data must be an object")
    key = crm_store.text(key, "Request ID", 80, True)
    digest = hashlib.sha256(json.dumps([action, data], sort_keys=True).encode()).hexdigest()
    try:
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM commands WHERE id=?", (key,)).fetchone()
            if previous:
                if previous["actor"] != KIOSK_ACTOR or previous["digest"] != digest:
                    raise crm_store.CRMError("Request ID was already used for a different command", 409)
                return json.loads(previous["response"])
            result = change(store, db, prof, None, action, {**data, "method": "qr" if method == "qr" else "kiosk"})
            crm_events.record(db, key, KIOSK_ACTOR, action, data, result)
            db.execute("INSERT INTO commands VALUES (?,?,?,?)", (key, KIOSK_ACTOR, digest, json.dumps(result)))
            return result
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            raise crm_store.CRMError("Check-in is busy. Tap again.", 503) from None
        raise


def week_dates(start, days=7):
    return [start + timedelta(days=n) for n in range(days)]
