#!/bin/sh
# The Linux builder's container start: the data folders; a persistent wineserver and Wine's background services, started detached (each Metal compile then starts in milliseconds,
# and no compile's output pipe is held open by them: linux/lib/nxn.py wine_up); in the background, the translation chain's lock for this checkout (the one kept in the volume when it
# fits these tools, else written again: setup.py chain --if-needed, logged to toolchain/chain.log); then the web interface, at once.
set -eu
DATA=${NXN_DATA:-/data}
mkdir -p "$DATA/toolchain" "$DATA/games" "$DATA/builds" "$DATA/keys" "$DATA/uploads" "$DATA/ipa"
chmod 700 "$DATA/keys"
if [ -f "$WINEPREFIX/.nxn_ready" ]; then
  wineserver -p < /dev/null > /dev/null 2>&1 || true
  python3 -c 'import sys; sys.path.insert(0, "/opt/nxnative/linux/lib"); import nxn; nxn.wine_up()' < /dev/null > /dev/null 2>&1 || true
fi
python3 /opt/nxnative/linux/setup/setup.py chain --if-needed < /dev/null > "$DATA/toolchain/chain.log" 2>&1 &
exec python3 -m uvicorn --app-dir /opt/nxnative/linux/web app:app --host 0.0.0.0 --port "${PORT:-8080}" --proxy-headers --forwarded-allow-ips='*'
