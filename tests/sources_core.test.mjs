// Run: node --test tests/l1_core.test.mjs tests/sources_core.test.mjs   (name the files; the folder form fails on Windows)
// Exercises the <script id="sources-core"> block of btc_dashboard_v2.html against real
// provider responses saved on 2026-11-15 (tests/fixtures/sources) and malformed variants.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const here = new URL('.', import.meta.url);
const html = readFileSync(new URL('../btc_dashboard_v2.html', here), 'utf8');
const src = html.match(/<script id="sources-core">([\s\S]*?)<\/script>/)[1];
const ctx = { Date, Map, Number, Object, Math, String, Error, Array, JSON };
ctx.globalThis = ctx;
vm.runInNewContext(src, ctx);
const C = ctx.SourcesCore;
const fx = name => JSON.parse(readFileSync(new URL(`fixtures/sources/${name}.json`, here), 'utf8'));
const clone = o => JSON.parse(JSON.stringify(o));

test('every saved provider response parses and keeps the provider values exactly', () => {
  const t = fx('binance-ticker'), p = C.parse('binance-ticker', t);
  assert.equal(p.price, Number(t.lastPrice)); assert.equal(p.changePct, Number(t.priceChangePercent)); assert.equal(p.obsTime, t.closeTime);
  const pr = fx('binance-premium'), pp = C.parse('binance-premium', pr);
  assert.equal(pp.rate, Number(pr.lastFundingRate)); assert.equal(pp.nextFundingTime, pr.nextFundingTime);
  const k = C.parse('binance-klines', fx('binance-klines'));
  assert.equal(k.rows.length, 180); assert.equal(k.rows[179].close, Number(fx('binance-klines')[179][4]));
  const f = C.parse('binance-funding', fx('binance-funding'));
  assert.equal(f.rows.length, 90); assert.equal(f.obsTime, fx('binance-funding')[89].fundingTime);
  const g = fx('coingecko-global'), gp = C.parse('coingecko-global', g);
  assert.equal(gp.btcd, g.data.market_cap_percentage.btc); assert.equal(gp.obsTime, g.data.updated_at * 1000);
  const s = C.parse('coingecko-stablecoins', fx('coingecko-stablecoins'));
  assert.equal(s.counted, 250); assert.ok(Math.abs(s.stableMcapUsd - 291814840825) < 1);
  const b = C.parse('coingecko-bitcoin', fx('coingecko-bitcoin'));
  assert.equal(b.ath, fx('coingecko-bitcoin')[0].ath);
  for (const key of ['yahoo-dxy', 'yahoo-qqq', 'yahoo-tnx']) {
    const y = fx(key), yp = C.parse(key, y);
    assert.equal(yp.price, y.chart.result[0].meta.regularMarketPrice);
    assert.equal(yp.obsTime, y.chart.result[0].meta.regularMarketTime * 1000);
  }
});

test('Yahoo: a null bar (session not closed yet) is skipped; previous close is the prior session', () => {
  const y = fx('yahoo-dxy'), p = C.parse('yahoo-dxy', y);
  const closes = y.chart.result[0].indicators.quote[0].close;
  assert.equal(closes[closes.length - 1], null);                 // the saved response really has a null last bar
  assert.ok(p.bars.every(b => Number.isFinite(b.close)));
  assert.equal(p.sessionDate, '2026-11-13');                      // Friday close, New York date
  assert.equal(p.prevClose, closes[closes.length - 3]);           // Thursday's close, not Friday's
  assert.equal(p.bars[p.bars.length - 1].date, '2026-11-13');
  assert.equal(p.currency, 'USD');
});

test('malformed or mismatched responses are rejected, never turned into numbers', () => {
  const bad = (key, json, re) => assert.throws(() => C.parse(key, json), re);
  const t = fx('binance-ticker');
  bad('binance-ticker', { ...t, symbol:'ETHUSDT' }, /not BTCUSDT/);
  bad('binance-ticker', { ...t, lastPrice:'NaN' }, /lastPrice/);
  bad('binance-ticker', { ...t, lastPrice:'0' }, /positive/);
  bad('binance-ticker', null, /not BTCUSDT/);
  const k = clone(fx('binance-klines')); [k[5], k[6]] = [k[6], k[5]];
  bad('binance-klines', k, /out of order/);
  bad('binance-klines', fx('binance-klines').slice(0, 10), /at least 30/);
  bad('binance-premium', { ...fx('binance-premium'), lastFundingRate:'0.2' }, /±5%/);
  const g = clone(fx('coingecko-global')); g.data.market_cap_percentage.btc = 140;
  bad('coingecko-global', g, /0–100/);
  bad('coingecko-global', { status:{ error_code:429, error_message:'rate limit' } }, /missing data/);
  bad('coingecko-stablecoins', [], /list of stablecoins/);
  bad('yahoo-qqq', { chart:{ result:null, error:{ code:'Not Found', description:'No data found' } } }, /Yahoo error/);
  bad('yahoo-qqq', fx('yahoo-dxy'), /symbol DX-Y.NYB/);              // wrong instrument
  bad('yahoo-tnx', '<html>blocked</html>', /missing chart/);
});

test('freshness states: loading, unavailable, live, latest, cached, stale', () => {
  const now = Date.parse('2026-11-15T14:15:00Z');
  const t = { parsed: { price: 1, changePct: 0, obsTime: now - 30e3 } };
  assert.equal(C.status('binance-ticker', undefined, now), 'loading');
  assert.equal(C.status('binance-ticker', { error: 'HTTP 429' }, now), 'unavailable');
  assert.equal(C.status('binance-ticker', t, now), 'live');
  assert.equal(C.status('binance-ticker', { ...t, fromCache: true }, now), 'cached');
  assert.equal(C.status('binance-ticker', { ...t, error: 'timeout' }, now), 'cached');     // failed refresh keeps value, never 'live'
  assert.equal(C.status('binance-ticker', { parsed: { ...t.parsed, obsTime: now - 20 * 60e3 } }, now), 'stale');
  const friday = Date.parse('2026-11-13T21:59:59Z');
  assert.equal(C.status('yahoo-dxy', { parsed: { price: 103, obsTime: friday } }, now), 'latest');   // weekend: last close is current
  assert.equal(C.status('yahoo-dxy', { parsed: { price: 103, obsTime: friday - 5 * 86400e3 } }, now), 'stale');
});

test('snapshot data is never "live"; stale after twice the 30-min snapshot interval', () => {
  const now = Date.parse('2026-11-17T16:00:00Z');
  const snap = (minsAgo, extra = {}) => ({ via:'snapshot', retrievedAt: new Date(now - minsAgo * 60e3).toISOString(), parsed: { price: 103, obsTime: now - 60e3 }, ...extra });
  assert.equal(C.SNAPSHOT_INTERVAL_MS, 30 * 60e3);
  assert.equal(C.status('binance-ticker', snap(5), now), 'snapshot');          // even a 1-min-old market obs is not "live"
  assert.equal(C.status('yahoo-dxy', snap(59), now), 'snapshot');
  assert.equal(C.status('yahoo-dxy', snap(61), now), 'stale');                 // > 2 × 30 min
  assert.equal(C.status('yahoo-dxy', snap(5, { retrievedAt: null }), now), 'stale');   // unknown snapshot time is not current
  assert.equal(C.status('yahoo-dxy', snap(5, { error: 'snapshot kept-previous after http 451: …' }), now), 'cached');
  assert.equal(C.snapshotStale({ via:'local relay', retrievedAt: '2020-01-01T00:00:00Z' }, now), false);   // rule applies to snapshots only
});

test('restored cache entries are validated; corrupted ones are dropped', () => {
  assert.equal(C.validParsed('binance-ticker', { price: 65000, changePct: -1, obsTime: 1794751142010 }), true);
  assert.equal(C.validParsed('binance-ticker', { price: '65000', changePct: -1, obsTime: 1 }), false);
  assert.equal(C.validParsed('binance-klines', { rows: [], obsTime: 1 }), false);
  assert.equal(C.validParsed('yahoo-qqq', { price: null, obsTime: 1 }), false);
  assert.equal(C.validParsed('coingecko-global', 'garbage'), false);
});

test('BTC/QQQ history pairs closes by calendar date only (no shifted alignment)', () => {
  const btc = [{ date:'2026-11-12', close:66000 }, { date:'2026-11-13', close:64000 }, { date:'2026-11-14', close:65000 }];
  const qqq = [{ date:'2026-11-11', close:690 }, { date:'2026-11-12', close:660 }, { date:'2026-11-13', close:640 }];
  assert.equal(JSON.stringify(C.btcQqqSeries(btc, qqq)), JSON.stringify([{ date:'2026-11-12', ratio:100 }, { date:'2026-11-13', ratio:100 }]));
});

test('SSR from saved responses: BTC mcap (total × dominance) ÷ stablecoin mcap', () => {
  const g = C.parse('coingecko-global', fx('coingecko-global')), s = C.parse('coingecko-stablecoins', fx('coingecko-stablecoins'));
  const ssr = (g.totalMcapUsd * g.btcd / 100) / s.stableMcapUsd;
  const raw = fx('coingecko-global').data;
  assert.equal(ssr, (raw.total_market_cap.usd * raw.market_cap_percentage.btc / 100) / 291814840825);
  assert.ok(ssr > 1 && ssr < 20);
});
