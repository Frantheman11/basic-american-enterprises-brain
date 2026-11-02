#!/bin/bash
# Install a client's PORTFOLIO CRM (several businesses, one sign-in) on the CLIENT's own droplet
# (Ubuntu 24.04), behind Cloudflare Access. install.sh's sibling: same user, firewall, tunnel and
# checks; the difference is where the data lives. First use: Basic American Enterprises (planned).
# Run as root from the upload folder, which holds:
#   release.tar.gz   the client's crm/ folder (command_center/, data/, brand files, README.md), no private/.
#                    Its data/command-center/ holds every profile and portfolios/<PORTFOLIO>.json
#                    (tools/portfolio_setup.py writes them from the client's spec).
#   access.json      {"identity": "cloudflare_access", "team_domain": "https://<team>.cloudflareaccess.com",
#                     "aud": "<the Access app AUD>", "origins": ["https://<host>"]}: the host values,
#                    written into every profile here. Nothing else differs from the repo copy.
#
#   PORTFOLIO=basic-american SLUG=basic-american PORT=8780 DATE=20261101 ./install-portfolio.sh
#
# Layout: /srv/<slug>/crm-<date> = the code, root-owned and read-only to the service.
# /var/lib/crm/portfolio = the LIVE portfolio root, service-owned: profiles, the portfolio file, brand
# files, private/ (one database per business and the link file). It is seeded from the release ONCE;
# "+" writes new businesses there. A later code refresh never touches it.
set -euo pipefail
cd "$(dirname "$0")"
: "${PORTFOLIO:?}" "${SLUG:?}" "${PORT:?}" "${DATE:?}"
REL=/srv/$SLUG/crm-$DATE
DATA=/var/lib/crm
PF=$DATA/portfolio
test -f release.tar.gz && test -f access.json || { echo "release.tar.gz and access.json must sit next to this script"; exit 1; }
test ! -e "$REL" || { echo "$REL exists; pick another DATE"; exit 1; }
test ! -e "$PF" || { echo "$PF exists: this server already has a live portfolio. Refresh the code instead."; exit 1; }

echo "1/6 packages, service user, firewall"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq python3-venv python3-pip ufw curl >/dev/null
id crm >/dev/null 2>&1 || adduser --system --group --home "$DATA" --shell /usr/sbin/nologin crm
ufw allow OpenSSH >/dev/null && ufw --force enable >/dev/null
if ! command -v cloudflared >/dev/null; then
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared noble main" > /etc/apt/sources.list.d/cloudflared.list
  apt-get update -qq && apt-get install -y -qq cloudflared >/dev/null
fi
cloudflared --version

echo "2/6 the code at $REL"
install -d -m 755 "$REL"
tar -xzf release.tar.gz -C "$REL"
rm -rf "$REL/private"
chown -R root:root "$REL" && chmod -R u=rwX,go=rX "$REL"
python3 -m venv "$REL/.venv" && "$REL/.venv/bin/pip" install -q 'cryptography>=42'
test -f "$REL/data/command-center/portfolios/$PORTFOLIO.json" || { echo "the release has no portfolio $PORTFOLIO"; exit 1; }

echo "3/6 the live portfolio root at $PF, seeded once from the release, sign-in values in every profile"
install -d -o crm -g crm -m 750 "$DATA" "$DATA/backups"
install -d -o crm -g crm -m 750 "$PF" "$PF/private"
cp -r "$REL/data" "$PF/data"
find "$REL" -maxdepth 1 -type f \( -name '*.css' -o -name '*.svg' -o -name '*.png' -o -name '*.woff2' \) -exec cp {} "$PF/" \;
for d in brand; do test -d "$REL/$d" && cp -r "$REL/$d" "$PF/$d"; done
"$REL/.venv/bin/python" - "$PF" access.json <<'PY'
import json, sys
from pathlib import Path
root, access = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text())
assert access.get("identity") == "cloudflare_access" and access.get("team_domain") and access.get("aud"), "access.json is incomplete"
for f in sorted((root / "data/command-center").glob("*.json")):
    prof = json.loads(f.read_text())
    if "business_id" not in prof:
        continue
    prof["access"] = {**access, "local_person": (prof.get("access") or {}).get("local_person", "")}
    prof.setdefault("crm", {})["demo"] = False
    f.write_text(json.dumps(prof, indent=1) + "\n")
    print("signed in through Access:", f.name)
PY
chown -R crm:crm "$PF" && chmod -R u=rwX,go= "$PF"
(cd "$REL" && sudo -u crm "$REL/.venv/bin/python" -B -c "from command_center import portfolio; portfolio.load('$PORTFOLIO', '$PF'); print('portfolio ok')")
install -d -m 755 /etc/crm
cat > /etc/crm/crm.env <<ENV
CRM_PORTFOLIO=$PORTFOLIO
CRM_PORT=$PORT
CRM_ROOT=$REL
CRM_DATA=$PF
ENV
chmod 640 /etc/crm/crm.env && chgrp crm /etc/crm/crm.env
install -m 640 -g crm access.json /etc/crm/access.json  # refresh-portfolio.sh SYNC_SETUP=1 reuses it

echo "4/6 the service, bound to 127.0.0.1:$PORT"
cat > /etc/systemd/system/crm.service <<UNIT
[Unit]
Description=The portfolio CRM on 127.0.0.1, signed in through Cloudflare Access
After=network-online.target
Wants=network-online.target
[Service]
User=crm
Group=crm
WorkingDirectory=$REL
ExecStart=$REL/.venv/bin/python -B -m command_center.portfolio serve --portfolio $PORTFOLIO --root $PF --host 127.0.0.1 --port $PORT --identity cloudflare_access
Restart=on-failure
RestartSec=3
Environment=PYTHONUNBUFFERED=1
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=$DATA
UMask=0077
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/crm-backup.service <<UNIT
[Unit]
Description=Nightly portfolio CRM backup (every business and the link file, 30 days kept locally)
[Service]
Type=oneshot
User=crm
Group=crm
WorkingDirectory=$REL
ExecStart=/bin/sh -c '$REL/.venv/bin/python -B -m command_center.portfolio backup --portfolio $PORTFOLIO --root $PF --dir $DATA/backups && find $DATA/backups -name "*-2*.sqlite3" -mtime +30 -delete'
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$DATA
UNIT
cat > /etc/systemd/system/crm-backup.timer <<'UNIT'
[Unit]
Description=Nightly portfolio CRM backup
[Timer]
OnCalendar=*-*-* 10:30:00 UTC
Persistent=true
[Install]
WantedBy=timers.target
UNIT

echo "5/6 start"
systemctl daemon-reload
systemctl enable --now crm crm-backup.timer
sleep 2
systemctl is-active crm
test "$(curl -s http://127.0.0.1:$PORT/healthz)" = ok
test "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/)" = 401
systemctl start crm-backup && ls "$DATA/backups" | grep -q -- "-2"

echo "6/6 done. The CRM answers only on 127.0.0.1:$PORT. Next: the tunnel token to /etc/cloudflared/crm.token and the cloudflared unit."
