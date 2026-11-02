"""Exact configured task meanings shared by CRM and Command Center projections."""


def configuration(prof):
    if not (prof.get('crm') or {}).get('strategy'):
        return {}
    from . import strategy
    return strategy.config(prof)


def resolve(prof, cfg, kind, category_id, definitions=None):
    current = definitions if definitions is not None else prof.get('crm') or {}
    historical = cfg.get('historical_category_definitions', prof.get('crm') or {})
    for preserved, source in ((False, current), (True, historical)):
        rows = (source.get('business_task_categories') if kind == 'business' else None) or source.get('task_categories', [])
        found = next((row for row in rows if row['id'] == category_id), None)
        if found:
            return found, preserved
    return {}, False


def queue(task):
    if task['status'] != 'open':
        return task['status']
    if task.get('followup_state'):
        return task['followup_state']
    return ('parked' if task['title'].startswith('Pending review: ') else
            'decisions' if task.get('needs_decision') else
            'client' if task.get('work_type') == 'waiting_on_client' else
            'blocked' if task.get('blocked_reason') or (task.get('context') or {}).get('blocked_reason') else 'next')


def enrich(prof, tasks, contacts):
    cfg = configuration(prof)
    kinds = {c['id']: c['kind'] for c in contacts}
    for task in tasks:
        category, _ = resolve(prof, cfg, kinds[task['contact_id']], task['category'])
        purpose = category.get('purpose', task['category'])
        task.update(category_label=category.get('label') or ('Unmapped category: ' + task['category'] if task['category'] else 'Other follow-ups'),
                    work_type=purpose, needs_decision=purpose == 'decisions')
        task['queue'] = queue(task)
        from . import crm_store
        task['review_due'] = bool(task['status'] == 'open' and task.get('review_at') and task['review_at'] <= crm_store.now())
        task['is_waiting'] = task['queue'] in ('parked', 'decisions', 'client', 'blocked')
