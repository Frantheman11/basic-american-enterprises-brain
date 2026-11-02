"""Inbox core: the steps every inbox desk shares, with nothing about any one trade in it.

The Quote Desk and the Order Desk both use it: one engine, two profiles.
Whatever reads a machining print, prices a part or matches a PO line stays in the desk. What lives here:

    sort_email       an email to a kind, by the desk's ordered rules
    stop_marks       export-control marks (ITAR, EAR, CUI): any one stops the desk before any reader
    parse_sender     "Name <a@b.c>" to name, address, domain
    fingerprint      what an approval approves; any change after it is caught
    check_gate       the send rule: approved, and still exactly what was approved
    deliver          where an approved message goes (v1: a log line, never a send)
    log, correction  who did what; and every time a person corrected what the reader read
    kpis             time to answer, answers per week, same-day rate, follow-up rate, win rate, never answered
    local_date       a stored instant as a date in the business's time zone

    new_messages, parse_email   the .eml files a mailbox rule drops in a folder, read to facts and bytes
    sort                        kind by (kind, where, pattern, when) rules that can test for a document
    flag, resolve, still_open   red and yellow need a person's click; approve refuses while any is open
    log_event, replayed, request_id   an events table, once per request id
    remember                    a person's correction, kept; "corrected" outranks "confirmed"
    minutes, median_or_none, pct, local_day, local_hhmm   the KPI arithmetic

Every function is pure or takes the open database connection and a table name, so each desk keeps
its own tables (`qd_log`, `od_events`) and two desks never share a row. Table names come from the
desk's own code, never from a request.
"""
import email
import email.policy
import hashlib
import json
import os
import re
import statistics
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime
from statistics import median
from zoneinfo import ZoneInfo

from . import crm_store


EXPORT_CONTROL = re.compile(r"\bITAR\b|international traffic in arms|\b22\s*CFR\s*12[0-9]|\bUSML\b|export[- ]controlled|"
                            r"export control(?:led)? (?:data|item|technical)|\bEAR\b(?!99)|\bECCN\b|\bCUI\b|"
                            r"controlled unclassified", re.I)


def sort_email(subject, body, rules):
    """A kind for the email from ordered rules, or "other". Fixed rules a person can predict."""
    text = f"{subject}\n{body}".lower()
    for r in rules:
        if re.search(r["match"], text) and not (r.get("unless") and re.search(r["unless"], text)):
            return r["kind"]
    return "other"


def stop_marks(*texts, pattern=EXPORT_CONTROL):
    """Every export-control mark in the texts, as found, once each. Runs on raw text, locally,
    before any reader: a marked email goes to a person and nowhere else."""
    found = []
    for t in texts:
        for m in pattern.finditer(t or ""):
            if m.group(0) not in found:
                found.append(m.group(0))
    return found


def parse_sender(raw):
    m = re.match(r"\s*(?:\"?([^\"<]*)\"?\s*)?<([^>]+)>\s*$", raw or "")
    name, addr = (m.group(1) or "").strip(), (m.group(2) if m else raw or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", addr):
        raise crm_store.CRMError("The sender needs an email address", 400)
    return name, addr, addr.split("@", 1)[1]


def fingerprint(message):
    """A hash of exactly what was approved (a desk picks the fields that matter)."""
    return hashlib.sha256(json.dumps(message, sort_keys=True).encode("utf-8")).hexdigest()


def check_gate(status, approved_fp, current_fp):
    """The one send rule. Raises unless approved, and unless nothing changed since."""
    if status != "approved" or not approved_fp:
        raise crm_store.CRMError("Not approved. A person approves every message before it goes out.", 409)
    if current_fp != approved_fp:
        raise crm_store.CRMError("It changed after it was approved. Approve it again.", 409)


def deliver(mode, fictional=False):
    """Where an approved message goes. v1 knows one mode, "log": the message is recorded and a
    person sends it from the business's own mailbox. A mail connector plugs in here."""
    if (mode or "log") != "log":
        raise crm_store.CRMError(f"Mail mode '{mode}' is not built in v1", 503)
    return "demo: logged, nothing was emailed" if fictional else "logged: send it from your own mailbox"


def log(db, table, who, action, ref, detail=""):
    db.execute(f"INSERT INTO {table} VALUES (?,?,?,?,?)", (crm_store.now(), who, action, ref, detail))


def correction(db, table, who, ref, field, was, now):
    """A person changed what the reader read. Kept apart so a desk can count how often its reader
    is wrong, field by field, and so a better reader can be measured against it."""
    if was != now:
        log(db, table, who, "correction", ref, json.dumps({"field": field, "was": was, "now": now}))


def _t(s):
    return datetime.fromisoformat(s) if s else None


def kpis(rows, now, weeks=4, unanswered_after_hours=48, follow_up_first_day=3):
    """The owner's numbers from a desk's log. `rows`: one per inbound request, with received_at,
    and when answered: sent_at, value, outcome (won/lost/...), decided_at, followups (count sent),
    closed (a decision such as no-bid or an export-control stop). Window: the last `weeks` weeks."""
    now = now if isinstance(now, datetime) else _t(now)
    start = now - timedelta(weeks=weeks)
    inbound = [r for r in rows if _t(r["received_at"]) >= start]
    sent = [r for r in rows if r.get("sent_at") and _t(r["sent_at"]) >= start]
    hours = [(_t(r["sent_at"]) - _t(r["received_at"])).total_seconds() / 3600 for r in sent]
    due_follow = [r for r in sent if (now - _t(r["sent_at"])).days >= follow_up_first_day
                  and not (r.get("decided_at") and _t(r["decided_at"]) < _t(r["sent_at"]) + timedelta(days=follow_up_first_day))]
    followed = [r for r in due_follow if (r.get("followups") or 0) > 0]
    decided = [r for r in sent if r.get("outcome") in ("won", "lost")]
    won = [r for r in sent if r.get("outcome") == "won"]
    never = [r for r in inbound if not r.get("sent_at") and not r.get("closed")
             and (now - _t(r["received_at"])).total_seconds() / 3600 >= unanswered_after_hours]
    pct = lambda a, b: round(100 * a / b) if b else None
    value = lambda rs: round(sum(r.get("value") or 0 for r in rs), 2)
    weekly = []
    for w in range(weeks - 1, -1, -1):
        a, b = now - timedelta(weeks=w + 1), now - timedelta(weeks=w)
        ws = [r for r in sent if a <= _t(r["sent_at"]) < b]
        wh = [(_t(r["sent_at"]) - _t(r["received_at"])).total_seconds() / 3600 for r in ws]
        weekly.append({"week_of": a.date().isoformat(), "sent": len(ws),
                       "same_day": pct(sum(1 for h in wh if h <= 24), len(ws))})
    return {
        "window_weeks": weeks, "rfqs": len(inbound), "sent": len(sent),
        "time_to_quote_hours": round(statistics.median(hours), 1) if hours else None,
        "quotes_per_week": round(len(sent) / weeks, 1),
        "same_day_rate": pct(sum(1 for h in hours if h <= 24), len(hours)),
        "follow_up_rate": pct(len(followed), len(due_follow)),
        "win_rate": pct(len(won), len(sent)), "win_rate_decided": pct(len(won), len(decided)),
        "win_rate_dollars": pct(value(won), value(sent)), "won_value": value(won), "sent_value": value(sent),
        "never_answered": len(never), "never_answered_ids": [r["id"] for r in never], "weekly": weekly,
    }


def local_date(tz, iso):
    return datetime.fromisoformat(iso).astimezone(ZoneInfo(tz or "UTC")).date()



RED, YELLOW, INFO = "red", "yellow", "info"
OPEN = (RED, YELLOW)


# ---------------------------------------------------------------- watch and read

def new_messages(folder):
    """(name, raw bytes) for every .eml in the folder, oldest name first. Files are named by arrival
    time, so a later copy of the same PO is always read after the first one."""
    if not folder or not os.path.isdir(folder):
        return
    for name in sorted(os.listdir(folder)):
        if name.lower().endswith(".eml"):
            with open(os.path.join(folder, name), "rb") as f:
                yield name, f.read()


def parse_email(raw):
    """One .eml file -> the facts a desk keeps. Attachments keep their bytes for the side-by-side view."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    name, addr = parseaddr(str(msg.get("From", "")))
    try:
        received = parsedate_to_datetime(str(msg.get("Date"))).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        received = ""
    body = msg.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body is not None else ""
    if body is not None and body.get_content_type() == "text/html":
        text = re.sub(r"<[^>]+>", " ", text)
    files = []
    for part in msg.iter_attachments():
        data = part.get_payload(decode=True) or b""
        files.append({"name": part.get_filename() or "attachment", "type": part.get_content_type(), "data": data,
                      "sha": hashlib.sha256(data).hexdigest()})
    mid = str(msg.get("Message-ID") or "").strip() or "sha:" + hashlib.sha256(raw).hexdigest()
    return {"message_id": mid, "from_name": name, "from": addr.lower(), "subject": str(msg.get("Subject") or ""),
            "received_at": received, "body": text.replace("\r\n", "\n").strip(), "files": files}


def sort(subject, body, has_document, rules, default="other"):
    """The kind of one email. `rules` is the desk's ordered list of (kind, where, pattern, when): where is
    "subject" or "any"; when is "always", "with_document" (matches if a document came) or "without_document".
    The first rule that matches wins."""
    s = subject.lower()
    both = s + "\n" + body.lower()
    for kind, where, pattern, when in rules:
        if when == "without_document" and has_document:
            continue
        if when == "with_document":
            if has_document:
                return kind
            continue
        if re.search(pattern, s if where == "subject" else both):
            return kind
    return default


# ---------------------------------------------------------------- flags and the gate

def flag(code, level, text, suggest=None, extra=None):
    """One thing a person should see. Info is born resolved; red and yellow wait for a click."""
    return {"code": code, "level": level, "text": text, "suggest": suggest, **(extra or {}), "resolved": level == INFO}


def resolve(f, who, how):
    f.update(resolved=True, resolved_by=who, how=how)
    return f


def still_open(groups):
    """The texts of every red or yellow flag nobody has cleared. groups: [(prefix, flags)]. A desk's
    approve refuses while this is not empty: that is the gate."""
    return [prefix + f["text"] for prefix, flags in groups for f in flags if f["level"] in OPEN and not f["resolved"]]


# ---------------------------------------------------------------- the log, once per request

def events_table(name):
    return (f"CREATE TABLE IF NOT EXISTS {name} (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL, "
            "action TEXT NOT NULL, record_id TEXT, message_id TEXT, request_id TEXT UNIQUE, detail TEXT NOT NULL DEFAULT '{}', result TEXT)")


def request_id(body):
    rid = body.get("request_id") if isinstance(body, dict) else None
    if not isinstance(rid, str) or not 8 <= len(rid) <= 80:
        raise ValueError("Every action needs a request_id")
    return rid


def replayed(db, table, rid):
    """The first answer to this request id, or None. A retried click changes nothing twice."""
    row = db.execute(f"SELECT result FROM {table} WHERE request_id=?", (rid,)).fetchone()
    return json.loads(row[0] or "{}") if row else None


def log_event(db, table, at, actor, action, record_id=None, message_id=None, rid=None, detail=None, result=None):
    db.execute(f"INSERT INTO {table} (at, actor, action, record_id, message_id, request_id, detail, result) VALUES (?,?,?,?,?,?,?,?)",
               (at, actor, action, record_id, message_id, rid, json.dumps(detail or {}),
                json.dumps(result) if result is not None else None))


# ---------------------------------------------------------------- learning from a person

def remember(db, table, keys, values, how, who, at):
    """Upsert one learned row. keys and values are {column: value}; the table has `how`, `taught_by` and
    `taught_at` columns and a primary key on the key columns. A person's correction ("corrected") is
    never downgraded by a later plain confirmation ("confirmed")."""
    cols = {**keys, **values, "how": how, "taught_by": who, "taught_at": at}
    names = ", ".join(cols)
    sets = ", ".join(f"{c}=excluded.{c}" for c in (*values, "taught_by", "taught_at"))
    db.execute(f"INSERT INTO {table} ({names}) VALUES ({', '.join('?' for _ in cols)}) ON CONFLICT({', '.join(keys)}) DO UPDATE SET "
               f"{sets}, how=CASE WHEN {table}.how='corrected' AND excluded.how='confirmed' THEN {table}.how ELSE excluded.how END",
               tuple(cols.values()))


# ---------------------------------------------------------------- KPI arithmetic

def minutes(start, end):
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 60


def median_or_none(xs):
    return round(median(xs), 1) if xs else None


def pct(part, whole):
    return round(100 * part / whole) if whole else None


def local_day(iso, tz):
    return datetime.fromisoformat(iso).astimezone(tz).date()


def local_hhmm(iso, tz):
    return datetime.fromisoformat(iso).astimezone(tz).strftime("%H:%M")
