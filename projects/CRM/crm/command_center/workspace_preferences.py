"""Account-scoped workspace preferences (favorites, saved views), synced across devices."""
import json
import re

from . import crm_store

KEYS = {'favorites': 12, 'views.crm': 12, 'views.command': 12}
LIMIT = 16000  # bytes per stored value
CAPABILITIES = {'preferences.set': 'crm_read'}
# A favorite links inside this workspace only: the CRM (./) or the Command Center (strategy).
HREF = re.compile(r'^(?:strategy|\./)?(?:\?[A-Za-z0-9_\-=&%.:+]*)?(?:#[A-Za-z0-9_\-]*)?$')


def migrate(db):
    db.execute('CREATE TABLE IF NOT EXISTS workspace_preferences (person_id TEXT NOT NULL, key TEXT NOT NULL, '
               'value TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL, '
               'PRIMARY KEY(person_id,key))')


def read(store, prof, person):
    store.authorize(prof, person)
    with store.connection() as db:
        rows = db.execute('SELECT key,value,revision,updated_at FROM workspace_preferences WHERE person_id=? ORDER BY key',
                          (person.id,)).fetchall()
    return {'person': person.id, 'limits': KEYS,
            'preferences': {r['key']: {'value': json.loads(r['value']), 'revision': r['revision'],
                                       'updated_at': r['updated_at']} for r in rows}}


def encode(key, value):
    if key not in KEYS:
        raise crm_store.CRMError('Choose a supported preference')
    if not isinstance(value, list):
        raise crm_store.CRMError('Preference value must be a list')
    if len(value) > KEYS[key]:
        raise crm_store.CRMError(f'Keep at most {KEYS[key]} saved items. Remove one first.')
    names = set()
    for row in value:
        fields = {'name', 'href'} if key == 'favorites' else {'name', 'state'}
        if not isinstance(row, dict) or set(row) != fields:
            raise crm_store.CRMError('Unexpected preference fields')
        name = crm_store.text(row.get('name'), 'Name', 60, True)
        if name in names:
            raise crm_store.CRMError('Each saved item needs a different name')
        names.add(name)
        if key == 'favorites':
            href = crm_store.text(row.get('href'), 'Link', 500, True)
            if not HREF.fullmatch(href):
                raise crm_store.CRMError('Favorites link inside this workspace only')
        elif not isinstance(row['state'], dict) or any(isinstance(v, (dict, list)) for v in row['state'].values()):
            raise crm_store.CRMError('A saved view holds simple filter values only')
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'))
    if len(encoded) > LIMIT:
        raise crm_store.CRMError('This preference is too large to save')
    return encoded


def change(store, db, prof, person, data, stamp):
    if set(data) - {'key', 'value', 'revision'}:
        raise crm_store.CRMError('Unexpected preference fields')
    key = data.get('key')
    encoded = encode(key, data.get('value'))
    row = db.execute('SELECT revision FROM workspace_preferences WHERE person_id=? AND key=?', (person.id, key)).fetchone()
    current = row['revision'] if row else 0
    if type(data.get('revision')) is not int or data['revision'] != current:
        raise crm_store.CRMError('These preferences changed on another device. Reload them before saving again.', 409)
    db.execute('INSERT INTO workspace_preferences VALUES (?,?,?,?,?) ON CONFLICT(person_id,key) DO UPDATE SET '
               'value=excluded.value,revision=excluded.revision,updated_at=excluded.updated_at',
               (person.id, key, encoded, current + 1, stamp))
    return {'ok': True, 'id': key, 'revision': current + 1}
