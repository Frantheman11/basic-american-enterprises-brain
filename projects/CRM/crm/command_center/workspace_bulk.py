"""Bulk task changes: one transaction, a revision check per task, and a reversible result.

Every task goes through the normal task.update writer, so completion evidence, follow-up and
assignment rules apply to each one. If any task fails, nothing changes.
"""
from . import crm_store

LIMIT = 50
FIELDS = {'due_at', 'assignee', 'department', 'followup_state', 'review_at', 'waiting_on', 'blocked_reason', 'status', 'note'}
# A status or follow-up change can clear the others, so undo restores the whole group.
FOLLOWUP = ('followup_state', 'review_at', 'waiting_on', 'blocked_reason', 'status')
CAPABILITIES = {'task.bulk': 'crm_edit'}


def _title(row):
    title = row['title']
    return title if len(title) <= 60 else title[:57] + '…'


def change(store, db, prof, person, data, stamp):
    if set(data) - {'tasks', 'set'}:
        raise crm_store.CRMError('Unexpected bulk fields')
    tasks, shared = data.get('tasks'), data.get('set') or {}
    if not isinstance(tasks, list) or not tasks:
        raise crm_store.CRMError('Choose at least one task')
    if len(tasks) > LIMIT:
        raise crm_store.CRMError(f'Change at most {LIMIT} tasks at a time')
    if not isinstance(shared, dict) or set(shared) - FIELDS:
        raise crm_store.CRMError('Unexpected bulk fields')
    changed, seen = [], set()
    for item in tasks:
        if not isinstance(item, dict) or set(item) - {'id', 'revision', 'values'}:
            raise crm_store.CRMError('Each task needs an id and revision')
        tid = crm_store.text(item.get('id'), 'Task ID', 80, True)
        if tid in seen:
            raise crm_store.CRMError('A task is listed twice')
        seen.add(tid)
        values = item.get('values') or {}
        if not isinstance(values, dict) or set(values) - FIELDS:
            raise crm_store.CRMError('Unexpected bulk fields')
        fields = {**shared, **values}
        if not set(fields) - {'note'}:
            raise crm_store.CRMError('Choose what to change')
        row = db.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
        if not row:
            raise crm_store.CRMError('Task not found. Nothing was changed.', 404)
        row = dict(row)
        store.contact(db, prof, person, row['contact_id'])
        if type(item.get('revision')) is not int or item['revision'] != row['revision']:
            raise crm_store.CRMError(f'"{_title(row)}" changed elsewhere. Nothing was changed; refresh the list and try again.', 409)
        if row.get('archived_at'):
            raise crm_store.CRMError(f'"{_title(row)}" is archived. Nothing was changed.', 409)
        keys = set(fields) - {'note'}
        if keys & set(FOLLOWUP):
            keys |= set(FOLLOWUP)
        before = {k: row.get(k) or '' for k in sorted(keys)}
        try:
            store._change(db, prof, person, 'task.update', {'id': tid, 'revision': row['revision'], **fields})
        except crm_store.CRMError as error:
            raise crm_store.CRMError(f'"{_title(row)}": {error} Nothing was changed.', error.status) from None
        revision = db.execute('SELECT revision FROM tasks WHERE id=?', (tid,)).fetchone()[0]
        changed.append({'id': tid, 'revision': revision, 'values': before})
    return {'ok': True, 'id': changed[0]['id'], 'count': len(changed), 'undo': changed}
