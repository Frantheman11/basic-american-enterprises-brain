# Verify the foundation

From the brain root, run `python tools/foundation_check.py`. It checks the original installer skeleton plus the foundation library, skill entries and local documentation links. Use `--write-index` to rebuild the business index; this is the only writing mode. A clean, unpersonalized starter must pass factory stage and must **not** pass `--stage installed`.

After completing business intake, run `python tools/foundation_check.py --stage installed`. Fix issues at their source. A check verifies structure and common secret patterns; it cannot prove truth, find every secret or guarantee that Claude chose a skill.

## Manual path when Python is unavailable

- Confirm the files and folders listed in README exist, including hidden `.claude/skills`.
- Open each of the eight skill files from `library/SKILLS.md`; check its name, description and reference paths.
- Ensure every current wiki/process/company/handoff page is indexed. Open all knowledge and library links.
- Check that `config/business.json` parses as JSON and includes the actual name, owner, work, contact, at least one service, ideal customer, team and three voice words. Each fact has source/date evidence.
- Confirm the five core wiki pages are written from evidence and no longer have the stub marker. A first process exists and has been rehearsed.
- Keep `CLAUDE.md` under 200 lines and the repo's memory index under 150 lines (starter conventions). Every memory note has name, date, source and an index entry.
- Confirm each setup step except finish has dated evidence, including a restored backup file.
- Review files to be committed or shared for secrets and private data. Do not trust a file extension or ignore rule to do this.
- Open a fresh Claude session: ask it to locate a known fact and repeat a corrected procedure. Confirm the change survives.

Record the method as `manual`, the date, observed results and missing checks in `setup/progress.json`. Never report an automated pass for a manual inspection.
