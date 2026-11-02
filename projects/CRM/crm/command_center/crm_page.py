"""CRM workspace shell. All content and writes go through authenticated CRM routes."""
import html
from command_center import workspace_shell
import hashlib
import re
from pathlib import Path


def version_assets(page):
    """Give each bundled asset a content URL so returning users get this release."""
    def replace(match):
        path = Path(__file__).parent / 'www' / match[2]
        if not path.is_file():
            return match[0]
        version = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        return match[1] + match[2] + '?v=' + version + match[3]
    return re.sub(r'((?:src|href)=")([a-z0-9-]+\.(?:js|css))(")', replace, page)


def render(prof, preview=False):
    brand = (prof.get("crm") or {}).get("accent", "#3355e0")
    if not isinstance(brand, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", brand):
        brand = "#3355e0"
    prefix = (prof.get("crm") or {}).get("path_prefix", "/")
    if not isinstance(prefix, str) or not re.fullmatch(r"/(?:[a-zA-Z0-9_-]+/)*", prefix):
        raise ValueError("CRM path_prefix must be an absolute directory path")
    crm = prof.get("crm") or {}
    font = '<link rel="stylesheet" href="crm-font.css">' if crm.get("font_stylesheet") else ""
    # The business's own look, after the engine's: tokens and brand shapes only (crm-theme.css).
    theme = '<link rel="stylesheet" href="crm-theme.css">' if crm.get("theme_stylesheet") else ""
    mode = crm.get("theme_default") if crm.get("theme_default") in ("light", "dark") else ""
    mode = f' data-theme="{mode}"' if mode else ""
    name = html.escape(prof["name"])
    extra_nav = '<button data-nav="research">Research</button>' if (prof.get("crm") or {}).get("research") else ""
    if crm.get('strategy'):
        title = 'Command Center (Beta)' if crm.get('strategy_beta') else 'Strategy command center'
        extra_nav += f'<a href="strategy" style="display:block;padding:12px">{title} ↗</a>'
    if isinstance((prof.get("crm") or {}).get("calls"), dict):
        extra_nav += '<button data-nav="calls">Calls and chats</button>'
    if isinstance((prof.get("crm") or {}).get("content"), dict):
        extra_nav += '<button data-nav="content">Content</button>'
    if isinstance((prof.get("crm") or {}).get("leaderboard"), dict):
        extra_nav += '<button data-nav="leaderboard">Leaderboard</button>'
    subtitle = "Local CRM preview &middot; Separate database" if preview else "Command Center &middot; CRM"
    if (prof.get("crm") or {}).get("demo"):
        subtitle = "Demo workspace - Sample data only - Email not connected"
    page = f'''<!doctype html>
<html lang="en"{mode} data-theme-key="{html.escape(prof.get('_theme_key') or prof['business_id'] + '.theme', quote=True)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<base href="{prefix}"><meta name="robots" content="noindex,nofollow"><title>{name} | CRM</title>
{font}<link rel="stylesheet" href="crm.css">{theme}<style>:root{{--accent:{brand}}}</style><link rel="stylesheet" href="task-editor.css"><link rel="stylesheet" href="workspace-design.css"><script defer src="workspace-design.js"></script><script defer src="crm-sync.js"></script><script defer src="workspace-views.js"></script><script defer src="task-editor.js"></script><script defer src="record-form.js"></script><script defer src="task-create.js"></script><script defer src="workspace-actions.js"></script><script defer src="workspace-daily.js"></script><link rel="stylesheet" href="workspace-daily.css"><script defer src="crm.js"></script><link rel="stylesheet" href="workspace-search.css"><script defer src="workspace-search.js"></script><link rel="stylesheet" href="workspace-shell.css"><script defer src="workspace-shell.js"></script><script defer src="workspace-favorites.js"></script><script defer src="workspace-bulk.js"></script><script defer src="crm-io.js"></script></head>
<body class="workspace-design"><a class="skip" href="#main">Skip to workspace</a>
{workspace_shell.header(prof, subtitle=html.unescape(subtitle))}
<div class="layout">{workspace_shell.navigation(prof, 'crm')}
<main id="main"><div id="notice" role="status" aria-live="polite"></div><div id="content"><p>Loading your workspace&hellip;</p></div></main></div>
<dialog id="editor"><form id="edit-form"><div class="dialog-heading"><h2 id="editor-title"></h2><button type="button" id="close-editor" aria-label="Close form">&times;</button></div><div id="fields"></div><p id="form-error" role="alert"></p><div class="actions"><button type="button" id="cancel-editor">Cancel</button><button type="submit" class="primary" id="save-editor">Save</button></div></form></dialog>
<dialog id="viewer"><div class="dialog-heading"><h2 id="viewer-title"></h2><button type="button" id="close-viewer" aria-label="Close">&times;</button></div><div id="viewer-body"></div></dialog>
<noscript>This workspace needs JavaScript to load and save CRM records.</noscript></body></html>'''
    return version_assets(page)
