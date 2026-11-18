#!/usr/bin/env bash
# Assemble the GitHub Pages site into DIR (default _site) from an explicit list of files,
# then snapshot the data into DIR/data. Used by .github/workflows/pages.yml and for local checks.
#   scripts/build_site.sh [DIR] [PUBLISHED_DATA_URL]
# Only listed files are copied, so .runtime/, .remember/, tests/, archive/, Releases/ and serve.py
# never reach the site. Snapshot data is written here, never committed.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${1:-_site}"
FALLBACK="${2:-}"

rm -rf "$OUT"
mkdir -p "$OUT/data"
cp btc_dashboard_v2.html app.webmanifest "$OUT/"
cp -r vendor icons "$OUT/"
cp data/manual.json "$OUT/data/"
cat > "$OUT/index.html" <<'HTML'
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>BTC Macro Dashboard</title><meta http-equiv="refresh" content="0; url=btc_dashboard_v2.html">
<link rel="manifest" href="app.webmanifest"><link rel="canonical" href="btc_dashboard_v2.html"></head>
<body><a href="btc_dashboard_v2.html">Open the BTC Macro Dashboard</a></body></html>
HTML

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python
if [ -n "$FALLBACK" ]; then
  "$PY" serve.py --snapshot "$OUT/data" --fallback-url "$FALLBACK" || echo "snapshot exited with $?"
else
  "$PY" serve.py --snapshot "$OUT/data" || echo "snapshot exited with $?"
fi

# Guard: nothing outside the allow-list may be published.
"$PY" - "$OUT" <<'PYCHECK'
import json, sys
from pathlib import Path
out = Path(sys.argv[1])
allowed_top = {"btc_dashboard_v2.html", "app.webmanifest", "index.html", "vendor", "icons", "data"}
found = {p.name for p in out.iterdir()}
extra = found - allowed_top
assert not extra, f"unexpected files in site: {sorted(extra)}"
for required in ("btc_dashboard_v2.html", "app.webmanifest", "index.html", "vendor/chart.umd.4.4.1.js",
                 "icons/icon-192.png", "icons/icon-512.png", "icons/apple-touch-icon.png", "data/manual.json", "data/manifest.json"):
    assert (out / required).is_file(), f"missing {required}"
for bad in (".runtime", ".remember", "tests", "archive", "Releases", ".git", "serve.py"):
    assert not list(out.rglob(bad)), f"{bad} must not be published"
m = json.loads((out / "data/manifest.json").read_text(encoding="utf-8"))
usable = [k for k, e in m["sources"].items() if e["status"] != "missing"]
assert usable, "snapshot produced no usable source"
print(f"site ok: {sum(1 for _ in out.rglob('*') if _.is_file())} files; snapshot usable {len(usable)}/{len(m['sources'])}")
PYCHECK
