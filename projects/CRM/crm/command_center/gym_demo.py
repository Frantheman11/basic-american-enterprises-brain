"""The gym module's demo pieces: simulated billing, the switch guide and a sample export.

Billing is P2 (Stripe Connect, docs/products/gym-system-spec-2026-10-27.md). Until then a demo shows
what the owner will see with a simulated billing table that only the demo builder creates. Nothing
here calls Stripe, sends a text or holds an amount. It answers only when the profile says
`gym.billing: "demo"` AND the profile is a fictional demo (demo_mode), so a real gym can never
show simulated money.

The switch guide is profile data (`gym.switch`, copied by the builder from data/gym-switch/<tool>.json):
the steps from the gym's current tool to this one, and the column names of that tool's member export.
"""
import csv
import io
import random
from datetime import date

from . import crm_store, demo_mode, gym

CARD_TEXT = "Hi {first}, the card {gym} has on file {what}. Update it here, it takes a minute: {link}"
WHAT = {"Card expired": "has expired"}  # every other reason: "was declined"


def billing_on(prof):
    demo = demo_mode.config(prof) or {}
    return (gym.config(prof) or {}).get("billing") == "demo" and demo.get("fictional") is True


def migrate(db):
    db.execute("CREATE TABLE IF NOT EXISTS demo_billing (contact_id TEXT PRIMARY KEY, status TEXT NOT NULL, "
               "next_on TEXT NOT NULL, failed_on TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', "
               "link_sent_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL, classes_left INTEGER, amount_cents INTEGER)")
    if "amount_cents" not in {r[1] for r in db.execute("PRAGMA table_info(demo_billing)")}:  # a build from before 0.16
        db.execute("ALTER TABLE demo_billing ADD COLUMN amount_cents INTEGER")


def _table(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='demo_billing'").fetchone() is not None


def _row(r, rows):
    m = rows[r["contact_id"]]
    return {"id": r["contact_id"], "name": m["name"], "phone": m["phone"], "plan": (m["membership"] or {}).get("plan", ""),
            "status": r["status"], "next_on": r["next_on"], "failed_on": r["failed_on"], "reason": r["reason"],
            "link_sent": bool(r["link_sent_at"]), "classes_left": r["classes_left"], "amount_cents": r["amount_cents"]}


def today(db, prof, rows):
    """The Today card. Without demo billing: the P1 line, never a zero."""
    if not billing_on(prof) or not _table(db):
        return {"connected": False, "text": "Billing not connected yet. Failed payments show here once billing "
                                            "is switched on."}
    failed = [_row(r, rows) for r in db.execute("SELECT * FROM demo_billing WHERE status='failed' ORDER BY failed_on DESC")
              if r["contact_id"] in rows]
    return {"connected": "demo", "failed": failed, "text": "Demo billing: made-up members, no real cards or charges."}


def summary(store, prof, rows_of):
    if not billing_on(prof):
        raise crm_store.CRMError("Billing is not connected yet", 404)
    on = gym.today(prof).isoformat()
    with store.connection() as db:
        db.execute("BEGIN")
        rows = rows_of(db)
        if not _table(db):
            raise crm_store.CRMError("Billing is not connected yet", 404)
        all_rows = [_row(r, rows) for r in db.execute("SELECT * FROM demo_billing ORDER BY next_on, contact_id")
                    if r["contact_id"] in rows]
        plans = _plans(db)
        # A month of revenue: monthly plans, plus a twelfth of a yearly one; a pack or a term paid once is not monthly.
        monthly = db.execute("SELECT coalesce(sum(CASE p.interval WHEN 'month' THEN b.amount_cents WHEN 'year' THEN "
                             "b.amount_cents / 12 ELSE 0 END), 0) FROM demo_billing b JOIN memberships m ON "
                             "m.contact_id=b.contact_id AND m.status IN ('active','trial') JOIN plans p ON p.id=m.plan_id "
                             "WHERE b.status='paid'").fetchone()[0]
    live = [r for r in all_rows if (rows[r["id"]]["membership"] or {}).get("status") in gym.LIVE]
    cents = lambda rs: sum(r["amount_cents"] or 0 for r in rs)
    failed = sorted((r for r in live if r["status"] == "failed"), key=lambda r: (r["failed_on"], r["id"]), reverse=True)
    return {"demo": True, "today": on,  # failed payments newest first, the same order as Today
            "paid": sum(1 for r in live if r["status"] == "paid"),
            "failed": failed,
            "monthly_cents": monthly,
            "at_risk_cents": cents(failed),
            "plans": plans,
            "upcoming": [r for r in live if r["status"] == "paid" and r["next_on"] and on <= r["next_on"]][:12],
            "members": len(live)}


def _plans(db):
    """Each plan at its sample price and how many live members are on it."""
    return [dict(r) for r in db.execute(
            "SELECT p.name, p.interval, max(b.amount_cents) AS amount_cents, count(b.contact_id) AS members FROM plans p "
            "LEFT JOIN memberships m ON m.plan_id=p.id AND m.status IN ('active','trial') "
            "LEFT JOIN demo_billing b ON b.contact_id=m.contact_id WHERE p.active=1 GROUP BY p.id ORDER BY p.name") if r["amount_cents"]]


def member(db, prof, cid):
    if not billing_on(prof) or not _table(db):
        return None
    r = db.execute("SELECT * FROM demo_billing WHERE contact_id=?", (cid,)).fetchone()
    return None if r is None else {"status": r["status"], "next_on": r["next_on"], "failed_on": r["failed_on"],
                                   "reason": r["reason"], "link_sent": bool(r["link_sent_at"]),
                                   "classes_left": r["classes_left"], "amount_cents": r["amount_cents"]}


def failed_ids(db, prof):
    """Members with a failed payment: the Members flags and the kiosk's gentle card note."""
    if not billing_on(prof) or not _table(db):
        return set()
    return {r[0] for r in db.execute("SELECT contact_id FROM demo_billing WHERE status='failed'")}


def act(store, prof, person, body):
    """`link`: record that the "update your card" link went out (demo: nothing is sent) and return
    the text the member would get. `paid`: the member updated the card and the retry went through."""
    if not billing_on(prof):
        raise crm_store.CRMError("Billing is not connected yet", 404)
    if not isinstance(body, dict) or body.get("action") not in ("link", "paid"):
        raise crm_store.CRMError("Choose link or paid")
    store.authorize(prof, person, "gym_manage")
    stamp = crm_store.now()
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        contact = store.contact(db, prof, person, body.get("contact_id"))
        row = db.execute("SELECT * FROM demo_billing WHERE contact_id=?", (contact["id"],)).fetchone()
        if row is None or row["status"] != "failed":
            raise crm_store.CRMError("This member has no failed payment", 409)
        if body["action"] == "link":
            db.execute("UPDATE demo_billing SET link_sent_at=?, updated_at=? WHERE contact_id=?", (stamp, stamp, contact["id"]))
        else:
            db.execute("UPDATE demo_billing SET status='paid', failed_on='', reason='', updated_at=? WHERE contact_id=?",
                       (stamp, contact["id"]))
    first = contact["name"].split()[0]
    return {"ok": True, "demo": True,
            "message": CARD_TEXT.format(first=first, gym=prof["name"], what=WHAT.get(row["reason"], "was declined"),
                                        link="(a secure Stripe page)") if body["action"] == "link" else "",
            "note": "Demo: nothing was sent and no card was charged."}


# ---------- the switch guide ----------

def switch(prof):
    guide = (gym.config(prof) or {}).get("switch")
    if not isinstance(guide, dict) or not isinstance(guide.get("steps"), list):
        raise crm_store.CRMError("No switch guide for this gym", 404)
    return {"gym": prof["name"], **guide}


FIRST = ["Adrian", "Bea", "Cyrus", "Delia", "Emil", "Fiona", "Gideon", "Hollis", "Imani", "Jasper", "Kira",
         "Lorenzo", "Marisol", "Nico", "Odette", "Pablo", "Quinn", "Rhea"]
LAST = ["Ashby", "Beltran", "Corwin", "Duvall", "Encarnacion", "Farrow", "Greer", "Huxley", "Ishida", "Jaramillo",
        "Keats", "Lowell", "Marchetti", "Nordin", "Oyelaran", "Prado"]


def sample_export(store, prof):
    """A made-up member export in the gym's current tool's own column names, for the import step.
    The same file every time, so a second import skips every row (the import never doubles)."""
    guide = switch(prof)
    columns = [c for c in (guide.get("export") or {}).get("columns") or [] if isinstance(c, list) and len(c) == 2]
    if not columns:
        raise crm_store.CRMError("No export format for this tool", 404)
    rnd = random.Random(prof["business_id"])
    on = gym.today(prof)
    ladders = gym.ranks(prof)
    with store.connection() as db:
        plans = [r["name"] for r in db.execute("SELECT name FROM plans WHERE active=1 ORDER BY name")]
    rows = []
    for i in range(int((guide.get("export") or {}).get("rows", 10))):
        first, last = FIRST[i % len(FIRST)], rnd.choice(LAST)
        kid = i % 4 == 3 and (len(ladders) > 1 or not ladders)
        ladder = (ladders[1] if kid else ladders[0]) if ladders else {"program": "kids" if kid else "adult", "belts": [{"label": ""}]}
        belt = ladder["belts"][min(len(ladder["belts"]) - 1, int(rnd.expovariate(1.3)))]  # no ranks: no belt
        born = date(on.year - (rnd.randint(7, 12) if kid else rnd.randint(19, 45)), rnd.randint(1, 12), rnd.randint(1, 28))
        values = {"first_name": first, "last_name": last, "name": f"{first} {last}",
                  "email": f"{first}.{last}.import@example.test".lower(), "phone": f"555-03{i:02d}",
                  "birth_date": born.strftime("%m/%d/%Y"), "program": ladder["program"],
                  "plan": rnd.choice([p for p in plans if kid == ("kid" in p.lower())] or plans or [""]),
                  "status": "Active", "started_on": date.fromordinal(on.toordinal() - rnd.randint(30, 900)).strftime("%m/%d/%Y"),
                  "belt": belt["label"], "stripes": str(rnd.randint(0, 3)),
                  "emergency_name": "" if kid else f"{rnd.choice(FIRST)} {last}", "emergency_phone": "" if kid else f"555-04{i:02d}"}
        rows.append([values.get(field, "") for _, field in columns])
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow([h for h, _ in columns])
    w.writerows(rows)
    return out.getvalue(), guide["export"].get("file_name", "members-export.csv")


def seed_billing(store, prof, rnd, failed=5, packs=None, prices=None):
    """Builder only: a billing row per live member, a few of them failed, each at its plan's SAMPLE
    price (`prices`: plan id -> cents; the builder says on screen they are samples, never the gym's).
    A class pack (`packs`: plan id -> classes in the pack) never bills monthly: it shows classes left."""
    packs, prices = packs or {}, prices or {}
    on = gym.today(prof)
    stamp = crm_store.now()
    with store.connection() as db:
        migrate(db)
        live = {r["contact_id"]: r["plan_id"] for r in db.execute(
            "SELECT contact_id, plan_id FROM memberships WHERE status IN ('active','trial') ORDER BY contact_id")}
        adults = [r[0] for r in db.execute("SELECT m.contact_id FROM memberships m JOIN member_details d ON d.contact_id="
                                           "m.contact_id WHERE m.status IN ('active','trial') AND d.guardian_id IS NULL "
                                           "ORDER BY m.contact_id")]  # a card is an adult's: a kid's sits with the parent
        adults = [cid for cid in adults if live.get(cid) not in packs]  # a pack is paid up front
        bad = set(rnd.sample(adults, min(failed, len(adults))))
        reasons = ["Card declined", "Card expired", "Insufficient funds", "Card declined", "Card expired"]
        for i, cid in enumerate(live):
            nxt = on.toordinal() + rnd.randint(1, 30)
            cols = "(contact_id,status,next_on,failed_on,reason,link_sent_at,updated_at,classes_left,amount_cents)"
            cents = prices.get(live[cid])
            if cid in bad:
                when = date.fromordinal(on.toordinal() - rnd.randint(0, 6)).isoformat()
                db.execute(f"INSERT OR REPLACE INTO demo_billing {cols} VALUES (?,?,?,?,?,?,?,?,?)",
                           (cid, "failed", when, when, reasons[i % len(reasons)], "", stamp, None, cents))
            elif live[cid] in packs:
                db.execute(f"INSERT OR REPLACE INTO demo_billing {cols} VALUES (?,?,?,?,?,?,?,?,?)",
                           (cid, "paid", "", "", "", "", stamp, rnd.randint(1, max(1, packs[live[cid]] - 1)), cents))
            else:
                db.execute(f"INSERT OR REPLACE INTO demo_billing {cols} VALUES (?,?,?,?,?,?,?,?,?)",
                           (cid, "paid", date.fromordinal(nxt).isoformat(), "", "", "", stamp, None, cents))
    return len(live), len(bad)

