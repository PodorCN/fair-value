import assert from 'node:assert/strict';

const origin = process.env.USDCAD_FRONTEND_URL || 'http://127.0.0.1:4173';
async function get(url, options = {}) {
  const response = await fetch(url, { ...options, signal: AbortSignal.timeout(15000) });
  assert.equal(response.status, 200, `${url}: HTTP ${response.status}`);
  return response;
}
const page = await get(origin);
assert.match(await page.text(), /USD.*CAD|USD<span/);
const config = await (await get(`${origin}/config.json`)).json();
assert.match(config.dashboardUrl, /^https?:\/\//, 'Run npm run dev for a separate API backend');
assert.notEqual(new URL(config.dashboardUrl).origin, new URL(origin).origin, 'Backend must be a separate origin');
const response = await get(config.dashboardUrl, { headers: { Origin: origin } });
assert.equal(response.headers.get('access-control-allow-origin'), origin, 'Backend must explicitly allow frontend origin');
assert.equal(response.headers.get('cache-control'), 'no-store');
const snapshot = await response.json();
assert.equal(snapshot.schema_version, 1);
assert(snapshot.latest.beer_fv > 0 && snapshot.history.length > 1000);
const status = await (await get(config.statusUrl)).json();
assert.equal(status.result, 'success', `Last backend update was ${status.result}`);
const absent = await fetch(`${origin}/data/dashboard.json`);
assert.equal(absent.status, 404, 'API frontend must not bundle a static data fallback');
console.log(`PASS: frontend ${origin} → backend ${new URL(config.dashboardUrl).origin}`);
console.log(`Update ${status.result} · ${status.sources_fetched.length} sources · valuation ${snapshot.valuation_date} · BEER ${snapshot.latest.beer_fv}`);
