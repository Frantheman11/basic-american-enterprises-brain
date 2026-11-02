"""Strategy shell. Data and writes use the authenticated CRM boundary."""
import html
from command_center import workspace_shell
import re
from command_center.crm_page import version_assets


def render(prof, preview=False, live=False):
    crm = prof.get('crm') or {}
    prefix = crm.get('path_prefix', '/')
    if not isinstance(prefix, str) or not re.fullmatch(r'/(?:[a-zA-Z0-9_-]+/)*', prefix):
        raise ValueError('CRM path_prefix must be an absolute directory path')
    name = html.escape(prof['name'])
    short_name = html.escape(''.join(word[0] for word in prof['name'].split()))
    theme_key = html.escape(prof.get('_theme_key') or prof['business_id'] + '.theme', quote=True)
    research_link = '<a href="?view=research">Research map ↗</a>' if crm.get('research') else ''
    label = 'Sample campaign · fictional companies · isolated database' if crm.get('demo') else (
        'Local strategy workspace' if preview else 'Strategy workspace')
    if live:
        label = 'Live CRM · read-only'
    elif crm.get('strategy_beta'):
        label = 'Command Center Beta · live CRM records'
    search_button = '' if live else '<button type="button" id="workspace-search-launcher" aria-haspopup="dialog" aria-controls="workspace-search"><span aria-hidden="true">⌕</span> Search <kbd>Ctrl / ⌘ K</kbd></button>'
    page = f'''<!doctype html>
<html lang="en" data-theme="light" data-theme-key="{theme_key}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<base href="{prefix}"><meta name="robots" content="noindex,nofollow"><title>{name} | Command Center</title>
<link rel="stylesheet" href="crm-font.css"><link rel="stylesheet" href="strategy.css">
<link rel="stylesheet" href="strategy-help.css"><script defer src="workspace-design.js"></script><script defer src="strategy-art.js"></script><script defer src="strategy-help.js"></script><link rel="stylesheet" href="strategy-company.css"><script defer src="strategy-company.js"></script><link rel="stylesheet" href="strategy-products.css"><script defer src="strategy-products.js"></script><link rel="stylesheet" href="strategy-handoffs.css"><script defer src="strategy-handoffs.js"></script><link rel="stylesheet" href="strategy-activity.css"><script defer src="strategy-activity.js"></script><link rel="stylesheet" href="strategy-delivery.css"><script defer src="strategy-delivery.js"></script><link rel="stylesheet" href="strategy-payments.css"><script defer src="strategy-payments.js"></script><script defer src="strategy-connections.js"></script><link rel="stylesheet" href="task-editor.css"><script defer src="crm-sync.js"></script><script defer src="workspace-views.js"></script><script defer src="task-editor.js"></script><script defer src="record-form.js"></script><script defer src="task-create.js"></script><script defer src="workspace-actions.js"></script><script defer src="workspace-daily.js"></script><link rel="stylesheet" href="workspace-daily.css"><script defer src="company-editor.js"></script><script defer src="strategy.js"></script><link rel="stylesheet" href="workspace-design.css"><link rel="stylesheet" href="workspace-search.css"><script defer src="workspace-search.js"></script><link rel="stylesheet" href="workspace-shell.css"><script defer src="workspace-shell.js"></script><script defer src="workspace-favorites.js"></script><script defer src="workspace-bulk.js"></script></head>
<body class="workspace-design"><a class="st-skip" href="strategy#workspace">Skip to command center</a>
{workspace_shell.header(prof, live, 'Command Center')}
<div class="st-environment"><button type="button" id="help-launcher" aria-haspopup="dialog" aria-controls="help-panel">Help &amp; feedback</button><span id="connection-label">{label}</span><span id="freshness">Connecting to CRM…</span><button id="refresh">Refresh records</button></div>

<div class="st-shell">{workspace_shell.navigation(prof, 'strategy')}
<main id="workspace" tabindex="-1"><div id="notice" role="status" aria-live="polite"></div><div id="workspace-content"><div class="st-empty">Loading your business…</div></div><div id="coverage"></div></main></div>
<dialog id="editor"><form id="edit-form"><div class="st-dialog-head"><div><small id="editor-kicker">Plan the next move</small><h2 id="editor-title"></h2></div><button type="button" data-close aria-label="Close form">×</button></div><div id="fields"></div><p id="form-error" role="alert"></p><div class="st-dialog-actions"><button type="button" data-close>Cancel</button><button type="submit" class="st-primary" id="save">Save to CRM</button></div></form></dialog>
<dialog id="company-editor" aria-labelledby="company-action-title"><form id="company-action-form"><div class="st-dialog-head"><div><small id="company-action-context"></small><h2 id="company-action-title"></h2></div><button type="button" data-company-close aria-label="Close company action">×</button></div><div id="company-action-fields"></div><p id="company-action-error" role="alert"></p><div class="st-dialog-actions"><button type="button" data-company-close>Close</button><button type="submit" class="st-primary" id="company-action-save">Save to CRM</button></div></form></dialog>
<dialog id="payment-editor" aria-labelledby="payment-title"><form id="payment-form"><div class="st-dialog-head"><h2 id="payment-title">Record payment</h2><button type="button" data-payment-close aria-label="Close payment form">×</button></div><div id="payment-fields"></div><p id="payment-error" role="alert"></p><div class="st-dialog-actions"><button type="button" data-payment-close>Cancel</button><button type="submit" id="payment-save" class="st-primary">Save payment</button></div></form></dialog>
<dialog id="help-panel" class="st-help-panel" aria-labelledby="help-title"><div class="st-help-header"><div><small>Command Center</small><h2 id="help-title">A little help, right here.</h2></div><button type="button" id="help-close" aria-label="Close help">×</button></div><div class="st-help-tabs" role="tablist" aria-label="Help and feedback"><button type="button" id="help-guide-tab" role="tab" aria-selected="true" aria-controls="help-guide">Find your way</button><button type="button" id="help-feedback-tab" role="tab" aria-selected="false" aria-controls="help-feedback" tabindex="-1">Share feedback</button></div><section id="help-guide" role="tabpanel" aria-labelledby="help-guide-tab"></section><section id="help-feedback" role="tabpanel" aria-labelledby="help-feedback-tab" hidden></section></dialog>
<noscript>Enable JavaScript to load and edit the CRM strategy views.</noscript></body></html>'''
    return version_assets(page)
