import test from 'node:test';
import assert from 'node:assert/strict';
import { verifyPublication, verifySourceHealth } from './smoke-checks.mjs';

function fixture() {
  const sources = Array.from({ length: 11 }, (_, i) => ({ name: `source_${i}`, status: 'ok', error: null, fetched_this_run: true }));
  return {
    snapshot: { schema_version: 1, latest: { beer_fv: 1.3 }, history: Array(1001).fill({}), generated_at: '2026-11-17T23:15:15Z', mode: 'live', health: 'ok', valuation_date: '2026-11-11', sources },
    status: { result: 'success', mode: 'live', valuation_date: '2026-11-11', errors: {}, sources_fetched: sources.map(s => s.name) },
  };
}

test('successful live publication passes both gates', () => {
  const { snapshot, status } = fixture();
  verifyPublication(snapshot, status);
  verifySourceHealth(snapshot, status);
});
test('November 17 StatCan 403 regression passes publication but fails source health', () => {
  const { snapshot, status } = fixture();
  status.result = 'partial';
  status.errors = { ca_cpi: 'HTTPError: HTTP Error 403: Forbidden' };
  snapshot.health = 'degraded';
  Object.assign(snapshot.sources[0], { name: 'ca_cpi', status: 'error', error: status.errors.ca_cpi, fetched_this_run: false });
  status.sources_fetched.shift();
  verifyPublication(snapshot, status);
  assert.throws(() => verifySourceHealth(snapshot, status), /SOURCE HEALTH.*partial/);
});
test('partial publication cannot hide the retained source or its error', () => {
  for (const hidden of ['status', 'error', 'fetched_this_run', 'health']) {
    const { snapshot, status } = fixture();
    status.result = 'partial'; status.errors = { source_0: 'HTTP 403' };
    snapshot.health = 'degraded';
    Object.assign(snapshot.sources[0], { status: 'error', error: 'HTTP 403', fetched_this_run: false });
    if (hidden === 'health') snapshot.health = 'ok';
    else delete snapshot.sources[0][hidden];
    assert.throws(() => verifyPublication(snapshot, status), /Partial update/);
  }
});
test('empty partial errors cannot pass publication', () => {
  const { snapshot, status } = fixture(); status.result = 'partial'; snapshot.health = 'degraded';
  assert.throws(() => verifyPublication(snapshot, status), /retain its errors/);
});
test('cached recalculation can publish but cannot pass live source health', () => {
  const { snapshot, status } = fixture(); snapshot.mode = status.mode = 'cached'; snapshot.health = 'cached';
  verifyPublication(snapshot, status);
  assert.throws(() => verifySourceHealth(snapshot, status), /cached recalculation/);
});
test('failed attempt can expose retained publication without claiming source success', () => {
  const { snapshot, status } = fixture(); status.result = 'failed'; status.errors = { fatal: 'source failure' };
  delete status.mode; delete status.valuation_date;
  verifyPublication(snapshot, status);
  assert.throws(() => verifySourceHealth(snapshot, status), /SOURCE HEALTH.*failed/);
});
test('publication rejects inconsistent successful mode or valuation date', () => {
  for (const key of ['mode', 'valuation_date']) {
    const { snapshot, status } = fixture(); status[key] = 'different';
    assert.throws(() => verifyPublication(snapshot, status), /mismatch/);
  }
});
test('HTTP success does not hide stale valuation or sources', () => {
  const { snapshot, status } = fixture(); snapshot.health = 'stale';
  assert.throws(() => verifySourceHealth(snapshot, status), /freshness/);
  snapshot.health = 'ok'; snapshot.sources[0].status = 'stale';
  assert.throws(() => verifySourceHealth(snapshot, status), /missing, stale or failed/);
});
test('source-health rejects missing, duplicate, unfetched or errored sources', () => {
  for (const change of [s => s.sources.pop(), s => s.sources[0].name = s.sources[1].name,
    s => s.sources[0].fetched_this_run = false, s => s.sources[0].error = '403']) {
    const { snapshot, status } = fixture(); change(snapshot);
    assert.throws(() => verifySourceHealth(snapshot, status), /SOURCE HEALTH/);
  }
  const { snapshot, status } = fixture(); status.sources_fetched.pop();
  assert.throws(() => verifySourceHealth(snapshot, status), /incomplete fetch/);
});
test('unknown result and invalid publication timestamp fail closed', () => {
  const { snapshot, status } = fixture(); status.result = 'unknown';
  assert.throws(() => verifyPublication(snapshot, status), /Unknown update/);
  status.result = 'success'; snapshot.generated_at = 'invalid';
  assert.throws(() => verifyPublication(snapshot, status), /timestamp/);
});
