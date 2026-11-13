import test from 'node:test';
import assert from 'node:assert/strict';
import { bindDomain, client, context, ensureProject, verifyPublication } from './cloudflare.mjs';

const config = { project_name: 'podor-fair-value', production_branch: 'main', zone_name: 'podor.org', domain: 'fv.podor.org' };
const ctx = { accountId: 'account', zoneId: 'zone' };
const project = { subdomain: 'podor-fair-value.pages.dev' };

test('missing Cloudflare access fails before any request', () => {
  assert.throws(() => client(''), /No Cloudflare deployment credential/);
});
test('account mismatch cannot mutate a domain or project', async () => {
  await assert.rejects(context(async () => [{ id: 'zone', account: { id: 'actual' } }], config, 'wrong'), /does not own/);
});
test('existing Pages project is reused', async () => {
  let reads = 0;
  const result = await ensureProject(async (path, method) => { reads++; assert.equal(method, 'GET'); return project; }, config, 'account');
  assert.equal(result, project);
  assert.equal(reads, 1);
});
test('domain binding touches only fv.podor.org and preserves TXT records', async () => {
  const writes = [];
  const api = async (path, method = 'GET', body) => {
    if (method !== 'GET') { writes.push({ path, method, body }); return {}; }
    if (path.includes('/domains/')) return null;
    return [{ id: 'txt', type: 'TXT', name: 'fv.podor.org', content: 'keep-verification' }];
  };
  assert.equal(await bindDomain(api, config, ctx, project), 'https://fv.podor.org');
  assert.deepEqual(writes.map(w => w.method), ['POST', 'POST']);
  assert.equal(writes[1].body.name, 'fv.podor.org');
  assert.equal(writes[1].body.content, project.subdomain);
  assert(!writes.some(w => w.path.includes('/txt')));
});
test('an already correct CNAME does not get rewritten', async () => {
  const api = async (path, method = 'GET') => {
    assert.equal(method, 'GET');
    return path.includes('/domains/') ? { status: 'active' } : [{ id: 'dns', type: 'CNAME', content: project.subdomain }];
  };
  await bindDomain(api, config, ctx, project);
});
test('multi-record routing is retained rather than deleted', async () => {
  const api = async path => path.includes('/domains/') ? {} : [{ type: 'A' }, { type: 'AAAA' }];
  await assert.rejects(bindDomain(api, config, ctx, project), /multiple address records/);
});
test('HTTPS verification requires the exact built snapshot', async () => {
  let reads = 0;
  const expected = { generated_at: 'new', model_version: 'rev18' };
  const result = await verifyPublication(config.domain, expected, async () => ({ ok: true, json: async () => ++reads === 1 ? { generated_at: 'old' } : expected }), async () => {});
  assert.equal(result, expected);
  assert.equal(reads, 2);
});
