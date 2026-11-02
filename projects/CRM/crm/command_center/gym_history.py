"""Attendance history: who trained when, the trend per member, and who to follow up with.

WHY
    Sam, 2026-10-28: Today shows the day; an owner also needs the history. Filter by a week, a
    month or any range; see each member's visits against the period before, their total since the
    first class, and the lists that say who needs a call.

HOW
    Read-only, through gym_read's visibility (`sees: own` reads only that person's members). One
    pass over check-ins and bookings in SQL; the rest is arithmetic. Every list names its rule, so a
    number on the screen can be checked by hand:
      slowing down   on a plan, 4+ visits in the period before, at most half that in this one
      not seen       on a plan, no visit for the gym's not_seen_days (Today's rule)
      new, few visits  joined in the last 30 days of the range, fewer than 4 visits since joining
      booked, no-show  booked a class in the range that has ended, and never checked in to it
"""
from datetime import date, datetime, timedelta

from . import crm_store, gym, gym_read

SLOW_MIN = 4  # visits in the period before, for "slowing down" to mean anything
NEW_DAYS, NEW_FEW = 30, 4
MAX_DAYS = 400  # the widest range one request reads


def _range(prof, start, end):
    on = gym.today(prof)
    end = date.fromisoformat(gym.day(end, "To")) if end else on
    start = date.fromisoformat(gym.day(start, "From")) if start else end - timedelta(days=end.weekday())  # this week
    if start > end:
        raise crm_store.CRMError("From must be on or before To")
    if (end - start).days >= MAX_DAYS:
        raise crm_store.CRMError(f"Choose a range of at most {MAX_DAYS} days")
    return start, end


def attendance(store, prof, person, start=None, end=None, program=None):
    gym_read._open(store, prof, person)
    start, end = _range(prof, start, end)
    days = (end - start).days + 1
    before = (start - timedelta(days=days), start - timedelta(days=1))  # the same length, just before
    program = program or ""
    if program and program not in {p["id"] for p in gym.programs(prof)}:
        raise crm_store.CRMError("Choose a program from this gym")
    now = gym.local_now(prof)
    with store.connection() as db:
        db.execute("BEGIN")
        rows = gym_read._members(db, prof, person)
        if program:
            rows = {k: r for k, r in rows.items() if (r["program"] or "") == program}
        visits = db.execute("SELECT k.contact_id, k.local_date, k.session_id, coalesce(s.name, t.name, 'Open mat') AS class "
                            "FROM checkins k LEFT JOIN sessions s ON s.id=k.session_id "
                            "LEFT JOIN class_templates t ON t.id=substr(k.session_id, 1, instr(k.session_id, '@') - 1) "
                            "WHERE k.local_date BETWEEN ? AND ?", (before[0].isoformat(), end.isoformat())).fetchall()
        first = {r[0]: r[1] for r in db.execute("SELECT contact_id, min(local_date) FROM checkins GROUP BY contact_id")}
        bookings = db.execute("SELECT b.contact_id, b.session_id FROM bookings b WHERE substr(b.session_id, "
                              "instr(b.session_id, '@') + 1) BETWEEN ? AND ?", (start.isoformat(), end.isoformat())).fetchall()
        came = {(r[0], r[1]) for r in db.execute("SELECT contact_id, session_id FROM checkins WHERE session_id IS NOT NULL "
                                                 "AND local_date BETWEEN ? AND ?", (start.isoformat(), end.isoformat()))}
        ended = {}  # session id -> has it ended (a class later today has not)
        for _, sid in bookings:
            if sid not in ended:
                s = next((x for x in gym_read.sessions_on(db, prof, date.fromisoformat(sid.rpartition("@")[2])) if x["id"] == sid), None)
                ended[sid] = bool(s) and not s["canceled"] and datetime.fromisoformat(s["ends_at"]) <= now
    lo, hi = start.isoformat(), end.isoformat()
    per_day = {(start + timedelta(days=n)).isoformat(): 0 for n in range(days)}
    per_class, now_n, prev_n = {}, {}, {}
    for cid, on, _, cls in visits:
        if cid not in rows:
            continue
        if lo <= on <= hi:
            per_day[on] += 1
            now_n[cid] = now_n.get(cid, 0) + 1
            per_class[cls] = per_class.get(cls, 0) + 1
        else:
            prev_n[cid] = prev_n.get(cid, 0) + 1
    live = {k for k, r in rows.items() if r["membership"] and r["membership"]["status"] in gym.LIVE}
    no_show = {}
    for cid, sid in bookings:
        if cid in rows and ended.get(sid) and (cid, sid) not in came:
            no_show[cid] = no_show.get(cid, 0) + 1

    def member(cid):
        r = rows[cid]
        since = first.get(cid, "")
        weeks = max(1, ((end - date.fromisoformat(since)).days + 1) / 7) if since else 1
        return {"id": cid, "name": r["name"], "phone": r["phone"], "plan": (r["membership"] or {}).get("plan", ""),
                "status": (r["membership"] or {}).get("status", "none"), "rank": gym_read.rank_label(prof, r["rank"]),
                "visits": now_n.get(cid, 0), "before": prev_n.get(cid, 0), "total": r["visits"],
                "first_visit": since, "last_visit": r["last_visit"], "joined_on": r["joined_on"] or "",
                "per_week": round(r["visits"] / weeks, 1) if since else 0.0, "no_shows": no_show.get(cid, 0)}

    members = [member(cid) for cid in rows if cid in live or now_n.get(cid)]
    members.sort(key=lambda m: (-m["visits"], m["name"].casefold()))
    cfg = gym.require(prof)
    quiet_days = cfg.get("not_seen_days", 14) if type(cfg.get("not_seen_days")) is int else 14
    quiet_cut = (end - timedelta(days=quiet_days)).isoformat()
    new_cut = (end - timedelta(days=NEW_DAYS)).isoformat()
    follow = {
        "slowing_down": sorted((m for m in members if m["id"] in live and m["before"] >= SLOW_MIN and m["visits"] * 2 <= m["before"]),
                               key=lambda m: m["visits"] - m["before"]),
        "not_seen": sorted((m for m in members if m["id"] in live and (m["last_visit"] or "") < quiet_cut
                            and (m["last_visit"] or (rows[m["id"]]["created_at"] or "")[:10] < quiet_cut)),
                           key=lambda m: m["last_visit"] or ""),
        "new_few_visits": sorted((m for m in members if m["id"] in live and m["joined_on"] >= new_cut and m["total"] < NEW_FEW),
                                 key=lambda m: m["joined_on"]),
        "no_shows": sorted((m for m in members if m["no_shows"]), key=lambda m: -m["no_shows"]),
    }
    total, total_before = sum(now_n.values()), sum(prev_n.values())
    return {"from": lo, "to": hi, "days": days, "before": {"from": before[0].isoformat(), "to": before[1].isoformat()},
            "program": program, "programs": gym.programs(prof), "today": gym.today(prof).isoformat(),
            "rules": {"slow_min": SLOW_MIN, "new_days": NEW_DAYS, "new_few": NEW_FEW, "not_seen_days": quiet_days},
            "totals": {"checkins": total, "checkins_before": total_before, "members": len(now_n),
                       "members_before": len(prev_n), "per_day": round(total / days, 1),
                       "on_plan": len(live), "on_plan_trained": len(live & set(now_n))},
            "per_day": [{"date": d, "checkins": n} for d, n in per_day.items()],
            "classes": sorted(({"class": k, "checkins": v} for k, v in per_class.items()), key=lambda c: -c["checkins"]),
            "follow_up": follow, "members": members}


def member_months(db, cid, on, months=12):
    """A member's visits per calendar month, the last `months` months ending with `on`'s month."""
    firsts = []
    y, m = on.year, on.month
    for _ in range(months):
        firsts.append(date(y, m, 1))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    firsts.reverse()
    counts = {r[0]: r[1] for r in db.execute(
        "SELECT substr(local_date, 1, 7), count(*) FROM checkins WHERE contact_id=? AND local_date>=? GROUP BY 1",
        (cid, firsts[0].isoformat()))}
    return [{"month": f.isoformat()[:7], "visits": counts.get(f.isoformat()[:7], 0)} for f in firsts]
