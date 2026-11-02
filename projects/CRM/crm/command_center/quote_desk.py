"""Quote Desk, v1: an RFQ email in, a draft quote out, a person approves every quote.

Design: docs/handoffs/industrial/QD01-quote-desk-v1.md. On only when the profile says
`quote_desk.enabled: true`. Without it no quote table is created and no quote route answers, so a
business that is not a shop runs exactly as before.

This file is the pure part: no database, no network. It sorts an email, reads an RFQ, finds the
shop's most similar past jobs, prices a draft from the shop's own rates, and counts the KPIs.
quote_desk_store.py keeps the records and holds the approval gate. The steps any inbox desk shares
(sorting, the export-control stop, the gate, the log, KPI counting) are in inbox_core.py, with
nothing about machining in them; this file holds only what is about quoting a part.

The order of the read is the safety rule: `itar_marks` runs on the raw text FIRST. A marked RFQ
stops there, and its text never reaches an extractor, a model or a draft.

The extractor is pluggable (`quote_desk.extractor`): "demo" reads with fixed rules and needs no
key, so a demo never depends on one; "model" sends the text to an AI model and needs a key. Both
return the same shape, and `normalize` checks it, so a model can never add a field or skip a flag.

Every number in the engine comes from the profile (the shop's rates, materials, outside processes,
markup) or from its own past jobs. The engine names no shop, material price or vendor.
"""
import json
import math
import os
import re
from datetime import date, datetime, timezone
from urllib.request import Request, urlopen

from . import crm_store, inbox_core

KINDS = ("rfq", "po", "question", "other")
FIELDS = ("part_number", "revision", "description", "material", "quantities", "tolerances", "finish",
          "heat_treat", "plating", "certs", "due_date", "size")
REQUIRED = ("material", "quantities", "finish", "due_date")  # a profile may set its own list
ITAR = inbox_core.EXPORT_CONTROL  # the export-control stop is generic: inbox_core.py
CERTS = (("material certs", r"material cert|mill cert|\bMTR\b|material test report"),
         ("CoC", r"\bC\s?of\s?C\b|\bCoC\b|certificate of conformance|cert(?:ificate)? of compliance"),
         ("FAI", r"\bFAI\b|first article|AS9102"),
         ("PPAP", r"\bPPAP\b"),
         ("DFARS material", r"\bDFARS\b|domestic material"))
PART = re.compile(r"^\s*(?:(?:line|item)\s*\d+\s*[:.)-]\s*)?(?:P/?N|part\s*(?:number|no\.?|#)?)\s*[:#]?\s*"
                  r"(?=[A-Z._/-]*\d)([A-Z0-9][A-Z0-9._/-]{2,})", re.I | re.M)
MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                          "dec"))}


def config(prof):
    qd = prof.get("quote_desk")
    return qd if isinstance(qd, dict) and qd.get("enabled") is True else None


def enabled(prof):
    return config(prof) is not None


DeskError = crm_store.CRMError  # one error type, so the routes answer the desk's refusals like any other

def require(prof):
    cfg = config(prof)
    if cfg is None:
        raise DeskError("The Quote Desk is not enabled for this business", 404)
    return cfg


# ---- 1. Sort the email ------------------------------------------------------------------------

# Ordered: the first rule that matches wins; `unless` keeps a reply about a quote out of "po".
SORT_RULES = (
    {"kind": "po", "match": r"\bpurchase order\b|\bp\.?o\.?\s*(?:#|no\.?|number)\s*\w+",
     "unless": r"\brfq\b|request for quot"},
    {"kind": "rfq", "match": r"\brfq\b|request for quot|quote request|please quote|quote (?:on|for|this|these|the)\b|"
                             r"quotation|pricing (?:on|for)|\bbid\b|price (?:and|&) lead"},
    {"kind": "question", "match": r"\bstatus\b|where (?:is|are)|tracking|ship date|\bquestion\b|\?"},
)


def classify(subject, body):
    """RFQ, PO, question or other: this desk's ordered rules through inbox_core's sort."""
    return inbox_core.sort_email(subject, body, SORT_RULES)


def itar_marks(*texts):
    """Every ITAR, EAR or CUI mark in the text, as found. Any one stops the desk (v1 takes no such work)."""
    return inbox_core.stop_marks(*texts)


# ---- 2. Read the RFQ --------------------------------------------------------------------------

def _materials(prof):
    return (require(prof).get("materials") or {})


def _processes(prof):
    return (require(prof).get("outside_processes") or {})


def _num(s):
    return float("0" + s if s.startswith(".") else s)


def _due(text, received):
    """A due date as an ISO date. Reads 2026-11-20, 11/20, 11/20/26 and Nov 20. A month and day
    without a year is the next one after the email arrived."""
    m = re.search(r"(?:need(?:ed)?\s*(?:parts\s*)?by|due(?:\s*date)?|deliver(?:y)?(?:\s*by)?|required by|ship by)\s*[:\-]?\s*"
                  r"([^\n]{0,30})", text, re.I)
    if not m:
        return ""
    s = m.group(1)
    iso = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if iso:
        return iso.group(0)
    year = None
    md = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", s)
    if md:
        mo, d = int(md.group(1)), int(md.group(2))
        year = int(md.group(3)) if md.group(3) else None
    else:
        named = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b", s, re.I)
        if not named:
            return ""
        mo, d = MONTHS[named.group(1).lower()], int(named.group(2))
    if year is not None and year < 100:
        year += 2000
    try:
        when = date(year or received.year, mo, d)
        if year is None and when < received:
            when = date(received.year + 1, mo, d)
    except ValueError:
        return ""
    return when.isoformat()


def _size(text):
    """The part envelope in inches: L x W x H, or a diameter and a length (a round part)."""
    m = re.search(r"(\d*\.?\d+)\s*(?:\"|in)?\s*[xX×]\s*(\d*\.?\d+)\s*(?:\"|in)?\s*[xX×]\s*(\d*\.?\d+)", text)
    if m:
        return {"shape": "block", "dims": sorted((_num(g) for g in m.groups()), reverse=True)}
    m = re.search(r"(?:Ø|dia\.?|diameter)\s*(\d*\.?\d+)\s*(?:\"|in)?\s*[xX×]\s*(\d*\.?\d+)", text, re.I)
    if m:
        return {"shape": "round", "dims": [_num(m.group(1)), _num(m.group(2))]}
    return None


def _quantities(text):
    """Quantity breaks: "Qty 25 / 100 / 250", "quantities of 10, 25 and 50", "QTY: 200 pcs"."""
    m = re.search(r"\b(?:qty|qtys|quantity|quantities)\b\.?(?:\s*breaks?)?(?:\s*of)?\s*[:\-]?\s*([^\n]+)", text, re.I)
    if not m:
        return []
    line = re.split(r";|\.\s|\bneed|\bdue\b|\bmaterial|\bfinish|\brev\b", m.group(1), maxsplit=1, flags=re.I)[0]
    nums = sorted({int(n) for n in re.findall(r"(?<![\d.])(\d{1,6})(?!\d|\.\d)", line) if int(n) > 0})
    return nums[:6]


def demo_extract(prof, text, received):
    """The deterministic extractor: the rules a careful estimator reads a print with. Returns the
    header (fields every line shares) and one dict per part number."""
    received = received if isinstance(received, date) else date.fromisoformat(str(received)[:10])
    starts = [m for m in PART.finditer(text)]
    header = text[:starts[0].start()] if starts else text
    blocks = {}
    order = []
    for i, m in enumerate(starts):
        pn = m.group(1).upper().rstrip(".,")
        chunk = text[m.start(): starts[i + 1].start() if i + 1 < len(starts) else len(text)]
        if pn not in blocks:
            blocks[pn] = ""
            order.append(pn)
        blocks[pn] += "\n" + chunk
    if not order:
        order, blocks = [""], {"": ""}
    items = []
    for pn in order:
        own = blocks[pn]
        both = own + "\n" + header
        item = {k: v for k, v in _read_fields(prof, own, received).items() if v}
        for k, v in _read_fields(prof, both, received).items():  # the header fills what the part did not say
            item.setdefault(k, v)
        item["part_number"] = pn
        items.append({f: item.get(f, "" if f not in ("quantities", "tolerances", "certs") else []) for f in FIELDS}
                     | {"processes": item.get("processes", [])})
    return items


def _read_fields(prof, text, received):
    out = {}
    rev = re.search(r"\brev(?:ision)?\.?\s*[:#]?\s*([A-Z]{1,2}|\d{1,2})\b", text, re.I)
    out["revision"] = rev.group(1).upper() if rev else ""
    desc = re.search(r"(?:description|desc\.?|part name|title)\s*[:\-]\s*([^\n]+)", text, re.I)
    out["description"] = desc.group(1).strip().rstrip(".") if desc else ""
    low = text.lower()
    out["material"] = ""
    hits = []
    for key, mat in _materials(prof).items():
        for alias in [key] + list(mat.get("aliases") or []):
            m = re.search(r"(?<![a-z0-9-])" + re.escape(alias.lower()) + r"(?![0-9])", low)
            if m:
                hits.append((m.start(), -len(alias), key))
    if hits:
        out["material"] = sorted(hits)[0][2]
    else:
        raw = re.search(r"material\s*[:\-]\s*([^\n,;]+)", text, re.I)
        if raw and not re.search(r"\b(?:tbd|per print|see print|\?)", raw.group(1), re.I):
            out["material"] = "?" + raw.group(1).strip()  # named, but not in the shop's price list
    out["quantities"] = _quantities(text)
    tols = re.findall(r"(?:±|\+/-|\+-)\s*(\d*\.\d+)", text)
    out["tolerances"] = sorted({f"±{t if t.startswith('0') else '0' + t}" for t in tols}, key=lambda s: float(s[1:]))
    procs, finish, heat, plating = [], [], [], []
    for key, p in _processes(prof).items():
        if any(re.search(r"(?<![a-z])" + re.escape(phrase.lower()) + r"(?![a-z])", low) for phrase in p.get("match") or []):
            procs.append(key)
            {"heat_treat": heat, "plating": plating}.get(p.get("kind"), finish).append(p.get("label", key))
    if not finish and re.search(r"as[- ]machined|no finish|finish\s*[:\-]\s*none|bare\b|break all edges only", low):
        finish = ["As machined"]
    out["processes"], out["finish"] = procs, ", ".join(finish)
    out["heat_treat"], out["plating"] = ", ".join(heat), ", ".join(plating)
    out["certs"] = [name for name, pat in CERTS if re.search(pat, text, re.I)]
    out["due_date"] = _due(text, received)
    out["size"] = _size(text)
    return out


def model_extract(prof, text, received, complete=None):
    """The AI extractor. `complete(prompt) -> str` is the model call; the default calls the API named
    in the profile with the key from its env var. The answer goes through `normalize` like any other."""
    cfg = require(prof)
    if complete is None:
        key = os.environ.get(cfg.get("model_key_env") or "ANTHROPIC_API_KEY", "")
        if not key:
            raise DeskError("The AI reader has no key. Set it, or use the demo reader.", 503)
        complete = lambda prompt: _anthropic(prompt, key, cfg.get("model") or "claude-sonnet-5-5")
    mats = ", ".join(_materials(prof))
    procs = ", ".join(f"{k} ({p.get('label', k)})" for k, p in _processes(prof).items())
    prompt = ("Read this request for quote for a machine shop. Answer with JSON only: a list of line items, "
              f"each with the keys {', '.join(FIELDS)} and processes. material is one of: {mats}, or '?' and the "
              f"text when it is none of them, or '' when not stated. processes is a list of keys from: {procs}. "
              "quantities is a list of whole numbers. tolerances is a list like '±0.0005'. certs is a list from: "
              f"{', '.join(n for n, _ in CERTS)}. due_date is YYYY-MM-DD (the email arrived {received}). size is "
              "null or {\"shape\": \"block\"|\"round\", \"dims\": [inches]}. Leave a field empty when the RFQ does "
              "not say it. Never guess.\n\n" + text)
    try:
        items = json.loads(re.sub(r"^```(?:json)?|```$", "", complete(prompt).strip(), flags=re.M))
    except ValueError:
        raise DeskError("The AI reader did not answer in the expected shape. Read this one by hand.", 502) from None
    return items if isinstance(items, list) else [items]


def _anthropic(prompt, key, model):
    req = Request("https://api.anthropic.com/v1/messages", method="POST",
                  data=json.dumps({"model": model, "max_tokens": 4000,
                                   "messages": [{"role": "user", "content": prompt}]}).encode("utf-8"),
                  headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urlopen(req, timeout=60) as r:
        return "".join(b.get("text", "") for b in json.loads(r.read())["content"] if b.get("type") == "text")


def normalize(prof, items):
    """One shape out of any extractor: known fields only, known materials and processes only."""
    mats, procs = _materials(prof), _processes(prof)
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        clean = {}
        for f in ("part_number", "revision", "description", "finish", "heat_treat", "plating", "due_date"):
            v = it.get(f)
            clean[f] = str(v).strip()[:200] if isinstance(v, (str, int, float)) and v is not True else ""
        mat = str(it.get("material") or "").strip()
        clean["material"] = mat if mat in mats or mat.startswith("?") or not mat else "?" + mat
        clean["quantities"] = sorted({int(q) for q in it.get("quantities") or [] if str(q).isdigit() and int(q) > 0})[:6]
        clean["tolerances"] = [str(t)[:20] for t in it.get("tolerances") or [] if isinstance(t, str)][:10]
        clean["certs"] = [c for c in it.get("certs") or [] if c in {n for n, _ in CERTS}]
        clean["processes"] = [p for p in it.get("processes") or [] if p in procs]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", clean["due_date"]):
            clean["due_date"] = ""
        size = it.get("size")
        ok = (isinstance(size, dict) and size.get("shape") in ("block", "round") and isinstance(size.get("dims"), list)
              and len(size["dims"]) == (3 if size["shape"] == "block" else 2)
              and all(isinstance(d, (int, float)) and 0 < d < 240 for d in size["dims"]))
        clean["size"] = {"shape": size["shape"], "dims": [float(d) for d in size["dims"]]} if ok else None
        out.append(clean)
    return out


def missing(prof, item):
    """What the RFQ did not say that the shop needs to price it, in plain words."""
    need = require(prof).get("required") or list(REQUIRED)
    words = {"material": "material", "quantities": "quantity", "finish": "finish (or 'as machined')",
             "due_date": "due date", "revision": "drawing revision", "part_number": "part number",
             "size": "part size or a drawing", "certs": "which certs you need"}
    out = [words.get(f, f) for f in need if not item.get(f)]
    if str(item.get("material", "")).startswith("?"):
        out.append(f"material '{item['material'][1:]}' is not on our price list: please confirm the alloy and temper")
    return out


def question_text(prof, rfq, gaps):
    """The draft question to the buyer when specs are missing. A person sends it, never the desk."""
    cfg = require(prof)
    first = (rfq.get("sender_name") or "").split(" ")[0] or "there"
    lines = "\n".join(f"- {pn + ': ' if pn else ''}{g}" for pn, gs in gaps for g in gs)
    return (f"Hi {first},\n\nThanks for the RFQ. To quote it right we need a few details:\n\n{lines}\n\n"
            f"Once we have these we will turn the quote around quickly.\n\n{cfg.get('signature', '')}").strip()


# ---- 3. Similar past jobs ----------------------------------------------------------------------

def _envelope(size):
    if not size:
        return None
    d = size["dims"]
    return d[0] * d[1] * d[2] if size["shape"] == "block" else math.pi / 4 * d[0] ** 2 * d[1]


def _tokens(s):
    return {w for w in re.findall(r"[a-z]{3,}", (s or "").lower())} - {"the", "and", "with", "for", "per"}


def similar(prof, item, customer, jobs, n=3):
    """The `n` past jobs most like this line item, best first, each with its score (0 to 100) and why."""
    mats = _materials(prof)
    fam = lambda m: (mats.get(m) or {}).get("family", "")
    out = []
    pn = (item.get("part_number") or "").upper()
    for j in jobs:
        score, why = 0.0, []
        jpn = (j.get("part_number") or "").upper()
        if pn and jpn == pn:
            score += 45
            why.append("same part number")
        elif pn and jpn and jpn.rsplit("-", 1)[0] == pn.rsplit("-", 1)[0]:
            score += 10
            why.append("same part family")
        if customer and j.get("customer_domain") == customer:
            score += 12
            why.append("same customer")
        if item.get("material") and j.get("material") == item["material"]:
            score += 15
            why.append(f"same material ({item['material']})")
        elif item.get("material") and fam(item["material"]) and fam(j.get("material")) == fam(item["material"]):
            score += 7
            why.append(f"same family ({fam(item['material'])})")
        a, b = _envelope(item.get("size")), _envelope(j.get("size"))
        if a and b:
            r = min(a, b) / max(a, b)
            score += 15 * r
            if r >= 0.5:
                why.append("similar size")
        ip, jp = set(item.get("processes") or []), set(j.get("processes") or [])
        if ip or jp:
            share = len(ip & jp) / len(ip | jp)
            score += 8 * share
            if share and ip & jp:
                why.append("same outside work (" + ", ".join(_processes(prof).get(p, {}).get("label", p)
                                                               for p in sorted(ip & jp)) + ")")
        it, jt = _tokens(item.get("description")), _tokens(j.get("description"))
        if it and jt:
            share = len(it & jt) / len(it | jt)
            score += 5 * share
            if share >= 0.3:
                why.append("similar description")
        if score > 0:
            out.append({"job": j, "score": round(min(score, 100), 1), "why": why})
    out.sort(key=lambda s: s["job"].get("quoted_on", ""), reverse=True)  # a tie goes to the newest job
    out.sort(key=lambda s: -s["score"])
    return out[:n]


# ---- 4. Price the draft ------------------------------------------------------------------------

def money(x):
    return float(f"{x:.2f}")


def material_each(prof, material, size):
    """Stock for one part: the envelope plus the shop's stock allowance, at its price per pound plus
    its material markup. None when the material or the size is unknown."""
    cfg, mat = require(prof), _materials(prof).get(material)
    if not mat or not size:
        return None
    a = float(cfg.get("stock_allowance_in", 0.125))
    d = size["dims"]
    cubic = ((d[0] + a) * (d[1] + a) * (d[2] + a) if size["shape"] == "block"
             else (d[0] + a) ** 2 * (d[1] + a))  # round parts are cut from bar: a square of the diameter
    return money(cubic * mat["density"] * mat["per_lb"] * (1 + float(cfg.get("material_markup_pct", 0)) / 100))


def inputs_for(prof, item, matches):
    """The starting numbers for a line: run time from the best similar job, rates from the profile.
    The estimator owns run time on a new part; v1 never invents one from CAD."""
    cfg = require(prof)
    basis = matches[0]["job"] if matches and matches[0]["score"] >= float(cfg.get("basis_min_score", 25)) else None
    default = cfg.get("default_inputs") or {}
    machine = (basis or {}).get("machine") or default.get("machine") or next(iter(cfg.get("machines") or {"": 0}))
    setup = float((basis or {}).get("setup_hours", default.get("setup_hours", 2)))
    cycle = float((basis or {}).get("cycle_minutes", default.get("cycle_minutes", 20)))
    if basis and item.get("size") and basis.get("size"):  # scale run time by envelope, gently, never below half
        r = (_envelope(item["size"]) / _envelope(basis["size"])) ** (1 / 3)
        cycle = round(cycle * max(0.5, min(r, 2.0)), 1)
    return {"basis": (basis or {}).get("id", ""), "machine": machine, "setup_hours": setup, "cycle_minutes": cycle,
            "markup_pct": float(cfg.get("markup_pct", 20)),
            "material_each": material_each(prof, item.get("material"), item.get("size") or (basis or {}).get("size")),
            "overrides": {}}


def price(prof, item, inputs, basis_job=None):
    """One priced line per quantity break, with the source of every number. Pure arithmetic:
    unit = (setup / qty + run + material + outside work each + outside lot / qty) x (1 + markup),
    then raised so the line meets the shop's minimum order."""
    cfg = require(prof)
    machines, procs = cfg.get("machines") or {}, _processes(prof)
    m = machines.get(inputs["machine"])
    if not m:
        raise DeskError(f"Machine '{inputs['machine']}' is not in the shop rates", 400)
    rate = float(m["rate"])
    op_each = sum(float(procs[p].get("each", 0)) for p in item.get("processes") or [] if p in procs)
    op_lot = sum(float(procs[p].get("lot", 0)) for p in item.get("processes") or [] if p in procs)
    mat = inputs.get("material_each")
    minimum = float(cfg.get("minimum_order", 0))
    markup = float(inputs["markup_pct"])
    lines = []
    for q in item.get("quantities") or []:
        cost = inputs["setup_hours"] * rate / q + inputs["cycle_minutes"] / 60 * rate + (mat or 0) + op_each + op_lot / q
        unit = money(cost * (1 + markup / 100))
        note = ""
        if unit * q < minimum:
            unit, note = money(math.ceil(minimum / q * 100) / 100), f"raised to the ${minimum:,.0f} minimum order"
        over = (inputs.get("overrides") or {}).get(str(q))
        if over is not None:
            unit, note = money(float(over)), "set by the estimator"
        lines.append({"qty": q, "unit": unit, "total": money(unit * q), "cost_each": money(cost), "note": note})
    sources = [f"Rate: {m.get('label', inputs['machine'])} ${rate:,.2f}/h (shop rates)",
               f"Setup {inputs['setup_hours']:g} h, run {inputs['cycle_minutes']:g} min each " +
               (f"(from job {basis_job['id']}, {basis_job.get('description') or basis_job.get('part_number')})"
                if basis_job else "(shop default: no similar job, the estimator sets run time)"),
               f"Markup {markup:g}% (shop profile)"]
    if mat is not None:
        mt = _materials(prof).get(item.get("material"), {})
        sources.append(f"Material {item.get('material')}: ${mat:,.2f} each (${mt.get('per_lb')}/lb + "
                       f"{cfg.get('material_markup_pct', 0)}%, stock = part size + {cfg.get('stock_allowance_in', 0.125)} in)")
    else:
        sources.append("Material: no price yet (material or size unknown). The estimator adds it.")
    for p in item.get("processes") or []:
        if p in procs:
            sources.append(f"{procs[p].get('label', p)}: ${float(procs[p].get('lot', 0)):,.2f} lot + "
                           f"${float(procs[p].get('each', 0)):,.2f} each ({procs[p].get('vendor', 'vendor list')})")
    return {"lines": lines, "sources": sources, "material_unknown": mat is None}


def lead_days(prof, item):
    cfg, procs = require(prof), _processes(prof)
    return int(cfg.get("lead_days", 15)) + max([int(procs[p].get("days", 0)) for p in item.get("processes") or []
                                                if p in procs] or [0])


def draft_text(prof, rfq, number, items, lead):
    """The quote email in the shop's own words (`quote_desk.template`)."""
    cfg = require(prof)
    t = cfg.get("template") or {}
    first = (rfq.get("sender_name") or "").split(" ")[0] or "there"
    rows = []
    for it in items:
        label = " ".join(x for x in (it.get("part_number"), "Rev " + it["revision"] if it.get("revision") else "") if x)
        rows.append(f"{label or 'Part'}{' - ' + it['description'] if it.get('description') else ''}"
                    f"{' (' + it['material'] + ')' if it.get('material') and not it['material'].startswith('?') else ''}")
        for ln in it.get("lines") or []:
            rows.append(f"    Qty {ln['qty']:>5}:  ${ln['unit']:,.2f} each   ${ln['total']:,.2f}")
    body = (t.get("opening", "Hi {first},\n\nThank you for the opportunity. Our quote {number}:").format(first=first, number=number)
            + "\n\n" + "\n".join(rows) + "\n\n" + f"Lead time: {lead} working days after receipt of order."
            + ("\n" + t["notes"] if t.get("notes") else "") + "\n" + (cfg.get("terms") or "")
            + "\n\n" + (cfg.get("signature") or ""))
    return {"subject": t.get("subject", "Quote {number}: {subject}").format(number=number, subject=rfq.get("subject", "")),
            "body": body.strip()}


def fingerprint(draft):
    """What an approval approves: the prices, the lines, the words and who it goes to. Any change
    after approval changes this, and send refuses (inbox_core.check_gate)."""
    return inbox_core.fingerprint({
        "to": draft.get("to"), "subject": draft.get("subject"), "body": draft.get("body"),
        "lead_days": draft.get("lead_days"),
        "items": [{"part_number": i.get("part_number"), "lines": i.get("lines")} for i in draft.get("items") or []]})


# ---- 5. KPIs -----------------------------------------------------------------------------------

kpis = inbox_core.kpis  # counting is generic; the Quote Desk reads it as quotes


def local_date(prof, iso):
    return inbox_core.local_date(require(prof).get("timezone"), iso)


def utcnow():
    return datetime.now(timezone.utc)
