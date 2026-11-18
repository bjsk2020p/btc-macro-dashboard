#!/usr/bin/env python3
"""Local server for the BTC Macro Dashboard.

Serves this project directory and a same-origin FRED relay at
    /api/fred/<SERIES_ID>?cosd=YYYY-MM-DD
because FRED's public CSV endpoint (fredgraph.csv) sends no CORS headers, so
browsers block direct requests. No API key is required. Standard library only.

    python serve.py              # http://127.0.0.1:8765/btc_dashboard_v2.html
    python serve.py --port 9000
"""
import argparse
import datetime as dt
import functools
import http.server
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# Only the series the dashboard uses; the relay is not an open proxy.
ALLOWED = {"M2SL", "WALCL", "WDTGAL", "RRPONTSYD", "DFII10", "DGS10", "DGS2"}
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={cosd}"
TIMEOUT_S = 15
ATTEMPTS = 3           # bounded retries for network errors, timeouts, 429 and 5xx
MEMO_TTL_S = 600       # FRED itself sends Cache-Control: max-age=600
RETRYABLE_HTTP = {429, 500, 502, 503, 504}

_memo = {}
_memo_lock = threading.Lock()


class RelayError(Exception):
    def __init__(self, kind, message, status=None):
        super().__init__(message)
        self.kind, self.message, self.status = kind, message, status


def fetch_fred_csv(sid, cosd):
    key = (sid, cosd)
    with _memo_lock:
        hit = _memo.get(key)
        if hit and time.time() - hit["at"] < MEMO_TTL_S:
            return hit
    url = FRED_CSV.format(sid=sid, cosd=cosd)
    last = None
    for attempt in range(ATTEMPTS):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "btc-macro-dashboard-local-relay/1.0",
                "Accept": "text/csv"})
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                body = resp.read().decode("utf-8-sig")
                last_modified = resp.headers.get("Last-Modified")
            first = body.split("\n", 1)[0].strip()
            if first not in (f"observation_date,{sid}", f"DATE,{sid}"):
                raise RelayError("parse", f"unexpected CSV header from FRED: {first[:80]!r}")
            result = {"body": body, "last_modified": last_modified,
                      "retrieved": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                      "at": time.time()}
            with _memo_lock:
                _memo[key] = result
            return result
        except RelayError:
            raise
        except urllib.error.HTTPError as e:
            kind = "auth" if e.code in (401, 403) else "rate-limit" if e.code == 429 else "http"
            last = RelayError(kind, f"FRED returned HTTP {e.code}", e.code)
            if e.code not in RETRYABLE_HTTP:
                break
        except (socket.timeout, TimeoutError):
            last = RelayError("timeout", f"FRED did not respond within {TIMEOUT_S}s")
        except urllib.error.URLError as e:
            reason = e.reason
            if isinstance(reason, (socket.timeout, TimeoutError)):
                last = RelayError("timeout", f"FRED did not respond within {TIMEOUT_S}s")
            else:
                last = RelayError("network", f"network error reaching FRED: {reason}")
        if attempt < ATTEMPTS - 1:
            time.sleep(2 ** attempt)
    raise last


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        m = re.fullmatch(r"/api/fred/([A-Z0-9]+)(?:\?(.*))?", self.path)
        if not m:
            return super().do_GET()
        sid, query = m.group(1), m.group(2) or ""
        if sid not in ALLOWED:
            return self._json(404, {"error": "unknown-series", "message": f"{sid} is not relayed"})
        cm = re.search(r"(?:^|&)cosd=(\d{4}-\d{2}-\d{2})(?:&|$)", query)
        cosd = cm.group(1) if cm else (dt.date.today() - dt.timedelta(days=730)).isoformat()
        try:
            res = fetch_fred_csv(sid, cosd)
        except RelayError as e:
            return self._json(502, {"error": e.kind, "status": e.status, "message": e.message})
        data = res["body"].encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Provider", "FRED (Federal Reserve Bank of St. Louis) via local relay")
        self.send_header("X-Retrieved-At", res["retrieved"])
        if res["last_modified"]:
            self.send_header("X-Source-Last-Modified", res["last_modified"])
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/"):
            super().log_message(fmt, *args)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    handler = functools.partial(Handler, directory=str(ROOT))
    with http.server.ThreadingHTTPServer((args.host, args.port), handler) as httpd:
        print(f"BTC Macro Dashboard: http://{args.host}:{args.port}/btc_dashboard_v2.html", flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
