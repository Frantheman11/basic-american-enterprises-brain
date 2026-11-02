"""Manual payment receipts projected from the CRM's immutable audit events.

Store.execute owns the transaction, permission boundary, idempotency and audit.
No money is moved, no invoice is sent, and no separate database is introduced.
"""
import json
import re
from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import access, crm_store, stripe_reporting

PROTOCOL = 1
ACTIONS = {'payment.record', 'payment.clear', 'payment.void', 'payment.refund'}
METHODS = {'cash': 'Cash', 'check': 'Check', 'bank_transfer': 'Bank transfer', 'other': 'Other'}
FIELDS = {
    'payment.record': {'contact_id', 'amount_cents', 'method', 'received_on', 'reference', 'note', 'duplicate_reason', 'source_ref'},
    'payment.clear': {'id', 'revision', 'cleared_on'},
    'payment.void': {'id', 'revision', 'reason'},
    'payment.refund': {'id', 'revision', 'amount_cents', 'received_on', 'note'},
}


def enabled(prof):
    return ((prof.get('crm') or {}).get('payments') or {}).get('enabled') is True


def today(prof):
    name = ((prof.get('crm') or {}).get('payments') or {}).get('timezone', 'UTC')
    try:
        return datetime.now(ZoneInfo(name)).date()
    except (ZoneInfoNotFoundError, TypeError):
        raise crm_store.CRMError('Payment timezone is not configured correctly', 503) from None


def valid_date(value, label, prof):
    try:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError()
        parsed = date.fromisoformat(value)
        if parsed > today(prof):
            raise ValueError()
        return value
    except ValueError:
        raise crm_store.CRMError(label + ' must be a real date, today or earlier') from None


def amount(value):
    if type(value) is not int or not 1 <= value <= 100_000_000_000:
        raise crm_store.CRMError('Enter a positive amount with at most two decimal places')
    return value


def ledger(db):
    """Full-state events allow deterministic projection without altering old receipts."""
    result = {}
    for event in db.execute("SELECT payload FROM audit WHERE action IN ('payment.record','payment.clear','payment.void','payment.refund')"):
        try:
            row = json.loads(event['payload'])['result']['payment']
            if row['id'] not in result or row['revision'] > result[row['id']]['revision']:
                result[row['id']] = row
        except (KeyError, ValueError, TypeError):
            raise crm_store.CRMError('A payment history entry needs review; totals are unavailable', 503) from None
    return result


def refunded(rows, rid):
    return sum(p['amount_cents'] for p in rows.values() if p.get('parent_id') == rid and p['status'] != 'voided')


def change(store, db, prof, person, action, data):
    if not enabled(prof):
        raise crm_store.CRMError('Payment recording is not enabled', 403)
    if set(data) - FIELDS[action]:
        raise crm_store.CRMError('Unexpected payment fields')
    rows = ledger(db)
    stamp = crm_store.now()
    if action == 'payment.record':
        contact = store.contact(db, prof, person, data.get('contact_id'))
        if contact['kind'] != 'company' or contact['lifecycle'] == 'archived':
            raise crm_store.CRMError('Choose an active company')
        method = data.get('method')
        if not isinstance(method, str) or method not in METHODS:
            raise crm_store.CRMError('Choose Cash, Check, Bank transfer or Other')
        received = valid_date(data.get('received_on'), 'Received date', prof)
        cents = amount(data.get('amount_cents'))
        reference = crm_store.text(data.get('reference', ''), 'Reference', 120)
        source_ref = crm_store.text(data.get('source_ref', ''), 'Source reference', 200)
        duplicate_reason = crm_store.text(data.get('duplicate_reason', ''), 'Reason this is a separate payment', 500)
        for previous in rows.values():
            if source_ref and source_ref == previous.get('source_ref'):
                raise crm_store.CRMError('This source already has a payment record', 409)
            if previous['contact_id'] != contact['id'] or previous['status'] == 'voided' or previous['kind'] != 'payment':
                continue
            if method == previous['method'] and ((reference and reference.casefold() == previous['reference'].casefold()) or
                    (cents == previous['amount_cents'] and received == previous['received_on'])) and not duplicate_reason:
                raise crm_store.CRMError('A similar payment is already recorded. Review the history, or explain why this is a separate payment.', 409)
        row = {'id': str(uuid4()), 'contact_id': contact['id'], 'kind': 'payment',
               'amount_cents': cents, 'currency': (prof.get('crm') or {}).get('currency', 'USD'),
               'method': method, 'received_on': received, 'collected_on': None if method == 'check' else received,
               'status': 'pending' if method == 'check' else 'collected', 'reference': reference,
               'note': crm_store.text(data.get('note', ''), 'Payment note', 2000),
               'source_ref': source_ref, 'duplicate_reason': duplicate_reason,
               'revision': 1, 'created_at': stamp, 'updated_at': stamp, 'recorded_by': person.id}
    else:
        rid = crm_store.text(data.get('id'), 'Payment ID', 80, True)
        original = rows.get(rid)
        if not original:
            raise crm_store.CRMError('Payment not found', 404)
        contact = store.contact(db, prof, person, original['contact_id'])
        if type(data.get('revision')) is not int or data['revision'] != original['revision']:
            raise crm_store.CRMError('This payment changed. Refresh and reopen it.', 409)
        if original['status'] == 'voided':
            raise crm_store.CRMError('This entry is already voided', 409)
        row = {**original, 'revision': original['revision'] + 1, 'updated_at': stamp}
        if action == 'payment.clear':
            if original['method'] != 'check' or original['status'] != 'pending':
                raise crm_store.CRMError('Only a pending check can be marked cleared', 409)
            cleared = valid_date(data.get('cleared_on'), 'Cleared date', prof)
            if cleared < original['received_on']:
                raise crm_store.CRMError('Cleared date cannot precede receipt')
            row.update(status='collected', collected_on=cleared)
        elif action == 'payment.void':
            if refunded(rows, rid):
                raise crm_store.CRMError('This payment has refund records. Review those entries first.', 409)
            row.update(status='voided', void_reason=crm_store.text(data.get('reason'), 'Correction reason', 500, True))
        else:
            if original['kind'] != 'payment' or original['status'] != 'collected':
                raise crm_store.CRMError('Record a refund against a collected payment', 409)
            cents = amount(data.get('amount_cents'))
            if cents > original['amount_cents'] - refunded(rows, rid):
                raise crm_store.CRMError('Refund exceeds the remaining collected payment', 409)
            received = valid_date(data.get('received_on'), 'Refund date', prof)
            if received < original['collected_on']:
                raise crm_store.CRMError('Refund date cannot precede collection')
            row = {**original, 'id': str(uuid4()), 'parent_id': rid, 'kind': 'refund', 'amount_cents': cents,
                   'received_on': received, 'collected_on': received, 'reference': '', 'source_ref': '',
                   'note': crm_store.text(data.get('note'), 'Refund note', 2000, True),
                   'revision': 1, 'created_at': stamp, 'updated_at': stamp, 'recorded_by': person.id}
    # Human-readable history is a companion, not the source of payment totals.
    body = f"{action.replace('payment.', 'Payment ')}: {row['currency']} {row['amount_cents']/100:.2f} by {METHODS[row['method']]}. "
    body += f"Received {row['received_on']}; {row['status']}. Receipt {row['id']}."
    if row.get('note'):
        body += '\n' + row['note']
    if action == 'payment.void':
        body += '\nCorrection: ' + row['void_reason']
    store._change(db, prof, person, 'activity.add', {'contact_id': row['contact_id'], 'kind': 'note', 'body': body})
    return {'ok': True, 'id': row['id'], 'payment': row}


def snapshot(store, prof, person):
    store.authorize(prof, person)
    if not enabled(prof):
        return {'enabled': False}
    with store.connection() as db:
        if not db.in_transaction:
            db.execute('BEGIN')
        # Keep receipts for deleted/archived companies. Ownership still controls visibility.
        scope, params = access.contact_scope(prof, person, 'c')
        contacts = {r['id']: dict(r) for r in db.execute('SELECT c.id,c.name,c.owner,c.deleted_at FROM contacts c WHERE 1=1' + scope, params)}
        all_rows = ledger(db)
        rows = [{**p, 'company_name': contacts[p['contact_id']]['name'],
                 'company_deleted': bool(contacts[p['contact_id']]['deleted_at']),
                 'refunded_cents': refunded(all_rows, p['id'])}
                for p in all_rows.values() if p['contact_id'] in contacts]
    return {'enabled': True, 'writable': access.can_do(prof, person, 'crm_edit'),
            'stripe': stripe_reporting.snapshot(prof, person),
            'currency': (prof.get('crm') or {}).get('currency', 'USD'), 'today': today(prof).isoformat(),
            'scope': 'business' if access.sees(prof, person) == 'all' else 'your companies',
            'rows': sorted(rows, key=lambda p: (p['received_on'], p['created_at']), reverse=True)}


def summarize(model, hidden=()):
    if not model or not model.get('enabled'):
        return {'enabled': False}
    rows = [p for p in model['rows'] if p['contact_id'] not in hidden and not p['company_name'].lower().endswith('(sample)')]
    currency = model['currency']
    if any(p['currency'] != currency for p in rows):
        raise crm_store.CRMError('Payment currencies differ; review currency configuration before showing totals', 503)
    month = model['today'][:7]
    settled = [p for p in rows if p['status'] == 'collected']
    net = lambda p: p['amount_cents'] * (-1 if p['kind'] == 'refund' else 1)
    manual = sum(net(p) for p in settled if p['collected_on'][:7] == month)
    stripe = model.get('stripe') or {'state': 'not_connected'}
    combined = None
    if stripe.get('state') == 'connected' and stripe.get('month') == month:
        if stripe.get('currency') == currency:
            combined = manual + stripe['collected_cents']
    start = date.fromisoformat(month + '-01')
    previous = (start - timedelta(days=1)).replace(day=1)
    periods = {}
    for key, label, since, until in (
        ('all_time', 'All time', '', model['today']),
        ('this_month', start.strftime('%B %Y'), start.isoformat(), model['today']),
        ('last_month', previous.strftime('%B %Y'), previous.isoformat(), (start-timedelta(days=1)).isoformat()),
    ):
        manual_cents = sum(net(p) for p in settled if since <= p['collected_on'] <= until)
        complete = combined is not None and stripe.get('history_complete') is True
        relevant = [r for r in stripe.get('all_rows', []) if since <= r['date'] <= until
                    and r['category'] in stripe_reporting.COLLECTIONS]
        stripe_cents = sum(r['amount_cents'] for r in relevant) if complete else None
        fees = sum(r['fee_cents'] for r in relevant) if complete else None
        # Older deployed readers can still report this month, never implied history.
        if key == 'this_month' and combined is not None and not complete:
            stripe_cents, fees = stripe['collected_cents'], stripe['fees_cents']
        periods[key] = {'label': label, 'from': since, 'to': until, 'manual_cents': manual_cents,
                        'stripe_cents': stripe_cents, 'fees_cents': fees,
                        'combined_cents': None if stripe_cents is None else manual_cents + stripe_cents}
    return {**model, 'rows': rows, 'month': month, 'stripe': stripe,
            'combined_cents': combined, 'periods': periods,
            'combined_total_cents': periods['all_time']['combined_cents'],
            'collected_cents': manual,
            'total_cents': sum(net(p) for p in settled),
            'pending_cents': sum(p['amount_cents'] for p in rows if p['status'] == 'pending')}
