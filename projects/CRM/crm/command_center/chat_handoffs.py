"""Chat follow-up receipts, inside the existing call writer's transaction. No new schema."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import crm_store, identity

KEY = '_chat_handoff'


def ensure(store, db, prof, call, contact_id, stamp, record):
    """One call ID owns one task. A replay reads the original stored call, not new input."""
    row = db.execute('SELECT * FROM calls WHERE id=?', (call['call_id'],)).fetchone()
    notes = json.loads(row['notes'])
    if isinstance(notes.get(KEY), dict):
        return notes[KEY]
    cfg = ((prof.get('crm') or {}).get('calls') or {}).get('chat_follow_up') or {}
    if row['channel'] != 'chat' or not call.get('requires_follow_up') or cfg.get('enabled') is not True:
        return None
    from .call_log import is_real
    original = dict(row) | {'transcript': json.loads(row['transcript'])}
    if not is_real(original):
        receipt = {'state': 'ignored', 'reason': 'Spam or empty inquiry; no follow-up created.'}
    elif not contact_id:
        receipt = {'state': 'needs_contact', 'reason': 'No phone or email was provided. Review the chat.'}
    else:
        contact = db.execute('SELECT owner,name FROM contacts WHERE id=?', (contact_id,)).fetchone()
        owner = identity.person(prof, contact['owner'])
        hours = cfg.get('due_hours', 24)
        if type(hours) is not int or not 1 <= hours <= 168:
            raise crm_store.CRMError('Chat follow-up interval must be 1 to 168 hours')
        due = (datetime.fromisoformat(row['received_at']) + timedelta(hours=hours)).isoformat()
        task = store._change(db, prof, owner, 'task.create', {
            'contact_id': contact_id, 'title': ('Follow up on website inquiry: ' + contact['name'])[:200],
            'due_at': due, 'category': cfg.get('category', ''), 'action': 'none'})
        record(db, 'task.create', task['id'], {'contact_id': contact_id, 'call_id': row['id'],
                                              'owner': contact['owner'], 'source': 'website_chat'})
        receipt = {'state': 'delivered', 'task_id': task['id'], 'owner': contact['owner'], 'due_at': due}
    notes[KEY] = receipt
    db.execute('UPDATE calls SET notes=? WHERE id=?', (json.dumps(notes, ensure_ascii=False), row['id']))
    record(db, 'chat.handoff', row['id'], receipt)
    return receipt


def monitor(store, prof, person, at=None):
    """Private, read-only delivery view. Unmatched source failures are for all-record operators."""
    from . import access
    store.authorize(prof, person)
    cfg = ((prof.get('crm') or {}).get('calls') or {}).get('chat_follow_up') or {}
    if cfg.get('enabled') is not True:
        return {'enabled': False, 'items': []}
    if access.sees(prof, person) != 'all':
        return {'enabled': True, 'restricted': True, 'items': []}
    directory = Path(cfg.get('delivery_dir') or '')
    at = at or datetime.now(timezone.utc)
    result = {'enabled': True, 'items': [], 'worker_healthy': False, 'unreadable': 0, 'checked_at': ''}
    try:
        heartbeat = json.loads((directory / 'worker.json').read_text(encoding='utf-8'))
        seen = datetime.fromisoformat(heartbeat['checked_at'])
        result.update(checked_at=heartbeat['checked_at'], unreadable=heartbeat.get('unreadable', 0),
                      worker_healthy=0 <= (at - seen).total_seconds() <= 180)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    with store.connection() as db:
        for path in directory.glob('CH*.json') if cfg.get('delivery_dir') else []:
            try:
                job = json.loads(path.read_text(encoding='utf-8'))
                datetime.fromisoformat(job['created_at'])  # validate before sorting the whole view
                state = job['state']
                if state == 'ignored':
                    continue
                receipt = job.get('receipt') or {}
                if not isinstance(receipt, dict):
                    raise ValueError('Invalid delivery receipt')
                handoff = receipt.get('handoff') or {}
                if not isinstance(handoff, dict):
                    raise ValueError('Invalid follow-up receipt')
                task = db.execute('SELECT id,status,due_at,contact_id,archived_at FROM tasks WHERE id=?',
                                  (handoff.get('task_id'),)).fetchone()
                contact = db.execute('SELECT id,name,owner,deleted_at FROM contacts WHERE id=?',
                                     (receipt.get('contact_id'),)).fetchone()
                if contact and contact['deleted_at']:
                    continue
                if state == 'delivered' and (not task or task['archived_at']):
                    state = 'needs_attention'
                elif state == 'delivered' and task['status'] == 'open' and datetime.fromisoformat(task['due_at']) < at:
                    state = 'follow_up_due'
                item = {'id': job['call_id'], 'state': state, 'attempts': job['attempts'],
                        'created_at': job['created_at'], 'updated_at': job['updated_at'],
                        'error': job.get('error', ''), 'name': contact['name'] if contact else
                            str((job.get('payload') or {}).get('name') or 'Website inquiry')[:120],
                        'contact_id': contact['id'] if contact else None,
                        'owner': contact['owner'] if contact else None,
                        'task_id': task['id'] if task and not task['archived_at'] else None,
                        'task_status': task['status'] if task else None, 'due_at': task['due_at'] if task else None,
                        'next_attempt': datetime.fromtimestamp(job['next_attempt'], timezone.utc).isoformat()
                            if job.get('next_attempt') else None}
                if state == 'needs_attention' and not item['error']:
                    item['error'] = 'The saved follow-up task is missing or archived. Review the CRM record.'
                result['items'].append(item)
            except (OSError, ValueError, KeyError, TypeError, OverflowError):
                result['unreadable'] += 1
    priority = {'needs_attention': 0, 'needs_contact': 1, 'follow_up_due': 2,
                'retrying': 3, 'delivering': 4, 'preparing': 5, 'delivered': 6}
    items = result['items']
    result['counts'] = {'delivered': sum(i['state'] in ('delivered', 'follow_up_due') for i in items),
                        'retrying': sum(i['state'] in ('retrying','delivering','preparing') for i in items),
                        'attention': sum(i['state'] in ('needs_attention','needs_contact','follow_up_due') for i in items)}
    result['items'] = sorted(items, key=lambda i: (priority.get(i['state'], 0),
                                                 -datetime.fromisoformat(i['created_at']).timestamp()))
    result['total'] = len(items)
    return result
