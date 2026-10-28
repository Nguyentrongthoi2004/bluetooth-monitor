import json
from pathlib import Path
from app_test_support import TemporaryDirectory
import unittest
from unittest.mock import patch

import main
import test_app_lifecycle as lifecycle


AKKO = {'id': 'akko', 'name': 'Akko S9 Ultra_BT', 'battery_level': 80}
SONY = {'id': 'sony', 'name': 'WH-1000XM5', 'battery_level': 15}


class DeviceSelectionTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def test_device_list_only_appears_for_several_headphones(self):
        self.app._apply_headphones_update([AKKO])
        self.assertFalse(self.app.devices_frame.winfo_manager())
        self.app._apply_headphones_update([AKKO, SONY])
        self.assertTrue(self.app.devices_frame.winfo_manager())
        self.assertEqual(len(self.app._device_buttons), 2)

    def test_user_can_switch_displayed_headphone(self):
        self.app._apply_headphones_update([AKKO])
        self.app._apply_headphones_update([AKKO, SONY])
        self.assertEqual(self.app.lbl_device_title.cget('text'), 'WH-1000XM5')
        self.app._select_headphone('akko')
        self.assertEqual(self.app.lbl_device_title.cget('text'), 'Akko S9 Ultra_BT')
        self.assertEqual(self.app.lbl_device_battery.cget('text'), '80%')
        # A routine poll with the same devices keeps the user's choice.
        self.app._apply_headphones_update([AKKO, SONY])
        self.assertEqual(self.app.lbl_device_title.cget('text'), 'Akko S9 Ultra_BT')

    def test_switching_away_from_jbl_drops_component_battery(self):
        jbl = {'id': 'jbl', 'name': 'JBL CLUB PRO+ TWS'}
        self.app._apply_headphones_update([AKKO])
        self.app._apply_headphones_update([AKKO, jbl])
        self.app._apply_battery_update(94, 67, 100, {'_fresh': True})
        self.app._select_headphone('akko')
        self.assertIsNone(self.app.left_level)
        self.assertFalse(self.app.row_left.winfo_manager())


class LowBatteryAlertTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def alerts(self):
        sent = []
        self.app._notify = lambda message, title: sent.append(message) or True
        return sent

    def test_alert_once_per_crossing(self):
        sent = self.alerts()
        self.app.settings.update(low_battery_alert=True, low_battery_threshold=20)
        self.app._apply_headphones_update([dict(AKKO, battery_level=19)])
        self.app._apply_headphones_update([dict(AKKO, battery_level=18)])
        self.assertEqual(len(sent), 1)
        self.assertIn('19%', sent[0])
        self.app._apply_headphones_update([dict(AKKO, battery_level=60)])
        self.app._apply_headphones_update([dict(AKKO, battery_level=10)])
        self.assertEqual(len(sent), 2)

    def test_no_alert_when_disabled_or_unknown(self):
        sent = self.alerts()
        self.app.settings.update(low_battery_alert=False, low_battery_threshold=20)
        self.app._apply_headphones_update([dict(AKKO, battery_level=5)])
        self.app.settings['low_battery_alert'] = True
        self.app._apply_headphones_update([{'id': 'sony', 'name': 'WH-1000XM5'}])
        self.assertEqual(sent, [])

    def test_charging_earbud_does_not_alert(self):
        sent = self.alerts()
        self.app.settings.update(low_battery_alert=True, low_battery_threshold=20)
        self.app._apply_headphones_update([{'id': 'jbl', 'name': 'JBL CLUB PRO+ TWS'}])
        self.app._apply_battery_update(10, 50, 90, {'_fresh': True, 'left': True})
        self.assertEqual(sent, [])
        self.app._apply_battery_update(10, 50, 90, {'_fresh': True, 'left': False})
        self.assertEqual(len(sent), 1)


class TrayAndSettingsTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def test_tray_icon_shows_lowest_known_level(self):
        self.app.settings['tray_show_percent'] = True
        self.app._apply_headphones_update([{'id': 'jbl', 'name': 'JBL CLUB PRO+ TWS'}])
        self.app._apply_battery_update(94, 67, 100, {'_fresh': True})
        self.assertEqual(self.app._tray_icon_key, (67, False))
        self.assertIn('Hộp: 100%', self.app.tray_icon.title)

    def test_tray_icon_greys_out_after_disconnect(self):
        self.app._apply_headphones_update([AKKO])
        self.app._apply_headphones_update([])
        self.assertEqual(self.app._tray_icon_key, ('offline', False))
        self.assertIn('Không kết nối', self.app.tray_icon.title)
        self.app._apply_headphones_update([{'id': 'sony', 'name': 'WH-1000XM5'}])
        self.assertEqual(self.app._tray_icon_key, (None, False))

    def test_percent_image_renders_all_levels(self):
        for level in (0, 7, 55, 100):
            self.assertEqual(self.app._create_percent_image(level, False).size, (64, 64))


class SettingsFileTests(unittest.TestCase):
    def test_invalid_values_fall_back_and_unknown_keys_survive(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder, 'headphone_settings.json')
            path.write_text(json.dumps({'jbl_battery_device_id': 'jbl',
                                        'low_battery_threshold': 99,
                                        'low_battery_alert': 'yes'}), encoding='utf-8')
            with patch.object(main, 'SETTINGS_PATH', str(path)):
                settings = main.load_settings()
                self.assertEqual(settings['low_battery_threshold'], 20)
                self.assertTrue(settings['low_battery_alert'])
                self.assertEqual(main.load_battery_device_id(), 'jbl')
                settings['tray_show_percent'] = False
                self.assertTrue(main.save_settings(settings))
                saved = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(saved['jbl_battery_device_id'], 'jbl')
            self.assertFalse(saved['tray_show_percent'])

    def test_missing_file_uses_defaults(self):
        with patch.object(main, 'SETTINGS_PATH', str(Path(__file__).parent / 'missing.json')):
            self.assertEqual(main.load_settings(), main.DEFAULT_SETTINGS)
            self.assertIsNone(main.load_battery_device_id())


if __name__ == '__main__':
    unittest.main()
