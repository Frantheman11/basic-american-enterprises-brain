"""Deploy a client's portfolio CRM from THEIR repo to THEIR server: pack, upload, refresh.

Runs on the client's computer (Windows, Mac or Linux; it uses the ssh and scp that ship with them),
from their crm/ folder. Nothing is sent unless you pass --apply: without it, it prints every step.

    python deploy/push_release.py --host <server ip> --portfolio <id> --slug <slug>            # dry run
    python deploy/push_release.py --host <server ip> --portfolio <id> --slug <slug> --apply    # code only
    python deploy/push_release.py --host <server ip> --portfolio <id> --slug <slug> --sync-setup --apply

--sync-setup also brings the setup across: data/command-center/ (profiles, the portfolio file) and the
brand files. The server keeps its own sign-in values and any business added there with "+".
The server backs up every database first and puts everything back if its checks fail
(refresh-portfolio.sh). The private/ folder, .git and caches never leave this computer.
"""
import argparse
import subprocess
import sys
import tarfile
from datetime import datetime
from pathlib import Path

SKIP_DIRS = {"private", ".git", "__pycache__", ".venv", "Generated", "node_modules"}
SKIP_SUFFIXES = {".pyc", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".env"}
HERE = Path(__file__).resolve().parent


def members(crm):
    """The files that go into the release, relative to the crm/ folder."""
    out = []
    for f in sorted(Path(crm).rglob("*")):
        rel = f.relative_to(crm)
        if not f.is_file() or any(p in SKIP_DIRS for p in rel.parts) or f.name == ".env" \
                or any(f.name.endswith(s) for s in SKIP_SUFFIXES):
            continue
        out.append(rel)
    return out


def pack(crm, target):
    with tarfile.open(target, "w:gz") as tar:
        for rel in members(crm):
            tar.add(Path(crm) / rel, arcname=rel.as_posix())
    return target


def steps(host, user, portfolio, slug, stamp, sync_setup, key=None):
    """The commands, in order. [(description, argv)]"""
    ident = ["-i", key] if key else []
    where = f"/root/upload-{stamp}"
    env = f"PORTFOLIO={portfolio} SLUG={slug} DATE={stamp}" + (" SYNC_SETUP=1" if sync_setup else "")
    return [
        ("make the upload folder", ["ssh", *ident, f"{user}@{host}", f"mkdir -p {where}"]),
        ("upload the release and the refresh script",
         ["scp", *ident, "release.tar.gz", str(HERE / "refresh-portfolio.sh"), f"{user}@{host}:{where}/"]),
        ("refresh (backup first; everything back on a failed check)",
         ["ssh", *ident, f"{user}@{host}", f"cd {where} && chmod +x refresh-portfolio.sh && {env} ./refresh-portfolio.sh"]),
    ]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", required=True)
    ap.add_argument("--user", default="root")
    ap.add_argument("--portfolio", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--crm", default=".", help="the crm/ folder (default: here)")
    ap.add_argument("--key", help="ssh private key file, if not the default")
    ap.add_argument("--sync-setup", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    crm = Path(a.crm).resolve()
    if not (crm / "command_center").is_dir() or not (crm / "data/command-center/portfolios" / f"{a.portfolio}.json").is_file():
        raise SystemExit(f"{crm} is not a crm/ folder with the portfolio {a.portfolio}")
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    files = members(crm)
    print(f"release: {len(files)} files from {crm} (no private/, .git, caches or databases)")
    plan = steps(a.host, a.user, a.portfolio, a.slug, stamp, a.sync_setup, a.key)
    for what, cmd in plan:
        print(f"- {what}: {' '.join(cmd)}")
    if not a.apply:
        print("dry run: nothing sent. Add --apply to deploy.")
        return
    pack(crm, Path("release.tar.gz"))
    for what, cmd in plan:
        print(f"== {what}")
        if subprocess.run(cmd).returncode != 0:
            raise SystemExit(f"stopped at: {what}. The server rolls itself back on a failed check.")
    Path("release.tar.gz").unlink(missing_ok=True)
    print(f"deployed {stamp}.")


if __name__ == "__main__":
    sys.exit(main())
