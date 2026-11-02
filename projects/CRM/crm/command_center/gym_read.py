"""What the gym screens read: Today, members, one member, the schedule, a class roster and the
paper roster. Read-only; every read goes through `Store.authorize` and the person's visibility
(`sees: own` reads only the members that person owns). Design: docs/products/gym-system-p1-design.md.
"""
import html
from datetime import date, datetime, timedelta

from . import access, crm_store, gym, gym_billing, gym_demo, gym_door


def _open(store, prof, person):
    gym.require(prof)
    store.authorize(prof, person, "gym_read")
    gym.ensure(store, prof)


def _visible(prof, person):
    """SQL filter on contacts `c` for this person, and its parameters."""
    return access.contact_scope(prof, person, "c")


def _members(db, prof, person):
    """Every visible person record, with membership, plan, rank and last visit."""
    where, params = _visible(prof, person)
    rows = {r["id"]: dict(r) for r in db.execute(
        "SELECT c.id,c.name,c.email,c.phone,c.lifecycle,c.revision,c.owner,c.created_at,d.birth_date,d.joined_on,d.program,"
        "d.emergency_name,d.emergency_phone,d.guardian_id,d.revision AS details_revision FROM contacts c "
        "LEFT JOIN member_details d ON d.contact_id=c.id WHERE c.kind='person' AND c.deleted_at IS NULL" + where +
        " ORDER BY c.name COLLATE NOCASE", params)}
    for r in rows.values():
        r.update(membership=None, rank=None, last_visit="", visits=0)
    for m in db.execute("SELECT m.*, p.name AS plan FROM memberships m JOIN plans p ON p.id=m.plan_id "
                        "ORDER BY CASE WHEN m.status IN ('active','trial') THEN 0 ELSE 1 END, m.started_on DESC"):
        r = rows.get(m["contact_id"])
        if r and r["membership"] is None:
            r["membership"] = dict(m)
    for a in current_ranks(db).values():
        if a["contact_id"] in rows:
            rows[a["contact_id"]]["rank"] = a
    for c in db.execute("SELECT contact_id, max(local_date) AS last, count(*) AS n FROM checkins GROUP BY contact_id"):
        if c["contact_id"] in rows:
            rows[c["contact_id"]].update(last_visit=c["last"], visits=c["n"])
    waivers = gym_door.signed(db, prof)  # None: this gym takes no waiver, and the screens show no dot
    for r in rows.values():
        r["waiver"] = None if waivers is None else (waivers.get(r["id"], ("",))[0])
    return rows


def current_ranks(db):
    out = {}
    for a in db.execute("SELECT * FROM rank_awards ORDER BY awarded_on, created_at"):
        out[a["contact_id"]] = dict(a)
    return out


def rank_label(prof, award):
    if not award:
        return "No rank recorded"
    try:
        _, belt = gym.belt_of(prof, award["system_id"], award["belt"])
    except crm_store.CRMError:
        return award["belt"]
    n = award["stripes"]
    return f"{belt['label']} belt" + (f", {n} stripe{'s' if n != 1 else ''}" if n else "")


def due(db, prof, rows):
    """Members whose classes since their last award reach the threshold (profile data)."""
    out = []
    for r in rows.values():
        a = r["rank"]
        if not a or not (r["membership"] and r["membership"]["status"] in gym.LIVE):
            continue
        try:
            system, belt = gym.belt_of(prof, a["system_id"], a["belt"])
        except crm_store.CRMError:
            continue
        need = belt.get("due_after_classes")
        if type(need) is not int or need <= 0:
            continue
        since = db.execute("SELECT count(*) FROM checkins WHERE contact_id=? AND local_date>?",
                           (r["id"], a["awarded_on"])).fetchone()[0]
        if since >= need:
            out.append({"id": r["id"], "name": r["name"], "rank": rank_label(prof, a), "classes": since,
                        "threshold": need, "what": "stripe" if a["stripes"] < belt["stripes"] else "belt review"})
    return sorted(out, key=lambda x: -x["classes"])


def sessions_on(db, prof, on):
    """Every class on a local date: weekly templates plus stored rows (canceled, or from a template
    that changed since), in start order."""
    tz = gym.zone(prof)
    stored = {r["id"]: dict(r) for r in db.execute("SELECT * FROM sessions WHERE local_date=?", (on.isoformat(),))}
    out = dict(stored)
    for t in db.execute("SELECT * FROM class_templates WHERE active=1 AND weekday=?", (on.weekday(),)):
        sid = gym.session_id(t["id"], on.isoformat())
        if sid not in out:
            out[sid] = {"id": sid, "template_id": t["id"], "local_date": on.isoformat(), "name": t["name"],
                        "starts_at": gym.starts_at(on, t["start"], tz).isoformat(), "minutes": t["minutes"],
                        "program": t["program"], "canceled": 0, "cancel_reason": ""}
    templates = {t["id"]: dict(t) for t in db.execute("SELECT * FROM class_templates")}
    booked = _count(db, "bookings", list(out))
    came = _count(db, "checkins", list(out))
    marks = ",".join("?" * len(out))
    booked_came = {r[0]: r[1] for r in db.execute(  # booked AND on the mat: the rest who came are walk-ins
        f"SELECT k.session_id, count(*) FROM checkins k JOIN bookings b ON b.session_id=k.session_id AND "
        f"b.contact_id=k.contact_id WHERE k.session_id IN ({marks}) GROUP BY k.session_id", list(out))} if out else {}
    result = []
    for s in out.values():
        t = templates.get(s["template_id"], {})
        start = datetime.fromisoformat(s["starts_at"]).astimezone(tz)
        result.append({**s, "start_local": start.strftime("%H:%M"), "time": _time(start),
                       "ends_at": (datetime.fromisoformat(s["starts_at"]) + timedelta(minutes=s["minutes"])).isoformat(),
                       "instructor": t.get("instructor", ""), "room": t.get("room", ""), "cap": t.get("cap", 0),
                       "booked": booked.get(s["id"], 0), "came": came.get(s["id"], 0),
                       "booked_came": booked_came.get(s["id"], 0)})
    return sorted(result, key=lambda s: s["starts_at"])


def _count(db, table, ids):
    if not ids:
        return {}
    column = "session_id"
    marks = ",".join("?" * len(ids))
    return {r[0]: r[1] for r in db.execute(
        f"SELECT {column}, count(*) FROM {table} WHERE {column} IN ({marks}) GROUP BY {column}", ids)}


def _time(local):
    return local.strftime("%I:%M %p").lstrip("0")


def _names(rows, ids):
    return [{"id": i, "name": rows[i]["name"], "waiver": rows[i]["waiver"]} for i in ids if i in rows]


def today(store, prof, person):
    _open(store, prof, person)
    cfg = gym.require(prof)
    now = gym.local_now(prof)
    on = now.date()
    with store.connection() as db:
        db.execute("BEGIN")
        rows = _members(db, prof, person)
        classes = sessions_on(db, prof, on)
        for s in classes:
            start, end = datetime.fromisoformat(s["starts_at"]), datetime.fromisoformat(s["ends_at"])
            s["state"] = ("canceled" if s["canceled"] else "now" if start <= now < end
                          else "done" if end <= now else "later")
        nxt = next((s for s in classes if s["state"] == "later"), None)
        if nxt:
            nxt["state"] = "next"
        focus = next((s for s in classes if s["state"] == "now"), None) or nxt
        if focus:
            focus["booked_names"] = _names(rows, [r[0] for r in db.execute(
                "SELECT contact_id FROM bookings WHERE session_id=?", (focus["id"],))])
            focus["came_names"] = _names(rows, [r[0] for r in db.execute(
                "SELECT contact_id FROM checkins WHERE slot=? ORDER BY at", (focus["id"],))])
        came = []
        by_id = {s["id"]: s for s in classes}
        for c in db.execute("SELECT * FROM checkins WHERE local_date=? ORDER BY at DESC", (on.isoformat(),)):
            if c["contact_id"] in rows:
                s = by_id.get(c["slot"])  # a class shows its own time; open mat shows when they came
                came.append({"id": c["id"], "contact_id": c["contact_id"], "name": rows[c["contact_id"]]["name"],
                             "waiver": rows[c["contact_id"]]["waiver"],
                             "class": s["name"] if s else "Open mat", "method": c["method"],
                             "time": s["time"] if s else _time(datetime.fromisoformat(c["at"]).astimezone(gym.zone(prof))),
                             "starts": s["starts_at"] if s else c["at"]})
        came.sort(key=lambda c: c["starts"], reverse=True)  # the latest class first
        days = cfg.get("not_seen_days", 14)
        days = days if type(days) is int and days > 0 else 14
        cutoff = (on - timedelta(days=days)).isoformat()
        quiet = []
        for r in rows.values():
            m = r["membership"]
            if not m or m["status"] not in gym.LIVE:
                continue
            since = r["last_visit"] or (r["joined_on"] or m["started_on"])
            if not r["last_visit"] and (r["created_at"] or "")[:10] >= cutoff:
                continue  # added in the last N days (an import on switch day): no visit is not "not seen"
            if since and since < cutoff:  # never came: no day count, the screen says "never checked in"
                quiet.append({"id": r["id"], "name": r["name"], "last_visit": r["last_visit"],
                              "days": (on - date.fromisoformat(since)).days if r["last_visit"] else None,
                              "phone": r["phone"]})
        status = [r["membership"]["status"] for r in rows.values() if r["membership"]]
        active, trials = status.count("active"), status.count("trial")
        return {"date": on.isoformat(), "weekday": gym.DAYS[on.weekday()], "now": now.strftime("%I:%M %p").lstrip("0"),
                "classes": classes, "focus": focus, "came": came,
                "not_seen": sorted(quiet, key=lambda q: -(q["days"] if q["days"] is not None else 10 ** 6)),
                "not_seen_days": days, "due": due(db, prof, rows), "active_members": active, "trials": trials,
                "has_ranks": bool(gym.ranks(prof)),
                "billing": gym_billing.today(db, prof, rows),
                "can": _can(prof, person)}


def _can(prof, person):
    return {k: access.can_do(prof, person, "gym_" + k) for k in ("checkin", "promote", "manage")} | {
        "admin": access.sees(prof, person) == "all" and access.can_do(prof, person, "crm_assign")}


def members(store, prof, person):
    _open(store, prof, person)
    with store.connection() as db:
        db.execute("BEGIN")
        rows = _members(db, prof, person)
        plans = [dict(p) for p in db.execute("SELECT id,name,interval,program,active,revision FROM plans ORDER BY name")]
        flagged = {"due": {d["id"] for d in due(db, prof, rows)}, "card_failed": gym_billing.failed_ids(db, prof)}
    out = [{"id": r["id"], "name": r["name"], "phone": r["phone"], "email": r["email"],
            "flags": [k for k in ("card_failed", "due") if r["id"] in flagged[k]],
            "status": (r["membership"] or {}).get("status", "none"), "plan": (r["membership"] or {}).get("plan", ""),
            "rank": rank_label(prof, r["rank"]), "belt": (r["rank"] or {}).get("belt", ""),
            "last_visit": r["last_visit"], "visits": r["visits"], "program": r["program"] or "", "waiver": r["waiver"]}
           for r in rows.values()]
    return {"members": out, "plans": plans, "programs": gym.programs(prof), "ranks": gym.ranks(prof),
            "today": gym.today(prof).isoformat(), "can": _can(prof, person)}


def member(store, prof, person, cid):
    _open(store, prof, person)
    with store.connection() as db:
        db.execute("BEGIN")
        contact = store.contact(db, prof, person, cid)
        rows = _members(db, prof, person)
        r = rows.get(contact["id"])
        if not r:
            raise crm_store.CRMError("Record not found", 404)
        memberships = [dict(m) for m in db.execute(
            "SELECT m.*, p.name AS plan FROM memberships m JOIN plans p ON p.id=m.plan_id WHERE m.contact_id=? "
            "ORDER BY m.started_on DESC", (r["id"],))]
        awards = [{**dict(a), "label": rank_label(prof, a)} for a in db.execute(
            "SELECT * FROM rank_awards WHERE contact_id=? ORDER BY awarded_on DESC, created_at DESC", (r["id"],))]
        visits = [dict(v) for v in db.execute(
            "SELECT k.id, k.local_date, k.method, coalesce(s.name, 'Open mat') AS class FROM checkins k "
            "LEFT JOIN sessions s ON s.id=k.session_id WHERE k.contact_id=? ORDER BY k.local_date DESC, k.at DESC "
            "LIMIT 500", (r["id"],))]  # the screen shows 30, then "show all"
        mine = {k[0] for k in db.execute("SELECT session_id FROM checkins WHERE contact_id=? AND local_date=?",
                                         (r["id"], gym.today(prof).isoformat()))}
        today_classes = [{"id": s["id"], "name": s["name"], "time": s["time"]} for s in sessions_on(db, prof, gym.today(prof))
                         if not s["canceled"] and s["id"] not in mine and (not s["program"] or not r["program"] or s["program"] == r["program"])]
        since = db.execute("SELECT count(*) FROM checkins WHERE contact_id=? AND local_date>?",
                           (r["id"], (r["rank"] or {}).get("awarded_on", ""))).fetchone()[0]
        family = [{"id": f["id"], "name": f["name"]} for f in rows.values()
                  if f["id"] != r["id"] and (f["guardian_id"] == r["id"] or (r["guardian_id"] and f["id"] == r["guardian_id"]))]
        plans = [dict(p) for p in db.execute("SELECT id,name,active FROM plans ORDER BY name")]
        billing = gym_billing.member(db, prof, r["id"])
        waivers = gym_door.history(db, prof, r["id"])
        from . import gym_history  # a member's months of training, for the attendance card
        months = gym_history.member_months(db, r["id"], gym.today(prof))
        first_visit = db.execute("SELECT min(local_date) FROM checkins WHERE contact_id=?", (r["id"],)).fetchone()[0] or ""
    names = {m["id"]: m.get("name", m["id"]) for m in (prof.get("people") or {}).get("members", [])}
    for a in awards:
        a["by"] = names.get(a["awarded_by"].removeprefix("human:"), a["awarded_by"])
    return {"member": {k: r[k] for k in ("id", "name", "email", "phone", "birth_date", "joined_on", "program",
                                          "emergency_name", "emergency_phone", "guardian_id", "revision",
                                          "details_revision", "last_visit", "visits")},
            "rank": rank_label(prof, r["rank"]), "current": r["rank"], "classes_since_award": since,
            "memberships": memberships, "billing": billing, "today_classes": today_classes,
            "months": months, "first_visit": first_visit, "waivers": waivers, "awards": awards, "visits": visits, "family": family, "plans": plans,
            "ranks": gym.ranks(prof), "programs": gym.programs(prof), "today": gym.today(prof).isoformat(),
            "can": _can(prof, person)}


def schedule(store, prof, person, start=None):
    _open(store, prof, person)
    first = date.fromisoformat(gym.day(start, "From")) if start else gym.today(prof)
    with store.connection() as db:
        db.execute("BEGIN")
        templates = [dict(t) for t in db.execute("SELECT * FROM class_templates ORDER BY weekday, start")]
        days = [{"date": d.isoformat(), "weekday": gym.DAYS[d.weekday()], "classes": sessions_on(db, prof, d)}
                for d in gym.week_dates(first)]
    return {"templates": templates, "days": days, "weekdays": gym.DAYS, "programs": gym.programs(prof),
            "today": gym.today(prof).isoformat(), "can": _can(prof, person)}


def roster(store, prof, person, sid):
    """One class: who is booked, who came, and every other live member to tap."""
    _open(store, prof, person)
    with store.connection() as db:
        db.execute("BEGIN")
        s = gym.session(db, prof, sid)
        on = date.fromisoformat(s["local_date"])
        s = next((x for x in sessions_on(db, prof, on) if x["id"] == s["id"]), s)
        rows = _members(db, prof, person)
        booked = {r[0] for r in db.execute("SELECT contact_id FROM bookings WHERE session_id=?", (s["id"],))}
        came = {r[0]: r[1] for r in db.execute("SELECT contact_id, id FROM checkins WHERE slot=?", (s["id"],))}
    people = [{"id": r["id"], "name": r["name"], "rank": rank_label(prof, r["rank"]), "belt": (r["rank"] or {}).get("belt", ""),
               "booked": r["id"] in booked, "checkin_id": came.get(r["id"]), "waiver": r["waiver"],
               "live": bool(r["membership"] and r["membership"]["status"] in gym.LIVE),
               "program": r["program"] or ""}
              for r in rows.values() if r["id"] in booked or r["id"] in came or
              (r["membership"] and r["membership"]["status"] in gym.LIVE and  # a kids class lists the kids
               (not s["program"] or not r["program"] or r["program"] == s["program"]))]
    people.sort(key=lambda p: (not p["booked"], p["checkin_id"] is None, p["name"].casefold()))
    return {"session": s, "people": people, "today": gym.today(prof).isoformat(), "ranks": gym.ranks(prof),
            "can": _can(prof, person)}


def printable(store, prof, person, on=None):
    """The paper roster: every class on a date, every live member of its program, a tick box. It is
    what a coach uses when the system is down, so it prints from a plain page with no script."""
    _open(store, prof, person)
    on = date.fromisoformat(gym.day(on, "Date")) if on else gym.today(prof)
    with store.connection() as db:
        db.execute("BEGIN")
        rows = _members(db, prof, person)
        classes = [s for s in sessions_on(db, prof, on) if not s["canceled"]]
        booked = {}
        for b in db.execute("SELECT session_id, contact_id FROM bookings"):
            booked.setdefault(b[0], set()).add(b[1])
    live = [r for r in rows.values() if r["membership"] and r["membership"]["status"] in gym.LIVE]
    ranked = bool(gym.ranks(prof))  # a gym with no belts prints no Rank column
    e = html.escape
    parts = []
    for s in classes:
        group = [r for r in live if not s["program"] or not r["program"] or r["program"] == s["program"]]
        rank = (lambda r: f"<td>{e(rank_label(prof, r['rank']))}</td>") if ranked else (lambda r: "")
        lines = "".join(f"<tr><td class=box></td><td>{e(r['name'])}{' (booked)' if r['id'] in booked.get(s['id'], ()) else ''}"
                        f"</td>{rank(r)}</tr>" for r in group)
        parts.append(f"<section><h2>{e(s['time'])} &middot; {e(s['name'])}</h2><p>{e(s['instructor'])}"
                     f"{' &middot; ' + e(s['room']) if s['room'] else ''} &middot; tick who is on the mat, enter it later"
                     f"</p><table><thead><tr><th>Here</th><th>Member</th>{'<th>Rank</th>' if ranked else ''}</tr></thead><tbody>{lines}"
                     f"<tr><td class=box></td><td>Visitor: ____________________</td>{'<td></td>' if ranked else ''}</tr></tbody></table></section>")
    body = "".join(parts) or "<p>No classes on this date.</p>"
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,"
            f"initial-scale=1'><meta name=robots content='noindex,nofollow'><title>Paper roster {on.isoformat()}</title>"
            "<style>body{font:16px/1.4 system-ui,sans-serif;margin:24px;color:#111}h1{font-size:22px}"
            "h2{font-size:18px;margin:24px 0 4px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #999;"
            "padding:6px 8px;text-align:left}td.box{width:44px}section{break-inside:avoid}p{margin:0 0 8px}"
            "@media print{.no-print{display:none}body{margin:0}}</style></head><body>"
            f"<h1>{e(prof['name'])} &middot; paper roster &middot; {gym.DAYS[on.weekday()]} {on.isoformat()}</h1>"
            "<p class=no-print><button onclick='print()'>Print</button> Use this when check-in is down.</p>"
            f"{body}</body></html>")


def billing(store, prof, person):
    """Billing for the Billing tab: simulated in a demo (gym_demo.py), Stripe test mode with a
    `gym.billing` block (gym_billing.py)."""
    _open(store, prof, person)
    return gym_billing.summary(store, prof, lambda db: _members(db, prof, person)) | {"can": _can(prof, person)}


def switch(store, prof, person):
    """The guide from the gym's current tool to this one (profile data, gym.switch)."""
    _open(store, prof, person)
    return gym_demo.switch(prof) | {"can": _can(prof, person)}
