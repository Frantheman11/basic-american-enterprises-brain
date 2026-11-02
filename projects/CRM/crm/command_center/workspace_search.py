"""Live, permission-scoped search. No copied index or truncated activity feed."""
import re
import unicodedata

from . import access, crm_store, record_edits

KINDS = ('company', 'contact', 'task', 'note')


def normalize(value):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD', str(value or '')).casefold()
                           if not unicodedata.combining(c)).split())


def excerpt(value, terms):
    value = ' '.join(str(value or '').split())
    folded = normalize(value)
    positions = [folded.find(term) for term in terms if term in folded]
    start = max(0, min(positions, default=0) - 45)
    return ('…' if start else '') + value[start:start + 210] + ('…' if len(value) > start + 210 else '')


def visible(prof, person, archived):
    scope, given = access.contact_scope(prof, person, 'c')
    clause, params = 'c.deleted_at IS NULL' + scope, list(given)
    if not archived:
        clause += " AND c.lifecycle!='archived'"
    return clause, params


def context(contact, contacts):
    parent = contacts.get(contact.get('company_id'))
    return contact['name'] + (' · ' + parent['name'] if parent else '')


def search(store, prof, person, query='', kind='all', archived='0', offset='0'):
    store.authorize(prof, person)
    if not isinstance(query, str) or len(query) > 160:
        raise crm_store.CRMError('Search must be 160 characters or fewer')
    query = ' '.join(query.split())
    if kind not in ('all', *KINDS) or archived not in ('0', '1'):
        raise crm_store.CRMError('Choose a supported search filter')
    if not isinstance(offset, str) or not re.fullmatch(r'\d{1,6}', offset):
        raise crm_store.CRMError('Invalid search page')
    offset = int(offset)
    if kind == 'all' and offset:
        raise crm_store.CRMError('Choose a record type to see more results')
    groups = {key: [] for key in KINDS if kind in ('all', key)}
    terms = normalize(query).split()
    if len(normalize(query)) < 2:
        return {'query': query, 'groups': [], 'total': 0}
    phone = re.sub(r'\D', '', query) if re.fullmatch(r'[\d\s()+.\-]+', query) else ''

    def add(record, body='', extra=''):
        title = normalize(record['title'])
        combined = normalize(' '.join((record['title'], record['context'], body, extra)))
        phone_match = len(phone) >= 3 and phone in re.sub(r'\D', '', extra)
        if not all(term in combined for term in terms) and not phone_match:
            return
        score = 0 if title == normalize(query) else 1 if title.startswith(normalize(query)) else 2 if all(t in title for t in terms) else 3
        record['snippet'] = excerpt(body or extra, terms)
        groups[record['type']].append((score, record))

    with store.connection() as db:
        db.execute('BEGIN')
        where, params = visible(prof, person, archived == '1')
        contacts = {r['id']: dict(r) for r in db.execute('SELECT c.* FROM contacts c WHERE ' + where, params)}
        if kind in ('all', 'company', 'contact'):
            channels = {}
            for r in db.execute('SELECT x.contact_id,x.value FROM contact_channels x JOIN contacts c ON c.id=x.contact_id WHERE ' + where, params):
                channels.setdefault(r['contact_id'], []).append(r['value'])
            for c in contacts.values():
                category = 'company' if c['kind'] == 'company' else 'contact'
                if category not in groups:
                    continue
                extra = ' · '.join(dict.fromkeys(filter(None, [c.get('email'), c.get('phone'), c.get('role'), c.get('website'), c.get('address'), *channels.get(c['id'], [])])))
                add({'type': category, 'id': c['id'], 'contact_id': c['id'], 'title': c['name'],
                     'context': context(c, contacts), 'status': c['lifecycle'], 'updated_at': c['updated_at']}, extra=extra)
        if kind in ('all', 'task'):
            suffix = '' if archived == '1' else ' AND r.archived_at IS NULL'
            for r in db.execute('SELECT r.* FROM tasks r JOIN contacts c ON c.id=r.contact_id WHERE ' + where + suffix, params):
                c = contacts[r['contact_id']]
                add({'type': 'task', 'id': r['id'], 'contact_id': c['id'], 'title': r['title'],
                     'context': context(c, contacts), 'status': 'archived' if r['archived_at'] else r['status'],
                     'due_at': r['due_at'], 'updated_at': r['updated_at']},
                    ' · '.join(filter(None, [r['action_detail'], r['blocked_reason'], r['completion_evidence']])))
        if kind in ('all', 'note'):
            for r in db.execute("SELECT r.* FROM activities r JOIN contacts c ON c.id=r.contact_id WHERE " + where +
                                " AND r.deleted_at IS NULL AND r.kind IN ('note','call','meeting','email')", params):
                c = contacts[r['contact_id']]
                add({'type': 'note', 'id': r['id'], 'contact_id': c['id'], 'title': r['kind'].capitalize() + ' · ' + c['name'],
                     'context': context(c, contacts), 'kind': r['kind'], 'status': 'archived' if c['lifecycle'] == 'archived' else '',
                     'updated_at': r['edited_at'] or r['created_at'], 'created_at': r['created_at']}, r['body'])
    result = []
    for key, hits in groups.items():
        # Stable pages: rank first, then most recently changed, then ID for ties.
        hits.sort(key=lambda item: item[1]['id'])
        hits.sort(key=lambda item: item[1]['updated_at'], reverse=True)
        hits.sort(key=lambda item: item[0])
        limit = 5 if kind == 'all' else 25
        result.append({'type': key, 'total': len(hits), 'items': [item for _, item in hits[offset:offset + limit]],
                       'next_offset': offset + limit if len(hits) > offset + limit else None})
    return {'query': query, 'groups': result, 'total': sum(g['total'] for g in result)}


def note(store, prof, person, record_id):
    store.authorize(prof, person)
    record_id = crm_store.text(record_id, 'Note ID', 80, True)
    with store.connection() as db:
        db.execute('BEGIN')
        row = db.execute('SELECT * FROM activities WHERE id=? AND deleted_at IS NULL', (record_id,)).fetchone()
        if not row or row['kind'] not in record_edits.EDITABLE:
            raise crm_store.CRMError('Note not found', 404)
        c = store.contact(db, prof, person, row['contact_id'])
        return {'id': row['id'], 'kind': row['kind'], 'body': row['body'], 'contact_id': c['id'],
                'contact_name': c['name'], 'created_at': row['created_at'], 'edited_at': row['edited_at']}
