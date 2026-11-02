"""Shared daily work and private, account-scoped priority pins."""
from . import access, crm_store, oauth, task_labels, task_work


def migrate(db):
    db.execute('CREATE TABLE IF NOT EXISTS task_pins ('
               'person_id TEXT NOT NULL, task_id TEXT NOT NULL REFERENCES tasks(id), '
               'pinned_at TEXT NOT NULL, PRIMARY KEY(person_id,task_id))')


def visible(store, db, prof, person):
    scope, params = access.contact_scope(prof, person, 'c')
    contacts = [dict(r) for r in db.execute("SELECT c.* FROM contacts c WHERE c.deleted_at IS NULL "
                "AND c.lifecycle!='archived'" + scope, params)]
    # A person's tasks also leave the daily view when their company is archived.
    archived = {r[0] for r in db.execute("SELECT id FROM contacts WHERE lifecycle='archived' OR deleted_at IS NOT NULL")}
    samples = set() if (prof.get('crm') or {}).get('demo') else {c['id'] for c in contacts if c['name'].endswith('(sample)')}
    contacts = [c for c in contacts if c['id'] not in samples and c.get('company_id') not in archived | samples]
    ids = {c['id'] for c in contacts}
    tasks = [dict(r) for r in db.execute("SELECT * FROM tasks WHERE status='open' AND archived_at IS NULL") if r['contact_id'] in ids]
    return contacts, tasks


def read(store, prof, person):
    store.authorize(prof, person)
    with store.connection() as db:
        db.execute('BEGIN')
        contacts, tasks = visible(store, db, prof, person)
        task_work.enrich(db, tasks)
        task_labels.enrich(prof, tasks, contacts)
        pins = [r[0] for r in db.execute('SELECT task_id FROM task_pins WHERE person_id=? ORDER BY pinned_at,task_id', (person.id,))]
    records = {c['id']: c for c in contacts}
    names = {m['id']: m['name'] for m in prof['people']['members']}
    for task in tasks:
        contact = records[task['contact_id']]
        assigned = task['assignee'] or contact['owner']
        task.update(record_name=contact['name'], assigned_to=assigned, assigned_name=names.get(assigned, assigned or 'Unassigned'))
    ids = {t['id'] for t in tasks}
    return {'server_time': crm_store.now(), 'person': {'id':person.id,'name':person.name},
            'can_edit': access.can_do(prof, person, 'crm_edit'),
            'connected_apps': oauth.door(prof) is not None,  # the Claude apps' sign-in door is open: link Connected apps
            'tasks': sorted(tasks, key=lambda t:(t['due_at'] or '9999',t['id'])),
            'pins': [tid for tid in pins if tid in ids]}


def change(store, db, prof, person, action, data, stamp):
    if set(data) != {'id'}:
        raise crm_store.CRMError('Choose one task to pin or unpin')
    tid = crm_store.text(data.get('id'), 'Task ID', 80, True)
    _, tasks = visible(store, db, prof, person)
    ids = {t['id'] for t in tasks}
    for row in db.execute('SELECT task_id FROM task_pins WHERE person_id=?', (person.id,)).fetchall():
        if row[0] not in ids:
            db.execute('DELETE FROM task_pins WHERE person_id=? AND task_id=?', (person.id,row[0]))
    if action == 'task.unpin':
        db.execute('DELETE FROM task_pins WHERE person_id=? AND task_id=?', (person.id,tid))
    else:
        if tid not in ids:
            raise crm_store.CRMError('Choose an open task you can access', 404)
        pinned = {r[0] for r in db.execute('SELECT task_id FROM task_pins WHERE person_id=?', (person.id,))}
        if tid not in pinned and len(pinned) >= 3:
            raise crm_store.CRMError('Three priorities are pinned. Unpin one before adding another.')
        db.execute('INSERT OR IGNORE INTO task_pins VALUES (?,?,?)', (person.id,tid,stamp))
    return {'ok':True,'id':tid}
