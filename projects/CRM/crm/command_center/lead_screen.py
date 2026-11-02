"""The lead screen: is a website lead a real enquiry? (Sam, 2026-10-21)

A client's first two "real" web leads were junk: a vendor selling a $49/yr certificate
through the contact form, and an offshore number with no message. Both alerts reached the
client. This reads each new web lead and answers one of three verdicts:

    ok       a person asking about the business; the alert goes out as before
    review   something is off; the lead is saved and held for an administrator
    spam     it reads as a sales pitch to the business; saved and held the same way

Nothing is ever dropped: `lead_intake.receive` saves every lead, and a held lead waits in the
administrator's "Waiting for you" list with these reasons until it is released or set aside.

Deterministic and offline: word lists and number shapes only, no network and no model, so the
same lead always gets the same verdict and a test can pin it. Each reason is one plain sentence
the administrator reads. Off until the profile turns it on, inside `crm.intake`:

    "screen": {
      "country": "US",            # the business's service country; phones from elsewhere wait
      "min_seconds": 3,           # optional; a form sent faster than this waits
      "pitch_phrases": ["..."],   # optional; added to the engine's own list
      "reviewers": ["<member id>"] # optional; who sees and settles held leads (default: every admin)
    }

What it does not do: look a domain up on the internet ("has no website"). That needs the
network inside a web request; a person checks it when the lead is held for another reason.
"""
import re

# The service country: its calling code and how many digits a local number has.
COUNTRIES = {"US": ("1", 10, "the US"), "CA": ("1", 10, "Canada"), "MX": ("52", 10, "Mexico"),
             "GB": ("44", 10, "the UK"), "AU": ("61", 9, "Australia")}

# Calling codes that show up on junk form fills, for a readable reason. Longest prefix wins.
CALLING_CODES = {
    "880": "Bangladesh", "91": "India", "92": "Pakistan", "94": "Sri Lanka", "977": "Nepal",
    "63": "the Philippines", "62": "Indonesia", "84": "Vietnam", "86": "China", "7": "Russia",
    "234": "Nigeria", "233": "Ghana", "254": "Kenya", "20": "Egypt", "212": "Morocco",
    "971": "the UAE", "966": "Saudi Arabia", "90": "Turkey", "380": "Ukraine", "55": "Brazil",
    "44": "the UK", "52": "Mexico", "61": "Australia", "49": "Germany", "33": "France", "1": "the US or Canada",
}

# Words a vendor uses when selling TO the business. A customer asking for help rarely does.
PITCH_PHRASES = (
    "claim your profile", "claim your listing", "get verified", "verified badge", "verification badge",
    "certificate of", "transparency certificate", "we noticed your website", "i noticed your website",
    "i came across your website", "i was looking at your website", "i visited your website",
    "your website is not ranking", "first page of google", "page 1 of google", "search engine optimization",
    "seo services", "seo expert", "web design services", "website redesign", "redesign your website",
    "more leads", "generate leads", "lead generation", "qualified leads", "guest post", "backlinks",
    "digital marketing services", "marketing agency", "virtual assistant", "outsourcing",
    "business listing", "limited time offer", "special offer", "free audit", "free trial",
    "reply stop", "to unsubscribe", "opt out",
)
# "$49 per year", "$299/mo", "$9.99 a month": a price with a billing period.
PRICE = re.compile(r"\$\s?\d[\d,]*(\.\d\d)?\s*(/|per|a|an|each)\s*(yr|year|mo|month|week|annum|annually)\b", re.I)
EMAIL_IN_TEXT = re.compile(r"[^\s@]+@[^\s@]+")
LINK = re.compile(r"\b(?:https?://)?(?:www\.)?((?:[a-z0-9-]+\.)+(?:com|net|org|io|co|biz|info|us|ai|app|site|online|xyz))\b", re.I)

# Throwaway inboxes. Free mail (gmail, yahoo) is how real customers write; these are not.
DISPOSABLE = {"mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com", "temp-mail.org",
              "yopmail.com", "trashmail.com", "sharklasers.com", "getnada.com", "dispostable.com",
              "maildrop.cc", "throwawaymail.com", "fakeinbox.com", "mailnesia.com", "mintemail.com"}


def config(block):
    """The checked screen settings, or None when the profile has no `screen` block. Raises
    ValueError on a block that is present but wrong, like `lead_intake.config`."""
    if block is None:
        return None
    if not isinstance(block, dict):
        raise ValueError("crm.intake.screen must be an object")
    country = block.get("country")
    if country not in COUNTRIES:
        raise ValueError("crm.intake.screen.country must be one of " + ", ".join(COUNTRIES))
    extra = block.get("pitch_phrases") or []
    if not isinstance(extra, list) or any(not isinstance(p, str) or not p.strip() for p in extra):
        raise ValueError("crm.intake.screen.pitch_phrases must list words or phrases")
    seconds = block.get("min_seconds", 3)
    if type(seconds) is not int or seconds < 0:
        raise ValueError("crm.intake.screen.min_seconds must be a whole number")
    code, digits, name = COUNTRIES[country]
    return {"country": country, "calling_code": code, "national_digits": digits, "country_name": name,
            "min_seconds": seconds,
            "pitch_phrases": tuple(dict.fromkeys(PITCH_PHRASES + tuple(p.strip().casefold() for p in extra)))}


def phone_outside(phone, cfg):
    """None when the number fits the service country (or there is none), else the reason."""
    raw = (phone or "").strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("00"):
        digits, international = digits[2:], True
    else:
        international = raw.startswith("+")
    if not digits:
        return None
    code, local = cfg["calling_code"], cfg["national_digits"]
    if not international and len(digits) <= local:
        return None  # a local number, or too short to judge
    if digits.startswith(code) and len(digits) == len(code) + local:
        return None
    prefix = next((digits[:n] for n in (3, 2, 1) if digits[:n] in CALLING_CODES), None)
    if prefix and prefix != code:
        return (f"The phone number starts with +{prefix} ({CALLING_CODES[prefix]}), "
                f"outside {cfg['country_name']} where the business works.")
    return f"The phone number ({len(digits)} digits) does not look like a number in {cfg['country_name']}."


def check(lead, cfg, own_origins=()):
    """{"verdict": "ok" | "review" | "spam", "reasons": [{"signal", "level", "text"}]} for one
    normalized lead (`lead_intake.validate`). With no screen settings every lead is ok."""
    if not cfg:
        return {"verdict": "ok", "reasons": []}
    reasons = []
    add = lambda signal, level, text: reasons.append({"signal": signal, "level": level, "text": text})
    message = lead.get("message") or ""
    words = " ".join([message, lead.get("company") or ""] + [b for _, b in lead.get("details") or []])
    folded = words.casefold()

    # A sales pitch to the business: a priced subscription, vendor phrases, a link elsewhere.
    hits = [f'"{p}"' for p in cfg["pitch_phrases"] if re.search(r"\b" + re.escape(p) + r"\b", folded)]
    price = PRICE.search(words)
    if price:
        hits.insert(0, f"a price with a billing period ({price.group(0).strip()})")
    # A link counts only beside a pitch: customers name their own site, vendors link to theirs.
    own = {re.sub(r"^https?://(www\.)?", "", o).lower() for o in own_origins}
    links = sorted({m.lower().removeprefix("www.") for m in LINK.findall(EMAIL_IN_TEXT.sub(" ", words))} - own)
    links = links if hits else []
    score = len(hits) + (1 if links else 0)
    if hits:
        add("pitch", "spam" if score >= 2 else "review",
            "The message reads like a sales pitch to the business: " + ", ".join(hits[:4]) + ".")
    if links:
        add("link", "spam" if score >= 2 else "review",
            "The message links to another website (" + ", ".join(links[:3]) + ").")

    outside = phone_outside(lead.get("phone"), cfg)
    if outside:
        add("phone", "review", outside)

    if not message.strip() and not lead.get("details") and (lead.get("interest") or "general") == "general":
        add("empty", "review", "No message, and the form only says \"general\": nothing about what they need.")

    domain = (lead.get("email") or "").rpartition("@")[2].lower()
    if domain in DISPOSABLE:
        add("email", "review", f"The email address is a throwaway inbox ({domain}).")

    elapsed = lead.get("elapsed_ms")
    if cfg["min_seconds"] and isinstance(elapsed, int) and 0 <= elapsed < cfg["min_seconds"] * 1000:
        add("speed", "review", f"The form was sent {elapsed / 1000:.1f} s after it opened; a person takes longer.")

    levels = {r["level"] for r in reasons}
    return {"verdict": "spam" if "spam" in levels else "review" if levels else "ok", "reasons": reasons}
