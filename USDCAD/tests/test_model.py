from pathlib import Path
import hashlib
import json
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen
from http.server import ThreadingHTTPServer

import numpy as np
import pandas as pd

from backend.data import read_observations, refresh
from backend.model import DRIVERS, build_panel, calculate, grade, monthly_decisions, weight
from backend.server import handler
from backend.update import ROOT, make_snapshot, update


class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.obs = read_observations(ROOT / "data/observations.csv")
        cls.training = pd.read_csv(ROOT / "data/ppp_training.csv", index_col="date", parse_dates=True)
        cls.panel = build_panel(cls.obs, pd.Timestamp("2026-10-16"))
        cls.values, cls.fits, cls.ppp = calculate(cls.panel, cls.training)

    def test_all_189_research_monthly_decisions_match(self):
        expected = pd.read_csv(ROOT / "tests/research_monthly.csv", index_col="date", parse_dates=True)
        actual = monthly_decisions(self.values, pd.Timestamp("2026-10-19")).set_index("date").loc[expected.index]
        self.assertEqual(len(actual), 189)
        for ours, research in [("layer1_tier", "t_sleeve1"), ("layer2_tier", "t_sleeve2"), ("usd_weight", "w_ew")]:
            np.testing.assert_allclose(actual[ours], expected[research], atol=1e-12, rtol=0)

    def test_tier_boundaries_and_mirrored_weights(self):
        x = pd.Series([-2, -1.5, -1, -0.5, -0.499, 0, 0.499, 0.5, 1, 1.5, 2, np.nan])
        t = grade(x)
        np.testing.assert_allclose(t, [-3, -3, -2, -1, 0, 0, 0, 1, 2, 3, 3, np.nan], equal_nan=True)
        np.testing.assert_allclose(weight(pd.Series(range(-3, 4))), [0, .2, .35, .5, .65, .8, 1])

    def test_signal_fair_value_and_band_identity(self):
        v = self.values.dropna(subset=["beer_z"])
        implied_z = np.log(v.spot / v.beer_fv) / np.log(v.band_high / v.beer_fv)
        np.testing.assert_allclose(implied_z, v.beer_z, atol=1e-11, rtol=0)
        np.testing.assert_allclose(100 * (v.spot / v.beer_fv - 1), v.beer_gap_pct, atol=1e-11)
        np.testing.assert_allclose(v.band_high * v.band_low, v.beer_fv ** 2, atol=1e-11)

    def test_truncation_does_not_refit_partial_year_or_change_past(self):
        cutoff = pd.Timestamp("2019-07-15")
        short, fits, _ = calculate(self.panel.loc[:cutoff], self.training)
        self.assertEqual(fits.index[-1], pd.Timestamp("2018-12-31"))
        cols = ["beer_fv", "beer_z", "peer_z", "usd_weight"]
        pd.testing.assert_frame_equal(short[cols], self.values.loc[:cutoff, cols])

    def test_month_end_uses_previous_day_and_execution_plus_five(self):
        m = monthly_decisions(self.values, pd.Timestamp("2026-10-16"))
        r = m.iloc[-1]
        self.assertEqual(r.date, pd.Timestamp("2026-09-30"))
        self.assertEqual(r.data_through, pd.Timestamp("2026-09-29"))
        self.assertEqual(r.execution_date, pd.Timestamp("2026-10-07"))
        self.assertAlmostEqual(r.usd_weight, self.values.loc["2026-09-29"].usd_weight)

    def test_future_cpi_release_cannot_change_earlier_panel(self):
        obs = pd.concat([self.obs, pd.DataFrame([{"series": "us_cpi", "obs_date": pd.Timestamp("2025-01-01"),
                       "available_date": pd.Timestamp("2026-11-13"), "value": 999999, "rule": "realtime"}])])
        pd.testing.assert_frame_equal(build_panel(obs, pd.Timestamp("2026-10-16")), self.panel)

    def test_negative_wti_is_missing_and_does_not_poison_year(self):
        self.assertLess(self.panel.loc["2020-04-20"].wti, 0)
        self.assertTrue(pd.isna(self.values.loc["2020-04-20"].beer_fv))
        self.assertTrue(np.isfinite(self.values.loc["2020-04-21"].beer_z))
        self.assertTrue(np.isfinite(self.values.loc["2020-12-31"].beer_z))

    def test_frozen_ppp_training_hash_and_constants(self):
        provenance = json.loads((ROOT / "data/provenance.json").read_text())
        digest = hashlib.sha256((ROOT / "data/ppp_training.csv").read_bytes()).hexdigest()
        self.assertEqual(digest, provenance["training_sha256"])
        self.assertEqual(self.ppp["months"], 480)
        self.assertAlmostEqual(self.ppp["constant"], -0.38022428259605334, places=13)

    def test_driver_attribution_reconciles_and_common_date_is_honest(self):
        snap = make_snapshot(self.obs, self.training, pd.Timestamp("2026-10-19"), "cached", {}, [], {})
        total = sum(d["contribution_log_bp"] for d in snap["drivers"]) + snap["refit_change_log_bp"]
        self.assertAlmostEqual(total, snap["raw_fv_change_log_bp"], places=6)
        for name in ["usdcad", "wti", "us2y", "ca2y", "vix", "usd_broad", "audusd", "nzdusd", "usdnok"]:
            source = next(s for s in snap["sources"] if s["name"] == name)
            self.assertLessEqual(snap["valuation_date"], source["observation_date"])
            self.assertEqual(snap["valuation_date"], source["valuation_observation_date"])
        self.assertEqual(snap["mode"], "cached")
        json.dumps(snap, allow_nan=False)

    def test_published_partial_update_labels_failures_and_retains_source_dates(self):
        snap = make_snapshot(self.obs, self.training, pd.Timestamp("2026-10-19"), "live",
                             {"ca2y": "test outage"}, ["usdcad"], {})
        source = next(s for s in snap["sources"] if s["name"] == "ca2y")
        self.assertEqual(source["status"], "error")
        self.assertFalse(source["fetched_this_run"])
        self.assertTrue(any(n["title"] == "Partial source failure" for n in snap["notices"]))


class OperationsTests(unittest.TestCase):
    def test_cpi_revision_is_first_seen_and_never_backdated(self):
        today = pd.Timestamp("2026-11-13")
        old = read_observations(ROOT / "data/observations.csv")
        def fetch_only_cpi(name, start, day):
            if name != "us_cpi":
                raise OSError("test outage")
            return pd.DataFrame({"obs_date": [pd.Timestamp("2026-09-01")], "value": [999.]})
        with patch("backend.data.fetch", side_effect=fetch_only_cpi):
            merged, errors, successes = refresh(old, today)
        event = merged[(merged.series == "us_cpi") & (merged.value == 999)].iloc[-1]
        self.assertEqual(event.available_date, today)
        self.assertEqual(successes, ["us_cpi"])
        self.assertIn("ca2y", errors)
        cutoff = pd.Timestamp("2026-10-16")
        pd.testing.assert_frame_equal(build_panel(old, cutoff), build_panel(merged, cutoff))

    def test_outage_backfill_starts_before_last_cached_observation(self):
        obs = read_observations(ROOT / "data/observations.csv")
        starts = {}
        def failed_fetch(name, start, today):
            starts[name] = pd.Timestamp(start)
            raise OSError("offline")
        with patch("backend.data.fetch", side_effect=failed_fetch):
            refresh(obs, pd.Timestamp("2027-05-01"))
        for name in ["usdcad", "wti", "ca2y"]:
            self.assertEqual(starts[name], obs[obs.series == name].obs_date.max() - pd.Timedelta(days=14))

    def test_failed_update_preserves_previous_dashboard(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            for name in ["observations.csv", "ppp_training.csv", "provenance.json", "dashboard.json"]:
                shutil.copy(ROOT / "data" / name, path / name)
            before = (path / "dashboard.json").read_bytes()
            with patch("backend.update.refresh", return_value=(None, {"all": "test failure"}, [])):
                with self.assertRaises(RuntimeError):
                    update(path, live=True)
            self.assertEqual(before, (path / "dashboard.json").read_bytes())
            self.assertEqual(json.loads((path / "update-status.json").read_text())["result"], "failed")

    def test_api_is_read_only_with_explicit_cors(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler(ROOT / "data", "https://monitor.example"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(base + "/api/v1/dashboard") as response:
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], "https://monitor.example")
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(json.load(response)["schema_version"], 1)
            with self.assertRaises(HTTPError) as error:
                urlopen(base + "/../observations.csv")
            self.assertEqual(error.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
