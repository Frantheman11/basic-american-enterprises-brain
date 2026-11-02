"""The Research read model: industry -> ring -> company, each company in a lane with its reason.

Sources, all named by the profile's `crm.research` block (the engine names no file):

    industries   the ranked industry file: rings, review floors, lanes, chain names
    discovered   places found by `webpresence.py --discover-nearby`, keyed by place_id
    live         Google's fields for each place (name, website, phone, address, reviews,
                 types, status), fetched live with Place Details and held in memory only

⚠️ The Places terms let us store the place ID indefinitely and coordinates for 30 days,
nothing else (auditor/webpresence.py, the discovery notes). So `discovered` holds only the
place ID, industries, ring, first and last seen and fresh coordinates, and every Google field
here comes from the live source. A place with no live details yet and no scan is `unchecked`.
    ledger       the scan ledger: registry.json, scans/<id>.jsonl, ads/<id>.jsonl

Lanes, in priority order (docs/crm/crm-research-section-brief-2026-09-26.md §4.2):

    paid_weak     an ad tag or confirmed ads, AND the site fails on lead capture
    no_website    operating, no website, and reviews at or above the industry's floor
    weak_site     the site is weak, and there is no sign of paid traffic
    unchecked     has a website that has not been scanned yet
    disqualified  a chain, a clean scan, too little activity, or an industry marked out

A second lane model, `facility`, exists for a business that sells on-site work (2026-10-01):
multi_site, fit, unchecked, disqualified. The industry file's `lane_model` picks it; missing
means `web_presence`. See `classify_facility`.

⚠️ A lane is computed from what we measured, and every lane carries its reasons. There is
never a revenue estimate: size is shown by reviews, locations and ads, nothing else.
⚠️ A missing ad tag is never a finding. `ad_signals` is True or None, never False,
because a tag manager can load the tag after the page renders.
"""

import json
import math
import os
import re
import threading
import time
import urllib.request
from datetime import date
from pathlib import Path

WEAK = ("C", "D", "F")
PRESENCE = ("website", "seo_basics", "lead_capture")
AD_WORDS = {"google_ads_tag": "Google Ads tag on the site",
            "meta_pixel": "Meta (Facebook) pixel on the site",
            "call_tracking": "call tracking on the site"}


class ResearchUnavailable(Exception):
    pass


# ---------------------------------------------------------------------------------------
# Live Google details. Nothing here writes to disk. A detail is held in memory for at most
# `live_details_minutes` (profile, default 60, never more) so one screen does not pay twice,
# and this process makes at most `live_details_per_day` Place Details calls a day (default 0:
# no paid call until Sam sets a number). Without GOOGLE_PLACES_API_KEY nothing is fetched.
# ---------------------------------------------------------------------------------------
DETAILS_URL = "https://places.googleapis.com/v1/places/"
DETAILS_FIELDS = ("id,displayName,websiteUri,formattedAddress,rating,userRatingCount,"
                  "nationalPhoneNumber,types,location,businessStatus")


def _details_row(p):
    return {"name": (p.get("displayName") or {}).get("text", ""), "url": p.get("websiteUri", ""),
            "address": p.get("formattedAddress", ""), "rating": p.get("rating"),
            "review_count": p.get("userRatingCount"), "phone": p.get("nationalPhoneNumber", ""),
            "types": p.get("types") or [], "location": p.get("location"),
            "business_status": p.get("businessStatus", "")}


def fetch_details(place_id, key):
    """One Place Details (New) call. Returns the live row, or None when Google says no."""
    req = urllib.request.Request(DETAILS_URL + place_id, headers={
        "X-Goog-Api-Key": key, "X-Goog-FieldMask": DETAILS_FIELDS})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return _details_row(json.loads(resp.read().decode("utf-8")))
    except (OSError, ValueError):
        return None


class LiveDetails:
    """Google fields by place ID, fetched on demand, kept in memory for `ttl` seconds, and
    capped at `per_day` calls a day. `fetch(place_id)` is the paid call (replaced in tests)."""

    def __init__(self, fetch, per_day=0, ttl=3600, clock=time.time):
        self.fetch, self.per_day, self.ttl, self.clock = fetch, per_day, ttl, clock
        self.held, self.day, self.used = {}, None, 0
        self.lock = threading.Lock()

    def get(self, ids):
        now, out = self.clock(), {}
        with self.lock:
            self.held = {k: v for k, v in self.held.items() if now - v[0] < self.ttl}
            today = date.fromtimestamp(now).isoformat()
            if today != self.day:
                self.day, self.used = today, 0
            for pid in ids:
                if pid in self.held:
                    out[pid] = self.held[pid][1]
                elif self.fetch and self.used < self.per_day:
                    self.used += 1
                    row = self.fetch(pid)
                    if row:
                        self.held[pid] = (now, row)
                        out[pid] = row
        return out


_LIVE = {}
_LIVE_OVERRIDE = None


def set_live_source(source):
    """Replace the live details: `source(ids)` returns {place_id: row}. Tests use it. None
    restores the Places source."""
    global _LIVE_OVERRIDE
    _LIVE_OVERRIDE = source


def live_details(prof, ids):
    """{place_id: Google row} for the ids the live source can give now."""
    ids = list(ids)
    if _LIVE_OVERRIDE is not None:
        return _LIVE_OVERRIDE(ids) or {}
    spec = (prof.get("crm") or {}).get("research") or {}
    per_day = spec.get("live_details_per_day")
    per_day = per_day if type(per_day) is int and per_day > 0 else 0
    minutes = spec.get("live_details_minutes")
    ttl = 60 * (minutes if type(minutes) is int and 0 <= minutes <= 60 else 60)
    key = os.environ.get("GOOGLE_PLACES_API_KEY") or ""
    if not per_day or not key:
        return {}
    source = _LIVE.get(prof.get("id"))
    if source is None or (source.per_day, source.ttl) != (per_day, ttl):
        source = _LIVE[prof.get("id")] = LiveDetails(lambda pid: fetch_details(pid, key), per_day, ttl)
    return source.get(ids)


def _miles(a, b):
    """Great-circle miles between two {latitude, longitude} points, or None."""
    try:
        la1, lo1, la2, lo2 = (math.radians(float(v)) for v in
                              (a["latitude"], a["longitude"], b["latitude"], b["longitude"]))
    except (TypeError, KeyError, ValueError):
        return None
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * 3958.7613 * math.asin(math.sqrt(h))


def _domain(url):
    m = re.match(r"^[a-z]+://([^/:?#]+)", (url or "").lower())
    return m.group(1).removeprefix("www.") if m else ""


def place_view(stored, live, office=None, scanned_url=""):
    """One place as the lane rules read it: the stored record plus the live Google row when
    there is one. `details` is False when there is none, so the rules never read a missing
    website as "no website". `scanned_url` is the website our own scan fetched."""
    g = live or {}
    loc = g.get("location") or stored.get("location")
    dist = g.get("distance_mi") if g.get("distance_mi") is not None else _miles(office, loc)
    view = {"place_id": stored["place_id"], "industries": stored.get("industries") or [],
            "ring": stored.get("ring"), "first_seen": stored.get("first_seen") or "",
            "last_seen": stored.get("last_seen") or "",
            "distance_mi": round(dist, 2) if dist is not None else None,
            "details": live is not None}
    if live is None:
        view.update({"name": "", "url": scanned_url, "domain": _domain(scanned_url),
                     "no_website": False, "business_status": "", "google": {}})
        return view
    url = g.get("url") or ""
    view.update({"name": g.get("name") or "", "url": url, "domain": _domain(url),
                 "no_website": not url, "business_status": g.get("business_status") or "",
                 "google": {k: g.get(k) for k in ("address", "phone", "rating", "review_count",
                                                  "types", "location", "photo_count")}})
    return view


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _last_line(path):
    try:
        lines = [ln for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return None
    for ln in reversed(lines):
        try:
            return json.loads(ln)
        except json.JSONDecodeError:
            continue
    return None


def _inside(root, name):
    root = Path(root).resolve()
    p = (root / name).resolve()
    if root != p and root not in p.parents:
        raise ResearchUnavailable("research paths must be inside the repository")
    return p


def data_root(prof, root):
    """Where the Research files live. Normally the repository root. A deployed CRM that runs
    from an uploaded release names the git checkout instead (`crm.research.data_root`, set by
    deploy/crm-work/setup.py), because discovery and the scan ledger are written and synced
    there, never inside the release."""
    spec = (prof.get("crm") or {}).get("research") or {}
    named = spec.get("data_root")
    if isinstance(named, str) and named and Path(named).is_absolute() and Path(named).is_dir():
        return Path(named)
    return Path(root)


def config(prof, root):
    """The profile's research block, resolved. None when this business has no Research."""
    spec = (prof.get("crm") or {}).get("research")
    if not isinstance(spec, dict) or not spec.get("industries"):
        return None
    root = data_root(prof, root)
    return {k: _inside(root, spec[k]) for k in ("industries", "discovered", "ledger") if spec.get(k)}


def norm_name(name):
    return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"[&+]", " and ", (name or "").lower())).split()


def chains(places, qualify):
    """place_id -> reason, for places that look like a chain or a franchise."""
    floor = int(qualify.get("chain_min_locations") or 3)
    known = [" ".join(norm_name(n)) for n in qualify.get("chain_names") or []]
    by_name, by_domain = {}, {}
    for p in places:
        by_name.setdefault(" ".join(norm_name(p.get("name"))), set()).add(p["place_id"])
        if p.get("domain"):
            by_domain.setdefault(p["domain"], set()).add(p["place_id"])
    out = {}
    for p in places:
        name = " ".join(norm_name(p.get("name")))
        hit = next((k for k in known if k and (name == k or name.startswith(k + " "))), None)
        if hit:
            out[p["place_id"]] = f"chain or franchise ({hit})"
        elif len(by_name.get(name, ())) >= floor:
            out[p["place_id"]] = f"same name at {len(by_name[name])} places"
        elif p.get("domain") and len(by_domain[p["domain"]]) >= floor:
            out[p["place_id"]] = f"one website ({p['domain']}) for {len(by_domain[p['domain']])} places"
    return out


def capture_gaps(checks, slow_below=50):
    """Lead-capture failures in owner words. Only what the scan measured as failing."""
    if not checks:
        return []
    if checks.get("reachable") is False:
        return ["the website did not respond"]
    out = []
    if checks.get("form_found") is False:
        out.append("no contact form")
    if checks.get("tel_link") is False:
        out.append("no tap-to-call link")
    score = checks.get("psi_mobile_score")
    if isinstance(score, (int, float)) and score < slow_below:
        out.append(f"slow on phones (Google speed score {int(score)}/100)")
    if checks.get("analytics_installed") is False:
        out.append("no analytics, so the ad spend is blind")
    return out


def paid_signals(checks, ads, transparency=None):
    """Signs of paid traffic: tags on their own page, then confirmed paid results, then
    Google's Ads Transparency Center. Only a measured yes counts; unmeasured is silence."""
    out = [AD_WORDS[k] for k, v in ((checks or {}).get("ad_signals") or {}).items()
           if v is True and k in AD_WORDS]
    for side in ("trade_ads", "name_ads"):
        s = (ads or {}).get(side)
        if isinstance(s, dict) and s.get("found"):
            out.append(f"seen in paid search results for \"{s.get('query')}\"")
    t = transparency or {}
    if t.get("measurable") and t.get("found"):
        out.append(f"{t.get('ads')} ads in Google's Ads Transparency Center"
                   + (f", last shown {t['last_shown'][:10]}" if t.get("last_shown") else ""))
    return out


def not_the_trade(place, industry):
    """A reason when Google files a place under the trade but it is something else: a dealer,
    a car wash, a manufacturer (sales rep review, 2026-09-27). From the industry file's
    `exclude_types` (Google place types) and `exclude_name_words`."""
    types = set((place.get("google") or {}).get("types") or [])
    hit = next((t for t in industry.get("exclude_types") or [] if t in types), None)
    if hit:
        return f"not a {industry.get('short') or 'shop in this trade'}: Google lists it as {hit.replace('_', ' ')}"
    name = f" {(place.get('name') or '').lower()} "
    word = next((w for w in industry.get("exclude_name_words") or [] if f" {w.lower()}" in name), None)
    if word:
        return f"not a {industry.get('short') or 'shop in this trade'}: the name says \"{word}\""
    return None


LANE_MODELS = ("web_presence", "facility")


def lane_model(industry_file):
    """Which lane rules an industry file asks for. Missing means `web_presence`, so every
    existing pool keeps its lanes (2026-10-01: a security integrator's pool needs different
    rules; a plant with a perfect website is still a lead there)."""
    model = (industry_file or {}).get("lane_model") or "web_presence"
    if model not in LANE_MODELS:
        raise ResearchUnavailable(f"Unknown lane_model {model!r}; one of {', '.join(LANE_MODELS)}")
    return model


def classify(place, scan, ads, industry, qualify, chain_reason=None, transparency=None,
             model="web_presence"):
    """(lane, reasons) for one discovered place. Pure, so every rule is tested offline.
    `model` picks the rules: `web_presence` (ours) or `facility` (a buyer of on-site work)."""
    if place.get("details") is False and not scan:
        return "unchecked", ["Google details not loaded yet: open the lead to fetch them"]
    if model == "facility":
        return classify_facility(place, scan, industry, qualify, chain_reason)
    if model != "web_presence":
        raise ValueError(f"unknown lane model {model!r}")
    return classify_web_presence(place, scan, ads, industry, qualify, chain_reason, transparency)


def classify_facility(place, scan, industry, qualify, chain_reason=None):
    """Lanes for a business that sells on-site work to facilities (a security integrator):

        multi_site    the same name or website at several places: a BIGGER account, ranked first
        fit           operating, the right trade, inside the rings; its website changes nothing
        unchecked     has a website nobody fetched yet, so it may be a head office elsewhere
        disqualified  not operating, the wrong trade, or no website AND reviews under the floor

    Reviews are the only size signal Places gives, and a plant can have few, so the floor is
    low, named per industry, and never applied to a place that has a website.
    """
    if place.get("business_status") not in (None, "", "OPERATIONAL"):
        return "disqualified", [f"not operating ({place['business_status'].lower()})"]
    if industry is None:
        return "disqualified", ["industry is marked out of the pool"]
    off = not_the_trade(place, industry)
    if off:
        return "disqualified", [off]
    if chain_reason:
        return "multi_site", [chain_reason]
    reviews = (place.get("google") or {}).get("review_count") or 0
    has_site = bool(place.get("url")) and not place.get("no_website")
    if not has_site:
        floor = int(industry.get("review_floor") or qualify.get("review_floor_default") or 3)
        photos, min_photos = (place.get("google") or {}).get("photo_count"), industry.get("min_photos")
        few_photos = min_photos is None or photos is None or int(photos) < int(min_photos)
        if reviews < floor and few_photos:
            return "disqualified", [f"no website and {reviews} reviews, under the floor of {floor}: "
                                    "too little sign of an operating facility"]
        return "fit", [f"operating with {reviews} Google reviews and no website"]
    if not scan:
        return "unchecked", ["has a website that has not been fetched yet"]
    return "fit", [f"operating facility with {reviews} Google reviews and a website"]


def classify_web_presence(place, scan, ads, industry, qualify, chain_reason=None, transparency=None):
    """The website-selling rules (docs/crm/crm-research-section-brief-2026-09-26.md §4.2)."""
    if place.get("business_status") not in (None, "", "OPERATIONAL"):
        return "disqualified", [f"not operating ({place['business_status'].lower()})"]
    if industry is None:
        return "disqualified", ["industry is marked out of the pool"]
    if chain_reason:
        return "disqualified", [chain_reason]
    off = not_the_trade(place, industry)
    if off:
        return "disqualified", [off]
    reviews = (place.get("google") or {}).get("review_count") or 0
    if place.get("no_website") or not place.get("url"):
        floor = int(industry.get("review_floor") or qualify.get("review_floor_default") or 15)
        if reviews >= floor:
            return "no_website", [f"operating with {reviews} Google reviews and no website"]
        return "disqualified", [f"no website and {reviews} reviews, under the floor of {floor}"]
    if not scan:
        return "unchecked", ["has a website that has not been scanned yet"]
    checks = scan.get("checks") or {}
    gaps = capture_gaps(checks, int(qualify.get("slow_mobile_below") or 50))
    paid = paid_signals(checks, ads, transparency)
    if paid and gaps:
        return "paid_weak", paid + gaps
    weak = [f"{k.replace('_', ' ')} graded {g.get('grade')}"
            for k, g in (scan.get("grades") or {}).items()
            if k in PRESENCE and isinstance(g, dict) and g.get("grade") in WEAK]
    if (scan.get("grades") or {}).get("site_down"):
        weak.append("website did not respond")
    if gaps or weak:
        why = gaps + [w for w in weak if w not in gaps]
        return "weak_site", (why + paid) if paid else why
    return "disqualified", ["clean scan: nothing for us to fix"]


def snapshot(prof, root, live=None):
    """Everything the Research section draws. Raises ResearchUnavailable when not configured.
    `live` ({place_id: Google row}) replaces the live source; None asks it."""
    cfg = config(prof, root)
    if cfg is None:
        raise ResearchUnavailable("Research is not configured for this business")
    ind = _read_json(cfg["industries"])
    if not isinstance(ind, dict) or not isinstance(ind.get("industries"), list):
        raise ResearchUnavailable("The industry file is missing or unreadable")
    qualify = ind.get("qualify") or {}
    model = lane_model(ind)
    industries = sorted(ind["industries"], key=lambda i: i.get("rank", 99))
    by_id = {i["id"]: i for i in industries}
    lanes = sorted(ind.get("lanes") or [], key=lambda l: l.get("rank", 99))
    lane_rank = {l["id"]: l.get("rank", 99) for l in lanes}

    discovered = _read_json(cfg["discovered"]) if cfg.get("discovered") else None
    stored = [p for p in (discovered or {}).values() if isinstance(p, dict) and p.get("place_id")]
    ledger = cfg.get("ledger")
    registry = (_read_json(ledger / "registry.json") if ledger else None) or {}
    by_place = {e["place_id"]: bid for bid, e in registry.items()
                if isinstance(e, dict) and e.get("place_id")}
    by_domain = {e["domain"]: bid for bid, e in registry.items()
                 if isinstance(e, dict) and e.get("domain")}
    center = (ind.get("office") or {}).get("center")
    live = live_details(prof, [p["place_id"] for p in stored]) if live is None else live
    places = [place_view(p, live.get(p["place_id"]), center,
                         (registry.get(by_place.get(p["place_id"], "")) or {}).get("url") or "")
              for p in stored]
    chain = chains([p for p in places if p["details"]], qualify)

    groups = {i["id"]: [] for i in industries}
    orphans = []
    for p in places:
        mine = [i for i in (p.get("industries") or []) if i in by_id]
        home = min(mine, key=lambda i: by_id[i].get("rank", 99)) if mine else None
        bid = by_place.get(p["place_id"]) or by_domain.get(p.get("domain") or "\0")
        scan = _last_line(ledger / "scans" / f"{bid}.jsonl") if (ledger and bid) else None
        ads = _last_line(ledger / "ads" / f"{bid}.jsonl") if (ledger and bid) else None
        tc = _last_line(ledger / "ads-transparency" / f"{bid}.jsonl") if (ledger and bid) else None
        lane, why = classify(p, scan, ads, by_id.get(home), qualify, chain.get(p["place_id"]), tc, model)
        g = p.get("google") or {}
        checks = (scan or {}).get("checks") or {}
        row = {
            "id": p["place_id"], "business_id": bid or "",
            "name": p.get("name") or p.get("domain") or "",  # no live details: the site we scanned
            "lane": lane, "reasons": why, "industry": home or "",
            "ring": p.get("ring"), "distance_mi": p.get("distance_mi"),
            "reviews": g.get("review_count"), "rating": g.get("rating"),
            "phone": g.get("phone") or "", "address": g.get("address") or "",
            "lat": (g.get("location") or {}).get("latitude"), "lng": (g.get("location") or {}).get("longitude"),
            "url": p.get("url") or "", "domain": p.get("domain") or "",
            "details": p["details"],
            "scanned_at": (scan or {}).get("scanned_at") or "",
            "worst_finding": (scan or {}).get("worst_finding") or "",
            "grades": {k: v.get("grade") for k, v in ((scan or {}).get("grades") or {}).items()
                       if isinstance(v, dict)},
            "ad_signals": [AD_WORDS[k] for k, v in (checks.get("ad_signals") or {}).items()
                           if v is True and k in AD_WORDS],
        }
        (groups[home] if home else orphans).append(row)

    def order(row):  # best lane first, then nearest (reps drop in), then the busiest
        return (lane_rank.get(row["lane"], 99),
                row["distance_mi"] if row["distance_mi"] is not None else 99,
                -(row["reviews"] or 0), row["name"].lower())

    out, totals = [], {l["id"]: 0 for l in lanes}
    for i in industries:
        rows = sorted(groups[i["id"]], key=order)
        counts = {l["id"]: sum(1 for r in rows if r["lane"] == l["id"]) for l in lanes}
        for k, v in counts.items():
            totals[k] = totals.get(k, 0) + v
        out.append({
            "id": i["id"], "rank": i.get("rank"), "label": i.get("label") or i["id"],
            "offer": i.get("offer") or "", "why": i.get("why") or "",
            "playbook": i.get("playbook") or "", "evidence": i.get("evidence") or "",
            "review_floor": i.get("review_floor"), "counts": counts, "companies": rows,
        })
    rings = [{"index": n, "label": f"{lo} to {hi} miles"}
             for n, (lo, hi) in enumerate(ind.get("rings_miles") or [])]
    office = ind.get("office") or {}
    return {
        "office": office.get("label") or "", "center_set": bool(office.get("center")),
        "center": office.get("center") or None,
        "rings": rings, "lanes": lanes, "industries": out, "totals": totals,
        "excluded": [{"label": x.get("label"), "reason": x.get("reason")}
                     for x in ind.get("excluded") or []],
        "unfiled": len(orphans),
        "last_seen": max((p.get("last_seen") or "" for p in places), default=""),
    }


def runs(prof, root):
    """Discovery's run log (`runs.jsonl`, beside the discovered file): one line per search run."""
    cfg = config(prof, root)
    path = cfg.get("discovered").parent / "runs.jsonl" if cfg and cfg.get("discovered") else None
    out = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines() if path else []
    except OSError:
        lines = []
    for ln in lines:
        try:
            row = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def searches_this_month(prof, root, month):
    """Places calls already made in `month` ("2026-09"): what the monthly cap counts."""
    return sum(int(r.get("calls") or 0) for r in runs(prof, root) if str(r.get("at", "")).startswith(month))


def searched(prof, root):
    """industry id -> the rings already searched at least once."""
    out = {}
    for r in runs(prof, root):
        for iid in r.get("industries") or []:
            if isinstance(r.get("ring"), int):
                out.setdefault(iid, set()).add(r["ring"])
    return {k: sorted(v) for k, v in out.items()}


def up_next(snap, claims, size, prof=None, root=None):
    """The short list a rep works from (Sam, 2026-09-27): per industry, the best `size` open
    leads, best lane first. Open = a working lane (rank 1 to 3), nobody holds it, not resting
    (prune() already took resting leads out). When one is taken or dropped the next moves up
    by itself, because this is computed on every read. The rest wait as the backlog."""
    held = {c["place_id"] for c in claims.get("claims", []) if c.get("live")}
    work = {l["id"] for l in snap.get("lanes", []) if (l.get("rank") or 99) <= 3}
    done = searched(prof, root) if prof is not None else {}
    for i in snap["industries"]:
        open_ = [c["id"] for c in i["companies"] if c["lane"] in work and c["id"] not in held]
        i["up_next"] = open_[:size]
        i["open_count"] = len(open_)
        i["backlog"] = max(0, len(open_) - size)
        i["searched_rings"] = done.get(i["id"], [])
    snap["up_next_size"] = size
    return snap


def find(prof, root, place_id):
    """One company in the pool, as the tree draws it, or None. Claims check it exists and
    is not disqualified, so a lead cannot be taken that the pool does not offer."""
    try:
        snap = snapshot(prof, root)
    except ResearchUnavailable:
        return None
    for i in snap["industries"]:
        for c in i["companies"]:
            if c["id"] == place_id:
                return c
    return None


def industry_file(prof, root):
    """The profile's industry file, read. Raises ResearchUnavailable when missing."""
    cfg = config(prof, root)
    data = _read_json(cfg["industries"]) if cfg else None
    if not isinstance(data, dict) or not isinstance(data.get("industries"), list):
        raise ResearchUnavailable("The industry file is missing or unreadable")
    return data
