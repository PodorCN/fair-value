import { cp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
const out = path.join(root, 'dist');
const api = process.env.USDCAD_API_URL?.replace(/\/$/, '');
if (api) {
  const parsed = new URL(api);
  if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error('USDCAD_API_URL must be an HTTP(S) backend URL without credentials, query, or fragment');
  }
}
// In API mode this build is independent of all backend data files.
// Remove any bundled snapshots left by a previous static build.
await rm(path.join(out, 'data'), { recursive: true, force: true });
await mkdir(out, { recursive: true });
for (const name of ['index.html', 'app.js', 'view.js', 'styles.css', '_headers']) {
  await cp(path.join(root, 'frontend', name), path.join(out, name));
}
let payload;
if (!api) {
  payload = JSON.parse(await readFile(path.join(root, 'data/dashboard.json'), 'utf8'));
  if (payload.schema_version !== 1 || !payload.latest?.beer_fv || !payload.history?.length) {
    throw new Error('Run python -m backend.update before building: invalid snapshot');
  }
  await mkdir(path.join(out, 'data'), { recursive: true });
  for (const name of ['dashboard.json', 'update-status.json']) {
    await cp(path.join(root, 'data', name), path.join(out, 'data', name));
  }
}
await writeFile(path.join(out, 'config.json'), JSON.stringify({
  dashboardUrl: api ? `${api}/api/v1/dashboard` : './data/dashboard.json',
  statusUrl: api ? `${api}/api/v1/status` : './data/update-status.json',
}));
console.log(api ? `Built ${out} · independent API backend ${api}` : `Built ${out} · valuation ${payload.valuation_date} · ${payload.health}`);
