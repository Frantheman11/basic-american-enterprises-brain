"""HubSpot behind the slot: the five verbs on a HubSpot account (2026-10-02).

A business that already runs HubSpot keeps its records there. A module (the morning brief,
a follow-up, a website lead) talks to the slot, and this adapter speaks HubSpot's API.
Works on every HubSpot plan, Free included, with a private app token. Setup, two minutes:
docs/ops/runbooks/hubspot-connect.md.

The profile names where the secrets are, never the secrets (architecture R6):

    "slot": {
      "kind": "hubspot",
      "token_env": "HUBSPOT_TOKEN",                   # the private app's access token
      "client_secret_env": "HUBSPOT_CLIENT_SECRET",   # checks the new-contact webhook
      "owners": {"<member id>": "<HubSpot owner id>"},   # optional; else matched by email
      "state_db": "Generated/slot/<business>.sqlite3"    # failures, for "Waiting for you"
    }

Every call has an error path (R4): a 429 waits for HubSpot's Retry-After (or backs off) and
retries twice; a 5xx or a dropped connection retries twice; then the verb raises SlotError.
401 and 403 are an expired or revoked token, 400 is a missing or wrong field. Nothing retries
forever and nothing fails silently: `slot.deliver_lead` records the failure, and the morning
brief reads it back as "waiting for you".
"""
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .slot import FailureLog, Slot, SlotError

API = "https://api.hubapi.com"
NOTE_TO_CONTACT, TASK_TO_CONTACT = 202, 204  # HubSpot-defined association type ids
CONTACT_FIELDS = ["firstname", "lastname", "email", "phone", "company", "hubspot_owner_id"]
TASK_FIELDS = ["hs_task_subject", "hs_timestamp", "hs_task_status", "hubspot_owner_id"]
MAX_SKEW_MS = 5 * 60 * 1000  # HubSpot's own rule for a v3 webhook signature


class HubSpotSlot(Slot):
    name = "HubSpot"

    def __init__(self, token, client_secret="", owners=None, people=None, failures=None,
                 api=API, retries=2, sleep=time.sleep, timeout=15):
        if not token:
            raise SlotError("auth", "No HubSpot key is set. Paste the private app token into the variable the profile names")
        self.token, self.client_secret, self.api = token, client_secret, api.rstrip("/")
        self.owners, self.people = dict(owners or {}), people or []
        self.failures, self.retries, self.sleep, self.timeout = failures, retries, sleep, timeout

    # ---- the wire ----------------------------------------------------------------------
    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(self.api + path, data=data, method=method, headers={
                "Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                text = e.read().decode("utf-8", "replace")[:2000]
                if e.code in (401, 403):
                    raise SlotError("auth", "HubSpot refused the key: it expired, was revoked, or lacks a scope "
                                            "(see the runbook)", text) from None
                if e.code == 404:
                    raise SlotError("not_found", "HubSpot has no such record", text) from None
                if e.code == 429 or e.code >= 500:
                    if attempt < self.retries:
                        after = e.headers.get("Retry-After") if e.headers else None
                        self.sleep(int(after) if after and after.isdigit() else 2 ** attempt)
                        continue
                    kind = "rate_limit" if e.code == 429 else "unavailable"
                    reason = ("HubSpot's rate limit held after retries" if e.code == 429
                              else f"HubSpot answered {e.code} after retries")
                    raise SlotError(kind, reason, text) from None
                raise SlotError("invalid", f"HubSpot refused the data ({e.code}): {_message(text)}", text) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                if attempt < self.retries:
                    self.sleep(2 ** attempt)
                    continue
                raise SlotError("unavailable", "HubSpot did not answer", e) from None

    # ---- the five verbs ----------------------------------------------------------------
    def upsert_contact(self, contact):
        email = (contact.get("email") or "").strip()
        name = (contact.get("name") or "").strip()
        if not email and not name:
            raise SlotError("invalid", "A contact needs a name or an email")
        first, _, last = name.partition(" ")
        props = {k: v for k, v in (("email", email), ("firstname", first), ("lastname", last),
                                   ("phone", (contact.get("phone") or "").strip()),
                                   ("company", (contact.get("company") or "").strip())) if v}
        if email:
            found = self.call("POST", "/crm/v3/objects/contacts/search", {
                "filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}],
                "properties": ["email"], "limit": 1}).get("results") or []
            if found:
                cid = found[0]["id"]
                self.call("PATCH", f"/crm/v3/objects/contacts/{cid}", {"properties": props})
                return cid
        return self.call("POST", "/crm/v3/objects/contacts", {"properties": props})["id"]

    def add_note(self, contact_id, body):
        return self.call("POST", "/crm/v3/objects/notes", {
            "properties": {"hs_note_body": body[:65000], "hs_timestamp": _ms(datetime.now(timezone.utc))},
            "associations": [_link(contact_id, NOTE_TO_CONTACT)]})["id"]

    def make_task(self, contact_id, title, due_at):
        return self.call("POST", "/crm/v3/objects/tasks", {
            "properties": {"hs_task_subject": title[:200], "hs_timestamp": _ms(due_at),
                           "hs_task_status": "NOT_STARTED", "hs_task_type": "CALL"},
            "associations": [_link(contact_id, TASK_TO_CONTACT)]})["id"]

    def read(self, person):
        """Open tasks owned by this person (all open tasks when HubSpot has no owner for them),
        their contacts, and the failures still waiting. `owner` is the profile's member id."""
        mine = self.owner_id(person)
        tasks, after = [], None
        while True:
            page = self.call("GET", "/crm/v3/objects/tasks?limit=100&archived=false&associations=contacts"
                             "&properties=" + ",".join(TASK_FIELDS) + (f"&after={after}" if after else ""))
            for t in page.get("results") or []:
                p = t.get("properties") or {}
                links = ((t.get("associations") or {}).get("contacts") or {}).get("results") or []
                if p.get("hs_task_status") == "COMPLETED" or not links or not p.get("hs_timestamp"):
                    continue
                if mine and p.get("hubspot_owner_id") and p["hubspot_owner_id"] != mine:
                    continue
                tasks.append({"id": t["id"], "contact_id": str(links[0]["id"]), "title": p.get("hs_task_subject") or "Task",
                              "due_at": _iso(p["hs_timestamp"]), "status": "open"})
            after = ((page.get("paging") or {}).get("next") or {}).get("after")
            if not after or len(tasks) > 2000:
                break
        ids = sorted({t["contact_id"] for t in tasks})
        contacts = []
        for i in range(0, len(ids), 100):
            batch = self.call("POST", "/crm/v3/objects/contacts/batch/read", {
                "inputs": [{"id": x} for x in ids[i:i + 100]], "properties": CONTACT_FIELDS})
            for c in batch.get("results") or []:
                p = c.get("properties") or {}
                name = " ".join(x for x in (p.get("firstname"), p.get("lastname")) if x) or p.get("email") or "Contact"
                # A task assigned to this person makes its contact theirs for the brief.
                contacts.append({"id": str(c["id"]), "name": name, "phone": p.get("phone") or "",
                                 "email": p.get("email") or "", "owner": person.id, "kind": "person",
                                 "company_id": None})
        return {"contacts": contacts, "tasks": tasks, "research_links": {},
                "waiting": self.failures.open() if self.failures else []}

    def lead_alert(self, headers, raw, method="POST", uri=""):
        """A HubSpot webhook (contact.creation), checked with signature v3, as contact ids."""
        h = {k.lower(): v for k, v in headers.items()}
        sig, stamp = h.get("x-hubspot-signature-v3", ""), h.get("x-hubspot-request-timestamp", "")
        if not self.client_secret or not sig or not stamp.isdigit():
            raise SlotError("auth", "The push carried no HubSpot signature")
        if abs(time.time() * 1000 - int(stamp)) > MAX_SKEW_MS:
            raise SlotError("auth", "The HubSpot push is older than five minutes")
        body = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        mac = hmac.new(self.client_secret.encode(), (method + uri + body + stamp).encode(), hashlib.sha256)
        if not hmac.compare_digest(base64.b64encode(mac.digest()).decode(), sig):
            raise SlotError("auth", "The HubSpot signature did not match")
        events = json.loads(body)
        return [str(e["objectId"]) for e in (events if isinstance(events, list) else [events])
                if e.get("subscriptionType") == "contact.creation" and e.get("objectId") is not None]

    # ---- owners --------------------------------------------------------------------------
    def owner_id(self, person):
        if person.id in self.owners:
            return str(self.owners[person.id])
        emails = next((m.get("emails") or [] for m in self.people if m.get("id") == person.id), [])
        for email in emails:
            found = self.call("GET", "/crm/v3/owners?limit=1&email=" + urllib.request.quote(email)).get("results") or []
            if found:
                self.owners[person.id] = str(found[0]["id"])
                return self.owners[person.id]
        return ""


def _link(contact_id, type_id):
    return {"to": {"id": str(contact_id)},
            "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": type_id}]}


def _ms(dt):
    return str(int(dt.timestamp() * 1000))


def _iso(value):
    """HubSpot sends an ISO time or epoch milliseconds; the slot always answers ISO in UTC."""
    if str(value).isdigit():
        dt = datetime.fromtimestamp(int(value) / 1000, timezone.utc)
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _message(text):
    try:
        return json.loads(text).get("message", "")[:300]
    except (ValueError, AttributeError):
        return text[:300]


def from_profile(prof, root, environ=None):
    env = environ if environ is not None else os.environ
    cfg = (prof.get("crm") or {}).get("slot") or {}
    state = cfg.get("state_db") or f"Generated/slot/{prof['business_id']}.sqlite3"
    if Path(state).is_absolute() or ".." in Path(state).parts:
        raise ValueError("crm.slot.state_db must be a path inside the repository")
    return HubSpotSlot(env.get(cfg.get("token_env") or "HUBSPOT_TOKEN", ""),
                       env.get(cfg.get("client_secret_env") or "HUBSPOT_CLIENT_SECRET", ""),
                       owners=cfg.get("owners"), people=(prof.get("people") or {}).get("members", []),
                       failures=FailureLog(Path(root) / state), api=cfg.get("api") or API)
