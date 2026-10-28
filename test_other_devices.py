import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from winrt.windows.foundation import PropertyValue as PV

import headphone_monitor as monitor
from ble_battery import address_from_device_id


def row(key, name, major, minor, address, appearance=None):
    values = {monitor.CONNECTED_KEY: PV.create_boolean(True),
              monitor.MAJOR_KEY: PV.create_uint16(major),
              monitor.MINOR_KEY: PV.create_uint16(minor),
              monitor.ADDRESS_KEY: PV.create_string(address)}
    if appearance is not None:
        values['System.Devices.Aep.Bluetooth.Le.Appearance'] = PV.create_uint16(appearance)
    return SimpleNamespace(id=key, name=name, properties=SimpleNamespace(lookup=values.get))


class OtherDeviceTests(unittest.IsolatedAsyncioTestCase):
    async def test_opt_in_discovers_peripherals_and_ble_by_address(self):
        classic = [row('mouse', 'Same', 5, 0x20, '00:11:22:33:44:55'),
                   row('keyboard', 'Keys', 5, 0x10, '00:11:22:33:44:66'),
                   row('pad', 'Pad', 5, 2, '00:11:22:33:44:77')]
        ble = [row('ble', 'BLE keys', 0, 0, '00:11:22:33:44:88', 0x3C1),
               row('duplicate', 'Same', 0, 0, '00:11:22:33:44:55', 0x3C2)]
        query = SimpleNamespace(find_all_async_aqs_filter_and_additional_properties=
                                AsyncMock(side_effect=[classic, ble]))
        with patch.object(monitor, 'DeviceInformation', query), patch.object(
                monitor, 'read_windows_batteries', AsyncMock(return_value={'001122334455': 50})):
            devices = await monitor.read_connected_devices(True)
        by_id = {d['id']: d for d in devices}
        self.assertEqual(set(by_id), {'mouse', 'keyboard', 'pad', 'ble'})
        self.assertEqual(by_id['mouse']['kind'], 'mouse')
        self.assertEqual(by_id['mouse']['battery_level'], 50)
        self.assertEqual(by_id['keyboard']['kind'], 'keyboard')
        self.assertEqual(by_id['pad']['kind'], 'gamepad')
        self.assertEqual(by_id['ble']['kind'], 'keyboard')

    async def test_connected_phone_is_included_as_other_device(self):
        classic = [row('iphone', 'iPhone', 2, 3, '00:11:22:33:44:99')]
        query = SimpleNamespace(find_all_async_aqs_filter_and_additional_properties=
                                AsyncMock(side_effect=[classic, []]))
        with patch.object(monitor, 'DeviceInformation', query), patch.object(
                monitor, 'read_windows_batteries', AsyncMock(return_value={})):
            devices = await monitor.read_connected_devices(True)
        self.assertEqual([(d['id'], d['kind']) for d in devices], [('iphone', 'phone')])
        query.find_all_async_aqs_filter_and_additional_properties.side_effect = [classic]
        with patch.object(monitor, 'DeviceInformation', query), patch.object(
                monitor, 'read_windows_batteries', AsyncMock(return_value={})):
            self.assertEqual(await monitor.read_connected_devices(False), [], 'Phones only when opted in')

    async def test_bthle_nodes_join_by_remote_address(self):
        node = SimpleNamespace(id=r'BTHLE\DEV_AABBCCDDEEFF\7&INSTANCE',
                               properties=SimpleNamespace(lookup=lambda key: PV.create_uint8(81)))
        query = SimpleNamespace(find_all_async_with_kind_aqs_filter_and_additional_properties=
                                AsyncMock(return_value=[node]))
        with patch.object(monitor, 'DeviceInformation', query):
            self.assertEqual(await monitor.read_windows_batteries(True), {'AABBCCDDEEFF': 81})
        self.assertIn('BTHLE', query.find_all_async_with_kind_aqs_filter_and_additional_properties.call_args.args[0])

    def test_ble_windows_id_gatt_target(self):
        self.assertEqual(address_from_device_id('BluetoothLE#BluetoothLE00:00:00:00:00:00-AA:BB:CC:DD:EE:FF'),
                         'AA:BB:CC:DD:EE:FF')

    def test_ble_non_game_hid_devices_are_not_reported_as_gamepads(self):
        self.assertEqual(monitor._ble_kind(0x3C4), 'gamepad')
        for appearance in (0x3C5, 0x3C9, 0x3CA, 0x944, None):
            self.assertEqual(monitor._ble_kind(appearance), 'other')

    async def test_setting_change_discards_inflight_other_device_results(self):
        updates = []
        watcher = monitor.HeadphoneConnectionMonitor(lambda *args: updates.append(args))
        watcher.set_include_other(True)
        async def changed_reader(include_other):
            watcher.set_include_other(False)
            return [{'id': 'mouse', 'name': 'Mouse', 'kind': 'mouse'}]
        with patch.object(monitor, 'read_connected_devices', changed_reader):
            await watcher.refresh_once()
        self.assertEqual(updates, [])
