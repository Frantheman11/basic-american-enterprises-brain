"""Shared task fields and completion rules, inside the CRM writer's transaction."""
from uuid import uuid4

from . import crm_store, task_followup


def migrate(db):
    task_followup.migrate(db)
    from . import workspace_daily, workspace_preferences
    workspace_daily.migrate(db)
    workspace_preferences.migrate(db)  # v22: account-synced favorites and saved views
    columns = {r['name'] for r in db.execute('PRAGMA table_info(tasks)')}
    for field in ('blocked_reason', 'completion_evidence'):
        if field not in columns:
            db.execute(f"ALTER TABLE tasks ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
            db.execute(f"UPDATE tasks SET {field}=COALESCE((SELECT {field} FROM strategy_task_links "
                       "WHERE task_id=tasks.id), '')")
    for field in ('assignee', 'department'):
        if field not in columns:
            db.execute(f"ALTER TABLE tasks ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
    # v26: main tasks. A main task (is_main=1) holds subtasks (parent_id = its id). One level only:
    # a main task has no parent, and a subtask's parent is always a main task (main_task below).
    if 'is_main' not in columns:
        db.execute('ALTER TABLE tasks ADD COLUMN is_main INTEGER NOT NULL DEFAULT 0')
    if 'parent_id' not in columns:
        db.execute('ALTER TABLE tasks ADD COLUMN parent_id TEXT REFERENCES tasks(id)')
    db.execute('CREATE INDEX IF NOT EXISTS tasks_parent ON tasks(parent_id)')


def assignees(prof, contact):
    from . import access, identity
    result=[]
    for member in prof['people']['members']:
        person=identity.person(prof, member['id'])
        if access.can_do(prof, person, 'crm_read') and access.can_do(prof, person, 'crm_edit') and (
                access.sees_contact(prof, person, contact)):
            result.append({'id':person.id,'name':person.name})
    return result


def assignment(prof, contact, row, data):
    assignee=crm_store.text(data.get('assignee',row.get('assignee','')), 'Assignee',80)
    department=crm_store.text(data.get('department',row.get('department','')), 'Department',80)
    if assignee and assignee!=row.get('assignee') and assignee not in {m['id'] for m in assignees(prof,contact)}:
        raise crm_store.CRMError('Choose someone who can already access and edit this company')
    if department and department!=row.get('department') and department not in {d['id'] for d in prof['departments'] if not d.get('cross_cutting')}:
        raise crm_store.CRMError('Choose a configured department')
    return assignee,department


def options(store,prof,person,cid):
    from . import access
    store.authorize(prof,person)
    with store.connection() as db:
        db.execute('BEGIN')
        contact=store.contact(db,prof,person,cid)
        deals=[dict(r) for r in db.execute('SELECT id,title FROM deals WHERE contact_id=? AND deleted_at IS NULL',(cid,))]
    return {'contact': {k:contact[k] for k in ('id','name','owner')}, 'can_edit':access.can_do(prof,person,'crm_edit'),
            'categories':crm_store.categories(prof,contact['kind']), 'deals':deals, 'assignees':assignees(prof,contact),
            'departments':[{'id':d['id'],'label':d['label']} for d in prof['departments'] if not d.get('cross_cutting')]}


def main_task(store, db, prof, person, row, data, rid):
    """The (is_main, parent_id) a save leaves. A main task may sit on any record, the business
    record included, and its subtasks on other records. One level: never a main under a main."""
    is_main = data.get('is_main', bool(row.get('is_main')))
    if type(is_main) is not bool:
        raise crm_store.CRMError('Main task must be true or false')
    parent = data.get('parent_id', row.get('parent_id')) or None
    if parent is not None:
        parent = crm_store.text(parent, 'Main task ID', 80, True)
        found = db.execute('SELECT * FROM tasks WHERE id=?', (parent,)).fetchone()
        if not found:
            raise crm_store.CRMError('Main task not found', 404)
        store.contact(db, prof, person, found['contact_id'])  # it must be one this person can see
        if parent == rid or not found['is_main']:
            raise crm_store.CRMError('A subtask goes under a main task')
        if is_main:
            raise crm_store.CRMError('A main task cannot sit under another main task')
    if row and row.get('is_main') and not is_main and db.execute(
            'SELECT 1 FROM tasks WHERE parent_id=?', (rid,)).fetchone():
        raise crm_store.CRMError('Move its subtasks out before this stops being a main task')
    return int(is_main), parent


def fields(db, row, data, status):
    linked = db.execute('SELECT node_id FROM strategy_task_links WHERE task_id=?', (row.get('id'),)).fetchone()
    blocker = crm_store.text(data.get('blocked_reason', row.get('blocked_reason', '')), 'Blocker', 2000)
    evidence = crm_store.text(data.get('completion_evidence', row.get('completion_evidence', '')), 'Completion evidence', 4000)
    if status == 'completed' and linked and (row.get('status') != 'completed' or 'completion_evidence' in data):
        # Each completion needs fresh evidence; evidence from a previous completion is history.
        evidence = crm_store.text(data.get('completion_evidence', ''), 'Completion evidence', 4000, True)
    if status != 'open' or data.get('followup_state') == 'next':
        blocker = ''
    note = crm_store.text(data.get('note', ''), 'Task note', 4000)
    return blocker, evidence, note, task_followup.values(row, data, status)


def save(db, rid, contact_id, title, values, actor, stamp):
    blocker, evidence, note, followup = values
    change = task_followup.save(db, rid, followup)
    note = '\n'.join(x for x in (change, note) if x)
    db.execute('UPDATE tasks SET blocked_reason=?,completion_evidence=? WHERE id=?', (blocker, evidence, rid))
    # Keep the v18 projection readable by existing observer tools during a rolling release.
    db.execute('UPDATE strategy_task_links SET blocked_reason=?,completion_evidence=? WHERE task_id=?',
               (blocker, evidence, rid))
    if note:
        db.execute('INSERT INTO activities (id,contact_id,kind,body,actor,created_at) VALUES (?,?,?,?,?,?)',
                   (str(uuid4()), contact_id, 'note', f'Task [{rid}] {title}\n{note}', actor, stamp))


def progress(tasks):
    """On each main task: its visible subtasks and how many are done ("4 of 11 done").
    A cancelled subtask counts toward neither number."""
    mains = {t['id']: t for t in tasks if t.get('is_main')}
    for main in mains.values():
        main.update(subtask_ids=[], subtasks_done=0, subtasks_total=0)
    for task in tasks:
        main = mains.get(task.get('parent_id'))
        if main is None or task['status'] == 'cancelled':
            continue
        main['subtask_ids'].append(task['id'])
        main['subtasks_total'] += 1
        main['subtasks_done'] += task['status'] == 'completed'


def enrich(db, tasks):
    progress(tasks)
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='strategy_task_links'").fetchone():
        return  # Older read-only observer databases are never migrated while reading.
    plans = {r['task_id']: dict(r) for r in db.execute('SELECT * FROM strategy_task_links')}
    for task in tasks:
        task['requires_completion_evidence'] = task['id'] in plans
        task['plan'] = plans.get(task['id'])


def read(store, prof, person, tid):
    store.authorize(prof, person)
    with store.connection() as db:
        db.execute('BEGIN')
        row = db.execute('SELECT * FROM tasks WHERE id=?', (crm_store.text(tid, 'Task ID', 80, True),)).fetchone()
        if not row:
            raise crm_store.CRMError('Task not found', 404)
        task = dict(row)
        contact = store.contact(db, prof, person, task['contact_id'])
        from . import access
        scope, params = access.contact_scope(prof, person, 'c')
        subtasks = [dict(r) for r in db.execute(  # only the subtasks this person can see are counted
            'SELECT t.id,t.status,t.parent_id FROM tasks t JOIN contacts c ON c.id=t.contact_id WHERE t.parent_id=? '
            'AND c.deleted_at IS NULL' + scope, (task['id'], *params))]
        enrich(db, [task, *subtasks])
        from . import task_labels
        task_labels.enrich(prof, [task], [contact])
        deals = [dict(r) for r in db.execute('SELECT id,title FROM deals WHERE contact_id=? AND deleted_at IS NULL', (contact['id'],))]
        history = [dict(r) for r in db.execute("SELECT body,created_at,actor FROM activities WHERE contact_id=? "
                   "AND deleted_at IS NULL AND body LIKE ? ORDER BY created_at DESC LIMIT 10",
                   (contact['id'], f'Task [{tid}] %'))]
        company = db.execute('SELECT id,name FROM contacts WHERE id=? AND deleted_at IS NULL',
                             (contact.get('company_id'),)).fetchone() if contact.get('company_id') else None
        others = db.execute("SELECT count(*) FROM tasks WHERE contact_id=? AND status='open' AND archived_at IS NULL AND id!=?",
                            (contact['id'], task['id'])).fetchone()[0]
        last = db.execute('SELECT kind,created_at FROM activities WHERE contact_id=? AND deleted_at IS NULL '
                          'ORDER BY created_at DESC LIMIT 1', (contact['id'],)).fetchone()
    context = {'kind': contact['kind'], 'lifecycle': contact['lifecycle'], 'company': dict(company) if company else None,
               'other_open_tasks': others, 'last_activity': dict(last) if last else None}
    return {**options(store,prof,person,contact['id']), 'task':task, 'history':history, 'context':context}
