"""The brain check: proves this second brain is built right. Stdlib only.

    python tools/brain_check.py                    # factory stage: the structure is sound
    python tools/brain_check.py --stage installed  # setup finished: the brain has real content
    python tools/brain_check.py --write-index      # rebuild index.md, then check

Exit code 0 = pass, 1 = fail. GitHub runs it on every save (.github/workflows/brain-check.yml),
so a computer without Python still gets the check.

Part of the Second Brain Installer by Custom Tailored Agents. Version below.
"""

import argparse
import json
import re
import sys
from pathlib import Path

VERSION = "0.1.1"

REQUIRED_FILES = [
    "CLAUDE.md", "README.md", "START-HERE.md", "index.md", "log.md", "todo.md",
    "config/business.json", "setup/steps.json", "setup/progress.json",
    "memory/MEMORY.md",
    ".claude/skills/brain-setup/SKILL.md", ".claude/skills/file-it/SKILL.md",
    ".claude/skills/catch-me-up/SKILL.md", ".claude/skills/remember-this/SKILL.md",
    ".claude/skills/build-from-intake/SKILL.md",
]
REQUIRED_DIRS = ["raw", "raw/inbox", "wiki", "wiki/processes", "memory", "handoffs", "drafts"]
CORE_PAGES = ["business", "offers", "customers", "voice", "team"]
STUB = "<!-- stub -->"
WHO_PLACEHOLDER = "One short paragraph, written during setup."
CLAUDE_MD_MAX_LINES = 200   # Claude Code's own guidance for CLAUDE.md
MEMORY_INDEX_MAX_LINES = 150  # Claude reads only the start of a long index

TEXT_SUFFIXES = {".md", ".json", ".txt", ".csv", ".py", ".yml", ".yaml", ".html"}
PLACEHOLDER = re.compile(r"\{\{[a-z_]+\}\}")
SECRETS = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("API key", re.compile(r"\b(sk-[A-Za-z0-9_-]{20,}|sk_live_[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|xox[baprs]-[A-Za-z0-9-]{10,})")),
    ("password", re.compile(r"(?i)\b(password|passwd|pwd|passcode|pass|pw)\b\s*[:=]\s*(?![\[(]removed)\S{4,}")),
]
CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def luhn(digits):
    total, flip = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if flip:
            d = d * 2 - 9 if d > 4 else d * 2
        total, flip = total + d, not flip
    return total % 10 == 0


# The check reads the brain's own files only. A brain can share a repo with other work (a
# website, an app); that code is not the brain's to judge.
BRAIN_SCOPE = ["CLAUDE.md", "README.md", "START-HERE.md", "index.md", "log.md", "todo.md", "drafts",
               "config", "raw", "wiki", "memory", "handoffs", "setup", ".claude"]
PAGE_FOLDERS = [("Wiki", "wiki"), ("Companies", "wiki/companies"),
                ("Processes", "wiki/processes"), ("Handoffs", "handoffs")]


def text_files(root):
    for top in BRAIN_SCOPE:
        base = root / top
        found = [base] if base.is_file() else sorted(base.rglob("*")) if base.is_dir() else []
        for p in found:
            if not p.is_file() or p.suffix.lower() not in TEXT_SUFFIXES:
                continue
            yield p


def read(p):
    try:
        return p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def first_line_summary(text):
    """Title from the first heading; summary from the first plain sentence after it."""
    title, summary = "", ""
    body = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)
    for line in body.splitlines():
        s = line.strip()
        if not s or s == STUB or s.startswith("<!--"):
            continue
        if s.startswith("#") and not title:
            title = s.lstrip("#").strip()
            continue
        if title and not s.startswith(("#", "|", "-", "*", ">", "`")):
            summary = re.split(r"(?<=[.!?])\s", s)[0]
            break
    return title, summary[:140]


def build_index(root):
    lines = ["# Index", "", "One line per page. Rebuilt by `python tools/brain_check.py --write-index`.",
             "If Python is missing, edit it by hand, one line per page; the next rebuild replaces it.", ""]
    for label, folder in PAGE_FOLDERS:
        pages = sorted(p for p in (root / folder).glob("*.md") if p.name != "README.md")
        if not pages:
            continue
        lines += [f"## {label}", ""]
        for p in pages:
            title, summary = first_line_summary(read(p))
            stub = " (not written yet)" if STUB in read(p) else ""
            rel = p.relative_to(root).as_posix()
            lines.append(f"- [{title or p.stem}]({rel}){stub}" + (f" - {summary}" if summary and not stub else ""))
        lines.append("")
    lines += ["## Also", "",
              "- [Business facts](config/business.json) - phone, hours, prices, team. The one source",
              "- [Memory](memory/MEMORY.md) - decisions and corrections, one line each",
              "- [Log](log.md) - what changed, newest last", ""]
    return "\n".join(lines)


def check(root, stage):
    fails, warns = [], []

    for f in REQUIRED_FILES:
        if not (root / f).is_file():
            fails.append(f"missing file: {f}")
    for d in REQUIRED_DIRS:
        if not (root / d).is_dir():
            fails.append(f"missing folder: {d}/")
    if fails:
        return fails, warns  # the rest assumes the skeleton exists

    # Template placeholders left unfilled.
    for p in text_files(root):
        if p.parts[len(root.parts)] == "raw":
            continue
        m = PLACEHOLDER.search(read(p))
        if m:
            fails.append(f"unfilled placeholder {m.group(0)} in {p.relative_to(root).as_posix()}")

    # The profile parses and names the business.
    try:
        prof = json.loads(read(root / "config/business.json"))
    except json.JSONDecodeError as e:
        prof = {}
        fails.append(f"config/business.json is not valid JSON: {e}")
    if prof and not str(prof.get("name", "")).strip():
        fails.append("config/business.json has no business name")
    sources = (prof or {}).get("sources") or {}
    for key in ("what_we_do", "phone", "email", "address", "hours", "service_area", "services",
                "ideal_customer", "team"):
        if (prof or {}).get(key) and key not in sources:
            warns.append(f"config/business.json: '{key}' is filled but has no line in 'sources'")
    for c in (prof or {}).get("companies") or []:
        page = c.get("page", "")
        if not c.get("name") or not page or not (root / page).is_file():
            fails.append(f"company '{c.get('name', '?')}' needs a name and a page that exists (wiki/companies/...)")

    # Size limits: a long rules file or memory index makes Claude worse, not better.
    n = len(read(root / "CLAUDE.md").splitlines())
    if n > CLAUDE_MD_MAX_LINES:
        fails.append(f"CLAUDE.md is {n} lines; keep it under {CLAUDE_MD_MAX_LINES}. Move detail into wiki pages")
    n = len(read(root / "memory/MEMORY.md").splitlines())
    if n > MEMORY_INDEX_MAX_LINES:
        fails.append(f"memory/MEMORY.md is {n} lines; keep it under {MEMORY_INDEX_MAX_LINES}. Merge or delete old notes")

    # The index covers every page.
    index = read(root / "index.md")
    for _, folder in PAGE_FOLDERS:
        for p in (root / folder).glob("*.md"):
            rel = p.relative_to(root).as_posix()
            if p.name != "README.md" and f"({rel})" not in index:
                fails.append(f"index.md does not list {rel}; run --write-index")

    # Every memory note is dated, sourced and listed.
    mem_index = read(root / "memory/MEMORY.md")
    for p in sorted((root / "memory").glob("*.md")):
        if p.name == "MEMORY.md":
            continue
        head = read(p)[:400]
        for key in ("name:", "date:", "source:"):
            if key not in head:
                fails.append(f"memory/{p.name} has no '{key}' line at the top")
        if f"({p.name})" not in mem_index:
            fails.append(f"memory/{p.name} is not listed in memory/MEMORY.md")

    # No secrets anywhere, raw included.
    for p in text_files(root):
        text = read(p)
        rel = p.relative_to(root).as_posix()
        for label, rx in SECRETS:
            if rx.search(text):
                fails.append(f"possible {label} in {rel}; remove it, secrets never live here")
        for m in CARD.finditer(text):
            digits = re.sub(r"\D", "", m.group(0))
            if 13 <= len(digits) <= 19 and luhn(digits) and not re.fullmatch(r"(\d)\1+", digits):
                fails.append(f"possible card number in {rel}; remove it")
                break

    # Steps file is the one source for the install.
    try:
        steps = json.loads(read(root / "setup/steps.json"))["steps"]
        progress = json.loads(read(root / "setup/progress.json"))
    except (json.JSONDecodeError, KeyError) as e:
        fails.append(f"setup files are not valid: {e}")
        return fails, warns

    if stage == "installed":
        p = prof or {}
        need = {
            "what_we_do": p.get("what_we_do"),
            "phone or email": p.get("phone") or p.get("email"),
            "services": p.get("services"),
            "ideal_customer": p.get("ideal_customer"),
            "team": p.get("team"),
            "voice.three_words": (p.get("voice") or {}).get("three_words"),
        }
        for k, v in need.items():
            if not v:
                fails.append(f"config/business.json: '{k}' is still blank")
        for page in CORE_PAGES:
            f = root / "wiki" / f"{page}.md"
            if not f.is_file() or STUB in read(f):
                fails.append(f"wiki/{page}.md is not written yet")
        for c in p.get("companies") or []:
            page = root / c.get("page", "")
            if page.is_file() and STUB in read(page):
                fails.append(f"{c['page']} (company '{c['name']}') is not written yet")
        if not [x for x in (root / "wiki/processes").glob("*.md") if x.name != "README.md"]:
            fails.append("no process is mapped yet in wiki/processes/")
        if WHO_PLACEHOLDER in read(root / "CLAUDE.md"):
            fails.append("CLAUDE.md: the 'Who this business is' paragraph is not written")
        done = progress.get("done", {})
        for s in steps:
            if s.get("stage") == "setup" and s["id"] not in done and s["id"] != "setup-finish":
                fails.append(f"setup step not done: {s['id']} ({s['title']})")
        if not len(read(root / "log.md").splitlines()) > 3:
            warns.append("log.md has almost nothing in it")
    else:
        for page in CORE_PAGES:
            if not (root / "wiki" / f"{page}.md").is_file():
                fails.append(f"missing starter page: wiki/{page}.md")

    if any((root / "raw/inbox").iterdir()) and stage == "installed":
        names = [x.name for x in (root / "raw/inbox").iterdir() if x.name != ".gitkeep"]
        if names:
            warns.append(f"{len(names)} file(s) waiting in raw/inbox; say 'file it'")
    return fails, warns


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check this second brain is built right.")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--stage", choices=["factory", "installed"], default=None,
                    help="default: 'installed' once setup/progress.json says so, else 'factory'")
    ap.add_argument("--write-index", action="store_true", help="rebuild index.md first")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()

    if a.write_index:
        (root / "index.md").write_text(build_index(root), encoding="utf-8", newline="\n")
    stage = a.stage
    if stage is None:
        try:
            status = json.loads(read(root / "setup/progress.json")).get("status")
        except json.JSONDecodeError:
            status = ""
        stage = "installed" if status == "installed" else "factory"

    fails, warns = check(root, stage)
    print(f"Brain check {VERSION} - stage: {stage} - {root.name}")
    for w in warns:
        print(f"  note: {w}")
    for f in fails:
        print(f"  FAIL: {f}")
    print("PASS" if not fails else f"{len(fails)} problem(s) to fix")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
