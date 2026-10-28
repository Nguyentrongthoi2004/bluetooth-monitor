"""Pin trái/phải/hộp cho tai nghe true wireless khác JBL (Fast Pair, ui_split.py, fast_pair.py)."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import fast_pair
import main
import test_app_lifecycle as lifecycle
import ui_split
from app_test_support import TemporaryDirectory

SONY = {'id': 'Bluetooth#Bluetooth0c:1d:2e:3f:40:bc-aa:bb:cc:dd:ee:01', 'name': 'WF-1000XM5', 'battery_level': 70}
PIXEL = {'id': 'Bluetooth#Bluetooth0c:1d:2e:3f:40:bc-aa:bb:cc:dd:ee:02', 'name': 'Pixel Buds Pro', 'battery_level': 60}
SONY_FP, PIXEL_FP = '11:22:33:44:55:66', '66:55:44:33:22:11'
SONY_BATTERY = ((70, False), (65, True), (40, False))
PIXEL_BATTERY = ((90, False), (88, False), (None, None))


class FastPairPacketTests(unittest.TestCase):
    def section(self, data_type, data):
        return SimpleNamespace(data_type=data_type, data=bytes(data))

    def test_battery_payload_is_found_after_the_fast_pair_uuid(self):
        payload = bytes([0x00, 0x33, 70, 0x80 | 65, 40])
        sections = [self.section(0x01, [0x06]), self.section(0x16, [0x0F, 0x18, 1]),
                    self.section(0x16, [0x2C, 0xFE, *payload])]
        self.assertEqual(fast_pair.battery_payload(sections), payload)
        self.assertEqual(fast_pair.parse_fast_pair_battery(payload), SONY_BATTERY)
        self.assertIsNone(fast_pair.battery_payload([self.section(0x16, [0x0F, 0x18, 1])]))

    def test_address_formatting(self):
        self.assertEqual(fast_pair.format_address(0xC0FFEE0000CC), 'C0:FF:EE:00:00:CC')
        self.assertEqual(fast_pair.format_address(0x1), '00:00:00:00:00:01')

    def test_over_ear_single_battery_is_not_a_candidate(self):
        # Tai nghe chụp tai chỉ phát một mức pin: không có trái/phải/hộp.
        self.assertIsNone(fast_pair.parse_fast_pair_battery(bytes([0x00, 0x13, 80])))

    def test_received_packets_candidates_and_staleness(self):
        scanner = fast_pair.FastPairScanner(lambda *args: None)
        advertisement = SimpleNamespace(data_sections=[self.section(0x16, [0x2C, 0xFE, 0x00, 0x33, 70, 0x80 | 65, 40])])
        with patch.object(fast_pair.time, 'monotonic', return_value=100.0):
            scanner._on_received(None, SimpleNamespace(bluetooth_address=0x112233445566, raw_signal_strength_in_dbm=-50,
                                                       advertisement=advertisement))
            scanner._on_received(None, SimpleNamespace(bluetooth_address=0x665544332211, raw_signal_strength_in_dbm=-80,
                                                       advertisement=SimpleNamespace(data_sections=[])))
        self.assertEqual(scanner.candidates(now=105.0), [(SONY_FP, -50, SONY_BATTERY)])
        self.assertEqual(scanner.latest(SONY_FP, now=125.0), SONY_BATTERY)
        self.assertEqual(scanner.candidates(now=125.0), [], 'Old packets are not offered for linking')
        self.assertIsNone(scanner.latest(SONY_FP, now=200.0))

    def test_hostile_packets_never_raise(self):
        scanner = fast_pair.FastPairScanner(lambda *args: None)
        for data in ([0x2C, 0xFE], [0x2C, 0xFE, 0x00, 0x33, 250, 1, 1], [0x2C, 0xFE, 0xFF] * 20):
            scanner._on_received(None, SimpleNamespace(bluetooth_address=1, raw_signal_strength_in_dbm=-40,
                                                       advertisement=SimpleNamespace(data_sections=[self.section(0x16, data)])))
        scanner._on_received(None, object())
        self.assertEqual(scanner.candidates(), [])

    def test_tick_publishes_only_changes_for_watched_headsets(self):
        updates = []
        scanner = fast_pair.FastPairScanner(lambda address, value: updates.append((address, value)))
        scanner.watched = {SONY_FP}
        scanner._seen = {SONY_FP: (fast_pair.time.monotonic(), -50, SONY_BATTERY),
                         PIXEL_FP: (fast_pair.time.monotonic(), -50, PIXEL_BATTERY)}
        scanner._watcher = object()
        with patch.object(scanner, '_schedule'):
            scanner._tick(scanner._generation)
            scanner._tick(scanner._generation)
            scanner._tick(scanner._generation - 1)  # tick của lần chạy trước
        self.assertEqual(updates, [(SONY_FP, SONY_BATTERY)])
        scanner._watcher = None


class ScannerReviewFixTests(unittest.TestCase):
    def test_reconnected_headset_is_published_again_with_same_numbers(self):
        updates = []
        scanner = fast_pair.FastPairScanner(lambda address, value: updates.append(address))
        scanner._seen = {SONY_FP: (fast_pair.time.monotonic(), -50, SONY_BATTERY)}
        scanner._watcher = object()
        with patch.object(scanner, '_schedule'):
            scanner.set_watched({SONY_FP})
            scanner._tick(scanner._generation)
            scanner.set_watched(set())       # ngắt kết nối
            scanner.set_watched({SONY_FP})   # kết nối lại, số pin y như cũ
            scanner._tick(scanner._generation)
        self.assertEqual(updates, [SONY_FP, SONY_FP])
        scanner._watcher = None

    def test_old_addresses_are_pruned(self):
        scanner = fast_pair.FastPairScanner(lambda *args: None)
        now = fast_pair.time.monotonic()
        scanner._seen = {SONY_FP: (now - fast_pair.STALE_SECONDS - 1, -50, SONY_BATTERY),
                         PIXEL_FP: (now, -50, PIXEL_BATTERY)}
        scanner._prune(now)
        self.assertEqual(set(scanner._seen), {PIXEL_FP})

    def test_tick_from_a_previous_run_cannot_start_a_second_timer_chain(self):
        scanner = fast_pair.FastPairScanner(lambda *args: None)
        scanner._watcher = object()
        old = scanner._generation
        scanner._generation += 1  # stop() rồi start() trong lúc tick cũ đang chạy
        with patch.object(fast_pair.threading, 'Timer') as timer:
            scanner._schedule(old)
        timer.assert_not_called()
        scanner._watcher = None


class SplitBatteryUiTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def choose(self, device, address):
        self.app._select_headphone(device['id'])
        self.app._bind_fast_pair_to_active()
        row = next(row for row in self.app._split_picker.rows if row.address == address)
        row.btn_choose.invoke()

    def end_discovery(self):
        self.app._split_discovery_until = 0
        self.app._check_split_discovery()

    def test_any_true_wireless_headset_gets_left_right_case(self):
        scanner = self.app.fast_pair_scanner
        scanner.items = [(SONY_FP, -48, SONY_BATTERY), (PIXEL_FP, -75, PIXEL_BATTERY)]
        scanner.values = {SONY_FP: SONY_BATTERY}
        self.app._apply_headphones_update([dict(SONY)])
        self.assertTrue(scanner.running, 'Listens briefly when a headset connects')
        self.assertFalse(self.app.btn_split.winfo_manager(), 'No button before anything is heard')
        self.app._check_split_discovery()
        self.assertTrue(self.app.btn_split.winfo_manager(), 'Shortcut once a true wireless headset is heard')
        self.app.btn_split.invoke()
        self.assertTrue(scanner.running, 'Scans while the list is open')
        texts = [row.winfo_children()[0].winfo_children()[0].cget('text') for row in self.app._split_picker.rows]
        self.assertEqual(texts[0], 'Trái 70% · Phải ⚡65% · Hộp 40%', 'Numbers to compare with the real headset')
        next(row for row in self.app._split_picker.rows if row.address == SONY_FP).btn_choose.invoke()
        self.assertEqual(self.app.settings['fast_pair_bindings'], {SONY['id']: SONY_FP})
        self.assertEqual((self.app.left_level, self.app.right_level, self.app.case_level), (70, 65, 40))
        self.assertTrue(self.app.row_left.winfo_manager())
        self.assertIn('Fast Pair', self.app.lbl_status_dot.cget('text'))
        self.assertFalse(self.app.btn_split.winfo_manager())
        self.assertEqual(scanner.watched, {SONY_FP})
        self.assertTrue(scanner.running, 'Keeps scanning for the bound headset')

    def test_two_headsets_each_get_their_own_numbers(self):
        self.app._apply_headphones_update([dict(SONY), dict(PIXEL)])
        self.app.fast_pair_scanner.items = [(SONY_FP, -48, SONY_BATTERY), (PIXEL_FP, -50, PIXEL_BATTERY)]
        self.choose(SONY, SONY_FP)
        self.app._bind_fast_pair_to_active()
        self.app._close_split_picker()
        self.app._select_headphone(PIXEL['id'])
        self.app._bind_fast_pair_to_active()
        self.assertEqual([row.address for row in self.app._split_picker.rows], [PIXEL_FP],
                         'An address used by another connected headset is not offered')
        self.app._split_picker.rows[0].btn_choose.invoke()
        self.app._apply_fast_pair_update(SONY_FP, ((50, False), (51, False), (30, False)))
        self.app._apply_fast_pair_update(PIXEL_FP, ((20, False), (22, False), (None, None)))
        self.assertEqual(self.app._components[SONY['id']][:3], (50, 51, 30))
        self.assertEqual(self.app._components[PIXEL['id']][:3], (20, 22, None))
        self.assertEqual((self.app.left_level, self.app.right_level), (20, 22), 'Screen shows the selected one')

    def test_lost_packets_fall_back_to_windows_battery(self):
        self.app._apply_headphones_update([dict(SONY)])
        self.app.fast_pair_scanner.items = [(SONY_FP, -48, SONY_BATTERY)]
        self.app.fast_pair_scanner.values = {SONY_FP: SONY_BATTERY}
        self.choose(SONY, SONY_FP)
        self.app._apply_fast_pair_update(SONY_FP, None)
        self.assertFalse(self.app._showing_components())
        self.assertEqual(self.app._display_level(), (70, False), 'Windows battery again')

    def test_packets_for_unbound_or_disconnected_headsets_are_ignored(self):
        self.app._apply_headphones_update([dict(SONY)])
        self.app._apply_fast_pair_update(SONY_FP, SONY_BATTERY)
        self.assertNotIn(SONY['id'], self.app._components, 'Not linked: never guessed')
        self.app.settings['fast_pair_bindings'] = {PIXEL['id']: PIXEL_FP}
        self.app._apply_fast_pair_update(PIXEL_FP, PIXEL_BATTERY)
        self.assertNotIn(PIXEL['id'], self.app._components, 'Linked but not connected')

    def test_unbinding_one_headset_keeps_the_jbl(self):
        self.app._apply_headphones_update([dict(SONY), {'id': 'jbl', 'name': 'JBL'}])
        self.app.fast_pair_scanner.items = [(SONY_FP, -48, SONY_BATTERY)]
        self.choose(SONY, SONY_FP)
        self.app._unbind_fast_pair()
        self.assertEqual(self.app.settings['fast_pair_bindings'], {})
        self.assertEqual(self.app.battery_device_id, 'jbl')
        self.end_discovery()
        self.assertFalse(self.app.fast_pair_scanner.running)
        self.assertTrue(self.app.btn_split.winfo_manager(), 'Its headset is still heard nearby')

    def test_repaired_headset_can_take_its_address_back(self):
        old_id = 'Bluetooth#Bluetooth0c:1d:2e:3f:40:bc-aa:bb:cc:dd:ee:99'
        self.app.settings['fast_pair_bindings'] = {old_id: SONY_FP}
        self.app._apply_headphones_update([dict(SONY)])
        self.app.fast_pair_scanner.items = [(SONY_FP, -48, SONY_BATTERY)]
        self.choose(SONY, SONY_FP)
        self.assertEqual(self.app.settings['fast_pair_bindings'], {SONY['id']: SONY_FP})

    def test_empty_list_explains_after_a_while(self):
        self.app._apply_headphones_update([{'id': 'akko', 'name': 'Akko (chụp tai)'}])
        self.app._bind_fast_pair_to_active()
        picker = self.app._split_picker
        self.assertEqual(picker.lbl_empty.cget('text'), 'Đang tìm…')
        picker.opened -= ui_split.PICKER_EMPTY_SECONDS + 1
        self.app.after_cancel(picker.timer)
        self.app._refresh_split_picker()
        self.assertIn('Tai nghe chụp tai', picker.lbl_empty.cget('text'))
        self.app._close_split_picker()
        self.end_discovery()
        self.assertFalse(self.app.fast_pair_scanner.running, 'Closing the list stops scanning')

    def test_over_ear_headset_never_gets_the_button(self):
        # Tai nghe chụp tai không phát pin trái/phải/hộp: nghe thử 30 giây không thấy gì -> không nút, tắt quét.
        self.app._apply_headphones_update([{'id': 'akko', 'name': 'Akko S9 Ultra_BT'}])
        self.app._check_split_discovery()
        self.end_discovery()
        self.assertFalse(self.app.btn_split.winfo_manager())
        self.assertFalse(self.app.fast_pair_scanner.running)

    def test_bound_jbl_in_its_case_does_not_put_the_button_on_another_headset(self):
        self.app.core.target_address = 'C0:FF:EE:00:00:CC'
        self.app.fast_pair_scanner.items = [('C0:FF:EE:00:00:CC', -50, SONY_BATTERY)]
        self.app._apply_headphones_update([{'id': 'akko', 'name': 'Akko S9 Ultra_BT'}])  # 'jbl' không kết nối
        self.app._check_split_discovery()
        self.assertFalse(self.app.btn_split.winfo_manager())
        self.app._bind_fast_pair_to_active()
        self.assertEqual(len(self.app._split_picker.rows), 1, 'Still choosable by hand (re-paired JBL)')

    def test_binding_the_jbl_keeps_other_headsets_numbers(self):
        self.app._apply_headphones_update([dict(SONY), {'id': 'jbl', 'name': 'JBL'}, {'id': 'jbl2', 'name': 'JBL'}])
        self.app.fast_pair_scanner.items = [(SONY_FP, -48, SONY_BATTERY)]
        self.app.fast_pair_scanner.values = {SONY_FP: SONY_BATTERY}
        self.choose(SONY, SONY_FP)
        self.app._confirm_bind('jbl2')
        self.assertEqual(self.app._components[SONY['id']][:3], (70, 65, 40))
        self.app._unbind_fast_pair()  # đang xem Sony: chỉ gỡ Sony
        self.app._select_headphone('jbl2')
        self.app._unbind_fast_pair()
        self.assertIsNone(self.app.battery_device_id)

    def test_headset_that_lost_its_numbers_offers_to_pick_again(self):
        # Hãng đổi địa chỉ quảng bá: liên kết cũ không còn gói nào -> lối tắt hiện lại để chọn địa chỉ mới.
        self.app._apply_headphones_update([dict(SONY)])
        self.app.fast_pair_scanner.items = [(SONY_FP, -48, SONY_BATTERY)]
        self.app.fast_pair_scanner.values = {SONY_FP: SONY_BATTERY}
        self.choose(SONY, SONY_FP)
        self.assertFalse(self.app.btn_split.winfo_manager())
        self.app._apply_fast_pair_update(SONY_FP, None)
        self.assertTrue(self.app.btn_split.winfo_manager())
        self.app.fast_pair_scanner.items = [('77:77:77:77:77:77', -45, SONY_BATTERY)]
        self.choose(SONY, '77:77:77:77:77:77')
        self.assertEqual(self.app.settings['fast_pair_bindings'], {SONY['id']: '77:77:77:77:77:77'})

    def test_bound_split_headset_is_not_probed_over_ble(self):
        self.app.gatt_monitor = Mock()
        self.app._apply_headphones_update([dict(SONY)])
        self.assertIn(SONY['id'], self.app.gatt_monitor.set_targets.call_args.args[0])
        self.app.fast_pair_scanner.items = [(SONY_FP, -48, SONY_BATTERY)]
        self.choose(SONY, SONY_FP)
        self.assertNotIn(SONY['id'], self.app.gatt_monitor.set_targets.call_args.args[0])


class BindingSettingsTests(unittest.TestCase):
    def test_bindings_are_validated(self):
        with TemporaryDirectory() as folder:
            path = Path(folder, 'settings.json')
            path.write_text(json.dumps({'fast_pair_bindings': {
                'ok': '11:22:33:44:55:66', 'lower': 'aa:bb:cc:dd:ee:ff', 'bad': 'AirPods', '': '11:22:33:44:55:66',
                'num': 5}}), encoding='utf-8')
            with patch.object(main, 'SETTINGS_PATH', str(path)):
                bindings = main.load_settings()['fast_pair_bindings']
            path.write_text(json.dumps({'fast_pair_bindings': ['x']}), encoding='utf-8')
            with patch.object(main, 'SETTINGS_PATH', str(path)):
                self.assertEqual(main.load_settings()['fast_pair_bindings'], {})
        self.assertEqual(bindings, {'ok': '11:22:33:44:55:66', 'lower': 'AA:BB:CC:DD:EE:FF'})

    def test_one_address_one_headset_and_jbl_not_duplicated(self):
        with TemporaryDirectory() as folder:
            path = Path(folder, 'settings.json')
            path.write_text(json.dumps({'jbl_battery_device_id': 'jbl', 'fast_pair_bindings': {
                'old': '11:22:33:44:55:66', 'new': '11:22:33:44:55:66', 'jbl': '22:22:22:22:22:22'}}),
                encoding='utf-8')
            with patch.object(main, 'SETTINGS_PATH', str(path)):
                self.assertEqual(main.load_settings()['fast_pair_bindings'], {'new': '11:22:33:44:55:66'})


if __name__ == '__main__':
    unittest.main()
