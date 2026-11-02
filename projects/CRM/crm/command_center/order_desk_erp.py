"""Order Desk: what an approved order turns into. The ERP import file, the confirmation email draft,
and the draft reply to a "where is my order" email. Nothing here sends anything.

Two import layouts:

clipboard staging (staging()): the same lines as tab-separated rows for a paste into the ERP's own
  order-entry grid. The v1 route for an ERP with no import turned on.

generic (order-desk-generic/1): one row per line, UTF-8 CSV with a header row. Columns:
    desk_order, customer_id, customer_name, po_number, order_date, need_by, ship_to, line, sku,
    customer_part, description, qty, uom, unit_price, extended, approved_by, approved_at
  Dates are YYYY-MM-DD; prices have up to 4 decimals; qty is in the SKU's sell unit. Most ERP
  import tools (P21 order import, SX.e flat-file EDI batch, Sage 100 Visual Integrator, Acumatica
  import scenarios) map a file like this once, on their own setup screen.

qbo (QuickBooks Online "Import invoices", CSV): Intuit's help page and its guide PDF (both read
  2026-10-28) require Invoice number, Customer, Invoice date, Due date and Item amount; item names
  must match the QuickBooks product name exactly; at most 1,000 rows and 100 invoices per file; no
  negative lines; "Custom transaction numbers" must be on to keep our numbers. The other columns
  below are optional and are matched on QuickBooks' mapping screen. Confirm against the sample CSV in
  the client's own account at the pilot (Settings > Import data > Invoices).
"""
import csv
import io
import re
from datetime import date, datetime, timedelta
from email.message import EmailMessage

GENERIC = ("desk_order", "customer_id", "customer_name", "po_number", "order_date", "need_by", "ship_to", "line",
           "sku", "customer_part", "description", "qty", "uom", "unit_price", "extended", "approved_by", "approved_at")
QBO = ("InvoiceNo", "Customer", "InvoiceDate", "DueDate", "Item(Product/Service)", "ItemDescription", "ItemQuantity",
       "ItemRate", "ItemAmount", "Memo")
QBO_MAX_ROWS, QBO_MAX_INVOICES = 1000, 100
LAYOUTS = ("generic", "qbo")


def _n(v):
    return f"{v:.4f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def generic_rows(order):
    for ln in order["lines"]:
        yield {"desk_order": order["number"], "customer_id": order["customer_id"], "customer_name": order["customer_name"],
               "po_number": order["po_number"], "order_date": order["order_date"], "need_by": order["need_by"],
               "ship_to": order["ship_to"], "line": ln["n"], "sku": ln["sku"], "customer_part": ln["customer_part"],
               "description": ln["description"], "qty": _n(float(ln["qty"])), "uom": ln["uom"],
               "unit_price": _n(float(ln["price"])), "extended": f"{ln['qty'] * ln['price']:.2f}",
               "approved_by": order["approved_by"], "approved_at": order["approved_at"]}


def qbo_rows(order):
    d = date.fromisoformat(order["order_date"])
    due = d + timedelta(days=int(order.get("terms_days") or 30))
    for ln in order["lines"]:
        amount = round(ln["qty"] * ln["price"], 2)
        if amount < 0:
            raise ValueError("QuickBooks Online imports no negative lines")
        yield {"InvoiceNo": order["number"], "Customer": order["customer_name"], "InvoiceDate": d.strftime("%m/%d/%Y"),
               "DueDate": due.strftime("%m/%d/%Y"), "Item(Product/Service)": ln["sku"], "ItemDescription": ln["description"],
               "ItemQuantity": _n(float(ln["qty"])), "ItemRate": _n(float(ln["price"])), "ItemAmount": f"{amount:.2f}",
               "Memo": f"PO {order['po_number']}" if order["po_number"] else ""}


STAGING = ("sku", "qty", "uom", "price")


def staging(lines, columns=None):
    """Clipboard staging, the no-integration route: the approved lines as tab-separated rows, no header,
    in the column order of the ERP's order-entry grid (`order_desk.staging_columns`). The rep pastes them
    into the first empty line. Works beside any ERP whose grid takes a paste, with no import set up."""
    cols = [c for c in (columns or STAGING) if c in ("sku", "qty", "uom", "price", "description", "customer_part")]
    out = []
    for ln in lines:
        if ln.get("removed") or not ln.get("sku"):
            continue
        row = {"sku": ln["sku"], "qty": _n(float(ln["qty"])), "uom": ln["uom"], "price": _n(float(ln["price"])),
               "description": ln.get("item") or ln.get("description", ""), "customer_part": ln.get("customer_part", "")}
        out.append("\t".join(str(row[c]).replace("\t", " ") for c in cols))
    return "\r\n".join(out)


def write_csv(header, rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=header, lineterminator="\r\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8")


def import_file(order, layout):
    if layout == "generic":
        return write_csv(GENERIC, generic_rows(order))
    if layout == "qbo":
        return write_csv(QBO, qbo_rows(order))
    raise ValueError(f"Unknown import layout {layout!r}; use one of {', '.join(LAYOUTS)}")


def qbo_batches(orders):
    """Many approved orders as QuickBooks files inside its limits: 100 invoices, 1,000 rows each.
    An order is never split across two files."""
    batches, rows, count = [], [], 0
    for o in orders:
        mine = list(qbo_rows(o))
        if len(mine) > QBO_MAX_ROWS:
            raise ValueError(f"Order {o['number']} has more lines than one QuickBooks file takes")
        if rows and (len(rows) + len(mine) > QBO_MAX_ROWS or count + 1 > QBO_MAX_INVOICES):
            batches.append(write_csv(QBO, rows))
            rows, count = [], 0
        rows += mine
        count += 1
    if rows:
        batches.append(write_csv(QBO, rows))
    return batches


# ---------------------------------------------------------------- emails, drafts only

def _eml(sender, to, subject, body, reply_to_id=""):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, to, subject
    if reply_to_id:
        m["In-Reply-To"] = reply_to_id
        m["References"] = reply_to_id
    m["X-Order-Desk"] = "draft: a person sends this"
    m.set_content(body)
    return bytes(m)


def confirmation(order, desk, stock, approved_local):
    """The confirmation email draft: order number, lines, prices, ship date, any backorder."""
    cutoff = desk.get("cutoff", "15:00")
    ships = approved_local.date()
    if approved_local.strftime("%H:%M") >= cutoff:
        ships += timedelta(days=1)
    while ships.weekday() >= 5:
        ships += timedelta(days=1)
    first = (order.get("buyer_name") or "").split(" ")[0] or "there"
    rows, back = [], []
    for ln in order["lines"]:
        have = stock.get(ln["sku"], 0.0)
        note = "" if have >= ln["qty"] else (f"  ({have:g} ship now, {ln['qty'] - have:g} on backorder)" if have > 0
                                             else "  (backorder)")
        if note:
            back.append(ln["sku"])
        rows.append(f"  {ln['n']}. {ln['sku']}  {ln['description'][:48]}  {ln['qty']:g} {ln['uom']} @ ${ln['price']:,.2f}"
                    f" = ${ln['qty'] * ln['price']:,.2f}{note}")
    total = sum(ln["qty"] * ln["price"] for ln in order["lines"])
    po = f"PO {order['po_number']}" if order["po_number"] else "your order"
    body = "\n".join([
        f"Hi {first},", "", f"Thank you for {po}. We entered it as our order {order['number']}.", "", *rows, "",
        f"Total before tax and freight: ${total:,.2f}",
        f"Ship date: {ships.strftime('%a %b %d').replace(' 0', ' ')}" + (" for the lines in stock." if back else "."),
        *(["We will send a date for the backordered lines as soon as we have it."] if back else []),
        f"Ship to: {order['ship_to']}" if order["ship_to"] else "", "",
        "Reply to this email if anything is wrong.", "", desk.get("signature") or desk.get("name", "")])
    subject = f"Order confirmation: {po} (our order {order['number']})"
    return {"to": order["buyer"], "subject": subject, "body": re.sub(r"\n{3,}", "\n\n", body),
            "ship_date": ships.isoformat(), "backorder": back,
            "eml": _eml(desk.get("from_address", ""), order["buyer"], subject, body, order.get("message_id", ""))}


PO_IN_TEXT = re.compile(r"\b(?:P\.?O\.?|purchase order|order)\s*(?:#|no\.?|number)?\s*[:#]?\s*([A-Z0-9-]*\d[A-Z0-9-]*)", re.I)


def status_reply(msg, customer, book, desk_orders, desk):
    """The draft reply to a status question. It looks the PO up in the ERP's open orders, its history
    and the desk's own orders. Not found: the draft asks for the number. Returns {to, subject, body, found}."""
    numbers = []
    for m in PO_IN_TEXT.finditer(msg["subject"] + "\n" + msg["body"]):
        if m.group(1).upper() not in numbers:
            numbers.append(m.group(1).upper())
    cid = customer["customer_id"] if customer else None
    first = (msg.get("from_name") or "").split(" ")[0] or "there"
    found, lines = [], []
    for n in numbers:
        hit = next((o for o in book.open_orders if o["customer_id"] == cid and n in (o["po_number"].upper(), o["erp_order"].upper())), None)
        if hit:
            found.append(n)
            st = hit["status"].lower()
            if st == "shipped":
                lines.append(f"PO {hit['po_number']} (our order {hit['erp_order']}) shipped {hit['ship_date']} by "
                             f"{hit['carrier']}, tracking {hit['tracking']}.")
            elif st == "backorder":
                lines.append(f"PO {hit['po_number']} (our order {hit['erp_order']}) is waiting on a backordered item. "
                             f"Our current ship date is {hit['ship_date']}.")
            else:
                lines.append(f"PO {hit['po_number']} (our order {hit['erp_order']}) is {st} and is scheduled to ship {hit['ship_date']}.")
            continue
        mine = next((o for o in desk_orders if o["customer_id"] == cid and (o["po_number"] or "").upper() == n), None)
        if mine:
            found.append(n)
            lines.append(f"PO {n} is entered as our order {mine['number']}"
                         + (f" and is set to ship {mine['ship_date']}." if mine.get("ship_date") else "; we are confirming the ship date now."))
    if not lines:
        lines.append("I could not find that order under your account. Can you send the PO number or our order number?")
    body = "\n".join([f"Hi {first},", "", *lines, "", desk.get("signature") or desk.get("name", "")])
    subject = "Re: " + msg["subject"] if not msg["subject"].lower().startswith("re:") else msg["subject"]
    return {"to": msg["sender"], "subject": subject, "body": body, "found": found, "asked": numbers}
