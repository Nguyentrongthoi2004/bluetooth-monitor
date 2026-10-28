import unittest
import unittest.mock
from unittest.mock import patch

import main
import test_app_lifecycle as lifecycle


def device(name, paired=False, connected=False, signal=-70, le=False, kind='headphone', id_=None):
    return {'id': id_ or name, 'name': name, 'address': None, 'le': le, 'paired': paired,
            'connected': connected, 'signal': signal, 'kind': kind, 'seen': 0, 'nearby': True}


class FakeScanner:
    def __init__(self):
        self.running = False
        self.devices = []
        self.starts = 0

    def start(self):
        self.running = True
        self.starts += 1

    def stop(self):
        self.running = False

    def snapshot(self, include_unnamed=False):
        return [d for d in self.devices if include_unnamed or d['name']]


class NearbyPanelTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def open_panel(self, devices):
        scanner = FakeScanner()
        scanner.devices = devices
        self.enterContext(patch.object(main.bt_scan, 'NearbyScanner', return_value=scanner))
        self.app.show_window()
        self.app.btn_bluetooth.invoke()
        return scanner

    def row_texts(self):
        texts = []
        for row in self.app._nearby_win.rows:
            for widget in row.winfo_children():
                for child in widget.winfo_children() or [widget]:
                    if isinstance(child, main.ctk.CTkLabel) and child.cget('text'):
                        texts.append(child.cget('text'))
        return texts

    def test_bluetooth_button_opens_one_panel_and_scans_only_while_open(self):
        scanner = self.open_panel([device('Akko S9 Ultra_BT', paired=True, connected=True),
                                   device('WH-1000XM5', signal=-55), device('', signal=-40)])
        self.assertEqual(self.app.state(), 'withdrawn', 'Battery panel hides while scanning')
        self.assertTrue(scanner.running)
        texts = self.row_texts()
        self.assertIn('Akko S9 Ultra_BT', texts)
        self.assertIn('Đang kết nối', texts)
        self.assertIn('Chưa ghép đôi · tín hiệu mạnh', texts)
        self.assertNotIn('Thiết bị không tên', texts)
        self.app._nearby_win.show_unnamed.toggle()
        self.assertIn('Thiết bị không tên', self.row_texts())
        self.app._nearby_win.btn_close.invoke()
        self.assertFalse(scanner.running)
        self.assertIsNone(self.app._nearby_win)
        self.assertEqual(self.app.state(), 'normal')

    def test_pairing_runs_off_the_ui_thread_and_reports_result(self):
        self.open_panel([device('WH-1000XM5', id_='sony')])
        with patch.object(main.bt_scan, 'pair', return_value=(True, 'Đã ghép đôi')) as pair, \
                patch.object(main.threading, 'Thread') as thread:
            self.app._pair_nearby(self.app._nearby_scanner.devices[0])
            self.assertIn('sony', self.app._pairing_ids)
            thread.return_value.start.assert_called_once()
            thread.call_args.kwargs['target']()  # run the worker inline
            pair.assert_called_once()
            self.assertEqual(pair.call_args.args, ('sony',))
            self.assertTrue(callable(pair.call_args.kwargs['confirm_pin']), 'PIN codes are asked, never auto-accepted')
        self.app._drain_ui_queue()
        self.assertNotIn('sony', self.app._pairing_ids)
        self.assertEqual(self.app._toast.lbl_subtitle.cget('text'), 'Đã ghép đôi')

    def test_details_show_known_battery_then_gatt_info_and_back(self):
        self.app._apply_headphones_update([
            {'id': 'Bluetooth#Bluetooth0c:1d:2e:3f:40:bc-0b:1c:2d:3e:4f:05', 'name': 'Akko', 'battery_level': 90}])
        akko = device('Akko', paired=True, connected=True)
        akko['address'] = '0B:1C:2D:3E:4F:05'
        self.open_panel([akko])
        with patch.object(main.bt_scan, 'read_details',
                          return_value={'battery': 93, 'manufacturer': 'Akko', 'firmware': '1.2'}), \
                patch.object(main.threading, 'Thread') as thread:
            self.app._show_nearby_details(akko)
            thread.call_args.kwargs['target']()
        fields = self.app._nearby_win.detail.fields
        self.assertEqual(fields['Pin'].cget('text'), '90% · Windows', 'Battery the app already knows wins')
        self.assertEqual(fields['Địa chỉ'].cget('text'), '0B:1C:2D:3E:4F:05')
        self.app._drain_ui_queue()
        self.assertEqual(fields['Hãng'].cget('text'), 'Akko')
        self.assertEqual(fields['Model'].cget('text'), '—')
        self.assertEqual(fields['Firmware'].cget('text'), '1.2')
        self.assertFalse(self.app._nearby_win.list._parent_frame.winfo_manager())
        self.app._hide_nearby_details()
        self.assertIsNone(self.app._nearby_win.detail)
        self.assertTrue(self.app._nearby_win.list._parent_frame.winfo_manager())

    def test_gatt_battery_fills_unknown_device_and_stale_results_are_ignored(self):
        phone = device('iPhone', paired=True, connected=True, kind='phone')
        phone['address'] = 'AA:BB:CC:DD:EE:FF'
        self.open_panel([phone])
        with patch.object(main.bt_scan, 'read_details', return_value={'battery': 57}), \
                patch.object(main.threading, 'Thread') as thread:
            self.app._show_nearby_details(phone)
            first = thread.call_args.kwargs['target']
            self.app._show_nearby_details(phone)  # "Đọc lại" before the first read returned
            first()
            self.app._drain_ui_queue()
            self.assertEqual(self.app._nearby_win.detail.fields['Pin'].cget('text'), 'Đang đọc…')
            thread.call_args.kwargs['target']()
        self.app._drain_ui_queue()
        self.assertEqual(self.app._nearby_win.detail.fields['Pin'].cget('text'), '57% · BLE')

    def test_viewing_an_unpaired_device_never_connects_or_pairs(self):
        tv = device('[TV] Samsung', signal=-80, kind='tv')
        self.open_panel([tv])
        with patch.object(main.bt_scan, 'read_details') as read, patch.object(main.bt_scan, 'pair') as pair, \
                patch.object(main.threading, 'Thread') as thread:
            self.app._show_nearby_details(tv)
            self.app._drain_ui_queue()
        read.assert_not_called()
        pair.assert_not_called()
        thread.assert_not_called()
        self.assertEqual(self.app._nearby_win.detail.fields['Pin'].cget('text'), 'Cần ghép đôi để đọc')
        self.assertNotIn(tv['id'], self.app._pairing_ids)

    def paired_details(self):
        jbl = device('JBL CLUB PRO+ TWS', paired=True, id_='jbl-aep')
        jbl['address'] = '0A:1B:2C:3D:4E:3F'
        self.open_panel([jbl])
        with patch.object(main.bt_scan, 'read_details', return_value={}), \
                patch.object(main.threading, 'Thread'):
            self.app._show_nearby_details(jbl)
        return jbl

    def test_unpair_asks_first_and_cancel_does_nothing(self):
        jbl = self.paired_details()
        with patch.object(main.bt_scan, 'unpair') as unpair:
            self.app._nearby_win.detail.btn_unpair.invoke()
            confirm = self.app._nearby_win.detail.confirm
            self.assertIn('JBL CLUB PRO+ TWS', confirm.winfo_children()[0].cget('text'))
            confirm.btn_cancel.invoke()
        unpair.assert_not_called()
        self.assertIsNone(self.app._nearby_win.detail.confirm)
        self.assertEqual(jbl['paired'], True)

    def test_confirmed_unpair_runs_in_background_and_returns_to_list(self):
        self.paired_details()
        with patch.object(main.bt_scan, 'unpair', return_value=(True, 'Đã bỏ ghép đôi')) as unpair, \
                patch.object(main.threading, 'Thread') as thread:
            self.app._nearby_win.detail.btn_unpair.invoke()
            self.app._nearby_win.detail.confirm.btn_confirm.invoke()
            unpair.assert_not_called()  # not on the UI thread
            thread.call_args.kwargs['target']()
            unpair.assert_called_once_with('jbl-aep')
        self.app._drain_ui_queue()
        self.assertIsNone(self.app._nearby_win.detail)
        self.assertEqual(self.app._toast.lbl_subtitle.cget('text'), 'Đã bỏ ghép đôi')

    def test_unpaired_device_has_no_unpair_button(self):
        tv = device('[TV] Samsung', kind='tv')
        self.open_panel([tv])
        self.app._show_nearby_details(tv)
        self.assertFalse(hasattr(self.app._nearby_win.detail, 'btn_unpair'))

    def test_settings_and_scan_panel_never_show_together(self):
        scanner = self.open_panel([])
        self.app._show_settings_dialog()
        self.assertIsNone(self.app._nearby_win)
        self.assertFalse(scanner.running)


class PairingHandlerTests(unittest.TestCase):
    def test_custom_pairing_answers_confirmation_and_pin(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock
        from winrt.windows.devices.enumeration import DevicePairingKinds, DevicePairingResultStatus
        handlers = []

        async def pair_async(kinds):
            for kind in (DevicePairingKinds.CONFIRM_ONLY, DevicePairingKinds.PROVIDE_PIN):
                args = Mock(pairing_kind=kind, pin='')
                handlers[0](None, args)
                answered.append((kind, args.accept.called, args.accept_with_pin.call_args))
            return SimpleNamespace(status=DevicePairingResultStatus.PAIRED)
        answered = []
        custom = SimpleNamespace(add_pairing_requested=lambda handler: handlers.append(handler) or 1,
                                 remove_pairing_requested=Mock(), pair_async=pair_async)
        info = SimpleNamespace(pairing=SimpleNamespace(is_paired=False, can_pair=True, custom=custom))
        with unittest.mock.patch.object(main.bt_scan.DeviceInformation, 'create_from_id_async',
                                        AsyncMock(return_value=info)):
            ok, message = main.bt_scan.pair('dev')
        self.assertTrue(ok)
        self.assertEqual(message, 'Đã ghép đôi')
        self.assertTrue(answered[0][1], 'Confirm-only request is accepted')
        self.assertEqual(answered[1][2].args, ('0000',))
        custom.remove_pairing_requested.assert_called_once_with(1)


class SnapshotTests(unittest.TestCase):
    def test_device_kind_separates_headphones_from_tvs(self):
        self.assertEqual(main.bt_scan._kind(4, 6), 'headphone')
        self.assertEqual(main.bt_scan._kind(4, 15), 'tv')
        self.assertEqual(main.bt_scan._kind(2, 3), 'phone')
        self.assertEqual(main.bt_scan._kind(None, None), 'other')

    def test_signal_wording(self):
        self.assertEqual(main.JblFlyoutApp._signal_text(-50), 'tín hiệu mạnh')
        self.assertEqual(main.JblFlyoutApp._signal_text(-70), 'tín hiệu vừa')
        self.assertEqual(main.JblFlyoutApp._signal_text(-90), 'tín hiệu yếu')
        self.assertIsNone(main.JblFlyoutApp._signal_text(None))


if __name__ == '__main__':
    unittest.main()
