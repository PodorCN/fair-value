import { appendFile, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const apiBase = 'https://api.cloudflare.com/client/v4';
const root = new URL('../', import.meta.url);

export function client(token, fetcher = fetch) {
  if (!token) throw new Error('No Cloudflare deployment credential is available. Set the CLOUDFLARE_API_TOKEN repository secret (Pages Edit, Zone Read, DNS Edit for podor.org), then rerun this workflow. Existing CF_API_TOKEN, CLOUDFLARE_TOKEN and CF_TOKEN aliases are supported.');
  return async (path, method = 'GET', body, allow404 = false) => {
    const response = await fetcher(`${apiBase}${path}`, {
      method, redirect: 'error', signal: AbortSignal.timeout(20000),
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (allow404 && response.status === 404) return null;
    const json = await response.json();
    if (!response.ok || json.success === false) {
      const detail = JSON.stringify(json.errors || []).replaceAll(token, '[redacted]');
      throw new Error(`Cloudflare ${method} ${path}: HTTP ${response.status} ${detail}`);
    }
    return json.result;
  };
}

export async function context(api, config, accountId) {
  const zones = await api(`/zones?name=${encodeURIComponent(config.zone_name)}`);
  if (zones.length !== 1) throw new Error(`Expected one accessible ${config.zone_name} zone; found ${zones.length}`);
  const zone = zones[0];
  if (accountId && accountId !== zone.account.id) throw new Error('Configured account does not own the requested domain zone');
  return { accountId: zone.account.id, zoneId: zone.id };
}

export async function ensureProject(api, config, accountId) {
  const path = `/accounts/${accountId}/pages/projects/${encodeURIComponent(config.project_name)}`;
  const existing = await api(path, 'GET', undefined, true);
  if (existing) return existing;
  return api(`/accounts/${accountId}/pages/projects`, 'POST', {
    name: config.project_name, production_branch: config.production_branch,
  });
}

export async function bindDomain(api, config, { accountId, zoneId }, project) {
  const path = `/accounts/${accountId}/pages/projects/${encodeURIComponent(config.project_name)}/domains`;
  const domain = await api(`${path}/${encodeURIComponent(config.domain)}`, 'GET', undefined, true);
  if (!domain) await api(path, 'POST', { name: config.domain });
  const records = await api(`/zones/${zoneId}/dns_records?name=${encodeURIComponent(config.domain)}`);
  const addressRecords = records.filter(r => ['A', 'AAAA', 'CNAME'].includes(r.type));
  // Preserve TXT/MX and every other hostname. Never remove a multi-record setup.
  if (addressRecords.length > 1) throw new Error(`${config.domain} has multiple address records; cannot safely replace its existing routing automatically`);
  const existing = addressRecords[0];
  const target = project.subdomain;
  if (!target || !target.endsWith('.pages.dev')) throw new Error('Cloudflare did not return a valid Pages target');
  if (!existing || existing.type !== 'CNAME' || existing.content !== target) {
    const body = { type: 'CNAME', name: config.domain, content: target, proxied: true, ttl: 1 };
    await api(`/zones/${zoneId}/dns_records${existing ? `/${existing.id}` : ''}`, existing ? 'PUT' : 'POST', body);
  }
  return `https://${config.domain}`;
}

export async function verifyPublication(domain, expected, fetcher = fetch, wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds))) {
  let last = '';
  for (let attempt = 0; attempt < 30; attempt++) {
    try {
      const response = await fetcher(`https://${domain}/data/dashboard.json?_=${Date.now()}`, { signal: AbortSignal.timeout(10000) });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const actual = await response.json();
      if (actual.generated_at !== expected.generated_at || actual.model_version !== expected.model_version) throw new Error('Domain is serving a different snapshot');
      return actual;
    } catch (error) {
      last = error.message;
      if (attempt < 29) await wait(10000);
    }
  }
  throw new Error(`Domain binding was submitted, but HTTPS publication is not verified yet: ${last}`);
}

async function main() {
  const command = process.argv[2];
  if (!['prepare', 'bind'].includes(command)) throw new Error('Usage: node scripts/cloudflare.mjs prepare|bind');
  const config = JSON.parse(await readFile(new URL('cloudflare.json', root), 'utf8'));
  const api = client(process.env.CLOUDFLARE_API_TOKEN);
  const ctx = await context(api, config, process.env.CLOUDFLARE_ACCOUNT_ID);
  const project = await ensureProject(api, config, ctx.accountId);
  if (command === 'prepare') {
    if (process.env.GITHUB_OUTPUT) await appendFile(process.env.GITHUB_OUTPUT, `account_id=${ctx.accountId}\nproject_name=${config.project_name}\n`);
    console.log(`Cloudflare access verified · project ${config.project_name} · zone ${config.zone_name}`);
  } else {
    const url = await bindDomain(api, config, ctx, project);
    console.log(`Domain association and DNS configured for ${url}; checking HTTPS publication.`);
    const expected = JSON.parse(await readFile(new URL('dist/data/dashboard.json', root), 'utf8'));
    const actual = await verifyPublication(config.domain, expected);
    console.log(`Published ${url} · valuation ${actual.valuation_date} · ${actual.health}`);
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
}
