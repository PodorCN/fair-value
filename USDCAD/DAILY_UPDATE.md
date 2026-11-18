# Daily agent update

The user will arrange the agent's daily schedule. This document is the job
procedure, not a created hosted automation. One agent updates data; the Cloudflare
Pages GitHub integration deploys the static frontend after the push. No API
server is needed. The custom domain is `fv.podor.org`.

1. Read the repository and USDCAD `AGENT.MD` files. Start on a clean checkout
   tracking `origin/main`, then `git pull --ff-only`. If unrelated changes are
   present, preserve them and use an isolated checkout for this update.
2. Install `USDCAD/requirements.txt` if needed. From `USDCAD`, run:

   ```bash
   python -m backend.update
   ```

   This fetches public data and recalculates all valuations and monthly
   decisions according to the frozen specification. It records actual source
   dates. Do not substitute generated prices or relabel cached data as live.
3. Inspect `data/update-status.json`. Exit 0 indicates a successful run. Exit 1
   can mean a partial source failure (degraded valuation published) or a failed
   calculation (previous valuation retained). Preserve and publish the recorded
   health status even when the source run failed; report that failure honestly.
4. Run `npm ci --ignore-scripts` if test dependencies are missing or the lockfile
   changed, then `npm test` and `npm run build`. If model validation fails, do not publish
   a changed dashboard; report the failure. Do not retune the selected model.
5. From the repository root, stage only these generated files:

   ```bash
   git add USDCAD/data/observations.csv USDCAD/data/dashboard.json USDCAD/data/update-status.json
   git diff --cached --stat
   git diff --cached --check
   ```

   If staged changes exist, commit with `data: update USDCAD fair value` and
   `git push origin HEAD:main`. Never force-push. If the remote advanced, obtain
   the new data and rerun the calculation rather than merging generated JSON.
6. The Cloudflare Pages GitHub integration builds the project named in
   `cloudflare.json` and publishes it on the data push. The **Verify fair-value
   dashboard** run re-runs the tests, waits for `https://fv.podor.org` to serve the
   pushed `generated_at`, then checks publication and live source health in
   separate steps. A red run alone does not identify which failed: a partial
   snapshot may already be live while the source-health step fails. Inspect the
   individual steps and served JSON before reporting publication status.
   `npm run smoke -- --publication-only` permits visibly degraded/cached or
   retained snapshots but does not call them fresh. `npm run smoke` (the default)
   and `npm run smoke -- --source-health` require a successful live fetch of all
   eleven sources and fresh source/valuation health. These gates stay red for
   partial, failed, cached or stale data.
7. Verify the published `data/dashboard.json` has the expected `generated_at`
   and `valuation_date`. Report the data date, source health and deployment URL.

The browser re-fetches config/data on load, manual refresh, and every five
minutes, using cache-busting URLs. New dates extend the date selector and chart
range automatically. A user viewing the latest observation follows new data;
a pinned historical date stays pinned. Cloudflare can cache files briefly,
so publication is measured by the deployed artifact and served payload.

If Statistics Canada refuses the configured public WDS request with HTTP 403,
retain the cached CPI observations and the explicit source error. Do not change
the selected `v41690973` series, rebase the index, backdate first-seen values,
rotate request identities or treat a cached calculation as source recovery.
Permanent HTTP refusals fail immediately; only transient HTTP/network failures
receive the existing bounded retry. Recovery requires a successful authorized
read of that source (with observed reference dates), followed by the normal
calculation, validation and publication procedure. A code/test pass does not
establish that provider access has recovered.

The manual **Update USDCAD fair value** workflow implements the same calculation
and commit procedure when needed. Its completion also triggers the verification
workflow, because GitHub suppresses push-triggered workflows for `GITHUB_TOKEN`
commits; the Cloudflare Pages integration receives that push independently and
still publishes. Do not enable two overlapping daily schedulers for the same data
files.
