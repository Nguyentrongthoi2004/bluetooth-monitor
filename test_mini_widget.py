import json
from pathlib import Path
from types import SimpleNamespace
import unittest
import main
import test_app_lifecycle as lifecycle


class MiniWidgetTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def test_toggle_tracks_battery_alias_and_disconnect_without_fake_charge(self):
        self.app._set_setting('mini_widget', True)
        self.app._apply_headphones_update([{'id': 'a', 'name': 'A', 'battery_level': 87}])
        self.app._set_setting('aliases', {'a': 'Riêng'})
        self.assertIn('Riêng', self.app._mini_label.cget('text'))
        self.assertIn('87%', self.app._mini_label.cget('text'))
        self.assertNotIn('⚡', self.app._mini_label.cget('text'))
        self.app._apply_headphones_update([])
        self.assertNotIn('87', self.app._mini_label.cget('text'))
        win = self.app._mini_window
        self.app._set_setting('mini_widget', False)
        self.assertFalse(win.winfo_exists())

    def test_charging_case_alone_does_not_mark_earbuds(self):
        self.app._set_setting('mini_widget', True)
        self.app._apply_headphones_update([{'id': 'jbl', 'name': 'JBL'}])
        self.app._apply_battery_update(94, 67, 40, {'left': False, 'right': False, 'case': True})
        self.assertNotIn('⚡', self.app._mini_label.cget('text'))

    def test_components_show_own_charge_flags_and_drag_saves_position(self):
        self.app._set_setting('mini_widget', True)
        self.app._apply_headphones_update([{'id': 'jbl', 'name': 'JBL'}])
        self.app._apply_battery_update(94, 67, 100, {'left': False, 'right': True, 'case': False})
        text = self.app._mini_label.cget('text')
        self.assertIn('L 94', text)
        # The flash marks the charging earbud only.
        self.assertIn('R ⚡67', text)
        self.assertEqual(text.count('⚡'), 1)
        self.app._mini_window.update_idletasks()
        self.app._start_mini_drag(SimpleNamespace(x_root=100, y_root=100))
        self.app._move_mini(SimpleNamespace(x_root=140, y_root=150))
        self.app._mini_window.update_idletasks()
        self.app._save_mini_position(None)
        position = self.app.settings['mini_widget_pos']
        self.assertEqual(len(position), 2)
        self.assertEqual(json.loads(Path(main.SETTINGS_PATH).read_text(encoding='utf-8'))['mini_widget_pos'], position)
        self.app._set_setting('mini_widget', False)
        self.app._set_setting('mini_widget', True)
        self.app._mini_window.update_idletasks()
        self.assertEqual([self.app._mini_window.winfo_x(), self.app._mini_window.winfo_y()], position)

    def test_negative_monitor_position_and_content_size(self):
        self.app._set_setting('mini_widget_pos', [-500, -100])
        self.app._set_setting('mini_widget', True)
        win = self.app._mini_window
        win.update_idletasks()
        self.assertEqual([win.winfo_x(), win.winfo_y()], [-500, -100])
        self.assertLess(win.winfo_height(), 70)
        self.app._start_mini_drag(SimpleNamespace(x_root=-450, y_root=-80))
        self.app._move_mini(SimpleNamespace(x_root=-400, y_root=-60))
        win.update_idletasks()
        self.app._save_mini_position(None)
        self.assertEqual(self.app.settings['mini_widget_pos'], [-450, -80])
