import json
from pathlib import Path
from app_test_support import TemporaryDirectory
import unittest
from unittest.mock import patch

import battery_history as history


class HistoryTests(unittest.TestCase):
    def test_record_appends_without_rewriting_existing_lines(self):
        with TemporaryDirectory() as folder, patch.object(history.time, 'time', return_value=10000):
            path = Path(folder, 'history.jsonl')
            store = history.BatteryHistory(path)
            store.record('a', 'A', 80, 'ble', None)
            before = path.read_bytes()
            with patch.object(history.os, 'replace') as replace:
                store.record('a', 'A', 79, 'ble', None, now=10001)
                replace.assert_not_called()
            self.assertTrue(path.read_bytes().startswith(before))
            self.assertEqual(len(path.read_bytes().splitlines()), 2)
            self.assertEqual(len(history.BatteryHistory(path).samples('a')), 2)

    def test_same_level_charge_transition_breaks_discharge_estimation(self):
        with TemporaryDirectory() as folder, patch.object(history.time, 'time', return_value=10000):
            store = history.BatteryHistory(Path(folder, 'history.jsonl'))
            store.record('a', 'A', 80, 'fast_pair', False, now=8000)
            store.record('a', 'A', 75, 'fast_pair', False, now=9000)
            self.assertTrue(store.record('a', 'A', 75, 'fast_pair', True, now=9001))
            store.record('a', 'A', 75, 'fast_pair', False, now=9500)
            store.record('a', 'A', 70, 'fast_pair', False, now=10000)
            self.assertIsNone(history.estimate_remaining(store.samples('a')))

    def test_changed_samples_and_five_minute_heartbeat_are_per_id(self):
        with TemporaryDirectory() as folder, patch.object(history.time, 'time', return_value=1000000):
            store = history.BatteryHistory(Path(folder, 'history.jsonl'))
            self.assertTrue(store.record('a', 'Same', 80, 'ble', None, now=1000000))
            self.assertFalse(store.record('a', 'Same', 80, 'ble', None, now=1000299))
            self.assertTrue(store.record('a', 'Same', 80, 'ble', None, now=1000300))
            self.assertTrue(store.record('b', 'Same', 40, 'windows', None, now=1000301))
            self.assertTrue(store.record('a', 'Same', 79, 'ble', None, now=1000302))
            self.assertEqual([s['level'] for s in store.samples('a')], [80, 80, 79])
            self.assertEqual(len(store.samples('b')), 1)

    def test_corrupt_lines_and_seven_day_retention(self):
        with TemporaryDirectory() as folder, patch.object(history.time, 'time', return_value=1000000):
            path = Path(folder, 'history.jsonl')
            def sample(t, level):
                return json.dumps(dict(t=t, id='a', name='A', level=level, source='ble', charging=None))
            path.write_text(sample(1, 90) + '\n{broken\n' + sample(999999, 50) + '\n' +
                            json.dumps({'t': 'bad'}) + '\n', encoding='utf-8')
            store = history.BatteryHistory(path)
            self.assertEqual([s['level'] for s in store.samples('a')], [50])
            self.assertEqual(len(path.read_text().splitlines()), 1)
            store.prune(now=1604800)
            self.assertEqual(store.samples('a'), [])

    def test_invalid_utf8_line_does_not_hide_later_valid_samples(self):
        with TemporaryDirectory() as folder, patch.object(history.time, 'time', return_value=1000000):
            path = Path(folder, 'history.jsonl')
            good = dict(t=999999, id='a', name='A', level=50, source='ble', charging=None)
            path.write_bytes(b'\xffbroken\n' + json.dumps(good).encode('utf-8') + b'\n')
            self.assertEqual(history.BatteryHistory(path).samples('a'), [good])


class EstimateTests(unittest.TestCase):
    def test_source_change_starts_a_new_estimation_run(self):
        with patch.object(history.time, 'time', return_value=10000):
            self.assertIsNone(history.estimate_remaining([
                dict(t=8800, level=80, source='ble'),
                dict(t=9400, level=70, source='ble'),
                dict(t=10000, level=50, source='windows')]))

    def test_gap_over_30_minutes_starts_a_new_run(self):
        self.assertIsNone(self.estimate([(7000, 80, False), (10000, 70, False)]))
        self.assertIsNotNone(self.estimate([(8200, 80, False), (10000, 70, False)]))

    def estimate(self, points):
        with patch.object(history.time, 'time', return_value=10000):
            return history.estimate_remaining([
                dict(t=t, level=level, charging=charging) for t, level, charging in points])

    def test_linear_decline_predicts_minutes_to_zero(self):
        self.assertAlmostEqual(self.estimate([(8800, 80, False), (9400, 75, False),
                                              (10000, 70, False)]), 140)

    def test_short_flat_charging_or_old_series_has_no_estimate(self):
        for points in ([(9900, 80, False), (10000, 70, False)],
                       [(8800, 80, False), (10000, 79, False)],
                       [(8800, 80, True), (10000, 70, True)],
                       [(-20000, 80, False), (-19000, 70, False)]):
            self.assertIsNone(self.estimate(points))

    def test_increase_or_charging_resets_to_most_recent_run(self):
        self.assertIsNone(self.estimate([(8000, 90, False), (9000, 70, False),
                                         (9500, 80, False), (10000, 75, False)]))
        self.assertAlmostEqual(self.estimate([(7000, 90, True), (8800, 80, False),
                                              (10000, 70, False)]), 140)
