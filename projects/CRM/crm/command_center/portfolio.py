"""The portfolio layer: one sign-in, several businesses, one database each.

    python -m command_center.portfolio serve --root <root> --portfolio <id> --port 8880
    python -m command_center.portfolio add   --root <root> --portfolio <id> --source <business> --label "New business"
    python -m command_center.portfolio backup --root <root> --portfolio <id> --dir <folder>
    python -m command_center.portfolio sync   --root <live root> --portfolio <id> --from <release> [--access <json>]

A portfolio file (`<root>/data/command-center/portfolios/<id>.json`, design in
`docs/products/portfolio-file-2026-10-14.md`) lists businesses. Each one keeps its own profile
and its own database: the wall. This module never opens two businesses' records in one query
and never adds a business_id column. It mounts each business at `<prefix>b/<business_id>/`
and hands the request to the ordinary engine (`server.Handler`) with that business's own
`Config`, so everything under the mount behaves exactly as a one-business server.

A portfolio with one business shows no tabs, no color bar and no "+": today's CRM. With two or
more, `/` is the "All businesses" roll-up (portfolio_rollup.py), read from each business's own store.
"""
import argparse
import copy
import json
import os
import re
import sys
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from command_center import crm_store, identity, lead_intake, portfolio_export, portfolio_links, portfolio_rollup, profiles, server  # noqa: E402

PORTFOLIO_DIR = profiles.PROFILE_DIR / "portfolios"
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")
COLOR = re.compile(r"#[0-9a-fA-F]{6}")
PREFIX = re.compile(r"/(?:[a-zA-Z0-9_-]+/)*")
# What "+" leaves behind: blocks that point at the source business's own outside things
# (its website, phone line, content, research pool, payment and analytics accounts).
CRM_DROP = ("intake", "calls", "content", "research", "strategy", "strategy_beta", "payments",
            "analytics", "event_out", "slot")
TOP_DROP = ("client_buildout",)
STATIC = ("/portfolio.css", "/portfolio.js", "/portfolio-rollup.css")


class PortfolioError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def portfolio_path(portfolio_id, root):
    return Path(root) / PORTFOLIO_DIR / f"{portfolio_id}.json"


def _inside(root, rel):
    base = Path(root).resolve()
    f = (base / rel).resolve()
    if not isinstance(rel, str) or not rel or Path(rel).is_absolute() or base not in f.parents:
        raise PortfolioError(f"{rel!r} must be a path inside the portfolio root")
    return f


def load(portfolio_id, root):
    """Read and check one portfolio file. Raises rather than returning half a portfolio."""
    path = portfolio_path(portfolio_id, root)
    if not path.is_file():
        raise PortfolioError(f"no portfolio at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    where = str(path)
    if data.get("portfolio_id") != portfolio_id:
        raise PortfolioError(f"{where}: portfolio_id must be {portfolio_id!r}")
    if not isinstance(data.get("path_prefix", "/"), str) or not PREFIX.fullmatch(data.get("path_prefix", "/")):
        raise PortfolioError(f"{where}: path_prefix must be an absolute directory path")
    owners = data.get("owners")
    if not isinstance(owners, list) or not owners or not all(isinstance(o, str) for o in owners):
        raise PortfolioError(f"{where}: owners must be a non-empty list of person ids")
    _inside(root, data.get("database_dir", "private") + "/x")
    palette = data.get("palette", [])
    if not isinstance(palette, list) or not all(isinstance(c, str) and COLOR.fullmatch(c) for c in palette):
        raise PortfolioError(f"{where}: palette must be a list of #rrggbb colors")
    if not isinstance(data.get("timezone", ""), str):
        raise PortfolioError(f"{where}: timezone must be a name like America/Los_Angeles")
    items = data.get("businesses")
    if not isinstance(items, list) or not items:
        raise PortfolioError(f"{where}: a portfolio needs at least one business")
    seen = {"business_id": set(), "color": set(), "database": set()}
    for b in items:
        bid = b.get("business_id") if isinstance(b, dict) else None
        if not isinstance(bid, str) or not ID.fullmatch(bid):
            raise PortfolioError(f"{where}: business_id {bid!r} must be lowercase letters, digits and dashes")
        label = b.get("label")
        if not isinstance(label, str) or not label.strip() or len(label) > 40:
            raise PortfolioError(f"{where}: {bid} needs a label of 1 to 40 characters")
        if not isinstance(b.get("color"), str) or not COLOR.fullmatch(b["color"]):
            raise PortfolioError(f"{where}: {bid} color must be #rrggbb")
        db = b.get("database")
        if not isinstance(db, str) or not db.endswith(".sqlite3"):
            raise PortfolioError(f"{where}: {bid} database must be a .sqlite3 path")
        _inside(root, db)
        for key in seen:
            value = b[key].lower() if key == "color" else b[key]
            if value in seen[key]:
                raise PortfolioError(f"{where}: two businesses share the {key.replace('_', ' ')} {b[key]!r}")
            seen[key].add(value)
        prof = profiles.load(bid, root)
        if prof["business_id"] != bid:
            raise PortfolioError(f"{where}: profile for {bid} names {prof['business_id']!r}")
        for o in owners:
            if identity.person(prof, o) is None:
                raise PortfolioError(f"{where}: owner {o!r} is not a person in {bid}")
    data["_path"] = str(path)
    return data


def _write(path, data):
    """One atomic replace, so a reader never sees half a file."""
    clean = {k: v for k, v in data.items() if not k.startswith("_")}
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(clean, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _slug(label, taken, root):
    base = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:36].strip("-") or "business"
    bid, n = base, 2
    while bid in taken or profiles.profile_path(bid, root).exists():
        bid, n = f"{base}-{n}", n + 1
    return bid


def add_business(portfolio_id, root, source, label, color=None):
    """'+': a new business with the source's setup and an empty database. Returns its entry.

    Copies people, roles, sign-in, pipeline stages, task categories and departments. Copies
    no record. The portfolio file is rewritten last, so a failure before it leaves no tab.
    """
    data = load(portfolio_id, root)
    label = (label or "").strip() if isinstance(label, str) else ""
    if not label or len(label) > 40:
        raise PortfolioError("Name the business (1 to 40 characters)")
    ids = [b["business_id"] for b in data["businesses"]]
    if source not in ids:
        raise PortfolioError("Copy the setup of a business in this portfolio", 404)
    used = {b["color"].lower() for b in data["businesses"]}
    if color:
        if not isinstance(color, str) or not COLOR.fullmatch(color):
            raise PortfolioError("A color is #rrggbb")
        if color.lower() in used:
            raise PortfolioError("Another business already has that color", 409)
    else:
        color = next((c for c in data.get("palette", []) if c.lower() not in used), None)
        if color is None:
            raise PortfolioError("Every palette color is taken: pick a color", 409)
    bid = _slug(label, ids, root)
    src = json.loads(profiles.profile_path(source, root).read_text(encoding="utf-8"))
    prof = {k: copy.deepcopy(v) for k, v in src.items() if not k.startswith("_") and k not in TOP_DROP}
    prof["crm"] = {k: v for k, v in (prof.get("crm") or {}).items() if k not in CRM_DROP}
    prof["crm"]["accent"] = color
    prof.update(business_id=bid, name=label)
    profiles.validate_profile(prof, f"copy of {source}")
    db_rel = f"{data.get('database_dir', 'private')}/{bid}.sqlite3"
    db = _inside(root, db_rel)
    if db.exists():
        raise PortfolioError(f"A database already exists at {db_rel}", 409)
    ppath = profiles.profile_path(bid, root)
    with open(ppath, "x", encoding="utf-8") as f:  # never overwrite a profile
        f.write(json.dumps(prof, indent=1) + "\n")
    db.parent.mkdir(parents=True, exist_ok=True)
    crm_store.Store(str(db), bid)  # creates the empty, walled database
    entry = {"business_id": bid, "label": label, "color": color, "database": db_rel}
    data["businesses"].append(entry)
    _write(portfolio_path(portfolio_id, root), data)
    return entry


def backup(portfolio_id, root, directory):
    """An online backup of every business's database and the link file, one stamp for all.
    Returns the files made. The nightly timer on a client's server runs this (deploy/client-crm)."""
    data = load(portfolio_id, root)
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    made = []
    for b in data["businesses"]:
        target = out / f"{b['business_id']}-{stamp}.sqlite3"
        crm_store.Store(str(_inside(root, b["database"])), b["business_id"]).backup(target)
        made.append(target)
    links = Path(root) / data.get("database_dir", "private") / f"{portfolio_id}-links.sqlite3"
    if links.is_file():
        target = out / f"{portfolio_id}-links-{stamp}.sqlite3"
        with closing(sqlite3.connect(links)) as src, closing(sqlite3.connect(target)) as dest:
            src.backup(dest)
        made.append(target)
    return made


BRAND_SUFFIXES = (".css", ".svg", ".png", ".woff2")


def sync(portfolio_id, root, source, access=None):
    """Bring the setup from a new release (`source`, the client's crm/ folder) into the live root.

    The client's repo is the truth for the businesses it names: their profiles and brand files are
    replaced. Sign-in values stay the server's (`access`, else each live profile's own block).
    A business added on the server with "+" is kept, profile and tab. Databases and the link file are
    never touched. Written only after the new setup passes `load`; on a failure every file is put
    back. Returns the list of changes, for the log."""
    root, source = Path(root), Path(source)
    new_pf = json.loads(portfolio_path(portfolio_id, source).read_text(encoding="utf-8"))
    live_pf = json.loads(portfolio_path(portfolio_id, root).read_text(encoding="utf-8"))
    ours = {b["business_id"] for b in new_pf["businesses"]}
    kept = [b for b in live_pf["businesses"] if b["business_id"] not in ours]
    new_pf["businesses"] = new_pf["businesses"] + kept
    writes, changes = {}, []
    for b in new_pf["businesses"]:
        if b["business_id"] not in ours:
            changes.append(f"kept {b['business_id']} (added on the server)")
            continue
        prof = json.loads(profiles.profile_path(b["business_id"], source).read_text(encoding="utf-8"))
        live = profiles.profile_path(b["business_id"], root)
        old = json.loads(live.read_text(encoding="utf-8")) if live.is_file() else {}
        prof["access"] = {**(access or old.get("access") or prof.get("access") or {}),
                          "local_person": (prof.get("access") or {}).get("local_person", "")}
        if access or old.get("access", {}).get("identity") == "cloudflare_access":
            prof.setdefault("crm", {})["demo"] = False
        writes[live] = json.dumps(prof, indent=1) + "\n"
        changes.append(("updated " if live.is_file() else "added ") + b["business_id"])
    writes[portfolio_path(portfolio_id, root)] = json.dumps(new_pf, indent=2) + "\n"
    for f in source.iterdir():
        if f.is_file() and f.suffix in BRAND_SUFFIXES:
            writes[root / f.name] = f.read_bytes()
    before = {f: (f.read_bytes() if f.is_file() else None) for f in writes}
    try:
        for f, data in writes.items():
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = Path(str(f) + ".tmp")
            tmp.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
            os.replace(tmp, f)
        load(portfolio_id, root)
    except Exception:
        for f, data in before.items():
            if data is None:
                f.unlink(missing_ok=True)
            else:
                f.write_bytes(data)
        raise
    return changes


# ---------------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------------

class Portfolio:
    """The live portfolio on a server: the file, one engine Config per business, one lock."""

    def __init__(self, portfolio_id, root, host, identity_mode=None):
        self.id, self.root, self.host, self.override = portfolio_id, root, host, identity_mode
        self.lock = threading.Lock()
        self.configs, self.data, self.mtime = {}, None, None
        self.refresh()

    def refresh(self):
        """Reload when the file changed (a '+' here or from the command line)."""
        path = portfolio_path(self.id, self.root)
        mtime = path.stat().st_mtime_ns
        if mtime == self.mtime:
            return self.data
        with self.lock:
            data = load(self.id, self.root)
            for b in data["businesses"]:
                if b["business_id"] in self.configs:
                    continue
                prof = profiles.load(b["business_id"], self.root)
                mode = identity.check_startup(prof, self.host, override=self.override)
                store = crm_store.Store(str(_inside(self.root, b["database"])), b["business_id"])
                try:
                    intake = lead_intake.config(prof)
                except ValueError as e:
                    raise identity.StartupRefused(f"{b['business_id']} lead intake: {e}") from None
                self.configs[b["business_id"]] = server.Config(b["business_id"], self.root, mode, store, intake)
            self.data, self.mtime = data, mtime
        return data

    def entry(self, bid):
        return next((b for b in self.data["businesses"] if b["business_id"] == bid), None)

    def prefix(self):
        return self.data.get("path_prefix", "/")

    def visible(self, headers, client_address):
        """[(entry, person)] for every business this sign-in is somebody in, in tab order."""
        out = []
        for b in self.data["businesses"]:
            cfg = self.configs[b["business_id"]]
            try:
                prof = profiles.load(b["business_id"], self.root)
                out.append((b, identity.resolve(headers, client_address, prof, mode=cfg.mode)))
            except (identity.Unauthenticated, identity.NotAPerson):
                continue
            except profiles.SchemaError:  # a broken profile closes its own tab, not every tab
                continue
        return out


class Handler(server.Handler):
    """Routes `<prefix>b/<id>/...` to the engine with that business's Config. Nothing else
    in the engine knows a portfolio exists."""

    _cc = None
    _biz = None

    @property
    def cc(self):
        return self._cc

    def _pf(self):
        return self.server.portfolio

    def _refuse(self, code, msg, as_json=True):
        if as_json:
            return self._send(code, json.dumps({"error": msg}), server.TYPES[".json"], server.PRIVATE)
        return self._send(code, msg + "\n", "text/plain; charset=utf-8", server.PRIVATE)

    def _mount(self):
        """Pick the business from the path. True when the request now belongs to one."""
        pf = self._pf()
        pf.refresh()
        u = urlparse(self.path)
        m = re.fullmatch(r"/b/([a-z0-9][a-z0-9-]{0,40})(/.*)?", u.path)
        if not m or pf.entry(m[1]) is None:
            return False
        self._biz, self._cc = pf.entry(m[1]), pf.configs[m[1]]
        self.path = (m[2] or "/") + (("?" + u.query) if u.query else "")
        self._bare = m[2] is None
        return True

    def _who(self, as_json):
        who = super()._who(as_json)
        if who is None:
            return None
        prof, person = who
        pf, b = self._pf(), self._biz
        crm = dict(prof.get("crm") or {})
        crm["path_prefix"] = f"{pf.prefix()}b/{b['business_id']}/"
        if len(pf.data["businesses"]) > 1:
            crm["accent"] = b["color"]
            prof["_theme_key"] = f"portfolio.{pf.id}.theme"  # one light/gray/dark choice for every tab
            if urlparse(self.path).path.rstrip("/") in ("", "/strategy"):
                prof["_portfolio"] = self._tabs(person)
        prof["crm"] = crm
        return prof, person

    def _tabs(self, person, seen=None, active=None):
        pf = self._pf()
        seen = pf.visible(self.headers, self.client_address) if seen is None else seen
        owner = person.id in pf.data["owners"]
        used = {b["color"].lower() for b in pf.data["businesses"]}
        active = self._biz if active is None else active  # False: the All businesses page
        return {"active": active["business_id"] if active else "", "color": active["color"] if active else "#57534e",
                "home": len(seen) > 1,
                "tabs": [{"id": b["business_id"], "label": b["label"], "color": b["color"]} for b, _ in seen],
                "can_add": owner, "add_url": f"{pf.prefix()}portfolio/api/add",
                "links_url": f"{pf.prefix()}portfolio/api/links",
                "export_url": f"{pf.prefix()}portfolio/api/export" if owner else "",
                "palette": [c for c in pf.data.get("palette", []) if c.lower() not in used]}

    def _anyone(self):
        """(entry, person) of the first business this sign-in reaches, or None after refusing."""
        pf = self._pf()
        seen = pf.visible(self.headers, self.client_address)
        if seen:
            return seen[0]
        # Same answer the engine gives: 401 with no proof, 403 for nobody we know.
        first = pf.data["businesses"][0]
        self._biz, self._cc = first, pf.configs[first["business_id"]]
        super()._who(as_json=True)
        return None

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/healthz":
            return self._send(200, "ok", "text/plain; charset=utf-8")
        if self._mount():
            if self._bare:  # relative URLs need the trailing slash
                return self._send(302, "", headers={**server.PRIVATE, "Location": f"{self._pf().prefix()}b/{self._biz['business_id']}/"})
            if urlparse(self.path).path in STATIC:
                if self._who(as_json=False) is None:
                    return None
                return self._static(urlparse(self.path).path.lstrip("/"))
            return super().do_GET()
        self._pf().refresh()
        first = self._anyone()
        if first is None:
            return None
        seen = self._pf().visible(self.headers, self.client_address)
        if path in ("/", "/portfolio/api/rollup") and len(seen) > 1:
            return self._rollup(seen, as_json=path != "/")
        if path == "/":
            return self._send(302, "", headers={**server.PRIVATE, "Location": f"{self._pf().prefix()}b/{first[0]['business_id']}/"})
        if path == "/portfolio/api/rollup":
            return self._refuse(404, "There is one business to see: open its tab")
        if path in ("/portfolio/api/links", "/portfolio/api/links/suggest"):
            q = parse_qs(urlparse(self.path).query)
            bid, cid = (q.get("business") or [""])[0], (q.get("contact") or [""])[0]
            try:
                if path.endswith("/suggest"):
                    portfolio_links.owner_only(self._pf(), seen[0][1])
                    body = {"suggestions": portfolio_links.suggest(self._pf(), seen, bid, cid)}
                else:
                    body = {"also_in": portfolio_links.also_in(self._pf(), seen, bid, cid),
                            "can_link": seen[0][1].id in self._pf().data["owners"]}
            except portfolio_links.LinkError as e:
                return self._refuse(e.status, str(e))
            return self._send(200, json.dumps(body), server.TYPES[".json"], server.PRIVATE)
        return self._send(404, "Not found", "text/plain", server.PRIVATE)

    def _rollup(self, seen, as_json):
        """All businesses, side by side, each read as this person (portfolio_rollup.py)."""
        pf = self._pf()
        data = portfolio_rollup.read(pf, seen)
        if as_json:
            return self._send(200, json.dumps(data), server.TYPES[".json"], server.PRIVATE)
        first, person = seen[0]
        stand_in = {"name": data["name"], "_portfolio": self._tabs(person, seen, active=False)}
        page = portfolio_rollup.render(data, stand_in, f"{pf.prefix()}b/{first['business_id']}/",
                                       f"portfolio.{pf.id}.theme")
        return self._send(200, page, "text/html; charset=utf-8", server.PRIVATE)

    def do_OPTIONS(self):
        if self._mount():
            return super().do_OPTIONS()
        return self._send(404, "Not found", "text/plain; charset=utf-8")

    def do_POST(self):
        if self._mount():
            return super().do_POST()
        try:  # drain first, as the engine does: an early answer on a keep-alive socket breaks it
            n = int(self.headers.get("Content-Length") or 0)
            if n < 0 or n > 8_000:
                self.close_connection = True
                raise ValueError("body too large")
            raw = self.rfile.read(n)
        except ValueError as e:
            return self._refuse(400, str(e))
        pf = self._pf()
        pf.refresh()
        route = urlparse(self.path).path.rstrip("/") or "/"
        if route == "/portfolio/api/links":
            return self._links_post(raw)
        if route == "/portfolio/api/export":
            return self._export_post(raw)
        if route != "/portfolio/api/add":
            if self._anyone() is None:
                return None
            return self._refuse(404, "Not found")
        try:
            body = json.loads(raw or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._refuse(400, "Invalid request")
        source = pf.entry(body.get("source") if isinstance(body.get("source"), str) else "")
        if source is None:
            return self._refuse(404, "Copy the setup of a business in this portfolio")
        self._biz, self._cc = source, pf.configs[source["business_id"]]
        who = server.Handler._who(self, as_json=True)  # sign-in against the source business
        if who is None:
            return None
        prof, person = who
        bad = self._reject_cross_origin(prof)
        if bad:
            return self._refuse(403, bad)
        if person.id not in pf.data["owners"]:
            return self._refuse(403, "Only the owner can add a business")
        try:
            with pf.lock:
                entry = add_business(pf.id, pf.root, source["business_id"], body.get("label"), body.get("color") or None)
            pf.refresh()
        except PortfolioError as e:
            return self._refuse(e.status, str(e))
        except profiles.SchemaError as e:
            return self._refuse(400, str(e))
        return self._send(200, json.dumps({**entry, "url": f"{pf.prefix()}b/{entry['business_id']}/"}),
                          server.TYPES[".json"], server.PRIVATE)


    def _links_post(self, raw):
        """Link or unlink people across businesses (portfolio_links.py). Owners only."""
        pf = self._pf()
        first = self._anyone()
        if first is None:
            return None
        seen = pf.visible(self.headers, self.client_address)
        self._biz, self._cc = first[0], pf.configs[first[0]["business_id"]]  # the origin check reads its Config
        prof = profiles.load(first[0]["business_id"], pf.root)
        bad = self._reject_cross_origin(prof)
        if bad:
            return self._refuse(403, bad)
        try:
            body = json.loads(raw or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._refuse(400, "Invalid request")
        try:
            portfolio_links.owner_only(pf, first[1])
            with pf.lock:
                if body.get("action") == "link":
                    out = portfolio_links.link(pf, seen, first[1].id, body.get("a"), body.get("b"))
                elif body.get("action") == "unlink":
                    out = portfolio_links.unlink(pf, seen, first[1].id, body.get("business"), body.get("contact"))
                else:
                    raise portfolio_links.LinkError("Say link or unlink")
        except portfolio_links.LinkError as e:
            return self._refuse(e.status, str(e))
        return self._send(200, json.dumps(out), server.TYPES[".json"], server.PRIVATE)


    def _export_post(self, raw):
        """The handover file for one business (portfolio_export.py). Owner and admin only."""
        pf = self._pf()
        first = self._anyone()
        if first is None:
            return None
        seen = pf.visible(self.headers, self.client_address)
        try:
            body = json.loads(raw or b"{}")
            bid = body.get("business") if isinstance(body, dict) else None
        except ValueError:
            return self._refuse(400, "Invalid request")
        entry = pf.entry(bid) if isinstance(bid, str) else None
        if entry is None:
            return self._refuse(404, "Business not found")
        self._biz, self._cc = entry, pf.configs[entry["business_id"]]
        bad = self._reject_cross_origin(profiles.load(entry["business_id"], pf.root))
        if bad:
            return self._refuse(403, bad)
        try:
            name, data, _ = portfolio_export.handover(pf, seen, entry["business_id"], first[1])
        except portfolio_export.ExportError as e:
            return self._refuse(e.status, str(e))
        return self._send(200, data, "application/zip",
                          {**server.PRIVATE, "Content-Disposition": f'attachment; filename="{name}"'})


def make_server(portfolio_id, root=".", host="127.0.0.1", port=0, identity_mode=None):
    """A bound, unstarted portfolio server. Refuses a broken portfolio or profile before binding."""
    pf = Portfolio(portfolio_id, root, host, identity_mode)
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.portfolio = pf
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser(description="Serve or grow a portfolio of businesses.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--portfolio", required=True)
    s.add_argument("--root", default=".")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8880)
    s.add_argument("--identity", choices=identity.MODES, default=None)
    s.add_argument("--verbose", action="store_true")
    a2 = sub.add_parser("add")
    a2.add_argument("--portfolio", required=True)
    a2.add_argument("--root", default=".")
    a2.add_argument("--source", required=True)
    a2.add_argument("--label", required=True)
    a2.add_argument("--color")
    b = sub.add_parser("backup")
    b.add_argument("--portfolio", required=True)
    b.add_argument("--root", default=".")
    b.add_argument("--dir", required=True)
    sy = sub.add_parser("sync")
    sy.add_argument("--portfolio", required=True)
    sy.add_argument("--root", required=True)
    sy.add_argument("--from", dest="source", required=True)
    sy.add_argument("--access")
    a = ap.parse_args(argv)
    if a.cmd == "sync":
        access = json.loads(Path(a.access).read_text(encoding="utf-8")) if a.access else None
        try:
            for line in sync(a.portfolio, a.root, a.source, access):
                print(line)
        except (PortfolioError, profiles.SchemaError, OSError, ValueError) as e:
            raise SystemExit(f"portfolio: sync refused, nothing changed: {e}")
        return
    if a.cmd == "backup":
        try:
            for f in backup(a.portfolio, a.root, a.dir):
                print(f)
        except (PortfolioError, profiles.SchemaError, crm_store.CRMError) as e:
            raise SystemExit(f"portfolio: {e}")
        return
    if a.cmd == "add":
        try:
            entry = add_business(a.portfolio, a.root, a.source, a.label, a.color)
        except (PortfolioError, profiles.SchemaError, FileExistsError) as e:
            raise SystemExit(f"portfolio: {e}")
        print(f"added {entry['business_id']} ({entry['label']}, {entry['color']}) -> {entry['database']}")
        return
    try:
        srv = make_server(a.portfolio, a.root, a.host, a.port, a.identity)
    except (PortfolioError, profiles.SchemaError, identity.StartupRefused) as e:
        raise SystemExit(f"portfolio: {e}")
    pf = srv.portfolio
    print(f"Portfolio {pf.data.get('name') or pf.id}: {len(pf.data['businesses'])} businesses")
    for b in pf.data["businesses"]:
        print(f"  {b['business_id']:<28} {b['color']}  {b['database']}")
    print(f"  serving    http://{a.host}:{srv.server_address[1]}{pf.prefix()}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
