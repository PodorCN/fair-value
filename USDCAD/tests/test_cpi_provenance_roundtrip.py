from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from backend.data import SOURCE_INFO, STATCAN_CPI_URL, fetch_ca_cpi_mirror, read_observations
from backend.update import ROOT, update


class FrozenClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 18, tzinfo=timezone.utc)


class CPIProvenanceRoundtripTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fv-cpi-roundtrip-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        for name in ('ppp_training.csv', 'provenance.json'):
            shutil.copyfile(ROOT / 'data' / name, self.path / name)
        original = read_observations(ROOT / 'data/observations.csv')
        self.base = original[(original.obs_date <= '2026-11-17') & (original.available_date <= '2026-11-17')].copy()
        self.base.attrs = {}
        (self.path / 'observations.csv').write_bytes(self.base.to_csv(index=False, lineterminator='\n').encode())
        self.payload = json.loads((ROOT / 'tests/fixtures/boc-cpi-monthly.json').read_text())
        self.payload['observations'][-1]['V41690973']['v'] = '170.5'  # synthetic revision
        self.payload['observations'].append({'d': '2026-11-01', 'V41690973': {'v': '170.6'}})  # synthetic new print
        self.fetched_at = '2026-11-18T00:00:00+00:00'

    def update(self, live=True):
        # Fixed synthetic clock, without passing historical --as-of to a live run.
        with patch('backend.update.datetime', FrozenClock):
            return update(self.path, live=live)

    def fetched(self, name, start, today, mode='mirror'):
        if name == 'ca_cpi' and mode == 'failed':
            raise ValueError('StatCan direct HTTP403; official mirror HTTP503')
        if name == 'ca_cpi' and mode == 'mirror':
            with patch('backend.data.request', return_value=json.dumps(self.payload)):
                frame = fetch_ca_cpi_mirror(start, today, 'HTTPError: HTTP Error 403: Forbidden')
            frame['obs_date'] = pd.to_datetime(frame.obs_date)
            frame.attrs['source_access']['retrieved_at_utc'] = self.fetched_at
            return frame
        source = read_observations(self.path / 'observations.csv') if name == 'ca_cpi' else self.base
        frame = source[source.series == name].sort_values('available_date').drop_duplicates('obs_date', keep='last')[['obs_date', 'value']].copy()
        frame = frame[(frame.obs_date >= start) & (frame.obs_date <= today)]
        frame.attrs = {}
        if name == 'ca_cpi':
            frame.attrs['source_access'] = {'provider': 'Statistics Canada', 'url': SOURCE_INFO[name]['url'],
                'data_url': STATCAN_CPI_URL, 'access_method': 'direct', 'unit': 'Index (2002=100)',
                'frequency': 'Monthly', 'seasonal_adjustment': 'NSA', 'retrieved_at_utc': '2026-11-18T00:01:00+00:00'}
        return frame

    def run_live(self, mode='mirror'):
        with patch('backend.data.fetch', side_effect=lambda n, s, t: self.fetched(n, s, t, mode)):
            code = self.update()
        snapshot = json.loads((self.path / 'dashboard.json').read_text())
        status = json.loads((self.path / 'update-status.json').read_text())
        return code, snapshot, status

    def source(self, snapshot):
        return next(s for s in snapshot['sources'] if s['name'] == 'ca_cpi')

    def assert_retained(self, snapshot, status):
        source = self.source(snapshot)
        self.assertEqual(source['provider'], 'Bank of Canada (Statistics Canada republication)')
        self.assertEqual(source['access_scope'], 'retained_last_success')
        self.assertFalse(source['fetched_this_run'])
        self.assertEqual(source['retrieved_at_utc'], self.fetched_at)
        self.assertNotIn('ca_cpi', status.get('source_access', {}))
        self.assertEqual(status['retained_source_access']['ca_cpi']['retrieved_at_utc'], self.fetched_at)
        self.assertEqual(source['observation_access']['access']['retrieved_at_utc'], self.fetched_at)
        self.assertEqual(len(snapshot['observation_access']), 2)
        self.assertTrue(any(n['title'] == 'Retained CPI provenance' and 'prior direct failure' in n['detail'] for n in snapshot['notices']))

    def test_mirror_revision_and_new_print_survive_disk_and_repeated_cached_recalculation(self):
        code, first, _ = self.run_live()
        self.assertEqual(code, 0)
        self.assertEqual(len(first['observation_access']), 2)
        raw = (self.path / 'observations.csv').read_bytes()
        read = read_observations(self.path / 'observations.csv')
        self.assertEqual(read.attrs['source_access_this_run'], {})
        self.assertEqual(read.attrs['source_access']['ca_cpi']['retrieved_at_utc'], self.fetched_at)
        for _ in range(2):
            with patch('backend.data.fetch', side_effect=AssertionError('cached must not fetch')):
                self.assertEqual(self.update(live=False), 0)
            snapshot = json.loads((self.path / 'dashboard.json').read_text())
            status = json.loads((self.path / 'update-status.json').read_text())
            self.assert_retained(snapshot, status)
            self.assertEqual(snapshot['mode'], 'cached')
            self.assertEqual((self.path / 'observations.csv').read_bytes(), raw)
            self.assertEqual(snapshot['observation_access'], first['observation_access'])
            read_observations(self.path / 'observations.csv')  # hash binding still valid

    def test_mirror_then_both_cpi_sources_fail_preserves_origin_and_current_error(self):
        self.run_live()
        code, snapshot, status = self.run_live('failed')
        self.assertEqual(code, 1)
        self.assertEqual(status['result'], 'partial')
        self.assert_retained(snapshot, status)
        self.assertEqual(self.source(snapshot)['status'], 'error')
        self.assertIn('HTTP503', self.source(snapshot)['error'])
        self.assertIn('HTTP403', status['errors']['ca_cpi'])
        self.assertNotIn('ca_cpi', status['sources_fetched'])

    def test_direct_recovery_replaces_current_route_but_preserves_first_seen_origin(self):
        _, first, _ = self.run_live()
        code, snapshot, status = self.run_live('direct')
        self.assertEqual(code, 0)
        source = self.source(snapshot)
        self.assertEqual(source['provider'], 'Statistics Canada')
        self.assertEqual(source['access_scope'], 'current_fetch')
        self.assertTrue(source['fetched_this_run'])
        self.assertNotIn('primary_error', source)
        self.assertNotIn('primary_error', status['source_access']['ca_cpi'])
        self.assertFalse(any(n['title'] in ('Official CPI republication', 'Retained CPI provenance') for n in snapshot['notices']))
        self.assertEqual(snapshot['observation_access'], first['observation_access'])
        self.assertEqual(source['observation_access']['access']['provider'], 'Bank of Canada (Statistics Canada republication)')
        self.assertEqual(self.update(live=False), 0)
        cached = self.source(json.loads((self.path / 'dashboard.json').read_text()))
        self.assertEqual(cached['access_scope'], 'retained_last_success')
        self.assertFalse(cached['fetched_this_run'])
        self.assertEqual(cached['provider'], 'Statistics Canada')
        self.assertNotIn('primary_error', cached)

    def test_v2_row_only_access_record_is_migrated_without_restoring_current_flags(self):
        _, snapshot, _ = self.run_live()
        snapshot.pop('source_access'); snapshot.pop('observation_access')
        (self.path / 'dashboard.json').write_text(json.dumps(snapshot), encoding='utf-8')
        read = read_observations(self.path / 'observations.csv')
        self.assertEqual(read.attrs['source_access_this_run'], {})
        self.assertEqual(read.attrs['source_access']['ca_cpi']['retrieved_at_utc'], self.fetched_at)
        self.assertNotIn('fetched_this_run', read.attrs['source_access']['ca_cpi'])
        self.assertEqual(self.update(live=False), 0)
        current = json.loads((self.path / 'dashboard.json').read_text())
        self.assertFalse(self.source(current)['fetched_this_run'])
        self.assertEqual(self.source(current)['access_scope'], 'retained_last_success')

    def test_mismatched_csv_or_unbound_origin_cannot_silently_relabel_cache(self):
        _, snapshot, _ = self.run_live()
        original = (self.path / 'observations.csv').read_bytes()
        (self.path / 'observations.csv').write_bytes(original.replace(b'170.6', b'171.6'))
        with self.assertRaisesRegex(ValueError, 'does not match'):
            read_observations(self.path / 'observations.csv')
        (self.path / 'observations.csv').write_bytes(original)
        snapshot['observation_access'][0]['value'] = 999.
        (self.path / 'dashboard.json').write_text(json.dumps(snapshot), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'no matching observation'):
            read_observations(self.path / 'observations.csv')
