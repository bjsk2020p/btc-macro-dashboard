# Changelog

All notable changes to the BTC Macro Dashboard. The version source is `VERSION`; see
`VERSIONING.md`. Releases are created only on explicit request.

Earlier history predates this changelog and is not reconstructed here.

## [Unreleased]

### Added

- `Open_BTC_Dashboard.cmd` one-click launcher plus `serve.py --launch`. It reuses or starts a hidden
  background server on 127.0.0.1, waits for `/api/health`, and opens the localhost URL. It handles
  paths with spaces, any working directory, busy ports (8765–8775) without touching other
  programs, and a missing Python (clear message). `serve.py --stop` stops only this project's
  server.
- Allow-listed relay adapters for Binance, CoinGecko and Yahoo (fixed URLs only), a validated
  last-good cache in `.runtime/cache/` served with explicit headers when a provider fails,
  Retry-After handling, and a per-host cooldown after HTTP 429.
- Per-source refresh engine: saved data renders first, every source is requested on each
  opening, and values update progressively. One 30 s scheduler with per-source intervals,
  refresh on tab-visible/online, one request per source at a time, and backoff.
- Source line on each card (provider, instrument, observation time, Live / Latest / Cached /
  Stale / Unavailable / Manual, with link, retrieval time and last error on hover). Header pill
  with per-group ✓ / ◐ / ✕. A `file://` notice that names the launcher and links to a running
  server.
- Real chart data: DXY closes, the FRED 10Y−2Y spread on common dates, BTC/QQQ by date, and
  Binance funding history. The halving month and ATH drawdown are computed.
- Chart.js 4.4.1 vendored in `vendor/` (sha512 verified against the cdnjs SRI and jsDelivr).
- Tests: `tests/sources_core.test.mjs` (saved provider responses) and `tests/test_serve.py`
  (relay, cache, cooldown, ports).
- Guide section at the bottom with three tabs (Overview, Help & Parameters, References & Links).
  Vanilla JS; click the open tab again to close it; arrow keys move between tabs; 0.3 s fade.
  The parameter help states the dashboard's actual thresholds and equal weights (TGA included,
  open interest marked as not tracked). The "M2 leads BTC 8–12 weeks" idea is described as an
  unverified hypothesis. All 22 reference links open in a new tab.

### Fixed

- Data missing when the HTML was opened directly (`file://`). The page skipped the relay, FRED
  and Yahoo send no CORS headers, and the browser cache belongs to a different origin, so every
  FRED series showed "request failed and no valid cache".
- If the Chart.js CDN could not load (offline), `Chart is not defined` stopped initialisation,
  and every section stayed on "Loading" with no data requests made.
- Yahoo (DXY, QQQ, ^TNX) never loaded in the browser: no CORS, corsproxy.io 401, allorigins
  aborted. Those public proxies are removed.
- Spot price, candles and funding were one `Promise.all`, so a funding failure discarded the BTC
  price.
- On Windows the stock server's `SO_REUSEADDR` let the dashboard bind a port another program was
  already using. It now binds exclusively and the launcher moves to the next port.
- Funding badge and score compared a decimal rate with thresholds written in percent (e.g.
  `-0.005` meant −0.5% per 8h). The original page displayed −0.01% as "Squeeze Setup", which
  only fits percent thresholds. The rate is now converted to percent before comparison;
  the threshold values are unchanged.
- Hardcoded sample charts (spread, dominance, SSR, MVRV, funding, BTC master) and fixed "current"
  figures (Month 24, ATH drawdown, "Safe (98.3)", "46+ days negative", the "Accumulate (now)"
  highlight) were removed or computed.
- TradingView quick-launch links had `interval=—`, an earlier regex slip; the intervals are
  restored.

## [3.0.0] - 2026-11-15

### Type

First release (`unversioned` → `3.0.0`), numbered by the user to continue the dashboard's
"v3" page title. It was briefly numbered 1.0.0 the same day, before any commit or tag. The
snapshot was moved unchanged from `Releases/v1.0.0/` (hashes verified), and no 1.0.0 release
exists. The "v2" in the filename is a legacy label.

### Artifact

- `Releases/v3.0.0/btc_dashboard_v2.html`, SHA-256 `19b797d5d92b2007517768342d524cb06790883f1ae96fca78ae5644faaf4409`
- `Releases/v3.0.0/serve.py`, SHA-256 `3b50dc12d2e284450dd3172c363e6d8bfe46cf38665668d6565ee4431f6adfd1`

### Verification

- `node --test tests/l1_core.test.mjs`: 12/12 passed.
- Active file and snapshot both served by `python serve.py`. See the release report for results.

### Added

- Layer 1 12-month liquidity history table (M2, M2 trend, NLM, RRP, TGA, real yield, WALCL,
  L1 signal) built from month-end FRED observations. It has sortable columns and threshold
  badges, the current row follows live and **Set** values, and the last good history is cached
  in localStorage when FRED fails.
- Versioning scheme: `VERSION` (currently `unversioned`), `VERSIONING.md`, this changelog, and
  `archive/html-dashboards/` with a manifest. No dashboards needed archiving.

- `serve.py`: local server plus an allow-listed FRED relay with timeout and bounded retries,
  because FRED sends no CORS headers. No API key is needed.
- Layer 1 data layer: validated CSV parsing, Federal Reserve holiday and publication calendars,
  per-series freshness (Latest available / Cached / Stale / Unavailable / Not yet published),
  due-based refresh on load and every 15 minutes, and a validated localStorage cache.
- Clicking a value opens a source panel with provider, series link, observation date, units and
  conversion, update and retrieval times, status, and formula components. There is also a
  "Clear manual values" control.
- `DATA_SOURCES.md` (methodology) and `tests/l1_core.test.mjs` with official FRED fixtures.

### Changed

- Removed the six per-indicator Layer 1 sparklines; the history table replaces them.
- Net liquidity is now computed only from WALCL, WDTGAL and RRPONTSYD on the same Wednesday
  (it previously used the nearest earlier value). Historical cells use the last observation
  within the month; monthly M2 uses that month's own observation.
- The current row shows each metric's latest observation with its own date.
- L1 score and L1 card badges now use the table thresholds (bull 2 / neutral 1 / bear 0 over
  available inputs). They replace the separate level thresholds (e.g. WALCL > $7.5T).
- The composite score needs ≥2 scored layers, and partial coverage is disclosed.
- Removed the unsupported "leads BTC 8–12 weeks" claim and hardcoded narrative numbers from the
  Layer 1 cards. The NLM alert is now computed.
- Removed the broken public CORS proxies and DBnomics fallback for FRED.

### Fixed

- FRED never loaded in the browser (CORS block; corsproxy.io 401, allorigins unreachable,
  DBnomics 404). The current row and score showed hardcoded seed values (e.g. TGA $835B,
  RRP $0.15B, real yield 2.39%).
- Months without a published M2 value showed the previous month's value carried forward.
- Missing inputs no longer count as neutral or produce an apparently complete score. Seed values
  for all indicators (including MVRV) were removed.
- The NLM card showed `+-0.11` for negative values.
- Indicator card grids caused horizontal page scroll at ~1000 px width.
