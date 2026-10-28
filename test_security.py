"""Kiểm tra bảo mật: thứ người ngoài chạm được vào app phải không lừa được người dùng hay lộ thông tin.

- Thiết bị Bluetooth lạ ở gần: tự đặt tên, tự phát gói tin, tự khai hãng/model.
- Ghép đôi: mã so khớp chống kẻ chen giữa (MITM) phải do người dùng so, không tự đồng ý.
- Cổng IPC nội bộ: chỉ máy này gọi được; trang web độc hại gửi request HTTP tới localhost không làm gì được.
- Log gửi cho người khác: không lộ địa chỉ Bluetooth, tên tài khoản Windows.
- File cài đặt/lịch sử bị sửa bậy, mục khởi động cùng Windows.
"""
import json
import random
import socket
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from winrt.windows.devices.enumeration import DevicePairingKinds, DevicePairingResultStatus

import apple_pods
import battery_history
import bt_scan
import headphone_monitor
import main
import safe_text
import test_app_lifecycle as lifecycle
from app_test_support import TemporaryDirectory

SPOOFED_NAMES = [
    'JBL\u202eSDOPria',                     # đảo chiều chữ: hiện thành "JBLairPODS"
    'Air\u200bPods\u200d Pro',              # khoảng trắng độ rộng 0 để giả tên thật
    'Tai nghe\nWindows: gọi 1900 1234 để mở khóa máy',
    'Loa\r\n\tcủa\x00\x07 Long',
    '\u2066Microsoft\u2069 Support\ufeff',
    'A' * 5000,
]


class CleanNameTests(unittest.TestCase):
    def test_hidden_and_control_characters_are_removed(self):
        self.assertEqual(safe_text.clean_name('JBL\u202eSDOPria'), 'JBLSDOPria')
        self.assertEqual(safe_text.clean_name('Air\u200bPods\u200d Pro'), 'AirPods Pro')
        self.assertEqual(safe_text.clean_name('Loa\r\n\tcủa\x00\x07 Long'), 'Loa của Long')
        self.assertEqual(safe_text.clean_name('\u2066Microsoft\u2069 Support\ufeff'), 'Microsoft Support')

    def test_fake_notification_cannot_add_lines(self):
        name = safe_text.clean_name('Tai nghe\nWindows: gọi 1900 1234 để mở khóa máy')
        self.assertNotIn('\n', name)
        self.assertLessEqual(len(name), safe_text.NAME_LIMIT)

    def test_every_spoofed_name_is_single_line_short_and_visible(self):
        for raw in SPOOFED_NAMES:
            with self.subTest(raw=raw[:30]):
                name = safe_text.clean_name(raw, 'fallback')
                self.assertLessEqual(len(name), safe_text.NAME_LIMIT)
                self.assertEqual(name, name.strip())
                self.assertFalse(any(ch in name for ch in '\n\r\t\x00\u202e\u200b\u2066\ufeff'))

    def test_long_name_is_cut_with_ellipsis(self):
        name = safe_text.clean_name('A' * 5000)
        self.assertEqual(len(name), safe_text.NAME_LIMIT)
        self.assertTrue(name.endswith('…'))

    def test_real_names_are_unchanged(self):
        for name in ('Akko S9 Ultra_BT', 'JBL CLUB PRO+ TWS', 'iPhone của Long', 'Loa 🎵 phòng khách',
                     '[TV] Samsung 7 Series (55)'):
            self.assertEqual(safe_text.clean_name(name), name)
        decomposed = 'Tai nghe cu\u0309a Long'  # dấu tiếng Việt dạng tổ hợp
        self.assertEqual(safe_text.clean_name(decomposed), 'Tai nghe của Long')

    def test_missing_or_invisible_name_falls_back(self):
        self.assertEqual(safe_text.clean_name(None, 'Tai nghe Bluetooth'), 'Tai nghe Bluetooth')
        self.assertEqual(safe_text.clean_name(123, 'X'), 'X')
        self.assertEqual(safe_text.clean_name('\u200b\u202e \n', 'X'), 'X')
        self.assertEqual(safe_text.clean_name(''), '')


class FakeProperties:
    def __init__(self, values=None):
        self.values = values or {}

    def lookup(self, key):
        if key not in self.values:
            raise KeyError(key)
        return self.values[key]


class UntrustedBluetoothDataTests(unittest.TestCase):
    def test_nearby_scan_cleans_names_from_unknown_devices(self):
        for raw in SPOOFED_NAMES:
            info = SimpleNamespace(id='BluetoothLE#BluetoothLE00:11', name=raw, properties=FakeProperties())
            with self.subTest(raw=raw[:30]):
                self.assertEqual(bt_scan.describe(info, 0)['name'], safe_text.clean_name(raw))

    def test_connected_device_names_are_cleaned(self):
        import asyncio
        devices = [SimpleNamespace(id='hp', name='Tai nghe\u202e\nGọi 1900', properties=None),
                   SimpleNamespace(id='blank', name='\u200b', properties=None)]
        values = {headphone_monitor.CONNECTED_KEY: True, headphone_monitor.MAJOR_KEY: 4,
                  headphone_monitor.MINOR_KEY: 1, headphone_monitor.ADDRESS_KEY: None}
        with patch.object(headphone_monitor.DeviceInformation, 'find_all_async_aqs_filter_and_additional_properties',
                          AsyncMock(return_value=devices)), \
                patch.object(headphone_monitor.BluetoothDevice, 'get_device_selector', return_value=''), \
                patch.object(headphone_monitor, '_read_property', lambda info, key, getter: values[key]):
            result = asyncio.run(headphone_monitor.read_connected_devices())
        names = [device['name'] for device in result]
        self.assertCountEqual(names, ['Tai nghe Gọi 1900', 'Tai nghe Bluetooth'])

    def test_apple_parser_survives_random_and_hostile_packets(self):
        rng = random.Random(1234)
        packets = [bytes(rng.randrange(256) for _ in range(rng.randrange(40))) for _ in range(20000)]
        packets += [bytes([0x07, 0x19, 0x01, 0x0E, 0x20, status, pods, case]) + bytes(19)
                    for status in (0, 0x20, 0xFF) for pods in range(0, 256, 7) for case in range(0, 256, 13)]
        for data in packets:
            parsed = apple_pods.parse(data)
            if parsed is None:
                continue
            for side in ('left', 'right', 'case'):
                self.assertIn(parsed[side], [None, *range(0, 101, 10)], data.hex())
            self.assertTrue(all(type(value) is bool for value in parsed['charging'].values()))

    def test_apple_levels_above_100_percent_are_rejected(self):
        for nibble in range(11, 16):
            self.assertIsNone(apple_pods._level(nibble))

    def test_far_away_apple_packets_are_ignored(self):
        scanner = apple_pods.ApplePodsScanner(lambda parsed: None)
        data = bytes([0x07, 0x19, 0x01, 0x0E, 0x20, 0x2B, 0x89, 0x26]) + bytes(19)
        section = SimpleNamespace(data=data)

        def received(rssi):
            advertisement = Mock()
            advertisement.get_manufacturer_data_by_company_id.return_value = [section]
            scanner._on_received(None, SimpleNamespace(raw_signal_strength_in_dbm=rssi, advertisement=advertisement))

        received(apple_pods.MIN_RSSI - 1)
        self.assertEqual(scanner._seen, [], 'AirPods of someone across the room are not ours')
        received(apple_pods.MIN_RSSI)
        self.assertEqual(len(scanner._seen), 1)

    def test_broken_apple_advertisement_never_raises(self):
        scanner = apple_pods.ApplePodsScanner(lambda parsed: None)
        scanner._on_received(None, SimpleNamespace(raw_signal_strength_in_dbm=None, advertisement=None))
        scanner._on_received(None, object())
        self.assertEqual(scanner._seen, [])


class PairingCodeTests(unittest.TestCase):
    """Mã so khớp chỉ chống chen giữa nếu người thật so mã; app không được tự bấm "đồng ý" thay người dùng."""

    def request(self, kind, pin='123456'):
        return Mock(pairing_kind=kind, pin=pin)

    def test_pin_match_is_never_accepted_without_a_person(self):
        args = self.request(DevicePairingKinds.CONFIRM_PIN_MATCH)
        bt_scan.answer_pairing_request(args, None)
        args.accept.assert_not_called()

    def test_pin_match_rejected_when_user_says_codes_differ(self):
        args = self.request(DevicePairingKinds.CONFIRM_PIN_MATCH)
        confirm = Mock(return_value=False)
        bt_scan.answer_pairing_request(args, confirm)
        confirm.assert_called_once_with('123456', False)
        args.accept.assert_not_called()
        args.get_deferral.return_value.complete.assert_called_once()

    def test_pin_match_accepted_only_after_user_confirms(self):
        args = self.request(DevicePairingKinds.CONFIRM_PIN_MATCH)
        bt_scan.answer_pairing_request(args, Mock(return_value=True))
        args.accept.assert_called_once_with()
        args.get_deferral.return_value.complete.assert_called_once()

    def test_display_pin_is_shown_to_user_before_accepting(self):
        args = self.request(DevicePairingKinds.DISPLAY_PIN, pin='4821')
        confirm = Mock(return_value=True)
        bt_scan.answer_pairing_request(args, confirm)
        confirm.assert_called_once_with('4821', True)
        args.accept.assert_called_once()

    def test_truthy_but_not_true_answer_does_not_accept(self):
        args = self.request(DevicePairingKinds.CONFIRM_PIN_MATCH)
        bt_scan.answer_pairing_request(args, Mock(return_value='yes'))
        args.accept.assert_not_called()

    def test_failing_prompt_means_rejected(self):
        args = self.request(DevicePairingKinds.CONFIRM_PIN_MATCH)
        with self.assertLogs(level='ERROR'):
            bt_scan.answer_pairing_request(args, Mock(side_effect=RuntimeError('UI gone')))
        args.accept.assert_not_called()
        args.get_deferral.return_value.complete.assert_called_once()

    def test_pin_match_without_code_is_rejected(self):
        args = self.request(DevicePairingKinds.CONFIRM_PIN_MATCH, pin='')
        confirm = Mock(return_value=True)
        bt_scan.answer_pairing_request(args, confirm)
        confirm.assert_not_called()
        args.accept.assert_not_called()

    def test_unsupported_kinds_are_not_answered(self):
        for kind in (DevicePairingKinds.PROVIDE_PASSWORD_CREDENTIAL, DevicePairingKinds.NONE):
            args = self.request(kind)
            bt_scan.answer_pairing_request(args, Mock(return_value=True))
            args.accept.assert_not_called()
            args.accept_with_pin.assert_not_called()

    def test_pair_passes_user_answer_through_windows_pairing(self):
        handlers, answered = [], []

        async def pair_async(kinds):
            args = self.request(DevicePairingKinds.CONFIRM_PIN_MATCH)
            handlers[0](None, args)
            answered.append(args.accept.called)
            return SimpleNamespace(status=DevicePairingResultStatus.REJECTED_BY_HANDLER)
        custom = SimpleNamespace(add_pairing_requested=lambda handler: handlers.append(handler) or 7,
                                 remove_pairing_requested=Mock(), pair_async=pair_async)
        info = SimpleNamespace(pairing=SimpleNamespace(is_paired=False, can_pair=True, custom=custom))
        with patch.object(bt_scan.DeviceInformation, 'create_from_id_async', AsyncMock(return_value=info)), \
                self.assertLogs(level='WARNING'):
            ok, message = bt_scan.pair('dev', confirm_pin=lambda pin, display: False)
        self.assertFalse(ok)
        self.assertEqual(answered, [False])
        self.assertEqual(message, 'Đã hủy ghép đôi')
        custom.remove_pairing_requested.assert_called_once_with(7)


class PairingPromptUiTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def open_panel(self):
        scanner = Mock(running=False)
        scanner.snapshot.return_value = []
        self.enterContext(patch.object(main.bt_scan, 'NearbyScanner', return_value=scanner))
        self.app.show_window()
        self.app.btn_bluetooth.invoke()

    def ask_in_background(self, display=False):
        result = {}
        device = {'id': 'x', 'name': 'WH-1000XM5'}
        worker = threading.Thread(target=lambda: result.setdefault(
            'answer', self.app._wait_pin_answer(device, '123456', display)), daemon=True)
        worker.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not (self.app._nearby_win and self.app._nearby_win.pin_box):
            self.app._drain_ui_queue()
            time.sleep(.01)
        return worker, result

    def test_code_is_shown_and_mismatch_rejects(self):
        self.open_panel()
        worker, result = self.ask_in_background()
        box = self.app._nearby_win.pin_box
        self.assertEqual(box.lbl_pin.cget('text'), '123456')
        self.assertEqual(box.btn_no.cget('text'), 'Không khớp')
        box.btn_no.invoke()
        worker.join(2)
        self.assertIs(result['answer'], False)
        self.assertIsNone(self.app._nearby_win.pin_box)

    def test_user_confirms_matching_code(self):
        self.open_panel()
        worker, result = self.ask_in_background()
        self.app._nearby_win.pin_box.btn_yes.invoke()
        worker.join(2)
        self.assertIs(result['answer'], True)

    def test_display_code_asks_user_to_type_it(self):
        self.open_panel()
        worker, result = self.ask_in_background(display=True)
        self.assertEqual(self.app._nearby_win.pin_box.btn_yes.cget('text'), 'Đã nhập xong')
        self.app._nearby_win.pin_box.btn_no.invoke()
        worker.join(2)
        self.assertIs(result['answer'], False)

    def test_closing_panel_rejects_immediately(self):
        self.open_panel()
        worker, result = self.ask_in_background()
        started = time.monotonic()
        self.app._close_nearby_panel()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertLess(time.monotonic() - started, 2, 'Pairing thread must not wait for the full timeout')
        self.assertIs(result['answer'], False)

    def test_no_panel_means_rejected(self):
        worker, result = self.ask_in_background()
        worker.join(2)
        self.assertIs(result['answer'], False)

    def test_timeout_rejects_and_late_click_changes_nothing(self):
        self.open_panel()
        with patch.object(main.bt_scan, 'PIN_TIMEOUT', .2):
            worker, result = self.ask_in_background()
            worker.join(2)
        self.assertIs(result['answer'], False)
        self.app._nearby_win.pin_box.btn_yes.invoke()  # bấm muộn sau khi hết giờ
        self.assertIs(result['answer'], False)

    def test_finished_pairing_removes_leftover_prompt(self):
        self.open_panel()
        with patch.object(main.bt_scan, 'PIN_TIMEOUT', .2):
            worker, _ = self.ask_in_background()
            worker.join(2)
        self.app._finish_pairing({'id': 'x', 'name': 'WH-1000XM5'}, False, 'Đã hủy ghép đôi')
        self.assertIsNone(self.app._nearby_win.pin_box)


class IpcTests(unittest.TestCase):
    """Cổng IPC chỉ để mở lại cửa sổ đang chạy; không được nghe ngoài máy hay làm theo request HTTP."""

    def setUp(self):
        self.app = SimpleNamespace(_closing=False, calls=[])
        self.app._enqueue_ui = self.enqueue
        self.app.show_window = lambda: None
        self.app._on_refresh_clicked = lambda: None
        self.app._complete_status_request = lambda result, done: (result.update(connected=True), done.set())
        self.server = socket.socket()
        self.server.bind(('127.0.0.1', 0))
        self.server.listen(5)
        self.server.settimeout(.2)
        self.port = self.server.getsockname()[1]
        main.start_ipc_listener(self.app, self.server)

    def tearDown(self):
        self.app._closing = True
        self.server.close()

    def enqueue(self, callback, *args):
        self.app.calls.append(callback.__name__ if hasattr(callback, '__name__') else callback)
        callback(*args)

    def send(self, payload, read=False):
        with socket.create_connection(('127.0.0.1', self.port), timeout=3) as client:
            client.sendall(payload)
            client.shutdown(socket.SHUT_WR)
            data = client.recv(65536) if read else b''
        time.sleep(.1)
        return data

    def test_single_instance_port_listens_on_this_machine_only(self):
        reservation = socket.socket()
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
        reservation.close()
        with patch.object(main, 'IPC_PORT', port):
            server = main.try_single_instance()
        try:
            self.assertEqual(server.getsockname()[0], '127.0.0.1')
        finally:
            server.close()
        source = Path(main.__file__).read_text(encoding='utf-8')
        self.assertNotIn('0.0.0.0', source)
        self.assertNotIn('bind(("",', source)

    def test_known_commands_work(self):
        self.send(b'SHOW')
        self.assertEqual(self.app.calls, ['<lambda>'])
        status = json.loads(self.send(b'STATUS', read=True))
        self.assertEqual(status, {'connected': True})

    def test_browser_requests_to_localhost_do_nothing(self):
        # Trang web độc hại có thể gửi request tới 127.0.0.1:<cổng>; dòng đầu luôn là dòng HTTP.
        requests = [
            b'GET /SHOW HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n',
            b'POST / HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 6\r\n\r\nSTATUS',
            b'POST / HTTP/1.1\r\nContent-Type: text/plain\r\n\r\nSHOW\n',
        ]
        for request in requests:
            with self.subTest(request=request[:20]):
                self.assertEqual(self.send(request, read=True), b'')
        self.assertEqual(self.app.calls, [])

    def test_unknown_or_malformed_commands_are_ignored(self):
        for payload in (b'show', b'SHOW extra', b'STATUS;SHOW', b'\x00\xff' * 300, b'EXIT', b'', b'A' * 1_000_000):
            with self.subTest(payload=payload[:20]):
                try:
                    self.send(payload)
                except OSError:
                    pass  # app đóng kết nối trước khi đọc hết 1 MB: đúng ý
        self.assertEqual(self.app.calls, [])
        self.send(b'SHOW')
        self.assertEqual(self.app.calls, ['<lambda>'], 'Listener still serves after bad input')

    def test_idle_client_cannot_block_listener(self):
        idle = socket.create_connection(('127.0.0.1', self.port))
        try:
            with self.assertNoLogs(level='ERROR'):  # kết nối bỏ lửng không làm đầy log
                started = time.monotonic()
                self.send(b'SHOW')
                deadline = time.monotonic() + 4
                while not self.app.calls and time.monotonic() < deadline:
                    time.sleep(.05)
            self.assertEqual(self.app.calls, ['<lambda>'])
            self.assertLess(time.monotonic() - started, 4)
        finally:
            idle.close()


class StartupEntryTests(unittest.TestCase):
    def capture_command(self, frozen, executable):
        key = MagicMock()
        with patch.object(main.winreg, 'OpenKey', return_value=key) as open_key, \
                patch.object(main.winreg, 'SetValueEx') as set_value, \
                patch.object(main.sys, 'frozen', frozen, create=True), \
                patch.object(main.sys, 'executable', executable):
            self.assertTrue(main.set_startup(True))
        self.assertEqual(open_key.call_args.args[0], main.winreg.HKEY_CURRENT_USER, 'Never needs admin rights')
        return set_value.call_args.args[4]

    def test_exe_path_with_spaces_is_quoted(self):
        # Đường dẫn có dấu cách mà không có ngoặc kép: Windows có thể chạy nhầm "C:\Program.exe".
        exe = r'C:\Program Files\Bluetooth Monitor\Bluetooth Monitor.exe'
        self.assertEqual(self.capture_command(True, exe), f'"{exe}" --minimized')

    def test_source_run_quotes_both_paths(self):
        command = self.capture_command(False, r'C:\Python 314\python.exe')
        self.assertTrue(command.startswith('"'))
        self.assertEqual(command.count('"'), 4)
        self.assertTrue(command.endswith('" --minimized'))


class StartupMigrationTests(unittest.TestCase):
    EXE = r'C:\Tools\Bluetooth Monitor.exe'

    def run_migration(self, values):
        values = dict(values)

        def query(key, name):
            if name not in values:
                raise FileNotFoundError(name)
            return values[name], 1

        def set_value(key, name, reserved, kind, data):
            values[name] = data
        with patch.object(main.winreg, 'OpenKey', return_value=MagicMock()), \
                patch.object(main.winreg, 'QueryValueEx', side_effect=query), \
                patch.object(main.winreg, 'DeleteValue', side_effect=lambda key, name: values.pop(name)), \
                patch.object(main.winreg, 'SetValueEx', side_effect=set_value), \
                patch.object(main.sys, 'frozen', True, create=True), \
                patch.object(main.sys, 'executable', self.EXE):
            main.migrate_startup_entry()
        return values

    def test_entry_pointing_to_another_exe_follows_the_running_app(self):
        values = self.run_migration({main.REG_NAME: r'"D:\old\Bluetooth Monitor\Bluetooth Monitor.exe" --minimized'})
        self.assertEqual(values, {main.REG_NAME: f'"{self.EXE}" --minimized'})

    def test_old_name_entry_is_replaced(self):
        values = self.run_migration({main.OLD_REG_NAME: r'"D:\JBL\JBL Battery Monitor.exe"'})
        self.assertEqual(values, {main.REG_NAME: f'"{self.EXE}" --minimized'})

    def test_disabled_startup_stays_disabled(self):
        self.assertEqual(self.run_migration({'Other': 'x'}), {'Other': 'x'})

    def test_correct_entry_is_left_alone(self):
        correct = {main.REG_NAME: f'"{self.EXE}" --minimized'}
        self.assertEqual(self.run_migration(correct), correct)


class LogPrivacyTests(unittest.TestCase):
    LOG = ('2026-10-28 02:12:54,311 INFO GattBattery BLE battery for 0B:1C:2D:3E:4F:05: 93%\n'
           '2026-10-28 02:13:00,000 WARNING Pairing Bluetooth#Bluetooth0c:1d:2e:3f:40:bc-0a:1b:2c:3d:4e:3f ended\n'
           '2026-10-28 02:14:00,000 INFO address 0A1B2C3D4E3F battery 80\n'
           'File "C:\\Users\\alice\\AppData\\Local\\Bluetooth Monitor\\x.py", line 3\n'
           'crash at D:/Users/Long Nguyen/app\n')

    def test_redact_hides_addresses_and_account_name(self):
        text = safe_text.redact(self.LOG)
        for secret in ('0B:1C:2D:3E', '0c:1d:2e', '0a:1b:2c:3d', '0A1B2C3D4E', 'alice'):
            self.assertNotIn(secret, text)
        self.assertIn('XX:XX:XX:XX:XX:05: 93%', text, 'Last two digits stay to tell devices apart')
        self.assertIn('XX:XX:XX:XX:XX:BC-XX:XX:XX:XX:XX:3F', text)
        self.assertIn('XXXXXXXXXX3F', text)
        self.assertIn('C:\\Users\\***\\AppData', text)
        self.assertIn('2026-10-28 02:12:54,311', text, 'Times are not mistaken for addresses')

    def test_copied_log_is_redacted(self):
        with TemporaryDirectory() as folder, patch.object(main, 'DATA_DIR', folder):
            Path(folder, 'app.log').write_text(self.LOG, encoding='utf-8')
            text = main.read_log_tail()
        self.assertTrue(text.startswith(f'{main.APP_NAME} {main.APP_VERSION}'))
        self.assertNotIn('0B:1C:2D:3E:4F:05', text)
        self.assertNotIn('alice', text)


class CopyLogUiTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def test_clipboard_never_gets_raw_addresses(self):
        Path(main.DATA_DIR, 'app.log').write_text(LogPrivacyTests.LOG, encoding='utf-8')
        self.app._copy_log()
        copied = self.app.clipboard_get()
        self.assertNotIn('0B:1C:2D:3E:4F:05', copied)
        self.assertIn('XX:XX:XX:XX:XX:05', copied)


class TamperedFileTests(unittest.TestCase):
    def test_hostile_settings_fall_back_to_safe_values(self):
        hostile = {'low_battery_threshold': 10 ** 9, 'full_charge_level': -1, 'theme': '../../evil',
                   'time_alert_minutes': 'x', 'aliases': {'id': 5, '': 'x', 'ok': '  Tai nghe  ', 7: 'n'},
                   'mini_widget_pos': ['1', '2'], 'hotkey_enabled': 'yes', 'popup_on_connect': None}
        for content in (json.dumps(hostile), json.dumps([1, 2]), '{"broken', '\x00\xff', json.dumps('x'),
                        json.dumps({'aliases': []})):
            with self.subTest(content=content[:30]), TemporaryDirectory() as folder:
                path = Path(folder, 'settings.json')
                path.write_text(content, encoding='utf-8', errors='replace')
                with patch.object(main, 'SETTINGS_PATH', str(path)):
                    settings = main.load_settings()
                self.assertIn(settings['low_battery_threshold'], main.THRESHOLD_CHOICES)
                self.assertIn(settings['full_charge_level'], (80, 90, 100))
                self.assertIn(settings['theme'], ('system', 'dark', 'light'))
                self.assertIn(settings['time_alert_minutes'], main.TIME_ALERT_CHOICES)
                self.assertIsNone(settings['mini_widget_pos'])
                self.assertIs(type(settings['hotkey_enabled']), bool)
                self.assertTrue(all(isinstance(k, str) and isinstance(v, str) for k, v in settings['aliases'].items()))

    def test_hostile_history_lines_are_dropped(self):
        good = dict(t=time.time(), id='a', name='A', level=50, source='ble', charging=None)
        lines = [json.dumps(good), '{"t": NaN, "id": "a", "name": "A", "level": 5, "source": "ble"}',
                 json.dumps(dict(good, level=999)), json.dumps(dict(good, level=True)),
                 json.dumps(dict(good, id=['x'])), json.dumps([1]), 'not json', '\x00' * 100,
                 json.dumps(dict(good, t='now')), '{"t": Infinity, "id": "a", "name": "A", "level": 5, "source": "ble"}']
        with TemporaryDirectory() as folder:
            path = Path(folder, 'history.jsonl')
            path.write_text('\n'.join(lines), encoding='utf-8')
            history = battery_history.BatteryHistory(path)
        self.assertEqual([sample['level'] for sample in history.samples('a')], [50])


class TrayTextTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def test_tray_tooltip_never_exceeds_windows_limit(self):
        self.app.tray_icon = Mock()
        self.app._apply_headphones_update([{'id': 'x', 'name': 'A' * 400, 'battery_level': 50}])
        self.app._update_tray()
        self.assertLessEqual(len(self.app.tray_icon.title), 127)


if __name__ == '__main__':
    unittest.main()
