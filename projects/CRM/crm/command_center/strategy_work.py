"""Strategy task links; all mutations are called inside Store.execute's transaction."""
from . import crm_store, strategy, workspace_bulk, workspace_preferences

ACTIONS = ('task.pin', 'task.unpin', 'task.plan', 'task.block', 'task.finish', 'company.classify', 'company.update')
CAPABILITIES = {**dict.fromkeys(ACTIONS, 'crm_edit'), **workspace_preferences.CAPABILITIES, **workspace_bulk.CAPABILITIES}


def migrate(db):
    db.execute('CREATE TABLE IF NOT EXISTS strategy_companies ('
               'contact_id TEXT PRIMARY KEY REFERENCES contacts(id), industry TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS strategy_task_links ('
               'task_id TEXT PRIMARY KEY REFERENCES tasks(id), node_id TEXT NOT NULL, '
               'department TEXT NOT NULL, blocked_reason TEXT NOT NULL DEFAULT \'\', '
               'verification TEXT NOT NULL, completion_evidence TEXT NOT NULL DEFAULT \'\')')


def change(store, db, prof, person, action, data, stamp):
    if action == 'preferences.set':
        return workspace_preferences.change(store, db, prof, person, data, stamp)
    if action == 'task.bulk':
        return workspace_bulk.change(store, db, prof, person, data, stamp)
    if action in ('task.pin', 'task.unpin'):
        from . import workspace_daily
        return workspace_daily.change(store, db, prof, person, action, data, stamp)
    if action == 'company.update':
        from . import company_work
        return company_work.change(store, db, prof, person, data, stamp)
    if action == 'company.classify':
        if set(data) - {'contact_id', 'revision', 'industry'}:
            raise crm_store.CRMError('Unexpected company fields')
        contact = store.contact(db, prof, person, data.get('contact_id'))
        if contact['kind'] != 'company':
            raise crm_store.CRMError('Choose a company')
        store._revision(contact, data)
        industry = crm_store.text(data.get('industry'), 'Industry', 120, True)
        db.execute('INSERT INTO strategy_companies VALUES (?,?) ON CONFLICT(contact_id) DO UPDATE SET industry=excluded.industry',
                   (contact['id'], industry))
        db.execute('UPDATE contacts SET revision=revision+1,updated_at=? WHERE id=?', (stamp, contact['id']))
        return {'ok': True, 'id': contact['id']}
    if action == 'task.plan':
        allowed = {'contact_id', 'node_id', 'title', 'due_at', 'brief_path', 'department', 'verification'}
        if set(data) - allowed:
            raise crm_store.CRMError('Unexpected plan fields')
        contact = store.contact(db, prof, person, data.get('contact_id'))
        if contact['kind'] != 'company':
            raise crm_store.CRMError('Choose a company for an installation plan')
        cfg = strategy.config(prof)
        nodes, _, _ = strategy.graph(prof, cfg)
        node_id = crm_store.text(data.get('node_id'), 'Capability', 80, True)
        if node_id not in nodes:
            raise crm_store.CRMError('Choose a configured capability')
        department = crm_store.text(data.get('department'), 'Department', 80, True)
        if department not in {d['id'] for d in prof['departments'] if not d.get('cross_cutting')}:
            raise crm_store.CRMError('Choose a configured department')
        verification = crm_store.text(data.get('verification'), 'Acceptance check', 2000, True)
        brief = crm_store.text(data.get('brief_path'), 'Brief path', 2000, True)
        if not crm_store.BRIEF.fullmatch(brief) or '..' in brief.split('/'):
            raise crm_store.CRMError('Brief path must be a relative .md path inside the repository')
        # A new browser or request ID must not duplicate the same active installation plan.
        existing = db.execute('SELECT t.id FROM tasks t JOIN strategy_task_links l ON l.task_id=t.id '
                              "WHERE t.contact_id=? AND l.node_id=? AND t.status='open' AND t.archived_at IS NULL",
                              (contact['id'], node_id)).fetchone()
        if existing:
            return {'ok': True, 'id': existing['id'], 'existing': True}
        payload = {'contact_id': contact['id'], 'title': data.get('title'), 'due_at': data.get('due_at'),
                   'category': cfg.get('build_category', ''), 'action': 'work_session', 'action_detail': brief}
        result = store._change(db, prof, person, 'task.create', payload)
        db.execute('INSERT INTO strategy_task_links (task_id,node_id,department,verification) VALUES (?,?,?,?)',
                   (result['id'], node_id, department, verification))
        return result
    allowed = {'id', 'revision', 'blocked_reason'} if action == 'task.block' else {'id', 'revision', 'evidence'}
    if set(data) - allowed:
        raise crm_store.CRMError('Unexpected task fields')
    row = db.execute('SELECT * FROM tasks WHERE id=?', (crm_store.text(data.get('id'), 'Task ID', 80, True),)).fetchone()
    if row is None:
        raise crm_store.CRMError('Task not found', 404)
    row = dict(row)
    store.contact(db, prof, person, row['contact_id'])
    store._revision(row, data)
    if not db.execute('SELECT 1 FROM strategy_task_links WHERE task_id=?', (row['id'],)).fetchone():
        raise crm_store.CRMError('This task has no strategy plan; use its CRM editor')
    if row['status'] != 'open' or row['archived_at']:
        raise crm_store.CRMError('Only an open, unarchived plan can be changed', 409)
    if action == 'task.block':
        reason = crm_store.text(data.get('blocked_reason'), 'Blocker', 2000)
        store._change(db, prof, person, 'task.update', {'id': row['id'], 'revision': row['revision'], 'blocked_reason': reason})
    else:
        evidence = crm_store.text(data.get('evidence'), 'Completion evidence', 4000, True)
        store._change(db, prof, person, 'task.update', {'id': row['id'], 'revision': row['revision'],
                                                    'status': 'completed', 'completion_evidence': evidence})
    return {'ok': True, 'id': row['id']}
