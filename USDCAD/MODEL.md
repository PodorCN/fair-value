# USDCAD selected specification

Research authority: `case-study/USDCAD/main.tex`, 2026-10-24, and
`TDAM/usdcad_fv/src/allocation.py`, **revision 18**. Earlier five-year BEER,
three-month peer tranches, PCA sleeves, and VIX-filter variants are not baseline.

## Layer 1: BEER with PPP tier adjustment

At each business year-end fit OLS on the last 756 business-day rows (at least
604.8 valid rows, so at least 605):

```
log(S) = intercept + b1*(US2Y - CA2Y) + b2*log(WTI) + b3*VIX + b4*log(broad USD)
e = log(S) - fitted log(S)
z_BEER = (e - mean_252(e)) / sample_std_252(e)       # min 201 valid rows
x_BEER = -z_BEER                                  # positive = buy USD
raw OLS FV = exp(fitted log(S))
signal FV = exp(fitted log(S) + mean_252(e))
gap percent = 100 * (S / signal FV - 1)
band = signal FV * exp(±sample_std_252(e))
```

The source uses HAC for inference; HAC does not change the OLS coefficient
estimates. This watcher uses NumPy least squares and makes no HAC significance
claims. Positive rate spread is in percentage points, VIX in index points.
Negative WTI on 2020-04-20 becomes missing before log, matching the source.

PPP is relative CPI with a unit slope. On 480 monthly observations, 1971–2010,
freeze `c = mean(log(S) - log(CPI_US/CPI_CA))` and its sample standard deviation
`sigma`. Here `c = -0.38022428259605334`, `sigma = 0.16183876167797648`.
Online: `log(PPP FV) = log(CPI_US/CPI_CA) + c`, `x_PPP = -(log(S)-log(PPP FV))/sigma`.

Grade x_BEER at inclusive |x| cutoffs 0.5, 1 and 1.5. When |x_PPP| >= 0.5,
move a nonzero BEER grade one tier out if its sign agrees with PPP, one tier
toward zero otherwise. Clip to [-3,+3]. A neutral BEER grade stays neutral.

## Layer 2: commodity peers

For AUD, NZD and NOK, construct `log(foreign currency per CAD)` by subtracting
log(USDCAD) from log(foreign currency per USD). Invert FRED AUD/USD and NZD/USD;
FRED USD/NOK is already quoted in the required direction.

```
d_j = log(cross_j) - mean_252(log(cross_j))
z_j = d_j / sample_std_756(d_j)       # min 504 valid deviations
peer_score = mean(z_AUD, z_NZD, z_NOK) # requires all three
T2 = grade(peer_score)
```

Positive peer score means CAD rich against its peers, hence buy USD.

## Portfolio and timing

| Tier | −3 | −2 | −1 | 0 | +1 | +2 | +3 |
|---|---:|---:|---:|---:|---:|---:|---:|
| USD weight | 0% | 20% | 35% | 50% | 65% | 80% | 100% |

Portfolio USD weight is the average of the two sleeve weights. A missing sleeve
sits at 50/50; both missing means unavailable. No blended price fair value is
defined by this allocation model. PPP and BEER prices stay separate.

Daily observations are indicative. A completed business month-end reads the
prior BDay's values; its weight becomes effective 5 BDays later. A partial month
never becomes a decision. Weekdays follow pandas BDay, matching the source;
there is no exchange holiday calendar adjustment. No order entry is implemented.

## Source, vintage and revision policy

The seed contains the source PIT observations (including ALFRED US CPI events),
plus frozen PPP training. Public live adapters use FRED fredgraph CSV, BoC Valet
and StatCan WDS. No API key is needed. Daily data are refreshed from 14 calendar
days before the last cached observation, including any outage gap. Earlier daily
history is preserved; recent provider revisions can change recent computed history.
Do not represent this as a complete archive of previously published live signals.

New CPI values and revisions are recorded on their first fetch date, preventing
today's FRED latest vintage from being backdated into historical signals. Older
reference-month revisions cannot replace a newer reference month. This is more
conservative than exact release-time availability and may delay an online signal.
The training calibration uses latest-vintage CPI as disclosed by the paper.

The common valuation date is the earliest last usable observation date across
all nine market inputs, capped at the previous business day. Each source keeps
its own observation date. This prevents today's VIX and last week's FX from
being labeled as a same-day fair value. Source HTTP success is distinct from freshness.

The paper's +1bd historical market availability assumption is preserved; strict
H.10 weekly release timing is not claimed. Reported IR 0.50 falls to 0.38 under
that stricter timing; 2019 onward IR is 0.04, and the deflated measure is 0.32
after 261 trials. The dashboard shows valuation context, not demonstrated alpha.

## Published contract

`data/dashboard.json`, schema version 1, contains run metadata, health, latest
aligned valuation, daily history, completed monthly decisions, effective/pending
allocation, source dates/errors, driver attribution, annual coefficients, frozen
calibration, provenance and methodology. `data/update-status.json` separately
reports the most recent update attempt, even if the valuation publication failed.

Driver attribution explains the latest **raw OLS log FV** change over 21 BDays.
It uses current beta times each input change. A separate refit contribution
reconciles coefficient changes. It does not attribute the signal's residual
re-centering or imply causal economic effects. Values are log basis points.
