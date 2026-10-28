import asyncio
import importlib
import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from winrt.windows.foundation import PropertyValue

import main
import test_app_lifecycle as lifecycle


class ConnectedHeadphoneUiTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def apply_devices(self, devices, error=None):
        self.assertTrue(hasattr(self.app, '_apply_headphones_update'),
                        'App must handle real headphone connection changes')
        self.app._apply_headphones_update(devices, error)

    def test_startup_does_not_label_every_device_as_jbl(self):
        self.assertEqual(self.app.lbl_device_title.cget('text'),
                         'Chưa có tai nghe kết nối')

    def test_connected_headphone_name_appears_without_battery_data(self):
        self.apply_devices([{'id': 'akko', 'name': 'Akko S9 Ultra_BT'}])
        self.assertEqual(self.app.lbl_device_title.cget('text'), 'Akko S9 Ultra_BT')
        self.assertIn('Akko S9 Ultra_BT', self.app.tray_icon.title)
        self.assertTrue(self.app.is_connected)
        self.assertIsNone(self.app.left_level)

    def test_new_connection_replaces_name_then_falls_back_on_disconnect(self):
        akko = {'id': 'akko', 'name': 'Akko S9 Ultra_BT'}
        sony = {'id': 'sony', 'name': 'WH-1000XM5'}
        self.apply_devices([akko])
        self.apply_devices([akko, sony])
        self.assertEqual(self.app.lbl_device_title.cget('text'), 'WH-1000XM5')
        self.apply_devices([sony, akko])
        self.assertEqual(self.app.lbl_device_title.cget('text'), 'WH-1000XM5')
        self.apply_devices([akko])
        self.assertEqual(self.app.lbl_device_title.cget('text'), 'Akko S9 Ultra_BT')
        self.apply_devices([])
        # The last headphone stays visible, greyed out, as disconnected.
        self.assertEqual(self.app.lbl_device_title.cget('text'), 'Akko S9 Ultra_BT')
        self.assertEqual(self.app.lbl_status_dot.cget('text'), '○ Không kết nối')
        self.assertEqual(self.app.lbl_device_battery.cget('text'), '--%')
        self.assertFalse(self.app.is_connected)
        self.assertIsNone(self.app.active_headphone)

    def test_radio_off_shows_disconnected_with_last_level(self):
        sent = []
        self.app._notify = lambda message, title: sent.append(message) or True
        self.app.settings['connection_alert'] = True
        self.apply_devices([{'id': 'akko', 'name': 'Akko S9 Ultra_BT', 'battery_level': 90}])
        self.apply_devices([], main.RADIO_OFF)
        self.assertFalse(self.app.is_connected)
        self.assertIn('Bluetooth của máy đang tắt', self.app.lbl_status_dot.cget('text'))
        self.assertEqual(self.app.lbl_battery_state.cget('text'), 'Không kết nối')
        self.assertIn('90%', self.app.lbl_battery_source.cget('text'))
        self.app.after_cancel(self.app._announce_timer)
        self.app._announce_connection_changes()
        self.assertEqual(sent, ['Đã ngắt Akko S9 Ultra_BT'])

    def test_jbl_battery_cannot_be_shown_under_another_headphone_name(self):
        self.apply_devices([{'id': 'jbl', 'name': 'JBL CLUB PRO+ TWS'}])
        self.app._apply_battery_update(94, 67, 100, {'_fresh': True})
        self.assertEqual(self.app.left_level, 94)
        self.apply_devices([{'id': 'akko', 'name': 'Akko S9 Ultra_BT'}])
        self.app._apply_battery_update(94, 67, 100, {'_fresh': True})
        self.assertIsNone(self.app.left_level)
        self.assertIsNone(self.app.right_level)
        self.assertIsNone(self.app.case_level)

    def test_missing_battery_packet_does_not_erase_connection_name(self):
        self.apply_devices([{'id': 'akko', 'name': 'Akko S9 Ultra_BT'}])
        self.app._apply_status_update(False, 'Đang tìm dữ liệu tai nghe...')
        self.assertEqual(self.app.lbl_device_title.cget('text'), 'Akko S9 Ultra_BT')
        self.assertTrue(self.app.is_connected)
        self.assertIn('Đã kết nối', self.app.lbl_status_dot.cget('text'))

    def test_another_headphone_with_same_name_cannot_receive_target_battery(self):
        self.apply_devices([{'id': 'other-jbl', 'name': 'JBL CLUB PRO+ TWS'}])
        self.app._apply_battery_update(94, 67, 100, {'_fresh': True})
        self.assertIsNone(self.app.left_level)

    def test_renaming_known_device_preserves_battery_ownership(self):
        self.apply_devices([{'id': 'jbl', 'name': 'JBL CLUB PRO+ TWS'}])
        self.app._apply_battery_update(94, 67, 100, {'_fresh': True})
        self.apply_devices([{'id': 'jbl', 'name': 'Tai nghe của tôi'}])
        self.assertEqual(self.app.lbl_device_title.cget('text'), 'Tai nghe của tôi')
        self.app._apply_battery_update(93, 66, 100, {'_fresh': True})
        self.assertEqual(self.app.left_level, 93)

    def test_query_error_removes_stale_device_and_battery(self):
        self.apply_devices([{'id': 'jbl', 'name': 'JBL CLUB PRO+ TWS'}])
        self.app._apply_battery_update(94, 67, 100, {'_fresh': True})
        self.apply_devices([], 'Không đọc được kết nối Bluetooth')
        self.assertFalse(self.app.is_connected)
        self.assertIsNone(self.app.left_level)
        self.assertNotIn('JBL', self.app.lbl_device_title.cget('text'))

    def test_connection_callback_can_run_on_worker_without_touching_tk(self):
        self.assertTrue(hasattr(self.app, '_on_headphones_update'))
        import threading
        errors = []
        def update():
            try:
                self.app._on_headphones_update([{'id': 'akko', 'name': 'Akko S9 Ultra_BT'}])
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=update)
        worker.start()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.app._drain_ui_queue()
        self.assertEqual(self.app.lbl_device_title.cget('text'), 'Akko S9 Ultra_BT')


class HeadphoneDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def monitor_module(self):
        self.assertIsNotNone(importlib.util.find_spec('headphone_monitor'),
                             'Windows headphone connection reader must exist')
        return importlib.import_module('headphone_monitor')

    async def test_only_connected_headphones_are_reported(self):
        monitor = self.monitor_module()
        def row(key, name, connected, major, minor):
            values = {
                'System.Devices.Aep.IsConnected': PropertyValue.create_boolean(connected),
                'System.Devices.Aep.Bluetooth.Cod.Major': PropertyValue.create_uint16(major),
                'System.Devices.Aep.Bluetooth.Cod.Minor': PropertyValue.create_uint16(minor),
            }
            return SimpleNamespace(id=key, name=name,
                                   properties=SimpleNamespace(lookup=values.get))
        rows = [row('akko', 'Akko S9 Ultra_BT', True, 4, 1),
                row('jbl', 'JBL CLUB PRO+ TWS', False, 4, 1),
                row('mouse', 'Mouse', True, 5, 2),
                row('speaker', 'Speaker', True, 4, 5),
                row('sony', 'WH-1000XM5', True, 4, 6)]
        fake_query = SimpleNamespace(
            find_all_async_aqs_filter_and_additional_properties=AsyncMock(return_value=rows))
        with patch.object(monitor, 'DeviceInformation', fake_query):
            result = await monitor.read_connected_headphones()
        self.assertEqual(result, [{'id': 'akko', 'name': 'Akko S9 Ultra_BT'},
                                  {'id': 'sony', 'name': 'WH-1000XM5'}])

    async def test_reader_failure_clears_previous_connection_snapshot(self):
        monitor = self.monitor_module()
        updates = []
        async def reader():
            raise OSError('Bluetooth access failed')
        watcher = monitor.HeadphoneConnectionMonitor(
            on_update=lambda devices, error=None: updates.append((devices, error)))
        with patch.object(monitor, 'read_connected_headphones', reader):
            await watcher.refresh_once()
        self.assertEqual(updates[0][0], [])
        self.assertTrue(updates[0][1])


if __name__ == '__main__':
    unittest.main()
