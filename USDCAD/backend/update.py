"""Recalculate, validate, then atomically publish a self-contained dashboard snapshot."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

try:  # POSIX
    import fcntl
except ImportError:  # Windows: no flock; the lock is a one-byte msvcrt range lock.
    fcntl = None
    import msvcrt

import numpy as np
import pandas as pd

from .data import SOURCE_INFO, read_observations, refresh
from .model import DAILY, DRIVERS, LABELS, MODEL_VERSION, build_panel, calculate, monthly_decisions

ROOT = Path(__file__).resolve().parents[1]


def clean(x):
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if x is None or x is pd.NaT or pd.isna(x):
        return None
    if isinstance(x, (pd.Timestamp, datetime)):
        return x.isoformat()[:10]
    if isinstance(x, (float, np.floating)):
        return round(float(x), 8) if np.isfinite(x) else None
    if isinstance(x, np.integer):
        return int(x)
    return x


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="" keeps the bytes identical on Windows: no CRLF translation, so the
    # published observations still hash to the value recorded in the snapshot.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False) as f:
        temp = Path(f.name)
        try:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def lock_exclusive(handle):
    """Non-blocking exclusive lock on an open file; raises BlockingIOError if held."""
    if fcntl is not None:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as exc:  # Windows reports contention as a generic OSError.
        raise BlockingIOError(str(exc)) from exc


def json_text(value):
    return json.dumps(clean(value), ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"


def make_snapshot(obs, training, today, mode, errors, successes, provenance):
    # A single valuation uses a common observation date across all nine market inputs.
    # Never carry an absent driver forward into a newly dated fair value.
    dates = [obs[(obs.series == n) & (obs.available_date <= today)].obs_date.max() for n in DAILY]
    end = min([today - pd.offsets.BDay(1), *dates])
    if pd.isna(end):
        raise ValueError("A required market input has no usable observation")
    panel = build_panel(obs, end)
    values, fits, ppp = calculate(panel, training)
    valid = values.dropna(subset=["beer_fv", "ppp_fv", "peer_z"])
    if valid.empty:
        raise ValueError("No complete valuation can be published")
    row = valid.iloc[-1]
    aligned_date = valid.index[-1]
    monthly = monthly_decisions(values, today)
    if monthly.empty:
        raise ValueError("No completed monthly decisions")
    executed = monthly[monthly.execution_date <= today]
    held = executed.iloc[-1].to_dict() if len(executed) else None
    pending = monthly[monthly.execution_date > today]
    latest = row.to_dict()
    latest["date"] = aligned_date
    history_cols = ["spot", "beer_fv", "beer_raw_fv", "band_low", "band_high", "beer_z", "beer_gap_pct",
                    "ppp_fv", "ppp_z", "peer_z", "peer_aud_z", "peer_nzd_z", "peer_nok_z",
                    "layer1_tier", "layer2_tier", "layer1_usd", "layer2_usd", "usd_weight", "ppp_state", "vix_z"]
    history = valid.loc["2011-01-01":, history_cols].rename_axis("date").reset_index().to_dict("records")
    monthly_cols = ["date", "data_through", "execution_date", "spot", "beer_fv", "ppp_fv", "beer_z", "peer_z",
                    "layer1_tier", "layer2_tier", "layer1_usd", "layer2_usd", "usd_weight", "ppp_state"]
    source_rows = []
    for name, info in SOURCE_INFO.items():
        g = obs[(obs.series == name) & (obs.available_date <= today)].sort_values(["obs_date", "available_date"])
        r = g.iloc[-1]
        used = g[g.obs_date <= aligned_date] if name in DAILY else g[g.available_date <= aligned_date]
        used = used.iloc[-1]
        age = len(pd.bdate_range(r.obs_date, today)) - 1
        stale = age > (65 if name.endswith("cpi") else 7)
        source_rows.append({**info, "name": name, "observation_date": r.obs_date, "available_date": r.available_date,
                            "value": r.value, "age_business_days": age,
                            "valuation_observation_date": used.obs_date,
                            "valuation_available_date": used.available_date, "valuation_value": used.value,
                            "status": "error" if name in errors else "stale" if stale else "ok",
                            "error": errors.get(name), "fetched_this_run": name in successes})
    lag = len(pd.bdate_range(aligned_date, today)) - 1
    stale = lag > 7 or any(s["status"] == "stale" for s in source_rows)
    # Explain a 21-business-day change in RAW OLS FV exactly, including any annual refit.
    prev = values.loc[:aligned_date].iloc[-22]
    drivers = []
    for name in DRIVERS:
        change = row[f"beta_{name}"] * (row[f"driver_{name}"] - prev[f"driver_{name}"])
        drivers.append({"key": name, "label": LABELS[name], "value": row[f"driver_{name}"],
                        "beta": row[f"beta_{name}"], "contribution_log_bp": change * 10000})
    raw_change = np.log(row.beer_raw_fv / prev.beer_raw_fv) * 10000
    refit_change = raw_change - sum(d["contribution_log_bp"] for d in drivers)
    notices = []
    if mode == "cached":
        notices.append({"severity": "warning", "title": "Research cache", "detail": "Recalculated from bundled observations. No live sources fetched in this run."})
    if stale:
        notices.append({"severity": "warning", "title": "Inputs are delayed", "detail": f"Common valuation date is {aligned_date.date()}, {lag} business days behind this run. Check source health before using the reading."})
    if errors:
        notices.append({"severity": "warning", "title": "Partial source failure", "detail": "Cached observations retained for: " + ", ".join(errors)})
    if row.layer1_tier * row.layer2_tier < 0:
        notices.append({"severity": "info", "title": "Layers disagree", "detail": "Macro valuation and commodity peers point in opposite directions; the selected model averages the two allocations."})
    if row.vix_z > 2:
        notices.append({"severity": "info", "title": "VIX stress flag", "detail": "The research VIX-filter variant is active. The selected baseline allocation remains the two-layer model."})
    return clean({"schema_version": 1, "model_version": MODEL_VERSION,
                  "generated_at": datetime.now(timezone.utc).isoformat(), "run_date": str(today.date()),
                  "mode": mode, "health": "stale" if stale else "degraded" if errors else "cached" if mode == "cached" else "ok",
                  "valuation_date": aligned_date, "latest": latest,
                  "allocation": {"held": held, "last_decision": monthly.iloc[-1].to_dict(),
                                 "pending": pending.to_dict("records"), "next_decision": today + pd.offsets.BMonthEnd(1),
                                 "calendar": "Weekdays; paper uses BDay, without exchange holiday adjustments."},
                  "history": history, "monthly": monthly[monthly_cols].to_dict("records"),
                  "drivers": drivers, "raw_fv_change_log_bp": raw_change, "refit_change_log_bp": refit_change,
                  "sources": source_rows, "notices": notices, "ppp_calibration": ppp,
                  "coefficients": fits.rename_axis("date").reset_index().to_dict("records"),
                  "provenance": provenance,
                  "methodology": {"name": "Two-layer allocation · revision 18", "train_bd": 756, "z_bd": 252,
                                  "z_min_obs": 201, "peer_ma_bd": 252, "peer_sd_bd": 756, "peer_min_obs": 504,
                                  "thresholds": [0.5, 1.0, 1.5], "usd_weights": [0, 0.2, 0.35, 0.5, 0.65, 0.8, 1],
                                  "market_lag_bd": 1, "execution_lag_bd": 5,
                                  "fv_definition": "Signal fair value = exp(raw OLS log FV + trailing 252bd residual mean). Bands are ±1 trailing residual standard deviation, not confidence intervals.",
                                  "cpi_policy": "Bootstrap retains research vintages. New CPI values use their first fetch date; FRED latest-vintage data is not ALFRED. Frozen PPP calibration is never refitted.",
                                  "limitations": ["Public daily fixings; this is not an intraday quote feed.",
                                                  "Historical market availability assumes +1 business day; strict H.10 weekly timing reduced the paper IR from 0.50 to 0.38.",
                                                  "Historical observations may be revised by providers. The last 14 days of daily inputs are refreshed; new CPI revisions are first-seen events.",
                                                  "Paper: full-period IR 0.50; 2019 onward IR 0.04; DSR 0.32 after 261 trials. A valuation tilt, not established alpha."]}})


def update(data_dir=ROOT / "data", live=True, as_of=None):
    data_dir = Path(data_dir)
    today = pd.Timestamp(as_of or datetime.now(timezone.utc).date()).normalize()
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / ".update.lock").open("w") as lock:
        try:
            lock_exclusive(lock)
        except BlockingIOError:
            raise RuntimeError("Another USDCAD update is already running")
        started = datetime.now(timezone.utc).isoformat()
        try:
            obs = read_observations(data_dir / "observations.csv")
            training = pd.read_csv(data_dir / "ppp_training.csv", index_col="date", parse_dates=True)
            provenance = json.loads((data_dir / "provenance.json").read_text())
            errors, successes = {}, []
            if live:
                obs, errors, successes = refresh(obs, today)
                if not successes:
                    raise RuntimeError("All live sources failed; keeping last published snapshot: " + json.dumps(errors))
            snapshot = make_snapshot(obs, training, today, "live" if live else "cached", errors, successes, provenance)
            raw = obs.to_csv(index=False, lineterminator="\n")
            snapshot["observations_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
            body = json_text(snapshot)  # Validate all JSON before touching publication files.
            if len(snapshot["history"]) < 1000 or snapshot["latest"]["beer_fv"] <= 0:
                raise ValueError("Invalid dashboard payload")
            status = {"started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
                      "result": "partial" if errors else "success", "mode": snapshot["mode"],
                      "valuation_date": snapshot["valuation_date"], "errors": errors, "sources_fetched": successes}
            if live:
                atomic_write(data_dir / "observations.csv", raw)
            atomic_write(data_dir / "dashboard.json", body)
            atomic_write(data_dir / "update-status.json", json_text(status))
            print(f"Published {snapshot['valuation_date']} · {snapshot['health']} · {len(snapshot['history'])} historical rows")
            return 1 if errors else 0
        except Exception as exc:
            atomic_write(data_dir / "update-status.json", json_text({"started_at": started,
                         "finished_at": datetime.now(timezone.utc).isoformat(), "result": "failed", "error": str(exc)}))
            raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cached", action="store_true", help="Recalculate existing observations without network")
    p.add_argument("--as-of", help="Reconstruct the reading as of YYYY-MM-DD")
    p.add_argument("--data-dir", type=Path, default=ROOT / "data")
    p.add_argument("--watch", action="store_true", help="Run periodically in a separate backend process")
    p.add_argument("--interval", type=int, default=3600, help="Seconds between watcher runs (minimum 60)")
    args = p.parse_args()
    if args.as_of and not args.cached:
        p.error("--as-of requires --cached; historical reconstructions must not fetch today's vintages")
    if args.interval < 60:
        p.error("--interval must be at least 60 seconds")
    while True:
        try:
            code = update(args.data_dir, not args.cached, args.as_of)
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            code = 1
        if not args.watch:
            return code
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
