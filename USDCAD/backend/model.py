"""Causal implementation of case-study/USDCAD, allocation revision 18.

252-day re-centering changes the signal's zero: publish both raw OLS fair value
and the re-centered fair value actually used to produce the allocation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MODEL_VERSION = "usdcad-rev18-v1"
DAILY = ["usdcad", "wti", "us2y", "ca2y", "vix", "usd_broad", "audusd", "nzdusd", "usdnok"]
DRIVERS = ["rate_diff_2y", "ln_wti", "vix", "ln_usd_broad"]
LABELS = {"rate_diff_2y": "US − Canada 2Y", "ln_wti": "WTI crude", "vix": "VIX", "ln_usd_broad": "Broad USD"}


def grade(x: pd.Series) -> pd.Series:
    n = sum((x.abs() >= t).astype(float) for t in (0.5, 1.0, 1.5))
    return (np.sign(x) * n).where(x.notna())


def weight(t: pd.Series) -> pd.Series:
    a = np.array([0.0, 0.15, 0.30, 0.50])
    return (0.5 + np.sign(t) * a[t.abs().fillna(0).astype(int).clip(0, 3)]).where(t.notna())


def build_panel(observations: pd.DataFrame, end: pd.Timestamp) -> pd.DataFrame:
    """Obs-time panel, matching the paper. CPI is keyed by available date."""
    cal = pd.bdate_range("2001-01-02", end)
    cols = {}
    for name, g in observations.groupby("series"):
        if name in DAILY:
            s = g.sort_values("obs_date").drop_duplicates("obs_date", keep="last").set_index("obs_date").value
            cols[name] = s.reindex(s.index.union(cal)).ffill().reindex(cal)
        else:
            state, events = {}, []
            for r in g.sort_values(["available_date", "obs_date"]).itertuples():
                state[r.obs_date] = r.value
                events.append((r.available_date, state[max(state)]))
            ev = pd.DataFrame(events, columns=["date", "value"]).drop_duplicates("date", keep="last")
            m = pd.merge_asof(pd.DataFrame({"date": cal}), ev, on="date")
            cols[name] = pd.Series(m.value.to_numpy(), index=cal)
    return pd.DataFrame(cols, index=cal)


def calculate(panel: pd.DataFrame, training: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    p = panel
    y = np.log(p.usdcad)
    X = pd.DataFrame({"rate_diff_2y": p.us2y - p.ca2y,
                      "ln_wti": np.log(p.wti.where(p.wti > 0)), "vix": p.vix,
                      "ln_usd_broad": np.log(p.usd_broad.where(p.usd_broad > 0))})
    fits = []
    for i, tau in enumerate(p.index):
        if not pd.offsets.BYearEnd().is_on_offset(tau):
            continue
        win = pd.concat([y.rename("y"), X], axis=1).iloc[max(0, i - 755):i + 1].dropna()
        if len(win) < 756 * 0.8:
            continue
        a = np.column_stack([np.ones(len(win)), win[DRIVERS]])
        beta = np.linalg.lstsq(a, win.y, rcond=None)[0]
        fits.append({"date": tau, "intercept": beta[0], **dict(zip(DRIVERS, beta[1:])), "n": len(win)})
    if not fits:
        raise ValueError("Insufficient history for annual 756-business-day BEER fit")
    fits = pd.DataFrame(fits).set_index("date")
    c = fits.reindex(p.index, method="ffill")
    ln_fv = c.intercept + (c[DRIVERS] * X).sum(axis=1, min_count=4)
    resid = y - ln_fv
    mean = resid.rolling(252, min_periods=201).mean()  # int(0.8 * 252), paper's correction
    sd = resid.rolling(252, min_periods=201).std().replace(0, np.nan)
    z = (resid - mean) / sd
    train = training.loc["1971-01-01":"2010-12-31"].dropna()
    dev = np.log(train.spot) - np.log(train.us_cpi / train.ca_cpi)
    anchor, sigma = float(dev.mean()), float(dev.std())
    ppp_ln = np.log(p.us_cpi / p.ca_cpi) + anchor
    out = pd.DataFrame({"spot": p.usdcad, "beer_raw_fv": np.exp(ln_fv),
                        "beer_fv": np.exp(ln_fv + mean), "beer_z": z,
                        "beer_gap_pct": np.expm1(resid - mean) * 100,
                        "band_low": np.exp(ln_fv + mean - sd), "band_high": np.exp(ln_fv + mean + sd),
                        "ppp_fv": np.exp(ppp_ln), "ppp_z": (y - ppp_ln) / sigma,
                        "vix_z": (np.log(p.vix) - np.log(p.vix).rolling(252).mean()) / np.log(p.vix).rolling(252).std()})
    out["refit_date"] = pd.Series(fits.index, index=fits.index).reindex(p.index, method="ffill")
    for ccy in ("aud", "nzd", "nok"):
        fx = -np.log(p[f"{ccy}usd"]) if ccy != "nok" else np.log(p.usdnok)
        cross = fx - y  # foreign currency per CAD; + = CAD rich = buy USD
        d = cross - cross.rolling(252, min_periods=252).mean()
        out[f"peer_{ccy}_z"] = d / d.rolling(756, min_periods=504).std().replace(0, np.nan)
    out["peer_z"] = out[[f"peer_{c}_z" for c in ("aud", "nzd", "nok")]].mean(axis=1, skipna=False)
    gb = grade(-z)
    xp = -out.ppp_z
    active = xp.abs() >= 0.5
    agree = active & (np.sign(xp) == np.sign(gb)) & (gb != 0)
    oppose = active & (np.sign(xp) == -np.sign(gb)) & (gb != 0)
    t1 = gb.where(~agree, np.sign(gb) * np.minimum(3, gb.abs() + 1))
    t1 = t1.where(~oppose, np.sign(gb) * (gb.abs() - 1)).where(xp.notna() & z.notna())
    out["beer_tier"], out["layer1_tier"], out["layer2_tier"] = gb, t1, grade(out.peer_z)
    out["ppp_state"] = np.select([gb.eq(0), ~active, agree], ["BEER neutral", "Neutral", "Agrees"], "Opposes")
    out["layer1_usd"], out["layer2_usd"] = weight(t1), weight(out.layer2_tier)
    # In the selected paper a missing sleeve is allocated 50/50, explicitly.
    out["usd_weight"] = (out.layer1_usd.fillna(0.5) + out.layer2_usd.fillna(0.5)) / 2
    out["usd_weight"] = out.usd_weight.where(out.layer1_usd.notna() | out.layer2_usd.notna())
    out["vix_variant_usd"] = out.usd_weight.where(out.vix_z <= 2, out.usd_weight.clip(lower=0.5))
    for name in DRIVERS:
        out[f"driver_{name}"] = X[name]
        out[f"beta_{name}"] = c[name]
    out["beta_intercept"] = c.intercept
    return out, fits, {"constant": anchor, "sigma": sigma, "train_start": str(train.index.min().date()),
                       "train_end": str(train.index.max().date()), "months": len(train)}


def monthly_decisions(values: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Month-end reads the previous business day's model; execute at month-end + 5bd.

    A truncated run never promotes the final partial month into a decision.
    """
    rows = []
    for d in pd.date_range("2011-01-01", as_of, freq="BME"):
        cutoff = d - pd.offsets.BDay(1)
        if cutoff not in values.index:
            continue
        row = values.loc[cutoff].to_dict()
        row.update(date=d, data_through=cutoff, execution_date=d + pd.offsets.BDay(5))
        rows.append(row)
    return pd.DataFrame(rows)
