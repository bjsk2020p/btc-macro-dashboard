// Run: node --test tests/l1_core.test.mjs tests/sources_core.test.mjs   (name the files; the folder form fails on Windows)
// Exercises the <script id="l1-core"> block of btc_dashboard_v2.html against
// official FRED fixtures (tests/fixtures, retrieved 2026-11-15) and synthetic edge cases.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const here = new URL('.', import.meta.url);
const html = readFileSync(new URL('../btc_dashboard_v2.html', here), 'utf8');
const src = html.match(/<script id="l1-core">([\s\S]*?)<\/script>/)[1];
const ctx = { Intl, Date, Map, Set, Number, Object, Math, String, Error, isNaN };
ctx.globalThis = ctx;
vm.runInNewContext(src, ctx);
const C = ctx.L1Core;

const fixture = id => readFileSync(new URL(`fixtures/${id}.csv`, here), 'utf8');
const IDS = ['M2SL', 'WALCL', 'WDTGAL', 'RRPONTSYD', 'DFII10'];
const parsedAll = Object.fromEntries(IDS.map(id => [id, C.parseFredCsv(fixture(id), id)]));
const NOW = Date.parse('2026-11-15T13:00:00Z');           // Sunday 08:00 ET
const NOWSTAMP = C.etStamp(NOW);
const ds = C.buildDataset(parsedAll);
const close = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);

test('unit conversions match official native units', () => {
  const cur = C.currentRow(ds, '2026-11', NOWSTAMP).cells;
  close(cur.walcl.value, 6758867 / 1e6);          // WALCL millions → $T
  assert.equal(cur.walcl.date, '2026-11-11');
  close(cur.tga.value, 912134 / 1000);            // WDTGAL millions → $B
  assert.equal(cur.tga.date, '2026-11-11');
  close(cur.rrp.value, 0.325);                    // RRPONTSYD already $B
  assert.equal(cur.rrp.date, '2026-11-13');
  close(cur.ry.value, 2.86);                      // DFII10 percent
  assert.equal(cur.ry.date, '2026-11-12');
  close(cur.m2Level.value, 23413.9 / 1000);       // M2SL billions → $T
  assert.equal(cur.m2Level.date, '2026-09-01');   // latest published month, not the current month
});

test('net liquidity uses one common Wednesday and full precision', () => {
  const n = C.netLiquidity(ds, '2026-11-04', NOWSTAMP);
  close(n.value, 6748145 / 1e6 - 936248 / 1e6 - 0.225 / 1000);
  close(n.value, 5.811672, 1e-9);                 // independent Python check
  const m = C.nlmAt(ds, '2026-11-04', NOWSTAMP);
  close(m.value, -0.08481, 1e-9);
  assert.equal(m.start.date, '2026-09-09');
});

test('holiday Wednesday without RRP gives an unavailable NLM, not a carried-forward value', () => {
  assert.equal(C.isBusinessDay('2026-11-11'), false);              // Veterans Day
  const m = C.nlmAt(ds, '2026-11-11', NOWSTAMP);
  assert.equal(m.value, null);
  assert.match(m.reason, /RRPONTSYD has no observation on 2026-11-11/);
  const cur = C.currentRow(ds, '2026-11', NOWSTAMP).cells.nlm;   // current row falls back to latest computable week
  assert.equal(cur.date, '2026-11-04');
  assert.equal(cur.skipped[0].date, '2026-11-11');
});

test('historical months match official observations (three months + October)', () => {
  const rows = Object.fromEntries(C.buildTable(ds, NOW).map(r => [r.key, r]));
  const nov = rows['2025-11'].cells, mar = rows['2026-03'].cells, aug = rows['2026-08'].cells, oct = rows['2026-10'].cells;
  close(nov.m2Level.value, 22.2532); close(nov.m2Trend.value * 100, 0.8488210315464206, 1e-9);
  assert.deepEqual([nov.rrp.date, nov.rrp.value], ['2025-11-28', 7.561]);
  assert.deepEqual([nov.tga.date, nov.tga.raw], ['2025-11-26', 899678]);
  assert.deepEqual([nov.ry.date, nov.ry.value], ['2025-11-28', 1.79]);
  close(nov.walcl.qoq * 100, -0.7718012461489399, 1e-9);
  close(nov.nlm.value, -0.107038, 1e-6); assert.equal(nov.nlm.date, '2025-11-26');
  close(mar.m2Level.value, 22.6139); close(mar.nlm.value, 0.185212, 1e-6);
  assert.deepEqual([mar.rrp.date, mar.rrp.value], ['2026-03-31', 15.782]);
  close(aug.walcl.qoq * 100, 0.39569636758520765, 1e-9); close(aug.nlm.value, -0.145429, 1e-6);
  // Month boundary: October uses the last October observation, never early November.
  assert.equal(oct.rrp.date, '2026-10-30'); assert.equal(oct.walcl.date, '2026-10-28'); assert.equal(oct.ry.date, '2026-10-30');
});

test('rows: 12 completed months + labelled current partial month', () => {
  const rows = C.buildTable(ds, NOW);
  assert.equal(rows.length, 13);
  assert.equal(rows[0].key, '2025-11');
  assert.equal(rows[11].key, '2026-10');
  assert.equal(rows[12].label, 'Nov 2026 (Current)');
  assert.ok(rows[12].current);
});

test('publication delay: unreleased M2 months are "pending", not failures or carried forward', () => {
  const rows = Object.fromEntries(C.buildTable(ds, NOW).map(r => [r.key, r]));
  assert.equal(rows['2026-10'].cells.m2Level.value, null);
  assert.equal(rows['2026-10'].cells.m2Level.state, 'pending');
  assert.equal(C.dueStamp('M2SL', '2026-10'), '2026-12-01 09:00');   // 4th Tuesday (Nov 24) + 7 days grace
  // After the due date the same gap is reported as stale/unavailable instead.
  const late = C.buildTable(ds, Date.parse('2026-12-05T15:00:00Z')).find(r => r.key === '2026-10');
  assert.equal(late.cells.m2Level.state, 'unavailable');
});

test('expected-latest schedule and freshness states', () => {
  assert.equal(C.expectedLatest('WALCL', NOW), '2026-11-11');
  assert.equal(C.expectedLatest('RRPONTSYD', NOW), '2026-11-12');   // Fri 11-13 not required until Mon noon ET
  assert.equal(C.expectedLatest('DFII10', NOW), '2026-11-10');      // minimum required (staleness threshold), 11-11 holiday
  assert.equal(C.expectedLatest('M2SL', NOW), '2026-09');
  const entry = { parsed: parsedAll.WALCL, fromCache:false };
  assert.equal(C.seriesFreshness('WALCL', entry, NOW).state, 'latest');
  assert.equal(C.seriesFreshness('WALCL', { ...entry, fromCache:true }, NOW).state, 'cached');
  assert.equal(C.seriesFreshness('WALCL', entry, Date.parse('2026-11-21T15:00:00Z')).state, 'stale');
  assert.equal(C.seriesFreshness('WALCL', null, NOW).state, 'unavailable');
});

test('Federal Reserve holidays', () => {
  assert.equal(C.isBusinessDay('2026-11-26'), false);   // Thanksgiving
  assert.equal(C.isBusinessDay('2026-07-03'), true);    // Jul 4 is Saturday: Fed open Friday
  assert.equal(C.isBusinessDay('2027-07-05'), false);   // Jul 4 2027 is Sunday → Monday observed
  assert.equal(C.isBusinessDay('2026-06-19'), false);   // Juneteenth
});

test('validation rejects malformed data and keeps missing markers out of the values', () => {
  const ok = C.parseFredCsv('observation_date,RRPONTSYD\n2026-01-02,0\n2026-01-05,.\n2026-01-06,\n2026-01-07,1.5\n', 'RRPONTSYD');
  assert.equal(JSON.stringify(ok.rows), JSON.stringify([['2026-01-02', 0], ['2026-01-07', 1.5]]));
  assert.equal(JSON.stringify(ok.blanks), JSON.stringify(['2026-01-05', '2026-01-06']));
  const bad = (t, id, re) => assert.throws(() => C.parseFredCsv(t, id), re);
  bad('<html>error</html>', 'RRPONTSYD', /unexpected CSV header/);
  bad('observation_date,RRPONTSYD\n2026-01-02,1\n2026-01-02,1\n', 'RRPONTSYD', /duplicate/);
  bad('observation_date,RRPONTSYD\n2026-01-05,1\n2026-01-02,1\n', 'RRPONTSYD', /out-of-order/);
  bad('observation_date,RRPONTSYD\n2026-01-02,abc\n', 'RRPONTSYD', /non-numeric/);
  bad('observation_date,RRPONTSYD\n2026-02-30,1\n', 'RRPONTSYD', /invalid date/);
  bad('observation_date,WALCL\n2026-01-08,6700000\n', 'WALCL', /not a Wednesday/);
  bad('observation_date,WALCL\n2026-01-07,6700\n', 'WALCL', /plausible range/);   // wrong units (e.g. billions)
  bad('observation_date,M2SL\n2026-01-15,22000\n', 'M2SL', /1st/);
  bad('observation_date,DFII10\n', 'DFII10', /no valid observations/);
});

test('a legitimate zero is a value: RRP of 0 classifies bullish and enters net liquidity', () => {
  const csv = (id, lines) => C.parseFredCsv(`observation_date,${id}\n${lines.join('\n')}\n`, id);
  const z = C.buildDataset({
    WALCL: csv('WALCL', ['2026-01-07,6700000', '2026-03-04,6800000']),
    WDTGAL: csv('WDTGAL', ['2026-01-07,800000', '2026-03-04,700000']),
    RRPONTSYD: csv('RRPONTSYD', ['2026-01-07,0', '2026-03-04,0'])
  });
  const n = C.nlmAt(z, '2026-03-04', '2026-03-10 12:00');
  close(n.value, (6.8 - 0.7 - 0) - (6.7 - 0.8 - 0));
  assert.equal(C.classify('rrp', { value: 0 }), 'bull');
  assert.equal(C.classify('rrp', { value: null }), 'na');
});

test('failed series: cells unavailable, no fabricated values, signal/score withheld', () => {
  const partial = C.buildDataset({ M2SL: parsedAll.M2SL, DFII10: parsedAll.DFII10 });
  const row = C.currentRow(partial, '2026-11', NOWSTAMP);
  assert.equal(row.cells.rrp.value, null);
  assert.equal(row.cells.rrp.state, 'unavailable');
  assert.equal(row.cells.nlm.value, null);
  assert.equal(row.sig.signal, 'insufficient');
  assert.equal(row.score.score, null);
});

test('signal and score rules', () => {
  const S = (...v) => C.signalFrom(Object.fromEntries(v.map((x, i) => [i, x]))).signal;
  assert.equal(S('bull', 'bull', 'bull', 'bull', 'bear', 'bear'), 'bull');
  assert.equal(S('bear', 'bear', 'bear', 'bull', 'bull', 'neut'), 'bear');
  assert.equal(S('bull', 'bull', 'neut', 'neut', 'bear', 'bear'), 'neut');
  assert.equal(S('bull', 'bull', 'bull', 'na', 'neut', 'neut'), 'insufficient');  // missing one could make it bullish
  assert.equal(S('bull', 'neut', 'neut', 'neut', 'na', 'bear'), 'neut');          // missing one cannot change outcome
  const sc = C.scoreFrom({ a:'bull', b:'bull', c:'neut', d:'bear', e:'na', f:'na' });
  close(sc.score, 5 / 8 * 10); assert.equal(sc.known, 4);
  assert.equal(C.scoreFrom({ a:'bull', b:'bull', c:'neut', d:'na', e:'na', f:'na' }).score, null);
  // Fixture-based current row: verify the exact classes the dashboard shows.
  const cur = C.currentRow(ds, '2026-11', NOWSTAMP);
  assert.deepEqual({ ...cur.classes }, { m2Trend:'bull', nlm:'neut', rrp:'bull', tga:'bear', ry:'bear', walcl:'neut' });
  assert.equal(cur.sig.signal, 'neut');
  close(cur.score.score, 6 / 12 * 10);   // 2 bull×2 + 2 neutral×1 + 2 bear×0 = 6 of 12
});
