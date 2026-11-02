"""Demo mode: the real engine, running a sales demo on made-up records.

A demo is a profile with a `demo` block plus a database the demo builder filled with fictional
people (tools/gym_demo.py for gyms). Nothing here knows what kind of business it is: the gym
module, or later a barbershop's, names its own routes and commands in the profile.

    "demo": {
      "enabled": true, "fictional": true,
      "banner": "Demo: made-up members, no real cards, resets every night.",
      "story": {"timezone": "America/Los_Angeles", "time": "18:15", "weekdays": [0, 1, 2, 3, 4],
                "creep_minutes": 30},
      "allow": ["/gym", "/gym.js", "/gym.css", "/api/gym", "/kiosk", "/kiosk.js", "/kiosk.css", "/api/kiosk"],
      "commands": ["gym.", "contact.create"],
      "home": "gym#today",
      "kiosk_pin": "1234",
      "tour": [{"title": "Today", "text": "...", "href": "gym#today", "target": "#gym-stats"}]
    }

What it does:
- **The story clock.** The engine's clock (`crm_store.now`) starts at `story.time` on the most
  recent `story.weekdays` day and moves forward in real time for `creep_minutes`, then holds. So
  the demo always opens with a class on the mat, whatever hour the owner clicks the link.
- **A closed door around everything else.** Only the engine basics below and the profile's
  `allow` list answer; every other route is a 404. A write on /api/crm/command must start with one
  of `commands`. Research, connectors, API keys and the rest of the CRM never answer in a demo.
- **The tour.** `/api/demo` hands demo.js the banner, the guided steps and the kiosk PIN hint.
- **The stamp.** The builder writes `demo_database = fictional` into the database. The `demo`
  identity mode (every visitor is the owner) refuses to start on a database without it, so a real
  business's records can never be opened this way (server.make_server).
"""
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import crm_store, profiles

WWW = Path(__file__).resolve().parent / "www"
JSON = "application/json; charset=utf-8"
STAMP = ("demo_database", "fictional")
# Engine basics every demo needs: health, the shared look, the brand, the demo layer itself.
BASE = ("/healthz", "/crm.css", "/crm-font.css", "/crm-theme.css", "/brand-logo", "/demo.js", "/demo.css",
        "/api/demo", "/api/crm/command", "/api/crm/export")


def config(prof):
    demo = prof.get("demo")
    return demo if isinstance(demo, dict) and demo.get("enabled") is True else None


def story_start(prof, today=None):
    """The moment the story begins: `story.time` on the latest allowed weekday, today or before."""
    story = (config(prof) or {}).get("story") or {}
    tz = ZoneInfo(story.get("timezone") or "UTC")
    day = today or datetime.now(tz).date()
    days = [d for d in story.get("weekdays") or range(7) if type(d) is int and 0 <= d <= 6] or list(range(7))
    while day.weekday() not in days:
        day -= timedelta(days=1)
    h, m = (int(x) for x in str(story.get("time") or "18:00").split(":"))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=tz)


def install_clock(prof):
    """Point the engine's clock at the story. Returns the start, for the builder's log."""
    start = story_start(prof)
    creep = ((config(prof) or {}).get("story") or {}).get("creep_minutes", 30)
    creep = creep if type(creep) is int and 0 <= creep <= 240 else 30
    t0 = time.monotonic()
    crm_store.now = lambda: (start + timedelta(seconds=min(time.monotonic() - t0, creep * 60))).astimezone(
        timezone.utc).isoformat(timespec="microseconds")
    return start


def stamp(store):
    with store.connection() as db:
        db.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)", STAMP)


def stamped(store):
    with store.connection() as db:
        row = db.execute("SELECT value FROM metadata WHERE key=?", (STAMP[0],)).fetchone()
    return bool(row) and row[0] == STAMP[1]


def check_startup(mode, store):
    """server.make_server: the demo identity mode runs only on a database stamped fictional."""
    if mode == "demo" and (store is None or not stamped(store)):
        from .identity import StartupRefused
        raise StartupRefused("demo mode runs only on a database the demo builder stamped as fictional "
                             "(tools/gym_demo.py); this one is not")


def _under(path, prefixes):
    return any(path == p or path.startswith(p.rstrip("/") + "/") for p in prefixes)


def allowed(prof, path):
    return path == "/" or _under(path, BASE) or _under(path, [p for p in config(prof).get("allow") or []
                                                             if isinstance(p, str) and p.startswith("/")])


def command_allowed(prof, raw):
    try:
        action = json.loads(raw or b"{}").get("action")
    except (ValueError, AttributeError):
        return False
    starts = [c for c in config(prof).get("commands") or [] if isinstance(c, str) and c]
    return isinstance(action, str) and any(action == c or (c.endswith(".") and action.startswith(c)) for c in starts)


def tour(prof):
    demo = config(prof)
    steps = [{"title": str(s.get("title", "")), "text": str(s.get("text", "")), "href": str(s.get("href", "")),
              "target": str(s.get("target", ""))}  # target: CSS selectors the step points at, first match wins
             for s in demo.get("tour") or [] if isinstance(s, dict)]
    return {"name": prof["name"], "banner": demo.get("banner", "Demo with made-up records."),
            "home": demo.get("home", ""), "kiosk_pin": str(demo.get("kiosk_pin", "")), "tour": steps}


def serve(handler, method, u, raw):
    """server.py's first call on every request. True when this answered (or refused). A profile
    without a demo block: False at once, and the engine carries on exactly as before."""
    try:
        prof = profiles.load(handler.cc.business, handler.cc.root)
    except Exception:  # noqa: BLE001  the sign-in reports a broken profile
        return False
    if config(prof) is None:
        return False
    path = u.path.rstrip("/") or "/"
    send = handler._send
    if not allowed(prof, path):
        return send(404, json.dumps({"error": "Not part of this demo"}), JSON) or True
    if path == "/" and method == "GET" and not u.query and config(prof).get("home"):
        return send(302, "", headers={"Location": config(prof)["home"], "Cache-Control": "no-store"}) or True
    if path == "/" and method == "GET":
        return send(404, "Not part of this demo", "text/plain; charset=utf-8") or True
    if method == "GET" and path in ("/demo.js", "/demo.css"):
        ctype = "text/javascript; charset=utf-8" if path.endswith(".js") else "text/css; charset=utf-8"
        return send(200, (WWW / path.lstrip("/")).read_bytes(), ctype, {"Cache-Control": "no-cache"}) or True
    if method == "GET" and path == "/api/demo":
        return send(200, json.dumps(tour(prof)), JSON, {"Cache-Control": "no-store"}) or True
    if method == "POST" and path == "/api/crm/command" and not command_allowed(prof, raw):
        return send(403, json.dumps({"error": "That action is switched off in this demo"}), JSON) or True
    return False
