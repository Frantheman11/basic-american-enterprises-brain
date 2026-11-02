"""Feedback is a CRM task plus a note, saved in the caller's existing transaction.

No separate inbox, support database, outbound notification or new permission scope.
"""
from . import crm_store

KINDS = {'bug': 'Bug', 'improvement': 'Improvement', 'feature': 'Feature request', 'help': 'Help request'}
VIEWS = {'overview': 'Command', 'companies': 'Companies', 'company': 'Company details',
         'technology': 'Tech tree', 'departments': 'Departments', 'decisions': 'Decisions', 'payments': 'Payments', 'connections': 'Connections'}


def enabled(prof):
    crm = prof.get('crm') or {}
    return bool(crm.get('strategy') or (crm.get('feedback') or {}).get('enabled') is True)


def options(prof, source, cfg):
    """Only offer records/categories already visible to this signed-in person."""
    crm = source.get('category_definitions', prof.get('crm') or {})
    records = []
    for contact in source['contacts']:
        if contact['kind'] not in ('company', 'business'):
            continue
        categories = ((crm.get('business_task_categories') if contact['kind'] == 'business' else None)
                      or crm.get('task_categories', []))
        mapping = cfg.get('business_category_departments' if contact['kind'] == 'business' else 'category_departments', {})
        preferred = [c for c in categories if cfg.get('feedback_department')
                     and (c.get('department') or mapping.get(c['id'])) == cfg['feedback_department']
                     and c.get('purpose', c['id']) not in ('decisions', 'waiting_on_client')]
        default = next((c['id'] for c in preferred if c.get('purpose') == 'follow_up'),
                       preferred[0]['id'] if preferred else '')
        records.append({k: contact[k] for k in ('id', 'name', 'kind')} | {
            'categories': [{'id': c['id'], 'label': c['label']} for c in categories],
            'default_category': default})
    connection = source.get('connection', {})
    return {'records': records,
            'endpoint': 'api/crm/feedback' if connection.get('mode') == 'live_readonly' else 'api/crm/command',
            'writable': connection.get('feedback_writable') is True or
            (connection.get('mode') != 'live_readonly' and 'crm_edit' in source['capabilities'])}


def submit(store, db, prof, person, data):
    if not enabled(prof):
        raise crm_store.CRMError('Feedback is not enabled for this workspace', 404)
    fields = {'kind', 'title', 'details', 'contact_id', 'category', 'due_at',
              'context_view', 'context_company_id'}
    if set(data) - fields:
        raise crm_store.CRMError('Unexpected feedback fields')
    kind = crm_store.text(data.get('kind'), 'Feedback type', 20, True)
    if kind not in KINDS:
        raise crm_store.CRMError('Choose a feedback type')
    title = crm_store.text(data.get('title'), 'Summary', 160, True)
    details = crm_store.text(data.get('details'), 'Details', 6000, True)
    contact = store.contact(db, prof, person, data.get('contact_id'))
    if contact['kind'] not in ('company', 'business'):
        raise crm_store.CRMError('Choose a business or company record')
    view = crm_store.text(data.get('context_view', ''), 'Page', 40)
    if view and view not in VIEWS:
        raise crm_store.CRMError('Choose a command center page')
    company_id = crm_store.text(data.get('context_company_id', ''), 'Context company', 80)
    context = VIEWS.get(view, '')
    if company_id:
        company = store.contact(db, prof, person, company_id)
        if company['kind'] != 'company' or not view:
            raise crm_store.CRMError('Choose a company and page for context')
        context += ' / ' + company['name']
    task = store._change(db, prof, person, 'task.create', {
        'contact_id': contact['id'], 'title': KINDS[kind] + ': ' + title,
        'due_at': data.get('due_at'), 'category': data.get('category', '')})
    note = store._change(db, prof, person, 'activity.add', {
        'contact_id': contact['id'], 'kind': 'note',
        'body': '\n'.join([KINDS[kind] + ': ' + title, '', details, '',
                           *(['Page: ' + context] if context else []),
                           'Review task: ' + task['id'],
                           'Review date is for triage, not a promised completion date.'])})
    return {'ok': True, 'id': task['id'], 'activity_id': note['id'], 'contact_id': contact['id']}
