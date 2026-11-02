"""MCP over Streamable HTTP, stateless, both protocol eras on one endpoint (2026-10-11).

The Model Context Protocol is how Claude (claude.ai, Claude Desktop, Claude Code) and Codex call a
server's tools. This file is the wire format only: JSON-RPC in, JSON-RPC out, no sessions, no
server-to-client stream. It is a port of the Social Studio's `lib/mcp-protocol.ts` (the connector
that went live 2026-10-10), so the two products answer the same way.

Framework-free on purpose. `handle(method, headers, body, server)` returns (status, headers, body)
and knows nothing about http.server or aiohttp, so the CRM mounts it in `server.py` and the
receptionist can mount the same file in its aiohttp app. Tools are injected: `server` is a
`Server` whose `call(name, args)` does the work and checks scopes.

Eras:
  modern  2026-07-28   every request carries its version in `_meta` and mirrored headers; no
                       initialize; unknown version -> -32022; header mismatch -> -32020.
  legacy  2025-03-26 .. 2025-11-25   the `initialize` handshake. Claude still uses this.

Nothing here reads a database or a secret. Authentication happens before `handle` is called.
"""
import base64
import json
from dataclasses import dataclass, field

MODERN_VERSIONS = ("2026-07-28",)
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
SUPPORTED_VERSIONS = MODERN_VERSIONS + LEGACY_VERSIONS
META = "io.modelcontextprotocol/"
SCOPE_META = "com.cta-engine/scope"
# 2026-07-28 list results carry a cache hint; Claude Code rejects a tools/list without one.
# "private": the list sits behind a key, so no shared cache may keep it.
LIST_CACHE = {"ttlMs": 300_000, "cacheScope": "private"}
MAX_BODY_BYTES = 2_100_000
HEADERS = {"Content-Type": "application/json", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


@dataclass
class Tool:
    name: str
    title: str
    description: str
    input_schema: dict
    scope: str
    annotations: dict = field(default_factory=dict)

    def listed(self):
        return {"name": self.name, "title": self.title, "description": self.description,
                "inputSchema": self.input_schema, "annotations": self.annotations,
                "_meta": {SCOPE_META: self.scope}}


@dataclass
class Server:
    """What one endpoint serves. `call(name, args)` returns a result dict (see `tool_result`) or
    raises `ToolRefused`."""
    info: dict
    instructions: str
    tools: list
    call: object

    def tool(self, name):
        return next((t for t in self.tools if t.name == name), None)


class ToolRefused(Exception):
    """A call that must come back as an HTTP status, not a tool error: 403 insufficient_scope,
    429 too many requests."""

    def __init__(self, status, error, message):
        super().__init__(message)
        self.status, self.error = status, error


def tool_result(value, is_error=False):
    """The MCP shape: text for every client, structuredContent for the ones that read it."""
    structured = value if isinstance(value, dict) else {"value": value}
    out = {"content": [{"type": "text", "text": json.dumps(value, indent=1, ensure_ascii=False)}],
           "structuredContent": structured}
    if is_error:
        out["isError"] = True
    return out


def _json(body, status=200):
    return status, dict(HEADERS), json.dumps(body, ensure_ascii=False).encode("utf-8")


def _failure(rid, code, message, status, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    body = {"jsonrpc": "2.0", "error": err}
    if rid is not None:
        body["id"] = rid
    return _json(body, status)


def _decode_header(value):
    """A 2026-07-28 Mcp-Name header may be base64 (`=?base64?...?=`)."""
    if value.startswith("=?base64?") and value.endswith("?="):
        try:
            return base64.b64decode(value[9:-2]).decode("utf-8")
        except Exception:  # noqa: BLE001 - a bad encoding is a mismatch, not a crash
            return None
    return value


def _header(headers, name):
    name = name.lower()
    for k, v in headers.items():
        if k.lower() == name:
            return v
    return None


def handle(method, headers, body, server, origin_allowed=()):
    """One HTTP request in, (status, headers, body bytes) out.

    `headers` is any mapping with `.items()` (http.server's message object works). A request with
    an Origin header is a browser; refused unless it is one of `origin_allowed` (programs send none).
    """
    origin = _header(headers, "origin")
    if origin and origin not in origin_allowed:
        return _failure(None, -32600, "Origin not allowed.", 403)
    if method != "POST":
        return 405, {"Allow": "POST", "Cache-Control": "no-store"}, b""
    ctype = (_header(headers, "content-type") or "").lower()
    if not ctype.startswith("application/json"):
        return _failure(None, -32700, "Send JSON-RPC as application/json.", 415)
    if len(body) > MAX_BODY_BYTES:
        return _failure(None, -32600, "Request too large.", 413)
    try:
        message = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return _failure(None, -32700, "Parse error.", 400)
    if isinstance(message, list):
        return _failure(None, -32600, "Batches are not supported.", 400)
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return _failure(None, -32600, "Invalid request.", 400)
    is_notification = "id" not in message
    rid = message.get("id")
    if not is_notification and (isinstance(rid, bool) or not isinstance(rid, (str, int))):
        return _failure(None, -32600, "Request id must be a string or number.", 400)
    if "result" in message or "error" in message:
        return _failure(None, -32600, "Clients do not send responses here.", 400)
    if is_notification:
        return 202, {"Cache-Control": "no-store"}, b""

    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    header = _header(headers, "mcp-protocol-version")
    body_version = meta.get(META + "protocolVersion")
    modern = isinstance(body_version, str) or (header is not None and header not in LEGACY_VERSIONS)

    if modern:
        if header is None:
            return _failure(rid, -32020, "Header mismatch: MCP-Protocol-Version header is missing.", 400)
        if not isinstance(body_version, str):
            return _failure(rid, -32602, f'Invalid params: _meta["{META}protocolVersion"] is required.', 400)
        if header != body_version:
            return _failure(rid, -32020, f"Header mismatch: MCP-Protocol-Version header value '{header}' "
                                         f"does not match body value '{body_version}'.", 400)
        if body_version not in MODERN_VERSIONS:
            return _failure(rid, -32022, "Unsupported protocol version", 400,
                            {"supported": list(SUPPORTED_VERSIONS), "requested": body_version})
        if _header(headers, "mcp-method") != message["method"]:
            return _failure(rid, -32020, f"Header mismatch: Mcp-Method header does not match body value "
                                         f"'{message['method']}'.", 400)
        if message["method"] in ("tools/call", "resources/read", "prompts/get"):
            raw = _header(headers, "mcp-name")
            body_name = params.get("name", params.get("uri"))
            if raw is None or _decode_header(raw) != body_name:
                return _failure(rid, -32020, "Header mismatch: Mcp-Name header does not match the body.", 400)
        if not isinstance(meta.get(META + "clientCapabilities"), dict):
            return _failure(rid, -32602, f'Invalid params: _meta["{META}clientCapabilities"] is required.', 400)
        if message["method"] == "initialize":
            return _failure(rid, -32601, "Method not found: initialize is not used with 2026-07-28.", 404)
        return _dispatch(message, params, server, True)

    if header is not None and header not in LEGACY_VERSIONS:
        return _failure(rid, -32600, f"Unsupported protocol version: {header}. Supported: "
                                     f"{', '.join(SUPPORTED_VERSIONS)}.", 400)
    if message["method"] == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
        return _json({"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": server.info, "instructions": server.instructions}})
    return _dispatch(message, params, server, False)


def _dispatch(message, params, server, modern):
    rid = message["id"]

    def done(result):
        if modern:
            result = {"resultType": "complete", "_meta": {META + "serverInfo": server.info}, **result}
        return _json({"jsonrpc": "2.0", "id": rid, "result": result})

    method = message["method"]
    if method == "ping":
        return done({})
    if method == "server/discover":
        out = {"supportedVersions": list(SUPPORTED_VERSIONS), "capabilities": {"tools": {"listChanged": False}},
               "instructions": server.instructions}
        if not modern:
            out["serverInfo"] = server.info
        return done(out)
    if method == "tools/list":
        listed = {"tools": [t.listed() for t in server.tools]}
        return done({**listed, **LIST_CACHE} if modern else listed)
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments", {})
        if not isinstance(name, str) or server.tool(name) is None:
            return _failure(rid, -32602, f"Unknown tool: {name}", 400 if modern else 200)
        if not isinstance(args, dict):
            return _failure(rid, -32602, "Tool arguments must be an object.", 400 if modern else 200)
        try:
            return done(server.call(name, args))
        except ToolRefused as e:
            return _json({"error": e.error, "error_description": str(e)}, e.status)
    return _failure(rid, -32601, f"Method not found: {method}", 404 if modern else 200)
