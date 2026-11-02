"""The QR codes at the gym's door: check in from your own phone, and sign the waiver.

WHY
    Sam, 2026-10-28: most gyms have a QR check-in and a QR waiver at the front desk. Both must land
    in the gym system on their own, and staff must see at a glance who has signed (a green dot).

HOW
    Two printed codes, one per job, each a link with the gym's door code (`gym.door.code_env`, a
    secret in the host's environment, at least 8 characters). The code proves the phone is at the
    gym; it changes only when the owner changes it, because it is printed.

    Check-in: the member types their phone number or email. It must match a live member, or a parent
    whose kids are live members, and the same proof is sent again with the check-in, so nobody checks
    in someone they cannot name. The write is the kiosk's one write (gym.execute_as_kiosk), recorded
    as method `qr`. Answers show first name and last initial only, like the tablet.

    Waiver: the gym's own text (`gym.waiver`: title, text, version). The signer types their full
    name as the signature and ticks that they agree; under 18 a parent or guardian signs. Each
    signature keeps the version, a SHA-256 of the exact text, the time, the address and the browser,
    so it can be shown later. A signer who is not in the gym system yet becomes a new contact (a
    lead): the waiver is often the first thing a trial fills in.
"""
import hashlib
import hmac
import json
import os
import re
from datetime import date, datetime
from uuid import uuid4

from . import contact_channels, crm_events, crm_store, gym, gym_billing, gym_kiosk

PATHS = ("/checkin", "/waiver", "/door.js", "/door.css", "/api/door/find", "/api/door/checkin", "/api/door/waiver", "/api/door/waiver/sign")
ACTOR = "system:door"


def config(prof):
    cfg = gym.config(prof)
    door = (cfg or {}).get("door")
    return door if isinstance(door, dict) else None


def waiver_config(prof):
    w = (gym.config(prof) or {}).get("waiver")
    return w if isinstance(w, dict) and isinstance(w.get("text"), str) and w["text"].strip() else None


def check_code(prof, code):
    door = config(prof)
    real = os.environ.get((door or {}).get("code_env") or "", "")
    if not door or len(real) < 8:
        raise crm_store.CRMError("Check-in by QR is not set up. Ask at the front desk.", 503)
    if not isinstance(code, str) or not hmac.compare_digest(code.encode(), real.encode()):
        raise crm_store.CRMError("Scan the code at the gym's front desk.", 403)


def code(prof):
    return os.environ.get((config(prof) or {}).get("code_env") or "", "")


def migrate(db):
    db.execute("""CREATE TABLE IF NOT EXISTS waivers (
        id TEXT PRIMARY KEY, contact_id TEXT NOT NULL, name TEXT NOT NULL, birth_date TEXT NOT NULL,
        email TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', guardian_name TEXT NOT NULL DEFAULT '',
        signature TEXT NOT NULL, version TEXT NOT NULL, text_sha256 TEXT NOT NULL, signed_at TEXT NOT NULL,
        address TEXT NOT NULL DEFAULT '', browser TEXT NOT NULL DEFAULT '')""")


def signed(db, prof):
    """{contact id: (signed_at, version)} for the latest waiver each person signed, or None when the
    gym takes no waiver (then the screens show no dot at all)."""
    if waiver_config(prof) is None:
        return None
    migrate(db)
    return {r[0]: (r[1], r[2]) for r in db.execute(
        "SELECT contact_id, max(signed_at), version FROM waivers GROUP BY contact_id")}


def _digits(value):
    d = re.sub(r"\D", "", str(value or ""))
    return d[-10:] if len(d) >= 7 else ""


def _proof(value):
    value = " ".join(str(value or "").split())
    if "@" in value:
        return "email", value.casefold()
    digits = _digits(value)
    if digits:
        return "phone", digits
    raise crm_store.CRMError("Type the phone number or email the gym has for you.")


def _who(db, proof):
    """Contact ids whose phone (last 10 digits) or email matches the proof."""
    kind, value = proof
    out = set()
    for r in db.execute("SELECT c.id, c.email, c.phone FROM contacts c WHERE c.kind='person' AND c.deleted_at IS NULL"):
        if (kind == "email" and (r["email"] or "").casefold() == value) or (kind == "phone" and _digits(r["phone"]) == value):
            out.add(r["id"])
    for r in db.execute("SELECT x.contact_id, x.kind, x.value FROM contact_channels x JOIN contacts c ON c.id=x.contact_id "
                        "WHERE c.deleted_at IS NULL"):
        if r["kind"] == kind and ((kind == "email" and r["value"].casefold() == value) or (kind == "phone" and _digits(r["value"]) == value)):
            out.add(r["contact_id"])
    return out


def _matches(db, prof, proof):
    """Live members matching the proof, and the live kids of a matching parent."""
    ids = _who(db, proof)
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = db.execute(
        f"SELECT DISTINCT c.id, c.name FROM contacts c JOIN memberships m ON m.contact_id=c.id "
        f"LEFT JOIN member_details d ON d.contact_id=c.id WHERE c.deleted_at IS NULL AND m.status IN ('active','trial') "
        f"AND (c.id IN ({marks}) OR d.guardian_id IN ({marks})) ORDER BY c.name COLLATE NOCASE", list(ids) * 2).fetchall()
    return [dict(r) for r in rows][:8]


def find(store, prof, body):
    check_code(prof, body.get("door"))
    gym.ensure(store, prof)
    proof = _proof(body.get("contact"))
    with store.connection() as db:
        hits = _matches(db, prof, proof)
        waivers = signed(db, prof)
    if not hits:
        raise crm_store.CRMError("We could not find you. Check the number or email, or ask at the front desk.", 404)
    names = gym_kiosk._shorts([h["name"] for h in hits])
    return {"members": [{"id": h["id"], "name": n, "waiver": None if waivers is None else h["id"] in waivers}
                        for h, n in zip(hits, names)],
            "classes": gym_kiosk.now_classes(store, prof)["classes"]}


def checkin(store, prof, body):
    check_code(prof, body.get("door"))
    gym.ensure(store, prof)
    proof = _proof(body.get("contact"))
    with store.connection() as db:
        allowed = {h["id"] for h in _matches(db, prof, proof)}
    if body.get("contact_id") not in allowed:
        raise crm_store.CRMError("We could not find you. Ask at the front desk.", 404)
    data = {"contact_id": body["contact_id"]}
    if body.get("session_id"):
        data["session_id"] = body["session_id"]
    result = gym.execute_as_kiosk(store, prof, data, body.get("request_id") or str(uuid4()), method="qr")
    with store.connection() as db:
        waivers = signed(db, prof)
        card = data["contact_id"] in gym_billing.failed_ids(db, prof)
    return {"ok": True, "already": bool(result.get("already")), "update_card": card,
            "needs_waiver": waivers is not None and data["contact_id"] not in waivers}


def waiver(prof):
    w = waiver_config(prof)
    if w is None:
        raise crm_store.CRMError("This gym has no waiver to sign here.", 404)
    return {"gym": prof["name"], "title": w.get("title") or "Waiver", "text": w["text"], "version": str(w.get("version") or "1"),
            "sample": bool(w.get("sample")), "adult_age": 18}


def _age(born, on):
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def sign(store, prof, body, address="", browser=""):
    """One signed waiver. Refuses anything short of a full, agreed, named signature."""
    check_code(prof, body.get("door"))
    w = waiver(prof)
    gym.ensure(store, prof)
    name = crm_store.text(body.get("name"), "Full name", 120, True)
    email = crm_store.text(body.get("email") or "", "Email", 254)
    phone = crm_store.text(body.get("phone") or "", "Phone", 60)
    if not email and not _digits(phone):
        raise crm_store.CRMError("Give a phone number or an email, so the gym can reach you.")
    if email and "@" not in email:
        raise crm_store.CRMError("That email does not look right.")
    born = date.fromisoformat(gym.day(body.get("birth_date"), "Birth date"))
    on = gym.today(prof)
    if born >= on:
        raise crm_store.CRMError("Check the birth date.")
    minor = _age(born, on) < w["adult_age"]
    guardian = crm_store.text(body.get("guardian_name") or "", "Parent or guardian", 120, minor)
    signature = " ".join(str(body.get("signature") or "").split())
    signer = guardian if minor else name
    if signature.casefold() != " ".join(signer.split()).casefold():
        raise crm_store.CRMError(f"Type {'the parent or guardian' if minor else 'your'} full name exactly as the signature: {signer}")
    if body.get("agree") is not True:
        raise crm_store.CRMError("Tick that you have read and agree to the waiver.")
    if str(body.get("version")) != w["version"]:
        raise crm_store.CRMError("The waiver changed while you were reading it. Reload the page.", 409)
    stamp = crm_store.now()
    key = crm_store.text(body.get("request_id") or str(uuid4()), "Request ID", 80, True)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        migrate(db)
        previous = db.execute("SELECT response FROM commands WHERE id=?", (key,)).fetchone()
        if previous:
            return json.loads(previous["response"])
        ids = _who(db, _proof(email or phone))
        same = [r["id"] for r in db.execute("SELECT id, name FROM contacts WHERE kind='person' AND deleted_at IS NULL")
                if " ".join(r["name"].split()).casefold() == " ".join(name.split()).casefold()]
        kids = {r[0] for r in db.execute("SELECT contact_id FROM member_details WHERE guardian_id IN (%s)" % ",".join("?" * len(ids)),
                                         list(ids))} if ids else set()
        match = next((c for c in same if c in ids or c in kids), None)  # the same name AND a contact detail
        new = match is None
        if new:
            match = str(uuid4())
            db.execute("INSERT INTO contacts (id,owner,kind,name,created_at,updated_at,source_channel) "
                       "VALUES (?,?,'person',?,?,?,?)", (match, _owner(prof), name, stamp, stamp, "Waiver QR"))
            label = "Parent" if minor else "Waiver"
            contact_channels.write(db, match, ([{"kind": "email", "label": label, "value": email}] if email else []) +
                                   ([{"kind": "phone", "label": label, "value": phone}] if phone else []), stamp)
            program = next((p["program"] for p in sorted((gym.config(prof) or {}).get("age_programs") or [], key=lambda p: p.get("under", 0))
                            if isinstance(p, dict) and _age(born, on) < p.get("under", 0)), "")
            db.execute("INSERT OR IGNORE INTO member_details (contact_id,birth_date,joined_on,program,emergency_name,"
                       "emergency_phone,guardian_id,revision,created_at,updated_at) VALUES (?,?,?,?,?,?,NULL,1,?,?)",
                       (match, born.isoformat(), "", program or (gym.programs(prof)[0]["id"] if gym.programs(prof) else ""),
                        guardian if minor else "", phone if minor else "", stamp, stamp))
        rid = str(uuid4())
        db.execute("INSERT INTO waivers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (rid, match, name, born.isoformat(), email, phone, guardian or "", signature, w["version"],
                    hashlib.sha256(w["text"].encode("utf-8")).hexdigest(), stamp, str(address)[:80], str(browser)[:300]))
        result = {"ok": True, "id": rid, "contact_id": match, "new_contact": new, "name": name.split()[0]}
        crm_events.record(db, key, ACTOR, "gym.waiver.sign", {"contact_id": match, "version": w["version"], "new_contact": new}, result)
        db.execute("INSERT INTO commands VALUES (?,?,?,?)", (key, ACTOR, hashlib.sha256(key.encode()).hexdigest(), json.dumps(result)))
    return result


def _owner(prof):
    people = (prof.get("people") or {}).get("members") or []
    admin = next((p["id"] for p in people if p.get("role") == "admin"), None)
    return admin or (people[0]["id"] if people else "")


def history(db, prof, cid):
    """A member's signed waivers, newest first, for the member page."""
    if waiver_config(prof) is None:
        return None
    migrate(db)
    local = lambda s: datetime.fromisoformat(s).astimezone(gym.zone(prof)).date().isoformat()  # the gym's own day
    return [{"signed_at": r["signed_at"], "signed_on": local(r["signed_at"]), "version": r["version"],
             "signature": r["signature"], "guardian_name": r["guardian_name"]}
            for r in db.execute("SELECT * FROM waivers WHERE contact_id=? ORDER BY signed_at DESC", (cid,))]


def page(prof, mode):
    """The public page a phone opens from the printed code: `checkin` or `waiver`."""
    import html
    from . import demo_mode
    name = html.escape(prof["name"])
    accent = (prof.get("crm") or {}).get("accent", "#3355e0")
    accent = accent if isinstance(accent, str) and len(accent) == 7 and accent.startswith("#") else "#3355e0"
    logo = '<img class="kiosk-logo" src="brand-logo" alt="">' if (prof.get("crm") or {}).get("logo") else ""
    demo = '<link rel="stylesheet" href="demo.css"><script defer src="demo.js"></script>' if demo_mode.config(prof) else ""
    title = "Check in" if mode == "checkin" else "Waiver"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>{name} | {title}</title><link rel="stylesheet" href="door.css"><style>:root{{--accent:{accent}}}</style>{demo}
<script defer src="door.js"></script></head><body class="kiosk door" data-mode="{mode}">
<main id="kiosk" class="door-main" aria-live="polite"><h1>{logo}<span>{name}</span></h1><p>Loading&hellip;</p></main>
<noscript>This page needs JavaScript. Ask at the front desk.</noscript></body></html>"""
