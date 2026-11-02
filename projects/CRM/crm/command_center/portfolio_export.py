"""Export one business (Portfolio CRM P6): the handover file for selling one company.

One ZIP a buyer opens in a fresh install of the CRM module:

    <business>-handover-<day>/
      README.txt                                what is inside and how to start it
      data/command-center/<business>.json       its profile, without the seller's outside accounts
      private/<business>.sqlite3                its whole database (SQLite online backup)
      csv/contacts.csv, deals.csv, open-todos.csv   the same files as Export to Excel
      <brand files the profile names>           stylesheet, logo, fonts, theme, when present

Nothing of the other businesses goes in: no other profile, no link rows (a link names records the
buyer does not get), no roll-up. Owner only, and the person must be an administrator of that
business (the Excel export's own rule). Two audit lines land in the business's database:
the Excel export's and one for the handover.
"""
import copy
import io
import json
import sqlite3
import tempfile
from contextlib import closing
import zipfile
from datetime import datetime
from pathlib import Path

from . import crm_io, crm_store, profiles

BRAND_KEYS = ("logo", "font_stylesheet", "theme_stylesheet")
# Emptied in the COPY, never in the business's own database: the seller's sign-ins and keys (program
# API keys, Claude app OAuth, the brain repo connection) and deliveries queued for the seller's hooks.
# Email consent stays: the buyer must keep honouring every unsubscribe.
SCRUB = ("api_keys", "api_key_events", "mcp_connections", "oauth_clients", "oauth_codes", "oauth_consents",
         "oauth_tokens", "brain_connect_states", "brain_connections", "brain_events", "event_deliveries",
         "lead_alert_deliveries")


def scrub(path):
    """Empty the SCRUB tables in a database copy, then VACUUM so no deleted row stays in the file."""
    with closing(sqlite3.connect(path)) as db:
        have = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        with db:
            for table in SCRUB:
                if table in have:
                    db.execute(f"DELETE FROM {table}")
        db.execute("VACUUM")


class ExportError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def clean_profile(prof, person_id):
    """The profile a buyer gets: the seller's outside connections and sign-in removed."""
    from .portfolio import CRM_DROP, TOP_DROP
    out = {k: copy.deepcopy(v) for k, v in prof.items() if not k.startswith("_") and k not in TOP_DROP}
    out["crm"] = {k: v for k, v in (out.get("crm") or {}).items() if k not in CRM_DROP and k not in ("oauth", "path_prefix")}
    out["access"] = {"identity": "local", "local_person": person_id, "origins": []}  # the buyer sets their own
    profiles.validate_profile(copy.deepcopy(out), "handover profile")
    return out


def _brand_files(prof, root):
    """[(relative path, bytes)] for the brand files the profile names that exist inside the root."""
    base = Path(root).resolve()
    names = [prof.get("stylesheet")] + [(prof.get("crm") or {}).get(k) for k in BRAND_KEYS]
    out = []
    for rel in names:
        if not isinstance(rel, str) or not rel or Path(rel).is_absolute():
            continue
        f = (base / rel).resolve()
        if base in f.parents and f.is_file():
            out.append((Path(rel).as_posix(), f.read_bytes()))
    return out


README = """{name}: handover file
Made {day} by {who} from their CRM.

INSIDE
- data/command-center/{bid}.json   the business's profile: people, roles, pipelines, stages, regions.
                                     Its sign-in is set to local mode for {person}; set your own.
- private/{bid}.sqlite3            every record: contacts, companies, deals, to-dos, notes, history.
- csv/                              the same records as spreadsheets, for anyone not running the CRM.
{brand}
LEFT OUT ON PURPOSE
- The seller's other businesses, and any link from these records to people in them.
- Website lead intake, phone and chat, content, research, payments, analytics and event-out settings:
  they pointed at the seller's own accounts. Set yours up again.
- Program keys, Claude app sign-ins and the brain connection: make your own.
KEPT ON PURPOSE
- Every email consent and unsubscribe. Keep honouring them.

START IT
1. Install the CRM module (version {version} or later) in a new folder.
2. Copy this folder's data/, private/ and brand files into it.
3. Run:  python -B command_center/server.py --business {bid} --root . --identity local --crm-db private/{bid}.sqlite3
4. Open http://127.0.0.1:8795/
"""


def handover(pf, seen, bid, person):
    """(filename, ZIP bytes, counts). `seen` is [(entry, person)] from Portfolio.visible."""
    if person is None or person.id not in pf.data["owners"]:
        raise ExportError("Only the owner can export a business", 403)
    entry = next((e for e, _ in seen if e["business_id"] == bid), None)
    if entry is None:
        raise ExportError("Business not found", 404)
    who = next(p for e, p in seen if e["business_id"] == bid)
    prof = profiles.load(bid, pf.root)
    store = pf.configs[bid].crm
    try:
        _, csv_zip, counts = crm_io.export(store, prof, who)  # admin-only, and its own audit line
    except crm_store.CRMError as e:
        raise ExportError(str(e), e.status)
    crm_io._audit(store, who, "export.handover", "export", {"rows": counts})
    clean = clean_profile(prof, who.id)
    with tempfile.TemporaryDirectory() as tmp:
        copy_at = Path(tmp) / "copy.sqlite3"
        store.backup(copy_at)
        scrub(copy_at)
        database = copy_at.read_bytes()
    day = datetime.now().strftime("%Y-%m-%d")
    stem = f"{bid}-handover-{day}"
    brand = _brand_files(prof, pf.root)
    package = Path(__file__).resolve().parent.parent / "modules/crm/package.json"
    version = json.loads(package.read_text(encoding="utf-8"))["version"] if package.is_file() else "0.22.0"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{stem}/README.txt", README.format(
            name=prof["name"], day=day, who=who.name, bid=bid, person=who.id, version=version,
            brand="".join(f"- {rel}   brand file\n" for rel, _ in brand)))
        z.writestr(f"{stem}/data/command-center/{bid}.json", json.dumps(clean, indent=1) + "\n")
        z.writestr(f"{stem}/private/{bid}.sqlite3", database)
        with zipfile.ZipFile(io.BytesIO(csv_zip)) as csvs:
            for name in csvs.namelist():
                z.writestr(f"{stem}/csv/{Path(name).name}", csvs.read(name))
        for rel, data in brand:
            z.writestr(f"{stem}/{rel}", data)
    return stem + ".zip", out.getvalue(), counts
