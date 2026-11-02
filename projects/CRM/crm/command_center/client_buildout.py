"""Restricted, read-only client tech tree (internal name: Build-out). No internal CRM routes or filesystem browsing.

Copy this module with its listed dependencies into the client's own repository.
Run: python -m command_center.client_buildout --business ID --crm-db PATH --port 8830
The configured identity is required; this server has no identity override CLI flag.
"""
import argparse
import html
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import crm_store, identity, profiles


# Same square icon as the internal Build-out panel (crm.js), so both views read as one drawing.
ICON = ('<span class="tree-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.6"><rect x="3" y="3" width="7" height="7" rx="1.5"/>'
        '<rect x="14" y="14" width="7" height="7" rx="1.5"/><path d="M17 10V7h-3M7 14v3h3"/></svg></span>')
HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")
FONT = re.compile(r"[A-Za-z0-9 ,'-]{1,120}")


def brand(prof):
    """The client's own accent and font from its profile. Invalid values are ignored, never
    written into the page, because they land inside a <style> element."""
    cfg = (prof or {}).get("client_buildout") or {}
    accent = cfg.get("accent") if isinstance(cfg.get("accent"), str) and HEX.fullmatch(cfg["accent"]) else ""
    font = cfg.get("font_family") if isinstance(cfg.get("font_family"), str) and FONT.fullmatch(cfg["font_family"]) else ""
    return accent, font


def render(data, prof=None):
    esc = html.escape
    accent, font = brand(prof)
    built = sum(n['state'] == 'built' for n in data['nodes'])
    suggested = len(data['nodes']) - built
    layers = []
    for layer in data['layers']:
        nodes = []
        # Built first: what the client already owns, then what we recommend next.
        for n in sorted(data['nodes'], key=lambda n: n['state'] != 'built'):
            if n['layer'] != layer['id']:
                continue
            status = 'Built' if n['state'] == 'built' else 'Recommended for you'
            reason = f'<p class="why">{esc(n["reason"])}</p>' if n['reason'] else ''
            nodes.append(f'<article class="tree-node {esc(n["state"])}">{ICON}<span>'
                         f'<strong>{esc(n["label"])}</strong><small>{status}</small>'
                         f'<p>{esc(n["value"])}</p>{reason}</span></article>')
        layers.append(f'<section class="tree-layer"><h3>{esc(layer["label"])}</h3>'
                      f'<div class="tree-nodes">{"".join(nodes)}</div></section>')
    cols = max(1, min(len(layers), 3))
    tree = (f'<div class="tree-layers">{"".join(layers)}</div>' if layers else
            '<p class="muted empty">Nothing to show yet. Finished builds and recommendations '
            'appear here once they are ready.</p>')
    style = ':root{' + (f'--accent:{accent};' if accent else '') +             (f"--sans:{font},system-ui,sans-serif;" if font else '') + '}'
    # Fewer than four layers: one row, no full-width last row (that rule suits the internal panel).
    style += (f'.client-tree .tree-layers{{grid-template-columns:repeat({cols},minmax(0,1fr))}}'
              '.client-tree .tree-layer:last-child{grid-column:auto;border-top:0}'
              '.client-tree .tree-layer:last-child .tree-nodes{grid-template-columns:1fr}'
              if len(layers) < 4 else '')
    style += ('.client-tree .tree-node{align-items:flex-start;background:var(--surface)}'
              '.client-tree .tree-node.recommended{background:var(--field)}'
              '.client-tree .tree-node p{margin:8px 0 0;font-size:14px;color:var(--ink)}'
              '.client-tree .tree-node p.why{color:var(--muted)}'
              '.client-tree .key{text-align:center;margin:24px 0 0}'
              '.client-tree .empty{text-align:center;padding:24px 0}'
              '@media(max-width:540px){.client-tree .tree-layers{display:block}}')
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>{esc(data['client'])} | {esc(data['title'])}</title>
<link rel="stylesheet" href="/buildout.css"><style>{style}</style></head><body>
<main style="max-width:1200px;margin:auto;padding:24px 16px">
<section class="panel client-tree build-out">
<div class="tree-hub">{ICON}<div><strong>{esc(data['client'])}</strong><small>{esc(data['title'])}</small></div></div>
{tree}
<p class="muted key">{built} built · {suggested} recommended. Solid: built. Dashed: recommended for you.</p></section></main></body></html>'''


def make_server(business, root, db_path, host='127.0.0.1', port=0):
    prof = profiles.load(business, root)
    mode = identity.check_startup(prof, host)
    if not Path(db_path).is_file():
        raise ValueError('Initialize the client-owned database explicitly before starting this view')
    store = crm_store.Store(db_path, business)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, body, content_type='text/html; charset=utf-8'):
            raw = body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'private, no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(raw)

        def read(self):
            current = profiles.load(business, root)
            person = identity.resolve(self.headers, self.client_address, current, mode=mode)
            return store.client_snapshot(current, person), current

        def do_GET(self):
            try:
                # Authenticate and authorize even styles and unknown routes.
                data, current = self.read()
                url = urlparse(self.path)
                if url.query:
                    return self.send(400, 'This view does not accept record selectors.', 'text/plain')
                if url.path in ('/', '/build-out'):
                    return self.send(200, render(data, current))
                if url.path == '/api/build-out':
                    return self.send(200, json.dumps(data), 'application/json')
                if url.path == '/buildout.css':
                    css = Path(__file__).parent / 'www' / 'crm.css'
                    return self.send(200, css.read_text(encoding='utf-8'), 'text/css')
                return self.send(404, 'Not found', 'text/plain')
            except (identity.Unauthenticated, identity.NotAPerson, crm_store.CRMError) as error:
                return self.send(error.status, 'Access denied', 'text/plain')
            except Exception:
                return self.send(503, 'Temporarily unavailable', 'text/plain')

        def do_HEAD(self):
            self.do_GET()

        def do_POST(self):
            try:
                self.read()
                self.send(405, 'Read-only view', 'text/plain')
            except (identity.Unauthenticated, identity.NotAPerson, crm_store.CRMError) as error:
                self.send(error.status, 'Access denied', 'text/plain')
            except Exception:
                self.send(503, 'Temporarily unavailable', 'text/plain')

    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--business', required=True)
    parser.add_argument('--root', default='.')
    parser.add_argument('--crm-db', required=True)
    parser.add_argument('--port', type=int, default=8830)
    args = parser.parse_args()
    srv = make_server(args.business, args.root, args.crm_db, port=args.port)
    print(f'Private tech tree view: http://127.0.0.1:{args.port}/', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
