"""The prep package for one Research company: facts, email draft, checklist, and the checks.

Pure functions only: no database, no network. The research document itself is written by a
model outside the web request (`tools/research_doc.py`); this module builds the facts it is
allowed to use and checks what comes back.

Rules (docs/crm/crm-research-section-brief-2026-09-26.md §4.3):
- Every fact comes from our own records: the discovered place and the scan ledger.
  A playbook pain is labelled "industry pattern" with its source, never as their finding.
- The email opens with one computed fact about THEIR business.
- Zero em dashes. No banned stat (knowledge/industries/README.md). No revenue figure.
- Commercial email carries the sender, a real postal address and a way to opt out
  (CAN-SPAM). Without the address the draft says it is not ready to send.
- Nothing here sends anything.
"""
import json
import re
from pathlib import Path

EM_DASH = "—"


def plain(text):
    """Our scanner writes its findings with em dashes; client-facing text never has one.
    " — " becomes a colon, which reads right in every finding sentence we write."""
    return re.sub(r"\s*—\s*", ": ", text or "")


def check_text(text, banned):
    """Problems with a piece of client-facing text. An empty list means it passes."""
    problems = []
    if EM_DASH in (text or ""):
        problems.append("contains an em dash")
    low = (text or "").lower()
    for phrase in banned or []:
        if phrase.lower() in low:
            problems.append(f"contains a banned phrase: {phrase}")
    return problems


def banned_phrases(cfg):
    return list(((cfg or {}).get("banned_phrases") or {}).get("phrases") or [])


def playbook_patterns(root, playbook, limit=2500):
    """Section 3 of an industry playbook ("The pains, graded"), for the document to quote
    as industry pattern. Empty when the industry has no playbook yet."""
    if not playbook:
        return ""
    try:
        text = (Path(root) / playbook).read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r"^#+\s*3\b.*?$(.*?)(?=^#+\s*4\b|\Z)", text, re.M | re.S)
    return (m.group(1).strip() if m else "")[:limit]


def facts(company, industry, lane, root="."):
    """Everything the document may say, and nothing else."""
    return {
        "name": company["name"],
        "industry": industry.get("label", ""),
        "lane": lane.get("label", company["lane"]),
        "lane_pitch": lane.get("pitch", ""),
        "why_this_lane": list(company.get("reasons") or []),
        "distance_miles_from_our_office": company.get("distance_mi"),
        "google_reviews": company.get("reviews"),
        "google_rating": company.get("rating"),
        "website": company.get("domain") or "none",
        "scanned_at": (company.get("scanned_at") or "")[:10],
        "scan_grades": company.get("grades") or {},
        "top_finding": plain(company.get("worst_finding")),
        "paid_traffic_signals": list(company.get("ad_signals") or []),
        "industry_pattern": {
            "source": industry.get("playbook") or industry.get("evidence") or "",
            "text": playbook_patterns(root, industry.get("playbook")),
        },
    }


def opening_fact(company):
    """The one computed fact the email leads with. Always about them, always measured."""
    if company["lane"] == "no_website":
        n = company.get("reviews")
        return (f"{company['name']} has {n} Google reviews, and a customer who looks you up "
                "finds no website to go to." if n else
                f"A customer who looks up {company['name']} finds no website to go to.")
    finding = plain(company.get("worst_finding")).strip()
    if finding:
        return finding
    reasons = company.get("reasons") or []
    return (f"When we checked {company.get('domain') or 'your site'}, we found: {reasons[0]}."
            if reasons else "")


def email_draft(company, lane, sender, person_name, banned):
    """The first email, as a draft to copy. Never sent from here."""
    first = opening_fact(company)
    subject = f"Something we noticed about {company['name']}"
    pitch = lane.get("pitch") or ""
    body = "\n".join(line for line in [
        "Hi,",
        "",
        first,
        "",
        pitch,
        "We are a local team a few miles from you. We fix this kind of thing for small "
        "businesses, and the first look costs nothing.",
        "",
        "Would a 15-minute call this week be useful?",
        "",
        person_name,
        sender.get("business") or "",
        sender.get("website") or "",
        sender.get("address") or "[Postal address missing]",
        "",
        "If you would rather not hear from us, reply \"no thanks\" and we will not write again.",
    ] if line is not None)
    missing = [] if sender.get("address") else ["the business postal address (profile crm.research.sender.address)"]
    problems = check_text(subject + "\n" + body, banned)
    return {"subject": subject, "body": body, "opening_fact": first,
            "ready": not missing and not problems and bool(first),
            "missing": missing, "problems": problems}


def phone_script(cfg, company, lane_id, person_name):
    """The call script for this company, filled in, or None for a lane with no script.
    Its words come from the industry file (`scripts`), so Sam edits them there."""
    spec = (cfg or {}).get("scripts") or {}
    fact = (spec.get("facts") or {}).get(lane_id)
    if not fact:
        return None
    first = (person_name or "").split(" ")[0]
    # The problem to name is a gap in the site, never the ad signal that put them in the lane.
    signal = ("tag on the site", "pixel on the site", "call tracking", "paid search", "Ads Transparency")
    gaps = [r for r in company.get("reasons") or [] if not any(w in r for w in signal)]
    finding = (plain(company.get("worst_finding")).strip() if lane_id != "paid_weak" or not gaps else "")         or (gaps[0][0].upper() + gaps[0][1:] if gaps else "")
    fill = lambda text: (text or "").format_map({"me": first, "company": company["name"],
                                                  "reviews": company.get("reviews") or "your",
                                                  "finding": finding.rstrip(".") + "."})
    return {"status": spec.get("status", "draft"), "opener": fill(spec.get("opener")), "fact": fill(fact),
            "questions": [fill(q) for q in spec.get("questions") or []], "ask": fill(spec.get("ask")),
            "objections": [{"q": fill(o.get("q")), "a": fill(o.get("a"))} for o in spec.get("objections") or []],
            "voicemail": fill(spec.get("voicemail"))}


def checklist(cfg, lane_id):
    steps = list(((cfg or {}).get("checklists") or {}).get(lane_id) or [])
    if not steps or steps[-1] != "Record outcome":
        steps.append("Record outcome")
    return steps


def sections(cfg):
    return list(((cfg or {}).get("document") or {}).get("sections") or [])


def check_document(text, cfg):
    """Problems with a written research document. Every template section must be there."""
    problems = check_text(text, banned_phrases(cfg))
    for s in sections(cfg):
        if not re.search(r"^#+\s*" + re.escape(s) + r"\s*$", text or "", re.M):
            problems.append(f"missing section: {s}")
    return problems


SCRIPT_KEYS = ("opener", "fact", "questions", "ask", "objections", "voicemail")


def playbook_language(root, playbook, limit=1500):
    """Section 4 of an industry playbook ("Language that resonates"): their own words."""
    if not playbook:
        return ""
    try:
        text = (Path(root) / playbook).read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r"^#+\s*4\b.*?$(.*?)(?=^#+\s*5\b|\Z)", text, re.M | re.S)
    return (m.group(1).strip() if m else "")[:limit]


def script_prompt(fact_pack, cfg, person_name, language=""):
    """(system, user) for a phone script written for THIS lead (Sam, 2026-09-27: "writing it in
    a smart way, like they understand the lead and what the lead needs"). The approved template
    is the house style; the facts decide what this call is about."""
    house = {k: v for k, v in ((cfg or {}).get("scripts") or {}).items() if not k.startswith("$")}
    # Who is calling comes from the profile (crm.research.sender: business, and `about`, one
    # line on what the business does), never from the engine.
    sender = (cfg or {}).get("sender") or {}
    who = sender.get("business") or "our business"
    if sender.get("about"):
        who += ", " + sender["about"].strip().rstrip(".")
    system = (
        f"You write a short phone script for a salesperson at {who}. "
        "The salesperson calls the owner of ONE business. Use ONLY the facts in "
        "the JSON you are given, and pick the one or two that matter most for this business. "
        "Talk like the owner talks (their trade words are in owner_language); never marketing "
        "words, never AI or technical words. No statistics, no percentages, no prices and no "
        "revenue figures: if price comes up, the salesperson promises an exact number within a "
        "day. Say only what we actually did: we scanned their website's home page and checked "
        "their Google listing (we cannot see which page their ads send people to). We never clicked their ads, called them or visited, so never say we did. Never "
        "claim a result we did not measure (not 'it is costing you cars', not 'you are losing "
        "customers'); say what a customer runs into instead. Never use an em dash. Short sentences a person can say out loud. Keep the house "
        "style of the approved template. Write the salesperson's first name where the script "
        "says who is calling. Reply with ONE JSON object and nothing else, with exactly these "
        "keys: opener (string), fact (string: the one thing about their business), questions "
        "(3 strings), ask (string), objections (4 to 6 objects with q and a, the ones most "
        "likely from THIS owner), voicemail (string).\n\nApproved template (house style):\n"
        + json.dumps(house, indent=1, ensure_ascii=False))
    user = ("Salesperson first name: " + ((person_name or "").split(" ")[0] or "the salesperson") +
            "\nOwner language from our playbook:\n" + (language or "(none)") +
            "\n\nFacts (JSON):\n" + json.dumps(fact_pack, indent=2, ensure_ascii=False))
    return system, user


def parse_script(text, cfg):
    """(script, problems). The model's JSON, checked like any client-facing text, and never
    with a price in it. A script with a problem is not stored; the template stays in use."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").split("\n", 1)[-1]
        raw = raw.rsplit("```", 1)[0]
    try:
        script = json.loads(raw)
    except json.JSONDecodeError:
        return None, ["the script was not valid JSON"]
    if not isinstance(script, dict) or set(SCRIPT_KEYS) - set(script):
        return None, ["the script is missing parts"]
    ok_str = lambda v: isinstance(v, str) and v.strip()
    if not all(ok_str(script[k]) for k in ("opener", "fact", "ask", "voicemail")) or \
            not (isinstance(script["questions"], list) and script["questions"] and all(map(ok_str, script["questions"]))) or \
            not (isinstance(script["objections"], list) and script["objections"] and
                 all(isinstance(o, dict) and ok_str(o.get("q")) and ok_str(o.get("a")) for o in script["objections"])):
        return None, ["the script has an empty or malformed part"]
    script = {k: script[k] for k in SCRIPT_KEYS}
    text = json.dumps(script, ensure_ascii=False)
    problems = check_text(text, banned_phrases(cfg))
    if re.search(r"\$\s?\d|\d\s?%|\bpercent\b", text, re.I):
        problems.append("contains a price or a percentage")
    return (None if problems else script), problems


def document_prompt(fact_pack, cfg):
    """(system, user) for the research document. Fixed template, facts only."""
    heads = "\n".join(f"## {s}" for s in sections(cfg))
    system = (
        "You write a one-page research document that prepares a salesperson to approach a "
        "local business in person. Use ONLY the facts in the JSON you are given. If a fact "
        "is missing, say it was not measured; never guess one. Anything from the "
        "industry_pattern block must be labelled 'Industry pattern' and name its source, and "
        "must never be written as a finding about this business. No revenue or income "
        "figures of any kind. Never use an em dash; use a comma, colon or full stop instead. "
        "Plain, short sentences. Write in Markdown with exactly these section headings, in "
        "this order, and no others:\n" + heads)
    user = "Facts (JSON):\n" + json.dumps(fact_pack, indent=2, ensure_ascii=False)
    return system, user
