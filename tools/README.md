# Tools already provided

| Need | Command | Effects |
|---|---|---|
| Check foundation and current stage | `python tools/foundation_check.py` | Reads local brain files; prints findings |
| Rebuild index and check | `python tools/foundation_check.py --write-index` | Replaces index.md; reads and checks files |
| Check full business setup | `python tools/foundation_check.py --stage installed` | Reads files; fails if required setup is unfinished |
| Run original installer check | `python tools/brain_check.py` | Reads base brain files; fewer checks than foundation checker |
| Prepare minimal support diagnostics | `python tools/support_snapshot.py` | Prints fixed structural facts, no file contents |
| Save a diagnostic report | `python tools/support_snapshot.py --out support/diagnostic.json` | Creates a new local report; refuses overwrite |

Python 3.10+; standard library only. Use `py` or `python3` if that is your installed launcher. The scripts perform no network requests. Review findings locally before sharing; the full health check may name private files in a populated brain, so use the minimal support snapshot for exchange.

In this brain, `.claude/settings.json` keeps the installer's guardrails (deny rules plus pre-approved setup commands), and `.github/workflows/brain-check.yml` runs `brain_check.py` on GitHub. Both were kept when foundation 1.0.0 was adopted on 2026-10-17. `foundation_check.py` expects foundation-style setup steps and a `backup` record in progress; until those are merged, treat its setup failures as known gaps, not breakage.
