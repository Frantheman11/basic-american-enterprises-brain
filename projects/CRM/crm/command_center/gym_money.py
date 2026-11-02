"""Gym billing, P2: the nightly money check. Design: docs/products/gym-system-p2-design.md.

Every Stripe charge on the gym's account in one local day is compared with every billing record
(gym_billing.py). A gap is REPORTED: saved in `money_checks`, returned, and the command exits 1.
It is never fixed here. Stripe is the source, and a missing record means a webhook that did not
arrive, which is the thing to look at.

    python -m command_center.gym_money --business ID --root DIR --crm-db FILE [--day 2026-11-02]
"""
import json
import sys
from datetime import date, datetime, time as dtime, timedelta, timezone
from uuid import uuid4

from . import crm_store, gym
from .gym_billing import _stripe, client, migrate

def _day_bounds(prof, day):
    tz = gym.zone(prof)
    d = date.fromisoformat(gym.day(day, "Day")) if day else gym.today(prof) - timedelta(days=1)
    start = datetime.combine(d, dtime(0, 0), tz).astimezone(timezone.utc)
    end = datetime.combine(d + timedelta(days=1), dtime(0, 0), tz).astimezone(timezone.utc)
    return d, start, end


def money_check(store, prof, day=None):
    """One local day (default yesterday in the gym's zone): Stripe's charges against our records."""
    d, start, end = _day_bounds(prof, day)
    with store.connection() as db:
        db.execute("BEGIN")
        migrate(db)
        c = client(prof, db)
    pad = 3600  # a charge and its invoice can sit a few seconds apart across midnight
    charges = _stripe(lambda: c.listed("/v1/charges", {"created": {"gte": int(start.timestamp()) - pad,
                                                                    "lt": int(end.timestamp()) + pad}}))
    in_day = [ch for ch in charges if start.timestamp() <= ch["created"] < end.timestamp()]
    with store.connection() as db:
        db.execute("BEGIN")
        ours = [dict(r) for r in db.execute("SELECT * FROM billing_events")]
    paid = [o for o in ours if o["kind"] == "paid"]
    by_charge = {o["stripe_charge_id"]: o for o in paid if o["stripe_charge_id"]}
    by_pi = {o["stripe_payment_intent_id"]: o for o in paid if o["stripe_payment_intent_id"]}
    by_invoice = {o["stripe_invoice_id"]: o for o in paid if o["stripe_invoice_id"]}
    gaps, matched = [], set()
    stripe_tot = {"paid_cents": 0, "paid": 0, "refunded_cents": 0, "failed": 0}
    for ch in in_day:
        if ch.get("status") == "failed":
            if not ch.get("invoice"):  # a declined try on the Checkout page: the member tries another card
                continue
            stripe_tot["failed"] += 1
            if not any(o["kind"] == "failed" and o["stripe_charge_id"] == ch["id"] for o in ours):
                gaps.append({"kind": "failed_charge_not_recorded", "charge": ch["id"]})
            continue
        if ch.get("status") != "succeeded" or not ch.get("captured", True):
            continue
        amount = ch.get("amount_captured", ch["amount"])
        stripe_tot["paid"] += 1
        stripe_tot["paid_cents"] += amount
        stripe_tot["refunded_cents"] += ch.get("amount_refunded") or 0
        o = by_charge.get(ch["id"]) or by_pi.get(ch.get("payment_intent") or "-") or by_invoice.get(ch.get("invoice") or "-")
        if not o:
            gaps.append({"kind": "payment_not_recorded", "charge": ch["id"], "amount_cents": amount})
            continue
        matched.add(o["id"])
        if o["amount_cents"] != amount:
            gaps.append({"kind": "amount_differs", "charge": ch["id"], "stripe": amount, "ours": o["amount_cents"]})
        refunded = sum(r["amount_cents"] for r in ours if r["kind"] == "refunded" and r["stripe_charge_id"] == ch["id"])
        if refunded != (ch.get("amount_refunded") or 0):
            gaps.append({"kind": "refund_differs", "charge": ch["id"], "stripe": ch.get("amount_refunded") or 0, "ours": refunded})
    known = {ch["id"] for ch in charges} | {ch.get("payment_intent") for ch in charges} | {ch.get("invoice") for ch in charges}
    s, e = start.isoformat(), end.isoformat()
    ours_day = [o for o in paid if s <= o["occurred_at"] < e]
    for o in ours_day:
        if o["id"] not in matched and not ({o["stripe_charge_id"], o["stripe_payment_intent_id"], o["stripe_invoice_id"]} - {""}) & known:
            gaps.append({"kind": "recorded_but_not_in_stripe", "ref": o["ref"], "amount_cents": o["amount_cents"]})
    ours_tot = {"paid_cents": sum(o["amount_cents"] for o in paid if o["id"] in matched),
                "paid": len(matched),
                "refunded_cents": sum(r["amount_cents"] for r in ours if r["kind"] == "refunded" and
                                      r["stripe_charge_id"] in {ch["id"] for ch in in_day}),
                "failed": sum(1 for o in ours if o["kind"] == "failed" and o["stripe_charge_id"] in {ch["id"] for ch in in_day})}
    result = {"day": d.isoformat(), "account": c.account, "stripe": stripe_tot, "ours": ours_tot, "gaps": gaps,
              "equal": not gaps and stripe_tot == ours_tot}
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO money_checks VALUES (?,?,?,?,?)", (str(uuid4()), d.isoformat(), crm_store.now(),
                                                                   int(result["equal"]), json.dumps(result)))
    return result


def main(argv=None):
    """The nightly job: python -m command_center.gym_money --business ID --root DIR --crm-db FILE [--day D]"""
    import argparse
    from . import profiles
    ap = argparse.ArgumentParser(description="Gym billing: the nightly money check (Stripe test mode).")
    ap.add_argument("--business", required=True)
    ap.add_argument("--root", default=".")
    ap.add_argument("--crm-db", required=True)
    ap.add_argument("--day", help="local date, default yesterday in the gym's zone")
    a = ap.parse_args(argv)
    prof = profiles.load(a.business, a.root)
    result = money_check(crm_store.Store(a.crm_db, a.business), prof, a.day)
    print(json.dumps(result, indent=1))
    return 0 if result["equal"] else 1


if __name__ == "__main__":
    sys.exit(main())
