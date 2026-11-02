"""A business's own look, as the profile names it: its logo (crm.logo) and its font and theme
stylesheets (crm.font_stylesheet, crm.theme_stylesheet). Files inside the instance root only.
The engine names no brand file, which is what lets a second business look like itself."""
from pathlib import Path

from . import profiles

LOGO_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml",
              ".webp": "image/webp"}


def file_in_root(root, name, suffixes):
    root = Path(root).resolve()
    f = (root / name).resolve() if isinstance(name, str) and name else None
    if f is None or root not in f.parents or f.suffix.lower() not in suffixes or not f.is_file():
        return None
    return f


def stylesheet(root, prof, key):
    """The font or theme stylesheet's bytes, or nothing."""
    f = file_in_root(root, (prof.get("crm") or {}).get(key), (".css",))
    return f.read_bytes() if f else b""


def serve(handler, path):
    """GET /brand-logo: public like the sign on the door, because the kiosk shows it before sign-in.
    True when answered; any other path is not ours."""
    if path != "/brand-logo":
        return False
    try:
        prof = profiles.load(handler.cc.business, handler.cc.root)
    except Exception:  # noqa: BLE001
        prof = {}
    f = file_in_root(handler.cc.root, (prof.get("crm") or {}).get("logo"), LOGO_TYPES)
    if f is None:
        return handler._send(404, "not found", "text/plain; charset=utf-8") or True
    return handler._send(200, f.read_bytes(), LOGO_TYPES[f.suffix.lower()], {"Cache-Control": "public, max-age=3600"}) or True


def serve_stylesheet(handler, prof, key="font_stylesheet"):
    """The business's typeface (crm.font_stylesheet) or its look (crm.theme_stylesheet) for the CRM,
    as crm-font.css and crm-theme.css. Missing or outside the root: an empty stylesheet, so the page
    falls back to the engine's own look."""
    f = file_in_root(handler.cc.root, (prof.get("crm") or {}).get(key), (".css",))
    css = "text/css; charset=utf-8"
    if f is None:
        return handler._send(200, "/* no CRM font configured */", css, {"Cache-Control": "no-cache"})
    # no-cache, not a day: the links carry no version, so a changed look must reach a returning visitor
    return handler._send(200, f.read_bytes(), css, {"Cache-Control": "no-cache"})
