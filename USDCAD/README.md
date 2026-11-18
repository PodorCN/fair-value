# USD/CAD Fair Value

A portfolio-manager dashboard using the final **revision 18 two-layer model**
from `case-study/USDCAD`. The backend is a daily batch calculation run by an
agent; Cloudflare Pages serves the frontend and its calculated JSON files at
the requested custom domain `fv.podor.org`.

```
Daily agent
    ↓ fetch public sources + calculate model
USDCAD/data/observations.csv + dashboard.json + update-status.json
    ↓ commit and push to GitHub
GitHub Actions → static build → Cloudflare Pages → fv.podor.org
    ↓ browser loads JSON; checks for updates every 5 minutes
Current and historical valuation dashboard
```

No persistent API server is required. Frontend source stays the same when the
agent publishes new data. It automatically extends chart/date ranges, updates
latest metrics and preserves a user's pinned historical date.

## Run locally

Requires Python 3.11+ and Node 20+. No frontend runtime dependencies or source
API keys. `linkedom` is used only for the automated frontend refresh test.

```bash
cd /workspace/fair-value/USDCAD
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
npm ci --ignore-scripts
python -m backend.update
npm test
npm run dev
```

`npm run dev` builds the static site and serves it at `http://127.0.0.1:4173`.
In another terminal, `npm run smoke` verifies that the site reads its published
JSON. `npm run build` produces `dist/`; `npm run preview` serves an existing
build. The browser needs HTTP, so do not open `index.html` with `file://`.

For an offline recalculation, `python -m backend.update --cached` uses bundled
observations and visibly marks the result as cached. It is not a live update.

## Agent updates and publication

Read [DAILY_UPDATE.md](DAILY_UPDATE.md) for the complete daily agent procedure.
The agent's schedule will be arranged by the user; no hosted agent automation is
created here. The job fetches data, recalculates, checks the model, then commits
and pushes the generated files. Only these change during a routine data update:

- `USDCAD/data/observations.csv`: durable economic observations and availability.
- `USDCAD/data/dashboard.json`: precomputed valuation, history and portfolio data.
- `USDCAD/data/update-status.json`: latest attempt's success, partial or failure.

The Cloudflare Pages GitHub integration publishes `USDCAD/dist` when frontend or
data files are pushed to `main`. The **Verify fair-value dashboard** workflow
checks tests, build, the served snapshot, and live source health in separate
steps. A source-health failure can occur after successful publication; a red run
alone does not mean the site failed to deploy. The project uses relative URLs, so it
works at `/fair-value/` without hard-coded root paths.

The **Update USDCAD fair value** workflow is a manually dispatchable fallback
for the same calculation/commit process. Its completion also triggers Pages
publication: GitHub does not emit ordinary push workflow events for commits
created using `GITHUB_TOKEN`. Do not enable two concurrent daily data writers.

Hosting metadata is in `cloudflare.json`: Pages project `podor-fair-value`,
production branch `main`, zone `podor.org`, custom domain `fv.podor.org`.
GitHub stores code and calculated data; GitHub Pages is not used.

The configured Pages GitHub integration is the publisher; verification Actions
do not create projects, change DNS or require a Cloudflare credential. The
one-off `scripts/cloudflare.mjs prepare`/`bind` operator tools are not part of
the daily update or verification path. Do not wire them back into CI. The data
updater separately needs permission to commit to the branch; branch protection
may require a repository-specific adaptation.

## Frontend behavior

The frontend reads `config.json`, `data/dashboard.json` and
`data/update-status.json` on load, manual refresh and every five minutes while
open. Every request bypasses cached prior URLs. No data-provider calls or model
calculation happens in the browser. Market dates, source health and publication
time remain visible; a failed agent run retains the last valid valuation and
publishes its failure status.

The dashboard includes aligned spot, BEER signal fair value and raw OLS fit,
residual dispersion bands, PPP, historical signals, effective/pending monthly
model weights, daily indications, driver attribution, decision CSV export,
annual coefficients and source health. The engine matches all 189 original
monthly tier/weight records. Read [MODEL.md](MODEL.md) for formulas, sign
conventions and research limitations.

## Failure and recovery

A partial fetch publishes visibly degraded results and exits 1. A total source
failure or invalid calculation preserves the previous dashboard and separately
records the failed attempt. Publish the status file so readers can see the
failure; do not pretend the retained valuation is new market data.

Daily inputs more than 7 business days old, CPI more than 65, and snapshots more
than 3 calendar days old are flagged. Successful HTTP requests alone do not
establish data freshness. All nine market inputs use a common valuation date;
newly fetched CPI released after that date enters a later valuation.

Atomic file replacement and a file lock protect local calculations. No orders,
credentials in the frontend, or external notifications are involved.

For historical reconstruction, copy `data/` to a separate directory before
using `python -m backend.update --cached --as-of YYYY-MM-DD --data-dir /copy`.
Do not replace the production snapshot with a historical reconstruction.

## Optional API and alternate hosting

`npm run dev:api` still supports a separate development API at port 8000.
`USDCAD_API_URL=https://YOUR-BACKEND.example npm run build` builds an API client
without bundled data. These are optional and are not required for static hosting.

For Cloudflare Git integration instead of the included direct-upload workflow,
use root directory `USDCAD`, build command `npm run build`, output `dist`.
Leave `USDCAD_API_URL` unset for static publication. Keep a single deployment
path active. Never put secrets in a public config file.

## Maintenance

Read [AGENT.MD](AGENT.MD). `npm test` verifies research parity, causality, source
failure handling, historical lookup and an open page consuming new JSON while
preserving historical selection. `npm run smoke` checks a served static build
(or optional API mode). No sibling research checkout is needed at runtime.
