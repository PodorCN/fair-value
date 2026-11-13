"""Public source adapters and durable observations. No credentials in the browser.

Research seed is preserved. New CPI prints/revisions are recorded no earlier than
the first successful fetch, so later releases cannot rewrite historical signals.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from io import StringIO
import json
import time
from urllib.request import Request, urlopen

import pandas as pd

FRED = {"usdcad": "DEXCAUS", "wti": "DCOILWTICO", "us2y": "DGS2", "vix": "VIXCLS",
        "usd_broad": "DTWEXBGS", "audusd": "DEXUSAL", "nzdusd": "DEXUSNZ", "usdnok": "DEXNOUS", "us_cpi": "CPIAUCSL"}
SOURCE_INFO = {**{k: {"provider": "FRED", "series_id": v,
                       "url": f"https://fred.stlouisfed.org/series/{v}"} for k, v in FRED.items()},
               "ca2y": {"provider": "Bank of Canada", "series_id": "BD.CDN.2YR.DQ.YLD",
                        "url": "https://www.bankofcanada.ca/valet/observations/BD.CDN.2YR.DQ.YLD/json"},
               "ca_cpi": {"provider": "Statistics Canada", "series_id": "v41690973",
                          "url": "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1810000401"}}


def read_observations(path):
    return pd.read_csv(path, parse_dates=["obs_date", "available_date"])


def request(url: str, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"User-Agent": "USDCAD-FairValue/1.0 (public economic data)", "Accept": "application/json,text/csv"}
    if body:
        headers["Content-Type"] = "application/json"
    for attempt in range(3):
        try:
            with urlopen(Request(url, data=body, headers=headers), timeout=25) as res:
                return res.read().decode("utf-8-sig")
        except Exception:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def fetch(name, start, today):
    if name in FRED:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={FRED[name]}&cosd={start}&coed={today}"
        df = pd.read_csv(StringIO(request(url)))
        if len(df.columns) != 2:
            raise ValueError("Unexpected FRED CSV shape")
        df.columns = ["obs_date", "value"]
        df["value"] = pd.to_numeric(df.value, errors="coerce")
    elif name == "ca2y":
        code = "BD.CDN.2YR.DQ.YLD"
        rows = json.loads(request(f"https://www.bankofcanada.ca/valet/observations/{code}/json?start_date={start}&end_date={today}"))["observations"]
        df = pd.DataFrame([{"obs_date": r["d"], "value": float(r[code]["v"])} for r in rows if code in r and r[code].get("v")])
    else:
        rows = json.loads(request("https://www150.statcan.gc.ca/t1/wds/rest/getDataFromVectorsAndLatestNPeriods",
                                  [{"vectorId": 41690973, "latestN": 24}]))
        if rows[0]["status"] != "SUCCESS":
            raise ValueError("StatCan request unsuccessful")
        points = rows[0]["object"]["vectorDataPoint"]
        df = pd.DataFrame([{"obs_date": p["refPer"], "value": float(p["value"])} for p in points])
    df["obs_date"] = pd.to_datetime(df.obs_date)
    df = df.dropna().sort_values("obs_date").drop_duplicates("obs_date", keep="last")
    df = df[(df.obs_date <= today) & (df.obs_date >= start)]
    if df.empty:
        raise ValueError("Source returned no observations")
    if name != "wti" and (df.value <= 0).any():
        raise ValueError("Source returned non-positive values")
    return df


def refresh(existing, today):
    """Fetch independently, retain failed sources, return visible per-source errors.

    Daily history through the previous successful cache is immutable. Refresh the
    last 14 calendar days only; older revisions never silently rewrite the seed.
    """
    results, errors = {}, {}
    starts = {n: str((min(today, existing[existing.series == n].obs_date.max())
                      - pd.Timedelta(days=730 if n.endswith("cpi") else 14)).date()) for n in SOURCE_INFO}
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {pool.submit(fetch, n, starts[n], str(today.date())): n
                for n in SOURCE_INFO}
        for f in as_completed(jobs):
            n = jobs[f]
            try:
                results[n] = f.result()
            except Exception as exc:
                errors[n] = f"{type(exc).__name__}: {str(exc)[:180]}"
    out = existing.copy()
    for n, df in results.items():
        old = out[out.series == n]
        if n.endswith("cpi"):
            # Record changes as first-seen events. Latest-vintage FRED data is not ALFRED.
            last = old.sort_values("available_date").drop_duplicates("obs_date", keep="last").set_index("obs_date").value
            df = df[df.apply(lambda r: r.obs_date not in last or abs(last.loc[r.obs_date] - r.value) > 1e-9, axis=1)].copy()
            df["available_date"] = today
            df["rule"] = "realtime"
        else:
            # Use the same daily publication assumption as the paper, retain actual obs dates.
            df = df.copy()
            df["available_date"] = df.obs_date + pd.offsets.BDay(1)
            df["rule"] = "bd_lag"
            out = out[~((out.series == n) & out.obs_date.isin(df.obs_date))]
        df["series"] = n
        out = pd.concat([out, df[out.columns]], ignore_index=True)
    out = out.sort_values(["series", "available_date", "obs_date"]).drop_duplicates(["series", "obs_date", "available_date"], keep="last")
    return out, errors, sorted(results)
