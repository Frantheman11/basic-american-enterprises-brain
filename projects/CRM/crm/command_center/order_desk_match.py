"""Order Desk: match the customer, match each line to a SKU, then check it.

The facts come from the distributor's own ERP export, a folder of CSV files (`order_desk.data_dir`).
A pilot takes the daily export; nothing here talks to the ERP.

    items.csv        sku, description, uom, pack_qty, list_price, min_qty, multiple, category
    customers.csv    customer_id, name, emails (;), domain, ship_to, terms_days
    xref.csv         customer_id, customer_part, sku          (the customer part number table)
    prices.csv       customer_id, sku, price                  (contract prices, per sell unit)
    stock.csv        sku, on_hand                             (in sell units)
    history.csv      customer_id, erp_order, po_number, order_date, customer_part, sku, qty, uom, price
    open_orders.csv  customer_id, erp_order, po_number, status, ship_date, carrier, tracking

SKU match order, first hit wins: the cross-reference (the file plus what reps taught the desk), our
own SKU written on the PO, the customer's order history, the same three again with fax misreads
allowed (O/0, I/1, S/5), then a description search. Each match carries where it came from and a
confidence. Every check writes a flag in plain words: red stops the order, yellow needs a click.
"""
import csv
import os
import re

from . import inbox_core

RED, YELLOW, INFO = inbox_core.RED, inbox_core.YELLOW, inbox_core.INFO
PACK_UOMS = ("BX", "PK", "CS", "RL", "DZ")
SOURCES = {"xref": 0.99, "rep": 0.99, "sku": 0.98, "history": 0.92, "fax": 0.8}


def _csv(folder, name):
    p = os.path.join(folder, name)
    if not os.path.isfile(p):
        return []
    with open(p, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def key(part):
    return re.sub(r"[^A-Z0-9]", "", (part or "").upper())


def fax_key(part):
    """The same part with the usual fax misreads folded together."""
    return key(part).translate(str.maketrans({"O": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2"}))


class Book:
    """The ERP export, loaded once per request. `learned` and `senders` are the desk's own rows."""

    def __init__(self, folder, learned=(), senders=()):
        self.items = {r["sku"]: {**r, "pack_qty": int(_f(r.get("pack_qty"), 1)) or 1, "list_price": _f(r.get("list_price")),
                                 "min_qty": _f(r.get("min_qty"), 0), "multiple": int(_f(r.get("multiple"), 1)) or 1}
                      for r in _csv(folder, "items.csv")}
        self.customers = {r["customer_id"]: {**r, "emails": [e.strip().lower() for e in (r.get("emails") or "").split(";") if e.strip()],
                                             "terms_days": int(_f(r.get("terms_days"), 30))}
                          for r in _csv(folder, "customers.csv")}
        self.xref = {}
        for r in _csv(folder, "xref.csv"):
            self.xref[(r["customer_id"], key(r["customer_part"]))] = (r["sku"], "xref", r["customer_part"])
        for r in learned:
            self.xref[(r["customer_id"], key(r["customer_part"]))] = (r["sku"], "rep", r["customer_part"])
        self.prices = {(r["customer_id"], r["sku"]): _f(r["price"]) for r in _csv(folder, "prices.csv")}
        self.stock = {r["sku"]: _f(r["on_hand"]) for r in _csv(folder, "stock.csv")}
        self.history = _csv(folder, "history.csv")
        self.open_orders = _csv(folder, "open_orders.csv")
        self.senders = {r["email"]: r["customer_id"] for r in senders}
        self.sku_keys = {key(s): s for s in self.items}
        self.bought = {}
        for h in self.history:
            self.bought.setdefault(h["customer_id"], set()).add(h["sku"])
        self._tokens = {s: tokens(it["description"] + " " + it.get("category", "")) for s, it in self.items.items()}

    # ------------------------------------------------------------ customer

    def match_customer(self, sender, bill_to=""):
        """(customer_id, how) or (None, why). Sender address, then its domain, then the bill-to name."""
        sender = (sender or "").lower()
        if sender in self.senders:
            return self.senders[sender], "sender (taught by a rep)"
        for cid, c in self.customers.items():
            if sender in c["emails"]:
                return cid, "sender address"
        domain = sender.rsplit("@", 1)[-1]
        hits = [cid for cid, c in self.customers.items() if domain and c.get("domain", "").lower() == domain]
        if len(hits) == 1:
            return hits[0], "sender domain"
        if bill_to:
            want = set(tokens(bill_to)) - {"inc", "llc", "co", "corp", "the", "and"}
            best = max(self.customers.values(), key=lambda c: len(want & set(tokens(c["name"]))), default=None)
            if best and want and len(want & set(tokens(best["name"]))) >= max(1, len(want) // 2):
                return best["customer_id"], "bill-to name"
        return None, "no customer has this sender address or name"

    # ------------------------------------------------------------ SKU

    def match_line(self, cid, line):
        """{sku, source, confidence, alternatives}. source: xref, rep, sku, history, fax, description or none."""
        part = line.get("customer_part") or ""
        ours = line.get("our_part") or ""
        for folded, src_fax in ((key, False), (fax_key, True)):
            k = folded(part)
            if k:
                for (c, pk), (sku, src, _) in self.xref.items():
                    if c == cid and folded(pk) == k and sku in self.items:
                        return self._hit(sku, "fax" if src_fax else src)
            for cand in (ours, part):
                ck = folded(cand)
                if ck:
                    for sk, sku in self.sku_keys.items():
                        if folded(sk) == ck:
                            return self._hit(sku, "fax" if src_fax else "sku")
            if k:
                for h in sorted(self.history, key=lambda h: h.get("order_date", ""), reverse=True):
                    if h["customer_id"] == cid and folded(h.get("customer_part")) == k and h["sku"] in self.items:
                        return self._hit(h["sku"], "fax" if src_fax else "history")
        return self.search(cid, line.get("description") or "")

    def _hit(self, sku, source):
        return {"sku": sku, "source": source, "confidence": SOURCES[source], "alternatives": []}

    def search(self, cid, text):
        q = tokens(text)
        if not q:
            return {"sku": None, "source": "none", "confidence": 0.0, "alternatives": []}
        sizes = {t for t in q if re.match(r"\d", t)}
        scored = []
        for sku, it in self._tokens.items():
            i = set(it)
            hit = len(set(q) & i)
            if not hit:
                continue
            s = 0.75 * hit / len(set(q)) + 0.25 * hit / len(i)
            if sizes and not sizes <= i:
                s *= 0.35
            if sku in self.bought.get(cid, ()):
                s += 0.08
            scored.append((round(min(s, 0.95), 3), sku))
        scored.sort(key=lambda x: (-x[0], x[1]))
        if not scored or scored[0][0] < 0.55:
            return {"sku": None, "source": "none", "confidence": scored[0][0] if scored else 0.0,
                    "alternatives": [s for _, s in scored[:3]]}
        alts = [s for sc, s in scored[1:4] if sc >= scored[0][0] - 0.12]
        return {"sku": scored[0][1], "source": "description", "confidence": round(scored[0][0] * 0.9, 2), "alternatives": alts}

    # ------------------------------------------------------------ checks

    def expected_price(self, cid, sku):
        """(price, basis): the contract price for this customer, else list."""
        if (cid, sku) in self.prices:
            return self.prices[(cid, sku)], "contract"
        return self.items[sku]["list_price"], "list"

    def check_line(self, cid, line, match):
        """The draft values and the flags for one line. `line` is what the PO said."""
        sku = match["sku"]
        flags = []
        draft = {"sku": sku, "qty": line.get("qty") or 0, "uom": line.get("uom") or "", "price": None}
        if not sku:
            alts = ", ".join(match.get("alternatives") or [])
            flags.append(_flag("unknown_part", RED, "No SKU matches this line." + (f" Closest: {alts}." if alts else "")
                               + " Pick the SKU; the desk learns it for next time."))
            return draft, flags
        it = self.items[sku]
        if match["source"] == "description":
            flags.append(_flag("match_check", YELLOW, f"Matched by description ({round(match['confidence'] * 100)}%): "
                               f"{sku} {it['description']}. Check it." + (f" Also close: {', '.join(match['alternatives'])}." if match["alternatives"] else "")))
        elif match["source"] == "fax":
            flags.append(_flag("match_check", YELLOW, f"Part number read from a fax, matched with a misread allowed: {sku}. Check it."))
        qty, uom, sell, pack = draft["qty"], draft["uom"], it["uom"], it["pack_qty"]
        price = line.get("price")
        if not uom:
            draft["uom"] = sell
            uom = sell
        if uom != sell:
            if sell in PACK_UOMS and pack > 1 and uom in ("EA", "FT"):
                packs = qty / pack
                whole = int(packs) if packs == int(packs) else int(packs) + 1
                note = "" if packs == int(packs) else f" ({packs:g} {sell}, rounded up)"
                flags.append(_flag("uom", YELLOW, f"PO says {qty:g} {uom}; we sell {sell} of {pack}. Enter {whole} {sell}{note}?",
                                   {"qty": whole, "uom": sell, "price": round(price * pack, 4) if price is not None else None}))
                draft.update(qty=whole, uom=sell)
                price = round(price * pack, 4) if price is not None else None
            else:
                flags.append(_flag("uom", YELLOW, f"PO says {uom}; we sell {sku} by {sell}"
                                   + (f" of {pack}" if pack > 1 else "") + ". Check the quantity."))
                draft["uom"] = sell
        expected, basis = self.expected_price(cid, sku)
        draft["price"] = expected
        draft["po_price"] = price
        draft["expected_price"], draft["price_basis"] = expected, basis
        q = draft["qty"]
        if price is None:
            flags.append(_flag("price_missing", INFO, f"No price on the PO. {basis.title()} price ${expected:,.2f} applies."))
        elif price < expected - 0.005:
            gap = round((expected - price) * q, 2)
            flags.append(_flag("price_below", YELLOW, f"PO price ${price:,.2f}, {basis} ${expected:,.2f} "
                               f"(${expected - price:,.2f} x {q:g} = ${gap:,.2f}). Bill {basis}?",
                               {"price": expected}, {"at_risk": gap}))
            draft["price"] = price
        elif price > expected + 0.005:
            flags.append(_flag("price_above", INFO, f"PO price ${price:,.2f} is above {basis} ${expected:,.2f}. We bill ${expected:,.2f}."))
        on_hand = self.stock.get(sku, 0.0)
        if on_hand <= 0:
            flags.append(_flag("stock", YELLOW, f"Out of stock: all {q:g} {draft['uom']} go on backorder."))
        elif on_hand < q:
            flags.append(_flag("stock", YELLOW, f"{on_hand:g} on hand; {q - on_hand:g} go on backorder."))
        if it["min_qty"] and q < it["min_qty"]:
            flags.append(_flag("minimum", YELLOW, f"Minimum order is {it['min_qty']:g} {sell}.", {"qty": it["min_qty"]}))
        elif it["multiple"] > 1 and q % it["multiple"]:
            up = (int(q // it["multiple"]) + 1) * it["multiple"]
            flags.append(_flag("multiple", YELLOW, f"Sold in multiples of {it['multiple']}. Enter {up}?", {"qty": up}))
        return draft, flags

    def check_order(self, cid, po, kind, others=(), today=None):
        """Order-level flags. `others` are the desk's own live orders: (customer_id, po_number, label)."""
        flags = []
        if not cid:
            flags.append(_flag("customer", RED, "No customer matches the sender or the bill-to name. Pick the customer."))
        number = (po.get("po_number") or "").upper()
        if not number:
            flags.append(_flag("po_missing", YELLOW, "No PO number found. Enter it or accept without one."))
        elif cid:
            seen = [f"order {h['erp_order']} on {h.get('order_date') or h.get('ship_date') or 'file'}"
                    for h in self.history + self.open_orders if h["customer_id"] == cid and h["po_number"].upper() == number]
            seen += [label for c, n, label in others if c == cid and (n or "").upper() == number]
            if seen:
                flags.append(_flag("duplicate_po", RED, f"PO {number} was already entered: {sorted(set(seen))[0]}. "
                                   "Reject it, or enter it anyway if the buyer meant a new order."))
        if today and po.get("need_by") and po["need_by"] < today:
            flags.append(_flag("need_by", YELLOW, f"Need-by date {po['need_by']} has passed. Call the buyer?"))
        if kind == "change":
            flags.append(_flag("change", RED, "This is a revised PO. Change the open order by hand, then reject this "
                               "draft, or enter it as a new order."))
        return flags


_flag = inbox_core.flag


SYN = {"hcs": "hex cap screw", "blk": "black", "wht": "white", "grn": "green", "zip": "cable", "ties": "tie",
       "lg": "large", "med": "medium", "sm": "small", "xlg": "xl", "ss": "stainless", "galv": "galvanized",
       "conn": "connector", "recept": "receptacle", "brkr": "breaker", "bkr": "breaker", "glove": "gloves",
       "nitril": "nitrile", "ga": "awg", "emt": "emt", "ss304": "stainless", "nuts": "nut", "washers": "washer",
       "screws": "screw", "bolt": "screw", "bolts": "screw", "connectors": "connector", "wires": "wire",
       "bearings": "bearing", "belts": "belt", "breakers": "breaker", "fuses": "fuse", "terminals": "terminal",
       "gal": "gallon", "amp": "a", "amps": "a", "volt": "v", "inch": "in", "wirenut": "wire nut"}
STOP = {"the", "a", "of", "for", "and", "with", "x", "per", "pcs", "ea", "box", "bx", "pk", "case", "roll", "rl",
        "please", "pack", "each", "ft", "qty"}


def tokens(text):
    t = (text or "").lower().replace('"', " in ").replace("″", " in ")
    raw = re.findall(r"\d+-\d+/\d+|\d+/\d+-\d+|\d+/\d+|\d+(?:\.\d+)?|[a-z]+", t)
    out = []
    for w in raw:
        w = SYN.get(w, w)
        for part in w.split():
            if part in STOP:
                continue
            if len(part) > 4 and part.endswith("s") and not part.endswith("ss") and not re.match(r"\d", part):
                part = part[:-1]
            out.append(part)
    return out
