from copy import deepcopy
from pathlib import Path
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import pandas as pd

from backend.data import BOC_CPI_URL, SOURCE_INFO, STATCAN_CPI_URL, fetch, fetch_ca_cpi_mirror, read_observations, refresh
from backend.update import ROOT, update


class CPIRepublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads((ROOT / 'tests/fixtures/boc-cpi-monthly.json').read_text())

    def mirror(self, payload=None):
        with patch('backend.data.request', return_value=json.dumps(payload or self.fixture)):
            return fetch_ca_cpi_mirror('2024-11-01', '2026-11-18', 'HTTPError: HTTP Error 403: Forbidden')

    def test_real_official_fixture_matches_all_24_cached_reference_months(self):
        actual = self.mirror()
        cached = read_observations(ROOT / 'data/observations.csv')
        # Future legitimate revisions append first-seen events; keep the
        # immutable original record for this captured November 18 fixture.
        cached = cached[cached.series == 'ca_cpi'].sort_values('available_date').drop_duplicates('obs_date', keep='first').set_index('obs_date')
        self.assertEqual(len(actual), 24)
        for row in actual.itertuples():
            self.assertEqual(row.value, cached.loc[pd.Timestamp(row.obs_date), 'value'])
        self.assertEqual(actual.iloc[-1].obs_date, '2026-10-01')
        self.assertEqual(actual.iloc[-1].value, 170.4)

    def test_403_uses_only_the_explicit_official_republication_and_preserves_failure(self):
        refusal = HTTPError(STATCAN_CPI_URL, 403, 'Forbidden', {}, None)
        with patch('backend.data.request', side_effect=[refusal, json.dumps(self.fixture)]) as get:
            df = fetch('ca_cpi', '2024-11-01', '2026-11-18')
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[0].args, (STATCAN_CPI_URL, [{'vectorId': 41690973, 'latestN': 24}]))
        self.assertEqual(get.call_args_list[1].args, (BOC_CPI_URL + '?start_date=2024-11-01&end_date=2026-11-18',))
        access = df.attrs['source_access']
        self.assertEqual(access['mirror_series_id'], 'V41690973')
        self.assertEqual(access['original_provider'], 'Statistics Canada')
        self.assertIn('403', access['primary_error'])
        self.assertEqual(access['seasonal_adjustment'], 'NSA')

    def test_other_errors_are_not_rerouted_to_the_mirror(self):
        for error in [HTTPError(STATCAN_CPI_URL, 401, 'Unauthorized', {}, None), PermissionError('access denied'), ValueError('invalid response')]:
            with self.subTest(error=type(error).__name__), patch('backend.data.request', side_effect=error) as get:
                with self.assertRaises(type(error)):
                    fetch('ca_cpi', '2024-11-01', '2026-11-18')
                self.assertEqual(get.call_count, 1)

    def test_mirror_failure_does_not_erase_the_primary_403(self):
        with patch('backend.data.request', side_effect=[HTTPError(STATCAN_CPI_URL, 403, 'Forbidden', {}, None), HTTPError(BOC_CPI_URL, 503, 'Unavailable', {}, None)]) as get:
            with self.assertRaisesRegex(ValueError, '403.*republication failed.*503'):
                fetch('ca_cpi', '2024-11-01', '2026-11-18')
            self.assertEqual(get.call_count, 2)

    def test_wrong_series_units_frequency_or_adjustment_fail_closed(self):
        changes = [lambda d: d['seriesDetail'].pop('V41690973'),
                   lambda d: d['seriesDetail']['V41690973'].update(label='Total CPI (seasonally adjusted)'),
                   lambda d: d['seriesDetail']['V41690973']['metadata'].update(unit='Percent'),
                   lambda d: d['seriesDetail']['V41690973']['metadata'].update(unit='Index (2015=100)'),
                   lambda d: d['seriesDetail']['V41690973']['metadata'].update(frequency='Daily'),
                   lambda d: d['seriesDetail']['V41690973'].update(dateFormat='daily')]
        for i, change in enumerate(changes):
            data = deepcopy(self.fixture); change(data)
            with self.subTest(case=i), self.assertRaisesRegex(ValueError, 'metadata'):
                self.mirror(data)

    def test_invalid_duplicate_future_or_missing_observations_fail_closed(self):
        changes = [lambda d: d.update(observations=[]),
                   lambda d: d['observations'].append(deepcopy(d['observations'][-1])),
                   lambda d: d['observations'][-1].update(d='2026-10-02'),
                   lambda d: d['observations'][-1].update(d='2026-13-01'),
                   lambda d: d['observations'][-1].update(d='2026-12-01'),
                   lambda d: d['observations'][-1].pop('V41690973'),
                   lambda d: d['observations'][-1]['V41690973'].update(v='NaN'),
                   lambda d: d['observations'][-1]['V41690973'].update(v='inf'),
                   lambda d: d['observations'][-1]['V41690973'].update(v='0')]
        for i, change in enumerate(changes):
            data = deepcopy(self.fixture); change(data)
            with self.subTest(case=i), self.assertRaises(ValueError):
                self.mirror(data)

    def test_seasonal_and_percentage_columns_cannot_replace_index(self):
        data = deepcopy(self.fixture)
        for row in data['observations']:
            row['V41690914'] = {'v': '999'}
            row['STATIC_TOTALCPICHANGE'] = {'v': '3.1'}
        self.assertEqual(self.mirror(data).iloc[-1].value, 170.4)

    def test_first_seen_revisions_and_access_record_survive_refresh(self):
        today = pd.Timestamp('2026-11-18')
        existing = pd.DataFrame([{'series': n, 'obs_date': pd.Timestamp('2026-10-01'), 'value': 170.3 if n == 'ca_cpi' else 100.,
                                  'available_date': pd.Timestamp('2026-11-01'), 'rule': 'realtime'} for n in SOURCE_INFO])
        mirrored = self.mirror()
        mirrored['obs_date'] = pd.to_datetime(mirrored.obs_date)
        def fetched(name, start, end):
            return mirrored if name == 'ca_cpi' else pd.DataFrame({'obs_date': [pd.Timestamp('2026-10-01')], 'value': [100.]})
        with patch('backend.data.fetch', side_effect=fetched):
            out, errors, successes = refresh(existing, today)
        self.assertFalse(errors)
        self.assertEqual(len(successes), 11)
        self.assertEqual(out.attrs['source_access']['ca_cpi'], mirrored.attrs['source_access'])
        revision = out[(out.series == 'ca_cpi') & (out.obs_date == pd.Timestamp('2026-10-01'))]
        self.assertEqual(revision.value.tolist(), [170.3, 170.4])
        self.assertEqual(revision.available_date.tolist(), [pd.Timestamp('2026-11-01'), today])
        new_months = out[(out.series == 'ca_cpi') & (out.obs_date < pd.Timestamp('2026-10-01'))]
        self.assertTrue((new_months.available_date == today).all())

    def test_real_update_serializes_mirror_attribution_and_direct_failure(self):
        access = self.mirror().attrs['source_access']
        obs = read_observations(ROOT / 'data/observations.csv')
        obs.attrs['source_access'] = {'ca_cpi': access}
        obs.attrs['source_access_this_run'] = {'ca_cpi': access}
        with tempfile.TemporaryDirectory(prefix='usdcad-republication-') as directory:
            target = Path(directory)
            for name in ('observations.csv', 'ppp_training.csv', 'provenance.json'):
                shutil.copyfile(ROOT / 'data' / name, target / name)
            with patch('backend.update.refresh', return_value=(obs, {}, list(SOURCE_INFO))):
                self.assertEqual(update(target), 0)
            snapshot = json.loads((target / 'dashboard.json').read_text())
            status = json.loads((target / 'update-status.json').read_text())
        source = next(s for s in snapshot['sources'] if s['name'] == 'ca_cpi')
        self.assertEqual(source['series_id'], 'v41690973')
        self.assertEqual(source['provider'], access['provider'])
        self.assertEqual(source['primary_error'], access['primary_error'])
        self.assertTrue(source['fetched_this_run'])
        self.assertEqual(status['source_access']['ca_cpi'], access)
        self.assertEqual(status['result'], 'success')
        self.assertTrue(any(n['title'] == 'Official CPI republication' and '403' in n['detail'] for n in snapshot['notices']))
