import unittest
import unittest.mock

import ble_battery
import test_app_lifecycle as lifecycle


AKKO = {'id': 'Bluetooth#Bluetooth0c:1d:2e:3f:40:bc-0b:1c:2d:3e:4f:05',
        'name': 'Akko S9 Ultra_BT', 'battery_level': 100}


class GattParsingTests(unittest.TestCase):
    def test_remote_address_comes_from_windows_id(self):
        self.assertEqual(ble_battery.address_from_device_id(AKKO['id']), '0B:1C:2D:3E:4F:05')
        for bad in ('akko', '', None, 'Bluetooth#Bluetooth0c:1d:2e:3f:40:bc'):
            self.assertIsNone(ble_battery.address_from_device_id(bad))

    def test_battery_level_bytes(self):
        self.assertEqual(ble_battery.parse_battery_level(bytes([0x64])), 100)
        self.assertEqual(ble_battery.parse_battery_level(bytes([87])), 87)
        self.assertEqual(ble_battery.parse_battery_level(bytes([0])), 0)
        self.assertIsNone(ble_battery.parse_battery_level(bytes([101])))
        self.assertIsNone(ble_battery.parse_battery_level(b''))


class GattRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_unreachable_device_keeps_being_retried_slowly(self):
        monitor = ble_battery.GattBatteryMonitor(lambda *args: None)
        monitor.is_running = True
        delays = []

        async def fail(device_id, address, wake=None):
            return False

        async def record(wake, delay):
            delays.append(delay)
            if len(delays) == 5:
                monitor.is_running = False

        monitor._session = fail
        monitor._wait = record
        await monitor._watch('akko', '0B:1C:2D:3E:4F:05')
        self.assertEqual(delays, [20, 60, 180, 600, 600])


class GattUiTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def test_ble_level_overrides_windows_and_falls_back(self):
        self.app._apply_headphones_update([AKKO])
        self.assertEqual(self.app.lbl_device_battery.cget('text'), '100%')
        self.app._apply_gatt_update(AKKO['id'], 87)
        self.assertEqual(self.app.lbl_device_battery.cget('text'), '87%')
        self.assertIn('BLE', self.app.lbl_updated.cget('text'))
        self.assertIn('87% (BLE)', self.app.tray_icon.title)
        self.app._apply_gatt_update(AKKO['id'], None)
        self.assertEqual(self.app.lbl_device_battery.cget('text'), '100%')
        self.assertIn('Windows', self.app.lbl_updated.cget('text'))

    def test_ble_only_headphone_shows_battery(self):
        self.app._apply_headphones_update([dict(AKKO, battery_level=None)])
        self.assertIn('chưa có pin', self.app.lbl_status_dot.cget('text'))
        self.app._apply_gatt_update(AKKO['id'], 42)
        self.assertEqual(self.app.lbl_device_battery.cget('text'), '42%')
        self.assertNotIn('chưa có pin', self.app.lbl_status_dot.cget('text'))

    def test_level_for_disconnected_device_is_ignored_and_cleared(self):
        self.app._apply_gatt_update(AKKO['id'], 50)
        self.assertEqual(self.app.gatt_levels, {})
        self.app._apply_headphones_update([AKKO])
        self.app._apply_gatt_update(AKKO['id'], 50)
        self.app._apply_headphones_update([])
        self.assertEqual(self.app.gatt_levels, {})
        self.assertEqual(self.app.lbl_device_battery.cget('text'), '--%')

    def test_ble_level_belongs_to_its_own_headphone(self):
        sony = {'id': 'Bluetooth#Bluetooth0c:1d:2e:3f:40:bc-00:11:22:33:44:55',
                'name': 'WH-1000XM5'}
        self.app._apply_headphones_update([AKKO])
        self.app._apply_headphones_update([AKKO, sony])
        self.app._apply_gatt_update(AKKO['id'], 64)
        self.assertEqual(self.app.lbl_device_battery.cget('text'), '--%')
        self.app._select_headphone(AKKO['id'])
        self.assertEqual(self.app.lbl_device_battery.cget('text'), '64%')


if __name__ == '__main__':
    unittest.main()
