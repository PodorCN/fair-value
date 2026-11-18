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
  assert(status.errors && typeof status.errors === 'object' && !Array.isArray(status.errors), 'Missing source errors');
  if (status.result !== 'failed') {
    assert.equal(status.mode, snapshot.mode, 'Published mode/status mismatch');
    assert.equal(status.valuation_date, snapshot.valuation_date, 'Published valuation/status mismatch');
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
  return { result: status.result, mode: snapshot.mode, health: snapshot.health };
}

export function verifySourceHealth(snapshot, status) {
  verifyPublication(snapshot, status);
  assert.equal(status.result, 'success', `SOURCE HEALTH: last backend update was ${status.result}: ${JSON.stringify(status.errors)}`);
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
