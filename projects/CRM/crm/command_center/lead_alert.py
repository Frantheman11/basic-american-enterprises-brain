"""The lead alert worker: sends the `lead.alert` rows the website intake queues (2026-10-07).

`lead_intake.py` saves a website lead and queues one `lead.alert` outbox row. This sends it:
one email to every person in `crm.intake.alert.to`, with the lead's name, phone, email, form and
message, and a link to the CRM. Nothing here runs inside a web request; a timer runs `run`.

Off until the profile names a sender. The block (inside `crm.intake`):

    "alert": {
      "to": ["<member id>"],                 # lead_intake already checks these
      "sender": "gmail",                     # "gmail" (Gmail API, web port) or "smtp"
      "env_prefix": "LEAD_ALERT",            # optional; names the settings below
      "max_attempts": 3,                     # optional; then the row waits for a person
      "backoff_seconds": 60                  # optional; doubles after each failure
    }

A business with no public form puts the same block at `crm.email_list.alert` (freebie leads, P07).

The settings live in the host's environment, never in the profile, under the prefix:
    <P>_FROM                                     "Example CRM <alerts@example.com>"
    gmail: <P>_GOOGLE_CLIENT_ID, <P>_GOOGLE_CLIENT_SECRET, <P>_GOOGLE_REFRESH_TOKEN (send-only)
    smtp:  <P>_SMTP_HOST, <P>_SMTP_PORT (587), <P>_SMTP_USER, <P>_SMTP_PASSWORD
Why Gmail is the default: DigitalOcean blocks every outgoing mail port on a droplet (tested
2026-09-27, tools/gmail_auth.py), and the Gmail API runs over the web port.

Each row is sent at most once. The worker CLAIMS a row (`pending` -> `sending`) and commits
before it sends, so two workers, or a run after a crash, never send the same lead twice. A
send error puts the row back to `pending` with a later `next_at`; after `max_attempts` it is
`failed`, with the reason, in the administrator's "Waiting for you". A row left in `sending`
(the process died mid-send) shows there too: it may or may not have arrived, so a person checks.

    python -m command_center.lead_alert run --business <id> --crm-db <db> [--once]
    python -m command_center.lead_alert status --business <id> --crm-db <db>
    python -m command_center.lead_alert retry --business <id> --crm-db <db> --alert <id>
    python -m command_center.lead_alert send-test --business <id> --to <email>   # sender only
"""
import argparse
import base64
import html
import json
import os
import smtplib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

SENDERS = ("gmail", "smtp")
STUCK_MINUTES = 15


class SendError(Exception):
    """A message that did not go out. The text is what the administrator reads."""


# ---- the profile block ---------------------------------------------------------------
def config(prof, environ=None):
    """The checked sender settings, or None when the profile names no sender. A sender that is
    named but not set up raises ValueError, so the timer fails loudly instead of queueing forever."""
    crm = prof.get("crm") or {}
    # A business with no public form (CTA itself) names its sender on the freebie block (email_list.py)
    alert = ((crm.get("intake") or {}).get("alert") or (crm.get("email_list") or {}).get("alert") or {})
    kind = alert.get("sender")
    if kind is None:
        return None
    if kind not in SENDERS:
        raise ValueError("crm.intake.alert.sender must be gmail or smtp")
    prefix = alert.get("env_prefix") or "LEAD_ALERT"
    env = environ if environ is not None else os.environ
    keys = {"gmail": ("FROM", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"),
            "smtp": ("FROM", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD")}[kind]
    values = {k: env.get(f"{prefix}_{k}", "") for k in keys + ("SMTP_PORT",)}
    missing = [f"{prefix}_{k}" for k in keys if not values[k]]
    if missing:
        raise ValueError("The lead alert sender is not set up. Missing: " + ", ".join(missing))
    number = lambda v, d: v if type(v) is int and v > 0 else d
    origins = (prof.get("access") or {}).get("origins") or []
    return {"sender": kind, "settings": values, "from": values["FROM"],
            "max_attempts": number(alert.get("max_attempts"), 3),
            "backoff": number(alert.get("backoff_seconds"), 60),
            "crm_url": origins[0].rstrip("/") + "/" if origins and isinstance(origins[0], str) else "",
            "business": prof.get("name") or prof.get("business_id", "")}


# ---- the senders ---------------------------------------------------------------------
class Gmail:
    """users.messages.send over HTTPS with a send-only refresh token. Standard library only:
    this engine is copied into client repos that may not have `requests`."""

    def __init__(self, s, timeout=30):
        self.s, self.timeout, self.token = s, timeout, None

    def _call(self, url, data, headers):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise SendError(f"Google answered {e.code}: {e.read()[:200].decode('utf-8', 'replace')}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SendError(f"Google did not answer ({e})") from None

    def send(self, msg):
        if not self.token:
            form = urllib.parse.urlencode({
                "client_id": self.s["GOOGLE_CLIENT_ID"], "client_secret": self.s["GOOGLE_CLIENT_SECRET"],
                "refresh_token": self.s["GOOGLE_REFRESH_TOKEN"], "grant_type": "refresh_token"}).encode()
            try:
                self.token = self._call("https://oauth2.googleapis.com/token", form,
                                        {"Content-Type": "application/x-www-form-urlencoded"})["access_token"]
            except SendError as e:
                raise SendError(f"Gmail sign-in refused, run the sign-in again ({e})") from None
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        self._call("https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                   json.dumps({"raw": raw}).encode(),
                   {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})


class Smtp:
    def __init__(self, s, timeout=30):
        self.s, self.timeout = s, timeout

    def send(self, msg):
        try:
            with smtplib.SMTP(self.s["SMTP_HOST"], int(self.s["SMTP_PORT"] or 587), timeout=self.timeout) as smtp:
                smtp.starttls()
                smtp.login(self.s["SMTP_USER"], self.s["SMTP_PASSWORD"])
                smtp.send_message(msg)
        except (smtplib.SMTPException, OSError) as e:
            raise SendError(f"The mail server refused or did not answer ({e})") from None


def sender_for(cfg):
    return (Gmail if cfg["sender"] == "gmail" else Smtp)(cfg["settings"])


# ---- the message ---------------------------------------------------------------------
def lead_facts(db, data):
    """What the person reading the alert needs to call back: from the saved records, so the
    alert says exactly what the CRM holds."""
    c = db.execute("SELECT name, company_id FROM contacts WHERE id=?", (data.get("contact_id"),)).fetchone()
    company = c and c["company_id"] and db.execute("SELECT name FROM contacts WHERE id=?", (c["company_id"],)).fetchone()
    channels = {}
    for r in db.execute("SELECT kind, value FROM contact_channels WHERE contact_id=? ORDER BY position",
                        (data.get("contact_id"),)):
        channels.setdefault(r["kind"], r["value"])
    note = db.execute("SELECT body FROM activities WHERE contact_id=? AND kind='note' ORDER BY created_at LIMIT 1",
                      (data.get("contact_id"),)).fetchone()
    task = db.execute("SELECT due_at FROM tasks WHERE id=?", (data.get("task_id"),)).fetchone()
    return {"name": c["name"] if c else "", "company": company["name"] if company else "",
            "phone": channels.get("phone", ""), "email": channels.get("email", ""),
            "note": note["body"] if note else "", "due_at": task["due_at"] if task else ""}


def message(cfg, data, facts, to):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = cfg["from"], ", ".join(to), data.get("subject") or "New website lead"
    rows = [("Name", facts["name"]), ("Company", facts["company"]), ("Phone", facts["phone"]),
            ("Email", facts["email"]), ("Form", data.get("form", ""))]
    head = "URGENT. " if data.get("urgent") else ""
    text = [f"{head}A new lead came in through the website. Call them back.", ""]
    text += [f"{k}: {v}" for k, v in rows if v]
    if cfg["crm_url"]:
        text += ["", f"Open the CRM: {cfg['crm_url']}"]
    text += ["", "What they sent:", facts["note"], "", f"Sent by the {cfg['business']} CRM."]
    msg.set_content("\n".join(text))
    e = html.escape
    cells = "".join(
        f'<tr><td style="padding:6px 12px 6px 0;color:#555">{e(k)}</td><td style="padding:6px 0;font-weight:600">'
        + (f'<a href="tel:{e(v)}">{e(v)}</a>' if k == "Phone" else
           f'<a href="mailto:{e(v)}">{e(v)}</a>' if k == "Email" else e(v)) + "</td></tr>"
        for k, v in rows if v)
    button = (f'<p><a href="{e(cfg["crm_url"])}" style="display:inline-block;background:#1a56db;color:#fff;'
              f'padding:12px 20px;border-radius:6px;text-decoration:none;font-weight:600">Open the CRM</a></p>'
              if cfg["crm_url"] else "")
    flag = '<span style="color:#c81e1e">URGENT. </span>' if data.get("urgent") else ""
    msg.add_alternative(
        f'<div style="font-family:Arial,sans-serif;font-size:16px;color:#111;max-width:560px">'
        f'<p style="font-size:20px;font-weight:700;margin:0 0 12px">{flag}New website lead</p>'
        f'<table style="border-collapse:collapse;font-size:16px">{cells}</table>{button}'
        f'<p style="color:#555;margin-top:20px">What they sent:</p>'
        f'<pre style="white-space:pre-wrap;font-family:inherit;font-size:15px;background:#f5f5f5;padding:12px">'
        f'{e(facts["note"])}</pre><p style="color:#777;font-size:13px">Sent by the {e(cfg["business"])} CRM.</p></div>',
        subtype="html")
    return msg


# ---- the store -----------------------------------------------------------------------
def migrate(db):
    """Version 14: attempts and the last error for each lead alert. `outbox.status` stays the
    alert's state (lead_intake reads it); this table only says why and when to try again."""
    db.execute("""CREATE TABLE IF NOT EXISTS lead_alert_deliveries (
        id TEXT PRIMARY KEY REFERENCES outbox(id), attempts INTEGER NOT NULL DEFAULT 0,
        next_at TEXT NOT NULL, last_error TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL)""")


def _iso(at):
    return at.isoformat(timespec="microseconds")


def claim(store, at):
    """Take every due alert: `pending` -> `sending`, committed before anything is sent."""
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute(
            "SELECT o.id, o.payload, coalesce(d.attempts, 0) attempts FROM outbox o "
            "LEFT JOIN lead_alert_deliveries d ON d.id=o.id WHERE o.event_type='lead.alert' "
            "AND o.status='pending' AND (d.id IS NULL OR d.next_at<=?) ORDER BY o.created_at", (_iso(at),)).fetchall()
        for r in rows:
            db.execute("UPDATE outbox SET status='sending' WHERE id=? AND status='pending'", (r["id"],))
            _save(db, r["id"], r["attempts"], _iso(at), "", at)
    return [dict(r) for r in rows]


def _save(db, aid, attempts, next_at, error, at):
    db.execute("INSERT INTO lead_alert_deliveries VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
               "attempts=excluded.attempts, next_at=excluded.next_at, last_error=excluded.last_error, "
               "updated_at=excluded.updated_at", (aid, attempts, next_at, error[:500], _iso(at)))


def _finish(store, aid, status, attempts, next_at, error, at):
    with store.connection() as db:
        db.execute("UPDATE outbox SET status=? WHERE id=? AND status='sending'", (status, aid))
        _save(db, aid, attempts, next_at, error, at)


def run_once(store, cfg, sender=None, clock=None):
    """Send every due alert once. Returns {"sent", "retry", "failed"} counts."""
    at = clock() if clock else datetime.now(timezone.utc)
    counts = {"sent": 0, "retry": 0, "failed": 0}
    out = None
    for row in claim(store, at):
        data = json.loads(row["payload"]).get("data") or {}
        attempts = row["attempts"] + 1
        to = [p["emails"][0] for p in data.get("to") or [] if p.get("emails")]
        try:
            if not to:
                raise SendError("No recipient has an email address in the profile")
            with store.connection() as db:
                msg = message(cfg, data, lead_facts(db, data), to)
            out = out or sender or sender_for(cfg)
            out.send(msg)
        except Exception as e:  # noqa: BLE001 - every failure is recorded, none is lost
            error = str(e) if isinstance(e, SendError) else f"{type(e).__name__}: {e}"
            if attempts < cfg["max_attempts"] and to:
                wait = cfg["backoff"] * 2 ** (attempts - 1)
                _finish(store, row["id"], "pending", attempts, _iso(at + timedelta(seconds=wait)), error, at)
                counts["retry"] += 1
            else:
                _finish(store, row["id"], "failed", attempts, _iso(at), error, at)
                counts["failed"] += 1
            continue
        _finish(store, row["id"], "sent", attempts, _iso(at), "", at)
        counts["sent"] += 1
    return counts


def reason(db, aid):
    row = db.execute("SELECT last_error FROM lead_alert_deliveries WHERE id=?", (aid,)).fetchone()
    return row["last_error"] if row else ""


def stuck(db, at=None):
    """Alerts claimed but never finished: the worker stopped mid-send."""
    at = at or datetime.now(timezone.utc)
    cutoff = _iso(at - timedelta(minutes=STUCK_MINUTES))
    return [r["id"] for r in db.execute(
        "SELECT o.id FROM outbox o JOIN lead_alert_deliveries d ON d.id=o.id WHERE o.event_type='lead.alert' "
        "AND o.status='sending' AND d.updated_at<?", (cutoff,))]


def retry(store, aid):
    """Put a failed (or stuck) alert back in the queue, once the sender is fixed."""
    now = _iso(datetime.now(timezone.utc))
    with store.connection() as db:
        n = db.execute("UPDATE outbox SET status='pending' WHERE id=? AND event_type='lead.alert' "
                       "AND status IN ('failed','sending')", (aid,)).rowcount
        if n:
            db.execute("UPDATE lead_alert_deliveries SET attempts=0, next_at=?, updated_at=? WHERE id=?", (now, now, aid))
    return bool(n)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Send the website lead alerts the CRM has queued.")
    ap.add_argument("command", choices=("run", "status", "retry", "send-test"))
    ap.add_argument("--business", required=True)
    ap.add_argument("--crm-db")
    ap.add_argument("--once", action="store_true", help="one pass, then exit (a timer's way)")
    ap.add_argument("--every", type=int, default=30, help="seconds between passes")
    ap.add_argument("--alert", help="retry: the alert id")
    ap.add_argument("--to", help="send-test: the one address that gets the test message")
    a = ap.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from command_center import crm_store, lead_intake, profiles
    prof = profiles.load(a.business, str(root))
    if a.command == "send-test":
        cfg = config(prof)
        if cfg is None or not a.to:
            print("Name a sender in crm.intake.alert.sender and pass --to")
            return 2
        data = {"subject": f"Test: the {cfg['business']} lead alert works", "form": "Test message, no lead"}
        facts = {"name": "Test only", "company": "", "phone": "", "email": "", "note": "No lead was saved.",
                 "due_at": ""}
        sender_for(cfg).send(message(cfg, data, facts, [a.to]))
        print(f"test message sent to {a.to} ({cfg['sender']})")
        return 0
    if not a.crm_db:
        ap.error("--crm-db is required")
    store = crm_store.Store(a.crm_db, a.business)
    if a.command == "status":
        lead_intake.main(["status", "--business", a.business, "--crm-db", a.crm_db])
        with store.connection() as db:
            for r in db.execute("SELECT d.id, d.attempts, d.last_error FROM lead_alert_deliveries d "
                                "JOIN outbox o ON o.id=d.id WHERE o.status IN ('failed','pending') AND d.last_error!=''"):
                print(f"  {r['id']}  attempts {r['attempts']}  {r['last_error']}")
            for aid in stuck(db):
                print(f"  {aid}  stuck in sending: check the inbox, then retry or leave it")
        return 0
    if a.command == "retry":
        print("queued again" if retry(store, a.alert or "") else "no failed or stuck alert with that id")
        return 0
    cfg = config(prof)
    if cfg is None:
        print("crm.intake.alert.sender is not set in this profile: nothing to send")
        return 0
    while True:
        counts = run_once(store, cfg)
        if any(counts.values()):
            print(f"{_iso(datetime.now(timezone.utc))}  sent {counts['sent']}  retry {counts['retry']}  "
                  f"failed {counts['failed']}", flush=True)
        if a.once:
            return 0
        time.sleep(a.every)


if __name__ == "__main__":
    sys.exit(main())
