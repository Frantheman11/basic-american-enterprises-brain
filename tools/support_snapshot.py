"""Minimal structural diagnostics. No file contents, subprocesses, credentials or network."""
import argparse
import json
import platform
import sys
from pathlib import Path

REQUIRED_FILES = (
    "CLAUDE.md", "README.md", "START-HERE.md", "index.md", "log.md", "todo.md",
    "config/business.json", "setup/progress.json", "setup/steps.json", "memory/MEMORY.md",
    "knowledge/index.md", "library/SKILLS.md", "tools/brain_check.py",
    "tools/foundation_check.py", ".claude/skills/brain-setup/SKILL.md",
    ".claude/skills/file-it/SKILL.md", ".claude/skills/support-session/SKILL.md",
)


def snapshot(root):
    root = Path(root).resolve()
    family = platform.system()
    return {
        "report_schema": 1,
        "os_family": family if family in ("Windows", "Darwin", "Linux") else "Other",
        "python_version": ".".join(str(n) for n in sys.version_info[:3]),
        "required_files_present": {rel: (root / rel).is_file() for rel in REQUIRED_FILES},
        "scope": "Presence checks only; business contents, settings, logs and environment were not read.",
        "transport": "Local output only; nothing sent.",
    }


def save_report(root, output, report):
    root = Path(root).resolve()
    candidate = Path(output)
    if not candidate.is_absolute():
        candidate = root / candidate
    # Only direct JSON files in the real support directory; reject linked locations.
    support = root / "support"
    if support.is_symlink() or not support.resolve().is_relative_to(root):
        raise ValueError("support directory must stay inside this brain")
    if candidate.is_symlink() or candidate.resolve().parent != support.resolve() or candidate.suffix != ".json":
        raise ValueError("output must be a new .json file directly inside support/")
    support.mkdir(exist_ok=True)
    with candidate.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", help="Create a new JSON report directly inside support/")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    report = snapshot(root)
    if args.out:
        try:
            save_report(root, args.out, report)
        except (OSError, ValueError):
            print("Report not saved: use a new .json filename inside this brain's support folder.", file=sys.stderr)
            return 1
        print("Created local support report. Review it before sharing.")
    else:
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
