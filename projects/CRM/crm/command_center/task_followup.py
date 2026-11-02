"""Explicit follow-up state and review dates, separate from the task's deadline."""
from . import crm_store

FIELDS = {'followup_state', 'review_at', 'waiting_on'}
STATES = {'': 'From category', 'next': 'Ready to work', 'client': 'Waiting on someone',
          'blocked': 'Blocked', 'decisions': 'Decision needed', 'parked': 'Parked for later'}


def migrate(db):
    columns = {r['name'] for r in db.execute('PRAGMA table_info(tasks)')}
    for name in (*sorted(FIELDS), 'original_due_at'):
        if name not in columns:
            db.execute(f"ALTER TABLE tasks ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
    if 'followup_state' not in columns:
        db.execute("UPDATE tasks SET followup_state='parked' WHERE substr(title,1,16)='Pending review: '")
    if 'original_due_at' not in columns:
        db.execute('UPDATE tasks SET original_due_at=due_at')


def values(row, data, status):
    state = data.get('followup_state', row.get('followup_state', ''))
    if not isinstance(state, str) or state not in STATES:
        raise crm_store.CRMError('Choose a supported follow-up status')
    if 'followup_state' not in data and 'blocked_reason' in data:
        if data['blocked_reason']:
            state = 'blocked'
        elif state == 'blocked':
            state = 'next'
    person = crm_store.text(data.get('waiting_on', row.get('waiting_on', '')), 'Waiting on', 200)
    review = crm_store.text(data.get('review_at', row.get('review_at', '')), 'Review date', 80)
    if review:
        try: review = crm_store.date_time(review)
        except crm_store.CRMError: raise crm_store.CRMError('Review date must include a valid time and UTC offset') from None
    changed = bool(FIELDS & data.keys()) or state != row.get('followup_state', '')
    if status == 'open' and changed:
        if state == 'client' and not person:
            raise crm_store.CRMError('Enter who or what you are waiting on')
        if state in ('client', 'parked') and not review:
            raise crm_store.CRMError('Choose when to review this follow-up')
        if state == 'blocked' and not data.get('blocked_reason', row.get('blocked_reason')):
            raise crm_store.CRMError('Describe the blocker before saving')
    if state != 'client':
        person = ''
    if state in ('', 'next'):
        review, person = '', ''
    original = row.get('original_due_at') or row.get('due_at') or crm_store.date_time(data.get('due_at'))
    return {'state': state, 'person': person, 'review': review, 'original': original,
            'changed': changed and any((state != row.get('followup_state', ''),
                person != row.get('waiting_on', ''), review != row.get('review_at', '')))}


def save(db, rid, values):
    db.execute('UPDATE tasks SET followup_state=?,waiting_on=?,review_at=?,original_due_at=? WHERE id=?',
               (values['state'], values['person'], values['review'], values['original'], rid))
    if not values['changed']:
        return ''
    return 'Follow-up: ' + STATES[values['state']] + ('. Waiting on: ' + values['person'] if values['person'] else '') + (
        '. Review: ' + values['review'] if values['review'] else '. No review scheduled.')
