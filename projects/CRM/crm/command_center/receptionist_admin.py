"""CRM admin boundary for demo control. No voice credential is sent to a browser or MCP client."""
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import crm_store


def authorize(store, prof, person):
    store.authorize(prof, person)
    if not store.is_admin(prof, person):
        raise crm_store.CRMError("Receptionist demos are available only to CRM administrators", 403)
    if not (prof.get("crm") or {}).get("receptionist", {}).get("enabled"):
        raise crm_store.CRMError("Receptionist demo control is not configured for this CRM", 404)


def service(prof, action=None):
    cfg = prof["crm"]["receptionist"]
    # Endpoint is fixed loopback. A prospect URL can never become an internal control endpoint.
    env_file = Path(cfg.get("env_file", "/srv/cta/receptionist.env"))
    try:
        token = next(line.split("=", 1)[1].strip() for line in env_file.read_text().splitlines()
                     if line.startswith("RECEPTIONIST_ADMIN_TOKEN="))
        if not token:
            raise ValueError()
        req = Request("http://127.0.0.1:8796/admin/demos/" + ("control" if action else "state"),
                      data=json.dumps(action).encode() if action else None,
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with urlopen(req, timeout=12) as response:
            return json.load(response)
    except HTTPError as exc:
        message = exc.read(1500).decode("utf-8", "replace")
        raise crm_store.CRMError("Receptionist: " + message, exc.code if exc.code in (400, 403, 409) else 503) from None
    except (OSError, ValueError, StopIteration, URLError):
        raise crm_store.CRMError("Receptionist service is unavailable. Retry the same request.", 503) from None


def gate(store, prof, person, path):
    """Mark the profile so the menu shows the selector to admins, and answer the two admin GET
    routes. Returns (prof, None) or (prof, (kind, body)) with kind "json" or "html"."""
    enabled = bool((prof.get("crm") or {}).get("receptionist", {}).get("enabled"))
    admin = not getattr(store, "readonly", False) and store.is_admin(prof, person)
    prof = {**prof, "_receptionist_admin": enabled and admin}
    if path not in ("/admin/receptionist", "/api/crm/receptionist"):
        return prof, None
    authorize(store, prof, person)
    if not admin:
        raise crm_store.CRMError("Use the live admin CRM", 403)
    if path == "/api/crm/receptionist":
        return prof, ("json", json.dumps(read(store, prof, person)))
    return prof, ("html", render(prof))


def read(store, prof, person):
    authorize(store, prof, person)
    return service(prof)


def change(store, prof, person, data, request_id):
    authorize(store, prof, person)
    if not isinstance(data, dict) or data.get("action") not in ("select", "create", "review"):
        raise crm_store.CRMError("Unknown receptionist action")
    allowed = {"action", "profile", "minutes", "name", "industry", "website", "facts_text", "schedule_json", "timezone", "reviewed"}
    if set(data) - allowed:
        raise crm_store.CRMError("Unknown receptionist control field")
    return service(prof, dict(data, request_id=request_id))


def render(prof):
    page = (Path(__file__).parent / "www/receptionist-admin.html").read_text(encoding="utf-8")
    prefix = (prof.get("crm") or {}).get("path_prefix", "/")
    import re
    if not re.fullmatch(r"/(?:[a-zA-Z0-9_-]+/)*", prefix):
        raise crm_store.CRMError("Invalid CRM path prefix")
    return page.replace("__CRM_BASE__", prefix)
