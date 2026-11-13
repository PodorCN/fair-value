# USD/CAD Fair Value

A portfolio-manager dashboard and an actual public-data watcher, implementing
the final **revision 18 two-layer allocation** in `case-study/USDCAD`.

It shows aligned spot, BEER signal fair value and raw OLS fit, residual bands,
PPP, historical scores, effective/pending/month-end model weights, a daily
indication, driver attribution, decision history, coefficients and source health.
The bundled historical calculation matches all 189 original monthly tier and
weight records. Read [MODEL.md](MODEL.md) for formulas and timing limitations.

## Run locally

Requires Python 3.11+ and Node 20+. No frontend dependencies or API keys.

```bash
cd /workspace/fair-value/USDCAD
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m backend.update       # real fetch + complete recalculation
npm test
npm run dev                   # frontend :4173 + independent API :8000
```

In another terminal:

```bash
cd /workspace/fair-value/USDCAD
npm run smoke                 # verifies frontend → backend, CORS, and last update
```

To reproduce from bundled observations without network:

```bash
python -m backend.update --cached
```

This visibly marks the snapshot as cached; it is not a live refresh. Do not open
`index.html` through `file://`, because the frontend fetches JSON over HTTP.

## Frontend/backend separation

```
FRED + BoC + StatCan
         ↓
Python update worker → observations + frozen calibration
         ↓                      ↓
 full model recalculation → validated dashboard.json + update-status.json
                                ↓
                  static frontend OR read-only API
```

`npm run dev` starts two independent processes: the frontend at
`http://127.0.0.1:4173` and the Python API at `http://127.0.0.1:8000`.
The frontend fetches the API over HTTP with explicit CORS. Its build contains
no bundled market snapshots in this mode. Stop both processes with Ctrl+C.

`backend/` owns all economics and model calculations. `frontend/` owns filtering,
charts and formatting. It never fetches market providers or runs OLS. The build
copies frontend files to `dist/`; API mode does not read or copy backend data.
Chart clicks/date selection inspect history; CSV export downloads the decision
ledger. “Refresh snapshot” reloads published results, not a privileged backend job.
It polls for new snapshots every five minutes while open.

For an always-running separate worker and API:

```bash
python -m backend.update --watch --interval 3600
python -m backend.server --host 0.0.0.0 --port 8000 \
  --cors-origin https://YOUR-PROJECT.pages.dev
```

Serve that API through HTTPS behind your backend host/reverse proxy. It exposes
only `GET /api/v1/dashboard` and `GET /api/v1/status`. There is no public update
endpoint. Then build the frontend with:

```bash
USDCAD_API_URL=https://YOUR-BACKEND.example npm run build
```

The variable is a public backend URL, not a secret. CORS must match the
frontend's exact origin. For the optional snapshot-only distribution,
`npm run build && npm run preview` (without `USDCAD_API_URL`) bundles the
already-calculated `dist/data/` snapshots instead.

## Cloudflare Pages (prepared, not deployed)

Connect this repository to Pages with these settings:

| Setting | Value |
|---|---|
| Root directory | `USDCAD` |
| Build command | `npm run build` |
| Output directory | `dist` |
| Node version | `24` (or 20+) |
| Environment variable (API mode) | `USDCAD_API_URL=https://YOUR-BACKEND.example` |
| Production branch | your chosen repository branch |

Cloudflare Pages runs only the static build. It does not run the Python watcher.
Use a separate backend host for the API, or choose the snapshot distribution:

1. **Separate backend:** keep the worker/API on a Python host, use the API URL
   build variable above, and the frontend refreshes without a Pages rebuild.
2. **Git snapshot path:** the included GitHub Actions workflow runs the Python
   backend on weekdays at 23:30 UTC, validates, and commits `USDCAD/data`.
   Pages Git integration rebuilds when that commit arrives. The workflow is
   also manually dispatchable and supports repository branches selected in GitHub.

The Git workflow needs Actions enabled and workflow write permission on the
target branch. Branch protection may require adapting the commit step to your
repository policy. Once this workflow is on GitHub's default branch,
its schedule can run there. Hosting the website still requires connecting a
Pages project; pushing the repository does not deploy a backend service.

At 23:30 UTC the schedule is 18:30 Toronto in winter and 19:30 in summer. FX and
broad USD can still lag the latest daily rates. The page displays the actual
common data date; it never labels a run timestamp as a current quote.

## Failure and recovery

- One source failure keeps its existing observations, publishes an explicitly
  degraded snapshot, and exits 1. The scheduled workflow saves the result and
  ends red so operational failure remains visible.
- Total source failure or invalid calculation leaves the previous dashboard
  unchanged; `update-status.json` records the failed attempt. The frontend shows
  that failure even when it can still display the prior valuation.
- Source staleness is independent of fetch success. Daily inputs over 7 business
  days old, CPI over 65, and browser snapshots over 3 calendar days are flagged.
- Atomic replacement prevents half-written JSON; a file lock prevents overlapping
  local workers. No credentials, trading actions, or outbound alerts are required.
- For reconstruction, use `python -m backend.update --cached --as-of 2020-03-31
  --data-dir /path/to/copy-of-data`. Always copy the data folder first so a
  reconstruction does not replace the production snapshot.

## Maintenance

Read [AGENT.MD](AGENT.MD). `npm test` verifies research parity, temporal isolation,
annual refits, bands, tier boundaries, CPI revisions, failure recovery, API
boundaries and frontend historical lookup. `npm run build` verifies a publishable
static bundle. No runtime import or data read outside this folder is needed.
