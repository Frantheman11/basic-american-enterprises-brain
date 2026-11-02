"""Seed a CRM workspace from a reviewed JSON file, through the normal command path.

    python -m command_center.crm_seed --business cta --crm-db PATH --seed FILE --as sam

Every write is an ordinary CRM command: the same permission, audit and outbox rules as the
browser. Request IDs are derived from the seed file's keys, so a second run replays the same
acknowledgements and writes nothing new. A changed entry is refused (409), never duplicated.

The database is backed up with SQLite's online backup API before anything opens or upgrades it.
"""
import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from . import crm_store, identity, profiles


def backup_first(db_path):
    """Copy the database as it is now, before Store() can upgrade its schema."""
    src = Path(db_path).resolve()
    if not src.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = src.parent / "backups" / f"{src.stem}-{stamp}-before-seed.sqlite3"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():  # a backup is never overwritten
        raise SystemExit(f"crm_seed: backup {target} already exists")
    with closing(sqlite3.connect(src)) as source, closing(sqlite3.connect(target)) as dest:
        source.backup(dest)
    return target


def seed(store, prof, person, data, name):
    key = lambda k: str(uuid5(NAMESPACE_URL, f"crm-seed:{name}:{k}"))
    run = lambda action, body, k: store.execute(prof, person, {"action": action, "data": body, "request_id": key(k)})
    counts = {"contacts": 0, "tasks": 0}
    for c in data["contacts"]:
        fields = {f: c[f] for f in ("name", "kind", "email", "phone", "lifecycle") if f in c}
        if c.get("existing_name"):
            matches = [r for r in store.snapshot(prof, person)["contacts"]
                       if r["name"] == c["existing_name"] and r["kind"] == c.get("kind", "company")]
            if len(matches) != 1:
                raise crm_store.CRMError("Seed needs exactly one visible existing contact: " + c["existing_name"])
            cid = matches[0]["id"]
        else:
            cid = run("contact.create", fields, c["key"])["id"]
            counts["contacts"] += 1
        for p in c.get("people", []):
            fields = {f: p[f] for f in ("name", "email", "phone", "lifecycle") if f in p}
            run("contact.create", {**fields, "kind": "person", "company_id": cid}, p["key"])
            counts["contacts"] += 1
        for t in c.get("tasks", []):
            fields = {f: t[f] for f in ("title", "due_at", "category", "action", "action_detail") if f in t}
            run("task.create", {**fields, "contact_id": cid}, t["key"])
            counts["tasks"] += 1
        for act in c.get("activities", []):
            fields = {f: act[f] for f in ("kind", "body") if f in act}
            run("activity.add", {**fields, "contact_id": cid}, act["key"])
            counts["activities"] = counts.get("activities", 0) + 1
        for node in c.get("nodes", []):
            fields = {f: node[f] for f in ("node_id", "state", "brief_path", "location", "evidence") if f in node}
            run("node.set", {**fields, "contact_id": cid, "revision": 0}, node["key"])
            counts["nodes"] = counts.get("nodes", 0) + 1
    return counts


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--business", required=True)
    ap.add_argument("--root", default=".")
    ap.add_argument("--crm-db", required=True)
    ap.add_argument("--seed", required=True)
    ap.add_argument("--as", dest="person", required=True, help="the member id the writes are recorded as")
    a = ap.parse_args()
    prof = profiles.load(a.business, a.root)
    person = identity.person(prof, a.person)
    if person is None:
        raise SystemExit(f"crm_seed: no member '{a.person}' in the {a.business} profile")
    seed_path = Path(a.seed)
    data = json.loads(seed_path.read_text(encoding="utf-8"))
    saved = backup_first(a.crm_db)
    print(f"backup     {saved or 'none (new database)'}")
    store = crm_store.Store(a.crm_db, a.business)
    counts = seed(store, prof, person, data, seed_path.stem)
    print(f"seeded     {counts} (replays write nothing)")


if __name__ == "__main__":
    main()
