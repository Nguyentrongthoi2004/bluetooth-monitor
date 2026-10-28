import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from winrt.windows.foundation import PropertyValue

import headphone_monitor
import test_app_lifecycle as lifecycle


class WindowsBatteryUiTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def select(self, level=100, key='akko', name='Akko S9 Ultra_BT'):
        self.app._apply_headphones_update([{'id': key, 'name': name,
                                           'battery_level': level}])

    def test_windows_battery_appears_in_tray_without_jbl_packets(self):
        self.select()
        self.assertIn('100%', self.app.tray_icon.title)
        self.assertIn('Windows', self.app.tray_icon.title)
        self.assertIsNone(self.app.left_level)

    def test_zero_percent_is_displayed_as_real_battery(self):
        self.select(0)
        self.assertTrue(hasattr(self.app, 'lbl_device_battery'))
        self.assertEqual(self.app.lbl_device_battery.cget('text'), '0%')
        self.assertEqual(self.app.bar_device.get(), 0)

    def test_battery_changes_for_same_headphone_update_the_bar(self):
        self.select(73)
        self.select(64)
        self.assertTrue(hasattr(self.app, 'lbl_device_battery'))
        self.assertEqual(self.app.lbl_device_battery.cget('text'), '64%')
        self.assertAlmostEqual(self.app.bar_device.get(), .64)

    def test_disconnect_clears_windows_battery(self):
        self.select()
        self.app._apply_headphones_update([])
        self.assertTrue(hasattr(self.app, 'windows_battery_level'))
        self.assertIsNone(self.app.windows_battery_level)
        self.assertEqual(self.app.lbl_device_battery.cget('text'), '--%')

    def test_lost_windows_battery_does_not_leave_old_percentage(self):
        self.select(73)
        self.app._apply_headphones_update([{'id': 'akko', 'name': 'Akko S9 Ultra_BT'}])
        self.assertNotIn('73%', self.app.tray_icon.title)
        self.assertTrue(hasattr(self.app, 'windows_battery_level'))
        self.assertIsNone(self.app.windows_battery_level)

    def test_general_battery_is_not_copied_to_left_right_and_case(self):
        self.select(73)
        self.assertEqual((self.app.left_level, self.app.right_level, self.app.case_level),
                         (None, None, None))
        self.app.deiconify()
        self.app.update_idletasks()
        self.assertTrue(hasattr(self.app, 'row_device'))
        self.assertTrue(self.app.row_device.winfo_manager())
        self.assertFalse(self.app.row_left.winfo_manager())
        self.assertFalse(self.app.row_right.winfo_manager())
        self.assertFalse(self.app.row_case.winfo_manager())

    def test_fresh_jbl_components_take_priority_then_fall_back_to_windows(self):
        self.select(80, key='jbl', name='JBL CLUB PRO+ TWS')
        self.app._apply_battery_update(94, 67, 100, {'_fresh': True})
        self.assertEqual(self.app.left_level, 94)
        self.assertTrue(self.app.row_left.winfo_manager())
        self.app._apply_battery_update(None, None, None, {'_fresh': False})
        self.assertTrue(hasattr(self.app, 'lbl_device_battery'))
        self.assertEqual(self.app.lbl_device_battery.cget('text'), '80%')
        self.assertIn('Windows', self.app.lbl_updated.cget('text'))


class WindowsBatteryReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_battery_cache_expires_and_membership_or_refresh_bypasses_it(self):
        values = {
            headphone_monitor.CONNECTED_KEY: PropertyValue.create_boolean(True),
            headphone_monitor.MAJOR_KEY: PropertyValue.create_uint16(4),
            headphone_monitor.MINOR_KEY: PropertyValue.create_uint16(1),
            headphone_monitor.ADDRESS_KEY: PropertyValue.create_string('00:11:22:33:44:55'),
        }
        device = SimpleNamespace(id='a', name='Same', properties=SimpleNamespace(lookup=values.get))
        query = SimpleNamespace(find_all_async_aqs_filter_and_additional_properties=AsyncMock(return_value=[device]))
        batteries = AsyncMock(side_effect=[{'001122334455': n} for n in (80, 70, 60, 50)])
        updates = []
        watcher = headphone_monitor.HeadphoneConnectionMonitor(lambda devices, error: updates.append(devices))
        with patch.object(headphone_monitor, 'DeviceInformation', query), patch.object(
                headphone_monitor, 'read_windows_batteries', batteries), patch.object(
                headphone_monitor.time, 'monotonic', return_value=100) as clock:
            await watcher.refresh_once()
            device.name = 'Renamed'
            clock.return_value = 119
            await watcher.refresh_once()
            self.assertEqual(updates[-1][0]['battery_level'], 80)
            clock.return_value = 120
            await watcher.refresh_once()
            self.assertEqual(updates[-1][0]['battery_level'], 70)
            device.id = 'b'
            await watcher.refresh_once()
            self.assertEqual(updates[-1][0]['battery_level'], 60)
            watcher.trigger_refresh()
            await watcher.refresh_once()
            self.assertEqual(updates[-1][0]['battery_level'], 50)
        self.assertEqual(batteries.await_count, 4)

    async def test_slow_battery_query_keeps_connected_headphone_visible(self):
        values = {
            'System.Devices.Aep.IsConnected': PropertyValue.create_boolean(True),
            'System.Devices.Aep.Bluetooth.Cod.Major': PropertyValue.create_uint16(4),
            'System.Devices.Aep.Bluetooth.Cod.Minor': PropertyValue.create_uint16(1),
            'System.Devices.Aep.DeviceAddress': PropertyValue.create_string('00:11:22:33:44:55'),
        }
        device = SimpleNamespace(id='akko', name='Akko',
                                 properties=SimpleNamespace(lookup=values.get))
        async def stalled_query():
            await asyncio.Future()
        query = SimpleNamespace(find_all_async_aqs_filter_and_additional_properties=
                                AsyncMock(return_value=[device]))
        updates = []
        watcher = headphone_monitor.HeadphoneConnectionMonitor(
            on_update=lambda devices, error=None: updates.append((devices, error)))
        with patch.object(headphone_monitor, 'DeviceInformation', query), \
                patch.object(headphone_monitor, 'read_windows_batteries', stalled_query), \
                patch.object(headphone_monitor, 'BATTERY_QUERY_TIMEOUT', .01, create=True):
            await watcher.refresh_once()
        self.assertEqual(updates, [([{'id': 'akko', 'name': 'Akko'}], None)])

    async def test_windows_values_are_matched_by_address_not_device_name(self):
        self.assertTrue(hasattr(headphone_monitor, 'read_windows_batteries'))
        def row(address, raw):
            return SimpleNamespace(id='BTHENUM\\SERVICE\\7&TEST&2&' + address + '_C00000000',
                                   name='Same name',
                                   properties=SimpleNamespace(lookup=lambda key: raw))
        rows = [row('001122334455', PropertyValue.create_uint8(0)),
                row('AABBCCDDEEFF', PropertyValue.create_uint32(73)),
                row('112233445566', PropertyValue.create_uint8(255))]
        query = SimpleNamespace(find_all_async_with_kind_aqs_filter_and_additional_properties=
                                AsyncMock(return_value=rows))
        with patch.object(headphone_monitor, 'DeviceInformation', query):
            result = await headphone_monitor.read_windows_batteries()
        self.assertEqual(result, {'001122334455': 0, 'AABBCCDDEEFF': 73})

    async def test_conflicting_nodes_do_not_invent_one_battery_level(self):
        self.assertTrue(hasattr(headphone_monitor, 'read_windows_batteries'))
        rows = [SimpleNamespace(id='BTHENUM\\SERVICE\\7&TEST&2&001122334455_C00000000',
                                properties=SimpleNamespace(lookup=lambda key, level=level:
                                    PropertyValue.create_uint8(level))) for level in (20, 80)]
        query = SimpleNamespace(find_all_async_with_kind_aqs_filter_and_additional_properties=
                                AsyncMock(return_value=rows))
        with patch.object(headphone_monitor, 'DeviceInformation', query):
            result = await headphone_monitor.read_windows_batteries()
        self.assertNotIn('001122334455', result)

    async def test_monitor_publishes_level_changes_without_reconnection(self):
        updates = []
        watcher = headphone_monitor.HeadphoneConnectionMonitor(
            on_update=lambda devices, error=None: updates.append(devices))
        snapshots = [[{'id': 'akko', 'name': 'Akko', 'battery_level': 73}],
                     [{'id': 'akko', 'name': 'Akko', 'battery_level': 64}]]
        with patch.object(headphone_monitor, 'read_connected_headphones',
                          AsyncMock(side_effect=snapshots)):
            await watcher.refresh_once()
            await watcher.refresh_once()
        self.assertEqual([devices[0]['battery_level'] for devices in updates], [73, 64])


if __name__ == '__main__':
    unittest.main()
