"""Check this foundation profile on top of the shared installer checker. Python 3.10+."""
import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

import brain_check

CORE_SKILLS = (
    "brain-setup", "build-from-intake", "file-it", "catch-me-up", "remember-this",
    "grow-system", "build-skill", "support-session",
)
REQUIRED = (
    "AGENTS.md", "ADOPT-EXISTING.md", "MEETING-GUIDE.md", "knowledge/index.md",
    "knowledge/architecture.md", "knowledge/skill-design.md", "knowledge/knowledge-lifecycle.md",
    "knowledge/growth-rules.md", "knowledge/maintenance.md", "knowledge/sources.md",
    "library/SKILLS.md", "library/skill-template.md", "library/knowledge-template.md",
    "library/handoff-template.md", "support/README.md", "support/TICKET-TEMPLATE.md",
    "setup/VERIFY.md", "tools/support_snapshot.py", "tools/README.md",
)


def index_text(root):
    return brain_check.build_index(root) + (
        "\n## Foundation\n\n"
        "- [Structure and growth](knowledge/index.md) - maintain and extend the brain\n"
        "- [Skill library](library/SKILLS.md) - task routing and reusable templates\n"
        "- [Tool inventory](tools/README.md) - checks and diagnostics already provided\n"
        "- [Support](support/README.md) - cooperative troubleshooting\n"
    )


def check(root, stage):
    fails, warns = brain_check.check(root, stage)
    for rel in REQUIRED:
        if not (root / rel).is_file():
            fails.append(f"missing foundation file: {rel}")
    catalog = brain_check.read(root / "library/SKILLS.md")
    for name in CORE_SKILLS:
        if not (root / ".claude/skills" / name / "SKILL.md").is_file():
            fails.append(f"missing foundation skill: {name}")
    for path in sorted((root / ".claude/skills").glob("*/SKILL.md")):
        body = brain_check.read(path)
        match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", body, re.S)
        head = match.group(1) if match else ""
        name = re.search(r"^name:\s*([a-z0-9]+(?:-[a-z0-9]+)*)\s*$", head, re.M)
        description = re.search(r"^description:\s*(\S.+)$", head, re.M)
        if not name or name.group(1) != path.parent.name or not description:
            fails.append(f"invalid name/description frontmatter: {path.relative_to(root).as_posix()}")
        if f"../.claude/skills/{path.parent.name}/SKILL.md" not in catalog:
            fails.append(f"skill missing from library/SKILLS.md: {path.parent.name}")

    # Link validation covers authored knowledge, never external URLs or original source files.
    docs = list(root.glob("*.md"))
    for folder in ("knowledge", "library", "wiki", "memory", "handoffs", "setup", "support", ".claude/skills", "tools"):
        docs.extend((root / folder).rglob("*.md"))
    for path in sorted(set(docs)):
        body = brain_check.read(path)
        for target in re.findall(r"\[[^\]]+\]\(([^\s)]+)\)", body):
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("#"):
                continue
            destination = (path.parent / unquote(target.split("#", 1)[0])).resolve()
            if not destination.is_relative_to(root) or not destination.exists():
                fails.append(f"broken local link in {path.relative_to(root).as_posix()}: {target}")

    if stage == "installed":
        try:
            profile = json.loads((root / "config/business.json").read_text(encoding="utf-8"))
            progress = json.loads((root / "setup/progress.json").read_text(encoding="utf-8"))
            if not profile.get("owner") or profile.get("name") in (None, "", "Your business"):
                fails.append("business name and owner must be confirmed before installation is complete")
            backup = progress.get("backup", {})
            if not backup.get("method") or not backup.get("restore_tested_on"):
                fails.append("backup method and observed restore-test date are required")
        except (OSError, ValueError, TypeError):
            fails.append("cannot verify installed profile and progress")
    return list(dict.fromkeys(fails)), warns


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--stage", choices=("factory", "installed"))
    parser.add_argument("--write-index", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        stage = args.stage
        if stage is None:
            progress = json.loads((root / "setup/progress.json").read_text(encoding="utf-8"))
            stage = "installed" if progress.get("status") == "installed" else "factory"
        if args.write_index:
            (root / "index.md").write_text(index_text(root), encoding="utf-8", newline="\n")
        failures, warnings = check(root, stage)
    except (OSError, ValueError, TypeError) as exc:
        print(f"FAIL: required local file could not be checked ({type(exc).__name__})")
        return 1
    print(f"Foundation 1.0.0 - stage: {stage}")
    for message in warnings:
        print(f"NOTE: {message}")
    for message in failures:
        print(f"FAIL: {message}")
    print("PASS" if not failures else f"{len(failures)} issue(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
