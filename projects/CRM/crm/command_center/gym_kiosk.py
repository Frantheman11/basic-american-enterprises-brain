"""The check-in kiosk: a shared tablet at the door. Ported from the ordering engine's staff PIN
(modules/online-ordering/src/worker.js), in Python.

- The device PIN unlocks the kiosk and returns a signed token (HMAC-SHA256 over `kiosk.<expiry>`)
  for `gym.kiosk.token_hours` (default 14, one day of classes). The PIN and the secret sit in the
  host's environment (`pin_env`, `secret_env`), never in the profile or the page.
- 10 wrong PINs lock the kiosk for 15 minutes. The count lives in the gym's database, so a restart
  does not reset it. A right PIN clears it.
- With the token: today's classes, a name search (at least 2 letters, at most 12 answers, first
  name and last initial only, live members only), and one write: a check-in, as `system:kiosk`.
- The routes answer without a staff sign-in, like the lead intake. Behind Cloudflare Access they need
  one bypass rule for exactly these paths (docs/products/gym-system-p1-design.md).
"""
import hashlib
import hmac
import os
from datetime import datetime, timedelta

from . import crm_store, gym, gym_billing, gym_read

PATHS = ("/kiosk", "/kiosk.js", "/kiosk.css", "/api/kiosk/unlock", "/api/kiosk/now", "/api/kiosk/find", "/api/kiosk/checkin")
MAX_FAILS = 10
LOCK_MINUTES = 15


def config(prof):
    cfg = gym.config(prof)
    kiosk = (cfg or {}).get("kiosk")
    return kiosk if isinstance(kiosk, dict) else None


def _secret(kiosk, key):
    value = os.environ.get(kiosk.get(key) or "", "")
    if len(value) < (4 if key == "pin_env" else 16):
        raise crm_store.CRMError("Check-in is offline, tell your coach", 503)
    return value


def _sign(secret, body):
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


def make_token(prof, now):
    kiosk = config(prof)
    hours = kiosk.get("token_hours", 14)
    hours = hours if type(hours) is int and 1 <= hours <= 24 else 14
    body = f"kiosk.{int((now + timedelta(hours=hours)).timestamp())}"
    return f"{body}.{_sign(_secret(kiosk, 'secret_env'), body)}"


def check_token(prof, token, now):
    kiosk = config(prof)
    if kiosk is None:
        return False
    parts = str(token or "").split(".")
    if len(parts) != 3 or parts[0] != "kiosk" or not parts[1].isdigit():
        return False
    good = _sign(_secret(kiosk, "secret_env"), f"{parts[0]}.{parts[1]}")
    return hmac.compare_digest(good, parts[2]) and int(parts[1]) > now.timestamp()


def unlock(store, prof, pin):
    """The PIN for a token, or a refusal: 401 wrong, 429 locked (and for how long)."""
    kiosk = config(prof)
    if kiosk is None:
        raise crm_store.CRMError("Not found", 404)
    gym.ensure(store, prof)
    now = datetime.fromisoformat(crm_store.now())
    real = _secret(kiosk, "pin_env")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM kiosk_lock WHERE id=1").fetchone()
        fails, until = (row["fails"], row["locked_until"]) if row else (0, "")
        if until and datetime.fromisoformat(until) > now:
            raise crm_store.CRMError("Too many wrong PINs. Wait 15 minutes.", 429)
        if isinstance(pin, str) and hmac.compare_digest(pin.encode(), real.encode()):
            db.execute("INSERT OR REPLACE INTO kiosk_lock VALUES (1, 0, '')")
            return {"token": make_token(prof, now)}
        fails = 0 if until else fails  # a lock that ran out starts a fresh count
        fails += 1
        locked = (now + timedelta(minutes=LOCK_MINUTES)).isoformat() if fails >= MAX_FAILS else ""
        db.execute("INSERT OR REPLACE INTO kiosk_lock VALUES (1, ?, ?)", (fails, locked))
    if locked:
        raise crm_store.CRMError("Too many wrong PINs. Wait 15 minutes.", 429)
    raise crm_store.CRMError("Wrong PIN.", 401)


def _short(name, letters=1):
    words = name.split()
    if len(words) < 2:
        return name
    last = words[-1]
    return f"{words[0]} {last}" if letters >= len(last) else f"{words[0]} {last[:letters]}."


def _shorts(names):
    """First name and last initial; where two answers would read the same, more of the surname
    (Ethan Ga., Ethan Gu.), up to the whole of it, so every button on the tablet is different."""
    letters = [1] * len(names)
    for _ in range(max((len(n) for n in names), default=0)):
        out = [_short(n, k) for n, k in zip(names, letters)]
        clash = {s for s in out if out.count(s) > 1}
        if not clash:
            break
        letters = [k + 1 if s in clash else k for s, k in zip(out, letters)]
    return [_short(n, k) for n, k in zip(names, letters)]


def now_classes(store, prof):
    """Classes a member can check into right now: started, or starting within `early_minutes`."""
    gym.ensure(store, prof)
    early = config(prof).get("early_minutes", 30)
    early = early if type(early) is int and 0 <= early <= 120 else 30
    now = gym.local_now(prof)
    with store.connection() as db:
        db.execute("BEGIN")
        classes = gym_read.sessions_on(db, prof, now.date())
    out = []
    for s in classes:
        start, end = datetime.fromisoformat(s["starts_at"]), datetime.fromisoformat(s["ends_at"])
        if not s["canceled"] and start - timedelta(minutes=early) <= now < end:
            out.append({"id": s["id"], "name": s["name"], "time": s["time"]})
    return {"classes": out, "date": now.date().isoformat()}


def find(store, prof, query):
    query = " ".join(str(query or "").casefold().split())
    if len(query) < 2:
        return {"members": []}
    gym.ensure(store, prof)
    with store.connection() as db:
        rows = db.execute("SELECT DISTINCT c.id, c.name FROM contacts c JOIN memberships m ON m.contact_id=c.id "
                          "WHERE c.kind='person' AND c.deleted_at IS NULL AND m.status IN ('active','trial') "
                          "ORDER BY c.name COLLATE NOCASE").fetchall()
    hits = [r for r in rows if any(w.startswith(query) for w in r["name"].casefold().split())
            or r["name"].casefold().startswith(query)]
    hits = hits[:12]
    return {"members": [{"id": r["id"], "name": s} for r, s in zip(hits, _shorts([r["name"] for r in hits]))]}


def checkin(store, prof, body):
    if not isinstance(body, dict):
        raise crm_store.CRMError("Invalid check-in")
    data = {"contact_id": body.get("contact_id")}
    if body.get("session_id"):
        data["session_id"] = body["session_id"]
    result = gym.execute_as_kiosk(store, prof, data, body.get("request_id"))
    from . import gym_door
    with store.connection() as db:  # a failed payment: a gentle note on the tablet, never the reason
        card = data["contact_id"] in gym_billing.failed_ids(db, prof)
        waivers = gym_door.signed(db, prof)
    return {"ok": True, "already": bool(result.get("already")), "update_card": card,
            "needs_waiver": waivers is not None and data["contact_id"] not in waivers}


def page(prof):
    import html
    from . import demo_mode
    name = html.escape(prof["name"])
    accent = (prof.get("crm") or {}).get("accent", "#3355e0")
    accent = accent if isinstance(accent, str) and len(accent) == 7 and accent.startswith("#") else "#3355e0"
    logo = '<img class="kiosk-logo" src="brand-logo" alt="">' if (prof.get("crm") or {}).get("logo") else ""
    demo = '<link rel="stylesheet" href="demo.css"><script defer src="demo.js"></script>' if demo_mode.config(prof) else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>{name} | Check-in</title><link rel="stylesheet" href="kiosk.css"><style>:root{{--accent:{accent}}}</style>{demo}
<script defer src="kiosk.js"></script></head><body class="kiosk">
<main id="kiosk" aria-live="polite"><h1>{logo}<span>{name}</span></h1><p>Loading check-in&hellip;</p></main>
<noscript>Check-in needs JavaScript. Tell your coach.</noscript></body></html>"""
