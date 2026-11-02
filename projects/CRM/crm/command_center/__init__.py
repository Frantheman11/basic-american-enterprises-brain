"""The Command Center engine — the view layer over the files.

A second brain that only exists as files is a system its owner cannot see. This package
renders those files. It is business-agnostic: it takes a `business_id`, loads that
business's profile, and draws whatever the profile points at.

    engine    this package. Reusable machinery, lives in the CTA repo.
    profile   data/command-center/<business_id>.json — the facts of one business.
    instance  one running server, reading one profile and that business's live files.

The engine is COPIED into a client repo and version-stamped (see VERSION), never
referenced across repos — a live reference would need the client's runner to hold a
token for our repo, and that muddies the IP split.

    python command_center/server.py --business cta --port 8780

Brief: docs/crm/command-center-engine-brief-2026-09-20.md
"""

from pathlib import Path

VERSION = (Path(__file__).resolve().parent / "VERSION").read_text(encoding="utf-8").strip()

__all__ = ["VERSION"]
