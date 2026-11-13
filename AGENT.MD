# Fair Value — agent maintenance instructions

This repository hosts independently deployable currency fair-value monitors.
The user selected a daily agent batch calculation followed by static GitHub
Pages publication. Maintain that architecture. Do not place orders or send
notifications without authorization.

## Structure and boundaries

- Each instrument lives in its own folder. Start with `USDCAD/AGENT.MD`.
- Keep data acquisition and all model computations in the backend. Frontends
  consume versioned, precomputed JSON on GitHub Pages. Cloudflare Pages remains
  compatible with the same static build; no always-on API host is required.
- Preserve selected research specifications. Do not optimize, add predictive
  models, or silently change thresholds when maintaining a watcher.
- Keep API credentials out of Git, built static assets, and browser requests.
- Preserve provenance, availability dates, source failures, and stale indicators.
  Never label cached observations or generated timestamps as live market data.
- Publish new snapshots atomically only after validation. A failed update must
  preserve the last valid dashboard and record its failure separately.

## Working agreement

Read the instrument's README and model notes before editing. Changes to model
semantics require an explicit user request, a model-version change, documented
research impact, and appropriate numerical and causality checks. Operational
repairs and UI improvements should preserve the model contract.

Use the existing public-source adapters before adding providers. Run the
instrument's tests and static build after relevant changes. Inspect generated
assets for secrets and stale/failure behavior. Report data date, verification,
and deployment state accurately. Do not modify the source `case-study` repo as
part of dashboard maintenance.
