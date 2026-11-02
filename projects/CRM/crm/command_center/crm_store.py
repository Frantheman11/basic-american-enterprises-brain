"""One CRM writer: identified commands commit records, audit and events together."""
import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from . import access


class CRMError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def text(value, label, limit=200, required=False):
    if not isinstance(value, str) or len(value) > limit:
        raise CRMError(f"{label} must be text, at most {limit} characters")
    value = value.strip()
    if required and not value:
        raise CRMError(f"{label} is required")
    return value


def date_time(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.utcoffset() is None:
            raise ValueError()
        return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except (ValueError, AttributeError, TypeError):
        raise CRMError("Due date must include a valid time and UTC offset") from None


SCHEMA = 28
SOURCE = ("source_channel", "source_campaign", "source_ad")  # R4: "Meta · DSCR revised · Ad 3"
# A task action is built so a click can start it; this milestone only prepares it.
ACTIONS = {"none": "", "ask_client": "Question for the client", "work_session": "Brief path"}
BRIEF = re.compile(r"(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.md")


def categories(prof, kind=None):
    """Task categories come from the profile. The engine names none. The business record may
    have its own list (`business_task_categories`); without one it uses the client list."""
    crm = prof.get("crm") or {}
    rows = (kind == "business" and crm.get("business_task_categories")) or crm.get("task_categories") or []
    return [{"id": c["id"], "label": c.get("label", c["id"])} for c in rows
            if isinstance(c, dict) and isinstance(c.get("id"), str)]


class Store:
    feedback_protocol = 1
    payment_protocol = 1

    def __init__(self, path, business):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.business = business
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            # Older versions upgrade below; a newer database is refused.
            if version not in range(SCHEMA + 1):
                raise CRMError("Unsupported CRM database version", 503)
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS contacts (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
                    company_id TEXT REFERENCES contacts(id), name TEXT NOT NULL,
                    email TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '',
                    lifecycle TEXT NOT NULL DEFAULT 'lead', revision INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS deals (
                    id TEXT PRIMARY KEY, contact_id TEXT NOT NULL REFERENCES contacts(id),
                    title TEXT NOT NULL, stage TEXT NOT NULL, amount_cents INTEGER NOT NULL DEFAULT 0,
                    currency TEXT NOT NULL, lost_reason TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, contact_id TEXT NOT NULL REFERENCES contacts(id),
                    deal_id TEXT REFERENCES deals(id), title TEXT NOT NULL, due_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open', completed_at TEXT,
                    revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS client_nodes (
                    id TEXT PRIMARY KEY, contact_id TEXT NOT NULL REFERENCES contacts(id),
                    node_id TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('built','in_progress','recommended')),
                    brief_path TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '',
                    evidence TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(contact_id, node_id));
                CREATE TABLE IF NOT EXISTS node_reviews (
                    id TEXT PRIMARY KEY REFERENCES client_nodes(id),
                    decision TEXT NOT NULL CHECK(decision IN ('approved','revoked')),
                    actor TEXT NOT NULL, reviewed_at TEXT NOT NULL, node_revision INTEGER NOT NULL,
                    revision INTEGER NOT NULL, public_label TEXT NOT NULL, public_value TEXT NOT NULL,
                    public_reason TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS activities (
                    id TEXT PRIMARY KEY, contact_id TEXT NOT NULL REFERENCES contacts(id),
                    kind TEXT NOT NULL, body TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                    id TEXT PRIMARY KEY, command_id TEXT NOT NULL, actor TEXT NOT NULL,
                    action TEXT NOT NULL, entity_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS outbox (
                    id TEXT PRIMARY KEY REFERENCES audit(id), event_type TEXT NOT NULL,
                    payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS commands (
                    id TEXT PRIMARY KEY, actor TEXT NOT NULL, digest TEXT NOT NULL, response TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS research_claims (
                    place_id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
                    industry TEXT NOT NULL DEFAULT '', status TEXT NOT NULL
                    CHECK(status IN ('open','released','converted')),
                    claimed_at TEXT NOT NULL, touched_at TEXT NOT NULL,
                    contact_id TEXT REFERENCES contacts(id), revision INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS research_outcomes (
                    id TEXT PRIMARY KEY, place_id TEXT NOT NULL, actor TEXT NOT NULL,
                    outcome TEXT NOT NULL, channel TEXT NOT NULL, offer TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS research_outcomes_place ON research_outcomes(place_id, created_at);
                CREATE INDEX IF NOT EXISTS contacts_owner ON contacts(owner);
                CREATE INDEX IF NOT EXISTS deals_contact ON deals(contact_id);
                CREATE INDEX IF NOT EXISTS tasks_contact_due ON tasks(contact_id, due_at);
                CREATE INDEX IF NOT EXISTS activities_contact ON activities(contact_id, created_at);
            """)
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('business', ?)", (business,))
            if db.execute("SELECT value FROM metadata WHERE key='business'").fetchone()[0] != business:
                raise CRMError("Database belongs to another business", 403)
            # Version 2: task category and action. ALTER keeps every existing row.
            columns = {r["name"] for r in db.execute("PRAGMA table_info(tasks)")}
            for column in ("category", "action", "action_detail"):
                if column not in columns:
                    default = "'none'" if column == "action" else "''"
                    db.execute(f"ALTER TABLE tasks ADD COLUMN {column} TEXT NOT NULL DEFAULT {default}")
            # Version 8: where a lead came from, three levels (channel, campaign, ad).
            columns = {r["name"] for r in db.execute("PRAGMA table_info(contacts)")}
            for column in SOURCE:
                if column not in columns:
                    db.execute(f"ALTER TABLE contacts ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
            # Version 6: when a deal was won and who owned the client then (the leaderboard's
            # credit rule, Sam 2026-09-23). The stages live in the profile, not here, so a deal
            # won before this column existed is left NULL; leaderboard.py reads it by its last
            # update and its client's owner today, and marks the date as approximate.
            columns = {r["name"] for r in db.execute("PRAGMA table_info(deals)")}
            if "won_at" not in columns:
                db.execute("ALTER TABLE deals ADD COLUMN won_at TEXT")
                db.execute("ALTER TABLE deals ADD COLUMN won_by TEXT")
            from . import research_commands  # versions 7 and 8: the Research tables
            research_commands.migrate(db)
            from . import pipeline_stages, handoff  # version 8: edited stages; offer, monthly, hand-off
            pipeline_stages.migrate(db)  # v27: a stage names its pipeline (Portfolio CRM P3)
            handoff.migrate(db)
            record_edits.migrate(db)  # version 9: notes can be changed, checked to-dos archived
            record_deletes.migrate(db)  # version 11: a contact or deal can be deleted and restored
            contact_channels.migrate(db)  # 9: many phones and emails, a role; 10: address, website
            from . import territory
            territory.migrate(db)  # v28: a contact's state, for territory visibility (Portfolio CRM P5)
            lead_intake.migrate(db)  # version 12: website leads, keyed by Idempotency-Key; 25: the lead screen
            event_out.migrate(db)  # version 13: event-out deliveries (the universal connector)
            lead_alert.migrate(db)  # version 14: lead alert attempts and the last error
            call_log.migrate(db)  # version 15: phone calls from the AI receptionist
            email_list.migrate(db)  # version 16: freebie leads' email consent and unsubscribe tokens
            api_keys.migrate(db)  # version 17: keys for programs (Claude Code, Codex, scripts) and their activity
            from . import strategy_work
            strategy_work.migrate(db)  # v18: strategy task links
            from . import task_work
            task_work.migrate(db)  # v19: shared task blockers and completion evidence; v26: main tasks
            from . import oauth
            oauth.migrate(db)  # v23: Claude apps signed in by OAuth (mcp_connections, tokens)
            from . import brain
            brain.migrate(db)  # v24: one brain repo connection per business, its handshake and events
            db.execute(f"PRAGMA user_version={SCHEMA}")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def authorize(self, prof, person, capability="crm_read"):
        members = (prof.get("people") or {}).get("members", [])
        if (prof.get("business_id") != self.business or person is None
                or not any(m.get("id") == person.id and m.get("role") == person.role for m in members)
                or access.sees(prof, person) not in ("all", "own", "territory")
                or not access.can_do(prof, person, capability)):
            raise CRMError("You do not have permission for this CRM action", 403)

    @staticmethod
    def is_admin(prof, person):
        return access.sees(prof, person) == "all" and access.can_do(prof, person, "crm_assign")

    def contact(self, db, prof, person, cid):
        cid = text(cid, "Record ID", 80, True)
        row = db.execute("SELECT * FROM contacts WHERE id=?", (cid,)).fetchone()
        if not row or row["deleted_at"] or not territory.visible(db, prof, person, dict(row)):
            raise CRMError("Record not found", 404)
        return dict(row)

    def stages(self, prof, db=None):
        """The profile's stages, or the list edited on the screen (pipeline_stages.py)."""
        from . import pipeline_stages
        if db is not None:
            return pipeline_stages.current(db, prof)
        with self.connection() as own:
            return pipeline_stages.current(own, prof)

    def snapshot(self, prof, person):
        self.authorize(prof, person)
        with self.connection() as db:
            db.execute("BEGIN")
            all_records = access.sees(prof, person) == "all"
            scope, params = access.contact_scope(prof, person, "c")  # all, own or territory: one rule
            contacts = [dict(r) for r in db.execute("SELECT c.* FROM contacts c WHERE c.deleted_at IS NULL" + scope + " ORDER BY c.name", params)]
            result = {"contacts": contacts}
            owned = " WHERE c.deleted_at IS NULL" + scope
            for table in ("deals", "tasks", "activities", "client_nodes"):
                suffix = owned
                if table in ("activities", "deals"):  # removed: kept in the database, not on the screen
                    suffix += " AND r.deleted_at IS NULL"
                result[table] = [dict(r) for r in db.execute(
                    f"SELECT r.* FROM {table} r JOIN contacts c ON c.id=r.contact_id" + suffix +
                    " ORDER BY r.created_at DESC", params)]
            from . import task_work, task_labels
            task_work.enrich(db, result['tasks'])
            task_labels.enrich(prof, result['tasks'], contacts)
            result["channels"] = {}
            for r in db.execute("SELECT x.contact_id,x.kind,x.label,x.value FROM contact_channels x "
                                "JOIN contacts c ON c.id=x.contact_id" + owned +
                                " ORDER BY x.contact_id, x.position", params):
                result["channels"].setdefault(r["contact_id"], []).append(
                    {"kind": r["kind"], "label": r["label"], "value": r["value"]})
            suffix = " WHERE 1=1" + scope
            result["node_reviews"] = [dict(r) for r in db.execute(
                "SELECT r.* FROM node_reviews r JOIN client_nodes n ON n.id=r.id "
                "JOIN contacts c ON c.id=n.contact_id" + suffix, params)]
            result.update(name=prof["name"], person={"id": person.id, "name": person.name},
                          stages=self.stages(prof, db), pipelines=pipeline_stages.pipelines(prof), server_time=now(),
                          capabilities=[a for a in (access.role_of(prof, person) or {}).get("can", [])
                                        if a.startswith("crm_")],
                          members=[{"id": m["id"], "name": m.get("name", m["id"])}
                                   for m in (prof.get("people") or {}).get("members", [])]
                          if all_records and access.can_do(prof, person, "crm_assign") else [],
                          currency=(prof.get("crm") or {}).get("currency", "USD"),
                          tech_tree_catalog=catalog(prof), stages_revision=pipeline_stages.revision(db),
                          offers=handoff.offers(prof),
                          source_channels=[c for c in (prof.get("crm") or {}).get("source_channels", []) if isinstance(c, str)],
                          task_categories=categories(prof),
                          business_task_categories=categories(prof, "business"),
                          can_review_buildout=self.can_review_buildout(prof, person), **territory.screen(prof, person))
            if self.is_admin(prof, person):
                result["deleted"] = record_deletes.listing(db)
                from . import crm_io
                result["data_io"] = crm_io.limits()  # export and import buttons (admins only)
            from . import approvals, commission, research_claims
            result["waiting"] = approvals.read(db, prof, person)
            result["commission"] = commission.plans(prof, person)
            result["research_links"] = research_claims.links(
                db, prof, {c["id"] for c in contacts}, datetime.fromisoformat(result["server_time"]))
            known = {m["id"]: m.get("name", m["id"]) for m in (prof.get("people") or {}).get("members", [])}
            known[lead_intake.ACTOR] = lead_intake.ACTOR_NAME
            known[call_log.ACTOR] = call_log.ACTOR_NAME
            result["call_of"] = call_log.by_activity(db, [a["id"] for a in result["activities"] if a["kind"] == "call"])
            result["calls_enabled"] = call_log.config(prof) is not None
            result["actors"] = {a["actor"]: known.get(a["actor"].removeprefix("human:"), a["actor"])
                                for a in result["activities"]}
            return result

    def research_requests(self, prof, person):
        from . import research_requests
        self.authorize(prof, person)
        return research_requests.read(self, prof, person)

    def research_claims(self, prof, person):
        """Who holds which lead, and the history on each (research_claims.py)."""
        from . import research_claims
        self.authorize(prof, person)
        return research_claims.read(self, prof, person)

    def can_review_buildout(self, prof, person):
        return bool(person and access.can_do(prof, person, "crm_publish_buildout") and
                    person.id in (prof.get("crm") or {}).get("buildout_approvers", []))

    def client_snapshot(self, prof, person):
        """Allowlisted client read model (tech_tree.client_view)."""
        from .tech_tree import client_view
        return client_view(self, prof, person)

    def execute(self, prof, person, command):
        from . import strategy_work, payments, receptionist_admin
        self.authorize(prof, person)
        if not isinstance(command, dict) or not isinstance(command.get("data"), dict):
            raise CRMError("Command data must be an object")
        action = command.get("action")
        capabilities = {"contact.create": "crm_create", "contact.update": "crm_edit",
                        "receptionist.control": "crm_assign",
                        "contact.assign": "crm_assign", "deal.create": "crm_create",
                        "deal.update": "crm_edit", "task.create": "crm_edit",
                        **strategy_work.CAPABILITIES,
                        **dict.fromkeys(payments.ACTIONS, "crm_edit"),
                        "feedback.submit": "crm_edit",
                        "task.update": "crm_edit", "task.archive": "crm_edit", "activity.add": "crm_edit",
                        "activity.update": "crm_edit", "activity.delete": "crm_edit", "node.set": "crm_edit", "node.review": "crm_publish_buildout",
                        "research.claim": "crm_edit", "research.release": "crm_edit",
                        "research.outcome": "crm_edit", "research.reassign": "crm_assign",
                        "research.outcome.edit": "crm_edit", "research.release.undo": "crm_edit",
                        "research.document.request": "crm_edit", "research.print.request": "crm_edit",
                        "research.print.review": "crm_edit",
                        "research.request": "crm_edit", "research.request.decide": "crm_edit",
                        "pipeline.stages": "crm_assign", "deal.handoff": "crm_edit",
                        "lead.release": "crm_assign", "lead.dismiss": "crm_assign",
                        "contact.delete": "crm_edit", "deal.delete": "crm_edit",
                        "contact.restore": "crm_assign", "deal.restore": "crm_assign"}
        if isinstance(action, str) and action.startswith("gym."):
            from . import gym  # loaded only for gym commands: a release without gym.py still runs
            capabilities.update(gym.CAPABILITIES)
        if not isinstance(action, str) or action not in capabilities:
            raise CRMError("Unknown CRM command")
        self.authorize(prof, person, capabilities[action])
        if action == "node.review" and not self.can_review_buildout(prof, person):
            raise CRMError("Only a designated Build-out approver can review recommendations", 403)
        key = text(command.get("request_id"), "Request ID", 80, True)
        data = command["data"]
        digest = hashlib.sha256(json.dumps([action, data], sort_keys=True).encode()).hexdigest()
        actor = "human:" + person.id
        try:
            with self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                previous = db.execute("SELECT * FROM commands WHERE id=?", (key,)).fetchone()
                if previous:
                    if previous["actor"] != actor or previous["digest"] != digest:
                        raise CRMError("Request ID was already used for a different command", 409)
                    # Return only an acknowledgement, not old record content after reassignment.
                    return json.loads(previous["response"])
                result = (receptionist_admin.change(self, prof, person, data, key) if action == "receptionist.control"
                          else self._change(db, prof, person, action, data))
                from . import crm_events
                crm_events.record(db, key, actor, action, data, result)
                db.execute("INSERT INTO commands VALUES (?,?,?,?)", (key, actor, digest, json.dumps(result)))
                return result
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                raise CRMError("CRM is busy. Retry this same request shortly.", 503) from None
            raise

    def _change(self, db, prof, person, action, data):
        from . import strategy_work, payments
        stamp, rid = now(), str(uuid4())
        created_company = None
        if action in payments.ACTIONS:
            return payments.change(self, db, prof, person, action, data)
        if action.startswith("gym."):
            from . import gym  # off unless the profile says gym.enabled (gym.py)
            return gym.change(self, db, prof, person, action, data, stamp)
        if action in strategy_work.CAPABILITIES:
            return strategy_work.change(self, db, prof, person, action, data, stamp)
        if action == 'feedback.submit':
            from . import feedback
            return feedback.submit(self, db, prof, person, data)
        if action in ("lead.release", "lead.dismiss"):  # a website lead the screen held
            if data.get("step") != action.removeprefix("lead."):
                raise CRMError("The step must match the command")
            return lead_intake.settle(self, db, prof, person, data, stamp, "lead:" + rid)
        if action == "deal.handoff":
            from . import handoff
            return {"ok": True, "id": handoff.change(self, db, prof, person, data, stamp)}
        if action == "pipeline.stages":
            from . import pipeline_stages
            return {"ok": True, "id": pipeline_stages.change(self, db, prof, person, data, stamp)}
        if action in ("node.set", "node.review"):
            from . import tech_tree
            return {"ok": True, "id": tech_tree.change(self, db, prof, person, action, data, stamp, rid)}
        if action in record_deletes.ALLOWED:
            return record_deletes.change(self, db, prof, person, action, data, stamp)
        if action in record_edits.ALLOWED:
            return record_edits.change(self, db, prof, person, action, data, stamp)
        if action.startswith("research."):
            from . import research_commands
            return research_commands.change(self, db, prof, person, action, data, stamp)
        allowed = {
            "contact.create": {"name", "kind", "company_id", "company_name", "email", "phone", "channels", "role",
                               "address", "website", "territory", "lifecycle", "confirm_duplicate", "stage", *SOURCE},
            "contact.update": {"id", "revision", "name", "email", "phone", "channels", "role", "lifecycle",
                               "company_id", "company_name", "kind", "address", "website", "territory", *SOURCE},
            "contact.assign": {"id", "revision", "owner"},
            "deal.create": {"contact_id", "title", "amount_cents", "currency", "stage", "lost_reason", "offer", "monthly_cents"},
            "deal.update": {"id", "revision", "title", "amount_cents", "stage", "lost_reason", "offer", "monthly_cents"},
            "task.create": {"contact_id", "deal_id", "title", "due_at", "category", "action", "action_detail", "assignee", "department", "note", "blocked_reason", "followup_state", "review_at", "waiting_on", "is_main", "parent_id"},
            "task.update": {"id", "revision", "title", "due_at", "status", "deal_id", "category", "action", "action_detail",
                            "blocked_reason", "completion_evidence", "note", "assignee", "department", "followup_state", "review_at", "waiting_on",
                            "is_main", "parent_id"},
            "activity.add": {"contact_id", "kind", "body"},
}
        if set(data) - allowed[action]:
            raise CRMError("Unexpected fields in command")
        if action.startswith("contact."):
            kind = text(data.get("kind", "person"), "Contact type")
            if action == "contact.create":
                if kind not in ("person", "company", "business"):
                    raise CRMError("Choose person or company")
                if kind != "business" and data.get("confirm_duplicate") is not True:
                    from . import duplicates
                    duplicates.check(db, prof, data.get("name"), contact_channels.first_phone(data))
                # "business" is the workspace's own record: its tasks are the business's next
                # actions, not a client's. One per database, created by an administrator.
                if kind == "business":
                    if access.sees(prof, person) != "all" or not access.can_do(prof, person, "crm_assign"):
                        raise CRMError("Only an administrator can create the business record", 403)
                    if db.execute("SELECT 1 FROM contacts WHERE kind='business'").fetchone():
                        raise CRMError("This workspace already has its business record", 409)
                company_id, owner = None, person.id
                if kind == "person" and data.get("company_id"):
                    # "Add a person" from a company record: the person belongs with the company,
                    # so it takes the company's owner (an admin adding Manny to Ryan's lead).
                    owner = self.contact(db, prof, person, data["company_id"])["owner"]
                db.execute("INSERT INTO contacts (id,owner,kind,company_id,name,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                           (rid, owner, kind, company_id, text(data.get("name"), "Name", required=True), stamp, stamp))
                row = self.contact(db, prof, person, rid)
            else:
                rid = text(data.get("id"), "Record ID", 80, True)
                row = self.contact(db, prof, person, rid)
                self._revision(row, data)
            if action == "contact.assign":
                if access.sees(prof, person) != "all":
                    raise CRMError("Assignment requires visibility across the team", 403)
                owner = text(data.get("owner"), "Owner", 80)
                if owner not in [m["id"] for m in prof["people"]["members"]]:
                    raise CRMError("Choose a team member")
                if row["company_id"]:
                    raise CRMError("Reassign this person's company so related contacts move together")
                db.execute("UPDATE contacts SET owner=?,revision=revision+1,updated_at=? WHERE id=? OR company_id=?",
                           (owner, stamp, rid, rid))
            else:
                if action == "contact.update" and "kind" in data:
                    contact_channels.change_kind(db, row, data["kind"])  # batch 2: fix a wrong type
                created_company = contact_channels.link_company(self, db, prof, person, row, data, stamp)
                name = text(data.get("name", row["name"]), "Name", required=True)
                email = text(data.get("email", row["email"]), "Email", 254)
                if email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                    raise CRMError("Enter a valid email address")
                phone = text(data.get("phone", row["phone"]), "Phone", 60)
                lifecycle = data.get("lifecycle", row["lifecycle"])
                if lifecycle not in ("lead", "customer", "archived"):
                    raise CRMError("Choose lead, customer or archived")
                inc = 0 if action == "contact.create" else 1
                db.execute("UPDATE contacts SET name=?,email=?,phone=?,lifecycle=?,revision=revision+?,updated_at=? WHERE id=?",
                           (name, email, phone, lifecycle, inc, stamp, rid))
                contact_channels.apply(db, row, data, {"email": email, "phone": phone}, stamp)
                source = [text(data.get(k, row.get(k, "")), "Where they came from", 120) for k in SOURCE]
                db.execute("UPDATE contacts SET source_channel=?,source_campaign=?,source_ad=? WHERE id=?", (*source, rid))
                if action == "contact.create" and data.get("stage"):
                    # Straight into the pipeline: a lead card with no amount yet, in the same save.
                    spec = next((s for s in self.stages(prof, db) if s["id"] == data["stage"]), None)
                    if row["kind"] == "business":
                        raise CRMError("The business record is not a lead")
                    if not spec or spec["outcome"]:
                        raise CRMError("Choose an open pipeline stage. Mark a deal won or lost from Edit deal.")
                    db.execute("INSERT INTO deals (id,contact_id,title,stage,amount_cents,currency,lost_reason,"
                               "revision,created_at,updated_at) VALUES (?,?,?,?,0,?,'',1,?,?)",
                               (str(uuid4()), rid, name, spec["id"],
                                (prof.get("crm") or {}).get("currency", "USD"), stamp, stamp))
        elif action == "activity.add":
            contact = self.contact(db, prof, person, data.get("contact_id"))
            kind = data.get("kind", "note")
            if kind not in ("note", "call", "meeting", "email"):
                raise CRMError("Choose a supported activity type")
            body = text(data.get("body"), "Activity note", 10000, True)
            db.execute("INSERT INTO activities (id,contact_id,kind,body,actor,created_at) VALUES (?,?,?,?,?,?)",
                       (rid, contact["id"], kind, body, "human:" + person.id, stamp))
            if kind != "note":  # a logged touch keeps a Research lead, like an outcome does
                from . import research_claims
                research_claims.touch(db, prof, contact["id"], stamp)
        else:
            table = "deals" if action.startswith("deal.") else "tasks"
            creating = action.endswith("create")
            if creating:
                contact = self.contact(db, prof, person, data.get("contact_id"))
                row = {}
            else:
                rid = text(data.get("id"), "Record ID", 80, True)
                found = db.execute(f"SELECT * FROM {table} WHERE id=?", (rid,)).fetchone()
                if not found or (table == "deals" and found["deleted_at"]):
                    raise CRMError("Record not found", 404)
                row = dict(found)
                contact = self.contact(db, prof, person, row["contact_id"])
                self._revision(row, data)
            title = text(data.get("title", row.get("title")), "Title", required=True)
            if table == "deals":
                stages = self.stages(prof, db)
                stage = data.get("stage", row.get("stage", stages[0]["id"] if stages else ""))
                spec = next((s for s in stages if s["id"] == stage), None)
                if not spec:
                    raise CRMError("Choose a configured stage")
                amount = data.get("amount_cents", row.get("amount_cents", 0))
                if type(amount) is not int or not 0 <= amount <= 10**12:
                    raise CRMError("Amount must be a non-negative whole number of cents")
                currency = data.get("currency", row.get("currency", (prof.get("crm") or {}).get("currency", "USD")))
                if not isinstance(currency, str) or not re.fullmatch("[A-Z]{3}", currency):
                    raise CRMError("Currency must be a three-letter code")
                reason = text(data.get("lost_reason", row.get("lost_reason", "")), "Loss reason", 1000)
                if spec["outcome"] == "lost" and not reason:
                    raise CRMError("A loss reason is required")
                from . import handoff
                offer, monthly = handoff.fields(prof, data, row)
                if creating:
                    db.execute("INSERT INTO deals (id,contact_id,title,stage,amount_cents,currency,lost_reason,"
                               "revision,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                               (rid, contact["id"], title, stage, amount, currency, reason, 1, stamp, stamp))
                else:
                    db.execute("UPDATE deals SET title=?,stage=?,amount_cents=?,lost_reason=?,revision=revision+1,updated_at=? WHERE id=?",
                               (title, stage, amount, reason, stamp, rid))
                db.execute("UPDATE deals SET offer=?,monthly_cents=? WHERE id=?", (offer, monthly, rid))
                was_won = not creating and next((s["outcome"] for s in stages if s["id"] == row.get("stage")), "") == "won"
                if spec["outcome"] == "won" and not was_won:
                    db.execute("UPDATE contacts SET lifecycle='customer',revision=revision+1,updated_at=? WHERE id=?",
                               (stamp, contact["id"]))
                    # Credit goes to whoever owns the client at the moment it is won.
                    db.execute("UPDATE deals SET won_at=?,won_by=? WHERE id=?", (stamp, contact["owner"], rid))
                elif was_won and spec["outcome"] != "won":
                    db.execute("UPDATE deals SET won_at=NULL,won_by=NULL WHERE id=?", (rid,))
            else:
                due = date_time(data.get("due_at", row.get("due_at")))
                status = data.get("status", row.get("status", "open"))
                if status not in ("open", "completed", "cancelled"):
                    raise CRMError("Choose open, completed or cancelled")
                from . import task_work
                task_values = task_work.fields(db, row, data, status)
                assignee, department = task_work.assignment(prof, contact, row, data)
                is_main, parent_id = task_work.main_task(self, db, prof, person, row, data, rid)
                completed = (row.get("completed_at") or stamp) if status == "completed" else None
                deal_id = data.get("deal_id", row.get("deal_id")) or None
                if deal_id:
                    deal_id = text(deal_id, "Deal ID", 80, True)
                    if not db.execute("SELECT 1 FROM deals WHERE id=? AND contact_id=? AND deleted_at IS NULL", (deal_id, contact["id"])).fetchone():
                        raise CRMError("Deal does not belong to this contact")
                category = text(data.get("category", row.get("category", "")), "Category", 80)
                # Only a changed category is checked, so a profile edit never locks old tasks.
                if category and category != row.get("category") and category not in [c["id"] for c in categories(prof, contact["kind"])]:
                    raise CRMError("Choose a configured category")
                act = data.get("action", row.get("action", "none"))
                if act not in ACTIONS:
                    raise CRMError("Choose a supported task action")
                detail = text(data.get("action_detail", row.get("action_detail", "")) if act != "none" else "",
                              ACTIONS[act] or "Action detail", 2000, act != "none")
                if act == "work_session" and (not BRIEF.fullmatch(detail) or ".." in detail.split("/")):
                    raise CRMError("Brief path must be a relative .md path inside the repository")
                if creating:
                    db.execute("INSERT INTO tasks (id,contact_id,deal_id,title,due_at,status,completed_at,revision,"
                               "created_at,updated_at,category,action,action_detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (rid, contact["id"], deal_id, title, due, status, completed, 1, stamp, stamp,
                                category, act, detail))
                else:
                    db.execute("UPDATE tasks SET title=?,due_at=?,status=?,completed_at=?,deal_id=?,category=?,action=?,"
                               "action_detail=?,revision=revision+1,updated_at=? WHERE id=?",
                               (title, due, status, completed, deal_id, category, act, detail, stamp, rid))
                    if status != "completed":  # reopened: back on the list, out of the archive
                        db.execute("UPDATE tasks SET archived_at=NULL WHERE id=?", (rid,))
                    if status != row["status"] and "completed" in (status, row["status"]):
                        # "Task completed", or "Task reopened" when it is unchecked, so the history stays true.
                        db.execute("INSERT INTO activities (id,contact_id,kind,body,actor,created_at) VALUES (?,?,?,?,?,?)",
                                   (str(uuid4()), contact["id"], "task" if status == "completed" else "task_reopened",
                                    title, "human:" + person.id, stamp))
                task_work.save(db, rid, contact['id'], title, task_values, 'human:' + person.id, stamp)
                db.execute('UPDATE tasks SET assignee=?,department=?,is_main=?,parent_id=? WHERE id=?',
                           (assignee, department, is_main, parent_id, rid))
        result = {"ok": True, "id": rid}
        if action in ("contact.create", "contact.update"):
            result["company_id"] = row["company_id"]
        if created_company:
            result["created_company"] = created_company
        return result

    @staticmethod
    def _revision(row, data):
        if type(data.get("revision")) is not int or data["revision"] != row["revision"]:
            raise CRMError("This record changed. Refresh it before saving again.", 409)

    def backup(self, destination):
        """SQLite online backup includes committed WAL pages; never copy only the db file."""
        target = Path(destination).resolve()
        if target == self.path or target.exists():
            raise CRMError("Choose a new backup file")
        with self.connection() as source, closing(sqlite3.connect(target)) as dest:
            source.backup(dest)


from .tech_tree import catalog  # noqa: E402  (kept importable as crm_store.catalog)
from . import api_keys, call_log, contact_channels, email_list, event_out, handoff, lead_alert, lead_intake, pipeline_stages, record_deletes, record_edits, territory  # noqa: E402
