import json
import ssl
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pandas as pd

from backend.data import SOURCE_INFO, fetch, refresh, request


class SourceFailureTests(unittest.TestCase):
    def test_permanent_http_errors_are_not_retried(self):
        for status in (400, 401, 403, 404, 405):
            with self.subTest(status=status), patch('backend.data.urlopen', side_effect=HTTPError('https://example.test', status, 'refused', {}, None)) as get, patch('backend.data.time.sleep') as sleep:
                with self.assertRaises(HTTPError):
                    request('https://example.test', [{'vectorId': 41690973, 'latestN': 24}])
                self.assertEqual(get.call_count, 1)
                sleep.assert_not_called()

    def test_transient_http_errors_have_bounded_retries(self):
        for status in (408, 429, 500, 502, 503, 504):
            with self.subTest(status=status), patch('backend.data.urlopen', side_effect=HTTPError('https://example.test', status, 'temporary', {}, None)) as get, patch('backend.data.time.sleep') as sleep:
                with self.assertRaises(HTTPError):
                    request('https://example.test')
                self.assertEqual(get.call_count, 3)
                self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2])

    def test_network_timeouts_have_bounded_retries(self):
        for error in (TimeoutError(), URLError('timed out')):
            with self.subTest(error=type(error).__name__), patch('backend.data.urlopen', side_effect=error) as get, patch('backend.data.time.sleep'):
                with self.assertRaises(type(error)):
                    request('https://example.test')
                self.assertEqual(get.call_count, 3)

    def test_security_failures_are_not_retried_or_downgraded(self):
        for reason in (ssl.SSLCertVerificationError('certificate rejected'), ssl.SSLError('TLS rejected'), PermissionError('network access denied')):
            with self.subTest(reason=type(reason).__name__), patch('backend.data.urlopen', side_effect=URLError(reason)) as get, patch('backend.data.time.sleep') as sleep:
                with self.assertRaises(URLError):
                    request('https://example.test')
                self.assertEqual(get.call_count, 1)
                sleep.assert_not_called()

    def test_decode_and_unexpected_errors_are_not_retried(self):
        for error in (UnicodeDecodeError('utf8', b'\xff', 0, 1, 'invalid'), ValueError('invalid response')):
            with self.subTest(error=type(error).__name__), patch('backend.data.urlopen', side_effect=error) as get, patch('backend.data.time.sleep') as sleep:
                with self.assertRaises(type(error)):
                    request('https://example.test')
                self.assertEqual(get.call_count, 1)
                sleep.assert_not_called()

    def test_transient_then_success_keeps_post_and_honest_identity(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'\xef\xbb\xbf{"ok":true}'
        with patch('backend.data.urlopen', side_effect=[HTTPError('https://example.test', 503, 'temporary', {}, None), response]) as get, patch('backend.data.time.sleep'):
            self.assertEqual(request('https://example.test', [{'vectorId': 41690973, 'latestN': 24}]), '{"ok":true}')
        for call in get.call_args_list:
            req = call.args[0]
            self.assertEqual(req.get_method(), 'POST')
            self.assertEqual(json.loads(req.data), [{'vectorId': 41690973, 'latestN': 24}])
            self.assertIn('USDCAD-FairValue/1.0', req.get_header('User-agent'))
            self.assertEqual(call.kwargs['timeout'], 25)

    def test_statcan_definition_and_observation_dates_are_unchanged(self):
        payload = [{'status': 'SUCCESS', 'object': {'vectorDataPoint': [
            {'refPer': '2026-09-01', 'value': 164.2}, {'refPer': '2026-10-01', 'value': 164.5}]}}]
        with patch('backend.data.request', return_value=json.dumps(payload)) as get:
            result = fetch('ca_cpi', '2026-09-01', '2026-11-17')
        self.assertEqual(get.call_args.args[1], [{'vectorId': 41690973, 'latestN': 24}])
        self.assertEqual(result.obs_date.dt.strftime('%Y-%m-%d').tolist(), ['2026-09-01', '2026-10-01'])
        self.assertEqual(result.value.tolist(), [164.2, 164.5])

    def test_ca_cpi_revisions_remain_first_seen_and_refusal_retains_cache(self):
        today = pd.Timestamp('2026-11-17')
        existing = pd.DataFrame([{'series': name, 'obs_date': pd.Timestamp('2026-09-01'), 'value': 100.,
                                  'available_date': pd.Timestamp('2026-10-01'), 'rule': 'realtime'} for name in SOURCE_INFO])
        def successful(name, start, end):
            return pd.DataFrame({'obs_date': [pd.Timestamp('2026-09-01')], 'value': [101. if name == 'ca_cpi' else 100.]})
        with patch('backend.data.fetch', side_effect=successful):
            updated, errors, successes = refresh(existing, today)
        revision = updated[updated.series == 'ca_cpi']
        self.assertEqual(revision.value.tolist(), [100., 101.])
        self.assertEqual(revision.available_date.tolist(), [pd.Timestamp('2026-10-01'), today])
        self.assertFalse(errors)
        self.assertIn('ca_cpi', successes)
        def refused(name, start, end):
            if name == 'ca_cpi':
                raise HTTPError('https://www150.statcan.gc.ca/t1/wds/rest/getDataFromVectorsAndLatestNPeriods', 403, 'Forbidden', {}, None)
            return successful(name, start, end)
        with patch('backend.data.fetch', side_effect=refused):
            retained, errors, successes = refresh(existing, today)
        pd.testing.assert_frame_equal(retained[retained.series == 'ca_cpi'].reset_index(drop=True), existing[existing.series == 'ca_cpi'].reset_index(drop=True))
        self.assertIn('403', errors['ca_cpi'])
        self.assertNotIn('ca_cpi', successes)
