"""Content: the business's social posts and scheduled articles, read-only, in the CRM.

WHY
    A client who has content made for them should see it where they already work: their CRM.
    Sam, 2026-10-06: Pete should reach the Content Studio's output inside his CRM, without a
    separate Studio login.

HOW
    The profile turns it on: `crm.content = {"feed": "data/content/feed.json"}` (a path inside
    the CRM folder). The feed is a SNAPSHOT exported by tools/content_feed.py from the business's
    own content state (one writer: its content-state script). This module only reads it. Posts
    are approved, scheduled or marked posted where the state lives, never from the CRM, so there
    is still one writer. Images are served from the feed's folder by post id, never by a path
    from the request. Every signed-in member who may read the CRM sees the same content.
"""
import json
import re
from pathlib import Path

SCHEMA = "content-feed/1"
ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
POST_STATUS = {"draft", "approved", "posted"}
ARTICLE_STATUS = {"draft", "approved", "published"}


class ContentUnavailable(Exception):
    pass


def configured(prof):
    spec = (prof.get("crm") or {}).get("content")
    return isinstance(spec, dict) and isinstance(spec.get("feed"), str) and bool(spec["feed"])


def _feed_path(prof, root):
    if not configured(prof):
        raise ContentUnavailable("Content is not set up for this business")
    base = Path(root).resolve()
    f = (base / prof["crm"]["content"]["feed"]).resolve()
    if base not in f.parents or f.suffix != ".json":
        raise ContentUnavailable("The content feed must be a .json file inside the CRM folder")
    if not f.is_file():
        raise ContentUnavailable("No content has been exported to this CRM yet")
    return f


def _feed(prof, root):
    f = _feed_path(prof, root)
    try:
        feed = json.loads(f.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ContentUnavailable(f"The content feed is not valid JSON: {e}")
    if feed.get("schema") != SCHEMA:
        raise ContentUnavailable(f"The content feed is not {SCHEMA}")
    if feed.get("business") != prof.get("business_id"):
        raise ContentUnavailable("The content feed belongs to another business")
    return f, feed


def snapshot(prof, root):
    """The feed for the browser: posts (newest batch first as exported), articles, counts."""
    _, feed = _feed(prof, root)
    posts = []
    for p in feed.get("posts") or []:
        if not ID.fullmatch(str(p.get("id", ""))) or p.get("status") not in POST_STATUS:
            raise ContentUnavailable(f"A post in the feed has a bad id or status: {p.get('id')!r}")
        posts.append({
            "id": p["id"], "status": p["status"], "channel": p.get("channel", ""),
            "title": p.get("title", ""), "solution": p.get("solution", ""), "caption": p.get("caption", ""),
            "article_title": p.get("article_title", ""), "article_url": p.get("article_url", ""),
            "approval": p.get("approval"), "posted": p.get("posted"),
            "image": f"api/crm/content/image?post={p['id']}" if p.get("image") else None,
        })
    articles = []
    for a in feed.get("articles") or []:
        if a.get("status") not in ARTICLE_STATUS:
            raise ContentUnavailable(f"An article in the feed has a bad status: {a.get('slug')!r}")
        articles.append({k: a.get(k) for k in ("slug", "title", "url", "status", "publish_on", "approval", "published_at")})
    count = lambda s: sum(1 for p in posts if p["status"] == s)
    return {"exported_at": feed.get("exported_at"), "posts": posts, "articles": articles,
            "counts": {"ready": count("approved"), "posted": count("posted"), "draft": count("draft")},
            "note": (prof["crm"]["content"].get("note") or "")}


def image(prof, root, post_id):
    """(bytes, media type) of one post's image, found through the feed by id."""
    if not ID.fullmatch(post_id or ""):
        raise ContentUnavailable("Unknown post")
    f, feed = _feed(prof, root)
    entry = next((p for p in feed.get("posts") or [] if p.get("id") == post_id), None)
    if not entry or not entry.get("image"):
        raise ContentUnavailable("Unknown post")
    folder = f.parent.resolve()
    img = (folder / entry["image"]).resolve()
    if folder not in img.parents or img.suffix.lower() != ".png" or not img.is_file():
        raise ContentUnavailable("The post's image is missing")
    return img.read_bytes(), "image/png"
