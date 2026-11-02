"""Order Desk: what a person reads. The queue, one order beside its PO, one email, the files, the
part number table and the owner's numbers. A mixin of order_desk.Desk, split out to keep each engine
file small; every write stays in order_desk.py."""
import json
from pathlib import Path

from . import crm_store, inbox_core, order_desk as od, order_desk_erp, order_desk_match


class Views:
    # ------------------------------------------------------------ reads

    def queue(self, person):
        od.check(self.prof, person, "read")
        self.check_inbox()
        with self.db() as db:
            od.migrate(db)
            book = self.book(db)
            out = []
            for m in db.execute("SELECT m.*, o.id AS order_id, o.status AS order_status, o.customer_id, o.po_number, o.number, "
                                "o.flags AS order_flags FROM od_messages m LEFT JOIN od_orders o ON o.message_id=m.id "
                                "ORDER BY m.received_at DESC"):
                red = yellow = 0
                lines = 0
                if m["order_id"]:
                    flags = json.loads(m["order_flags"])
                    for ln in db.execute("SELECT flags FROM od_lines WHERE order_id=? AND removed=0", (m["order_id"],)):
                        lines += 1
                        flags += json.loads(ln["flags"])
                    red = sum(1 for f in flags if f["level"] == "red" and not f["resolved"])
                    yellow = sum(1 for f in flags if f["level"] == "yellow" and not f["resolved"])
                c = book.customers.get(m["customer_id"] or "") if m["order_id"] else book.customers.get(book.match_customer(m["sender"])[0] or "")
                state = m["order_status"] or ("answered" if m["reply_sent_at"] else m["status"])
                out.append({"message": m["id"], "order": m["order_id"], "kind": m["kind"], "received_at": m["received_at"],
                            "sender": m["sender"], "subject": m["subject"], "customer": c["name"] if c else "",
                            "po_number": m["po_number"] or "", "number": m["number"], "state": state,
                            "lines": lines, "red": red, "yellow": yellow})
        return {"desk": self._desk_facts()["name"], "items": out, "rights": sorted(od.rights(self.prof, person)),
                "reader": self.cfg.get("reader", "demo"), "sample": bool(self.cfg.get("sample"))}

    def order(self, person, oid):
        od.check(self.prof, person, "read")
        with self.db() as db:
            od.migrate(db)
            o = db.execute("SELECT * FROM od_orders WHERE id=?", (oid,)).fetchone()
            if not o:
                raise crm_store.CRMError("Order not found", 404)
            m = db.execute("SELECT * FROM od_messages WHERE id=?", (o["message_id"],)).fetchone()
            book = self.book(db)
            lines = []
            for ln in db.execute("SELECT * FROM od_lines WHERE order_id=? ORDER BY n", (oid,)):
                it = book.items.get(ln["sku"] or "")
                lines.append({**{k: ln[k] for k in ln.keys() if k not in ("flags", "alternatives")},
                              "flags": json.loads(ln["flags"]), "alternatives": json.loads(ln["alternatives"]),
                              "item": it["description"] if it else "", "on_hand": book.stock.get(ln["sku"] or "")})
            c = book.customers.get(o["customer_id"] or "")
            return {**{k: o[k] for k in o.keys() if k not in ("flags", "confirmation", "files")},
                    "flags": json.loads(o["flags"]), "confirmation": json.loads(o["confirmation"]) if o["confirmation"] else None,
                    "files": json.loads(o["files"]), "customer": c["name"] if c else "", "lines": lines,
                    "message": self._message(m), "customers": sorted(({"id": k, "name": v["name"]} for k, v in book.customers.items()),
                                                                     key=lambda x: x["name"]),
                    "total": round(sum((ln["qty"] or 0) * (ln["price"] or 0) for ln in lines if not ln["removed"] and ln["sku"]), 2),
                    "blocking": self._blocking(o, lines), "rights": sorted(od.rights(self.prof, person)),
                    "staging": order_desk_erp.staging(lines, self.cfg.get("staging_columns")) if o["status"] == "approved" else ""}

    def message(self, person, mid):
        od.check(self.prof, person, "read")
        with self.db() as db:
            od.migrate(db)
            m = db.execute("SELECT * FROM od_messages WHERE id=?", (mid,)).fetchone()
            if not m:
                raise crm_store.CRMError("Message not found", 404)
            return {**self._message(m), "reply": json.loads(m["reply"]) if m["reply"] else None,
                    "reply_sent_at": m["reply_sent_at"], "reply_sent_by": m["reply_sent_by"], "status": m["status"],
                    "rights": sorted(od.rights(self.prof, person))}

    def items(self, person, q, cid=""):
        """Item master search for the rep's SKU picker: a SKU prefix, else the description search."""
        od.check(self.prof, person, "read")
        with self.db() as db:
            od.migrate(db)
            book = self.book(db)
        k = order_desk_match.key(q)
        hits = [s for s in sorted(book.items) if k and order_desk_match.key(s).startswith(k)][:8]
        if len(hits) < 8 and q.strip():
            scored = sorted(((len(set(order_desk_match.tokens(q)) & set(book._tokens[s])), s) for s in book.items), key=lambda x: (-x[0], x[1]))
            hits += [s for n, s in scored if n and s not in hits][:8 - len(hits)]
        return {"items": [{"sku": s, "description": book.items[s]["description"], "uom": book.items[s]["uom"],
                           "on_hand": book.stock.get(s, 0)} for s in hits]}

    def _message(self, m):
        return {"id": m["id"], "kind": m["kind"], "received_at": m["received_at"], "sender": m["sender"],
                "from_name": m["from_name"], "subject": m["subject"], "body": m["body"],
                "docs": json.loads(m["docs"]), "files": json.loads(m["files"])}

    def file(self, person, mid, n):
        """One stored attachment, for the side-by-side view: (bytes, content type, name)."""
        od.check(self.prof, person, "read")
        with self.db() as db:
            od.migrate(db)
            m = db.execute("SELECT files FROM od_messages WHERE id=?", (mid,)).fetchone()
        files = json.loads(m["files"]) if m else []
        if not (0 <= n < len(files)):
            raise crm_store.CRMError("File not found", 404)
        f = files[n]
        return (self.out / "files" / mid / f["stored"]).read_bytes(), f["type"], f["name"]

    def output(self, person, oid, layout):
        """The ERP import file of an approved order."""
        od.check(self.prof, person, "read")
        with self.db() as db:
            od.migrate(db)
            o = db.execute("SELECT files, status FROM od_orders WHERE id=?", (oid,)).fetchone()
        files = json.loads(o["files"]) if o else {}
        if not o or o["status"] != "approved" or layout not in files:
            raise crm_store.CRMError("No import file: the order is not approved", 404)
        return (self.out / files[layout]).read_bytes(), Path(files[layout]).name

    def xref_csv(self, person):
        """The whole cross-reference, file rows and learned rows: the client's own asset to load back
        into their ERP's customer part number table."""
        od.check(self.prof, person, "read")
        with self.db() as db:
            od.migrate(db)
            book = self.book(db)
            learned = {(r["customer_id"], r["part_key"]): dict(r) for r in db.execute("SELECT * FROM od_xref")}
        rows = []
        for (cid, k), (sku, src, part) in sorted(book.xref.items()):
            how = learned[(cid, k)]["how"] if (cid, k) in learned else "erp table"
            rows.append({"customer_id": cid, "customer_part": part, "sku": sku, "source": how})
        return order_desk_erp.write_csv(("customer_id", "customer_part", "sku", "source"), rows)

    def _blocking(self, o, lines):
        """What still stops approval, in plain words: the inbox core's gate over this order's flags."""
        if o["status"] != "draft":
            return []
        live = [ln for ln in lines if not ln["removed"]]
        flags = json.loads(o["flags"]) if isinstance(o["flags"], str) else o["flags"]
        return (inbox_core.still_open([("", flags)]) + ([] if live else ["The order has no lines."])
                + inbox_core.still_open([(f"Line {ln['n']}: ", ln["flags"]) for ln in live]))

    # ------------------------------------------------------------ the owner's numbers

    def kpis(self, person):
        od.check(self.prof, person, "read")
        tz = od.zone(self.prof)
        cutoff = self.cfg.get("cutoff", "15:00")
        with self.db() as db:
            od.migrate(db)
            book = self.book(db)
            orders = [dict(r) for r in db.execute("SELECT o.*, m.received_at FROM od_orders o JOIN od_messages m ON m.id=o.message_id")]
            lines = {}
            for ln in db.execute("SELECT * FROM od_lines WHERE removed=0"):
                lines.setdefault(ln["order_id"], []).append(dict(ln, flags=json.loads(ln["flags"])))
            replies = [dict(r) for r in db.execute("SELECT received_at, reply_sent_at FROM od_messages WHERE kind='status'")]
            learned = db.execute("SELECT count(*) FROM od_xref").fetchone()[0]
        mins, day, hhmm = inbox_core.minutes, inbox_core.local_day, inbox_core.local_hhmm
        done = [o for o in orders if o["status"] == "approved"]
        flagged = lambda o: [f for ln in lines.get(o["id"], []) for f in ln["flags"] if f["level"] in od.OPEN] + \
                            [f for f in json.loads(o["flags"]) if f["level"] in od.OPEN]
        here = od.local_now(self.prof)
        day_over = lambda o: day(o["received_at"], tz) < here.date() or here.strftime("%H:%M") >= cutoff
        # Counted once it can no longer change: approved, or still waiting after its day's cutoff.
        before_cut = [o for o in orders if hhmm(o["received_at"], tz) < cutoff
                      and (o["status"] == "approved" or (o["status"] == "draft" and day_over(o)))]
        same_day = [o for o in before_cut if o["status"] == "approved"
                    and day(o["approved_at"], tz) == day(o["received_at"], tz)]
        caught = [(ln, f) for o in done for ln in lines.get(o["id"], []) for f in ln["flags"]
                  if f["code"] == "price_below" and (ln["price"] or 0) >= (ln["expected_price"] or 0) - 0.005]
        all_lines = [ln for o in orders if o["status"] != "rejected" for ln in lines.get(o["id"], [])]
        from_table = [ln for ln in all_lines if ln["first_source"] in ("xref", "rep", "sku")]
        pct, med = inbox_core.pct, inbox_core.median_or_none
        return {
            "orders": {"received": len(orders), "approved": len(done), "waiting": sum(1 for o in orders if o["status"] == "draft"),
                       "rejected": sum(1 for o in orders if o["status"] == "rejected")},
            "minutes_per_order": med([mins(o["received_at"], o["approved_at"]) for o in done]),
            "hands_on_minutes": med([mins(o["opened_at"], o["approved_at"]) for o in done if o["opened_at"]]),
            "touchless_pct": pct(sum(1 for o in done if not flagged(o) and not o["edits"]), len(done)),
            "no_edit_pct": pct(sum(1 for o in done if not o["edits"]), len(done)),
            "error_pct": pct(sum(1 for o in done if o["error_at"]), len(done)),
            "same_day_pct": pct(len(same_day), len(before_cut)),
            "confirmation_minutes": med([mins(o["received_at"], o["confirmation_sent_at"]) for o in done if o["confirmation_sent_at"]]),
            "price_exceptions": {"count": len(caught), "dollars": round(sum(f.get("at_risk", 0) for _, f in caught), 2)},
            "xref_coverage_pct": pct(len(from_table), len(all_lines)),
            "xref_rows": {"erp_table": len(book.xref) - learned, "learned": learned},
            "status_replies": {"asked": len(replies), "answered": sum(1 for r in replies if r["reply_sent_at"]),
                               "median_minutes": med([mins(r["received_at"], r["reply_sent_at"]) for r in replies if r["reply_sent_at"]])},
            "how": {"minutes_per_order": "email arrival to approval, median", "hands_on_minutes": "rep opens the draft to approval, median",
                    "touchless_pct": "approved with no flag and no edit", "no_edit_pct": "approved with no manual edit",
                    "error_pct": "approved orders the owner marked as an entry error",
                    "same_day_pct": f"received before {cutoff} and approved the same day",
                    "confirmation_minutes": "email arrival to confirmation sent, median",
                    "price_exceptions": "PO price below contract, caught and billed at contract",
                    "xref_coverage_pct": "lines matched from the cross-reference or our own SKU on first read"}}
