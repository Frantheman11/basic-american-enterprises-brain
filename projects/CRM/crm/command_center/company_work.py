"""Company form reads and atomic updates through the existing CRM writer."""
from . import access, crm_store

FIELDS = {'name', 'email', 'phone', 'address', 'website', 'lifecycle'}


def read(store, prof, person, cid):
    store.authorize(prof, person)
    with store.connection() as db:
        db.execute('BEGIN')
        row = store.contact(db, prof, person, cid)
        if row['kind'] != 'company':
            raise crm_store.CRMError('Choose a company')
        industry = db.execute('SELECT industry FROM strategy_companies WHERE contact_id=?', (cid,)).fetchone()
    return {'record': {**row, 'industry': industry['industry'] if industry else ''},
            'can_edit': access.can_do(prof, person, 'crm_edit'),
            'can_assign': access.can_do(prof, person, 'crm_assign') and access.sees(prof, person) == 'all',
            'members': [{'id': m['id'], 'name': m['name']} for m in prof['people']['members']]}


def change(store, db, prof, person, data, stamp):
    if set(data) - FIELDS - {'id', 'revision', 'industry', 'owner'}:
        raise crm_store.CRMError('Unexpected company fields')
    row = store.contact(db, prof, person, data.get('id'))
    if row['kind'] != 'company':
        raise crm_store.CRMError('Choose a company')
    store._revision(row, data)
    if 'owner' in data:
        store.authorize(prof, person, 'crm_assign')
    values = {k: v for k, v in data.items() if k in FIELDS}
    if values:
        store._change(db, prof, person, 'contact.update', {'id': row['id'], 'revision': row['revision'], **values})
    if 'industry' in data:
        industry = crm_store.text(data['industry'], 'Industry', 120)
        db.execute('INSERT INTO strategy_companies VALUES (?,?) ON CONFLICT(contact_id) DO UPDATE SET industry=excluded.industry',
                   (row['id'], industry))
        if not values:
            db.execute('UPDATE contacts SET revision=revision+1,updated_at=? WHERE id=?', (stamp, row['id']))
    if 'owner' in data:
        revision = db.execute('SELECT revision FROM contacts WHERE id=?', (row['id'],)).fetchone()[0]
        store._change(db, prof, person, 'contact.assign', {'id': row['id'], 'revision': revision, 'owner': data['owner']})
    return {'ok': True, 'id': row['id']}
