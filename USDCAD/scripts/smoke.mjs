import assert from 'node:assert/strict';
import { verifyPublication, verifySourceHealth } from './smoke-checks.mjs';

const mode = process.argv[2] || '--all';
assert(['--all', '--publication-only', '--source-health'].includes(mode), 'Unknown smoke mode');
assert(process.argv.length <= 3, 'Unexpected smoke arguments');

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
const status = await (await get(new URL(config.statusUrl, origin))).json();
verifyPublication(snapshot, status);
if (apiMode) {
  const absent = await fetch(new URL('data/dashboard.json', origin));
  assert.equal(absent.status, 404, 'API frontend must not bundle a static data fallback');
} else {
  assert.equal(dashboardUrl.pathname, new URL('data/dashboard.json', origin).pathname);
  const app = await (await get(new URL('app.js', origin))).text();
  assert.match(app, /setInterval\(load, 300000\)/);
}
console.log(`PASS PUBLICATION: frontend ${origin} serves ${apiMode ? 'API backend' : 'published JSON'} ${dashboardUrl}`);
console.log(`Published ${snapshot.generated_at}; update ${status.result}; mode ${snapshot.mode}; health ${snapshot.health}; valuation ${snapshot.valuation_date}`);
for (const source of snapshot.sources.filter(s => s.access_method === 'official-republication')) {
  console.log(`SOURCE PROVENANCE: ${source.series_id} via ${source.provider}; direct ${source.original_provider}: ${source.primary_error}`);
}
if (mode !== '--publication-only') {
  verifySourceHealth(snapshot, status);
  console.log(`PASS SOURCE HEALTH: all ${status.sources_fetched.length} sources fetched; health ${snapshot.health}`);
}
