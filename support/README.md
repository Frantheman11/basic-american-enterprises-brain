# Cooperative Claude support

The practical cross-person route is a private shared ticket or a manual exchange. Each Claude reads an explicitly shared request and offers advice. The receiving person controls local changes. The ticket stores text; it does not provide remote execution.

Use [TICKET-TEMPLATE.md](TICKET-TEMPLATE.md) and the [meeting exercise](../MEETING-GUIDE.md). If both people use GitHub, a private support-only repository with Issues enabled can carry the messages without exposing their business repositories. Invite only the required people. Existing authenticated GitHub tools can read/comment after each person authorizes that exact exchange; otherwise paste the reviewed text manually.

## Minimal diagnostic helper

Run `python tools/support_snapshot.py` from the brain folder. It reports the OS family, Python version and presence of fixed foundation files. It reads no business file contents, environment variables, Git remotes, usernames or logs, and sends nothing. To save a new report, use `--out support/diagnostic.json`. It refuses to overwrite a file. Read any text you add before sharing.

A missing Python interpreter is itself useful information. In that case, report OS family, the observed error (with personal paths removed), and whether the expected files exist. Do not install a runtime merely to produce this report.

## Native messaging: a separate possibility

Current Claude Code documentation describes cross-session messaging between your own discoverable sessions, including your sessions on other machines through Remote Control. It documents version and provider requirements and a `/list-agents` discovery command. This does not establish a connection between two separate people's accounts. [Official messaging documentation](https://code.claude.com/docs/en/cross-session-messaging).

During a native test, inspect available sessions first. Do not share account credentials to make another person's session appear. If the intended recipient is not discoverable, use the private ticket/manual route. Native incoming messages cannot approve actions or authorize configuration changes. A file path in a message also does not transfer that file.

Remote Control continues sessions associated with your own account across devices. It is not evidence that a customer's independent account is connected to yours. [Remote Control documentation](https://code.claude.com/docs/en/remote-control).

## Stop conditions

Stop if the other side requests credentials, unrelated private files, weaker permission settings or an unexplained destructive command. A failed step should return its sanitized result before another step is proposed. Bound the rehearsal to one fault and one verified recovery; do not create a background polling loop or leave a permanent support connection running.
