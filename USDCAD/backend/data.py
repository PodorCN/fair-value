"""Public source adapters and durable observations. No credentials in the browser.

Research seed is preserved. New CPI prints/revisions are recorded no earlier than
the first successful fetch, so later releases cannot rewrite historical signals.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from io import StringIO
import json
import math
import re
import ssl
import time
from urllib.error import HTTPError, URLError
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
STATCAN_CPI_URL = "https://www150.statcan.gc.ca/t1/wds/rest/getDataFromVectorsAndLatestNPeriods"
BOC_CPI_URL = "https://www.bankofcanada.ca/valet/observations/group/CPI_MONTHLY/json"
BOC_CPI_PAGE = "https://www.bankofcanada.ca/rates/price-indexes/cpi/"


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
        except HTTPError as exc:
            # Access refusals are not transient outages. Preserve the refusal;
            # never switch identity, host or data series to get around it.
            if exc.code not in {408, 429, 500, 502, 503, 504} or attempt == 2:
                raise
            time.sleep(attempt + 1)
        except (URLError, TimeoutError) as exc:
            if isinstance(getattr(exc, "reason", None), (ssl.SSLError, PermissionError)):
                raise
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def fetch_ca_cpi_mirror(start, today, primary_error):
    """Official republication of the identical NSA index, not an alternate CPI.

    The Bank's CPI page links CPI_MONTHLY and identifies V41690973 separately
    from the seasonally adjusted V41690914 and the percent-change series.
    Refuse unexpected metadata/units instead of silently changing the model.
    """
    url = f"{BOC_CPI_URL}?start_date={start}&end_date={today}"
    rows = json.loads(request(url))
    series = rows.get("seriesDetail", {}).get("V41690973", {})
    if (series.get("label") != "Total CPI" or series.get("dateFormat") != "monthly"
            or series.get("metadata", {}).get("unit") != "Index (2002=100)"
            or series.get("metadata", {}).get("frequency") != "Monthly"):
        raise ValueError("Bank of Canada CPI metadata does not identify v41690973 monthly NSA index (2002=100)")
    points, seen = [], set()
    for row in rows.get("observations", []):
        date = row.get("d", "")
        if not re.fullmatch(r"\d{4}-\d{2}-01", date) or date in seen:
            raise ValueError("Bank of Canada CPI has invalid or duplicate reference months")
        if pd.Timestamp(date) > pd.Timestamp(today):
            raise ValueError("Bank of Canada CPI returned a future reference month")
        seen.add(date)
        value = float(row.get("V41690973", {}).get("v", "nan"))
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Bank of Canada CPI has a missing or invalid v41690973 value")
        points.append({"obs_date": date, "value": value})
    if not points:
        raise ValueError("Bank of Canada CPI returned no observations")
    df = pd.DataFrame(points).sort_values("obs_date").tail(24)
    df.attrs["source_access"] = {
        "provider": "Bank of Canada (Statistics Canada republication)",
        "url": BOC_CPI_PAGE, "data_url": url,
        "access_method": "official-republication", "mirror_series_id": "V41690973",
        "unit": "Index (2002=100)", "frequency": "Monthly", "seasonal_adjustment": "NSA",
        "original_provider": "Statistics Canada", "original_url": SOURCE_INFO["ca_cpi"]["url"],
        "primary_endpoint": STATCAN_CPI_URL, "primary_error": primary_error,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    return df


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
    elif name == "ca_cpi":
        try:
            raw = request(STATCAN_CPI_URL, [{"vectorId": 41690973, "latestN": 24}])
        except HTTPError as exc:
            if exc.code != 403:
                raise
            primary_error = f"HTTPError: HTTP Error {exc.code}: {exc.reason}"
            # This is a separately published, public official copy. Do not solve
            # or bypass the StatCan browser challenge, spoof identity or rebase.
            try:
                df = fetch_ca_cpi_mirror(start, today, primary_error)
            except Exception as mirror_error:
                raise ValueError(f"StatCan direct {primary_error}; official CPI republication failed: {mirror_error}") from mirror_error
        else:
            rows = json.loads(raw)
            if rows[0]["status"] != "SUCCESS":
                raise ValueError("StatCan request unsuccessful")
            points = rows[0]["object"]["vectorDataPoint"]
            df = pd.DataFrame([{"obs_date": p["refPer"], "value": float(p["value"])} for p in points])
    else:
        raise ValueError(f"Unknown source: {name}")
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
    results, errors, source_access = {}, {}, {}
    starts = {n: str((min(today, existing[existing.series == n].obs_date.max())
                      - pd.Timedelta(days=730 if n.endswith("cpi") else 14)).date()) for n in SOURCE_INFO}
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {pool.submit(fetch, n, starts[n], str(today.date())): n
                for n in SOURCE_INFO}
        for f in as_completed(jobs):
            n = jobs[f]
            try:
                results[n] = f.result()
                if results[n].attrs.get("source_access"):
                    source_access[n] = results[n].attrs["source_access"]
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
    out.attrs["source_access"] = source_access
    return out, errors, sorted(results)
