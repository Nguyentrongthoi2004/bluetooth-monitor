import json
from pathlib import Path
import unittest
from unittest.mock import patch
import main
from app_test_support import TemporaryDirectory
import test_app_lifecycle as lifecycle


class SettingsValidationTests(unittest.TestCase):
    def test_new_values_are_validated_without_dropping_unknown_keys(self):
        with TemporaryDirectory() as folder:
            path = Path(folder, 'settings.json')
            path.write_text(json.dumps(dict(full_charge_alert='yes', full_charge_level=85,
                connection_alert=1, hotkey_enabled=None, show_other_devices=[], theme='purple',
                compact_mode='false', mini_widget=1, mini_widget_pos=[True, 12],
                aliases={'a': ' Riêng ', 'b': 2, 'c': ''}, jbl_battery_device_id='jbl')))
            with patch.object(main, 'SETTINGS_PATH', str(path)):
                settings = main.load_settings()
            for key in ('full_charge_alert', 'full_charge_level', 'connection_alert',
                        'hotkey_enabled', 'show_other_devices', 'theme', 'compact_mode',
                        'mini_widget', 'mini_widget_pos'):
                self.assertEqual(settings[key], main.DEFAULT_SETTINGS[key], key)
            self.assertEqual(settings['aliases'], {'a': 'Riêng'})
            self.assertEqual(settings['jbl_battery_device_id'], 'jbl')

    def test_mutable_defaults_are_not_shared_and_positions_survive(self):
        with TemporaryDirectory() as folder:
            path = Path(folder, 'settings.json')
            with patch.object(main, 'SETTINGS_PATH', str(path)):
                first = main.load_settings()
                first['aliases']['a'] = 'A'
                self.assertEqual(main.load_settings()['aliases'], {})
                first['mini_widget_pos'] = [40, 80]
                self.assertTrue(main.save_settings(first))
                self.assertEqual(main.load_settings()['mini_widget_pos'], [40, 80])


class SettingsUiTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def test_scrollable_settings_has_working_controls_including_hotkey_failure(self):
        self.app._apply_hotkey_status('Phím tắt đang được dùng')
        self.app._show_settings_dialog()
        win = self.app._settings_win
        self.assertIsInstance(win.settings_scroll, main.ctk.CTkScrollableFrame)
        self.assertIn('đang được dùng', win.lbl_hotkey_status.cget('text'))
        for key in ('hotkey_enabled', 'compact_mode', 'mini_widget', 'show_other_devices',
                    'full_charge_alert', 'connection_alert'):
            before = self.app.settings[key]
            win.setting_controls[key].toggle()
            self.assertEqual(self.app.settings[key], not before, key)
        win.full_charge_threshold.set('90%')
        win.full_charge_threshold._command('90%')
        self.assertEqual(self.app.settings['full_charge_level'], 90)
        self.app._set_setting('theme', 'light')
        self.assertTrue(self.app._settings_win.winfo_exists())
        self.assertEqual(self.app._settings_win.theme_choice.get(), 'Sáng')

    def test_advanced_settings_start_collapsed(self):
        self.app._show_settings_dialog()
        win = self.app._settings_win
        self.assertFalse(win.advanced_frame.winfo_manager())
        win.btn_advanced.invoke()
        self.assertTrue(win.advanced_frame.winfo_manager())
        self.assertIn('Nâng cao', win.btn_advanced.cget('text'))

    def test_copy_log_puts_version_and_log_tail_on_clipboard(self):
        with open(main.os.path.join(main.DATA_DIR, 'app.log'), 'w', encoding='utf-8') as file:
            file.write('line 1\nlỗi cuối cùng\n')
        self.app._show_settings_dialog()
        self.app._settings_win.btn_copy_log.invoke()
        text = self.app.clipboard_get()
        self.assertIn(main.APP_VERSION, text)
        self.assertTrue(text.rstrip().endswith('lỗi cuối cùng'))

    def test_settings_replace_battery_panel_until_save_and_close(self):
        self.app.show_window()
        self.app._show_settings_dialog()
        self.app.update()
        self.assertEqual(self.app.state(), 'withdrawn', 'Only one panel is on screen')
        self.app._settings_win.btn_save_close.invoke()
        self.app.update()
        self.assertIsNone(self.app._settings_win)
        self.assertEqual(self.app.state(), 'normal')
