import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { atOrBefore, rangeRows, csv, pct, signed } from './view.js';

const snapshot = JSON.parse(await readFile(new URL('../data/dashboard.json', import.meta.url), 'utf8'));

test('weekend historical selection reads the last observed day', () => {
  const row = atOrBefore(snapshot.history, '2026-09-26');
  assert.equal(row.date, '2026-09-25');
});
test('effective allocation is selected by execution date, not decision date', () => {
  const held = atOrBefore(snapshot.monthly, '2026-10-01', 'execution_date');
  assert.equal(held.date, '2026-08-31');
  const next = atOrBefore(snapshot.monthly, '2026-10-07', 'execution_date');
  assert.equal(next.date, '2026-09-30');
  assert.equal(next.usd_weight, .175);
});
test('chart ranges never include future readings', () => {
  const rows = rangeRows(snapshot.history, '2018-02-28', '1');
  assert(rows.length > 250);
  assert(rows.every(r => r.date >= '2017-02-28' && r.date <= '2018-02-28'));
});
test('CSV preserves backend decision values and quotes embedded strings', () => {
  assert.equal(csv([{ date: '2026-01-30', value: 'one,"two"' }], ['date', 'value']), '"date","value"\r\n"2026-01-30","one,""two"""');
  assert.match(csv(snapshot.monthly.slice(-1), ['date', 'usd_weight']), /^"date","usd_weight"\r\n/);
});
test('formatting preserves missing readings and neutral tiers', () => {
  assert.equal(pct(null), '—');
  assert.equal(signed(-0, 0), '0');
  assert.equal(pct(.175), '17.5%');
});
test('snapshot exposes separate raw FV, signal FV, source dates and allocation states', () => {
  assert.equal(snapshot.schema_version, 1);
  assert(snapshot.latest.beer_raw_fv > 0 && snapshot.latest.beer_fv > 0);
  assert(snapshot.sources.every(s => s.observation_date && s.available_date));
  assert(snapshot.allocation.held.execution_date <= snapshot.run_date);
  assert(snapshot.allocation.pending.every(r => r.execution_date > snapshot.run_date));
  assert(snapshot.history.every(r => r.date <= snapshot.valuation_date));
});
