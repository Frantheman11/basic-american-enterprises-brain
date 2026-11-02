#!/bin/bash
# Refresh a client's PORTFOLIO CRM (made by install-portfolio.sh) to a new release folder, and with
# SYNC_SETUP=1 bring the setup from the client's repo across (profiles, the portfolio file, brand files).
# Run as root from the upload folder, which holds release.tar.gz (the client's crm/ folder, no private/).
# push_release.py (on the client's PC) uploads it and runs this.
#
#   PORTFOLIO=basic-american SLUG=basic-american DATE=20261105-1430 [SYNC_SETUP=1] ./refresh-portfolio.sh
#
# Order: an online backup of every database first; the new release beside the old one with its own
# venv; the setup sync (writes only after the new setup passes the server's own check); the env file and
# both units pointed at the new folder; restart; health ok and an unsigned / gets 401. Any failure after
# the backup puts the old folder, the setup files and every database back, and restarts the old release.
# Never touched: the tunnel, Access, DNS, /etc/crm/access.json.
set -euo pipefail
cd "$(dirname "$0")"
: "${PORTFOLIO:?}" "${SLUG:?}" "${DATE:?}"
DATA=/var/lib/crm
PF=$DATA/portfolio
ENV=/etc/crm/crm.env
NEW=/srv/$SLUG/crm-$DATE
SAVE=$DATA/backups/pre-refresh-$DATE
UNITS="/etc/systemd/system/crm.service /etc/systemd/system/crm-backup.service"
PORT=$(grep -oP '^CRM_PORT=\K.*' "$ENV")
OLD=$(grep -oP '^CRM_ROOT=\K.*' "$ENV")
test -d "$OLD" || { echo "CRM_ROOT $OLD is not a folder"; exit 1; }
test -d "$PF" || { echo "no live portfolio at $PF (install-portfolio.sh makes it)"; exit 1; }
test -f release.tar.gz || { echo "release.tar.gz must sit next to this script"; exit 1; }
test ! -e "$NEW" || { echo "$NEW exists; pick another DATE"; exit 1; }
test ! -e "$SAVE" || { echo "$SAVE exists; pick another DATE"; exit 1; }
echo "old release: $OLD"

echo "1/5 backup of every database and the setup files (engine online backup, while it runs)"
install -d -o crm -g crm -m 750 "$SAVE"
(cd "$OLD" && sudo -u crm "$OLD/.venv/bin/python" -B -m command_center.portfolio backup \
    --portfolio "$PORTFOLIO" --root "$PF" --dir "$SAVE")
cp -a "$PF/data" "$SAVE/data"
find "$PF" -maxdepth 1 -type f \( -name '*.css' -o -name '*.svg' -o -name '*.png' -o -name '*.woff2' \) -exec cp -a {} "$SAVE/" \;
ls "$SAVE"/*.sqlite3 >/dev/null

rollback() {
  echo "FAILED: putting the old release, the setup and every database back"
  systemctl stop crm || true
  sed -i "s#$NEW#$OLD#g" "$ENV" $UNITS
  rm -rf "$PF/data" && cp -a "$SAVE/data" "$PF/data"
  find "$SAVE" -maxdepth 1 -type f \( -name '*.css' -o -name '*.svg' -o -name '*.png' -o -name '*.woff2' \) -exec cp -a {} "$PF/" \;
  for f in "$SAVE"/*.sqlite3; do
    name=$(basename "$f" .sqlite3); base=${name%-*}   # <business>-<stamp> or <portfolio>-links-<stamp>
    rm -f "$PF/private/$base.sqlite3-wal" "$PF/private/$base.sqlite3-shm"
    cp "$f" "$PF/private/$base.sqlite3" && chown crm:crm "$PF/private/$base.sqlite3"
  done
  chown -R crm:crm "$PF/data"
  systemctl daemon-reload && systemctl start crm
  echo "back on $OLD. The failed release is left at $NEW for a look."
  exit 1
}
trap rollback ERR

echo "2/5 the new release at $NEW"
install -d -m 755 "$NEW"
tar -xzf release.tar.gz -C "$NEW"
rm -rf "$NEW/private"
chown -R root:root "$NEW" && chmod -R u=rwX,go=rX "$NEW"
python3 -m venv "$NEW/.venv" && "$NEW/.venv/bin/pip" install -q 'cryptography>=42'

if [ "${SYNC_SETUP:-0}" = 1 ]; then
  echo "3/5 the setup from the release into $PF (sign-in values from /etc/crm/access.json)"
  (cd "$NEW" && sudo -u crm "$NEW/.venv/bin/python" -B -m command_center.portfolio sync \
      --portfolio "$PORTFOLIO" --root "$PF" --from "$NEW" --access /etc/crm/access.json)
else
  echo "3/5 code only: the live setup is unchanged"
fi

echo "4/5 point the service at $NEW and restart"
sed -i "s#$OLD#$NEW#g" "$ENV" $UNITS
systemctl daemon-reload
systemctl restart crm
sleep 2

echo "5/5 check"
systemctl is-active crm
test "$(curl -s http://127.0.0.1:$PORT/healthz)" = ok
test "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/)" = 401
trap - ERR
echo "done: $NEW is live. The old release stays at $OLD; the backup at $SAVE."
