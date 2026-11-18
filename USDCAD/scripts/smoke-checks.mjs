import assert from 'node:assert/strict';

// Publication and source acquisition are separate facts. A visibly degraded or
// retained snapshot can be published correctly while the source-health gate fails.
export function verifyPublication(snapshot, status) {
  assert.equal(snapshot.schema_version, 1);
  assert(snapshot.latest.beer_fv > 0 && snapshot.history.length > 1000);
  assert(Number.isFinite(Date.parse(snapshot.generated_at)), 'Missing publication timestamp');
  assert(['success', 'partial', 'failed'].includes(status.result), 'Unknown update result');
  assert(['live', 'cached'].includes(snapshot.mode), 'Unknown snapshot mode');
  assert(['ok', 'degraded', 'stale', 'cached'].includes(snapshot.health), 'Unknown snapshot health');
  if (status.result === 'failed') {
    // update.py's fatal handler retains dashboard.json and writes a singular
    // error, without mode, valuation_date, errors or sources_fetched fields.
    assert(typeof status.error === 'string' && status.error.trim(), 'Failed update must retain its fatal error');
  } else {
    assert(status.errors && typeof status.errors === 'object' && !Array.isArray(status.errors), 'Missing source errors');
    assert.equal(status.mode, snapshot.mode, 'Published mode/status mismatch');
    assert.equal(status.valuation_date, snapshot.valuation_date, 'Published valuation/status mismatch');
    if (status.result === 'success') {
      assert.equal(Object.keys(status.errors).length, 0, 'Successful update cannot contain source errors');
    }
  }
  if (status.result === 'partial') {
    assert(Object.keys(status.errors).length > 0, 'Partial update must retain its errors');
    assert(['degraded', 'stale'].includes(snapshot.health), 'Partial update must be visibly degraded');
    for (const [name, error] of Object.entries(status.errors)) {
      const source = snapshot.sources.find(s => s.name === name);
      assert(source && source.status === 'error' && source.error === error && source.fetched_this_run === false,
        `Partial update must expose the retained ${name} source and error`);
    }
  }
  for (const source of snapshot.sources) {
    if (source.access_method === 'official-republication') {
      const url = new URL(source.data_url);
      assert(source.name === 'ca_cpi' && source.series_id === 'v41690973' && source.mirror_series_id === 'V41690973'
        && source.provider === 'Bank of Canada (Statistics Canada republication)' && source.original_provider === 'Statistics Canada'
        && source.unit === 'Index (2002=100)' && source.frequency === 'Monthly' && source.seasonal_adjustment === 'NSA'
        && url.origin === 'https://www.bankofcanada.ca' && url.pathname === '/valet/observations/group/CPI_MONTHLY/json'
        && source.primary_endpoint === 'https://www150.statcan.gc.ca/t1/wds/rest/getDataFromVectorsAndLatestNPeriods'
        && typeof source.primary_error === 'string' && source.primary_error.includes('403')
        && Number.isFinite(Date.parse(source.retrieved_at_utc)), 'Official CPI republication must preserve identity, definition and direct-source failure');
      assert(snapshot.notices.some(n => n.title === 'Official CPI republication' && n.detail.includes(source.primary_error)),
        'Official CPI republication must visibly disclose the direct-source failure');
      if (status.result !== 'failed') {
        const access = status.source_access?.[source.name];
        assert(access && Object.keys(access).every(k => source[k] === access[k])
          && access.primary_error === source.primary_error && access.data_url === source.data_url,
        'Status must preserve the actual CPI republication access record');
      }
    }
  }
  return { result: status.result, mode: snapshot.mode, health: snapshot.health };
}

export function verifySourceHealth(snapshot, status) {
  verifyPublication(snapshot, status);
  assert.equal(status.result, 'success', `SOURCE HEALTH: last backend update was ${status.result}: ${JSON.stringify(status.result === 'failed' ? status.error : status.errors)}`);
  assert.equal(status.mode, 'live', 'SOURCE HEALTH: cached recalculation is not a live fetch');
  assert.equal(Object.keys(status.errors).length, 0, 'SOURCE HEALTH: source errors remain');
  assert.equal(snapshot.sources.length, 11, 'SOURCE HEALTH: expected eleven sources');
  assert.equal(new Set(snapshot.sources.map(s => s.name)).size, 11, 'SOURCE HEALTH: duplicate sources');
  assert.equal(new Set(status.sources_fetched).size, 11, 'SOURCE HEALTH: incomplete fetch set');
  for (const source of snapshot.sources) {
    assert(status.sources_fetched.includes(source.name) && source.fetched_this_run === true && source.status === 'ok' && !source.error,
      `SOURCE HEALTH: ${source.name} is missing, stale or failed`);
  }
  assert.equal(snapshot.health, 'ok', `SOURCE HEALTH: published health is ${snapshot.health}; successful HTTP alone does not prove freshness`);
}
