import json
from pathlib import Path
import unittest
from unittest.mock import patch
import main
import test_app_lifecycle as lifecycle


class AliasTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def test_alias_is_id_based_across_card_list_tray_and_notifications(self):
        self.app._apply_headphones_update([{'id': 'a', 'name': 'Same', 'battery_level': 80},
                                           {'id': 'b', 'name': 'Same', 'battery_level': 80}])
        self.app._set_setting('aliases', {'a': 'Tai nghe của tôi'})
        self.app._select_headphone('a')
        self.assertEqual(self.app.lbl_device_title.cget('text'), 'Tai nghe của tôi')
        self.assertIn('Tai nghe của tôi', self.app.tray_icon.title)
        self.assertIn('Tai nghe của tôi', self.app._device_buttons[0].cget('text'))
        self.assertIn('Same', self.app._device_buttons[1].cget('text'))
        sent = []
        self.app._notify = lambda message, title: sent.append(message) or True
        self.app._apply_headphones_update([{'id': 'a', 'name': 'Same', 'battery_level': 10}])
        self.assertIn('Tai nghe của tôi', sent[-1])

    def rename(self, text, key='<Return>'):
        self.app._edit_alias()
        entry = self.app._alias_entry[0]
        entry.delete(0, 'end')
        entry.insert(0, text)
        {'<Return>': lambda: self.app._finish_alias(True),
         '<Escape>': lambda: self.app._finish_alias(False)}[key]()
        self.assertIsNone(self.app._alias_entry)
        self.assertTrue(self.app.lbl_device_title.winfo_manager(), 'Title comes back after editing')

    def test_inline_rename_blank_restores_and_escape_cancels(self):
        self.app._apply_headphones_update([{'id': 'a', 'name': 'Original'}])
        self.app._edit_alias()
        self.assertIsInstance(self.app._alias_entry[0], main.ctk.CTkEntry, 'No popup window')
        self.assertEqual(self.app._alias_entry[0].get(), 'Original')
        self.app._finish_alias(False)
        self.rename('  Riêng  ')
        self.assertEqual(self.app.lbl_device_title.cget('text'), 'Riêng')
        self.rename('Khác', '<Escape>')
        self.assertEqual(self.app.lbl_device_title.cget('text'), 'Riêng')
        self.rename('   ')
        self.assertEqual(self.app.lbl_device_title.cget('text'), 'Original')
        saved = json.loads(Path(main.SETTINGS_PATH).read_text(encoding='utf-8'))
        self.assertEqual(saved['aliases'], {})
