"""Audit and outbox events inside the caller's existing CRM transaction."""
import json
from uuid import uuid4
from . import crm_store


def record(db, key, actor, action, data, result):
    if not db.in_transaction:
        raise crm_store.CRMError('CRM events require an active transaction', 503)
    company = result.pop("created_company", None)
    if company:
        company_event, company_stamp = str(uuid4()), crm_store.now()
        company_payload = json.dumps({"entity_id": company["id"], "data": company})
        db.execute("INSERT INTO audit VALUES (?,?,?,?,?,?,?)",
                   (company_event, key, actor, "contact.create", company["id"], company_payload, company_stamp))
        db.execute("INSERT INTO outbox (id,event_type,payload,created_at) VALUES (?,?,?,?)",
                   (company_event, "contact.create", company_payload, company_stamp))
    eid, stamp = str(uuid4()), crm_store.now()
    audit_result = dict(result)
    if action == "node.review":
        audit_result["review"] = dict(db.execute("SELECT * FROM node_reviews WHERE id=?", (result["id"],)).fetchone())
    payload = json.dumps({"entity_id": result["id"], "data": data, "result": audit_result})
    db.execute("INSERT INTO audit VALUES (?,?,?,?,?,?,?)",
               (eid, key, actor, action, result["id"], payload, stamp))
    db.execute("INSERT INTO outbox (id,event_type,payload,created_at) VALUES (?,?,?,?)",
               (eid, action, payload, stamp))
    result.pop('payment', None)  # replay acknowledgements never expose old financial content
