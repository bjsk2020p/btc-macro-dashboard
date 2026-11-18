"""Tests for serve.py (standard library only).  Run:  python -m unittest tests/test_serve.py -v"""
import email.message
import functools
import http.server
import importlib.util
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("serve", ROOT / "serve.py")
serve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(serve)
REAL_SOURCES = dict(serve.SOURCES)      # the relay tests below replace serve.SOURCES


class FakeResp(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(body.encode())
        self.headers = headers or {}
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def http_error(code, retry_after=None):
    h = email.message.Message()
    if retry_after is not None:
        h["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("http://x", code, "err", h, io.BytesIO(b""))


def scripted(*steps):
    calls = []
    def opener(req, timeout):
        step = steps[len(calls)]
        calls.append(req.full_url)
        if isinstance(step, Exception):
            raise step
        return FakeResp(step)
    return opener, calls


class FetchUpstream(unittest.TestCase):
    def test_retries_5xx_then_succeeds(self):
        opener, calls = scripted(http_error(503), http_error(502), '{"ok":1}')
        sleeps = []
        body, _ = serve.fetch_upstream("http://x", serve.validate_json, opener, sleeps.append)
        self.assertEqual(body, '{"ok":1}'); self.assertEqual(len(calls), 3); self.assertEqual(sleeps, [1, 2])

    def test_short_retry_after_is_honoured(self):
        opener, calls = scripted(http_error(429, 2), '{"ok":1}')
        sleeps = []
        serve.fetch_upstream("http://x", serve.validate_json, opener, sleeps.append)
        self.assertEqual(sleeps, [2]); self.assertEqual(len(calls), 2)

    def test_long_retry_after_is_returned_not_waited(self):
        opener, calls = scripted(http_error(429, 120))
        with self.assertRaises(serve.RelayError) as cm:
            serve.fetch_upstream("http://x", serve.validate_json, opener, lambda s: self.fail("must not sleep"))
        self.assertEqual((cm.exception.kind, cm.exception.status, cm.exception.retry_after), ("rate-limit", 429, 120))
        self.assertEqual(len(calls), 1)

    def test_auth_and_not_found_are_not_retried(self):
        for code, kind in ((401, "auth"), (404, "http")):
            opener, calls = scripted(http_error(code))
            with self.assertRaises(serve.RelayError) as cm:
                serve.fetch_upstream("http://x", serve.validate_json, opener, lambda s: None)
            self.assertEqual(cm.exception.kind, kind); self.assertEqual(len(calls), 1)

    def test_timeouts_are_bounded(self):
        opener, calls = scripted(TimeoutError(), TimeoutError(), TimeoutError())
        with self.assertRaises(serve.RelayError) as cm:
            serve.fetch_upstream("http://x", serve.validate_json, opener, lambda s: None)
        self.assertEqual(cm.exception.kind, "timeout"); self.assertEqual(len(calls), serve.ATTEMPTS)

    def test_malformed_body_is_rejected(self):
        opener, _ = scripted("<html>captcha</html>")
        with self.assertRaises(serve.RelayError) as cm:
            serve.fetch_upstream("http://x", serve.validate_json, opener, lambda s: None)
        self.assertEqual(cm.exception.kind, "malformed")
        opener, _ = scripted("observation_date,OTHER\n2026-01-01,1\n")
        with self.assertRaises(serve.RelayError):
            serve.fetch_upstream("http://x", serve.validate_fred("WALCL"), opener, lambda s: None)

    def test_retry_after_parsing(self):
        self.assertEqual(serve.parse_retry_after("30"), 30)
        self.assertIsNone(serve.parse_retry_after("soon"))
        self.assertIsNone(serve.parse_retry_after(None))


class Upstream(http.server.BaseHTTPRequestHandler):
    mode = {"status": 200, "body": '{"v":1}'}
    def do_GET(self):
        m = Upstream.mode
        self.send_response(m["status"])
        if m["status"] == 429:
            self.send_header("Retry-After", "90")
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(m["body"].encode())
    def log_message(self, *a):
        pass


def start(server):
    t = threading.Thread(target=server.serve_forever, daemon=True); t.start()
    return server


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()


class RelayServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        serve.CACHE_DIR = Path(cls.tmp.name) / "cache"
        serve.RUNTIME = Path(cls.tmp.name)
        serve.LOG_FILE = Path(cls.tmp.name) / "serve.log"
        cls.up = start(http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream))
        serve.SOURCES = {"test-src": (f"http://127.0.0.1:{cls.up.server_address[1]}/x", 0)}
        serve.ATTEMPTS = 1
        cls.app = start(http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(serve.Handler, directory=str(serve.ROOT))))
        cls.base = f"http://127.0.0.1:{cls.app.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.app.shutdown(); cls.up.shutdown(); cls.tmp.cleanup()

    def setUp(self):
        serve._cooldown.clear(); serve._memo.clear()   # each test starts without rate-limit state

    def test_health_identifies_this_app_and_project(self):
        code, headers, body = get(self.base + "/api/health")
        info = json.loads(body)
        self.assertEqual((code, info["app"], info["root"]), (200, "btc-macro-dashboard", str(serve.ROOT)))
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "*")
        self.assertEqual(serve.health(self.app.server_address[1]), "ours")

    def test_only_allow_listed_sources(self):
        self.assertEqual(get(self.base + "/api/src/evil")[0], 404)
        self.assertEqual(get(self.base + "/api/fred/GDP")[0], 404)
        self.assertEqual(get(self.base + "/api/src/http%3A%2F%2Fexample.com")[0], 404)
        code, headers, _ = get(self.base + "/api/src/test-src")
        self.assertNotIn("Access-Control-Allow-Origin", headers)   # data endpoints are same-origin only

    def test_success_then_failure_serves_marked_last_good_copy(self):
        Upstream.mode = {"status": 200, "body": '{"v":42}'}
        code, h1, body = get(self.base + "/api/src/test-src")
        self.assertEqual((code, json.loads(body)["v"]), (200, 42)); self.assertNotIn("X-Relay-Cache", h1)
        serve._memo.clear()
        Upstream.mode = {"status": 429, "body": '{"msg":"slow down"}'}
        code, h2, body = get(self.base + "/api/src/test-src")
        self.assertEqual((code, json.loads(body)["v"]), (200, 42))
        self.assertEqual(h2["X-Relay-Cache"], "last-good")
        self.assertEqual(h2["X-Retrieved-At"], h1["X-Retrieved-At"])     # failure does not renew the retrieval time
        self.assertIn("429", h2["X-Upstream-Error"]); self.assertEqual(h2["X-Retry-After"], "90")

    def test_failure_without_cache_is_an_error_with_retry_after(self):
        serve.SOURCES["test-src2"] = (serve.SOURCES["test-src"][0], 0)
        Upstream.mode = {"status": 429, "body": "{}"}
        code, headers, body = get(self.base + "/api/src/test-src2")
        info = json.loads(body)
        self.assertEqual((code, info["error"], info["retry_after"]), (502, "rate-limit", 90))
        self.assertEqual(headers.get("Retry-After"), "90")

    def test_after_429_the_host_is_not_contacted_until_cooldown_ends(self):
        serve._cooldown.clear(); serve._memo.clear()
        serve.SOURCES["test-src4"] = (serve.SOURCES["test-src"][0], 0)
        Upstream.mode = {"status": 200, "body": '{"v":7}'}
        self.assertEqual(get(self.base + "/api/src/test-src4")[0], 200)
        hits = []
        orig = serve.fetch_upstream
        serve.fetch_upstream = lambda *a, **k: (hits.append(1), orig(*a, **k))[1]
        try:
            Upstream.mode = {"status": 429, "body": "{}"}
            code, h, _ = get(self.base + "/api/src/test-src4")           # one upstream call, then cooldown
            self.assertEqual((code, h["X-Relay-Cache"]), (200, "last-good"))
            Upstream.mode = {"status": 200, "body": '{"v":8}'}
            code, h, body = get(self.base + "/api/src/test-src4")        # served from last-good, upstream not called
            self.assertEqual((code, h["X-Relay-Cache"], json.loads(body)["v"]), (200, "last-good", 7))
            self.assertEqual(len(hits), 1)
            self.assertIn("pausing requests", h["X-Upstream-Error"])
        finally:
            serve.fetch_upstream = orig
            serve._cooldown.clear()

    def test_corrupt_cache_is_ignored(self):
        serve.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        serve.cache_path("src:test-src3").write_text("{not json", encoding="utf-8")
        serve.SOURCES["test-src3"] = (serve.SOURCES["test-src"][0], 0)
        Upstream.mode = {"status": 500, "body": "{}"}
        self.assertEqual(get(self.base + "/api/src/test-src3")[0], 502)

    def test_internal_folders_are_not_served(self):
        for p in ("/.runtime/server.json", "/.git/config", "/.RUNTIME/x", "/.remember/now.md"):
            self.assertEqual(get(self.base + p)[0], 404, p)


FIX = ROOT / "tests" / "fixtures"
FRED_FIXTURE = {sid: (FIX / f"{sid}.csv").read_text(encoding="utf-8") for sid in ("M2SL", "WALCL", "WDTGAL", "RRPONTSYD", "DFII10")}
FRED_FIXTURE["DGS10"] = "observation_date,DGS10\n2026-11-11,\n2026-11-12,5.10\n2026-11-13,5.15\n"
FRED_FIXTURE["DGS2"] = "observation_date,DGS2\n2026-11-12,4.86\n2026-11-13,4.90\n"
SRC_FIXTURE = {key: (FIX / "sources" / f"{key}.json").read_text(encoding="utf-8") for key in REAL_SOURCES}


class Snapshot(unittest.TestCase):
    """serve.snapshot() against the saved fixtures; upstream and the published site are faked."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "data"
        self.saved = serve.fetch_upstream, serve.RUNTIME, serve.LOG_FILE, serve.SOURCES
        serve.SOURCES = dict(REAL_SOURCES)
        serve.RUNTIME = Path(self.tmp.name) / "rt"; serve.LOG_FILE = serve.RUNTIME / "serve.log"
        self.fail = {}          # key -> RelayError for that upstream
        self.published = {}     # rel path -> body served by the fake published site
        self.calls = []

        def fake(url, validate, *a, **k):
            self.calls.append(url)
            if url.startswith("https://site.example/data/"):
                rel = url[len("https://site.example/data/"):]
                if rel not in self.published:
                    raise serve.RelayError("http", "HTTP 404", 404)
                try:
                    validate(self.published[rel])
                except ValueError as e:          # like the real fetch_upstream
                    raise serve.RelayError("malformed", f"unexpected response: {e}")
                return self.published[rel], None
            key = next((s for s in FRED_FIXTURE if f"id={s}&" in url), None) or next(k2 for k2, (u, _) in serve.SOURCES.items() if u == url)
            if key in self.fail:
                raise self.fail[key]
            body = FRED_FIXTURE.get(key) or SRC_FIXTURE[key]
            try:
                validate(body)
            except ValueError as e:
                raise serve.RelayError("malformed", f"unexpected response: {e}")
            return body, "Thu, 12 Nov 2026 21:32:59 GMT" if key in FRED_FIXTURE else None
        serve.fetch_upstream = fake

    def tearDown(self):
        serve.fetch_upstream, serve.RUNTIME, serve.LOG_FILE, serve.SOURCES = self.saved
        self.tmp.cleanup()

    def run_snapshot(self, **kw):
        return serve.snapshot(self.dir, sleep=lambda s: None, **kw)

    def test_all_sources_fresh_files_match_fixtures(self):
        m = self.run_snapshot()
        self.assertEqual(len(m["sources"]), len(serve.FRED_IDS) + len(serve.SOURCES))
        for key, e in m["sources"].items():
            self.assertEqual((e["ok"], e["status"]), (True, "fresh"), key)
            body = (self.dir / e["file"]).read_text(encoding="utf-8")
            self.assertEqual(body, FRED_FIXTURE.get(key) or SRC_FIXTURE[key])
            self.assertEqual(len(e["sha256"]), 64)
        self.assertEqual(m["sources"]["WALCL"]["last_modified"], "Thu, 12 Nov 2026 21:32:59 GMT")
        self.assertEqual(json.loads((self.dir / "manifest.json").read_text())["sources"].keys(), m["sources"].keys())
        self.assertFalse(list(self.dir.rglob("*.tmp")))

    def test_failed_source_keeps_previous_good_file_and_its_time(self):
        first = self.run_snapshot()
        t0 = first["sources"]["yahoo-dxy"]["retrieved_at"]
        before = (self.dir / "src/yahoo-dxy.json").read_bytes()
        self.fail["yahoo-dxy"] = serve.RelayError("rate-limit", "upstream returned HTTP 429", 429, 60)
        self.fail["WALCL"] = serve.RelayError("network", "cannot reach upstream")
        m = self.run_snapshot()
        e = m["sources"]["yahoo-dxy"]
        self.assertEqual((e["ok"], e["status"], e["error_kind"], e["http_status"]), (False, "kept-previous", "rate-limit", 429))
        self.assertEqual(e["retrieved_at"], t0)                       # failure never renews the time
        self.assertEqual((self.dir / "src/yahoo-dxy.json").read_bytes(), before)
        self.assertEqual(m["sources"]["WALCL"]["status"], "kept-previous")
        self.assertEqual(m["sources"]["yahoo-qqq"]["status"], "fresh")  # others unaffected

    def test_malformed_upstream_never_replaces_good_file(self):
        self.run_snapshot()
        before = (self.dir / "fred/M2SL.csv").read_bytes()
        FRED_FIXTURE_ORIG = FRED_FIXTURE["M2SL"]
        FRED_FIXTURE["M2SL"] = "observation_date,M2SL\n2026-09-01,.\n"          # header ok, no values
        try:
            m = self.run_snapshot()
        finally:
            FRED_FIXTURE["M2SL"] = FRED_FIXTURE_ORIG
        self.assertEqual((m["sources"]["M2SL"]["status"], m["sources"]["M2SL"]["error_kind"]), ("kept-previous", "malformed"))
        self.assertEqual((self.dir / "fred/M2SL.csv").read_bytes(), before)

    def test_invalid_previous_file_is_not_kept(self):
        (self.dir / "src").mkdir(parents=True)
        (self.dir / "src/yahoo-qqq.json").write_text('{"chart":{"result":[{"meta":{"symbol":"SPY"}}]}}')
        self.fail["yahoo-qqq"] = serve.RelayError("http", "upstream returned HTTP 503", 503)
        m = self.run_snapshot()
        self.assertEqual(m["sources"]["yahoo-qqq"]["status"], "missing")
        self.assertIsNone(m["sources"]["yahoo-qqq"]["retrieved_at"])

    def test_ci_fallback_uses_validated_published_copy(self):
        self.published["manifest.json"] = json.dumps({"sources": {
            "yahoo-tnx": {"retrieved_at": "2026-11-17T10:00:00+00:00"},
            "yahoo-qqq": {"retrieved_at": "2026-11-17T10:00:00+00:00"},
            "DGS2": {"retrieved_at": "2026-11-17T10:00:00+00:00"}}})
        self.published["src/yahoo-tnx.json"] = SRC_FIXTURE["yahoo-tnx"]
        self.published["src/yahoo-qqq.json"] = SRC_FIXTURE["yahoo-dxy"]          # wrong instrument: must be rejected
        self.published["fred/DGS2.csv"] = "observation_date,DGS2\n2026-11-13,abc\n"  # non-numeric: must be rejected
        for k in ("yahoo-tnx", "yahoo-qqq", "DGS2", "binance-ticker"):
            self.fail[k] = serve.RelayError("http", "upstream returned HTTP 451", 451)
        m = self.run_snapshot(fallback_url="https://site.example/data")
        s = m["sources"]
        self.assertEqual((s["yahoo-tnx"]["status"], s["yahoo-tnx"]["retrieved_at"]), ("published-fallback", "2026-11-17T10:00:00+00:00"))
        self.assertEqual((self.dir / "src/yahoo-tnx.json").read_text(), SRC_FIXTURE["yahoo-tnx"])
        self.assertEqual(s["yahoo-qqq"]["status"], "missing"); self.assertIn("fallback_error", s["yahoo-qqq"])
        self.assertEqual(s["DGS2"]["status"], "missing")
        self.assertEqual(s["binance-ticker"]["status"], "missing")            # not in the published manifest
        self.assertFalse((self.dir / "src/yahoo-qqq.json").exists())

    def test_first_ci_run_without_published_site(self):
        self.fail["yahoo-dxy"] = serve.RelayError("http", "upstream returned HTTP 451", 451)
        m = self.run_snapshot(fallback_url="https://site.example/data")       # published manifest 404
        self.assertEqual(m["sources"]["yahoo-dxy"]["status"], "missing")
        self.assertEqual(m["sources"]["yahoo-tnx"]["status"], "fresh")

    def test_structural_validator(self):
        bad = [("fred", "M2SL", "observation_date,M2SL\n"), ("fred", "M2SL", "observation_date,M2SL\n2026-01-01,x\n"),
               ("src", "binance-klines", "[]"), ("src", "binance-ticker", '{"symbol":"ETHUSDT"}'),
               ("src", "coingecko-global", '{"status":{"error_code":429}}'), ("src", "yahoo-tnx", SRC_FIXTURE["yahoo-qqq"])]
        for kind, key, body in bad:
            with self.assertRaises(ValueError, msg=(key, body[:30])):
                serve.snapshot_validator(kind, key)(body)
        serve.snapshot_validator("fred", "DGS10")(FRED_FIXTURE["DGS10"])         # blanks allowed if values exist

    def test_cli_exit_code_when_everything_missing(self):
        for k in list(FRED_FIXTURE) + list(serve.SOURCES):
            self.fail[k] = serve.RelayError("network", "offline")
        m = self.run_snapshot()
        self.assertTrue(all(e["status"] == "missing" for e in m["sources"].values()))
        import subprocess, sys
        r = subprocess.run([sys.executable, str(ROOT / "serve.py"), "--fallback-url", "x"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)                                   # --fallback-url needs --snapshot


class LauncherPortChecks(unittest.TestCase):
    def test_other_program_on_port_is_not_mistaken_for_dashboard(self):
        other = start(http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream))
        try:
            Upstream.mode = {"status": 200, "body": '{"app":"something-else"}'}
            self.assertEqual(serve.health(other.server_address[1]), "other")
        finally:
            other.shutdown()

    def test_server_refuses_a_port_another_program_listens_on(self):
        # Regression: the stock server's SO_REUSEADDR let it share a busy port on Windows.
        import socket
        other = socket.socket(); other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        other.bind(("127.0.0.1", 0)); other.listen(); port = other.getsockname()[1]
        try:
            with self.assertRaises(OSError):
                serve.ExclusiveServer(("127.0.0.1", port), serve.Handler)
        finally:
            other.close()

    def test_free_port_detected(self):
        import socket
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        self.assertEqual(serve.health(port), "free")


if __name__ == "__main__":
    unittest.main()
