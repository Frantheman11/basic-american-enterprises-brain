"""Order Desk: read one email. Sort it, pull the text out of each attachment, read the PO.

The reader is the one AI step, and it is pluggable (`order_desk.reader`):

- "demo" (the default): rules for the sample formats. It needs no key and calls nothing. It reads
  a table with a header row (PDF, fax text, CSV, XLSX) and order lines written in an email body.
- "claude": the Claude Messages API reads the same inputs, scans included (a PDF goes as a
  document block), and answers in the same JSON shape. The key comes from the env var the profile
  names (`order_desk.ai.key_env`), never from the profile itself.

Either way the output is only a proposal. Matching and checks run on it next (order_desk_match.py)
and a person approves every order (order_desk.py).

A fax arrives as an image PDF. The demo reader cannot do OCR, so a sample fax carries its OCR text
in the demo data (`order_desk.ocr_dir`, one file per attachment hash): what an OCR step returns,
noise and all. The Claude reader reads the image itself.
"""
import csv
import io
import json
import os
import re
import zipfile
import zlib
from datetime import date, datetime
from xml.etree import ElementTree

from . import inbox_core

KINDS = ("order", "change", "quote", "status", "other")
UOMS = {"ea": "EA", "each": "EA", "eaches": "EA", "pc": "EA", "pcs": "EA", "piece": "EA", "pieces": "EA",
        "bx": "BX", "box": "BX", "boxes": "BX", "pk": "PK", "pack": "PK", "packs": "PK", "pkg": "PK",
        "cs": "CS", "case": "CS", "cases": "CS", "rl": "RL", "roll": "RL", "rolls": "RL", "ft": "FT",
        "feet": "FT", "foot": "FT", "pr": "PR", "pair": "PR", "pairs": "PR", "dz": "DZ", "doz": "DZ",
        "dozen": "DZ", "st": "EA", "stick": "EA", "sticks": "EA", "spl": "RL", "spool": "RL", "spools": "RL"}
_MONEY = r"\$?\s*(\d{1,6}(?:,\d{3})*(?:\.\d{1,4})?)"
MAX_STREAM = 5_000_000  # bytes one PDF stream or spreadsheet part may expand to


# ---------------------------------------------------------------- the email itself

parse_email = inbox_core.parse_email  # the shared reader, kept here for this module's callers


def kind_of(name, ctype):
    n = name.lower()
    if n.endswith(".pdf") or ctype == "application/pdf":
        return "pdf"
    if n.endswith(".xlsx"):
        return "xlsx"
    if n.endswith(".csv") or ctype == "text/csv":
        return "csv"
    if n.endswith(".txt") or ctype.startswith("text/"):
        return "text"
    return "other"


def file_text(f, ocr_dir=None):
    """(text, rows, how). rows is a table for a spreadsheet, else None. how says where the text came from."""
    k = kind_of(f["name"], f["type"])
    if k == "csv":
        rows = [r for r in csv.reader(io.StringIO(f["data"].decode("utf-8-sig", "replace"))) if any(c.strip() for c in r)]
        return "\n".join("  ".join(r) for r in rows), rows, "spreadsheet"
    if k == "xlsx":
        rows = xlsx_rows(f["data"])
        return "\n".join("  ".join(r) for r in rows), rows, "spreadsheet"
    if k == "text":
        return f["data"].decode("utf-8", "replace"), None, "text"
    if k == "pdf":
        text = pdf_text(f["data"])
        if text.strip():
            return text, None, "pdf text"
        if ocr_dir:
            p = os.path.join(ocr_dir, f["sha"] + ".txt")
            if os.path.isfile(p):
                with open(p, encoding="utf-8") as fh:
                    return fh.read(), None, "fax scan (OCR text)"
        return "", None, "scan, no text layer"
    return "", None, "not read"


# ---------------------------------------------------------------- PDF and XLSX, standard library only

def _pdf_string(s):
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            if n in "01234567":
                j = i + 1
                while j < len(s) and j < i + 4 and s[j] in "01234567":
                    j += 1
                out.append(chr(int(s[i + 1:j], 8)))
                i = j
                continue
            out.append({"n": "\n", "r": "", "t": "\t", "b": "", "f": ""}.get(n, n))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def pdf_text(data):
    """Text of a simple PDF (uncompressed or Flate streams, Tj/TJ with Td/Tm placement). Pieces on one
    baseline join with two spaces, so table columns stay columns. Falls back to pypdf when installed.
    A scan has no text layer and returns ''."""
    pieces = []
    for m in re.finditer(rb"<<(.*?)>>\s*stream\r?\n(.*?)\r?\nendstream", data, re.S):
        head, body = m.group(1), m.group(2)
        if b"/Image" in head:
            continue
        if b"/FlateDecode" in head:
            try:
                body = zlib.decompressobj().decompress(body, MAX_STREAM)  # a stranger's file: never unbounded
            except zlib.error:
                continue
        stream = body.decode("latin-1")
        if "BT" not in stream:
            continue
        x = y = 0.0
        for op in re.finditer(r"(-?[\d.]+)\s+(-?[\d.]+)\s+T[dD]|"
                              r"(?:-?[\d.]+\s+){4}(-?[\d.]+)\s+(-?[\d.]+)\s+Tm|"
                              r"\(((?:\\.|[^\\)])*)\)\s*Tj|\[((?:\\.|[^\]])*)\]\s*TJ|(BT)", stream):
            if op.group(7):
                x = y = 0.0
            elif op.group(1) is not None:
                x, y = x + float(op.group(1)), y + float(op.group(2))
            elif op.group(3) is not None:
                x, y = float(op.group(3)), float(op.group(4))
            elif op.group(5) is not None:
                pieces.append((y, x, _pdf_string(op.group(5))))
            elif op.group(6) is not None:
                pieces.append((y, x, "".join(_pdf_string(s) for s in re.findall(r"\(((?:\\.|[^\\)])*)\)", op.group(6)))))
    if not pieces and data[:5] == b"%PDF-":
        try:
            import pypdf  # optional: real-world PDFs with fonts and encodings this reader does not handle
            return "\n".join((p.extract_text() or "") for p in pypdf.PdfReader(io.BytesIO(data)).pages)
        except Exception:  # noqa: BLE001  no pypdf, or not a PDF it can read: no text
            return ""
    if not pieces:
        return ""
    lines = {}
    for y, x, t in pieces:
        lines.setdefault(round(y), []).append((x, t))
    return "\n".join("  ".join(t for _, t in sorted(lines[k])) for k in sorted(lines, reverse=True))


_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def xlsx_rows(data):
    """The first sheet of an .xlsx as rows of text."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if sum(i.file_size for i in z.infolist()) > 4 * MAX_STREAM:
            return []  # a zip bomb, or no purchase order anyone types
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ElementTree.fromstring(z.read("xl/sharedStrings.xml")).iter(_NS + "si"):
                shared.append("".join(t.text or "" for t in si.iter(_NS + "t")))
        sheet = ElementTree.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in sheet.iter(_NS + "row"):
        cells = {}
        for c in row.iter(_NS + "c"):
            col = re.match(r"[A-Z]+", c.get("r", "A")).group(0)
            idx = 0
            for ch in col:
                idx = idx * 26 + ord(ch) - 64
            v = c.find(_NS + "v")
            if c.get("t") == "s" and v is not None:
                val = shared[int(v.text)]
            elif c.get("t") == "inlineStr":
                val = "".join(t.text or "" for t in c.iter(_NS + "t"))
            else:
                val = v.text if v is not None else ""
                if re.fullmatch(r"-?\d+\.0+", val or ""):
                    val = val.split(".")[0]
            cells[idx - 1] = val or ""
        if cells:
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
    return [r for r in rows if any(c.strip() for c in r)]


# ---------------------------------------------------------------- sorting

# The Order Desk's sorting rules for inbox_core.sort, first match wins. A Quote Desk profile orders its own.
RULES = (
    ("change", "any", r"\b(revised|revision|change order|amended|change to (our )?po|update to (our )?po)\b", "always"),
    ("quote", "any", r"\b(rfq|request for quote|quote request|please quote|price and availability|p&a)\b", "always"),
    ("status", "any", r"\b(where is|status of|status on|tracking|when will|has .{0,30} shipped|eta)\b", "without_document"),
    ("order", "any", "", "with_document"),
    ("order", "any", r"\b(purchase order|p\.?o\.?\s*(#|no|number)|please (send|ship|order)|order:)", "always"),
    ("order", "subject", r"\bp\.?o\.?\s*#?\s*[a-z0-9-]*\d", "always"),
)


def classify(subject, body, has_po_file):
    """new order, change, quote request, status question or other."""
    return inbox_core.sort(subject, body, has_po_file, RULES)


# ---------------------------------------------------------------- the demo reader

HEADER_WORDS = {
    "qty": ("qty", "quantity", "order qty", "qty ordered", "ordered"),
    "uom": ("uom", "u/m", "unit", "um", "units"),
    "part": ("part", "part number", "part #", "part no", "cust item", "item #", "item number", "your part #",
             "customer part", "p/n", "item"),
    "sku": ("our part #", "vendor part", "supplier part", "your item #", "mfr part"),
    "description": ("description", "item description", "desc"),
    "price": ("unit price", "price", "unit cost", "cost", "price each", "price/unit"),
    "line": ("line", "ln", "#", "line #"),
    "ext": ("ext", "extended", "amount", "total", "ext price", "line total"),
}


def _cell_key(cell):
    c = re.sub(r"\s+", " ", cell.strip().lower().rstrip(":"))
    for key in ("sku", "description", "price", "ext", "qty", "uom", "part", "line"):
        if c in HEADER_WORDS[key]:
            return key
    return None


def _split(line):
    return [c for c in re.split(r"\s{2,}|\t|\s*\|\s*", line.strip()) if c != ""]


def _num(s):
    s = (s or "").replace(",", "").replace("$", "").strip()
    s = s.translate(str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "S": "5"})) if re.fullmatch(r"[\dOolIS.]+", s) else s
    try:
        return float(s)
    except ValueError:
        return None


def _table_lines(rows):
    """Rows of cells -> order lines, using the first row that reads as a header."""
    for i, row in enumerate(rows):
        keys = [_cell_key(c) for c in row]
        if sum(1 for k in keys if k) >= 3 and "qty" in keys and ("part" in keys or "description" in keys):
            out = []
            for r in rows[i + 1:]:
                if not r or re.match(r"\s*(sub)?total|tax|freight|shipping|notes?\b", r[0], re.I):
                    if out:
                        break
                    continue
                cells = dict(zip(keys, r)) if len(r) == len(keys) else _align(keys, r)
                qty = _num(cells.get("qty", ""))
                if qty is None or qty <= 0:
                    if out:
                        break
                    continue
                uom = UOMS.get((cells.get("uom") or "").strip().lower().rstrip("."), (cells.get("uom") or "").strip().upper())
                out.append({"customer_part": (cells.get("part") or cells.get("sku") or "").strip(),
                            "our_part": (cells.get("sku") or "").strip(), "description": (cells.get("description") or "").strip(),
                            "qty": qty, "uom": uom, "price": _num(cells.get("price", ""))})
            return out
    return []


def _align(keys, r):
    """A row with an empty cell (two spaces swallowed it): match from both ends around the description."""
    if "description" not in keys or len(r) > len(keys):
        return dict(zip(keys, r))
    d = keys.index("description")
    tail = len(keys) - d - 1
    out = dict(zip(keys[:d], r[:d]))
    out.update(dict(zip(keys[d + 1:], r[len(r) - tail:] if tail else [])))
    out["description"] = " ".join(r[d:len(r) - tail] if tail else r[d:])
    return out


_FREE = re.compile(r"^\s*(?:[-*•]|\d+[.)])?\s*(?:qty\s*:?\s*)?(?P<qty>\d[\d,]*)\s*(?:x\s+)?(?P<uom>[A-Za-z]{1,7}\.?)?\s+(?:of\s+)?(?P<rest>.+)$", re.I)


def _free_lines(text):
    """Order lines written in an email: "4 bx 3/8-16 x 1 hex cap screw (BR-1042) @ $11.85"."""
    out = []
    for line in text.splitlines():
        if re.search(r"\b(po|p\.o\.|purchase order|need(ed)? by|ship to|thanks|phone|fax)\b", line, re.I) and not re.search(r"@|\bpart\b|p/n", line, re.I):
            continue
        m = _FREE.match(line)
        if not m:
            continue
        uom = UOMS.get((m.group("uom") or "").lower().rstrip("."))
        rest = m.group("rest") if uom else ((m.group("uom") or "") + " " + m.group("rest")).strip()
        price = None
        pm = re.search(r"(?:@|at|price[: ]|each[: ]?)\s*" + _MONEY + r"\s*(?:/\s*\w+|ea\b|each\b)?", rest, re.I)
        if pm:
            price, rest = _num(pm.group(1)), (rest[:pm.start()] + rest[pm.end():]).strip()
        part = ""
        km = re.search(r"\((?:p/n|part|pn|#)\s*:?\s*([^()]{3,30})\)|\(([A-Z0-9][A-Z0-9./-]{2,})\)|"
                       r"\b(?:p/n|part(?: number| #| no\.?)?|pn)\s*[:#]?\s*([A-Z0-9][A-Z0-9./-]{2,})", rest, re.I)
        if km:
            part = (km.group(1) or km.group(2) or km.group(3)).strip(" .")
            rest = (rest[:km.start()] + rest[km.end():]).strip()
        desc = re.sub(r"\s+", " ", rest.strip(" -,;:")).strip()
        if len(desc) < 3 and not part:
            continue
        out.append({"customer_part": part, "our_part": "", "description": desc, "qty": _num(m.group("qty")),
                    "uom": uom or "", "price": price})
    return out


def _date(s, ref):
    s = s.strip().rstrip(".")
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y", "%b %d, %Y", "%B %d, %Y", "%b %d %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    for fmt in ("%m/%d", "%b %d", "%B %d"):
        try:
            d = datetime.strptime(s, fmt).date().replace(year=ref.year)
            return (d if d >= ref.replace(day=1) else d.replace(year=ref.year + 1)).isoformat()
        except ValueError:
            pass
    return ""


# A labeled PO number ("PO Number:", "PO#", "P0 No." from a fax) beats a bare "PO 123" or "Purchase Order 123".
# Never across a line break: "PURCHASE ORDER" over a street address is not PO 1840.
_PO_NO = r"[ \t]*[:#]?[ \t]*([A-Z0-9][A-Z0-9-]*\d[A-Z0-9-]*)"
PO_LABELED = re.compile(r"(?:\bP\.?[ \t]?[O0]\.?[ \t]*(?:#|No\.?|Number|Num)|Purchase[ \t]+Order[ \t]*(?:#|No\.?|Number))" + _PO_NO, re.I)
PO_RE = re.compile(r"(?:\bP\.?[ \t]?[O0]\b\.?|Purchase[ \t]+Order)" + _PO_NO, re.I)


def _ship_to(text):
    """The ship-to block, read down the column its label sits in (a PDF often puts Vendor beside it)."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        cells = _split(line)
        col = next((j for j, c in enumerate(cells) if re.match(r"ship\s*to\b", c, re.I)), None)
        if col is None:
            continue
        block = []
        after = re.sub(r"(?i)^ship\s*to\s*:?\s*", "", cells[col]).strip()
        if after:
            block.append(after)
        for nxt in lines[i + 1:i + 4]:
            row = _split(nxt)
            if not row or sum(1 for c in row if _cell_key(c)) >= 2 or re.search(r"bill\s*to|need(ed)? by|thanks", nxt, re.I):
                break
            block.append(row[col] if col < len(row) else row[-1])
        return ", ".join(b.strip() for b in block if b.strip())[:200]
    return ""


def header_fields(text, subject, ref):
    """PO number, need-by date, ship-to block and bill-to name from free text."""
    out = {"po_number": "", "need_by": "", "ship_to": "", "bill_to": ""}
    for rx, src in ((PO_LABELED, text), (PO_LABELED, subject), (PO_RE, subject), (PO_RE, text)):
        m = rx.search(src)
        if m:
            out["po_number"] = m.group(1).upper().translate(str.maketrans({"O": "0"})) if re.fullmatch(r"[\dO-]+", m.group(1).upper()) else m.group(1).upper()
            break
    m = re.search(r"(?:need(?:ed)?\s*by|required(?:\s*date)?|deliver(?:y)?\s*(?:by|date)|ship\s*by|date\s*required)\s*[:\-]?\s*"
                  r"([A-Za-z]{3,9}\.? \d{1,2}(?:, \d{4})?|\d{1,4}[/-]\d{1,2}(?:[/-]\d{2,4})?)", text, re.I)
    if m:
        out["need_by"] = _date(m.group(1).replace(".", ""), ref)
    out["ship_to"] = _ship_to(text)
    m = re.search(r"(?:bill\s*to|sold\s*to|buyer|company)\s*:?\s*\n?\s*([^\n]+)", text, re.I)
    if m:
        out["bill_to"] = m.group(1).split("  ")[0].strip()
    return out


def demo_read(mail, docs, ref):
    """The demo reader: header fields from the PO document (or the email), lines from its table,
    else from the email body. `docs` is [(name, text, rows, how)]."""
    lines, source, head_text = [], "", ""
    for name, text, rows, how in docs:
        got = _table_lines(rows) if rows else _table_lines([_split(ln) for ln in text.splitlines()])
        if got:
            lines, source, head_text = got, name, text
            break
    if not lines:
        lines, source = _free_lines(mail["body"]), "email body"
        if not lines:
            for name, text, rows, how in docs:
                lines = _free_lines(text)
                if lines:
                    source = name
                    break
    head = header_fields(head_text + "\n" + mail["body"], mail["subject"], ref)
    return {**head, "lines": lines, "source": source, "reader": "demo"}


# ---------------------------------------------------------------- the Claude reader

PROMPT = """You read purchase orders for an industrial distributor's order desk. A person checks every
order you read, so never guess: leave a field empty when the document does not say it.
Answer with JSON only, this shape:
{"po_number": "", "need_by": "YYYY-MM-DD or empty", "ship_to": "one line", "bill_to": "buyer company name",
 "lines": [{"customer_part": "", "our_part": "", "description": "", "qty": 0, "uom": "EA|BX|PK|CS|RL|FT|PR|DZ|",
            "price": null}]}
customer_part is the buyer's own part number; our_part is the distributor's number only if the PO shows it.
price is the unit price on the PO, or null."""


def claude_read(mail, attachments, cfg, ref):
    """The same JSON from the Claude Messages API. PDFs go as document blocks, so a scan reads too."""
    key = os.environ.get((cfg.get("ai") or {}).get("key_env") or "", "")
    if not key:
        raise RuntimeError("The Claude reader needs the env var named in order_desk.ai.key_env")
    import base64
    import anthropic  # tests/requirements.txt carries it; only this reader imports it
    content = [{"type": "text", "text": f"From: {mail['from']}\nSubject: {mail['subject']}\n\n{mail['body'][:20000]}"}]
    for f in attachments:
        k = kind_of(f["name"], f["type"])
        if k == "pdf":
            content.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                           "data": base64.b64encode(f["data"]).decode()}})
        elif k in ("csv", "xlsx", "text"):
            content.append({"type": "text", "text": f"Attachment {f['name']}:\n" + file_text(f)[0][:20000]})
    client = anthropic.Anthropic(api_key=key)
    reply = client.messages.create(model=(cfg.get("ai") or {}).get("model") or "claude-sonnet-5-5", max_tokens=4000,
                                   system=PROMPT, messages=[{"role": "user", "content": content}])
    raw = "".join(b.text for b in reply.content if getattr(b, "type", "") == "text")
    data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    lines = [{"customer_part": str(x.get("customer_part") or ""), "our_part": str(x.get("our_part") or ""),
              "description": str(x.get("description") or ""), "qty": _num(str(x.get("qty") or "")),
              "uom": UOMS.get(str(x.get("uom") or "").lower(), str(x.get("uom") or "").upper()),
              "price": _num(str(x["price"])) if x.get("price") is not None else None}
             for x in data.get("lines") or [] if isinstance(x, dict)]
    return {"po_number": str(data.get("po_number") or "").upper(), "need_by": str(data.get("need_by") or ""),
            "ship_to": str(data.get("ship_to") or ""), "bill_to": str(data.get("bill_to") or ""),
            "lines": [ln for ln in lines if ln["qty"]], "source": "claude", "reader": "claude"}


def read(mail, cfg, ocr_dir=None, ref=None):
    """Sort and read one parsed email. Returns {kind, docs, po}; po is None unless it is an order or a change."""
    ref = ref or date.today()
    docs = []
    for f in mail["files"]:
        text, rows, how = file_text(f, ocr_dir)
        docs.append((f["name"], text, rows, how))
    has_po = any(t.strip() and (rows or PO_RE.search(t) or re.search(r"\bqty|quantity\b", t, re.I)) for _, t, rows, _ in docs)
    kind = classify(mail["subject"], mail["body"], has_po)
    po = None
    if kind in ("order", "change"):
        po = claude_read(mail, mail["files"], cfg, ref) if cfg.get("reader") == "claude" else demo_read(mail, docs, ref)
    return {"kind": kind, "docs": [{"name": n, "text": t, "rows": r, "how": h} for n, t, r, h in docs], "po": po}
