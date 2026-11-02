"""Outstand behind the posting interface (2026-10-03). Why Outstand: docs/content/posting-vendor-decision-2026-10-03.md.

    POST   /v1/posts/          {content, accounts, containers: [{content, media: [{url, filename}]}], scheduledAt}
    GET    /v1/posts/{id}      per-account status: pending, published, failed, deleted, with an `error` reason
    DELETE /v1/posts/{id}

Every call has an error path (architecture R4). A 429 waits for Retry-After (or backs off) and
retries twice; a 5xx or a dropped connection retries twice; then the verb raises PostingError.
401 and 403 are a missing, expired or revoked key; 400 and 422 are a rejected post (media, channel,
a time more than 30 days out). The response's id field is read from `id`, `post.id` or `data.id`
until the first live call confirms which one Outstand uses.
"""
import json
import time
import urllib.error
import urllib.request
from pathlib import PurePosixPath
from urllib.parse import urlparse

from .posting import Poster, PostingError

API = "https://api.outstand.so"


class OutstandPoster(Poster):
    name = "Outstand"

    def __init__(self, token, api=API, retries=2, sleep=time.sleep, timeout=30):
        if not token:
            raise PostingError("auth", "No Outstand key is set. Put the API key in the variable the profile names")
        self.token, self.api = token, api.rstrip("/")
        self.retries, self.sleep, self.timeout = retries, sleep, timeout

    def call(self, method, path, body=None, idempotent=True):
        """`idempotent=False` (a create) never retries after a 5xx or a dropped connection: the
        post may already be out, and a second send would double it. Only a 429 retries, because
        a rate-limited request was refused, not run."""
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(self.api + path, data=data, method=method, headers={
                "Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                text = e.read().decode("utf-8", "replace")[:2000]
                if e.code in (401, 403):
                    raise PostingError("auth", "Outstand refused the key: it is wrong, expired or revoked", text) from None
                if e.code == 404:
                    raise PostingError("not_found", "Outstand has no such post", text) from None
                if e.code in (400, 413, 415, 422):
                    raise PostingError("invalid", "Outstand rejected the post: " + (_message(text) or f"HTTP {e.code}"),
                                       text) from None
                if e.code == 429 or e.code >= 500:
                    if e.code >= 500 and not idempotent:
                        raise PostingError("unavailable", f"Outstand answered {e.code}. The post may or may not have "
                                           "gone out: check the channel before sending it again", text) from None
                    if attempt < self.retries:
                        after = e.headers.get("Retry-After") if e.headers else None
                        self.sleep(int(after) if after and after.isdigit() else 2 ** attempt)
                        continue
                    if e.code == 429:
                        raise PostingError("rate_limit", "Outstand's rate limit held after retries. Try again later",
                                           text) from None
                    raise PostingError("unavailable", f"Outstand answered {e.code} after retries", text) from None
                raise PostingError("invalid", f"Outstand answered {e.code}", text) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if not idempotent:
                    raise PostingError("unavailable", "Lost the connection to Outstand mid-send. The post may or may "
                                       "not have gone out: check the channel before sending it again", repr(e)) from None
                if attempt < self.retries:
                    self.sleep(2 ** attempt)
                    continue
                raise PostingError("unavailable", "Could not reach Outstand", repr(e)) from None
            except ValueError as e:
                raise PostingError("unavailable", "Outstand sent an answer we could not read", repr(e)) from None

    def post(self, item):
        body = {"content": item["text"], "accounts": list(item["accounts"])}
        if item.get("media"):
            body["containers"] = [{"content": item["text"], "media": [
                {"url": m["url"], "filename": m.get("filename") or PurePosixPath(urlparse(m["url"]).path).name}
                for m in item["media"]]}]
        if item.get("scheduled_at"):
            body["scheduledAt"] = item["scheduled_at"]
        out = self.call("POST", "/v1/posts/", body, idempotent=False)
        pid = out.get("id") or (out.get("post") or {}).get("id") or (out.get("data") or {}).get("id")
        if not pid:
            raise PostingError("unavailable", "Outstand accepted the post but sent no id. Check the dashboard "
                                              "before sending it again", json.dumps(out)[:2000])
        return str(pid)

    def status(self, post_id):
        out = self.call("GET", f"/v1/posts/{post_id}")
        post = out.get("post") or out.get("data") or out
        rows = post.get("accounts") or post.get("socialAccounts") or post.get("results") or []
        channels = [{"account": r.get("id") or r.get("accountId") or r.get("username") or "",
                     "state": (r.get("status") or "pending").lower(), "error": r.get("error") or "",
                     "url": r.get("url") or r.get("platformPostUrl") or ""} for r in rows]
        states = {c["state"] for c in channels} or {(post.get("status") or "pending").lower()}
        if "failed" in states:
            state = "failed"   # one failed channel is a failure the owner must see, even if others went out
        elif states == {"published"}:
            state = "published"
        elif states == {"deleted"}:
            state = "deleted"
        else:
            state = "pending"
        error = next((c["error"] for c in channels if c["error"]), post.get("error") or "")
        return {"state": state, "channels": channels, "error": error}

    def delete(self, post_id):
        self.call("DELETE", f"/v1/posts/{post_id}")


def _message(text):
    try:
        body = json.loads(text)
        return str(body.get("message") or body.get("error") or "")[:300]
    except (ValueError, AttributeError):
        return text[:300]
