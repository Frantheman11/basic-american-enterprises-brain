"""Read a deployed CRM through its own permissions and a read-only SQLite connection.

The local dashboard keeps no replica database. SSH transports scoped reads. The
optional action doors use the deployed CRM writer, with explicit action and company scopes.
"""
import json
import shlex
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlsplit

from . import crm_store

COMPANY_FIELDS = {'task.update': {'id', 'revision', 'status', 'due_at'},
                  'activity.add': {'contact_id', 'kind', 'body'},
                  'node.set': {'contact_id', 'node_id', 'revision', 'state', 'location', 'evidence'}}


# Executed on the existing host as the service user. Settings arrive as Python string
# literals, never interpolated shell text. No constructor runs a schema migration.
READ = '''
import json, sqlite3, sys
from pathlib import Path
from contextlib import contextmanager, closing
sys.path.insert(0, settings['release'])
from command_center import crm_store, profiles, identity, access
class Reader(crm_store.Store):
    def __init__(self, db):
        self.business = settings['business']
        self.db = db
    @contextmanager
    def connection(self):
        yield self.db
with closing(sqlite3.connect(Path(settings['database']).as_uri()+'?mode=ro', uri=True, timeout=5)) as db:
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    binding = db.execute("SELECT value FROM metadata WHERE key='business'").fetchone()
    if not binding or binding[0] != settings['business']:
        raise ValueError('Database identity mismatch')
    prof = profiles.load(settings['business'], settings['release'])
    person = identity.person(prof, settings['person'])
    source = Reader(db).snapshot(prof, person)
    contacts = {c['id'] for c in source['contacts']}
    tasks = {t['id'] for t in source['tasks']}
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    industries = {r['contact_id']: r['industry'] for r in db.execute('SELECT contact_id,industry FROM research_claims')
                  if r['contact_id'] in contacts and r['industry']}
    if 'strategy_companies' in tables:
        industries.update({r['contact_id']:r['industry'] for r in db.execute('SELECT * FROM strategy_companies')
                           if r['contact_id'] in contacts})
    links = ({r['task_id']:dict(r) for r in db.execute('SELECT * FROM strategy_task_links') if r['task_id'] in tasks}
             if 'strategy_task_links' in tables else {})
    # Retain recent, already permission-scoped history for company detail pages.
    recent, counts = [], {}
    for activity in source['activities']:
        cid = activity['contact_id']
        if counts.get(cid, 0) < 12:
            recent.append(activity)
            counts[cid] = counts.get(cid, 0) + 1
    keep = ('name','person','members','capabilities','server_time','stages','waiting','contacts','tasks','client_nodes','research_links')
    source = {k:source[k] for k in keep}
    source['category_definitions'] = {k:(prof.get('crm') or {}).get(k, [])
                                      for k in ('task_categories','business_task_categories')}
    source['activities'] = recent
    source['payments'] = {'enabled': False}
    if getattr(crm_store.Store, 'payment_protocol', 0) == 1:
        from command_center import payments
        source['payments'] = payments.snapshot(Reader(db), prof, person)
    try:
        from command_center import analytics_reporting
        source['analytics'] = analytics_reporting.snapshot(prof, person)
    except ImportError:
        source['analytics'] = {'state': 'not_connected'}
    try:
        from command_center import chat_handoffs
        source['chat_handoffs'] = chat_handoffs.monitor(Reader(db), prof, person)
    except ImportError:
        source['chat_handoffs'] = {'enabled':False,'items':[]}
    feedback_available = False
    if getattr(crm_store.Store, 'feedback_protocol', 0) == 1:
        from command_center import feedback
        feedback_available = feedback.enabled(prof) and 'crm_edit' in source['capabilities']
    company_available = 'crm_edit' in source['capabilities'] and db.execute('PRAGMA user_version').fetchone()[0] == crm_store.SCHEMA
    print(json.dumps({'source':source,'industries':industries,'links':links,'feedback_available':feedback_available,'company_available':company_available,
                      'views':access.views_for(prof,person),'schema':db.execute('PRAGMA user_version').fetchone()[0]}))
'''

# Only the deployed implementation handles the transaction; no alternate SQL writer.
WRITE_FEEDBACK = '''
import json, sqlite3, sys
from pathlib import Path
sys.path.insert(0, settings['release'])
from command_center import crm_store, feedback
from tools.todo import Todo
try:
    company_id = settings.get('company_id')
    payment_action = settings.get('payments') is True
    if payment_action:
        if getattr(crm_store.Store, 'payment_protocol', 0) != 1:
            raise crm_store.CRMError('Live payment recording is not available', 503)
        from command_center import payments
        if command.get('action') not in payments.ACTIONS:
            raise crm_store.CRMError('Only a payment record action is allowed', 403)
    if not payment_action and not company_id and (command.get('action') != 'feedback.submit' or getattr(crm_store.Store, 'feedback_protocol', 0) != 1):
        raise crm_store.CRMError('Live feedback saving is not available', 503)
    with sqlite3.connect(Path(settings['database']).as_uri()+'?mode=ro', uri=True) as db:
        binding = db.execute("SELECT value FROM metadata WHERE key='business'").fetchone()
        if not binding or binding[0] != settings['business'] or db.execute('PRAGMA user_version').fetchone()[0] != crm_store.SCHEMA:
            raise crm_store.CRMError('Live database identity or version changed; no write was attempted', 503)
    todo = Todo(settings['business'], settings['release'], settings['database'], settings['person'])
    todo.store.authorize(todo.prof, todo.person, 'crm_edit')
    if company_id:
        action, data = command['action'], command['data']
        if action not in company_fields or set(data) - company_fields[action]:
            raise crm_store.CRMError('This company action is not allowed', 403)
        with todo.store.connection() as db:
            company = todo.store.contact(db, todo.prof, todo.person, company_id)
            if company['kind'] != 'company' or company['lifecycle'] == 'archived':
                raise crm_store.CRMError('Choose an active company', 403)
            if action == 'task.update':
                task = db.execute('SELECT * FROM tasks WHERE id=?', (data.get('id'),)).fetchone()
                if not task or task['archived_at']:
                    raise crm_store.CRMError('Task not found', 404)
                contact = todo.store.contact(db, todo.prof, todo.person, task['contact_id'])
                if (contact.get('company_id') or contact['id']) != company_id:
                    raise crm_store.CRMError('Task does not belong to this company', 403)
                if not (set(data) & {'status','due_at'}) or data.get('status', 'open') not in ('open','completed'):
                    raise crm_store.CRMError('Choose completion, reopening or a review date')
            elif data.get('contact_id') != company_id:
                raise crm_store.CRMError('The record does not match this company', 403)
            if action == 'activity.add' and data.get('kind') != 'note':
                raise crm_store.CRMError('Only a note can be added here')
            if action == 'node.set':
                node = db.execute('SELECT * FROM client_nodes WHERE contact_id=? AND node_id=?', (company_id,data.get('node_id'))).fetchone()
                if not node or data.get('state') not in ('in_progress','built'):
                    raise crm_store.CRMError('Choose an existing build and a supported status', 409)
                if not str(data.get('evidence', '')).strip():
                    raise crm_store.CRMError('Describe the progress or installation evidence')
    elif not payment_action and not feedback.enabled(todo.prof):
        raise crm_store.CRMError('Live feedback saving is disabled', 403)
    result = todo.run(command['action'], command['data'], command['request_id'])
    print(json.dumps({'result':result}))
except crm_store.CRMError as error:
    print(json.dumps({'error':str(error),'status':error.status}))
'''


class RemoteStore(crm_store.Store):
    readonly = True

    def __init__(self, business, settings):
        self.business, self.settings = business, dict(settings)
        url = urlsplit(settings['url'])
        if url.scheme != 'https' or not url.hostname or url.username or url.query or url.fragment:
            raise ValueError('Live CRM URL must be an HTTPS base URL')
        self.url = settings['url'].rstrip('/') + '/'
        self.lock = threading.Lock()
        self.feedback_enabled = settings.get('feedback') is True
        self.company_actions = tuple(settings.get('company_actions') or [])
        self.payments_enabled = settings.get('payments') is True

    def authorize(self, prof, person, capability='crm_read'):
        super().authorize(prof, person, capability)
        if capability != 'crm_read' or person.id != self.settings['person']:
            raise crm_store.CRMError('This connection is a read-only view for its configured person', 403)

    def execute(self, *args, **kwargs):
        raise crm_store.CRMError('Make changes in the live CRM', 403)

    def connection(self):
        raise crm_store.CRMError('No local replica database exists for this view', 403)

    def submit_feedback(self, prof, person, command):
        self.authorize(prof, person)
        super().authorize(prof, person, 'crm_edit')
        if not self.feedback_enabled:
            raise crm_store.CRMError('Feedback saving is disabled on this connection', 403)
        if not isinstance(command, dict) or set(command) != {'action', 'data', 'request_id'} or command.get('action') != 'feedback.submit' or not isinstance(command.get('data'), dict):
            raise crm_store.CRMError('Only a feedback submission is allowed')
        return self._submit(command, receipt_fields=('id','activity_id','contact_id'))

    def submit_company(self, prof, person, envelope):
        self.authorize(prof, person)
        super().authorize(prof, person, 'crm_edit')
        if not isinstance(envelope, dict) or set(envelope) != {'company_id','action','data','request_id'}:
            raise crm_store.CRMError('Invalid company action')
        company_id = envelope['company_id']
        if company_id not in self.company_actions:
            raise crm_store.CRMError('Actions are not enabled for this company', 403)
        command = {k:envelope[k] for k in ('action','data','request_id')}
        if command['action'] not in COMPANY_FIELDS or not isinstance(command['data'], dict) or set(command['data']) - COMPANY_FIELDS[command['action']]:
            raise crm_store.CRMError('This company action is not allowed', 403)
        return self._submit(command, company_id=company_id)

    def submit_payment(self, prof, person, command):
        from . import payments
        self.authorize(prof, person)
        super().authorize(prof, person, 'crm_edit')
        if not self.payments_enabled:
            raise crm_store.CRMError('Payment recording is disabled on this connection', 403)
        if not isinstance(command, dict) or set(command) != {'action', 'data', 'request_id'} or command.get('action') not in payments.ACTIONS or not isinstance(command.get('data'), dict):
            raise crm_store.CRMError('Only a payment record action is allowed')
        return self._submit(command, payment_action=True)

    def _submit(self, command, company_id=None, receipt_fields=('id',), payment_action=False):
        crm_store.text(command.get('request_id'), 'Request ID', 80, True)
        s = self.settings
        settings = {k:s[k] for k in ('release', 'database', 'person')}
        settings['business'] = self.business
        settings['company_id'] = company_id
        settings['payments'] = payment_action
        script = 'settings = '+repr(settings)+'\ncommand = '+repr(command)+'\ncompany_fields = '+repr(COMPANY_FIELDS)+'\n'+WRITE_FEEDBACK
        remote = ' '.join(shlex.quote(x) for x in ('sudo','-u',s['service_user'],s['python'],'-B','-'))
        ssh = ['ssh','-i',str(Path(s['key']).expanduser()),'-o','BatchMode=yes','-o','ConnectTimeout=10',s['host'],remote]
        try:
            result = subprocess.run(ssh, input=script, capture_output=True, text=True, encoding='utf-8', timeout=25, check=True)
            reply = json.loads(result.stdout)
            if 'error' in reply:
                status = reply.get('status', 503)
                raise crm_store.CRMError(reply['error'], status if type(status) is int and status in (400,403,404,409,503) else 503)
            receipt = reply['result']
            if receipt.get('ok') is not True or not all(isinstance(receipt.get(k),str) and receipt[k] for k in receipt_fields):
                raise ValueError('Incomplete feedback receipt')
            return receipt
        except crm_store.CRMError:
            raise
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            raise crm_store.CRMError('The live CRM save could not be confirmed. Retry the same request to check without duplicating it.', 503) from None

    def strategy_source(self, prof, person):
        self.authorize(prof, person)
        s = self.settings
        settings = {k: s[k] for k in ('release', 'database', 'person')}
        settings['business'] = self.business
        script = 'settings = ' + repr(settings) + '\n' + READ
        command = ' '.join(shlex.quote(x) for x in ('sudo','-u',s['service_user'],s['python'],'-B','-'))
        ssh = ['ssh','-i',str(Path(s['key']).expanduser()),'-o','BatchMode=yes','-o','ConnectTimeout=10',s['host'],command]
        try:
            # Serialize refreshes. Never reuse an old success after a failed read.
            if not self.lock.acquire(timeout=5):
                raise crm_store.CRMError('A live refresh is already in progress. Try again shortly.', 503)
            try:
                result = subprocess.run(ssh, input=script, capture_output=True, text=True,
                                        encoding='utf-8', timeout=40, check=True)
            finally:
                self.lock.release()
            payload = json.loads(result.stdout)
            source = payload['source']
            if source['person']['id'] != person.id or 'crm_read' not in source['capabilities']:
                raise ValueError('Live identity mismatch')
            source['capabilities'] = ['crm_read']
            if source.get('payments', {}).get('enabled'):
                source['payments']['writable'] = self.payments_enabled and source['payments'].get('writable') is True
            source['connection'] = {'mode':'live_readonly','url':self.url,
                                    'writable_companies':[c['id'] for c in source['contacts'] if c['id'] in self.company_actions and c['kind']=='company' and c['lifecycle']!='archived'] if payload.get('company_available') is True else [],
                                    'feedback_writable':self.feedback_enabled and payload.get('feedback_available') is True,
                                    'read_at':source['server_time'],'schema':payload['schema']}
            source['allowed_views'] = payload['views']
            return source, payload['links'], payload['industries']
        except crm_store.CRMError:
            raise
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            raise crm_store.CRMError('Live CRM could not be read. Check the connection and refresh; previous records may be stale.', 503) from None
