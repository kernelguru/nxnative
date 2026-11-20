#!/bin/sh
# The Linux builder's container start: the data folders, a persistent wineserver (each Metal compile then starts in milliseconds, not seconds), the translation chain's lock for
# this checkout when the Apple parts are installed but the lock is not theirs yet (a new image or new Metal tools), then the web interface.
set -eu
DATA=${NXN_DATA:-/data}
mkdir -p "$DATA/toolchain" "$DATA/games" "$DATA/builds" "$DATA/keys" "$DATA/uploads" "$DATA/ipa"
chmod 700 "$DATA/keys"
if [ -f "$WINEPREFIX/.nxn_ready" ]; then wineserver -p 2> /dev/null || true; fi
python3 /opt/nxnative/linux/setup/setup.py status --json > /tmp/nxn_status.json 2> /dev/null || true
if python3 -c 'import json,sys; s=json.load(open("/tmp/nxn_status.json")); sys.exit(0 if s["xcode"]["ios_sdk"] and s["metal"]["installed"] and not s["chain"] else 1)' 2> /dev/null; then
  echo "writing the translation chain lock for this checkout's tools"
  python3 /opt/nxnative/linux/setup/setup.py chain || echo "[RED] setup.py chain failed: see the web interface's setup page"
fi
exec python3 -m uvicorn --app-dir /opt/nxnative/linux/web app:app --host 0.0.0.0 --port "${PORT:-8080}" --proxy-headers --forwarded-allow-ips='*'
