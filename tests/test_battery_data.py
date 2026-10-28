import json
from pathlib import Path
from app_test_support import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import jbl_core
import test_app_lifecycle as lifecycle


class FastPairTests(unittest.TestCase):
    def test_real_captured_packet_reports_all_batteries_not_charging(self):
        decoded = jbl_core.parse_fast_pair_battery(bytes.fromhex('003000000011e9335e4364'))
        self.assertEqual(decoded, ((94, False), (67, False), (100, False)))

    def test_charging_bit_is_distinct_from_percentage(self):
        decoded = jbl_core.parse_fast_pair_battery(bytes.fromhex('003001020311aa33de4364'))
        self.assertEqual(decoded, ((94, True), (67, False), (100, False)))

    def test_unknown_level_does_not_become_a_fake_percentage(self):
        decoded = jbl_core.parse_fast_pair_battery(bytes.fromhex('003001020311aa345eff7f'))
        self.assertEqual(decoded, ((94, False), (None, None), (None, None)))

    def test_packet_without_battery_field_is_not_battery_data(self):
        self.assertIsNone(jbl_core.parse_fast_pair_battery(bytes.fromhex('003000003311aa')))

    def test_battery_header_inside_account_filter_is_not_a_field(self):
        self.assertIsNone(jbl_core.parse_fast_pair_battery(bytes.fromhex('0030335e4311aa')))

    def test_truncated_or_invalid_battery_packet_is_rejected(self):
        for raw in ['003001020311aa335e43', '003001020311aa335e4365', '', '76efa7']:
            with self.subTest(raw=raw):
                self.assertIsNone(jbl_core.parse_fast_pair_battery(bytes.fromhex(raw)))

    def test_live_packet_replaces_charging_state_and_stale_data_expires(self):
        updates = []
        statuses = []
        with TemporaryDirectory(dir=Path(__file__).parent) as folder:
            core = jbl_core.JblCore(on_update=lambda *args:updates.append(args),
                                    on_status=lambda *args:statuses.append(args),
                                    state_path=Path(folder)/'state.json')
            device = SimpleNamespace(address=core.target_address, name='JBL CLUB PRO+ TWS-LE')
            adv = SimpleNamespace(local_name=device.name, service_data={
                jbl_core.FAST_PAIR_SERVICE:bytes.fromhex('003001020311aa33de4364')})
            with patch.object(jbl_core.time,'monotonic',return_value=10):
                core._on_advertisement(device,adv)
            self.assertTrue(updates[-1][3]['left'])
            adv.service_data[jbl_core.FAST_PAIR_SERVICE]=bytes.fromhex('003000000011e9335e4364')
            with patch.object(jbl_core.time,'monotonic',return_value=12):
                core._on_advertisement(device,adv)
            self.assertEqual(updates[-1][:3], (94,67,100))
            self.assertFalse(updates[-1][3]['left'])
            self.assertTrue(statuses[-1][0])
            with patch.object(jbl_core.time,'monotonic',return_value=100):
                core._expire_stale_data()
            self.assertEqual(updates[-1][:3], (None,None,None))
            self.assertIsNone(updates[-1][3]['left'])
            self.assertFalse(statuses[-1][0])

    def test_other_device_cannot_update_target_battery(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as folder:
            core = jbl_core.JblCore(state_path=Path(folder)/'state.json')
            core._on_advertisement(
                SimpleNamespace(address='00:11:22:33:44:55',name='Other buds'),
                SimpleNamespace(local_name='Other buds',service_data={
                    jbl_core.FAST_PAIR_SERVICE:bytes.fromhex('003000000011e9335e4364')}))
            self.assertIsNone(core.battery_left)


class BatteryUiTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown
    def test_old_cache_does_not_show_charging_as_current_state(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as folder:
            Path(folder,'battery_state.json').write_text(json.dumps({
                'left':78,'right':48,'case':None,'charging_right':True,
                'updated_at':'2026-10-13 14:44:43'}))
            with patch('main.APP_DIR',folder):
                self.app._load_cached_state()
            self.assertNotEqual(self.app.charging_info.get('right'), True)
            self.assertIsNone(self.app.right_level)

    def test_unknown_case_does_not_display_invented_charge_bar(self):
        self.app._apply_headphones_update([{'id': 'jbl', 'name': 'JBL CLUB PRO+ TWS'}])
        self.app._apply_battery_update(94,67,None,{'left':True,'right':False,'case':None})
        self.assertEqual(self.app.bar_case.get(),0)
        self.assertNotIn('Đang sạc',self.app.lbl_c_val.cget('text'))


if __name__=='__main__':
    unittest.main()
