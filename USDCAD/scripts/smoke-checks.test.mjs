import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { verifyPublication, verifySourceHealth } from './smoke-checks.mjs';

function fixture() {
  const sources = Array.from({ length: 11 }, (_, i) => ({ name: `source_${i}`, status: 'ok', error: null, fetched_this_run: true }));
  return {
    snapshot: { schema_version: 1, latest: { beer_fv: 1.3 }, history: Array(1001).fill({}), generated_at: '2026-11-17T23:15:15Z', mode: 'live', health: 'ok', valuation_date: '2026-11-11', sources },
    status: { result: 'success', mode: 'live', valuation_date: '2026-11-11', errors: {}, sources_fetched: sources.map(s => s.name) },
  };
}

function republicationFixture() {
  const { snapshot, status } = fixture();
  const access = {
    provider: 'Bank of Canada (Statistics Canada republication)', original_provider: 'Statistics Canada',
    access_method: 'official-republication', mirror_series_id: 'V41690973',
    data_url: 'https://www.bankofcanada.ca/valet/observations/group/CPI_MONTHLY/json?start_date=2024-11-01&end_date=2026-11-18',
    primary_endpoint: 'https://www150.statcan.gc.ca/t1/wds/rest/getDataFromVectorsAndLatestNPeriods',
    primary_error: 'HTTPError: HTTP Error 403: Forbidden', retrieved_at_utc: '2026-11-18T00:11:21Z',
    unit: 'Index (2002=100)', frequency: 'Monthly', seasonal_adjustment: 'NSA',
  };
  Object.assign(snapshot.sources[0], access, { name: 'ca_cpi', series_id: 'v41690973' });
  status.sources_fetched[0] = 'ca_cpi';
  status.source_access = { ca_cpi: { ...access } };
  snapshot.notices = [{ title: 'Official CPI republication', detail: `Bank of Canada copy; direct endpoint failed: ${access.primary_error}` }];
  return { snapshot, status };
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
  const output = execFileSync(process.env.USDCAD_TEST_PYTHON || 'python',
    ['-B', 'tests/failed_status_fixture.py'],
    { cwd: fileURLToPath(new URL('..', import.meta.url)), encoding: 'utf8', timeout: 15000 });
  const { snapshot_sha256, status } = JSON.parse(output);
  const raw = readFileSync(new URL('../data/dashboard.json', import.meta.url));
  assert.equal(createHash('sha256').update(raw).digest('hex'), snapshot_sha256);
  const snapshot = JSON.parse(raw);
  assert.equal(status.result, 'failed');
  assert.equal(status.error, 'Synthetic acquisition failure');
  assert.equal(Object.hasOwn(status, 'errors'), false);
  verifyPublication(snapshot, status);
  assert.throws(() => verifySourceHealth(snapshot, status), /SOURCE HEALTH.*failed/);
});
test('publication rejects inconsistent successful mode or valuation date', () => {
  for (const key of ['mode', 'valuation_date']) {
    const { snapshot, status } = fixture(); status[key] = 'different';
    assert.throws(() => verifyPublication(snapshot, status), /mismatch/);
  }
});
test('fatal publication requires the real singular nonempty error', () => {
  const { snapshot } = fixture();
  for (const bad of [{}, { error: '' }, { error: '  ' }, { error: {} }, { errors: { fatal: 'synthetic old fixture' } }]) {
    assert.throws(() => verifyPublication(snapshot, { result: 'failed', ...bad }), /fatal error/);
  }
});
test('successful and partial statuses require their own source-error map', () => {
  const { snapshot, status } = fixture();
  delete status.errors;
  assert.throws(() => verifyPublication(snapshot, status), /Missing source errors/);
  status.result = 'partial';
  assert.throws(() => verifyPublication(snapshot, status), /Missing source errors/);
  status.result = 'success'; status.errors = { ca_cpi: '403' };
  assert.throws(() => verifyPublication(snapshot, status), /cannot contain source errors/);
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
test('fresh official same-index republication passes without claiming direct-source recovery', () => {
  const { snapshot, status } = republicationFixture();
  verifySourceHealth(snapshot, status);
  assert.match(snapshot.sources[0].provider, /Bank of Canada/);
  assert.match(status.source_access.ca_cpi.primary_error, /403/);
});
test('republication rejects wrong identity, scale, attribution or hidden primary failure', () => {
  for (const [key, bad] of [['series_id', 'v41690914'], ['unit', 'Percent'], ['seasonal_adjustment', 'SA'],
    ['provider', 'Statistics Canada'], ['data_url', 'https://example.test/cpi'], ['primary_error', ''], ['retrieved_at_utc', 'invalid']]) {
    const { snapshot, status } = republicationFixture(); snapshot.sources[0][key] = bad;
    assert.throws(() => verifyPublication(snapshot, status), /republication must preserve/);
  }
});
test('republication cannot disappear from the notice or update-status access record', () => {
  const { snapshot, status } = republicationFixture(); snapshot.notices = [];
  assert.throws(() => verifyPublication(snapshot, status), /visibly disclose/);
  const again = republicationFixture(); delete again.status.source_access;
  assert.throws(() => verifyPublication(again.snapshot, again.status), /access record/);
  const retained = republicationFixture(); retained.status = { result: 'failed', error: 'Synthetic fatal failure' };
  verifyPublication(retained.snapshot, retained.status);
  assert.throws(() => verifySourceHealth(retained.snapshot, retained.status), /SOURCE HEALTH.*failed/);
});
