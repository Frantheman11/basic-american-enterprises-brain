"""Operator commands for a client's tech tree database. Run on the host that owns the database.

Every change goes through Store.execute: the same permission, revision, audit and outbox path as
the internal CRM. The person named by --as must be a profile member with the right capability;
approvals also need that person in crm.buildout_approvers.

  python -m command_center.buildout_admin --business ID --crm-db PATH --as sam status
  ... set NODE --state recommended|in_progress|built [--evidence T] [--location T] [--brief P]
  ... approve NODE --reason "What the client reads"
  ... revoke NODE
  ... backup [--dir DIR]
  ... restore FILE        (stop the view server first; the current database is backed up first)
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from . import crm_store, identity, profiles


def integrity(path):
    db = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    try:
        ok = db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        version = db.execute("PRAGMA user_version").fetchone()[0]
    finally:
        db.close()
    if not ok:
        raise crm_store.CRMError(f"{path} failed the SQLite integrity check", 503)
    if version != crm_store.SCHEMA:
        raise crm_store.CRMError(f"{path} is schema {version}, this engine is {crm_store.SCHEMA}", 503)


def backup(db_path, folder=None):
    """Consistent online copy (safe while the view server runs), then verified."""
    db_path = Path(db_path).resolve()
    folder = Path(folder) if folder else db_path.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = folder / f"{db_path.stem}-{stamp}.sqlite3"
    src, dst = sqlite3.connect(db_path), sqlite3.connect(target)
    try:
        src.backup(dst)
        # One self-contained file to copy off the host: no -wal / -shm side files.
        dst.execute("PRAGMA journal_mode=DELETE")
    finally:
        dst.close()
        src.close()
    integrity(target)
    return target


def restore(db_path, source):
    """Replace the database with a verified backup. The current file is backed up first."""
    db_path, source = Path(db_path).resolve(), Path(source).resolve()
    if source == db_path:
        raise crm_store.CRMError("Choose a backup file, not the live database")
    integrity(source)
    before = backup(db_path) if db_path.exists() else None
    src, dst = sqlite3.connect(source), sqlite3.connect(db_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    integrity(db_path)
    return before


class Operator:
    def __init__(self, business, root, db_path, person_id):
        self.prof = profiles.load(business, root)
        self.person = identity.person(self.prof, person_id)
        if self.person is None:
            raise crm_store.CRMError(f"{person_id!r} is not a member of this profile", 403)
        self.store = crm_store.Store(db_path, business)

    def contact_id(self, requested=None):
        mapped = sorted(set((self.prof.get("client_buildout") or {}).get("readers", {}).values()))
        if requested:
            if requested not in mapped:
                raise crm_store.CRMError("That contact is not mapped to a client reader")
            return requested
        if len(mapped) != 1:
            raise crm_store.CRMError("Pass --contact: the profile maps zero or several client contacts")
        return mapped[0]

    def run(self, action, data):
        return self.store.execute(self.prof, self.person,
                                  {"action": action, "data": data, "request_id": str(uuid4())})

    def rows(self, cid):
        snap = self.store.snapshot(self.prof, self.person)
        nodes = {n["node_id"]: n for n in snap["client_nodes"] if n["contact_id"] == cid}
        reviews = {r["id"]: r for r in snap["node_reviews"]}
        return nodes, reviews

    def set(self, cid, node_id, state, **fields):
        node = self.rows(cid)[0].get(node_id)
        data = {"contact_id": cid, "node_id": node_id, "revision": node["revision"] if node else 0,
                "state": state}
        data.update({k: v for k, v in fields.items() if v is not None})
        return self.run("node.set", data)

    def review(self, cid, node_id, decision, reason=""):
        nodes, reviews = self.rows(cid)
        node = nodes.get(node_id)
        if node is None:
            raise crm_store.CRMError("Record the build first with: set NODE --state recommended", 404)
        review = reviews.get(node["id"])
        return self.run("node.review", {"contact_id": cid, "node_id": node_id,
                                        "node_revision": node["revision"],
                                        "revision": review["revision"] if review else 0,
                                        "decision": decision, "reason": reason})

    def status(self, cid):
        nodes, reviews = self.rows(cid)
        labels = {n["id"]: n["label"] for n in crm_store.catalog(self.prof)["nodes"]}
        out = []
        for node_id, n in sorted(nodes.items()):
            r = reviews.get(n["id"])
            if n["state"] == "built":
                client = "shown (built)"
            elif (n["state"] == "recommended" and r and r["decision"] == "approved"
                  and r["node_revision"] == n["revision"]):
                client = "shown (approved)"
            elif r and r["decision"] == "approved":
                client = "hidden (approval lapsed after an edit)"
            else:
                client = "hidden"
            out.append({"node": node_id, "label": labels.get(node_id, node_id), "state": n["state"],
                        "revision": n["revision"], "client_view": client})
        return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--business", required=True)
    ap.add_argument("--root", default=".")
    ap.add_argument("--crm-db", required=True)
    ap.add_argument("--as", dest="person", help="Profile member id doing the change")
    ap.add_argument("--contact", help="Only needed when the profile maps several client contacts")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    s = sub.add_parser("set")
    s.add_argument("node")
    s.add_argument("--state", required=True, choices=["recommended", "in_progress", "built"])
    s.add_argument("--evidence")
    s.add_argument("--location")
    s.add_argument("--brief", dest="brief_path")
    a = sub.add_parser("approve")
    a.add_argument("node")
    a.add_argument("--reason", required=True)
    r = sub.add_parser("revoke")
    r.add_argument("node")
    b = sub.add_parser("backup")
    b.add_argument("--dir")
    rs = sub.add_parser("restore")
    rs.add_argument("file")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "backup":
            print(backup(args.crm_db, args.dir))
            return 0
        if args.cmd == "restore":
            print("Previous database saved to", restore(args.crm_db, args.file))
            return 0
        if not args.person:
            ap.error("--as is required for this command")
        if not Path(args.crm_db).is_file():
            raise crm_store.CRMError("Database not found. Create it explicitly first.", 404)
        op = Operator(args.business, args.root, args.crm_db, args.person)
        cid = op.contact_id(args.contact)
        if args.cmd == "status":
            result = op.status(cid)
        elif args.cmd == "set":
            result = op.set(cid, args.node, args.state, evidence=args.evidence,
                            location=args.location, brief_path=args.brief_path)
        else:
            result = op.review(cid, args.node, "approved" if args.cmd == "approve" else "revoked",
                               getattr(args, "reason", ""))
        print(json.dumps(result, indent=2))
        return 0
    except (crm_store.CRMError, identity.StartupRefused) as error:
        print("Refused:", error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
