import unittest
import test_app_lifecycle as lifecycle


class FullChargeTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def capture(self):
        sent = []
        self.app._notify = lambda message, title: sent.append((message, title)) or True
        return sent

    def test_general_battery_requires_rise_and_resets_below_hysteresis(self):
        sent = self.capture()
        for value in (100, 100, 94, 99, 100, 100, 95, 100, 94, 100):
            self.app._apply_headphones_update([{'id': 'a', 'name': 'A', 'battery_level': value}])
        self.assertEqual(len(sent), 2)
        self.assertTrue(all('đầy' in title.lower() for _, title in sent))

    def test_fast_pair_flags_belong_to_component_and_work_unselected(self):
        sent = self.capture()
        self.app._apply_headphones_update([{'id': 'jbl', 'name': 'JBL'}, {'id': 'x', 'name': 'X'}])
        self.app._apply_battery_update(100, 70, 100, {'left': False, 'right': True, 'case': False})
        self.assertEqual(sent, [])
        self.app._apply_battery_update(100, 70, 100, {'left': True, 'right': True, 'case': False})
        self.app._apply_battery_update(100, 70, 100, {'left': True, 'right': True, 'case': False})
        self.assertEqual(len(sent), 1)
        self.assertIn('Tai trái', sent[0][0])

    def test_disabled_and_stale_never_alert(self):
        sent = self.capture()
        self.app.settings['full_charge_alert'] = False
        self.app._apply_headphones_update([{'id': 'jbl', 'name': 'JBL'}])
        self.app._apply_battery_update(100, 100, 100, {'left': True, '_fresh': True})
        self.app.settings['full_charge_alert'] = True
        self.app._apply_battery_update(100, 100, 100, {'left': True, '_fresh': False})
        self.assertEqual(sent, [])

    def test_fast_pair_handoff_does_not_compare_old_general_baseline(self):
        sent = self.capture()
        jbl = {'id': 'jbl', 'name': 'JBL', 'battery_level': 99}
        self.app._apply_headphones_update([jbl])
        self.app._apply_battery_update(60, 60, 70, {'left': False, 'right': False, 'case': False})
        self.app._apply_headphones_update([dict(jbl, battery_level=100)])
        self.app._apply_battery_update(None, None, None, {'_fresh': False})
        self.assertEqual(sent, [])
