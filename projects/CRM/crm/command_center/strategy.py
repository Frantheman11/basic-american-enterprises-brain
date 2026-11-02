"""Strategic read model over CRM records and profile-selected catalogs. No record writes."""
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from . import access, crm_store, tech_tree


def read_json(prof, name):
    root = Path(prof['_path']).resolve().parents[2]
    path = (root / name).resolve() if isinstance(name, str) and name else None
    if path is None or root not in path.parents or path.suffix != '.json':
        raise crm_store.CRMError('Strategy source must be a JSON file inside this repository', 503)
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise crm_store.CRMError('A strategy source is unavailable or invalid', 503) from None


def config(prof):
    name = (prof.get('crm') or {}).get('strategy')
    if not name:
        raise crm_store.CRMError('Strategy view is not configured', 404)
    cfg = read_json(prof, name)
    if not isinstance(cfg, dict):
        raise crm_store.CRMError('Invalid strategy configuration', 503)
    return cfg


def graph(prof, cfg):
    cat = tech_tree.catalog(prof)
    nodes = {n['id']: dict(n) for n in cat['nodes']}
    edges = []
    try:
        for key in cfg.get('requirements', {}):
            if key not in nodes:
                raise ValueError()
        for key, node in nodes.items():
            rule = cfg.get('requirements', {}).get(key, {'all': node['prerequisites'], 'any': []})
            if set(rule) - {'all', 'any'}:
                raise ValueError()
            groups = [[v] for v in rule.get('all', [])] + rule.get('any', [])
            if any(not isinstance(g, list) or not g for g in groups):
                raise ValueError()
            if any(not isinstance(p, str) or p not in nodes or p == key for g in groups for p in g):
                raise ValueError()
            node['requirement_groups'] = groups
            for i, group in enumerate(groups):
                edges += [{'from': p, 'to': key, 'type': 'alternative' if len(group) > 1 else 'requires',
                           'group': i} for p in group]
        for edge in cfg.get('supports', []):
            if edge['from'] not in nodes or edge['to'] not in nodes or edge['from'] == edge['to']:
                raise ValueError()
            edges.append({**edge, 'type': 'supports'})
        def depth(key, trail):
            if key in trail:
                raise ValueError()
            parents = [p for g in nodes[key]['requirement_groups'] for p in g]
            return 1 + max((depth(p, trail | {key}) for p in parents), default=-1)
        for key, node in nodes.items():
            node['depth'] = depth(key, set())
    except (KeyError, TypeError, ValueError, RecursionError):
        raise crm_store.CRMError('Invalid strategy dependencies: check IDs, groups, and cycles', 503) from None
    return nodes, edges, cat['layers']


def requirements(node, installations):
    """A recorded build satisfies a planning prerequisite, never a live-health assertion."""
    recorded = {n['node_id'] for n in installations if n['state'] == 'built'}
    missing = [group for group in node['requirement_groups'] if not recorded.intersection(group)]
    return {'ready': not missing, 'missing': missing, 'basis': 'Recorded builds; operational health not verified'}


def location_for(contact, locations):
    """A cached public address match is usable only while the CRM address agrees."""
    row = locations.get(contact['id'], {})
    if not contact.get('address') or row.get('address') != contact['address']:
        return None
    lat, lng = row.get('lat'), row.get('lng')
    if (type(lat) not in (int, float) or type(lng) not in (int, float)
            or not math.isfinite(lat) or not math.isfinite(lng)
            or not -90 <= lat <= 90 or not -180 <= lng <= 180 or row.get('state')):
        return None
    return {k: row.get(k) for k in ('lat', 'lng', 'source', 'source_url', 'checked_at', 'precision', 'matched_address')}


def product_tree(cfg, modules):
    """Product suggestions annotate catalog records; they never change build status."""
    spec = cfg.get('product_tree', {})
    categories = spec.get('categories', [])
    placements = spec.get('placements', {})
    nodes = [{**{k: m.get(k) for k in ('id','name','job','status','sources','gate','version','readiness_note','connectivity')},
              **{k: placements.get(m['id'], {}).get(k) for k in ('tier','featured','summary')}, 'kind': 'module',
              'category': placements.get(m['id'], {}).get('category', 'other')}
             for m in modules.values()]
    suggestions = spec.get('suggestions', [])
    nodes += [{**n, 'status': 'proposed', 'kind': 'suggestion'} for n in suggestions]
    tree_spec = spec.get('tree') or {}
    nodes += [{**{k: n.get(k) for k in ('id', 'name', 'job', 'category', 'sources')}, 'kind': n.get('kind'),
               'status': n.get('state')} for n in tree_spec.get('nodes', [])]
    ids = [n['id'] for n in nodes]
    edges = spec.get('edges', [])
    by_id = {n['id']: n for n in nodes}
    known_ids = set(ids) | set(placements)
    pairs = [(e.get('from'), e.get('to')) for e in edges]
    if (len(set(ids)) != len(ids) or len(set(pairs)) != len(pairs)
            or any(e.get('from') not in known_ids or e.get('to') not in known_ids
            or e['from'] == e['to'] or e.get('type') not in ('complements', 'implemented')
            or (e['type'] == 'implemented' and (not e.get('label') or not e.get('sources'))) for e in edges)):
        raise crm_store.CRMError('Invalid product tree connections', 503)
    missing = sorted({e[k] for e in edges for k in ('from', 'to') if e[k] not in by_id})
    edges = [e for e in edges if e['from'] in by_id and e['to'] in by_id]
    # A connection annotation can outlive an older catalog revision. Keep the tree
    # usable, but never use that annotation to promote an endpoint's build status.
    edges = [{**e, 'type': 'unverified', 'review_note': 'Connection evidence exists, but a module is not '
              'catalogued built. Reconcile its build evidence before confirming this link.'}
             if e['type'] == 'implemented' and any(by_id[k]['status'] != 'built' for k in (e['from'], e['to']))
             else dict(e) for e in edges]
    if any(n['category'] == 'other' for n in nodes) and not any(c['id'] == 'other' for c in categories):
        categories = [*categories, {'id': 'other', 'label': 'Other modules'}]
    ranked = [n for n in nodes if n['kind'] == 'suggestion' and n.get('priority') is not None]
    ranks = [n['priority'] for n in ranked]
    if any(type(r) is not int or r < 1 for r in ranks) or len(set(ranks)) != len(ranks):
        raise crm_store.CRMError('Invalid product recommendation order', 503)
    tree = unlock_tree(tree_spec, by_id, edges)
    return {'categories': categories, 'nodes': nodes, 'edges': edges, 'missing_catalog_modules': missing,
            'recommendations': [n['id'] for n in sorted(ranked, key=lambda n: n['priority'])], 'tree': tree,
            'basis': 'Built = catalog evidence. Solid links = implemented handoffs; dashed = proposed combinations. '
                     'Needed-first and works-better-with links describe what makes what possible. '
                     'None of them is a current health check or a client installation claim.'}


TREE_KINDS = {'combo': ('live', 'partial', 'planned'), 'product': ('planned',)}


def unlock_tree(spec, by_id, edges):
    """What makes what possible: `requires` (first entry places the node) and `boosts`.
    Validates ids, cycles and evidence; merges each pair into the connection list in place."""
    bad = lambda: crm_store.CRMError('Invalid product tech tree: check IDs, states and cycles', 503)
    requires = {k: list(v) for k, v in (spec.get('requires') or {}).items()}
    boosts = {k: list(v) for k, v in (spec.get('boosts') or {}).items()}
    for n in spec.get('nodes', []):
        if (n.get('kind') not in TREE_KINDS or n.get('state') not in TREE_KINDS[n['kind']]
                or not n.get('name') or not n.get('job') or not n.get('requires')
                or (n['state'] != 'planned' and not n.get('sources'))):
            raise bad()
        if n['id'] in requires or n['id'] in boosts:
            raise bad()
        requires[n['id']] = list(n['requires'])
        if n.get('boosts'):
            boosts[n['id']] = list(n['boosts'])
    for table in (requires, boosts):
        for key, parents in table.items():
            if (key not in by_id or not isinstance(parents, list) or not parents
                    or len(set(parents)) != len(parents)
                    or any(p not in by_id or p == key for p in parents)):
                raise bad()
    if any(set(requires.get(k, [])) & set(v) for k, v in boosts.items()):
        raise bad()
    standalone = spec.get('standalone') or {}
    if any(k not in by_id or not isinstance(v, str) or not v for k, v in standalone.items()):
        raise bad()
    depth = {}
    def visit(key, trail):
        if key in trail:
            raise bad()
        if key not in depth:
            parents = requires.get(key, []) + boosts.get(key, [])
            [visit(p, trail | {key}) for p in parents]
            depth[key] = 1 + max((depth[p] for p in requires.get(key, [])), default=-1)
    for key in by_id:
        visit(key, frozenset())
    pairs = {(e['from'], e['to']): e for e in edges}
    for need, table, label in (('requires', requires, 'Needed first'), ('boosts', boosts, 'Works better with it')):
        for key, parents in table.items():
            for p in parents:
                edge = pairs.get((p, key))
                if edge is None:
                    combo = by_id[key]['kind'] == 'combo'
                    edge = {'from': p, 'to': key, 'type': need, 'label': 'Makes the smart version' if combo else label}
                    edges.append(edge)
                    pairs[(p, key)] = edge
                edge['need'] = need
    notes = {}
    for key in requires:
        node = by_id[key]
        if node['kind'] == 'combo' and node['status'] != 'planned':
            behind = [by_id[p]['name'] for p in requires[key]
                      if by_id[p]['kind'] == 'module' and by_id[p]['status'] != 'built']
            if behind:
                notes[key] = ('The catalog does not yet record ' + ', '.join(behind) +
                              ' as built. Reconcile its build evidence before claiming this works today.')
    module_ids = {k for k, n in by_id.items() if n['kind'] == 'module'}
    linked = {k for k in requires} | {k for k in boosts} | {p for v in requires.values() for p in v} | \
             {p for v in boosts.values() for p in v}
    return {'requires': requires, 'boosts': boosts, 'depth': depth, 'standalone': standalone,
            'parent': {k: (requires.get(k) or [None])[0] for k in by_id if by_id[k]['kind'] != 'suggestion'},
            'unlinked': sorted(module_ids - linked - set(standalone)), 'review_notes': notes,
            'note': spec.get('note', '')}


def run_state(row, at, stale_seconds=300):
    status = row.get('status', 'unknown')
    if status not in ('queued', 'running', 'waiting', 'succeeded', 'failed', 'canceled'):
        return 'unknown'
    if status == 'running':
        try:
            seen = datetime.fromisoformat(row.get('heartbeat_at', '').replace('Z', '+00:00'))
            if seen.utcoffset() is None:
                return 'unknown'
            age = (at - seen).total_seconds()
            return 'running' if 0 <= age <= stale_seconds else 'stale'
        except (ValueError, TypeError, AttributeError):
            return 'unknown'
    return status


def classified_tasks(prof, cfg, source, links, departments):
    """Shared category/plan interpretation for the browser and machine readers."""
    visible = {c['id']: c for c in source['contacts']}
    tasks = []
    for task in source['tasks']:
        contact = visible[task['contact_id']]
        link = links.get(task['id'], {})
        from . import task_labels
        category, historical_category = task_labels.resolve(prof, cfg, contact['kind'], task['category'], source.get('category_definitions'))
        mapping = cfg.get('business_category_departments', cfg.get('category_departments', {})) if contact['kind'] == 'business' else cfg.get('category_departments', {})
        dept = task.get('department') or link.get('department') or category.get('department') or mapping.get(task['category'])
        basis = ('Task department' if task.get('department') else 'Explicit plan' if link.get('department') else
                 'Preserved category definition' if dept and historical_category else
                 'CRM category' if dept else 'Not classified')
        dept = dept or ('unassigned' if cfg.get('unassigned_unclassified') else
                        cfg.get('default_department', '') if contact['kind'] == 'business' else cfg.get('client_department', ''))
        if dept not in {d['id'] for d in departments}:
            dept = 'unassigned'
        tasks.append({**task, 'company_id': contact.get('company_id') or contact['id'],
                      'record_name': contact['name'], 'owner': task.get('assignee') or contact['owner'], 'company_owner':contact['owner'], 'department': dept,
                      'department_basis': basis, 'needs_decision': category.get('purpose', task['category']) == 'decisions',
                      'work_type': category.get('purpose', task['category']),
                       'context': {**link, **{key: task[key] for key in ('blocked_reason', 'completion_evidence') if key in task}}})
    return tasks


def daily_snapshot(store, prof, person):
    """Permission-scoped machine brief; no reporting services or catalog graph required."""
    from . import strategy_activity
    store.authorize(prof, person)
    cfg = config(prof)
    source = store.snapshot(prof, person)
    hidden = set() if (prof.get('crm') or {}).get('demo') else {
        c['id'] for c in source['contacts'] if c['name'].endswith('(sample)')}
    hidden.update(c['id'] for c in source['contacts'] if c.get('company_id') in hidden)
    source['tasks'] = [t for t in source['tasks'] if t['contact_id'] not in hidden]
    ids = {t['id'] for t in source['tasks']}
    with store.connection() as db:
        # Schema-17 deployments have categories but not explicit strategy plans yet.
        has_links = db.execute("SELECT 1 FROM sqlite_master WHERE name='strategy_task_links'").fetchone()
        links = {r['task_id']: dict(r) for r in db.execute('SELECT * FROM strategy_task_links')
                 if r['task_id'] in ids} if has_links else {}
    departments = [d for d in prof['departments'] if not d.get('cross_cutting')]
    tasks = classified_tasks(prof, cfg, source, links, departments)
    return strategy_activity.daily(departments, tasks, strategy_activity.instant(source['server_time']))


def snapshot(store, prof, person):
    store.authorize(prof, person)
    cfg = config(prof)
    nodes, edges, layers = graph(prof, cfg)
    if hasattr(store, 'strategy_source'):
        source, links, industries = store.strategy_source(prof, person)
    else:
        source = store.snapshot(prof, person)
        with store.connection() as db:
            task_ids = {t['id'] for t in source['tasks']}
            visible_ids = {c['id'] for c in source['contacts']}
            links = {r['task_id']: dict(r) for r in db.execute('SELECT * FROM strategy_task_links') if r['task_id'] in task_ids}
            industries = {r['contact_id']: r['industry'] for r in db.execute('SELECT * FROM strategy_companies') if r['contact_id'] in visible_ids}
    sample = bool((prof.get('crm') or {}).get('demo'))
    hidden = set() if sample else {c['id'] for c in source['contacts'] if c['name'].endswith('(sample)')}
    hidden.update(c['id'] for c in source['contacts'] if c.get('company_id') in hidden)
    source['contacts'] = [c for c in source['contacts'] if c['id'] not in hidden]
    source['tasks'] = [t for t in source['tasks'] if t['contact_id'] not in hidden]
    source['client_nodes'] = [n for n in source['client_nodes'] if n['contact_id'] not in hidden]
    source['waiting'] = [w for w in source['waiting'] if w.get('contact_id') not in hidden]
    source['activities'] = [a for a in source['activities'] if a.get('contact_id') not in hidden]
    modules = read_json(prof, cfg['module_catalog']) if cfg.get('module_catalog') else {'modules': []}
    modules = {m['id']: m for m in modules.get('modules', [])}
    for key, node in nodes.items():
        mids = cfg.get('module_map', {}).get(key, [])
        if any(m not in modules for m in mids):
            raise crm_store.CRMError('Strategy module mapping names an unknown module', 503)
        node['modules'] = [{'id': m, 'name': modules[m]['name'], 'status': modules[m]['status'],
                            'version': modules[m].get('version'), 'sources': modules[m].get('sources', []),
                            'gate': modules[m].get('gate', {})} for m in mids]
        node['readiness'] = ('recorded_built' if mids and all(modules[m]['status'] == 'built' for m in mids)
                             else 'future' if mids else 'unassessed')
        node['source'] = (prof.get('crm') or {}).get('tech_tree_catalog')
        node['department'] = cfg.get('node_departments', {}).get(key, cfg.get('default_department', ''))
    departments = [{k: d.get(k, '') for k in ('id', 'label', 'plain_english')} for d in prof['departments']
                   if not d.get('cross_cutting')]
    visible = {c['id']: c for c in source['contacts']}
    tasks = classified_tasks(prof, cfg, source, links, departments)
    activity_tasks = tasks
    tasks = [t for t in tasks if not t.get('archived_at')]
    # Presentation links point at authoritative CRM tasks; no status or dates live here.
    for task in tasks:
        mapping = cfg.get('company_task_links', {}).get(task['company_id'], {})
        task['build_node_ids'] = [key for key in nodes if task['id'] in mapping.get(key, [])
                                  or task['context'].get('node_id') == key]
    if any(t['department'] == 'unassigned' for t in tasks):
        departments.append({'id':'unassigned','label':'Unassigned','plain_english':'Work that needs a department. No assignment has been inferred.'})
    companies = []
    locations = read_json(prof, cfg['locations']).get('locations', {}) if cfg.get('locations') else {}
    for c in source['contacts']:
        if c['kind'] != 'company':
            continue
        installed = [n for n in source['client_nodes'] if n['contact_id'] == c['id']]
        work = [t for t in tasks if t['company_id'] == c['id']]
        # The contact's existing research link is the authority when an industry is known.
        research = source.get('research_links', {}).get(c['id'], {})
        industry = industries.get(c['id']) or research.get('industry') or 'Industry not recorded'
        location = location_for(c, locations)
        companies.append({**c, 'industry': industry, 'installations': installed,
                          'health': 'unknown', 'placement': 'geocoded' if location else 'unplaced', 'location': location,
                          'location_note': (locations.get(c['id'], {}).get('reason') or
                                            ('No address recorded.' if not c.get('address') else 'Address needs a confirmed map match.')) if not location else '',
                          'activity_ids': [a['id'] for a in source['activities']
                                           if a['contact_id'] == c['id'] or visible.get(a['contact_id'], {}).get('company_id') == c['id']],
                          'tasks': [t['id'] for t in work],
                          'eligibility': {k: requirements(n, installed) for k, n in nodes.items()}})
    agents, warnings = [], []
    allowed_views = source.get('allowed_views', access.views_for(prof, person))
    if cfg.get('agent_registry') and 'agents' in allowed_views:
        try:
            registry = read_json(prof, cfg['agent_registry'])
            agents = [{k: a.get(k) for k in ('id', 'label', 'department', 'kind', 'status', 'trigger', 'source')}
                      for a in registry.get('agents', [])]
            for agent in agents:
                agent['run_state'] = 'unknown'
        except crm_store.CRMError:
            warnings.append('Agent registry unavailable. Current activity is unknown.')
    # Telemetry is optional and record-scoped; never show another owner's task run.
    runs = []
    if cfg.get('runs'):
        try:
            observed = read_json(prof, cfg['runs'])
            ids = {t['id'] for t in tasks}
            at = datetime.now(timezone.utc)
            for row in observed.get('runs', []):
                if row.get('task_id') not in ids:
                    continue
                run = {k: row.get(k) for k in ('id', 'agent_id', 'task_id', 'heartbeat_at', 'evidence')}
                run['status'] = run_state(row, at)
                runs.append(run)
        except crm_store.CRMError:
            warnings.append('Run telemetry unavailable. Current activity is unknown.')
    from . import feedback, chat_handoffs, strategy_activity, payments, analytics_reporting
    payment_model = payments.summarize(source.get('payments') if hasattr(store, 'strategy_source') else payments.snapshot(store, prof, person), hidden)
    analytics = source.get('analytics', {'state': 'not_connected'}) if hasattr(store, 'strategy_source') else analytics_reporting.snapshot(prof, person)
    handoffs = source.get('chat_handoffs') if hasattr(store, 'strategy_source') else chat_handoffs.monitor(store, prof, person)
    activity = strategy_activity.build(cfg, departments, activity_tasks, handoffs, hidden,
                                       strategy_activity.instant(source['server_time']))
    return {k: source[k] for k in ('name', 'person', 'members', 'capabilities', 'server_time', 'stages', 'waiting')} | {
        'feedback': feedback.options(prof, source, cfg),
        'chat_handoffs': handoffs,
        'department_activity': activity,
        'delivery_board': strategy_activity.delivery(cfg, departments, companies, tasks, list(nodes.values())),
        'command_focus': strategy_activity.focus(departments, activity),
        'daily_command': strategy_activity.daily(departments, tasks, strategy_activity.instant(source['server_time'])),
        'payments': payment_model,
        'analytics': analytics,
        'companies': companies, 'tasks': tasks, 'activities': source['activities'], 'nodes': list(nodes.values()),
        'edges': edges, 'layers': layers, 'departments': departments, 'agents': agents, 'runs': runs,
        'warnings': warnings, 'sample': sample,
        'connection': source.get('connection', {'mode':'local','read_at':source['server_time']}),
        'modules': [{k:m.get(k) for k in ('id','name','job','status','version','sources','gate')} |
                    {'capabilities':[n['id'] for n in nodes.values() if any(x['id'] == m['id'] for x in n['modules'])]}
                    for m in modules.values()],
        'product_tree': product_tree(cfg, modules),
        'coverage': {'hidden_samples':len(hidden),
                     'unclassified_tasks':sum(t['department'] == 'unassigned' and t['status'] == 'open' for t in tasks),
                     'unknown_industries':sum(c['industry'] == 'Industry not recorded' for c in companies),
                     'recorded_builds':sum(n['state'] == 'built' for c in companies for n in c['installations']),
                     'proposed_systems':sum(n['state'] == 'recommended' for c in companies for n in c['installations']),
                     'unlinked_people':[{'id':c['id'],'name':c['name']} for c in visible.values() if c['kind'] == 'person' and not c.get('company_id')]},
        'metrics': [{'id': 'collections', 'label': 'Payments collected', 'period': 'All recorded time' if payment_model.get('combined_total_cents') is not None else 'This month',
                     'value': payment_model.get('combined_total_cents') if payment_model.get('combined_total_cents') is not None else payment_model.get('combined_cents') if (payment_model.get('stripe') or {}).get('state') in ('connected','error') else payment_model.get('collected_cents'), 'currency': payment_model.get('currency', 'USD'),
                     'total_value': payment_model.get('combined_total_cents'), 'month_value': payment_model.get('combined_cents'), 'month_label': payment_model.get('periods', {}).get('this_month', {}).get('label', payment_model.get('month', '')),
                     'state': 'live' if payment_model.get('combined_cents') is not None else 'error' if (payment_model.get('stripe') or {}).get('state') in ('connected','error') else 'manual' if payment_model.get('enabled') else 'not_connected',
                     'definition': 'Manual receipts plus Stripe collections after refunds and disputes, before processing fees. Pending checks and bank payouts are separate.' if payment_model.get('combined_cents') is not None else 'Manual receipts only. See Payments for Stripe connection status; unavailable Stripe totals are not treated as zero.'},
                    {'id': 'traffic', 'label': 'Website sessions', 'period': 'Last 30 completed days',
                     'value': analytics.get('sessions') if analytics.get('state') == 'connected' else None,
                     'state': 'live' if analytics.get('state') == 'connected' else analytics.get('state', 'not_connected'),
                     'definition': 'Google Analytics sessions for the configured website hosts. Excludes today; recent data may still be processing.'}],
        'category': cfg.get('build_category', ''), 'default_department': cfg.get('default_department', ''),
        'provenance': {'catalog': (prof.get('crm') or {}).get('tech_tree_catalog'),
                       'modules': cfg.get('module_catalog'), 'agents': cfg.get('agent_registry')},
    }
