"""The page shell — nav, head, and the view frame.

No business name, colour or typeface is written here. The shell takes the name from the
profile and the look from two stylesheets:

    /brand.css   the business's own design system, named by the profile
    /cc.css      this engine's layout, written entirely in that system's tokens

That split is what makes "inherit the brand, invent no third visual system" mechanical
rather than a promise. The engine ships no colour of its own, so it cannot drift from
the brand it is drawing.
"""

from html import escape

from . import VERSION
from .render import render_tree, stat_strip

FONTS = ("https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700"
         "&family=Instrument+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap")


def esc(s):
    return escape(str(s if s is not None else ""), quote=True)


def _nav(state, current):
    items = []
    for vid in state["order"]:
        v = state["views"][vid]
        cls = "cc-tab cc-on" if vid == current else "cc-tab"
        items.append(f'<a class="{cls}" href="/{esc(vid)}">{esc(v["title"])}</a>')
    return f'<nav class="cc-tabs">{"".join(items)}</nav>'


CANVAS_SHELL = """<div class="cc-board" id="cc-board" data-view="{vid}">
  <aside class="cc-spine" id="cc-spine" aria-label="Departments"></aside>
  <div class="cc-canvas-wrap">
    <svg class="cc-canvas" id="cc-canvas" role="img" aria-label="Board">
      <g id="cc-viewport"><g id="cc-edges"></g><g id="cc-nodes"></g></g>
    </svg>
    <div class="cc-hud" id="cc-hud" hidden><b id="cc-hud-t"></b><span id="cc-hud-s"></span></div>
    <div class="cc-filter" id="cc-filter" hidden></div>
    <p class="cc-empty" id="cc-empty">Pick a department on the left.</p>
    <div class="cc-zoom" id="cc-zoom">
      <button type="button" data-z="out" aria-label="Zoom out">&minus;</button>
      <button type="button" data-z="fit" aria-label="Fit to view">fit</button>
      <button type="button" data-z="in" aria-label="Zoom in">+</button>
    </div>
  </div>
  <aside class="cc-panel" id="cc-panel" aria-live="polite"></aside>
</div>
<noscript><p class="cc-note">The board is drawn in the browser. Every other tab works
without JavaScript.</p></noscript>"""


def view_html(state, view_id, open_to=1):
    """The part of the page that changes. Returned on its own by /api/view so a poll can
    swap it without a reload."""
    v = state["views"][view_id]
    bits = []
    if v.get("stats"):
        bits.append(stat_strip(v["stats"]))
    for n in v.get("notes") or []:
        bits.append(f'<p class="cc-note">{esc(n)}</p>')
    # A view may declare itself a canvas: the shell ships, and the nodes arrive as JSON
    # from /api/state, which the page already polls. The server stays the one place that
    # knows what the tree IS — the browser only decides where to put it.
    if v.get("canvas"):
        bits.append(CANVAS_SHELL.format(vid=esc(view_id)))
    elif v.get("nodes"):
        bits.append(f'<div class="cc-tree">{render_tree(v["nodes"], open_to)}</div>')
    else:
        bits.append('<p class="cc-note">Nothing here yet.</p>')
    return "".join(bits)


def _signed_in(state):
    """Who this page was built for. Every page is one person's view, so it says whose."""
    p = state.get("person")
    if not p:
        return ""
    return f'<span class="cc-sub mono">signed in as {esc(p.get("name") or p.get("id"))}</span>'


def render(state, view_id, open_to=1):
    v = state["views"][view_id]
    title = f'{state["business_name"]} — {v["title"]}'
    # The canvas ships its own stylesheet and renderer, and only on the view
    # that needs them — every other tab stays plain HTML that works without JS.
    board_css = '<link rel="stylesheet" href="/board.css">' if v.get("canvas") else ""
    board_js = '<script src="/board.js" defer></script>' if v.get("canvas") else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{esc(title)}</title>
<link rel="stylesheet" href="{FONTS}">
<link rel="stylesheet" href="/brand.css">
<link rel="stylesheet" href="/cc.css">
{board_css}
</head>
<body class="command-center">
<header class="cc-head">
  <div class="cc-head-in">
    <div class="cc-id">
      <span class="cc-mark" aria-hidden="true"></span>
      <span class="cc-name">{esc(state["business_name"])}</span>
      <span class="cc-sub mono">command center · v{esc(VERSION)}</span>
      {_signed_in(state)}
    </div>
    {_nav(state, view_id)}
  </div>
</header>
<main class="cc-main" id="cc-main">
  <div class="cc-title">
    <h1>{esc(v["title"])}</h1>
    <p class="cc-blurb">{esc(v["blurb"])}</p>
  </div>
  <div id="cc-view">{view_html(state, view_id, open_to)}</div>
</main>
<footer class="cc-foot">
  <span class="mono">{esc(state["business_id"])} · built {esc(state["built_at"])} · engine {esc(VERSION)}</span>
  <span class="cc-live mono" id="cc-live" data-view="{esc(view_id)}" data-etag="{esc(state["etag"])}">live</span>
</footer>
<script src="/cc.js" defer></script>
{board_js}
</body>
</html>
"""
