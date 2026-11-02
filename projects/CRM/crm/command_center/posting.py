"""Posting: the content engine's three verbs, whichever vendor carries the posts (2026-10-03).

The same shape as the slot (slot.py). A module asks `for_profile` for a poster and a queue; it
never imports a vendor client. The vendor is one file behind the interface (posting_outstand.py).
Vendor choice: docs/content/posting-vendor-decision-2026-10-03.md.

    post     send one approved post (now, or at its scheduled time) and return the vendor's id
    status   ask the vendor what happened to it: pending, published, failed or deleted
    delete   take it down, or cancel it before it goes out

Three rules this file enforces, because silent failure is the category's top complaint:

1. **A person approves every post.** `Queue.send` refuses a post that is not approved, and an
   approval is tied to the exact text, media, channels and time. An edit clears it.
2. **A failure is never silent.** A vendor error, a per-channel failure, or a post still
   "pending" long after its time goes on the failed list and calls the alert.
3. **Channels are capped, posts are not** (the profile's `max_channels`).

The profile names where the key is, never the key (architecture R6):

    "posting": {
      "vendor": "outstand",
      "key_env": "OUTSTAND_API_KEY",
      "max_channels": 3,
      "accounts": {"tiktok": "<vendor account id>", ...},
      "state_db": "Generated/posting/<business>.sqlite3",
      "stuck_after_minutes": 60
    }

Nothing in this file sends email or posts by itself. The CLI is tools/posting.py.
"""
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

KINDS = ("auth", "invalid", "rate_limit", "unavailable", "not_found")
STATES = ("draft", "approved", "sent", "published", "failed", "deleted")


class PostingError(Exception):
    """A verb that could not finish. `kind` is one of KINDS; `reason` is a sentence the owner
    can act on (reconnect the account, change the picture, try later)."""

    def __init__(self, kind, reason, detail=""):
        super().__init__(reason)
        self.kind, self.reason, self.detail = kind, reason, str(detail)[:2000]


class Poster:
    """The interface. Plain dicts in and out, never a vendor object."""
    name = "poster"

    def post(self, item):
        """`item`: {text, media: [{url, filename}], accounts: [vendor ids], scheduled_at: ISO or None}.
        Returns the vendor's post id."""
        raise NotImplementedError

    def status(self, post_id):
        """{"state": pending|published|failed|deleted, "channels": [{account, state, error, url}],
        "error": first failure reason or ""}."""
        raise NotImplementedError

    def delete(self, post_id):
        raise NotImplementedError


def now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def fingerprint(text, media, channels, scheduled_at):
    """What an approval covers. Any change to these four makes it a different post."""
    blob = json.dumps({"text": text, "media": media, "channels": sorted(channels),
                       "scheduled_at": scheduled_at}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


class Queue:
    """One business's posts and their history, in one small SQLite file. The failed list and
    the morning brief's "waiting for you" both read from here."""

    def __init__(self, path, accounts=None, max_channels=3, stuck_after_minutes=60, alert=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.accounts, self.max_channels = dict(accounts or {}), int(max_channels)
        self.stuck_after = timedelta(minutes=int(stuck_after_minutes))
        self.alert = alert
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS posts (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, source TEXT NOT NULL,
                text TEXT NOT NULL, media TEXT NOT NULL, channels TEXT NOT NULL,
                scheduled_at TEXT, hash TEXT NOT NULL, state TEXT NOT NULL,
                approved_by TEXT, approved_at TEXT, approved_hash TEXT,
                vendor TEXT, vendor_id TEXT, sent_at TEXT, published_at TEXT,
                error_kind TEXT, error TEXT, alerted_at TEXT, resolved_at TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, post_id TEXT NOT NULL, at TEXT NOT NULL,
                what TEXT NOT NULL, detail TEXT NOT NULL)""")

    # ---- storage -------------------------------------------------------------------------
    def _db(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def _log(self, db, post_id, what, detail=""):
        db.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                   (str(uuid4()), post_id, iso(now()), what, str(detail)[:2000]))

    def get(self, post_id):
        with closing(self._db()) as db:
            row = db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
        if row is None:
            raise KeyError(f"no post {post_id}")
        out = dict(row)
        out["media"], out["channels"] = json.loads(out["media"]), json.loads(out["channels"])
        return out

    def list(self, state=None):
        with closing(self._db()) as db:
            rows = db.execute("SELECT id FROM posts" + (" WHERE state=?" if state else "")
                              + " ORDER BY COALESCE(scheduled_at, created_at)", (state,) if state else ()).fetchall()
        return [self.get(r["id"]) for r in rows]

    def history(self, post_id):
        with closing(self._db()) as db:
            return [dict(r) for r in db.execute("SELECT at, what, detail FROM events WHERE post_id=? ORDER BY at",
                                                (post_id,))]

    # ---- drafting and approval -----------------------------------------------------------
    def _check(self, text, channels, scheduled_at):
        if not (text or "").strip():
            raise ValueError("A post needs text")
        if not channels:
            raise ValueError("A post needs at least one channel")
        if len(set(channels)) > self.max_channels:
            raise ValueError(f"{len(set(channels))} channels is over this plan's cap of {self.max_channels}")
        missing = [c for c in channels if not self.accounts.get(c)]
        if missing:
            raise ValueError(f"No connected account for {', '.join(missing)}. Connect it, then put its id in the profile")
        if "\u2014" in text:
            raise ValueError("The post has an em dash. House rule: zero em dashes")
        if scheduled_at:
            datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))

    def add(self, text, channels, media=None, scheduled_at=None, source="manual"):
        media = list(media or [])
        self._check(text, channels, scheduled_at)
        pid = str(uuid4())
        with closing(self._db()) as db, db:
            db.execute("INSERT INTO posts (id, created_at, source, text, media, channels, scheduled_at, hash, state)"
                       " VALUES (?,?,?,?,?,?,?,?,'draft')",
                       (pid, iso(now()), source, text, json.dumps(media), json.dumps(sorted(set(channels))),
                        scheduled_at, fingerprint(text, media, channels, scheduled_at)))
            self._log(db, pid, "drafted", source)
        return pid

    def edit(self, post_id, text=None, channels=None, media=None, scheduled_at=None):
        p = self.get(post_id)
        if p["state"] not in ("draft", "approved", "failed"):
            raise ValueError(f"A {p['state']} post cannot be edited; delete it and draft a new one")
        text = p["text"] if text is None else text
        channels = p["channels"] if channels is None else channels
        media = p["media"] if media is None else list(media)
        scheduled_at = p["scheduled_at"] if scheduled_at is None else scheduled_at
        self._check(text, channels, scheduled_at)
        with closing(self._db()) as db, db:
            db.execute("UPDATE posts SET text=?, channels=?, media=?, scheduled_at=?, hash=?, state='draft',"
                       " approved_by=NULL, approved_at=NULL, approved_hash=NULL, vendor_id=NULL,"
                       " resolved_at=CASE WHEN error IS NOT NULL THEN ? ELSE resolved_at END WHERE id=?",
                       (text, json.dumps(sorted(set(channels))), json.dumps(media), scheduled_at,
                        fingerprint(text, media, channels, scheduled_at), iso(now()), post_id))
            self._log(db, post_id, "edited", {"approved": "approval cleared", "failed": "failure resolved by the edit"}.get(p["state"], ""))

    def approve(self, post_id, by):
        if not (by or "").strip():
            raise ValueError("An approval needs the name of the person who approved")
        p = self.get(post_id)
        if p["state"] != "draft":
            raise ValueError(f"Only a draft can be approved; this one is {p['state']}")
        with closing(self._db()) as db, db:
            db.execute("UPDATE posts SET state='approved', approved_by=?, approved_at=?, approved_hash=? WHERE id=?",
                       (by.strip(), iso(now()), p["hash"], post_id))
            self._log(db, post_id, "approved", by.strip())

    # ---- the three verbs, through the queue ----------------------------------------------
    def send(self, post_id, poster):
        """Hand one approved post to the vendor. Returns {"ok", "vendor_id", "error"}; a failure is
        recorded and alerted, never raised, so a vendor's bad minute never loses a post."""
        p = self.get(post_id)
        if p["state"] != "approved" or not p["approved_by"]:
            raise PermissionError("This post is not approved. A person approves every post before it is sent")
        if p["approved_hash"] != p["hash"]:
            raise PermissionError("The post changed after it was approved. Approve it again")
        if p["scheduled_at"] and datetime.fromisoformat(p["scheduled_at"].replace("Z", "+00:00")) < now() - timedelta(minutes=5):
            return self._fail(p, poster, PostingError("invalid", "The scheduled time has passed. Pick a new time and approve again"))
        item = {"text": p["text"], "media": p["media"], "scheduled_at": p["scheduled_at"],
                "accounts": [self.accounts[c] for c in p["channels"]]}
        try:
            vendor_id = poster.post(item)
        except PostingError as e:
            return self._fail(p, poster, e)
        with closing(self._db()) as db, db:
            db.execute("UPDATE posts SET state='sent', vendor=?, vendor_id=?, sent_at=?, error_kind=NULL, error=NULL"
                       " WHERE id=?", (poster.name, vendor_id, iso(now()), post_id))
            self._log(db, post_id, "sent", f"{poster.name} {vendor_id}")
        return {"ok": True, "vendor_id": vendor_id, "error": None}

    def check(self, poster, at=None):
        """Ask the vendor about every sent post. Published ones close; failed ones and ones still
        pending long after their time go on the failed list. Returns the newly failed posts."""
        at = at or now()
        newly = []
        for p in self.list("sent"):
            try:
                st = poster.status(p["vendor_id"])
            except PostingError as e:
                if e.kind in ("rate_limit", "unavailable"):
                    continue  # the vendor is busy; the post may be fine. Ask again next round
                newly.append(self._fail(p, poster, e))
                continue
            if st["state"] == "published":
                with closing(self._db()) as db, db:
                    db.execute("UPDATE posts SET state='published', published_at=? WHERE id=?", (iso(at), p["id"]))
                    self._log(db, p["id"], "published", json.dumps(st.get("channels", [])))
            elif st["state"] == "failed":
                kind = "auth" if "token" in (st.get("error") or "").lower() or "reconnect" in (st.get("error") or "").lower() else "invalid"
                newly.append(self._fail(p, poster, PostingError(kind, st.get("error") or "The vendor says the post failed",
                                                               json.dumps(st.get("channels", [])))))
            elif st["state"] == "deleted":
                with closing(self._db()) as db, db:
                    db.execute("UPDATE posts SET state='deleted' WHERE id=?", (p["id"],))
                    self._log(db, p["id"], "deleted at the vendor")
            else:
                due = datetime.fromisoformat((p["scheduled_at"] or p["sent_at"]).replace("Z", "+00:00"))
                if at - due > self.stuck_after:
                    newly.append(self._fail(p, poster, PostingError(
                        "unavailable", f"Still not published {int((at - due).total_seconds() // 60)} minutes after its time. "
                                       "Check the channel before sending it again")))
        return [n for n in newly if not n["ok"]]

    def delete(self, post_id, poster):
        p = self.get(post_id)
        if p["vendor_id"] and p["state"] in ("sent", "published", "failed"):
            try:
                poster.delete(p["vendor_id"])
            except PostingError as e:
                if e.kind != "not_found":
                    return self._fail(p, poster, e, keep_state=True)
        with closing(self._db()) as db, db:
            db.execute("UPDATE posts SET state='deleted' WHERE id=?", (post_id,))
            self._log(db, post_id, "deleted")
        return {"ok": True, "vendor_id": p["vendor_id"], "error": None}

    # ---- failures ------------------------------------------------------------------------
    def _fail(self, p, poster, e, keep_state=False):
        with closing(self._db()) as db, db:
            db.execute("UPDATE posts SET state=?, error_kind=?, error=?, resolved_at=NULL WHERE id=?",
                       (p["state"] if keep_state else "failed", e.kind, e.reason, p["id"]))
            self._log(db, p["id"], "failed", f"{e.kind}: {e.reason} {e.detail}".strip())
        failed = self.get(p["id"])
        if self.alert:
            try:
                self.alert(failed)
                with closing(self._db()) as db, db:
                    db.execute("UPDATE posts SET alerted_at=? WHERE id=?", (iso(now()), p["id"]))
            except Exception as alert_error:  # a broken alert must not hide the failure itself
                with closing(self._db()) as db, db:
                    self._log(db, p["id"], "alert failed", repr(alert_error))
        return {"ok": False, "vendor_id": p["vendor_id"], "error": f"{e.kind}: {e.reason}"}

    def failed(self):
        """The visible failed list: every failure nobody has resolved yet."""
        with closing(self._db()) as db:
            ids = [r["id"] for r in db.execute(
                "SELECT id FROM posts WHERE error IS NOT NULL AND resolved_at IS NULL ORDER BY sent_at, created_at")]
        return [self.get(i) for i in ids]

    def unalerted(self):
        return [p for p in self.failed() if not p["alerted_at"]]

    def resolve(self, post_id, note=""):
        with closing(self._db()) as db, db:
            db.execute("UPDATE posts SET resolved_at=? WHERE id=?", (iso(now()), post_id))
            self._log(db, post_id, "resolved", note)

    def waiting(self):
        """The failed list in the morning brief's "waiting for you" shape."""
        return [{"kind": "post", "id": p["id"], "title": f"Post failed ({', '.join(p['channels'])}): {p['error']}",
                 "by": p["vendor"] or "posting", "at": p["sent_at"] or p["created_at"], "error": p["error_kind"]}
                for p in self.failed()]


def config(brand):
    cfg = brand.get("posting") or {}
    if not cfg:
        raise KeyError(f"brand '{brand.get('id')}' has no posting block")
    return cfg


def queue_for(brand, root, alert=None):
    cfg = config(brand)
    state = cfg.get("state_db") or f"Generated/posting/{brand['id']}.sqlite3"
    if Path(state).is_absolute() or ".." in Path(state).parts:
        raise ValueError("posting.state_db must be a path inside the repository")
    return Queue(Path(root) / state, accounts=cfg.get("accounts"), max_channels=cfg.get("max_channels", 3),
                 stuck_after_minutes=cfg.get("stuck_after_minutes", 60), alert=alert)


def for_profile(brand, environ=None):
    """The poster the profile names in `posting.vendor`. Only "outstand" has an adapter today."""
    cfg = config(brand)
    env = environ if environ is not None else os.environ
    vendor = cfg.get("vendor", "outstand")
    if vendor == "outstand":
        from . import posting_outstand
        return posting_outstand.OutstandPoster(env.get(cfg.get("key_env") or "OUTSTAND_API_KEY", ""),
                                               api=cfg.get("api") or posting_outstand.API)
    raise ValueError(f"posting.vendor {vendor!r} has no adapter yet")
