"""Shared navigation and chrome for the two views of one CRM workspace."""
import html

SVG = ('<svg class="ws-nav-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" '
       'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{}</svg>')
ICONS = {
    'today': '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    'companies': '<rect x="3.5" y="3.5" width="7" height="7" rx="1"/><rect x="13.5" y="3.5" width="7" height="7" rx="1"/>'
                 '<rect x="3.5" y="13.5" width="7" height="7" rx="1"/><rect x="13.5" y="13.5" width="7" height="7" rx="1"/>',
    'contacts': '<circle cx="12" cy="8" r="3.5"/><path d="M5 20c0-3.5 3-5.5 7-5.5s7 2 7 5.5"/>',
    'deals': '<path d="M4 5h16l-6 7.5V19l-4-2v-4.5z"/>',
    'tasks': '<path d="M10 6h10M10 12h10M10 18h10"/><path d="M4 6l1.5 1.5L8 5M4 12l1.5 1.5L8 11M4 18l1.5 1.5L8 17"/>',
    'technology': '<path d="M12 4l8 4.5-8 4.5-8-4.5z"/><path d="M4 13.5l8 4.5 8-4.5"/>',
    'insights': '<path d="M5 19v-8M11 19V5M17 19v-6M3 19h18"/>',
    'reports': '<path d="M6 3h8l5 5v13H6z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>',
    'research': '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
    'calls': '<path d="M6 4h3.5l1.5 4.5-2 1.3a10 10 0 0 0 5.2 5.2l1.3-2L20 14.5V18a2 2 0 0 1-2 2A14 14 0 0 1 4 6a2 2 0 0 1 2-2z"/>',
    'content': '<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M3.5 15.5l5-4.5 4 3.5 3-2.5 5 4"/>',
    'leaderboard': '<path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.2 1 5.9-5.2-2.8-5.2 2.8 1-5.9L3.5 9.7l5.9-.8z"/>',
    'departments': '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M9.5 4.5v15M15 4.5v15"/>',
    'decisions': '<rect x="3.5" y="3.5" width="17" height="17" rx="2"/><path d="M8 12.5l3 3 5.5-6.5"/>',
    'payments': '<path d="M12 3v18M16.5 7.5H10a2.75 2.75 0 0 0 0 5.5h4a2.75 2.75 0 0 1 0 5.5H7"/>',
    'gym': '<path d="M3 9v6M6 6.5v11M18 6.5v11M21 9v6M6 12h12"/>',
    'connections': '<path d="M10.5 13.5a3.5 3.5 0 0 0 5 0l2.5-2.5a3.5 3.5 0 0 0-5-5l-1 1"/>'
                   '<path d="M13.5 10.5a3.5 3.5 0 0 0-5 0L6 13a3.5 3.5 0 0 0 5 5l1-1"/>',
}
SEARCH = ('<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" '
          'stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/></svg>')
PERSON = ('<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" '
          'stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="8" r="3.5"/><path d="M5 20c0-3.5 3-5.5 7-5.5s7 2 7 5.5"/></svg>')


def icon(key):
    return SVG.format(ICONS[key])


def portfolio_tabs(prof):
    """One tab per business in its color, a bar in the active color, and "+" for an owner.
    With two or more businesses to see, "All businesses" (the roll-up) comes first, uncolored.
    Only a portfolio server with two or more businesses sets `_portfolio` (portfolio.py)."""
    p = prof.get('_portfolio')
    if not p:
        return ''
    esc = lambda s: html.escape(s, quote=True)
    home = ('<a class="ws-biz-tab ws-biz-all" href="../../"' + (' aria-current="page"' if not p['active'] else '')
            + '>All businesses</a>') if p.get('home') else ''
    tabs = home + ''.join(
        f'<a class="ws-biz-tab" href="../{esc(t["id"])}/" data-business="{esc(t["id"])}" style="--tab:{esc(t["color"])}"'
        + (' aria-current="page"' if t['id'] == p['active'] else '')
        + f'><span class="ws-biz-dot" aria-hidden="true"></span>{esc(t["label"])}</a>' for t in p['tabs'])
    add = dialog = ''
    if p.get('can_add'):
        add = ('<button type="button" class="ws-biz-add" aria-haspopup="dialog" aria-controls="portfolio-add" '
               'aria-label="Add a business" title="Add a business">+</button>')
        sources = ''.join(f'<option value="{esc(t["id"])}"' + (' selected' if t['id'] == p['active'] else '')
                          + f'>{esc(t["label"])}</option>' for t in p['tabs'])
        swatches = ''.join(f'<label class="ws-biz-swatch" style="--tab:{esc(c)}"><input type="radio" name="color" value="{esc(c)}"'
                           + (' checked' if i == 0 else '') + f'><span>{esc(c)}</span></label>' for i, c in enumerate(p['palette']))
        dialog = (f'<dialog id="portfolio-add" class="ws-biz-dialog" aria-labelledby="portfolio-add-title"><form method="dialog" data-add-url="{esc(p["add_url"])}">'
                  '<h2 id="portfolio-add-title">Add a business</h2><p>The new business gets the same stages, task lists and people, '
                  'and an empty database. No records are copied.</p>'
                  '<label>Business name<input name="label" maxlength="40" required autocomplete="off"></label>'
                  f'<label>Copy the setup of<select name="source">{sources}</select></label>'
                  + (f'<fieldset><legend>Color</legend><div class="ws-biz-swatches">{swatches}</div></fieldset>' if swatches else '')
                  + '<p class="ws-biz-error" role="alert"></p><div class="ws-biz-actions"><button type="button" data-portfolio-cancel>Cancel</button>'
                  '<button type="submit" class="primary">Add business</button></div></form></dialog>')
    return (f'<style>body.workspace-design{{--biz:{esc(p["color"])}}}</style><link rel="stylesheet" href="portfolio.css">'
            '<script defer src="portfolio.js"></script>'
            f'<div class="ws-portfolio" data-business="{esc(p["active"])}" data-links-url="{esc(p.get("links_url", ""))}" data-export-url="{esc(p.get("export_url", ""))}"><div class="ws-biz-tabs" role="navigation" aria-label="Businesses">{tabs}{add}</div>'
            '<div class="ws-biz-bar" aria-hidden="true"></div></div>' + dialog)


def header(prof, live=False, subtitle='Workspace'):
    name = html.escape(prof['name'])
    subtitle = html.escape(subtitle)
    initials = html.escape(''.join(w[0] for w in prof['name'].split() if w[0].isalnum())[:3])
    search = '' if live else (f'<button type="button" id="workspace-search-launcher" aria-haspopup="dialog" aria-controls="workspace-search" '
                              f'aria-label="Search">{SEARCH}<span class="ws-label">Search</span> <kbd>Ctrl / ⌘ K</kbd></button>')
    return (portfolio_tabs(prof) + f'<header class="ws-header"><a class="ws-brand" href="./"><span class="ws-mark" aria-hidden="true">{initials}↗</span>'
            f'<span><strong>{name}</strong><small>{subtitle}</small></span></a><div class="ws-header-tools">{search}'
            '<button type="button" id="workspace-add-launcher" aria-haspopup="dialog" aria-controls="workspace-add" hidden>+ Add</button>'
            '<details class="ws-appearance ws-account"><summary aria-label="Account and appearance">'
            f'<span class="ws-avatar" aria-hidden="true">{PERSON}</span><span id="identity"></span></summary>'
            '<div><small>Appearance</small><div role="group" aria-label="Color theme">'
            '<button type="button" data-workspace-theme="light">Light</button><button type="button" data-workspace-theme="gray">Gray</button>'
            '<button type="button" data-workspace-theme="dark">Dark</button></div></div></details></div></header>')


def navigation(prof, engine):
    crm = prof.get('crm') or {}
    strategic = bool(crm.get('strategy'))

    def item(label, view, target, key):
        attr = ('data-view' if target == 'strategy' else 'data-nav') + f'="{view}"'
        content = f'{icon(key)}<span>{label}</span>'
        if target == engine:
            return f'<button type="button" {attr}>{content}</button>'
        url = ('strategy' if target == 'strategy' else './') + '?view=' + view
        return f'<a href="{url}">{content}</a>'
    primary = item('Today', 'overview' if engine == 'strategy' else 'today', engine, 'today')
    if strategic:
        primary += item('Companies', 'companies', 'strategy', 'companies')
    if (prof.get('gym') or {}).get('enabled') is True:  # the gym screens (gym_routes.py)
        primary += '<a href="gym">' + icon('gym') + '<span>Gym</span></a>'
    primary += item('Contacts', 'contacts', 'crm', 'contacts') + item('Pipeline', 'deals', 'crm', 'deals') + item('All work', 'tasks', 'crm', 'tasks')
    if strategic:
        primary += item('Systems', 'technology', 'strategy', 'technology') + item('Business overview', 'insights', 'strategy', 'insights')
    secondary = item('Reports', 'reports', 'crm', 'reports')
    for key, label in [('research', 'Research'), ('calls', 'Calls and chats'), ('content', 'Content'), ('leaderboard', 'Leaderboard')]:
        if crm.get(key):
            secondary += item(label, key, 'crm', key)
    if strategic:
        for key, label in [('departments', 'Departments'), ('decisions', 'Decisions'), ('payments', 'Payments'), ('connections', 'Connections')]:
            secondary += item(label, key, 'strategy', key)
    if prof.get('_receptionist_admin'):
        secondary += '<a href="admin/receptionist">' + icon('calls') + '<span>Admin · Receptionist demos</span></a>'
    return (f'<nav class="ws-nav" aria-label="Workspace"><div class="ws-nav-primary">{primary}</div><div id="workspace-favorites"></div>'
            f'<details class="ws-nav-more"><summary>More workspace tools</summary><div class="ws-nav-secondary">{secondary}</div></details></nav>')
