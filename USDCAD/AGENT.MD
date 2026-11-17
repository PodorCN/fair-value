# USDCAD — maintain the valuation watcher

Read `../AGENT.MD`, `README.md`, and `MODEL.md`. The selected research authority
is `case-study/USDCAD/main.tex` (October 24, 2026) and the rev18 branch of
`case-study/TDAM/usdcad_fv/src/allocation.py`. The bundled research commit and
training hashes are in `data/provenance.json`. No sibling repo is required at runtime.

## Model invariants

- Baseline: equal average of **two** sleeve weights, not a blended fair-value price.
- BEER: log USDCAD on US–CA 2Y percentage-point spread, log WTI, VIX level,
  log broad USD. 756 business-day OLS, minimum 80% valid rows, annual business
  year-end refit. No refit just because a truncated input ends on a particular day.
- Signal: residual minus trailing 252bd mean, divided by trailing residual sample
  standard deviation; minimum `int(.8*252) = 201` observations. Nonpositive WTI
  becomes missing before log; do not replace it with zero or a positive price.
- Display both re-centered signal fair value and raw OLS fair value distinctly.
  Bands are ±1 residual sigma, not a prediction/confidence interval.
- PPP: frozen 480-month 1971–2010 relative CPI calibration. Never refit online.
  PPP adjusts a nonzero BEER tier one step outward when active and aligned,
  one step inward when opposed; active threshold |z| >= 0.5.
- Peers: AUD/NZD/NOK **per CAD**, log cross minus trailing 252bd mean, divided
  by trailing 756bd std of that deviation (minimum 504). Grade the mean z, not
  the mean of grades. Positive peer score means CAD rich → buy USD.
- Seven-tier cutoffs inclusive at 0.5/1/1.5. USD weights 0/20/35/50/65/80/100%.
  Missing sleeve contributes 50/50; both missing means no signal.
- Month-end decisions use prior business-day observations; execute 5 BDays
  later. Daily indications, pending decisions and effective model weights differ.
  These are model weights, never claim actual portfolio holdings or execution.
- Optional VIX filter is diagnostic only; never substitute it for baseline.

## Update procedure

1. From `USDCAD`, run `python -m backend.update` with public source access.
   Every run fetches inputs and recomputes the historical path, coefficients,
   daily valuations and monthly decisions. The original spec determines refits.
2. Read `data/update-status.json` and source health. Nonzero exit means a partial
   or failed run; partial snapshots are visibly degraded. Failure must retain the
   last published valuation. Do not retry indefinitely or hide failed sources.
3. Run `npm test`, then `npm run build`. Build does not perform network requests.
4. For UI-only work `python -m backend.update --cached` recalculates existing
   observations with an explicit cache label. Never present this as fresh data.
5. Follow `DAILY_UPDATE.md` to commit and push the three changed data files.
   The Cloudflare Pages GitHub integration on the project in `cloudflare.json`
   builds the site and publishes the frontend and JSON together after a data
   push. `.github/workflows/verify-publication.yml` only re-runs the tests and
   proves that `https://fv.podor.org` serves the pushed snapshot. An existing open
   page refreshes the published JSON every five minutes; do not bake model
   outputs into frontend source.

All paths in the frontend must work at both a domain root and a subpath. Use no
external CDN dependencies. All valuation arithmetic stays in Python; frontend
filtering, number formatting, chart projection and CSV export are presentation.

The primary backend is a once-daily batch job run by an agent. Cloudflare Pages
serves the static frontend and calculated data, with no persistent API service.
`npm run dev` previews this static publication. `npm run dev:api` retains the
optional API mode for development; it is not required for deployment.
After publication changes run `npm run smoke` against the preview and test that
automatic refresh accepts a new snapshot while preserving a pinned history date.
Do not create a new agent schedule unless the user requests it. The manual
GitHub refresh workflow is a fallback, not a second daily scheduled writer.

Hosting target is `fv.podor.org`, configured in `cloudflare.json`. Keep GitHub as
the source/data repository: the Cloudflare Pages GitHub integration is the only
publisher, so a data push is the deployment. No Cloudflare credential lives on the
maintenance host or in Actions. `scripts/cloudflare.mjs prepare`/`bind` stay as
one-off operator tools for a Pages/Zone token; never wire them back into CI. Only
claim the domain is live after HTTPS serves the exact pushed snapshot. If access is
missing, report the failed step; never fabricate a binding or URL status.

## Verification and data honesty

Retain the 189-month research parity fixture. Test annual refit causality,
future-release isolation, tier direction, signal/FV identity, first-seen CPI
revisions, publication failure recovery, API CORS, and frontend historical lookup.
Changes to a source adapter need a real fetch check, including observed dates.
Current data must use a common market date and retain individual source dates.

The bootstrap uses the paper's +1bd market publication assumption, not full H.10
release-time reconstruction. New CPI values/revisions are first-seen events.
The research PPP calibration uses latest-vintage CPI. Preserve these disclosures.
Never invent intraday prices, claim all inputs are current from a successful HTTP
response alone, or promote the paper's research metrics into a live backtest.
