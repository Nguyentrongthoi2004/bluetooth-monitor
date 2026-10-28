"""Hồi quy cho các lỗi tìm được khi rà soát sâu (luồng, tài nguyên, trạng thái sau lỗi)."""
import asyncio
import socket
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import apple_pods
import ble_battery
import headphone_monitor
import main
import test_app_lifecycle as lifecycle

A = {'id': 'a', 'name': 'Akko'}
Z = {'id': 'z', 'name': 'Zeta'}


class TransientDiscoveryErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_failures_keep_the_device_list(self):
        updates = []
        monitor = headphone_monitor.HeadphoneConnectionMonitor(lambda *args: updates.append(args))
        monitor._radios, monitor._radios_read_at = [], float('inf')
        results = [[A, Z], OSError('timeout'), OSError('timeout'), [A, Z]]

        async def reader():
            result = results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        with patch.object(headphone_monitor, 'read_connected_headphones', reader):
            for _ in range(4):
                await monitor.refresh_once()
        self.assertEqual(updates, [([A, Z], None)], 'Two blips publish nothing, devices stay connected')
        self.assertEqual(monitor._failures, 0)

    async def test_persistent_failure_is_reported(self):
        updates = []
        monitor = headphone_monitor.HeadphoneConnectionMonitor(lambda *args: updates.append(args))
        monitor._radios, monitor._radios_read_at = [], float('inf')
        with patch.object(headphone_monitor, 'read_connected_headphones', AsyncMock(return_value=[A])):
            await monitor.refresh_once()
        with patch.object(headphone_monitor, 'read_connected_headphones', AsyncMock(side_effect=OSError('x'))), \
                self.assertLogs(level='WARNING'):
            for _ in range(headphone_monitor.DISCOVERY_FAILURES_BEFORE_ERROR):
                await monitor.refresh_once()
        self.assertEqual(updates[-1], ([], 'Không đọc được kết nối Bluetooth'))

    async def test_missing_radio_is_looked_up_again_later(self):
        monitor = headphone_monitor.HeadphoneConnectionMonitor(lambda *args: None)
        radio = SimpleNamespace(kind=headphone_monitor.RadioKind.BLUETOOTH, add_state_changed=lambda handler: 1,
                                remove_state_changed=Mock(), state=headphone_monitor.RadioState.OFF)
        lookups = AsyncMock(side_effect=[OSError('adapter not ready'), [radio]])
        with patch.object(headphone_monitor.Radio, 'get_radios_async', lookups):
            await monitor._watch_radios()
            self.assertEqual(monitor._radios, [])
            await monitor._watch_radios()
            self.assertEqual(lookups.await_count, 1, 'Not re-read on every refresh')
            monitor._radios_read_at -= headphone_monitor.RADIO_RETRY_SECONDS + 1
            await monitor._watch_radios()
        self.assertTrue(monitor._radio_off(), 'Radio found on retry, and it is off')


class FakeWatcher:
    status = 1

    def __init__(self):
        self.stopped = False

    def __getattr__(self, name):
        return lambda *args: None  # add_added / add_updated / ...

    def start(self):
        pass

    def stop(self):
        self.stopped = True


class WatcherLeakTests(unittest.TestCase):
    def test_partial_watcher_setup_is_stopped(self):
        made = []

        def create(*args):
            made.append(FakeWatcher())
            return made[-1]
        monitor = headphone_monitor.HeadphoneConnectionMonitor(lambda *args: None)
        info = headphone_monitor.DeviceInformation
        with patch.object(info, 'create_watcher_aqs_filter_and_additional_properties', create), \
                patch.object(info, 'create_watcher_with_kind_aqs_filter_and_additional_properties',
                             Mock(side_effect=OSError('no battery watcher'))):
            with self.assertRaises(OSError):
                monitor._create_watchers()
        self.assertEqual(len(made), 2)
        self.assertTrue(all(watcher.stopped for watcher in made), 'Started watchers must not leak')
        self.assertEqual(monitor._watchers, [])


class FakeBleakClient:
    def __init__(self, address, timeout, disconnected_callback):
        char = SimpleNamespace(properties=['read', 'notify'])
        self.services = SimpleNamespace(get_characteristic=lambda uuid: char)

    async def __aenter__(self):
        await asyncio.sleep(.01)
        return self

    async def __aexit__(self, *args):
        return False

    async def read_gatt_char(self, char):
        return bytes([80])

    async def start_notify(self, char, callback):
        pass


class GattSlotTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_links_do_not_starve_other_devices(self):
        published = {}
        monitor = ble_battery.GattBatteryMonitor(lambda key, level: published.__setitem__(key, level))
        monitor.is_running = True
        monitor._links = asyncio.Semaphore(ble_battery.MAX_LINKS)
        ids = [f'mouse{i}' for i in range(ble_battery.MAX_LINKS)] + ['headphone']
        with patch.object(ble_battery, 'BleakClient', FakeBleakClient):
            tasks = [asyncio.create_task(monitor._session(key, '00:11:22:33:44:55')) for key in ids]
            await asyncio.sleep(.3)
            try:
                self.assertEqual(published.get('headphone'), 80,
                                 'Notify links held open by other devices must not block the headphone')
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        self.assertEqual(monitor._links._value, ble_battery.MAX_LINKS, 'Every slot is returned')

    async def test_failed_connection_returns_its_slot(self):
        monitor = ble_battery.GattBatteryMonitor(lambda *args: None)
        monitor._links = asyncio.Semaphore(1)

        class Broken(FakeBleakClient):
            async def __aenter__(self):
                raise OSError('unreachable')
        with patch.object(ble_battery, 'BleakClient', Broken):
            self.assertFalse(await monitor._session('x', '00:11:22:33:44:55'))
            self.assertFalse(await asyncio.wait_for(monitor._session('y', '00:11:22:33:44:55'), 1))


class AppleTimerTests(unittest.TestCase):
    def test_tick_from_previous_run_stops_itself(self):
        updates = []
        scanner = apple_pods.ApplePodsScanner(updates.append)
        scanner._watcher = object()  # "đang chạy" mà không cần Bluetooth thật
        old = scanner._generation
        scanner.stop()
        scanner._watcher = object()
        scanner._last = 'stale'
        with patch.object(scanner, '_schedule') as schedule:
            scanner._tick(old)
        schedule.assert_not_called()
        self.assertEqual(updates, [], 'A tick of the stopped run does nothing')
        scanner._watcher = None


class ExclusivePortTests(unittest.TestCase):
    def test_another_process_cannot_steal_the_port(self):
        reservation = socket.socket()
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
        reservation.close()
        with patch.object(main, 'IPC_PORT', port):
            server = main.try_single_instance()
        thief = socket.socket()
        try:
            thief.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with self.assertRaises(OSError):
                thief.bind(('127.0.0.1', port))
        finally:
            thief.close()
            server.close()


class FastPairAddressTests(unittest.TestCase):
    def load(self, value):
        import json
        from pathlib import Path
        from app_test_support import TemporaryDirectory
        with TemporaryDirectory() as folder:
            path = Path(folder, 'settings.json')
            path.write_text(json.dumps({'fast_pair_address': value}), encoding='utf-8')
            with patch.object(main, 'SETTINGS_PATH', str(path)):
                return main.load_settings()['fast_pair_address']

    def test_address_comes_from_settings_and_is_validated(self):
        self.assertEqual(self.load('AA:BB:CC:DD:EE:FF'), 'AA:BB:CC:DD:EE:FF')
        for bad in ('', 'AirPods', 'AA:BB:CC:DD:EE', 'AA-BB-CC-DD-EE-FF\n', 42, None):
            self.assertEqual(self.load(bad), main.DEFAULT_SETTINGS['fast_pair_address'], bad)


class ExtractionCleanupTests(unittest.TestCase):
    def make(self, root, name, ours=True, age=600):
        import os
        from pathlib import Path
        folder = Path(root, name)
        (folder / 'customtkinter').mkdir(parents=True)
        if ours:
            (folder / 'assets').mkdir()
            (folder / 'assets' / 'app_icon.ico').write_bytes(b'x')
        old = time.time() - age
        os.utime(folder, (old, old))
        return folder

    def test_only_old_unused_folders_of_this_app_are_removed(self):
        import os
        from app_test_support import TemporaryDirectory
        with TemporaryDirectory() as root:
            own = self.make(root, '_MEIown')
            stale = self.make(root, '_MEIstale')
            fresh = self.make(root, '_MEIfresh', age=10)        # phiên thứ hai đang giải nén
            foreign = self.make(root, '_MEIother', ours=False)  # app PyInstaller khác
            busy = self.make(root, '_MEIbusy')                  # phiên khác đang chạy
            real_rename = os.rename

            def rename(source, target):
                if os.path.basename(source) == '_MEIbusy':
                    raise PermissionError('in use')
                return real_rename(source, target)
            with patch.object(main.sys, 'frozen', True, create=True), \
                    patch.object(main.sys, '_MEIPASS', str(own), create=True), \
                    patch.object(main.os, 'rename', rename):
                self.assertEqual(main.cleanup_stale_extractions(), 1)
            self.assertFalse(stale.exists())
            for kept in (own, fresh, foreign, busy):
                self.assertTrue(kept.exists(), kept.name)
            self.assertEqual(sorted(os.listdir(root)), ['_MEIbusy', '_MEIfresh', '_MEIother', '_MEIown'])

    def test_folder_build_and_source_run_touch_nothing(self):
        with patch.object(main.sys, 'frozen', False, create=True):
            self.assertEqual(main.cleanup_stale_extractions(), 0)
        with patch.object(main.sys, 'frozen', True, create=True), \
                patch.object(main.sys, '_MEIPASS', r'D:\Apps\Bluetooth Monitor\_internal', create=True), \
                patch.object(main.os, 'listdir') as listdir:
            self.assertEqual(main.cleanup_stale_extractions(), 0)
        listdir.assert_not_called()


class AppStateTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def test_selection_survives_a_reported_scan_error(self):
        self.app._apply_headphones_update([dict(A), dict(Z)])
        self.app._select_headphone('a')
        self.app._apply_headphones_update([], 'Không đọc được kết nối Bluetooth')
        self.app._apply_headphones_update([dict(A), dict(Z)])
        self.assertEqual(self.app.active_headphone['id'], 'a', 'Recovery is not a new connection')

    def test_really_new_device_after_error_is_still_selected(self):
        self.app._apply_headphones_update([dict(A)])
        self.app._apply_headphones_update([], 'Không đọc được kết nối Bluetooth')
        self.app._apply_headphones_update([dict(A), dict(Z)])
        self.assertEqual(self.app.active_headphone['id'], 'z')

    def test_tray_checkmarks_follow_settings_and_audio(self):
        self.app.tray_icon = Mock()
        self.app._update_tray()
        rebuilds = self.app.tray_icon.update_menu.call_count
        self.app._set_setting('low_battery_alert', not self.app.settings['low_battery_alert'])
        self.assertEqual(self.app.tray_icon.update_menu.call_count, rebuilds + 1)
        self.app._set_setting('mini_widget', not self.app.settings['mini_widget'])
        self.assertEqual(self.app.tray_icon.update_menu.call_count, rebuilds + 2)
        with patch.object(main.audio_output, 'set_default_output', return_value=True):
            self.app._set_audio_output(('spk', 'Loa'))
        self.assertEqual(self.app.tray_icon.update_menu.call_count, rebuilds + 3)

    def test_opening_settings_cancels_nearby_refresh(self):
        scanner = Mock(running=False)
        scanner.snapshot.return_value = []
        with patch.object(main.bt_scan, 'NearbyScanner', return_value=scanner):
            self.app.show_window()
            self.app._show_nearby_panel()
            self.assertIsNotNone(self.app._nearby_timer)
            self.app._show_settings_dialog()
        self.assertIsNone(self.app._nearby_timer, 'No orphan refresh loop')
        self.assertIsNone(self.app._nearby_win)
        scanner.stop.assert_called()

    def test_low_battery_alert_covers_devices_not_on_screen(self):
        self.app._alert = Mock(return_value=True)
        self.app._apply_headphones_update([{'id': 'a', 'name': 'Akko', 'battery_level': 80},
                                           {'id': 'b', 'name': 'Sony', 'battery_level': 10},
                                           {'id': 'm', 'name': 'MX Master', 'battery_level': 5, 'kind': 'mouse'}])
        self.app._select_headphone('a')
        messages = {call.args[0]: call.args[1] for call in self.app._alert.call_args_list}
        self.assertEqual(messages, {'Sony còn 10% pin. Hãy sạc sớm.': 'Pin tai nghe yếu',
                                    'MX Master còn 5% pin. Hãy sạc sớm.': 'Pin thiết bị yếu'})
        self.app._history_tick()
        self.assertEqual(self.app._alert.call_count, 2, 'Each device is alerted once')
        self.app.after_cancel(self.app._history_timer)

    def test_charging_second_earbud_set_is_not_alerted(self):
        self.app._alert = Mock(return_value=True)
        self.app._apply_headphones_update([{'id': 'a', 'name': 'Akko', 'battery_level': 80},
                                           {'id': 'jbl', 'name': 'JBL'}])
        self.app._select_headphone('a')
        self.app._apply_components('jbl', 8, 60, 50, {'left': True, 'right': False, '_fresh': True})
        self.app._check_low_battery()
        self.app._alert.assert_not_called()  # the 8% earbud is charging, the other has 60%
        self.app._apply_components('jbl', 8, 9, 50, {'left': True, 'right': False, '_fresh': True})
        self.app._check_low_battery()
        self.assertIn('JBL còn 9% pin', self.app._alert.call_args.args[0])

    def test_time_alert_covers_devices_not_on_screen(self):
        self.app._alert = Mock(return_value=True)
        self.app._apply_headphones_update([{'id': 'a', 'name': 'Akko', 'battery_level': 80},
                                           {'id': 'b', 'name': 'Sony', 'battery_level': 30}])
        self.app._select_headphone('a')
        self.app._alert.reset_mock()
        self.app.settings['time_alert_minutes'] = 15
        with patch.object(main, 'estimate_remaining', side_effect=lambda samples: 10 if samples and
                          samples[-1]['id'] == 'b' else None):
            self.app._check_time_alert()
        self.assertEqual(self.app._alert.call_args.args[1], 'Sắp hết pin tai nghe')
        self.assertIn('Sony', self.app._alert.call_args.args[0])

    def test_no_ble_connection_attempts_to_headsets_with_split_battery(self):
        # Mỗi lần thử nối BLE tới JBL (không có dịch vụ pin BLE) chiếm sóng và làm nhạc giật.
        self.app.gatt_monitor = Mock()
        jbl = {'id': 'Bluetooth#Bluetooth0c:1d:2e:3f:40:bc-0a:1b:2c:3d:4e:3f', 'name': 'JBL'}
        akko = {'id': 'Bluetooth#Bluetooth0c:1d:2e:3f:40:bc-0b:1c:2d:3e:4f:05', 'name': 'Akko'}
        pods = {'id': 'Bluetooth#Bluetooth0c:1d:2e:3f:40:bc-11:22:33:44:55:66', 'name': 'AirPods Pro'}
        self.app.apple_scanner = Mock(running=False)
        self.app.battery_device_id = jbl['id']
        self.app._apply_headphones_update([jbl, akko, pods])
        targets = self.app.gatt_monitor.set_targets.call_args.args[0]
        self.assertEqual(set(targets), {akko['id']})
        self.app._unbind_fast_pair()
        self.assertIn(jbl['id'], self.app.gatt_monitor.set_targets.call_args.args[0])

    def test_fast_pair_scan_is_passive_and_filtered_in_windows(self):
        import inspect
        import jbl_core
        source = inspect.getsource(jbl_core.JblCore._main_worker)
        self.assertIn('scanning_mode="passive"', source)
        self.assertIn('AdvertisementFilter=fast_pair_filter()', source)
        pattern = jbl_core.fast_pair_filter().byte_patterns[0]
        self.assertEqual((pattern.data_type, pattern.offset, bytes(pattern.data)), (0x16, 0, b'\x2c\xfe'))

    def test_exit_does_not_wait_for_each_hung_worker_in_turn(self):
        slow = [Mock(stop=Mock(side_effect=lambda: time.sleep(1.5))) for _ in range(4)]
        self.app.hotkey_worker, self.app.connection_monitor, self.app.gatt_monitor, self.app.core = slow
        started = time.monotonic()
        with patch.object(self.app, 'destroy'), patch.object(main.sys, 'exit'):
            self.app._exit_app()
        self.assertLess(time.monotonic() - started, 3, 'Workers stop in parallel')
        for worker in slow:
            worker.stop.assert_called_once()


if __name__ == '__main__':
    unittest.main()
