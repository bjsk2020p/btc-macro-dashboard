#!/usr/bin/env python3
"""Local server and one-click launcher for the BTC Macro Dashboard (standard library only).

    python serve.py --launch     # what Open_BTC_Dashboard.cmd runs: reuse or start the
                                 # background server, wait for its health check, open the browser
    python serve.py              # run the server in the foreground on 127.0.0.1:8765
    python serve.py --port 9000
    python serve.py --snapshot DIR [--fallback-url URL]
                                 # write every allow-listed source to static files + manifest.json
                                 # (same fetch and validators; for hosting without this server)

Endpoints (all bound to 127.0.0.1):
    /api/health                  app identity, used by the launcher and the file:// notice
    /api/fred/<ID>?cosd=DATE     allow-listed FRED CSV series
    /api/src/<KEY>               allow-listed fixed market-data URLs (Binance, CoinGecko, Yahoo)

Browsers cannot read FRED or Yahoo directly (neither sends CORS headers). This relay only
reaches the fixed URLs listed below; it is not a general proxy. No credentials are used.
The last valid response of each source is kept in .runtime/cache/ and served, clearly marked,
when the upstream request fails.
"""
import argparse
import datetime as dt
import functools
import hashlib
import http.server
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

if sys.version_info < (3, 9):
    sys.stderr.write("BTC Macro Dashboard needs Python 3.9 or newer.\n")
    sys.exit(2)

APP_ID = "btc-macro-dashboard"
ROOT = Path(__file__).resolve().parent
ENTRY = "btc_dashboard_v2.html"
RUNTIME = ROOT / ".runtime"
CACHE_DIR = RUNTIME / "cache"
LOG_FILE = RUNTIME / "serve.log"
STATE_FILE = RUNTIME / "server.json"
DEFAULT_PORT = 8765
PORT_RANGE = range(8765, 8776)
USER_AGENT = "btc-macro-dashboard-local-relay/2.0"

FRED_IDS = {"M2SL", "WALCL", "WDTGAL", "RRPONTSYD", "DFII10", "DGS10", "DGS2"}
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={cosd}"

# key -> (fixed upstream URL, in-memory reuse window in seconds)
SOURCES = {
    "binance-ticker":   ("https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT", 10),
    "binance-klines":   ("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=180", 60),
    "binance-premium":  ("https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT", 30),
    "binance-funding":  ("https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=90", 300),
    "coingecko-global": ("https://api.coingecko.com/api/v3/global", 60),
    "coingecko-stablecoins": ("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&category=stablecoins&order=market_cap_desc&per_page=250&page=1", 300),
    "coingecko-bitcoin": ("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=bitcoin", 300),
    "yahoo-dxy":        ("https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?range=1mo&interval=1d", 60),
    "yahoo-qqq":        ("https://query1.finance.yahoo.com/v8/finance/chart/QQQ?range=1mo&interval=1d", 60),
    "yahoo-tnx":        ("https://query1.finance.yahoo.com/v8/finance/chart/%5ETNX?range=1mo&interval=1d", 60),
}
FRED_MEMO_S = 600          # FRED itself sends Cache-Control: max-age=600
TIMEOUT_S = 12
ATTEMPTS = 3               # bounded: network errors, timeouts, 5xx, and 429 with a short Retry-After
RETRYABLE_HTTP = {500, 502, 503, 504}
MAX_INLINE_RETRY_AFTER_S = 5

_memo = {}
_memo_lock = threading.Lock()
_log_lock = threading.Lock()


def log(msg):
    """Append to .runtime/serve.log. Never raises (pythonw has no console)."""
    try:
        RUNTIME.mkdir(exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 1_000_000:
            LOG_FILE.replace(LOG_FILE.with_suffix(".log.1"))
        with _log_lock, open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except Exception:
        pass
    if sys.stdout is not None:
        try:
            print(msg, flush=True)
        except Exception:
            pass


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class RelayError(Exception):
    def __init__(self, kind, message, status=None, retry_after=None):
        super().__init__(message)
        self.kind, self.message, self.status, self.retry_after = kind, message, status, retry_after


def parse_retry_after(value):
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return int(value)
    try:
        when = dt.datetime.strptime(value, "%a, %d %b %Y %H:%M:%S GMT").replace(tzinfo=dt.timezone.utc)
        return max(0, int((when - dt.datetime.now(dt.timezone.utc)).total_seconds()))
    except ValueError:
        return None


def fetch_upstream(url, validate, opener=urllib.request.urlopen, sleep=time.sleep):
    """GET with bounded retries. `validate(text)` raises ValueError for malformed bodies."""
    last = None
    for attempt in range(ATTEMPTS):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
            with opener(req, timeout=TIMEOUT_S) as resp:
                text = resp.read().decode("utf-8-sig")
                last_modified = resp.headers.get("Last-Modified")
            validate(text)
            return text, last_modified
        except ValueError as e:
            raise RelayError("malformed", f"unexpected response: {e}")
        except urllib.error.HTTPError as e:
            retry_after = parse_retry_after(e.headers.get("Retry-After") if e.headers else None)
            kind = "auth" if e.code in (401, 403) else "rate-limit" if e.code in (418, 429) else "http"
            last = RelayError(kind, f"upstream returned HTTP {e.code}", e.code, retry_after)
            if e.code == 429 and retry_after is not None and retry_after <= MAX_INLINE_RETRY_AFTER_S and attempt < ATTEMPTS - 1:
                sleep(retry_after)
                continue
            if e.code not in RETRYABLE_HTTP:
                break
        except (socket.timeout, TimeoutError):
            last = RelayError("timeout", f"upstream did not respond within {TIMEOUT_S}s")
        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                last = RelayError("timeout", f"upstream did not respond within {TIMEOUT_S}s")
            else:
                last = RelayError("network", f"cannot reach upstream: {e.reason}")
        except OSError as e:
            last = RelayError("network", f"cannot reach upstream: {e}")
        if attempt < ATTEMPTS - 1:
            sleep(2 ** attempt)
    raise last


def validate_fred(sid):
    def check(text):
        first = text.split("\n", 1)[0].strip()
        if first not in (f"observation_date,{sid}", f"DATE,{sid}"):
            raise ValueError(f"CSV header {first[:80]!r}")
    return check


def validate_json(text):
    try:
        json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"not JSON ({e.msg})")


def cache_path(key):
    return CACHE_DIR / (hashlib.sha1(key.encode()).hexdigest()[:16] + ".json")


def save_last_good(key, record):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = cache_path(key).with_suffix(".tmp")
        tmp.write_text(json.dumps({"key": key, **record}), encoding="utf-8")
        tmp.replace(cache_path(key))
    except OSError as e:
        log(f"cache write failed for {key}: {e}")


def load_last_good(key, validate):
    try:
        rec = json.loads(cache_path(key).read_text(encoding="utf-8"))
        if rec.get("key") != key:
            return None
        validate(rec["body"])
        return rec
    except Exception:
        return None


_cooldown = {}            # provider host -> epoch seconds until which no request is sent (after HTTP 429)
MIN_COOLDOWN_S = 60


def get_source(key, url, memo_s, validate):
    """Fresh upstream body, or a memoised one (< memo_s old), or the marked last-good copy.
    After a 429 from a provider, nothing is sent to that host until its cooldown ends."""
    with _memo_lock:
        hit = _memo.get(key)
        if hit and time.time() - hit["at"] < memo_s:
            return hit, None
    host = urllib.request.urlparse(url).hostname
    try:
        wait = _cooldown.get(host, 0) - time.time()
        if wait > 0:
            raise RelayError("rate-limit", f"{host} rate-limited this app; pausing requests for {int(wait) + 1}s", 429, int(wait) + 1)
        try:
            body, last_modified = fetch_upstream(url, validate)
        except RelayError as err:
            if err.status in (418, 429):
                _cooldown[host] = time.time() + max(err.retry_after or 0, MIN_COOLDOWN_S)
            raise
    except RelayError as err:
        good = load_last_good(key, validate)
        if good:
            return good, err
        raise
    rec = {"body": body, "last_modified": last_modified, "retrieved": now_iso(), "at": time.time()}
    with _memo_lock:
        _memo[key] = rec
    save_last_good(key, rec)
    return rec, None


class Handler(http.server.SimpleHTTPRequestHandler):
    server_version = "BTCMacroDashboard/2"
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, ".webmanifest": "application/manifest+json"}

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/api/health":
            return self._json(200, {"app": APP_ID, "root": str(ROOT), "pid": os.getpid(),
                                    "port": self.server.server_address[1], "started": STARTED},
                              cors=True)
        m = re.fullmatch(r"/api/fred/([A-Z0-9]+)", path)
        if m:
            sid = m.group(1)
            if sid not in FRED_IDS:
                return self._json(404, {"error": "unknown-series", "message": f"{sid} is not relayed"})
            cm = re.search(r"(?:^|&)cosd=(\d{4}-\d{2}-\d{2})(?:&|$)", query)
            cosd = cm.group(1) if cm else (dt.date.today() - dt.timedelta(days=730)).isoformat()
            return self._relay(f"fred:{sid}:{cosd}", FRED_CSV.format(sid=sid, cosd=cosd), FRED_MEMO_S,
                               validate_fred(sid), "text/csv; charset=utf-8",
                               "FRED (Federal Reserve Bank of St. Louis) via local relay")
        m = re.fullmatch(r"/api/src/([a-z0-9-]+)", path)
        if m:
            key = m.group(1)
            if key not in SOURCES:
                return self._json(404, {"error": "unknown-source", "message": f"{key} is not relayed"})
            url, memo_s = SOURCES[key]
            return self._relay(f"src:{key}", url, memo_s, validate_json, "application/json; charset=utf-8",
                               f"{key.split('-')[0].capitalize()} via local relay")
        if re.match(r"/\.(runtime|git|remember)(/|$)", path.lower()):
            return self._json(404, {"error": "not-found"})
        return super().do_GET()

    def _relay(self, key, url, memo_s, validate, ctype, provider):
        try:
            rec, err = get_source(key, url, memo_s, validate)
        except RelayError as e:
            log(f"{key}: {e.kind} {e.status or ''} {e.message}")
            payload = {"error": e.kind, "status": e.status, "message": e.message}
            if e.retry_after is not None:
                payload["retry_after"] = e.retry_after
            return self._json(502, payload, extra={"Retry-After": str(e.retry_after)} if e.retry_after is not None else None)
        data = rec["body"].encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Provider", provider)
        self.send_header("X-Retrieved-At", rec["retrieved"])
        if rec.get("last_modified"):
            self.send_header("X-Source-Last-Modified", rec["last_modified"])
        if err is not None:   # upstream failed: this is the last valid copy, not a fresh retrieval
            log(f"{key}: served last-good copy from {rec['retrieved']} after {err.kind}: {err.message}")
            self.send_header("X-Relay-Cache", "last-good")
            self.send_header("X-Upstream-Error", f"{err.kind}{' ' + str(err.status) if err.status else ''}: {err.message}"[:300])
            if err.retry_after is not None:
                self.send_header("X-Retry-After", str(err.retry_after))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, payload, cors=False, extra=None):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if cors:   # only the identity check is readable from file:// pages
            self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):   # default writes to stderr, which is None under pythonw
        if self.path.startswith("/api/") and not self.path.startswith("/api/health"):
            log("%s %s" % (self.address_string(), fmt % args))


STARTED = now_iso()
EXIT_PORT_IN_USE = 3


class ExclusiveServer(http.server.ThreadingHTTPServer):
    """Refuses to share a port. The stock server sets SO_REUSEADDR, which on Windows lets a
    second process bind a port another program is already using (both then 'listen')."""
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def serve(port):
    handler = functools.partial(Handler, directory=str(ROOT))
    try:
        httpd = ExclusiveServer(("127.0.0.1", port), handler)
    except OSError as e:
        log(f"cannot listen on 127.0.0.1:{port} (in use by another program?): {e}")
        return EXIT_PORT_IN_USE
    try:
        RUNTIME.mkdir(exist_ok=True)
        STATE_FILE.write_text(json.dumps({"app": APP_ID, "port": port, "pid": os.getpid(), "root": str(ROOT), "started": STARTED}), encoding="utf-8")
    except OSError:
        pass
    log(f"BTC Macro Dashboard: http://127.0.0.1:{port}/{ENTRY}")
    with httpd:
        httpd.serve_forever()
    return 0


# ---------------- launcher ----------------
def health(port, timeout=1.5):
    """'ours' | 'other' (something else answers) | 'free' (nothing listening)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as r:
            info = json.loads(r.read().decode("utf-8"))
        same = info.get("app") == APP_ID and os.path.normcase(os.path.realpath(info.get("root", ""))) == os.path.normcase(str(ROOT))
        return "ours" if same else "other"
    except urllib.error.HTTPError:
        return "other"
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError, ValueError):
        pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:   # nothing answered: is the port free?
        s.settimeout(0.5)
        return "other" if s.connect_ex(("127.0.0.1", port)) == 0 else "free"


def background_python():
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return str(pyw) if os.name == "nt" and pyw.exists() else str(exe)


def fail(msg):
    log(f"launcher: {msg}")
    sys.stderr.write(f"\nBTC Macro Dashboard could not start:\n  {msg}\n\nLog file: {LOG_FILE}\n")
    return 1


def launch(open_browser=True):
    candidates = []
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        candidates.append(int(state["port"]))
    except Exception:
        pass
    candidates += [p for p in PORT_RANGE if p not in candidates]
    status = {}
    for port in candidates:
        status[port] = health(port)
        if status[port] == "ours":
            url = f"http://127.0.0.1:{port}/{ENTRY}"
            log(f"launcher: reusing running dashboard server on port {port}")
            if open_browser:
                webbrowser.open(url)
            print(url)
            return 0
    free = [p for p in PORT_RANGE if status.get(p) == "free"]
    if not free:
        return fail(f"ports {PORT_RANGE.start}-{PORT_RANGE.stop - 1} are all used by other programs. Close one of them and try again.")
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for port in free:   # a port can be taken between the check and the bind: then try the next one
        if port != DEFAULT_PORT:
            log(f"launcher: port {DEFAULT_PORT} is used by another program; trying {port}. Browser-saved data is per port.")
        try:
            proc = subprocess.Popen([background_python(), str(ROOT / "serve.py"), "--port", str(port)], cwd=str(ROOT),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=flags, close_fds=True)
        except OSError as e:
            return fail(f"could not start the background server: {e}")
        deadline = time.time() + 20
        while time.time() < deadline:
            code = proc.poll()
            if code == EXIT_PORT_IN_USE:
                break                                     # next free port
            if code is not None:
                return fail(f"the background server exited (code {code}). See the log for details.")
            state = health(port)
            if state == "ours":
                url = f"http://127.0.0.1:{port}/{ENTRY}"
                log(f"launcher: started server pid {proc.pid} on port {port}")
                if open_browser:
                    webbrowser.open(url)
                print(url)
                return 0
            time.sleep(0.25)
        else:
            proc.kill()
            return fail("the server did not pass its health check within 20 seconds.")
    return fail(f"no usable port in {PORT_RANGE.start}-{PORT_RANGE.stop - 1}; other programs took them during startup.")


def stop():
    """Stop this project's background server (identity and PID verified; other programs are never touched)."""
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        port, pid = int(state["port"]), int(state["pid"])
    except Exception:
        print("No running dashboard server recorded.")
        return 0
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            info = json.loads(r.read().decode("utf-8"))
    except Exception:
        print("No running dashboard server found.")
        return 0
    if health(port) != "ours" or info.get("pid") != pid:
        print(f"Port {port} is not this dashboard's server; nothing stopped.")
        return 0
    os.kill(pid, 15)
    log(f"stopped server pid {pid} on port {port}")
    print(f"Stopped dashboard server (pid {pid}, port {port}).")
    return 0


# ---------------- snapshot mode ----------------
# Writes every allow-listed source to static files (for hosting without this server):
#   <dir>/fred/<ID>.csv   <dir>/src/<key>.json   <dir>/manifest.json
# Uses the same URLs, fetch_upstream() and validators as the relay, plus a structural check so a
# well-formed but empty/wrong response can never replace a good file. A failed source keeps its
# previous file; if there is none and --fallback-url is given (CI), the currently published copy
# is downloaded, validated the same way, and kept. Nothing is ever invented.
SNAPSHOT_FRED_DAYS = 730            # same default history window as the relay
SNAPSHOT_HOST_GAP_S = 1.5           # pause between requests to the same host (CoinGecko throttles)
YAHOO_SYMBOLS = {"yahoo-dxy": "DX-Y.NYB", "yahoo-qqq": "QQQ", "yahoo-tnx": "^TNX"}


def snapshot_validator(kind, key):
    """Relay validator + minimal structure check for files that will be published."""
    if kind == "fred":
        header = validate_fred(key)

        def check_fred(text):
            header(text)
            rows = [ln.split(",") for ln in text.strip().splitlines()[1:]]
            values = [r[1] for r in rows if len(r) == 2 and r[1] not in ("", ".")]
            if not values:
                raise ValueError("no observations")
            for v in values:
                float(v)            # ValueError for non-numeric values
        return check_fred

    def check_json(text):
        validate_json(text)
        j = json.loads(text)
        if key in YAHOO_SYMBOLS:
            res = (j.get("chart") or {}).get("result") or []
            if not res or (res[0].get("meta") or {}).get("symbol") != YAHOO_SYMBOLS[key]:
                raise ValueError(f"not a {YAHOO_SYMBOLS[key]} chart")
        elif key in ("binance-ticker", "binance-premium"):
            if not isinstance(j, dict) or j.get("symbol") != "BTCUSDT":
                raise ValueError("not BTCUSDT")
        elif key in ("binance-klines", "binance-funding", "coingecko-stablecoins", "coingecko-bitcoin"):
            if not isinstance(j, list) or not j:
                raise ValueError("empty list")
        elif key == "coingecko-global":
            if not isinstance(j, dict) or "data" not in j:
                raise ValueError("missing data object")
    return check_json


def snapshot_plan(today=None):
    cosd = ((today or dt.date.today()) - dt.timedelta(days=SNAPSHOT_FRED_DAYS)).isoformat()
    plan = [("fred", sid, f"fred/{sid}.csv", FRED_CSV.format(sid=sid, cosd=cosd)) for sid in sorted(FRED_IDS)]
    plan += [("src", key, f"src/{key}.json", url) for key, (url, _memo_s) in SOURCES.items()]
    return plan


def _write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    tmp.replace(path)


def snapshot(out_dir, fallback_url=None, today=None, sleep=time.sleep):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    try:
        previous = json.loads((out / "manifest.json").read_text(encoding="utf-8")).get("sources", {})
    except Exception:
        previous = {}
    published = None
    if fallback_url:
        try:
            text, _ = fetch_upstream(fallback_url.rstrip("/") + "/manifest.json", validate_json)
            published = json.loads(text).get("sources", {})
        except Exception as e:      # first deploy: nothing published yet
            log(f"snapshot: no published manifest at {fallback_url}: {e}")
            published = {}
    entries, last_hit = {}, {}
    for kind, key, rel, url in snapshot_plan(today):
        host = urllib.request.urlparse(url).hostname
        wait = SNAPSHOT_HOST_GAP_S - (time.time() - last_hit.get(host, 0))
        if wait > 0:
            sleep(wait)
        validate = snapshot_validator(kind, key)
        path = out / rel
        entry = {"kind": kind, "file": rel, "attempted_at": now_iso()}
        try:
            body, last_modified = fetch_upstream(url, validate)
            _write_atomic(path, body)
            entry.update(ok=True, status="fresh", retrieved_at=now_iso(), last_modified=last_modified)
        except RelayError as err:
            entry.update(ok=False, error_kind=err.kind, error=err.message, http_status=err.status)
            prev = previous.get(key) or {}
            kept = False
            if path.exists():
                try:
                    validate(path.read_text(encoding="utf-8"))
                    entry.update(status="kept-previous", retrieved_at=prev.get("retrieved_at"), last_modified=prev.get("last_modified"))
                    kept = True
                except (OSError, ValueError):
                    log(f"snapshot: previous {rel} is invalid; not kept")
            if not kept and published is not None and (published.get(key) or {}).get("retrieved_at"):
                try:
                    body, _ = fetch_upstream(fallback_url.rstrip("/") + "/" + rel, validate)
                    _write_atomic(path, body)
                    entry.update(status="published-fallback", retrieved_at=published[key]["retrieved_at"],
                                 last_modified=published[key].get("last_modified"))
                    kept = True
                except (RelayError, ValueError, OSError) as e2:
                    entry["fallback_error"] = f"{getattr(e2, 'kind', 'malformed')}: {getattr(e2, 'message', e2)}"
            if not kept:
                entry.update(status="missing", retrieved_at=None)
        finally:
            last_hit[host] = time.time()
        if path.exists() and entry.get("status") != "missing":
            data = path.read_bytes()
            entry.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        entries[key] = entry
    manifest = {"app": APP_ID, "generated_at": now_iso(), "fred_cosd_days": SNAPSHOT_FRED_DAYS, "sources": entries}
    _write_atomic(out / "manifest.json", json.dumps(manifest, indent=1))
    return manifest


def print_snapshot_summary(manifest):
    rows = manifest["sources"]
    for key, e in rows.items():
        detail = "" if e["ok"] else f"  [{e.get('error_kind')}{' ' + str(e['http_status']) if e.get('http_status') else ''}: {e.get('error')}]"
        print(f"  {key:22s} {e['status']:19s} retrieved {e.get('retrieved_at') or '—'}{detail}")
    fresh = sum(e["ok"] for e in rows.values())
    missing = [k for k, e in rows.items() if e["status"] == "missing"]
    print(f"  {fresh}/{len(rows)} fresh; missing: {', '.join(missing) or 'none'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--snapshot", metavar="DIR", help="write every allow-listed source to DIR (files + manifest.json) and exit")
    ap.add_argument("--fallback-url", metavar="URL", help="with --snapshot: published data/ URL to use when a fetch fails and DIR has no previous file")
    ap.add_argument("--launch", action="store_true", help="reuse or start the background server and open the dashboard")
    ap.add_argument("--no-browser", action="store_true", help="with --launch: do not open a browser (for tests)")
    ap.add_argument("--stop", action="store_true", help="stop this project's background server")
    args = ap.parse_args()
    if args.snapshot:
        manifest = snapshot(args.snapshot, args.fallback_url)
        print_snapshot_summary(manifest)
        # Partial results are normal (a provider may refuse); fail only if nothing usable exists.
        sys.exit(0 if any(e["status"] != "missing" for e in manifest["sources"].values()) else 2)
    if args.fallback_url:
        ap.error("--fallback-url needs --snapshot")
    if args.stop:
        sys.exit(stop())
    if args.launch:
        sys.exit(launch(open_browser=not args.no_browser))
    sys.exit(serve(args.port))


if __name__ == "__main__":
    main()
