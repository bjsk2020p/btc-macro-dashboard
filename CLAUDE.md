# BTC Macro Dashboard

## Project shape

- The app is a standalone static dashboard in `btc_dashboard_v2.html`.
- It has no build step or package manager. Users open it by double-clicking `Open_BTC_Dashboard.cmd`, which runs `serve.py --launch`: reuse or start a hidden background server on 127.0.0.1 (8765–8775, exclusive bind, never touches other programs), wait for `/api/health`, open the localhost URL. `python serve.py` runs the server in the foreground; `python serve.py --stop` stops this project's server.
- `serve.py` (standard library only) relays only allow-listed FRED series and fixed market URLs (no arbitrary URLs), keeps a validated last-good cache in `.runtime/`, and pauses a provider after HTTP 429.
- `python serve.py --snapshot DIR [--fallback-url URL]` writes every allow-listed source to static files (`fred/<ID>.csv`, `src/<key>.json`, `manifest.json`) for hosting without the server. It uses the same URLs, fetch and validators plus a structural check. A failed source keeps its previous valid file (or, in CI, the validated published copy) and is marked in the manifest; it is never overwritten with bad data.
- Chart.js 4.4.1 is vendored in `vendor/` (sha512 = cdnjs SRI). Market-feed parsing lives in `<script id="sources-core">` (no DOM/network).
- Tests: `node --test tests/l1_core.test.mjs tests/sources_core.test.mjs` (name the files; the folder form fails on Windows) and `python -m unittest tests/test_serve.py`.
- Layer 1 data logic lives in the self-contained `<script id="l1-core">` block. Keep it free of DOM and network code. Fixtures are in `tests/fixtures/` (official FRED CSVs and saved provider responses).
- `DATA_SOURCES.md` is the source inventory and methodology for every automatic metric. Update it when a source, unit, formula, threshold, interval or fallback changes.

## Live data contract

- On every opening, saved data renders immediately (labelled Cached), then every automatic source is requested and updates the page as it arrives. A single 30 s scheduler refreshes each source by its own interval and on tab-visible/online. `Refresh All` forces every source.
- BTC/USDT spot, 180-day klines, and BTCUSDT perpetual funding (8h) plus its history come from Binance public endpoints. Label BTC as USDT, not USD.
- BTC dominance, stablecoin market caps (SSR) and the BTC ATH come from CoinGecko (keyless, rate-limited).
- RRP (RRPONTSYD), TGA Wednesday level (WDTGAL, not WTREGEN), 10-year real yield (DFII10), the Fed balance sheet (WALCL), US M2 (M2SL), and Treasury yields (DGS10/DGS2) come from FRED's public CSV endpoint through the `serve.py` relay (`/api/fred/<ID>`, allow-listed IDs only); no API key is required. Never add third-party CORS proxies or non-equivalent mirrors for these series.
- Never combine net-liquidity inputs from different dates. Never interpolate or carry values forward. Never use seed or default numbers as data; missing inputs are "No data" and must not count in scores.
- DXY (DX-Y.NYB), QQQ and ^TNX come from Yahoo Finance's chart endpoint, only through the relay (no CORS).
- Authorised by the user (2026-11-17): FRED and Yahoo may also be read from snapshot files produced by `serve.py --snapshot` (the same allow-list, fetch and validators). Still no third-party CORS proxies or mirrors.
- Net liquidity momentum, the 10Y–2Y spread, BTC/QQQ, and SSR are derived from refreshed source data.
- MVRV remains manual (no verified free automated source). Never chart or display sample data; charts without a real history source say so.

## Refresh behavior invariants

- Never label the dashboard fully live unless Binance, CoinGecko, Yahoo and FRED are all current. Market data may be "Live"; FRED is "Latest published".
- A failed request must never renew the last-success time; one source failing must not discard another source's values.
- A failed source must leave its last known value visible and be identified in the refresh status/title.
- Refresh controls must be re-enabled even if requests throw.
- Derived scores and TradingView quick-launch cards must be rebuilt after refreshed source values are applied.

## Versioning and archives

This project overrides the global executable policy for HTML releases (there are no `.exe` files). Full rules are in `VERSIONING.md`.

- `VERSION` is the single version source (`MAJOR.MINOR.PATCH` or `unversioned`). The `v2` in the filename and the `v3` in the page title are legacy labels, not versions.
- Bump `VERSION`, create `Releases/v<X.Y.Z>/` snapshots, or create git tags **only when the user explicitly asks** for a release or names a version. Routine edits go under `## [Unreleased]` in `CHANGELOG.md`.
- On a release request without a number, infer PATCH/MINOR/MAJOR from the Unreleased changes and state why. Ask if the scope is unclear. Never overwrite an existing `Releases/` snapshot.
- Superseded dashboards go to `archive/html-dashboards/<YYYY-MM-DD_HHMMSS>/` (moved, never deleted or overwritten, hash-verified, logged in `MANIFEST.md`). Never archive the active entry point or its dependencies. Archiving is not a release.
- Git is local only: no commits, tags, remotes or pushes unless the user asks.
- Exception authorised by the user (2026-11-17): for the GitHub Pages hosting work only, Git may use a GitHub remote and push. Other commits, tags and releases still need an explicit request. Snapshot data is never committed; it is deployed by the workflow.
