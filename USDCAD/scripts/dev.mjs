import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const python = process.env.USDCAD_PYTHON || 'python';
const api = 'http://127.0.0.1:8000';
const frontend = 'http://127.0.0.1:4173';
const children = new Set();
let closing = false;

function stop(code) {
  if (closing) return;
  closing = true;
  for (const child of children) child.kill('SIGTERM');
  process.exitCode = code;
}
function launch(command, args, env = process.env) {
  const child = spawn(command, args, { cwd: root, env, stdio: 'inherit' });
  children.add(child);
  child.on('error', error => { console.error(error.message); stop(1); });
  child.on('exit', code => {
    children.delete(child);
    if (!closing && command === python) stop(code || 1);
  });
  return child;
}
const build = launch(process.execPath, ['frontend/build.mjs'], { ...process.env, USDCAD_API_URL: api });
const built = await new Promise(resolve => {
  build.once('error', () => resolve(false));
  build.once('exit', code => resolve(code === 0));
});
if (!built) { stop(1); } else {
  process.on('SIGINT', () => stop(0));
  process.on('SIGTERM', () => stop(0));
  launch(python, ['-m', 'backend.server', '--port', '8000', '--cors-origin', frontend]);
  launch(python, ['-m', 'http.server', '4173', '--directory', 'dist', '--bind', '127.0.0.1']);
  console.log(`Frontend ${frontend}\nBackend  ${api}/api/v1/dashboard\nRun npm run smoke in another terminal to verify the connection.`);
}
