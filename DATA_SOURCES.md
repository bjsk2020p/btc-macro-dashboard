# Data sources and methodology

How every automatically refreshed number on the dashboard is produced. Endpoints, definitions,
units and live responses were verified on 2026-11-15.

- Code: `serve.py` (local server, launcher, allow-listed relay); in `btc_dashboard_v2.html`, the
  pure blocks `<script id="l1-core">` (FRED/Layer 1) and `<script id="sources-core">`
  (market feeds), plus the page script.
- Tests (name the files; the folder form fails on Windows):
  `node --test tests/l1_core.test.mjs tests/sources_core.test.mjs` and
  `python -m unittest tests/test_serve.py`.

## Opening the dashboard

Double-click **`Open_BTC_Dashboard.cmd`**. It finds Python 3.9+ (`py -3`, then `python`) and runs
`serve.py --launch`, which:

1. Checks `/api/health` on the recorded port and then on 8765–8775, and reuses a server that
   reports this app and this project folder.
2. Otherwise starts `serve.py` in the background with `pythonw` (no console window), bound to
   127.0.0.1. It uses the first free port, skips ports used by other programs, and never stops
   them. On Windows the server binds with exclusive address use, so it cannot share a port with
   another program.
3. Waits for the health check (up to 20 s), then opens
   `http://127.0.0.1:<port>/btc_dashboard_v2.html`.

Errors (no Python, startup failure, no free port) are shown in the launcher window, with the log
in `.runtime/serve.log`. `python serve.py --stop` stops this project's background server only.

Opening the HTML file directly (`file://`) shows a notice at the top explaining the launcher. It
also links to an already-running server if one is found. In that mode only Binance and CoinGecko
can load, because they send CORS headers; FRED and Yahoo cannot. A browser page cannot start
Python itself.

Browser-saved data is stored per port. If 8765 is busy and another port is used, the browser
cache starts empty, but the server's last-good cache (below) still fills it.

## Source inventory

No source needs credentials. FRED and Yahoo send no CORS headers, so the browser reaches them only
through the relay. Binance and CoinGecko also go through the relay when it is running, which
gives Retry-After handling, cooldowns and the last-good cache; under `file://` they are fetched
directly.

| Metric (where shown) | Instrument / series | Provider (link) | Endpoint (relay key) | Native → display | Frequency, delay | Refresh |
|---|---|---|---|---|---|---|
| BTC price, 24h change, high/low (hero, alerts, BTC/QQQ) | BTCUSDT **spot**, quoted in USDT (not USD) | [Binance](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints) | `api/v3/ticker/24hr?symbol=BTCUSDT` (`binance-ticker`) | USDT → shown as "$…" with a USDT label | real time | 1 min |
| BTC 180-day chart, 30-day range | BTCUSDT 1d klines; last bar = today, in progress | Binance | `api/v3/klines?…interval=1d&limit=180` (`binance-klines`) | USDT, closes at 00:00 UTC | daily | 15 min |
| Funding rate (L3) | BTCUSDT USDⓈ-M perpetual, `lastFundingRate`, **8h interval** (`fundingInfo`: 8) | [Binance futures](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Mark-Price) | `fapi/v1/premiumIndex?symbol=BTCUSDT` (`binance-premium`) | decimal → % per 8h | real time | 5 min |
| Funding chart | last 90 settled 8h rates | Binance futures | `fapi/v1/fundingRate?symbol=BTCUSDT&limit=90` (`binance-funding`) | decimal → % | every 8h | 1 h |
| BTC dominance (L3) | `market_cap_percentage.btc` | [CoinGecko](https://docs.coingecko.com/v3.0.1/reference/crypto-global) | `api/v3/global` (`coingecko-global`) | % | ~minutes (`updated_at`) | 10 min |
| SSR (L3) | BTC mcap (global total × BTC.D) ÷ Σ market caps of CoinGecko "stablecoins" category, top 250, USD | CoinGecko | above + `coins/markets?category=stablecoins…` (`coingecko-stablecoins`) | ratio | ~minutes | 10 / 30 min |
| ATH & drawdown (hero, cycle card) | bitcoin `ath`, `ath_date` (USD) vs Binance BTC/USDT | CoinGecko | `coins/markets?ids=bitcoin` (`coingecko-bitcoin`) | USD | on new highs | 30 min |
| DXY (L2) | ICE US Dollar Index `DX-Y.NYB` | [Yahoo Finance](https://finance.yahoo.com/quote/DX-Y.NYB/) (unofficial endpoint, no published terms) | `v8/finance/chart/DX-Y.NYB?range=1mo&interval=1d` (`yahoo-dxy`) | index; change vs prior session close | market hours | 5 min |
| QQQ (BTC/QQQ, L2) | Invesco QQQ Trust, USD | Yahoo Finance | `…/chart/QQQ…` (`yahoo-qqq`) | USD | market hours | 5 min |
| 10Y yield (TradingView card) | CBOE `^TNX`, percent | Yahoo Finance | `…/chart/%5ETNX…` (`yahoo-tnx`) | % | market hours | 5 min |
| US M2, Fed assets, TGA, RRP, real yield, 10Y/2Y (L1, L2 spread) | M2SL, WALCL, WDTGAL, RRPONTSYD, DFII10, DGS10, DGS2 | FRED (see the Layer 1 table below) | `fredgraph.csv?id=…&cosd=…` (`/api/fred/<ID>`) | see below | see below | on every opening, then by schedule |
| MVRV Z (L3) | — | **Manual**: no verified free, unauthenticated automated source | — | — | — | never automatic |
| Altseason index (alerts) | — | not connected ("No source") | — | — | — | — |

Derived values:

- **BTC/QQQ** = BTC/USDT spot ÷ QQQ's latest price. This mixes a 24/7 crypto quote in USDT with
  a USD equity price that may be from the last close. The chart uses the BTC UTC daily close ÷
  QQQ close on the same calendar date.
- **10Y−2Y** = DGS10 − DGS2 on the latest date both have an observation (never mixed dates).
- **Post-halving month** is computed from today's date and the halving on 2024-04-20 (block
  840,000).
- Static notes, labelled as such: "Next expected low" and "Diminishing returns" in the cycle
  card; the decision matrix; and the alert thresholds $78,800 / $70,000.

Not substituted: there are no weekly averages for Wednesday levels (WDTGAL, not WTREGEN), US M2
is never labelled global liquidity, USDT is never presented as USD, the funding interval stays
the Binance 8h one, and no non-equivalent index is used. No public CORS proxies or mirrors are
used.

## Relay (`serve.py`)

- Only fixed, allow-listed URLs (`SOURCES`, `FRED_IDS`). The relay accepts no caller-supplied
  URLs and is not a general proxy.
- 12 s timeout, at most 3 attempts. It retries network errors, timeouts and 5xx; it retries a 429
  only when Retry-After ≤ 5 s. A longer Retry-After is returned to the browser (`retry_after`).
- **Cooldown:** after a 429 from a host, no request goes to that host for max(Retry-After, 60 s).
  Keyless CoinGecko throttles quickly.
- Short in-memory reuse (Binance ticker 10 s … FRED 10 min, the FRED `max-age`), so repeated
  reloads don't spam providers.
- **Last-good cache** in `.runtime/cache/` (validated before use; corrupt files are ignored). When
  the upstream fails, the last valid body is returned with `X-Relay-Cache: last-good`, its
  original `X-Retrieved-At`, and `X-Upstream-Error`. The page shows it as *Cached* with the error,
  never as a new retrieval.
- `/api/health` is the only endpoint readable cross-origin, for the `file://` notice.
  `.runtime`, `.git` and `.remember` are not served.

## Page refresh behaviour

- **Every opening:** validated saved data (localStorage `btcDash.sources.v1`, `btcDash.fred.v2`)
  renders immediately as *Cached*. Then every automatic source is requested at once, and each one
  updates its values, badges, charts, scores and TradingView cards as soon as it arrives.
- After that, a single 30-second scheduler refreshes each source when its interval (table above)
  has passed. It also runs immediately when the tab becomes visible again and when the network
  comes back (`online`). Hidden tabs are not polled.
- At most one request per source at a time; **Refresh All** forces every source and is always
  re-enabled.
- After a failure, a source waits for Retry-After (at least 60 s), otherwise backs off 1, 2, 4 … up
  to 30 min. The other sources are unaffected (e.g. a funding failure keeps the spot price).
- **Checked vs new observation:** each source records its last successful retrieval and whether
  that retrieval brought a newer observation. A failed request never renews the last-success
  time.
- **States:** Loading · Live (market data retrieved this session, < 15 min old) · Latest / Latest
  close (current for the source, e.g. a weekend close) · Latest published (FRED) · Cached (saved
  copy or failed refresh) · Stale (older than the source's limit, e.g. Binance spot > 15 min,
  Yahoo > 4 days) · Not yet published (FRED period not due) · Unavailable · Manual.
- The header pill says **LIVE · ALL 4 SOURCES** only when Binance, CoinGecko, Yahoo and FRED are
  all current; otherwise **PARTIAL**, with ✓ / ◐ / ✕ per group and the errors in its tooltip. Each
  card has a source line (provider, instrument, observation time, status); hover it for the link,
  last retrieval, check result and last error.
- If the chart library cannot load, values still update and each chart area says so. Chart.js
  4.4.1 is a local copy in `vendor/`; its sha512 matches the cdnjs SRI, so it also works offline.

## Layer 1: FRED series (verified against the official FRED series pages)

| Metric | FRED ID | Publisher / release | Native units | Display |
|---|---|---|---|---|
| US M2 (not global liquidity) | [M2SL](https://fred.stlouisfed.org/series/M2SL) | Federal Reserve Board, H.6 | Billions US$, SA, monthly | ÷ 1,000 → $T |
| Fed total assets | [WALCL](https://fred.stlouisfed.org/series/WALCL) | Federal Reserve Board, H.4.1 | Millions US$, Wednesday level | ÷ 1,000,000 → $T |
| Treasury General Account | [WDTGAL](https://fred.stlouisfed.org/series/WDTGAL) | Federal Reserve Board, H.4.1 | Millions US$, Wednesday level | ÷ 1,000 → $B |
| Overnight reverse repo | [RRPONTSYD](https://fred.stlouisfed.org/series/RRPONTSYD) | Federal Reserve Bank of New York | Billions US$, daily | as is ($B) |
| 10-year real yield | [DFII10](https://fred.stlouisfed.org/series/DFII10) | Federal Reserve Board, H.15 | Percent, daily | as is (%) |
| 10Y / 2Y yields (Layer 2 spread) | [DGS10](https://fred.stlouisfed.org/series/DGS10), [DGS2](https://fred.stlouisfed.org/series/DGS2) | Federal Reserve Board, H.15 | Percent, daily | 10Y − 2Y on a common date |

### Rows and dates

- 13 rows: the 12 completed calendar months plus the current partial month (US Eastern date).
- **Historical rows:** for daily and weekly series, the last valid observation *within* the month;
  for monthly M2, that month's own observation. Values are never interpolated or carried forward.
- **Current row:** each metric's latest published observation, with its own date.
- Values are FRED's **latest revised** data, not what was known at the time (use ALFRED vintages
  for backtests).
- **Not yet published** = the period is not due under the schedule below. **Unavailable** = a
  failed request or a FRED blank (e.g. a holiday), with the reason.

### Derived metrics (full precision; rounded only for display)

- **Net liquidity ($T)** on Wednesday *d* = WALCL/1e6 − WDTGAL/1e6 − RRPONTSYD/1,000, all three
  dated *d*; otherwise unavailable. Example: 2026-11-11 (Veterans Day) has no RRP value, so the
  latest computable week is 2026-11-04 ($5.811672T).
- **NLM 8W Δ ($T)** = net liquidity(*d*) − net liquidity(*d* − 56 days). This is an analytical
  proxy, not a proven lead indicator.
- **M2 trend** = M2SL[m] ÷ M2SL[m − 3 months] − 1; flat band ±0.1%.
- **WALCL 13W %** = WALCL[*d*] ÷ WALCL[*d* − 91 days] − 1.

### Badges, signal and score

| Metric | Bullish | Bearish | Otherwise |
|---|---|---|---|
| M2 trend | > +0.1% | < −0.1% | Neutral |
| NLM 8W Δ | > +0.1T | < −0.1T | Neutral |
| RRP | < $500B | > $1,000B | Neutral |
| TGA | < $600B | > $800B | Neutral |
| Real yield | < 1.5% | > 2.5% | Neutral |
| WALCL 13W % | > 0% | < −3% | Neutral |

- Missing inputs are **No data**, never Neutral, and are excluded from scores. A valid zero is a
  value.
- **L1 signal:** Bullish if ≥4 of 6 are bullish; Bearish if ≥3 are bearish; otherwise Neutral. If
  missing inputs could change the result, it shows **Insufficient data**.
- **L1 score (0–10):** bull 2 / neutral 1 / bear 0 points, averaged over available inputs;
  needs ≥4 of 6.
- **L2 / L3 scores:** graded rules over available inputs (L2 needs ≥2 of 3, L3 ≥3 of 4).
  **Funding** thresholds are in **% per 8h** (−0.02 / 0 / +0.02 for the score; −0.005 / +0.01 for
  the badge). The rate is stored as a decimal and converted before comparison (see CHANGELOG).
- **Composite:** the mean of the scored layers; needs ≥2. Partial coverage is stated.

### Freshness (US Eastern, Federal Reserve holidays)

| Series | Expected availability | Refetch when older than |
|---|---|---|
| RRPONTSYD | by noon on the next business day | 2 h |
| DFII10, DGS10, DGS2 | by 09:00 two business days after the observation | 2 h |
| WALCL, WDTGAL | by 09:00 two business days after the Wednesday (H.4.1 Thursday 16:30) | 6 h |
| M2SL | by the 4th Tuesday of the next month + 7 days | 12 h |

Every opening rechecks all FRED series; the relay's 10-minute reuse avoids repeat downloads.
Validation rejects data with the wrong header/series, invalid, duplicate or unsorted dates,
non-numeric values, implausible ranges (unit errors), non-Wednesday weekly dates, and monthly
dates not on the 1st. FRED blanks are missing values, not zeros. Click any table value for its
provider, series link, observation date, units and conversion, update and retrieval times, check
result, status and formula inputs.
