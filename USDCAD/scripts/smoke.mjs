import assert from 'node:assert/strict';

const origin = (process.env.USDCAD_FRONTEND_URL || 'http://127.0.0.1:4173').replace(/\/$/, '') + '/';
async function get(url, options = {}) {
  const response = await fetch(url, { ...options, signal: AbortSignal.timeout(15000) });
  assert.equal(response.status, 200, `${url}: HTTP ${response.status}`);
  return response;
}
const page = await get(origin);
assert.match(await page.text(), /USD.*CAD|USD<span/);
const config = await (await get(new URL('config.json', origin))).json();
const dashboardUrl = new URL(config.dashboardUrl, origin);
const pageOrigin = new URL(origin).origin;
const apiMode = dashboardUrl.origin !== pageOrigin;
const response = await get(dashboardUrl, apiMode ? { headers: { Origin: pageOrigin } } : {});
if (apiMode) {
  assert.equal(response.headers.get('access-control-allow-origin'), pageOrigin, 'Backend must explicitly allow frontend origin');
  assert.equal(response.headers.get('cache-control'), 'no-store');
}
const snapshot = await response.json();
assert.equal(snapshot.schema_version, 1);
assert(snapshot.latest.beer_fv > 0 && snapshot.history.length > 1000);
const status = await (await get(new URL(config.statusUrl, origin))).json();
assert.equal(status.result, 'success', `Last backend update was ${status.result}`);
if (apiMode) {
  const absent = await fetch(new URL('data/dashboard.json', origin));
  assert.equal(absent.status, 404, 'API frontend must not bundle a static data fallback');
} else {
  assert.equal(dashboardUrl.pathname, new URL('data/dashboard.json', origin).pathname);
  const app = await (await get(new URL('app.js', origin))).text();
  assert.match(app, /setInterval\(load, 300000\)/);
}
console.log(`PASS: frontend ${origin} → ${apiMode ? 'API backend' : 'published JSON'} ${dashboardUrl}`);
console.log(`Update ${status.result} · ${status.sources_fetched.length} sources · valuation ${snapshot.valuation_date} · BEER ${snapshot.latest.beer_fv}`);
