"""Giao diện tiếng Anh: cài đặt ngôn ngữ, tr(), đủ bản dịch trên mọi màn hình, đổi ngôn ngữ lúc đang chạy."""
import json
import re
import threading
import time
import tkinter as tk
import unicodedata
import unittest
from pathlib import Path
from unittest.mock import patch

from app_test_support import TemporaryDirectory
import audio_output
import bt_connect
import headphone_monitor
import i18n
import main
import test_app_lifecycle as lifecycle
from test_nearby import FakeScanner, device as nearby_device

# Chữ chỉ tiếng Việt có: đ và dấu mũ/trăng/móc/thanh (sau khi tách dấu bằng NFD).
_VIETNAMESE = re.compile('[̛̣̀́̂̃̆̉đĐ]')


def is_vietnamese(text):
    return bool(_VIETNAMESE.search(unicodedata.normalize('NFD', str(text))))


def widget_texts(root):
    """(đường dẫn widget, chữ) của mọi widget con: text, values, tiêu đề cửa sổ, chữ trên canvas."""
    found = []

    def walk(widget):
        for option in ('text', 'values', 'placeholder_text'):
            try:
                value = widget.cget(option)
            except Exception:
                continue
            for text in (value if isinstance(value, (list, tuple)) else [value]):
                if text:
                    found.append((str(widget), str(text)))
        if isinstance(widget, (tk.Tk, tk.Toplevel)):
            found.append((str(widget), widget.title()))
        if isinstance(widget, tk.Canvas):
            for canvas_item in widget.find_all():
                try:
                    found.append((str(widget), widget.itemcget(canvas_item, 'text')))
                except tk.TclError:
                    pass
        for child in widget.winfo_children():
            walk(child)
    walk(root)
    return found


def menu_texts(menu):
    for entry in menu:
        yield entry.text
        if entry.submenu:
            yield from menu_texts(entry.submenu)


class LanguageSettingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.path = Path(self.temp_dir.name, 'settings.json')
        self.enterContext(patch.object(main, 'SETTINGS_PATH', str(self.path)))

    def tearDown(self):
        self.temp_dir.cleanup()

    def write(self, data):
        self.path.write_text(json.dumps(data), encoding='utf-8')

    def test_existing_settings_without_language_stay_vietnamese(self):
        self.write({'theme': 'dark', 'low_battery_threshold': 15})
        settings = main.load_settings()
        self.assertEqual(settings['language'], 'vi')
        self.assertEqual(settings['low_battery_threshold'], 15)

    def test_new_install_follows_windows(self):
        self.assertFalse(self.path.exists())
        self.assertEqual(main.load_settings()['language'], 'system')

    def test_corrupt_settings_file_counts_as_existing_install(self):
        self.path.write_text('{not json', encoding='utf-8')
        self.assertEqual(main.load_settings()['language'], 'vi')

    def test_saved_choice_is_kept(self):
        for value in ('system', 'vi', 'en'):
            self.write({'language': value})
            self.assertEqual(main.load_settings()['language'], value)

    def test_invalid_values_are_rejected(self):
        for value in ('fr', 'EN', '', None, 1, ['en'], {'en': True}):
            self.write({'language': value})
            self.assertEqual(main.load_settings()['language'], 'vi', value)

    def test_round_trip(self):
        settings = main.load_settings()
        settings['language'] = 'en'
        self.assertTrue(main.save_settings(settings))
        self.assertEqual(main.load_settings()['language'], 'en')


class TranslateTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language('vi')

    def test_vietnamese_returns_the_source_string(self):
        i18n.set_language('vi')
        self.assertEqual(i18n.tr('Pin tốt'), 'Pin tốt')
        self.assertEqual(i18n.tr('Đã liên kết: {name}', name='Akko'), 'Đã liên kết: Akko')

    def test_english_fills_named_placeholders(self):
        i18n.set_language('en')
        self.assertEqual(i18n.tr('Pin tốt'), 'Good')
        self.assertEqual(i18n.tr('Đã liên kết: {name}', name='Akko'), 'Linked: Akko')
        self.assertEqual(i18n.tr('≈ còn {hours} giờ {minutes} phút', hours=2, minutes=5), '≈ 2 h 5 min left')
        self.assertEqual(i18n.tr('{name} | Pin: {level}% ({source})', name='JBL', level=80, source='BLE'),
                         'JBL | Battery: 80% (BLE)')

    def test_values_are_inserted_untranslated(self):
        i18n.set_language('en')
        self.assertEqual(i18n.tr('Đã liên kết: {name}', name='Pin tốt'), 'Linked: Pin tốt')

    def test_missing_translation_falls_back_to_source(self):
        i18n.set_language('en')
        self.assertEqual(i18n.tr('Chuỗi chưa dịch {n}', n=3), 'Chuỗi chưa dịch 3')

    def test_messages_from_workers_are_translated_when_shown(self):
        i18n.set_language('en')
        self.assertEqual(i18n.tr_message('Đã ghép đôi'), 'Paired')
        self.assertEqual(i18n.tr_message('Không ghép đôi được (FAILED)'), "Couldn't pair (FAILED)")
        self.assertEqual(i18n.tr_message('Không bỏ ghép đôi được (ACCESS_DENIED)'), "Couldn't unpair (ACCESS_DENIED)")
        self.assertFalse(is_vietnamese(i18n.tr_message(bt_connect.CANNOT_DISCONNECT)))
        self.assertEqual(i18n.tr_message('Something else'), 'Something else')
        i18n.set_language('vi')
        self.assertEqual(i18n.tr_message('Không ghép đôi được (FAILED)'), 'Không ghép đôi được (FAILED)')

    def test_resolve(self):
        self.assertEqual(i18n.resolve('vi'), 'vi')
        self.assertEqual(i18n.resolve('en'), 'en')
        with patch.object(i18n, 'system_language', return_value='en'):
            self.assertEqual(i18n.resolve('system'), 'en')
        with patch.object(i18n, 'system_language', return_value='vi'):
            self.assertEqual(i18n.resolve('system'), 'vi')

    def test_catalog_is_english_with_matching_placeholders(self):
        catalog = i18n._catalogs['en']
        self.assertGreater(len(catalog), 150)
        for source, english in catalog.items():
            self.assertFalse(is_vietnamese(english), english)
            self.assertEqual(sorted(re.findall(r'\{(\w+)\}', source)), sorted(re.findall(r'\{(\w+)\}', english)),
                             source)


JBL = {'id': 'jbl', 'name': 'JBL Test', 'battery_level': 80}
AKKO_ID = 'Bluetooth#Bluetooth0c:1d:2e:3f:40:bc-0b:1c:2d:3e:4f:05'
AKKO = {'id': AKKO_ID, 'name': 'Akko', 'battery_level': 15}
UNNAMED = {'id': 'unnamed', 'name': headphone_monitor.UNNAMED, 'kind': 'mouse', 'battery_level': 60}
OUTPUTS = [('spk', 'Speakers (Realtek Audio)'), ('hp', 'Headphones (JBL Test)'), ('n', audio_output.UNNAMED)]


class EnglishUiTests(unittest.TestCase):
    system_language = 'en'  # cài mới trên Windows tiếng Anh -> giao diện tiếng Anh
    setUp = lifecycle.AppLifecycleTests.setUp

    def tearDown(self):
        lifecycle.AppLifecycleTests.tearDown(self)
        i18n.set_language('vi')

    def assert_english(self, where):
        texts = widget_texts(self.app) + [('tray title', self.app.tray_icon.title)]
        texts += [('tray menu', text) for text in menu_texts(self.app.tray_icon.menu)]
        texts += [('notification', text) for pair in self.sent for text in pair]
        misses = sorted({(path, text) for path, text in texts if is_vietnamese(text)})
        self.assertEqual(misses, [], where)

    def test_every_screen_is_english(self):
        app = self.app
        self.assertEqual(app.settings['language'], 'system')
        self.assertEqual(i18n.language(), 'en')
        self.sent = []
        app._notify = lambda message, title: self.sent.append((title, message)) or True
        app.settings.update(connection_alert=True, alert_overlay=True, time_alert_minutes=15)
        self.enterContext(patch.object(main.audio_output, 'list_outputs', return_value=OUTPUTS))
        self.enterContext(patch.object(main.audio_output, 'default_output_id', return_value='spk'))
        app.show_window()
        app._apply_headphones_update([])
        self.assert_english('no headphones')

        # Tai nghe đang kết nối: pin từng bên, danh sách thiết bị, biểu đồ, cảnh báo, hàng âm thanh, cửa sổ nổi.
        app._apply_headphones_update([dict(AKKO), dict(UNNAMED), dict(JBL)])
        app._announce_connection_changes()
        app._select_headphone('jbl')
        now = time.time()
        samples = [{'t': now - 7200, 'level': 90, 'source': 'fast_pair'}, {'t': now, 'level': 80, 'source': 'fast_pair'}]
        self.enterContext(patch.object(app.history, 'samples', return_value=samples))
        estimate = self.enterContext(patch.object(main, 'estimate_remaining', return_value=95))
        app._apply_battery_update(80, 15, 60, {'left': False, 'right': False, 'case': False, '_fresh': True,
                                               '_updated_at': '2026-01-01 10:00:00'})
        self.assertTrue(app.row_left.winfo_manager())
        self.assertTrue(app.lbl_remaining.winfo_manager())
        self.assertTrue(app.devices_frame.winfo_manager())
        self.assertEqual(app.lbl_l_name.cget('text'), 'Left')
        estimate.return_value = 10
        app._check_time_alert()
        app._apply_battery_update(100, 100, 100, {'left': True, 'right': True, 'case': True, '_fresh': True})
        app._set_setting('mini_widget', True)
        app._render_audio_row()
        self.assertEqual(list(app.audio_switch.cget('values')), ['Speakers', 'Headphones'])
        self.assertTrue(self.sent, 'Alerts were sent')
        self.assert_english('connected with per-earbud battery')
        app._apply_battery_update(None, None, None, {'_fresh': False})
        app._apply_status_update(False, 'Dữ liệu đã cũ · đang chờ tai nghe')
        self.assert_english('stale battery')

        # Bảng Cài đặt, mở cả phần nâng cao; phím tắt lỗi.
        app._show_settings_dialog()
        win = app._settings_win
        if not win.advanced_frame.winfo_manager():
            win.toggle_advanced()
        app._apply_hotkey_status('Không đăng ký được Ctrl+Alt+B (có thể đang được ứng dụng khác sử dụng).')
        self.assertEqual(list(win.language_choice.cget('values')), ['Same as Windows', 'Vietnamese', 'English'])
        self.assert_english('settings')
        app._unbind_fast_pair()
        self.assert_english('settings after unlinking')
        app._close_settings()

        # Bảng chọn tai nghe để xem pin từng bên: có tai nghe, rồi không thấy gì.
        app.fast_pair_scanner.items = [('AA:BB:CC:DD:EE:02', -50, ((80, False), (70, True), (None, None)))]
        app._bind_fast_pair_to_active()
        self.assertEqual(len(app._split_picker.rows), 1)
        self.assert_english('earbud picker')
        app.fast_pair_scanner.items = []
        app._split_picker.opened -= 60
        app._refresh_split_picker()
        self.assert_english('earbud picker, nothing found')
        app._close_split_picker()

        # Bảng nổi.
        app._show_device_toast({'id': 'x', 'name': 'Sony', 'kind': 'headphone'})
        self.assert_english('device toast')
        with patch.object(main.audio_output, 'list_outputs', return_value=[]):
            app._toggle_audio_output()
        self.assert_english('audio toast')
        app._set_audio_output(('n', audio_output.UNNAMED))
        self.assertEqual(app._toast.lbl_subtitle.cget('text'), 'Audio device')
        self.assert_english('audio failed toast')

        # Ngắt kết nối: thẻ xám có nút Kết nối, Bluetooth tắt, lỗi đọc kết nối.
        app._select_headphone(AKKO_ID)
        app._apply_headphones_update([])
        app._announce_connection_changes()
        self.assertTrue(app.btn_connect.winfo_manager(), 'Offline card offers Connect')
        self.assert_english('offline card')
        app._connecting.add((AKKO_ID, True))
        app._render_connect_button()
        self.assert_english('offline card connecting')
        app._apply_headphones_update([], main.RADIO_OFF)
        self.assert_english('Bluetooth off')
        app._apply_headphones_update([], headphone_monitor.READ_FAILED)
        app._apply_status_update(False, 'Bluetooth PC đang tắt')
        self.assert_english('connections unreadable')

    def test_feedback_and_support_are_english(self):
        import product
        self.sent = []
        with patch.object(product, 'DONATE_URL', 'https://ko-fi.com/x'):
            self.app._show_settings_dialog()
            self.app._show_feedback_dialog()
            self.app._feedback_win.text.insert('1.0', 'x')
            self.app._feedback_count()
            self.assert_english('settings + feedback dialog')
            self.app._feedback_win.btn_send.invoke()  # không có form: chép clipboard + thông báo
            self.assert_english('feedback toast')
            self.app._open_donate()
            self.assert_english('donate dialog with QR codes')
            self.app._donate_win.btn_copy.invoke()
        self.assert_english('account copied toast')

    def test_bluetooth_panel_is_english(self):
        app = self.app
        self.sent = []
        paired = nearby_device('Akko', paired=True, connected=True, id_='akko')
        paired['address'] = '0B:1C:2D:3E:4F:05'
        unpaired = nearby_device('WH-1000XM5', signal=-55, id_='sony')
        scanner = FakeScanner()
        scanner.devices = [paired, unpaired, nearby_device('', signal=-80, le=True, kind='phone'),
                           nearby_device('Pixel', signal=-90, kind='phone', id_='pixel')]
        app._pairing_ids.add('pixel')
        self.enterContext(patch.object(main.bt_scan, 'NearbyScanner', return_value=scanner))
        thread = self.enterContext(patch.object(main.threading, 'Thread'))
        self.enterContext(patch.object(main.bt_scan, 'read_details', return_value={
            'battery': 50, 'manufacturer': 'Acme', 'model': 'X1', 'firmware': '1.0'}))
        app.show_window()
        app.btn_bluetooth.invoke()
        app._nearby_win.show_unnamed.toggle()
        self.assertEqual(app._nearby_win.title(), 'Nearby Bluetooth devices')
        self.assert_english('device list')

        app._show_nearby_details(paired)
        thread.call_args.kwargs['target']()
        app._drain_ui_queue()
        detail = app._nearby_win.detail
        self.assertEqual(detail.fields['Pin'].cget('text'), '50% · BLE')
        detail.btn_unpair.invoke()
        self.assert_english('paired device details with unpair confirmation')
        app._details_connect(paired, False)
        self.assert_english('disconnecting')
        app._finish_device_connection(paired, ('akko', False), False, bt_connect.CANNOT_DISCONNECT)
        self.assertEqual(app._toast.lbl_subtitle.cget('text'), i18n.tr(bt_connect.CANNOT_DISCONNECT))
        self.assert_english('disconnect refused')

        app._show_nearby_details(unpaired)
        app._ask_pairing_pin(unpaired, '123456', False, [], threading.Event())
        self.assert_english('unpaired details and PIN comparison')
        app._ask_pairing_pin(nearby_device('', id_='anon'), '0000', True, [], threading.Event())
        self.assert_english('PIN entry')
        app._finish_pairing(unpaired, False, 'Không ghép đôi được (FAILED)')
        self.assertEqual(app._toast.lbl_subtitle.cget('text'), "Couldn't pair (FAILED)")
        app._finish_unpair(nearby_device('', id_='anon'), True, 'Đã bỏ ghép đôi')
        self.assertEqual(app._toast.lbl_subtitle.cget('text'), 'Unpaired')
        self.assert_english('pairing results')
        app._nearby_win.btn_close.invoke()

        # Không quét được.
        app._nearby_scanner = None
        with patch.object(main.bt_scan, 'NearbyScanner', side_effect=RuntimeError('radio off')):
            app._show_nearby_panel()
        self.assert_english('scan failed')

    def test_segmented_buttons_show_english_but_keep_internal_values(self):
        app = self.app
        app._apply_headphones_update([dict(JBL)])
        app._set_chart_days(7)
        app._set_setting('theme', 'light')  # dựng lại giao diện
        self.assertEqual(app.chart_controls.get(), '7 days')
        app.chart_controls._command('24 hours')
        self.assertEqual(app._chart_days, 1)
        app._show_settings_dialog()
        win = app._settings_win
        self.assertEqual(list(win.time_alert.cget('values')), ['Off', '15 min', '30 min'])
        win.time_alert._command('30 min')
        self.assertEqual(app.settings['time_alert_minutes'], 30)
        self.assertEqual(win.theme_choice.get(), 'Light')
        with patch.object(main.audio_output, 'list_outputs', return_value=OUTPUTS), \
                patch.object(main.audio_output, 'default_output_id', return_value='spk'), \
                patch.object(main.audio_output, 'set_default_output', return_value=True) as switch:
            app._render_audio_row()
            self.assertEqual(app.audio_switch.get(), 'Speakers')
            app._on_audio_switch('Headphones')
        switch.assert_called_once_with('hp')


class LanguageSwitchTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp

    def tearDown(self):
        lifecycle.AppLifecycleTests.tearDown(self)
        i18n.set_language('vi')

    def test_switching_language_rebuilds_the_ui(self):
        app = self.app
        self.assertEqual(i18n.language(), 'vi')
        self.assertEqual(app.btn_bluetooth.cget('text'), 'Thiết bị BT')
        self.assertTrue(any(is_vietnamese(text) for _, text in widget_texts(app)), 'Checker sees Vietnamese')
        app._apply_headphones_update([dict(JBL)])
        app._set_chart_days(7)
        old_button = app.btn_bluetooth
        app._bind_fast_pair_to_active()
        app._show_settings_dialog()
        app._settings_win.language_choice._command('English')

        self.assertEqual(app.settings['language'], 'en')
        self.assertEqual(main.load_settings()['language'], 'en', 'Saved immediately')
        self.assertFalse(old_button.winfo_exists(), 'UI was rebuilt')
        self.assertEqual(app.btn_bluetooth.cget('text'), 'Bluetooth devices')
        self.assertEqual(app.chart_controls.get(), '7 days', 'Chart selection survives the rebuild')
        win = app._settings_win
        self.assertEqual(win.title(), 'Settings', 'Settings reopened in English')
        self.assertEqual(win.language_choice.get(), 'English')
        self.assertEqual(app._split_picker.title(), 'Show each earbud')
        self.assertEqual(list(app.tray_icon.menu)[0].text, 'Open')
        self.assertIn('JBL Test | Battery: 80% (Windows)', app.tray_icon.title)
        self.assertEqual([path for path, text in widget_texts(app) if is_vietnamese(text)], [])

        win.language_choice._command('Vietnamese')
        self.assertEqual(i18n.language(), 'vi')
        self.assertEqual(app.btn_bluetooth.cget('text'), 'Thiết bị BT')
        self.assertEqual(app._settings_win.title(), 'Cài đặt')
        self.assertEqual(list(app.tray_icon.menu)[0].text, 'Mở bảng điều khiển')
        self.assertIn('Pin: 80%', app.tray_icon.title)

        app._settings_win.language_choice._command('Theo Windows')
        self.assertEqual(app.settings['language'], 'system')
        self.assertEqual(i18n.language(), 'vi', 'Follows the (patched) Windows language')


if __name__ == '__main__':
    unittest.main()
