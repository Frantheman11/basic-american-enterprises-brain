"""Gym billing, P2: Stripe Connect Standard in TEST mode. Design: docs/products/gym-system-p2-design.md.

On only when the profile has a `gym.billing` block (an object). Without it the gym runs exactly as P1:
"billing not connected", never a zero. A demo's `gym.billing: "demo"` (a string) stays the simulated
billing in gym_demo.py; the read functions here send a demo there, so one call site serves all three.

The gym's own Stripe account holds every card, customer and subscription. This database holds Stripe
ids and amounts only: never a card number, expiry or CVC (a test reads every table and page for it).

Writes come from three places, each its own transaction:
- staff: a plan's price (`gym.plan.save`, inside Store.execute), the card link, the money check;
- Stripe: the webhook, signed, replay-guarded by `stripe_events` (event id unique), as `system:stripe`;
- nobody else. The join page only asks Stripe for a Checkout page; the member is written when
  Stripe says the payment went through.

`billing_events` mirrors what Stripe reported (paid, failed, refunded). Stripe is the source: the
nightly money check compares the two and REPORTS a gap. It never writes a billing event.
"""
import base64
import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from . import crm_store, gym, gym_demo, gym_stripe

SCHEMA = 1
ACTOR = "system:stripe"
ENV = re.compile(r"[A-Z][A-Z0-9_]{2,63}")
HANDLED = ("checkout.session.completed", "invoice.paid", "invoice.payment_failed", "charge.refunded",
           "customer.subscription.updated", "customer.subscription.deleted")
SUB_STATUS = {"active": "active", "trialing": "trial", "past_due": "active", "unpaid": "paused",
              "paused": "paused", "canceled": "canceled", "incomplete_expired": "canceled"}
CARD_TEXT = ("Hi {first}, your payment to {gym} did not go through. Update your card on this secure "
             "Stripe page, it takes a minute: {link}")
LINK_DAYS = 7


def config(prof):
    """The billing block, or None. A string (the demo) is not Stripe."""
    cfg = (gym.config(prof) or {}).get("billing")
    return cfg if isinstance(cfg, dict) else None


def require(prof):
    cfg = config(prof)
    if cfg is None:
        raise crm_store.CRMError("Billing is not connected yet", 404)
    return cfg


def env(cfg, field, default):
    name = cfg.get(field, default)
    if not isinstance(name, str) or not ENV.fullmatch(name):
        raise crm_store.CRMError(f"gym.billing.{field} must name an environment variable", 503)
    return os.environ.get(name, "")


def check_startup(prof):
    """server.make_server: a billing block must say test mode, and a key that is set must be a test
    key. Refused before the server binds, so a live key never serves a single request."""
    cfg = config(prof)
    if cfg is None:
        return
    from .identity import StartupRefused
    if cfg.get("mode") != "test":
        raise StartupRefused('gym.billing.mode must be "test" in P2: live billing is not built')
    try:
        key = env(cfg, "secret_env", "GYM_STRIPE_TEST_SECRET")
        if key:
            gym_stripe.check_key(key)
    except (gym_stripe.StripeError, crm_store.CRMError) as e:
        raise StartupRefused(f"gym billing: {e}") from None


def migrate(db):
    gym.migrate(db)
    row = db.execute("SELECT value FROM metadata WHERE key='gym_billing_schema'").fetchone()
    if row and row[0] == str(SCHEMA):
        return
    for statement in TABLES.split(";"):
        if statement.strip():
            db.execute(statement)
    db.execute("INSERT OR REPLACE INTO metadata VALUES ('gym_billing_schema', ?)", (str(SCHEMA),))


TABLES = """
    CREATE TABLE IF NOT EXISTS billing_customers (
        contact_id TEXT PRIMARY KEY REFERENCES contacts(id), stripe_customer_id TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS stripe_events (
        event_id TEXT PRIMARY KEY, type TEXT NOT NULL, account TEXT NOT NULL DEFAULT '',
        result TEXT NOT NULL, received_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS billing_events (
        id TEXT PRIMARY KEY, event_id TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL,
        amount_cents INTEGER NOT NULL, currency TEXT NOT NULL, stripe_customer_id TEXT NOT NULL DEFAULT '',
        stripe_subscription_id TEXT NOT NULL DEFAULT '', stripe_invoice_id TEXT NOT NULL DEFAULT '',
        stripe_charge_id TEXT NOT NULL DEFAULT '', stripe_payment_intent_id TEXT NOT NULL DEFAULT '',
        attempt INTEGER NOT NULL DEFAULT 0, next_attempt TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
        occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL, UNIQUE(kind, ref));
    CREATE TABLE IF NOT EXISTS card_links (
        contact_id TEXT PRIMARY KEY REFERENCES contacts(id), prepared_at TEXT NOT NULL, prepared_by TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS money_checks (
        id TEXT PRIMARY KEY, day TEXT NOT NULL, at TEXT NOT NULL, equal INTEGER NOT NULL, result TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS billing_events_sub ON billing_events(stripe_subscription_id, occurred_at);
    CREATE INDEX IF NOT EXISTS billing_events_day ON billing_events(occurred_at)
"""


def _tables(db):
    return db.execute("SELECT 1 FROM metadata WHERE key='gym_billing_schema'").fetchone() is not None


def account(db, cfg):
    """The gym's connected account: the profile pins it once known; onboarding stores it in the
    database. Two different ids is a stop, never a guess."""
    pinned, stored = cfg.get("account_id") or "", ""
    if db is not None:
        row = db.execute("SELECT value FROM metadata WHERE key='gym_stripe_account'").fetchone()
        stored = row[0] if row else ""
    if pinned and stored and pinned != stored:
        raise crm_store.CRMError("The Stripe account in the profile is not the one connected here. Check it.", 503)
    return pinned or stored


def client(prof, db=None, with_account=True):
    cfg = require(prof)
    try:
        acct = account(db, cfg) if with_account else None
        if with_account and not acct:
            raise crm_store.CRMError("Connect the gym's Stripe account first", 409)
        return gym_stripe.Client(env(cfg, "secret_env", "GYM_STRIPE_TEST_SECRET"), acct)
    except gym_stripe.StripeError as e:
        raise crm_store.CRMError(str(e), e.status) from None


def connected(prof, db):
    cfg = config(prof)
    if cfg is None:
        return False
    try:
        return bool(account(db, cfg)) and bool(gym_stripe.check_key(env(cfg, "secret_env", "GYM_STRIPE_TEST_SECRET")))
    except (gym_stripe.StripeError, crm_store.CRMError):
        return False


def _stripe(fn):
    try:
        return fn()
    except gym_stripe.StripeError as e:
        raise crm_store.CRMError(f"Stripe: {e}", 502 if e.status < 500 else e.status) from None


def stamp_of(seconds):
    return datetime.fromtimestamp(int(seconds), timezone.utc).isoformat()


# ------------------------------------------------------------------ plans to Prices

def price(db, prof, old, plan, amount):
    """`gym.plan.save` with `amount_cents`: the Price on the gym's account. A Price never changes
    amount, so a new amount is a new Price and the old one is archived. Members already on the old
    one keep it. The idempotency keys come from the plan id and its revision, so a retried save
    returns the same Price instead of a second one. `old` is the row before the save (or None);
    `plan` the row after it."""
    if amount is None or amount == 0:
        if plan["stripe_price_id"]:
            c = client(prof, db)
            _stripe(lambda: c.post(gym_stripe.path("prices", plan["stripe_price_id"]), {"active": False},
                                   f"gym-price-off-{plan['stripe_price_id']}"))
        db.execute("UPDATE plans SET amount_cents=NULL, stripe_price_id=NULL WHERE id=?", (plan["id"],))
        return
    if config(prof) is None:
        raise crm_store.CRMError("Connect Stripe billing before giving a plan a price", 409)
    gym.whole(amount, "Amount in cents", 50, 10_000_000)
    if plan["interval"] == "none":
        raise crm_store.CRMError("A plan with no billing interval has no price")
    c = client(prof, db)
    product = "gym_plan_" + plan["id"].replace("-", "")
    rev = plan["revision"]
    meta = {"gym_plan_id": plan["id"], "engine": "cta-gym"}
    try:  # the product carries the plan's name; create it the first time
        c.post(gym_stripe.path("products", product), {"name": plan["name"], "active": True}, f"gym-product-{product}-r{rev}")
    except gym_stripe.StripeError as e:
        if e.status != 404:
            raise crm_store.CRMError(f"Stripe: {e}", 502) from None
        _stripe(lambda: c.post("/v1/products", {"id": product, "name": plan["name"], "metadata": meta}, f"gym-product-new-{product}"))
    if old and old["stripe_price_id"] and old["amount_cents"] == amount and old["interval"] == plan["interval"]:
        return  # the same price: only the product's name may have changed
    params = {"product": product, "currency": (config(prof).get("currency") or "usd").lower(), "unit_amount": amount,
              "metadata": meta}
    if plan["interval"] in ("month", "year"):
        params["recurring"] = {"interval": plan["interval"]}
    new = _stripe(lambda: c.post("/v1/prices", params, f"gym-price-{plan['id']}-r{rev}"))
    if plan["stripe_price_id"] and plan["stripe_price_id"] != new["id"]:
        old = plan["stripe_price_id"]
        _stripe(lambda: c.post(gym_stripe.path("prices", old), {"active": False}, f"gym-price-off-{old}"))
    db.execute("UPDATE plans SET amount_cents=?, stripe_price_id=? WHERE id=?", (amount, new["id"], plan["id"]))


# ------------------------------------------------------------------ the join page

JOIN_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


def plans_for_sale(store, prof):
    with store.connection() as db:
        db.execute("BEGIN")
        migrate(db)
        return [dict(r) for r in db.execute(
            "SELECT id, name, interval, program, amount_cents FROM plans WHERE active=1 AND stripe_price_id IS NOT NULL "
            "AND amount_cents > 0 ORDER BY amount_cents DESC, name")]


def checkout(store, prof, form, base):
    """A Stripe Checkout page for one plan. Stripe hosts the card field; this returns its URL."""
    require(prof)
    plan_id = crm_store.text(form.get("plan_id"), "Plan", 80, True)
    name = crm_store.text(form.get("name"), "Your name", 120, True)
    email = crm_store.text(form.get("email"), "Email", 254, True)
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise crm_store.CRMError("Enter a valid email address")
    phone = crm_store.text(form.get("phone", ""), "Phone", 60)
    join_id = form.get("join_id") or ""
    if not JOIN_ID.fullmatch(join_id):
        raise crm_store.CRMError("Reload the page and try again")
    with store.connection() as db:
        db.execute("BEGIN")
        migrate(db)
        plan = db.execute("SELECT * FROM plans WHERE id=? AND active=1 AND stripe_price_id IS NOT NULL", (plan_id,)).fetchone()
        if not plan:
            raise crm_store.CRMError("That plan is not open for online sign-up", 404)
        c = client(prof, db)
    recurring = plan["interval"] in ("month", "year")
    meta = {"gym_plan_id": plan["id"], "member_name": name, "member_phone": phone, "join_id": join_id, "engine": "cta-gym"}
    params = {"mode": "subscription" if recurring else "payment",
              "line_items": [{"price": plan["stripe_price_id"], "quantity": 1}],
              "success_url": base + "join/thanks?session={CHECKOUT_SESSION_ID}", "cancel_url": base + "join?canceled=1",
              "customer_email": email, "client_reference_id": join_id, "metadata": meta}
    if recurring:
        params["subscription_data"] = {"metadata": meta}
    else:
        params["customer_creation"] = "always"
    session = _stripe(lambda: c.post("/v1/checkout/sessions", params, f"gym-join-{join_id}"))
    url = session.get("url") or ""
    if not url.startswith(("https://checkout.stripe.com/", c.api + "/")):
        raise crm_store.CRMError("Stripe did not return a checkout page", 502)
    return url


# ------------------------------------------------------------------ webhooks

def webhook(store, prof, payload, header):
    """One Stripe event. Signature first; then, in one transaction, the replay guard and the change.
    The same event twice writes once. Returns a small JSON-able result; raises CRMError(400) on a
    bad signature, so Stripe retries nothing that was forged."""
    cfg = require(prof)
    try:
        event = gym_stripe.verify(payload, header, env(cfg, "webhook_secret_env", "GYM_STRIPE_WEBHOOK_SECRET"))
    except gym_stripe.StripeError as e:
        raise crm_store.CRMError(str(e), e.status) from None
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        migrate(db)
        if db.execute("SELECT 1 FROM stripe_events WHERE event_id=?", (event["id"],)).fetchone():
            return {"ok": True, "duplicate": True}
        acct = account(db, cfg)
        if event.get("livemode") is not False:
            result = {"ignored": "live mode: P2 is test mode only"}
        elif not acct or event.get("account") != acct:
            result = {"ignored": "not this gym's Stripe account"}
        elif event["type"] not in HANDLED:
            result = {"ignored": "not used"}
        else:
            result = _apply(store, db, prof, cfg, event)
        db.execute("INSERT INTO stripe_events VALUES (?,?,?,?,?)", (event["id"], event["type"], event.get("account") or "",
                                                                     json.dumps(result), crm_store.now()))
        return {"ok": True, **result}


def _record(db, event, kind, ref, obj, amount, **extra):
    """One billing event, once per (kind, ref). Stripe's own time, never ours."""
    if db.execute("SELECT 1 FROM billing_events WHERE kind=? AND ref=?", (kind, ref)).fetchone():
        return False
    row = {"stripe_customer_id": obj.get("customer") or "", "stripe_subscription_id": obj.get("subscription") or "",
           "stripe_invoice_id": "", "stripe_charge_id": "", "stripe_payment_intent_id": obj.get("payment_intent") or "",
           "attempt": 0, "next_attempt": "", "reason": ""} | extra
    db.execute("INSERT INTO billing_events (id,event_id,kind,ref,amount_cents,currency,stripe_customer_id,stripe_subscription_id,"
               "stripe_invoice_id,stripe_charge_id,stripe_payment_intent_id,attempt,next_attempt,reason,occurred_at,recorded_at) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (str(uuid4()), event["id"], kind, ref, int(amount or 0), (obj.get("currency") or "usd").lower(),
                row["stripe_customer_id"], row["stripe_subscription_id"], row["stripe_invoice_id"], row["stripe_charge_id"],
                row["stripe_payment_intent_id"], row["attempt"], row["next_attempt"], row["reason"][:300],
                row.get("occurred_at") or stamp_of(event.get("created") or 0), crm_store.now()))
    return True


def _apply(store, db, prof, cfg, event):
    from . import crm_events
    obj, kind = (event.get("data") or {}).get("object") or {}, event["type"]
    if kind == "checkout.session.completed":
        return _joined(store, db, prof, cfg, event, obj)
    if kind == "invoice.paid":
        if not obj.get("amount_paid"):
            return {"ok": True, "zero": True}
        ref = obj.get("charge") or obj.get("payment_intent") or obj["id"]
        paid_at = (obj.get("status_transitions") or {}).get("paid_at")
        wrote = _record(db, event, "paid", ref, obj, obj["amount_paid"], stripe_invoice_id=obj["id"],
                        stripe_charge_id=obj.get("charge") or "", **({"occurred_at": stamp_of(paid_at)} if paid_at else {}))
        return {"recorded": wrote}
    if kind == "invoice.payment_failed":
        attempt = int(obj.get("attempt_count") or 1)
        ref = obj.get("charge") or f"{obj['id']}:{attempt}"
        nxt = obj.get("next_payment_attempt")
        wrote = _record(db, event, "failed", ref, obj, obj.get("amount_due") or 0, stripe_invoice_id=obj["id"],
                        stripe_charge_id=obj.get("charge") or "", attempt=attempt,
                        next_attempt=stamp_of(nxt) if nxt else "", reason=_reason(obj))
        return {"recorded": wrote}
    if kind == "charge.refunded":
        before = db.execute("SELECT coalesce(sum(amount_cents),0) FROM billing_events WHERE kind='refunded' AND stripe_charge_id=?",
                            (obj["id"],)).fetchone()[0]
        delta = int(obj.get("amount_refunded") or 0) - before
        if delta <= 0:
            return {"recorded": False}
        sub = db.execute("SELECT stripe_subscription_id FROM billing_events WHERE stripe_charge_id=? AND kind='paid'",
                         (obj["id"],)).fetchone()
        wrote = _record(db, event, "refunded", f"{obj['id']}:{obj.get('amount_refunded')}", obj, delta,
                        stripe_charge_id=obj["id"], stripe_invoice_id=obj.get("invoice") or "",
                        stripe_subscription_id=sub[0] if sub else "")
        return {"recorded": wrote}
    # customer.subscription.updated / deleted: the membership follows Stripe's status
    status = "canceled" if kind.endswith("deleted") else SUB_STATUS.get(obj.get("status"))
    row = db.execute("SELECT * FROM memberships WHERE stripe_subscription_id=?", (obj.get("id"),)).fetchone()
    if not row or not status or row["status"] == status:
        return {"ok": True, "changed": False}
    ends = gym.today(prof).isoformat() if status == "canceled" else row["ends_on"]
    db.execute("UPDATE memberships SET status=?, ends_on=?, revision=revision+1, updated_at=? WHERE id=?",
               (status, ends, crm_store.now(), row["id"]))
    crm_events.record(db, "stripe:" + event["id"], ACTOR, "gym.membership.save",
                      {"status": status, "stripe_subscription_id": obj.get("id")}, {"ok": True, "id": row["id"]})
    return {"ok": True, "changed": True}


def _reason(invoice):
    err = invoice.get("last_finalization_error") or {}
    return err.get("message") or "The card was declined"


def _owner(prof, cfg):
    from . import access, identity
    wanted = cfg.get("join_owner")
    for m in (prof.get("people") or {}).get("members", []):
        p = identity.person(prof, m["id"])
        if (wanted and m["id"] == wanted) or (not wanted and access.sees(prof, p) == "all"):
            return p
    raise crm_store.CRMError("gym.billing.join_owner must name a team member who sees all records", 503)


def _joined(store, db, prof, cfg, event, s):
    """Checkout finished: the member, their details and their membership, written from what Stripe
    says. A member who already exists (same Stripe customer, else same email) is reused."""
    from . import crm_events
    meta = s.get("metadata") or {}
    plan = db.execute("SELECT * FROM plans WHERE id=?", (meta.get("gym_plan_id") or "",)).fetchone()
    if not plan or s.get("payment_status") not in ("paid", "no_payment_required"):
        return {"ignored": "not a paid gym join"}
    owner, key = _owner(prof, cfg), "stripe:" + event["id"]
    details = s.get("customer_details") or {}
    email = (details.get("email") or s.get("customer_email") or "").strip().lower()
    known = db.execute("SELECT contact_id FROM billing_customers WHERE stripe_customer_id=?", (s.get("customer") or "",)).fetchone()
    if not known and email:
        known = db.execute("SELECT id FROM contacts WHERE lower(email)=? AND kind='person' AND deleted_at IS NULL "
                           "ORDER BY created_at LIMIT 1", (email,)).fetchone()
    if known:
        cid = known[0]
    else:
        data = {"name": meta.get("member_name") or details.get("name") or email, "kind": "person", "email": email,
                "phone": meta.get("member_phone") or details.get("phone") or "", "lifecycle": "customer",
                "confirm_duplicate": True, "source_channel": "Online join"}
        result = store._change(db, prof, owner, "contact.create", data)
        crm_events.record(db, key, ACTOR, "contact.create", data, result)
        cid = result["id"]
    on = datetime.fromtimestamp(int(event.get("created") or 0), timezone.utc).astimezone(gym.zone(prof)).date().isoformat()
    if not db.execute("SELECT 1 FROM member_details WHERE contact_id=?", (cid,)).fetchone():
        gym.change(store, db, prof, owner, "gym.member.save",
                   {"contact_id": cid, "revision": 0, "joined_on": on, "program": plan["program"]}, crm_store.now())
    if s.get("customer"):
        db.execute("INSERT OR IGNORE INTO billing_customers VALUES (?,?,?)", (cid, s["customer"], crm_store.now()))
    sub = s.get("subscription") or None
    had = sub and db.execute("SELECT id FROM memberships WHERE stripe_subscription_id=?", (sub,)).fetchone()
    if not had:
        mid, now = str(uuid4()), crm_store.now()
        db.execute("INSERT INTO memberships (id,contact_id,plan_id,status,started_on,ends_on,stripe_subscription_id,"
                   "created_at,updated_at) VALUES (?,?,?,'active',?,'',?,?,?)", (mid, cid, plan["id"], on, sub, now, now))
        db.execute("UPDATE contacts SET lifecycle='customer', updated_at=? WHERE id=? AND lifecycle!='customer'", (now, cid))
        crm_events.record(db, key, ACTOR, "gym.membership.save", {"plan_id": plan["id"], "stripe_subscription_id": sub},
                          {"ok": True, "id": mid})
    if s.get("mode") == "payment" and s.get("payment_intent"):  # a one-off plan: the session is the payment
        _record(db, event, "paid", s["payment_intent"], s, s.get("amount_total") or 0)
    return {"ok": True, "member": cid}


# ------------------------------------------------------------------ what staff see

def _failed(db):
    """{subscription: the failed event} where the newest paid-or-failed result is a failure."""
    last = {}
    for r in db.execute("SELECT * FROM billing_events WHERE kind IN ('paid','failed') AND stripe_subscription_id!='' "
                        "ORDER BY occurred_at, recorded_at"):
        last[r["stripe_subscription_id"]] = r
    return {s: r for s, r in last.items() if r["kind"] == "failed"}


def _failed_rows(db, rows):
    out = []
    links = {r[0]: r[1] for r in db.execute("SELECT contact_id, prepared_at FROM card_links")}
    for sub, f in _failed(db).items():
        m = db.execute("SELECT contact_id FROM memberships WHERE stripe_subscription_id=?", (sub,)).fetchone()
        if not m or m[0] not in rows:
            continue
        r = rows[m[0]]
        out.append({"id": r["id"], "name": r["name"], "phone": r["phone"], "plan": (r["membership"] or {}).get("plan", ""),
                    "status": "failed", "failed_on": f["occurred_at"][:10], "reason": f["reason"], "attempt": f["attempt"],
                    "next_attempt": f["next_attempt"][:10], "amount_cents": f["amount_cents"],
                    "link_sent": bool(links.get(r["id"]) and links[r["id"]] >= f["recorded_at"])})
    return sorted(out, key=lambda x: (x["failed_on"], x["id"]), reverse=True)


def today(db, prof, rows):
    """The Today card: demo, Stripe, or "not connected" (never a zero)."""
    if gym_demo.billing_on(prof) or config(prof) is None:
        return gym_demo.today(db, prof, rows)
    if not connected(prof, db) or not _tables(db):
        return {"connected": False, "text": "Billing is set up but Stripe is not connected yet. Failed payments show "
                                            "here once the gym's Stripe account is connected."}
    return {"connected": "stripe", "failed": _failed_rows(db, rows), "text": "Stripe test mode: test cards only."}


def failed_ids(db, prof):
    if gym_demo.billing_on(prof) or config(prof) is None:
        return gym_demo.failed_ids(db, prof)
    if not _tables(db):
        return set()
    subs = list(_failed(db))
    return {r[0] for s in subs for r in db.execute("SELECT contact_id FROM memberships WHERE stripe_subscription_id=?", (s,))}


def member(db, prof, cid):
    if gym_demo.billing_on(prof) or config(prof) is None:
        return gym_demo.member(db, prof, cid)
    if not _tables(db):
        return None
    events = [dict(r) for r in db.execute(
        "SELECT b.kind, b.amount_cents, b.occurred_at, b.reason FROM billing_events b JOIN memberships m "
        "ON m.stripe_subscription_id=b.stripe_subscription_id AND b.stripe_subscription_id!='' WHERE m.contact_id=? "
        "UNION ALL SELECT b.kind, b.amount_cents, b.occurred_at, b.reason FROM billing_events b JOIN billing_customers c "
        "ON c.stripe_customer_id=b.stripe_customer_id WHERE c.contact_id=? AND b.stripe_subscription_id='' "
        "ORDER BY 3 DESC LIMIT 12", (cid, cid))]
    if not events and not db.execute("SELECT 1 FROM billing_customers WHERE contact_id=?", (cid,)).fetchone():
        return None
    last = next((e for e in events if e["kind"] in ("paid", "failed")), None)
    return {"stripe": True, "status": (last or {}).get("kind", "none"), "failed_on": (last or {}).get("occurred_at", "")[:10],
            "reason": (last or {}).get("reason", ""), "history": events, "next_on": "", "classes_left": None,
            "link_sent": False}


def summary(store, prof, rows_of):
    if gym_demo.billing_on(prof) or config(prof) is None:
        return gym_demo.summary(store, prof, rows_of)
    with store.connection() as db:
        db.execute("BEGIN")
        rows = rows_of(db)
        migrate(db)
        cfg = require(prof)
        is_on = connected(prof, db)
        failed = _failed_rows(db, rows) if is_on else []
        plans = [dict(r) for r in db.execute("SELECT id, name, interval, amount_cents, stripe_price_id IS NOT NULL AS priced, "
                                             "active, revision FROM plans ORDER BY name")]
        since = (gym.today(prof) - timedelta(days=30)).isoformat()
        month = {k: v for k, v in db.execute("SELECT kind, coalesce(sum(amount_cents),0) FROM billing_events WHERE occurred_at>=? "
                                             "GROUP BY kind", (since,))}
        check = db.execute("SELECT day, at, equal, result FROM money_checks ORDER BY at DESC LIMIT 1").fetchone()
        acct = account(db, cfg)
    return {"stripe": True, "connected": is_on, "account": acct, "can_connect": bool(env(cfg, "client_id_env", "GYM_STRIPE_CLIENT_ID")),
            "test_mode": True, "failed": failed, "plans": plans, "upcoming": [],
            "last_30_days": {"paid_cents": month.get("paid", 0), "refunded_cents": month.get("refunded", 0),
                             "failed_cents": month.get("failed", 0)},
            "money_check": ({"day": check["day"], "at": check["at"], "equal": bool(check["equal"]),
                             "gaps": len(json.loads(check["result"]).get("gaps", []))} if check else None),
            "members": len([r for r in rows.values() if (r["membership"] or {}).get("status") in gym.LIVE])}


# ------------------------------------------------------------------ the card link

def _secret(cfg):
    s = env(cfg, "link_secret_env", "GYM_LINK_SECRET")
    if len(s) < 16:
        raise crm_store.CRMError("The card link secret is not set (16 characters or more)", 503)
    return s.encode()


def token(cfg, *parts, days=LINK_DAYS):
    exp = int((datetime.fromisoformat(crm_store.now()) + timedelta(days=days)).timestamp())
    body = "|".join([*parts, str(exp)])
    mac = hmac.new(_secret(cfg), body.encode(), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{body}|{mac}".encode()).decode().rstrip("=")


def untoken(cfg, value, kind):
    try:
        raw = base64.urlsafe_b64decode((value or "") + "=" * (-len(value or "") % 4)).decode()
        *parts, exp, mac = raw.split("|")
    except (ValueError, UnicodeDecodeError):
        raise crm_store.CRMError("This link is not valid", 400) from None
    good = hmac.new(_secret(cfg), "|".join([*parts, exp]).encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(mac, good) or not parts or parts[0] != kind:
        raise crm_store.CRMError("This link is not valid", 400)
    if not exp.isdigit() or int(exp) < datetime.fromisoformat(crm_store.now()).timestamp():
        raise crm_store.CRMError("This link has expired. Ask the gym for a new one.", 410)
    return parts[1:]


def act(store, prof, person, body, base):
    """Staff actions. `link`: the member's "update your card" text with a 7-day link, ready to copy
    (P2 sends nothing). `check`: the money check now. A demo goes to gym_demo.act."""
    if gym_demo.billing_on(prof) or config(prof) is None:
        return gym_demo.act(store, prof, person, body)
    cfg = require(prof)
    if not isinstance(body, dict) or body.get("action") not in ("link", "check"):
        raise crm_store.CRMError("Choose link or check")
    store.authorize(prof, person, "gym_manage")
    if body["action"] == "check":
        from . import gym_money
        return gym_money.money_check(store, prof, body.get("day"))
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        migrate(db)
        contact = store.contact(db, prof, person, body.get("contact_id"))
        cust = db.execute("SELECT stripe_customer_id FROM billing_customers WHERE contact_id=?", (contact["id"],)).fetchone()
        if not cust:
            raise crm_store.CRMError("This member has no Stripe customer yet", 409)
        link = base + "billing/card?t=" + token(cfg, "card", contact["id"], cust[0])
        db.execute("INSERT OR REPLACE INTO card_links VALUES (?,?,?)", (contact["id"], crm_store.now(), "human:" + person.id))
    first = contact["name"].split()[0]
    return {"ok": True, "url": link, "message": CARD_TEXT.format(first=first, gym=prof["name"], link=link),
            "note": "Ready to copy. P2 sends nothing: paste it into your own text or email."}


def portal(store, prof, value, base):
    """The member opened their card link: a fresh Stripe customer portal page (those last minutes,
    which is why the text carries our link and not Stripe's)."""
    cfg = require(prof)
    cid, cust = untoken(cfg, value, "card")
    with store.connection() as db:
        db.execute("BEGIN")
        migrate(db)
        if not db.execute("SELECT 1 FROM billing_customers WHERE contact_id=? AND stripe_customer_id=?", (cid, cust)).fetchone():
            raise crm_store.CRMError("This link is not valid any more. Ask the gym for a new one.", 410)
        c = client(prof, db)
    session = _stripe(lambda: c.post("/v1/billing_portal/sessions", {"customer": cust, "return_url": base + "join/thanks?card=1"},
                                     None))
    return session["url"]


# ------------------------------------------------------------------ Connect onboarding

def connect_url(store, prof, person, base):
    cfg = require(prof)
    store.authorize(prof, person, "gym_manage")
    client_id = env(cfg, "client_id_env", "GYM_STRIPE_CLIENT_ID")
    if not re.fullmatch(r"ca_[A-Za-z0-9]+", client_id):
        raise crm_store.CRMError("The platform's Connect client id is not set", 503)
    c = client(prof, None, with_account=False)
    return c.authorize_url(client_id, token(cfg, "connect", person.id, days=1), base + "gym/billing/connected")


def connected_back(store, prof, person, query, base):
    cfg = require(prof)
    store.authorize(prof, person, "gym_manage")
    if query.get("error"):
        raise crm_store.CRMError("Stripe did not connect: " + crm_store.text(query.get("error_description") or query["error"], "Error", 200), 400)
    (who,) = untoken(cfg, query.get("state") or "", "connect")
    if who != person.id:
        raise crm_store.CRMError("This connect link belongs to another sign-in", 403)
    code = crm_store.text(query.get("code"), "Code", 200, True)
    acct = _stripe(lambda: client(prof, None, with_account=False).oauth_token(code))
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        migrate(db)
        pinned = cfg.get("account_id")
        if pinned and pinned != acct:
            raise crm_store.CRMError("That Stripe account is not the one the profile names", 409)
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('gym_stripe_account', ?)", (acct,))
        from . import crm_events
        crm_events.record(db, "connect:" + acct, "human:" + person.id, "gym.billing.connect", {"account": acct},
                          {"ok": True, "id": acct})
    return acct
