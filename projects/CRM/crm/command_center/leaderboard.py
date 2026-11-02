"""The team leaderboard: who is winning, and who is doing the work that leads to winning.

Sam's rules (docs/crm/crm-design-v1.md §12.6 and §12.7, 2026-09-23):
- The whole team sees it, from day one.
- A win is credited to whoever owned the client at the moment the deal was won.
- It counts the date a deal was actually won. Never a forecast close date.
- With no won deals it says "No closed deals recorded yet", never a column of zeros.

Fixed after the sales rep review (2026-09-27):
- **Touches** = every recorded outcome plus every call, meeting or email logged. One number,
  so a rep who records outcomes is not shown "0 calls".
- **Interested** counts companies, not events: Interested then Quoted is one company.
- Who is ranked comes from the profile (`leaderboard.roles`, CTA: reps; plus
  `leaderboard.members`, CTA: Sam, who also sells, 2026-09-28). Everyone still sees the board. Somebody with nothing yet in the period is listed, never ranked.

Two halves, read from the CRM database only:
- **Results**: deals won and their value (`deals.won_at`, `deals.won_by`, schema 6).
- **Activity**: leads taken and outcomes recorded (the audit trail of research.* commands),
  and calls, meetings and emails logged (activities).

A figure links to its record only for someone who may open that record. A rep sees every
rep's counts, and only their own deals by name.
"""
import json
from datetime import timedelta, timezone

from . import access

PERIODS = ("week", "month", "all")
CONVERSATIONS = ("call", "meeting", "email")


class LeaderboardUnavailable(Exception):
    pass


def settings(prof):
    spec = (prof.get("crm") or {}).get("leaderboard")
    return spec if isinstance(spec, dict) else None


def period_start(period, at, tz_name):
    """UTC start of this week (Monday) or month, in the business's own time zone."""
    if period == "all":
        return None
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name or "UTC")
    except Exception:  # noqa: BLE001 - a bad zone name must not take the board down
        tz = timezone.utc
    local = at.astimezone(tz)
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start = day - timedelta(days=day.weekday()) if period == "week" else day.replace(day=1)
    return start.astimezone(timezone.utc).isoformat(timespec="microseconds")


def build(store, prof, person, period, at):
    """The board for one period. `at` is now, passed in so the tests can move time."""
    spec = settings(prof)
    if spec is None:
        raise LeaderboardUnavailable("The leaderboard is not switched on for this business")
    if period not in PERIODS:
        period = "month"
    since = period_start(period, at, spec.get("timezone"))
    currency = (prof.get("crm") or {}).get("currency", "USD")
    won_stages = {s["id"] for s in store.stages(prof) if s["outcome"] == "won"}
    everyone = access.sees(prof, person) == "all"
    roles = prof.get("roles") or {}
    # On the board: everyone whose role can work records. A read-only role is not competing.
    # `members` adds named people outside those roles who also sell (CTA: Sam, 2026-09-28).
    ranked = spec.get("roles") if isinstance(spec.get("roles"), list) else None
    also = spec.get("members") if isinstance(spec.get("members"), list) else []
    people = [m for m in (prof.get("people") or {}).get("members", [])
              if "crm_edit" in ((roles.get(m.get("role")) or {}).get("can") or [])
              and (ranked is None or m.get("role") in ranked or m.get("id") in also)]
    rows = {m["id"]: {"id": m["id"], "name": m.get("name", m["id"]), "you": m["id"] == person.id,
                      "won_count": 0, "won_cents": 0, "deals": [], "leads_taken": 0,
                      "touches": 0, "interested": 0, "_interested": set()} for m in people}

    def inside(stamp):
        return stamp is not None and (since is None or stamp >= since)

    with store.connection() as db:
        db.execute("BEGIN")
        deals = [dict(r) for r in db.execute(
            "SELECT d.id, d.contact_id, d.title, d.stage, d.amount_cents, d.currency, d.updated_at, "
            "d.won_at, d.won_by, c.owner, c.name AS client FROM deals d "
            "JOIN contacts c ON c.id = d.contact_id WHERE d.deleted_at IS NULL AND c.deleted_at IS NULL")]
        audit = [dict(r) for r in db.execute(
            "SELECT actor, action, payload, created_at FROM audit "
            "WHERE action IN ('research.claim', 'research.outcome')")]
        talks = [dict(r) for r in db.execute(
            "SELECT actor, kind, created_at FROM activities WHERE kind IN (?,?,?) AND deleted_at IS NULL", CONVERSATIONS)]

    ever_won = 0
    for d in deals:
        approx = False
        if d["won_at"] is None and d["stage"] in won_stages:
            # Won before won_at existed: its last update and its owner today (crm_store v6).
            d["won_at"], d["won_by"], approx = d["updated_at"], d["owner"], True
        if d["won_at"] is None:
            continue
        ever_won += 1
        r = rows.get(d["won_by"])
        if r is None or not inside(d["won_at"]) or d["currency"] != currency:
            continue
        r["won_count"] += 1
        r["won_cents"] += d["amount_cents"]
        if everyone or d["owner"] == person.id:
            r["deals"].append({"contact_id": d["contact_id"], "client": d["client"], "title": d["title"],
                               "amount_cents": d["amount_cents"], "won_at": d["won_at"],
                               "approximate": approx})
    for a in audit:
        r = rows.get(a["actor"].removeprefix("human:"))
        if r is None or not inside(a["created_at"]):
            continue
        if a["action"] == "research.claim":
            r["leads_taken"] += 1
        else:
            r["touches"] += 1
            try:
                data = json.loads(a["payload"])["data"]
                if data.get("outcome") in ("interested", "quoted", "won"):
                    r["_interested"].add(data.get("place_id"))
            except (ValueError, KeyError, TypeError, AttributeError):
                pass
    for t in talks:
        r = rows.get(t["actor"].removeprefix("human:"))
        if r is not None and inside(t["created_at"]):
            r["touches"] += 1
    for r in rows.values():
        r["interested"] = len(r.pop("_interested"))

    key = lambda r: (-r["won_cents"], -r["won_count"], -r["interested"], -r["touches"], -r["leads_taken"])
    board = sorted(rows.values(), key=lambda r: (*key(r), r["name"].lower()))
    for n, r in enumerate(board, 1):
        active = r["won_count"] or r["interested"] or r["touches"] or r["leads_taken"]
        r["rank"] = n if active else None
    return {"period": period, "since": since, "currency": currency, "rows": board,
            "any_closed": ever_won > 0,
            "empty_note": "" if ever_won else "No closed deals recorded yet.",
            "periods": [{"id": "week", "label": "This week"}, {"id": "month", "label": "This month"},
                        {"id": "all", "label": "All time"}]}

