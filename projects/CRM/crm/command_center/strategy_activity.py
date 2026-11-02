"""Department activity derived from authorized CRM tasks and delivery receipts. No writes."""
from datetime import datetime, timedelta, timezone

from . import crm_store, task_labels


def instant(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt if dt.tzinfo else None
    except (AttributeError, ValueError, TypeError):
        return None


def waiting(task):
    return task_labels.queue(task) in ('parked', 'client', 'blocked')


def task_item(task, at):
    context = task.get('context') or {}
    queue = task_labels.queue(task)
    parked = queue == 'parked'
    due = instant(task.get('due_at'))
    overdue = task['status'] == 'open' and bool(due and due < at) and not parked
    reason = '' if queue == 'next' else ('Waiting on ' + task['waiting_on']) if task.get('waiting_on') else 'Parked for later' if parked else context.get('blocked_reason') or (
        'Decision needed' if queue == 'decisions' else 'Waiting on input' if waiting(task) else '')
    if task['status'] != 'open':
        reason = ''
    return {'id': 'task:' + task['id'], 'kind': 'task', 'task_id': task['id'],
            'company_id': task.get('company_id') or task['contact_id'],
            'contact_id': task['contact_id'], 'title': task['title'].removeprefix('Pending review: '),
            'name': task.get('record_name', ''), 'owner': task.get('owner'),
            'review_at': task.get('review_at', ''), 'review_due': bool(task['status'] == 'open' and instant(task.get('review_at')) and instant(task['review_at']) <= at),
            'waiting_on': task.get('waiting_on', ''), 'followup_state': task.get('followup_state', ''),
            'status': task['status'], 'due_at': task.get('due_at'), 'created_at': task.get('created_at'),
            'completed_at': task.get('completed_at'), 'overdue': overdue, 'waiting': waiting(task),
            'parked': parked, 'reason': reason, 'decision': queue == 'decisions',
            'blocked': queue == 'blocked',
            'evidence': context.get('completion_evidence', ''), 'inquiry': None}


def inquiry_item(job, task=None):
    """Only receipt-backed steps are complete. A retry is never proof that CRM saved it."""
    state = job.get('state')
    saved = state in ('delivered', 'follow_up_due', 'needs_contact') or bool(job.get('contact_id'))
    status = task.get('status') if task else job.get('task_status')
    # The deployed monitor removes archived task IDs and calls them attention items.
    # Its completed status still comes from the saved task. Keep that completion
    # visible as history; normal CRM archiving is not a failed delivery.
    archived_completion = state == 'needs_attention' and not job.get('task_id') and status == 'completed' and saved
    if archived_completion:
        state = 'delivered'
    assigned = bool(job.get('task_id')) or bool(archived_completion)
    step = 'done' if status == 'completed' else 'cancelled' if status == 'cancelled' else 'current' if assigned else 'pending'
    return {'id': job['id'], 'name': job.get('name', 'Website inquiry'), 'state': state,
            'owner': job.get('owner'), 'contact_id': job.get('contact_id'), 'task_id': job.get('task_id'),
            'task_status': status, 'created_at': job.get('created_at'), 'due_at': job.get('due_at'),
            'completed_at': task.get('completed_at') if task else None, 'attempts': job.get('attempts', 0),
            'next_attempt': job.get('next_attempt'), 'error': '' if archived_completion else job.get('error', ''),
            'steps': [{'label': 'Inquiry received', 'state': 'done'},
                      {'label': 'Saved to CRM', 'state': 'done' if saved else 'current'},
                      {'label': 'Follow-up assigned', 'state': 'done' if assigned else 'pending'},
                      {'label': 'Follow-up completed', 'state': step}]}


def build(cfg, departments, tasks, handoffs, hidden_contacts=(), at=None):
    """Only configured departments get this view. Input is already scoped to the viewer."""
    configured = cfg.get('department_activity') or {}
    if not isinstance(configured, dict):
        raise crm_store.CRMError('Invalid department activity configuration', 503)
    at = at or datetime.now(timezone.utc)
    known = {d['id'] for d in departments}
    result = {}
    for department, settings in configured.items():
        if department not in known or not isinstance(settings, dict) or settings.get('sources') not in ([], ['website_chat']):
            raise crm_store.CRMError('Department activity must name a known department and supported source', 503)
        connected = settings['sources'] == ['website_chat']
        monitor = (handoffs or {}) if connected else {}
        enabled, restricted = bool(monitor.get('enabled')), bool(monitor.get('restricted'))
        observed = instant(monitor.get('checked_at'))
        health = ('restricted' if restricted else 'not_connected' if not enabled else
                  'healthy' if monitor.get('worker_healthy') and not monitor.get('unreadable') and
                  observed and 0 <= (at-observed).total_seconds() <= 180 else 'needs_attention')
        selected = [t for t in tasks if t['department'] == department]
        lookup = {t['id']: t for t in tasks}
        rows = {t['id']: task_item(t, at) for t in selected}
        inquiries, samples, system_attention, retrying = [], [], [], []
        # Never use receipt rows supplied by a restricted or disabled source.
        for job in monitor.get('items', []) if enabled and not restricted else []:
            flow = inquiry_item(job, lookup.get(job.get('task_id')))
            if job.get('contact_id') in hidden_contacts or job.get('name', '').endswith('(sample)'):
                samples.append(flow)
                continue
            inquiries.append(flow)
            if job.get('task_id') in rows:
                rows[job['task_id']]['inquiry'] = flow
            state = flow['state']
            if state in ('retrying', 'preparing', 'delivering'):
                retrying.append(flow)
            if state in ('needs_attention', 'needs_contact'):
                system_attention.append({'id': 'inquiry:' + job['id'], 'kind': 'inquiry',
                    'title': 'Review website inquiry', 'name': job.get('name', 'Website inquiry'),
                    'owner': job.get('owner'), 'contact_id': job.get('contact_id'),
                    'reason': 'Contact details missing' if state == 'needs_contact' else 'Delivery needs attention',
                    'inquiry': flow})
        opened = [rows[t['id']] for t in selected if t['status'] == 'open' and not t.get('archived_at')]
        opened.sort(key=lambda t: (instant(t['due_at']) or datetime.max.replace(tzinfo=timezone.utc), t['id']))
        next_actions = [t for t in opened if not t['waiting'] and not t['decision']]
        attention = [t for t in opened if not t['parked'] and (t['overdue'] or t['blocked'] or t['decision'])]
        # One saved follow-up is one action, even if its delivery also needs review.
        attention_ids = {t['task_id'] for t in attention}
        system_attention = [i for i in system_attention if i['inquiry'].get('task_id') not in attention_ids]
        completed = [rows[t['id']] for t in selected if t['status'] == 'completed' and
                     instant(t.get('completed_at')) and at-timedelta(days=30) <= instant(t['completed_at']) <= at]
        completed.sort(key=lambda t: t['completed_at'], reverse=True)
        result[department] = {'observed_at': at.isoformat(), 'source': 'website_chat' if connected else 'crm_tasks',
            'workflow': {'health': health, 'checked_at': monitor.get('checked_at'),
                         'unreadable': monitor.get('unreadable', 0), 'inquiries': inquiries,
                         'retrying': retrying, 'samples': samples},
            'counts': {'open': len(opened), 'next': len(next_actions),
                       'attention': len(system_attention)+len(attention),
                       'waiting': sum(t['waiting'] for t in opened), 'completed': len(completed)},
            'open': opened, 'next': next_actions, 'attention': system_attention+attention,
            'waiting': [t for t in opened if t['waiting']], 'completed': completed}
    return result


def delivery(cfg, departments, companies, tasks, nodes):
    """Recorded builds and linked work; no task completion can imply installation."""
    settings = cfg.get('delivery_board')
    if not settings:
        return {'enabled': False}
    if not isinstance(settings, dict) or settings.get('department') not in {d['id'] for d in departments}:
        raise crm_store.CRMError('Delivery board must name a known department', 503)
    department = settings['department']
    labels = {n['id']: n['label'] for n in nodes}
    opened = [t for t in tasks if t['status'] == 'open' and not t.get('archived_at')]
    boards, builds = [], []
    for company in companies:
        if company['lifecycle'] == 'archived':
            continue
        work = [t for t in opened if t['company_id'] == company['id']]
        if not company['installations'] and not any(t['department'] == department for t in work):
            continue
        boards.append({k: company.get(k) for k in ('id', 'name', 'owner', 'lifecycle')})
        for install in company['installations']:
            linked = sorted([t for t in work if install['node_id'] in t.get('build_node_ids', [])],
                            key=lambda t: (instant(t.get('due_at')) or datetime.max.replace(tzinfo=timezone.utc), t['id']))
            held = [t for t in linked if waiting(t) or task_labels.queue(t) == 'decisions']
            ready = [t for t in linked if t not in held]
            missing = company.get('eligibility', {}).get(install['node_id'], {}).get('missing', [])
            builds.append({**install, 'company_id': company['id'], 'company_name': company['name'],
                'owner': company['owner'], 'label': labels.get(install['node_id'], install['node_id']),
                'task_ids': [t['id'] for t in linked], 'waiting_ids': [t['id'] for t in held],
                'next_task_id': ready[0]['id'] if ready else None,
                'due_at': linked[0]['due_at'] if linked else None,
                'missing': [[labels.get(key, key) for key in group] for group in missing],
                'evidence_recorded': bool(install.get('evidence') and install.get('location'))})
    # A profile default cannot expose a company absent from this viewer's snapshot.
    focus = settings.get('focus_company')
    if focus not in {c['id'] for c in boards}:
        focus = next((c['id'] for c in boards if c['lifecycle'] == 'customer'), boards[0]['id'] if boards else None)
    return {'enabled': True, 'department': department, 'focus_company': focus, 'companies': boards, 'builds': builds}


def focus(departments, activity):
    """One oldest actionable task per configured department, in profile order."""
    labels = {d['id']: d['label'] for d in departments}
    items, seen = [], set()
    for department, model in activity.items():
        row = next((t for t in model['next'] if t['task_id'] not in seen), None)
        if row:
            items.append({**row, 'department': department, 'department_label': labels[department],
                          'basis': 'Oldest due actionable task in ' + labels[department]})
            seen.add(row['task_id'])
    return {'items': items, 'basis': 'Oldest CRM due date within each department. Waiting and decision tasks stay in their own queues.'}


def daily(departments, tasks, at=None):
    """Exclusive queues from recorded facts, including unassigned work. No priority inference."""
    at = at or datetime.now(timezone.utc)
    labels = {d['id']: d['label'] for d in departments}
    queues = {key: [] for key in ('next', 'client', 'blocked', 'decisions', 'parked')}
    for task in tasks:
        if task['status'] != 'open' or task.get('archived_at'):
            continue
        row = task_item(task, at)
        key = task_labels.queue(task)
        row.update(department=task.get('department', 'unassigned'),
                   department_label=labels.get(task.get('department'), 'Unassigned'),
                   action=task.get('action', 'none'), action_detail=task.get('action_detail', ''),
                   queue=key)
        row['reason'] = row['reason'] or ('No recorded blocker' if key == 'next' else 'Client input needed')
        queues[key].append(row)
    for rows in queues.values():
        rows.sort(key=lambda r: (instant(r['due_at']) or datetime.max.replace(tzinfo=timezone.utc), r['task_id']))
    return {'observed_at': at.isoformat(), 'queues': queues,
            'counts': {key: len(rows) for key, rows in queues.items()},
            'basis': 'Oldest recorded due date first; undated work last. This is a review order, not a confirmed business priority.',
            'classification': 'One queue per task: parked review, decision, client input, other blocker, then next action.'}
