import unittest
from unittest.mock import patch
import main
import test_app_lifecycle as lifecycle


class AppearanceTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def test_theme_change_rebuilds_existing_values_and_preserves_dark_tray(self):
        self.app._apply_headphones_update([{'id': 'a', 'name': 'A', 'battery_level': 87}])
        dark = self.app._create_percent_image(87, False).getpixel((10, 10))
        self.app._set_setting('theme', 'light')
        self.assertEqual(self.app.lbl_device_battery.cget('text'), '87%')
        self.assertEqual(self.app.device_card.cget('fg_color'), main.THEMES['light']['CARD'])
        self.assertEqual(self.app._create_percent_image(87, False).getpixel((10, 10)), dark)
        self.app._set_setting('theme', 'dark')

    def test_compact_hides_chart_list_and_notes_but_keeps_battery(self):
        self.app._apply_headphones_update([{'id': 'jbl', 'name': 'JBL'},
                                           {'id': 'a', 'name': 'A', 'battery_level': 80}])
        self.app._select_headphone('jbl')
        self.app._apply_battery_update(94, 67, 90, {'left': False})
        self.app._set_setting('compact_mode', True)
        self.assertFalse(self.app.devices_frame.winfo_manager())
        self.assertFalse(self.app.history_canvas.winfo_manager())
        self.assertFalse(self.app.card_footer.winfo_manager())
        self.assertEqual(self.app.lbl_device_battery.cget('text'), '67%')
        self.assertTrue(self.app.row_device.winfo_manager())
        self.app._set_setting('compact_mode', False)
        self.assertTrue(self.app.devices_frame.winfo_manager())
        self.assertTrue(self.app.row_left.winfo_manager())

    def test_system_theme_uses_registry_reader(self):
        with patch.object(main, 'read_system_theme', return_value='light'):
            self.app._set_setting('theme', 'system')
        self.assertEqual(self.app.device_card.cget('fg_color'), main.THEMES['light']['CARD'])
