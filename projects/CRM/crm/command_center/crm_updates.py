"""Private, owner-scoped CRM change notifications, including writes by other processes."""
import hashlib
import json
import time

from . import access, identity, profiles


def revision(store, prof, person):
    store.authorize(prof, person)
    where, params = access.contact_scope(prof, person, 'c')
    digest = hashlib.sha256(json.dumps(prof, sort_keys=True, default=str).encode())
    with store.connection() as db:
        db.execute('BEGIN')
        rows = db.execute('SELECT c.id,c.revision,c.updated_at FROM contacts c WHERE c.deleted_at IS NULL' + where + ' ORDER BY c.id', params)
        digest.update(json.dumps([tuple(r) for r in rows]).encode())
        for table in ('tasks', 'deals', 'activities', 'client_nodes'):
            rows = db.execute(f'SELECT r.* FROM {table} r JOIN contacts c ON c.id=r.contact_id '
                              'WHERE c.deleted_at IS NULL' + where + ' ORDER BY r.id', params)
            digest.update(json.dumps([tuple(r) for r in rows]).encode())
        pins = db.execute('SELECT task_id,pinned_at FROM task_pins WHERE person_id=? ORDER BY task_id', (person.id,))
        digest.update(json.dumps([tuple(r) for r in pins]).encode())
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='workspace_preferences'").fetchone():
            prefs = db.execute('SELECT key,revision FROM workspace_preferences WHERE person_id=? ORDER BY key', (person.id,))
            digest.update(json.dumps([tuple(r) for r in prefs]).encode())
        # Receipts are authoritative audit projections, including archived/deleted
        # companies. Match the payment reader's current ownership boundary.
        rows=db.execute("SELECT a.id FROM audit a JOIN contacts c ON c.id=json_extract(a.payload,'$.result.payment.contact_id') "
                        "WHERE a.action IN ('payment.record','payment.clear','payment.void','payment.refund')"+where+' ORDER BY a.id',params)
        digest.update(json.dumps([r[0] for r in rows]).encode())
    return digest.hexdigest()


def stream(handler, prof, person):
    store = handler.cc.crm
    token = revision(store, prof, person)
    handler.send_response(200)
    for name, value in {'Content-Type': 'text/event-stream', 'Cache-Control': 'private, no-store',
                        'X-Accel-Buffering': 'no', 'Connection': 'close'}.items():
        handler.send_header(name, value)
    handler.end_headers()
    if handler.command == 'HEAD':
        return
    last = handler.headers.get('Last-Event-ID', '')
    deadline = time.monotonic() + 25
    try:
        handler.wfile.write(b'retry: 1000\n\n')
        while True:
            if token != last:
                handler.wfile.write(f'id: {token}\nevent: change\ndata: {{"revision":"{token}"}}\n\n'.encode())
                last = token
            handler.wfile.write(b'event: heartbeat\ndata: {}\n\n')
            handler.wfile.flush()
            if time.monotonic() >= deadline:
                break
            time.sleep(0.75)
            # Recheck membership, role, ownership and token validity during the stream.
            prof = profiles.load(handler.cc.business, handler.cc.root)
            person = identity.resolve(handler.headers, handler.client_address, prof, mode=handler.cc.mode)
            token = revision(store, prof, person)
    except (BrokenPipeError, ConnectionError, OSError):
        pass
    except Exception:
        try:
            handler.wfile.write(b'event: unavailable\ndata: {}\n\n')
            handler.wfile.flush()
        except OSError:
            pass
    finally:
        handler.close_connection = True
