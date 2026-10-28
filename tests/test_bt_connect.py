import unittest
from unittest.mock import patch

import bt_connect
import main
import test_app_lifecycle as lifecycle
import ui_nearby
from test_nearby import FakeScanner, device

JBL_ID = 'Bluetooth#Bluetooth0c:1d:2e:3f:40:bc-0a:1b:2c:3d:4e:3f'
JBL_FILTER = (r'{2}.\\?\bthenum#{0000110b-0000-1000-8000-00805f9b34fb}_vid&00010ecb_pid&1f65'
              r'#7&85da40a&2&0a1b2c3d4e3f_c00000000#{6994ad04-93ef-11d0-a3cc-00a0c9223196}\src')


class FilterMatchTests(unittest.TestCase):
    def test_driver_is_matched_by_address_not_name(self):
        self.assertTrue(bt_connect.filter_matches(JBL_FILTER, '0A:1B:2C:3D:4E:3F'))
        self.assertTrue(bt_connect.filter_matches(JBL_FILTER, '0a1b2c3d4e3f'))
        self.assertFalse(bt_connect.filter_matches(JBL_FILTER, '0B:1C:2D:3E:4F:05'))
        self.assertFalse(bt_connect.filter_matches(JBL_FILTER, '1B:2C:3D:4E:3F:00'), 'Partial overlap is not a match')
        self.assertFalse(bt_connect.filter_matches(JBL_FILTER.replace('bthenum', 'usb'), '0A:1B:2C:3D:4E:3F'))
        self.assertFalse(bt_connect.filter_matches(None, '0A:1B:2C:3D:4E:3F'))
        self.assertFalse(bt_connect.filter_matches(JBL_FILTER, None))

    def test_bad_address_is_refused_before_touching_windows(self):
        with patch.object(bt_connect.ao, '_create') as create:
            self.assertEqual(bt_connect.set_connected('JBL', True), (False, 'Không rõ địa chỉ Bluetooth của thiết bị'))
        create.assert_not_called()

    def test_device_without_audio_driver_gets_clear_message(self):
        with patch.object(bt_connect.ao, '_create'), patch.object(bt_connect.ao, '_release'), \
                patch.object(bt_connect, '_filters', return_value=[]):
            ok, message = bt_connect._send_command('11:22:33:44:55:66', True)
        self.assertFalse(ok)
        self.assertIn('tai nghe/loa', message)

    def test_command_goes_to_every_matching_driver(self):
        sent = []
        with patch.object(bt_connect.ao, '_create'), patch.object(bt_connect.ao, '_release'), \
                patch.object(bt_connect, '_filters', return_value=['a2dp', 'hfp']), \
                patch.object(bt_connect, '_send', lambda enum, filter_id, command: sent.append((filter_id, command))):
            self.assertTrue(bt_connect._send_command('0A:1B:2C:3D:4E:3F', False)[0])
        self.assertEqual(sent, [('a2dp', 1), ('hfp', 1)])

    def test_windows_errors_never_escape(self):
        with patch.object(bt_connect.ao, '_create', side_effect=OSError('COM')), self.assertLogs(level='WARNING'):
            self.assertFalse(bt_connect._send_command('0A:1B:2C:3D:4E:3F', True)[0])


class VerifiedResultTests(unittest.TestCase):
    """Driver nhận lệnh chưa chắc đã làm (Intel Audio Offload): chỉ báo thành công khi Windows đổi trạng thái."""

    def run_with(self, states, sent=(True, 'Đã gửi lệnh'), connect=True):
        states = iter(states)
        with patch.object(bt_connect, 'is_connected', side_effect=lambda address: next(states)), \
                patch.object(bt_connect, '_send_command', return_value=sent) as send, \
                patch.object(bt_connect, 'VERIFY_POLL_SECONDS', 0), \
                patch.dict(bt_connect.VERIFY_SECONDS, {True: .05, False: .05}):
            return bt_connect.set_connected('0A:1B:2C:3D:4E:3F', connect), send

    def test_success_only_after_windows_confirms(self):
        (result, send) = self.run_with([False, False, False, True])
        self.assertEqual(result, (True, 'Đã kết nối'))
        send.assert_called_once()

    def test_accepted_command_without_effect_is_a_failure(self):
        import itertools
        with self.assertLogs(level='INFO'):
            (result, _) = self.run_with(itertools.chain([True], itertools.repeat(True)), connect=False)
        self.assertEqual(result, (False, bt_connect.CANNOT_DISCONNECT))

    def test_connect_timeout_says_what_to_do(self):
        import itertools
        with self.assertLogs(level='INFO'):
            (result, _) = self.run_with(itertools.repeat(False))
        self.assertFalse(result[0])
        self.assertIn('bật tai nghe', result[1])

    def test_already_in_wanted_state_sends_nothing(self):
        (result, send) = self.run_with([True])
        self.assertEqual(result, (True, 'Đã kết nối'))
        send.assert_not_called()

    def test_send_failure_is_passed_through(self):
        (result, _) = self.run_with([False], sent=(False, 'Chỉ tai nghe/loa Bluetooth mới kết nối được từ app'))
        self.assertEqual(result[1], 'Chỉ tai nghe/loa Bluetooth mới kết nối được từ app')


class ConnectButtonTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def run_threads_inline(self):
        return patch.object(ui_nearby.threading, 'Thread',
                            side_effect=lambda target, **kwargs: type('T', (), {'start': staticmethod(target)})())

    def test_disconnected_headphone_offers_connect_on_main_card(self):
        self.app._apply_headphones_update([{'id': JBL_ID, 'name': 'JBL CLUB PRO+ TWS'}])
        self.assertFalse(self.app.btn_connect.winfo_manager(), 'Connected: no button')
        self.app._apply_headphones_update([])
        self.assertTrue(self.app.btn_connect.winfo_manager())
        with patch.object(ui_nearby.bt_connect, 'set_connected', return_value=(True, 'Đã kết nối')) as call, \
                patch.object(ui_nearby.threading, 'Thread') as thread:
            self.app.btn_connect.invoke()
            self.assertEqual(self.app.btn_connect.cget('text'), 'Đang kết nối…')
            self.assertEqual(self.app.btn_connect.cget('state'), 'disabled', 'No double sends while waiting')
            self.app.btn_connect.invoke()
            thread.assert_called_once()
            thread.call_args.kwargs['target']()  # luồng nền: gửi lệnh và chờ Windows xác nhận
        self.app._drain_ui_queue()
        call.assert_called_once_with('0A:1B:2C:3D:4E:3F', True)
        self.assertEqual(self.app._toast.lbl_subtitle.cget('text'), 'Đã kết nối')
        self.assertEqual(self.app.btn_connect.cget('state'), 'normal')

    def test_failed_disconnect_never_opens_windows_on_its_own(self):
        # Người dùng phàn nàn: app tự nhảy vào Cài đặt Bluetooth. Chỉ báo, không mở gì.
        jbl = device('JBL', paired=True, connected=True, id_='jbl-aep')
        jbl['address'] = '0A:1B:2C:3D:4E:3F'
        self.open_details(jbl)
        with patch.object(self.app, '_open_bluetooth_settings') as settings, \
                patch.object(main.os, 'startfile') as startfile:
            self.app._finish_device_connection(jbl, (jbl['id'], False), False, ui_nearby.bt_connect.CANNOT_DISCONNECT)
        settings.assert_not_called()
        startfile.assert_not_called()
        self.assertIn('Cài đặt Bluetooth', self.app._toast.lbl_subtitle.cget('text'))

    def test_no_disconnect_button_where_the_pc_cannot_disconnect(self):
        # Máy dùng Intel Bluetooth Audio Offload: không hiện nút bấm mà không có tác dụng.
        jbl = device('JBL', paired=True, connected=True, id_='jbl-aep')
        jbl['address'] = '0A:1B:2C:3D:4E:3F'
        with patch.object(ui_nearby.bt_connect, 'can_disconnect', return_value=False):
            self.open_details(jbl)
        self.assertFalse(hasattr(self.app._nearby_win.detail, 'btn_connect'))
        jbl['connected'] = False
        with patch.object(ui_nearby.bt_connect, 'can_disconnect', return_value=False):
            self.app._show_nearby_details(jbl)
        self.assertEqual(self.app._nearby_win.detail.btn_connect.cget('text'), 'Kết nối', 'Connecting is still offered')

    def test_failure_is_reported_and_button_stays_usable(self):
        self.app._apply_headphones_update([{'id': JBL_ID, 'name': 'JBL'}])
        self.app._apply_headphones_update([])
        with patch.object(ui_nearby.bt_connect, 'set_connected', return_value=(False, 'Không kết nối được')), \
                self.run_threads_inline():
            self.app.btn_connect.invoke()
            self.app._drain_ui_queue()
        self.assertEqual(self.app._toast.lbl_subtitle.cget('text'), 'Không kết nối được')
        self.assertEqual(self.app.btn_connect.cget('state'), 'normal')

    def test_split_battery_shortcut_opens_picker_and_hides_once_bound(self):
        self.app.battery_device_id = None
        self.app.core.target_address = 'C0:FF:EE:00:00:CC'
        self.app.fast_pair_scanner.items = [('C0:FF:EE:00:00:CC', -55,
                                             ((80, False), (85, False), (100, False)))]
        self.app._apply_headphones_update([{'id': JBL_ID, 'name': 'JBL CLUB PRO+ TWS', 'battery_level': 90}])
        self.app._check_split_discovery()
        self.assertTrue(self.app.btn_split.winfo_manager())
        self.app.btn_split.invoke()
        self.assertIsNone(self.app.battery_device_id, 'Shortcut only opens the list')
        self.app._split_picker.rows[0].btn_choose.invoke()
        self.assertEqual(self.app.battery_device_id, JBL_ID)
        self.assertFalse(self.app.btn_split.winfo_manager())

    def test_mouse_or_phone_gets_no_connect_button(self):
        self.app._apply_headphones_update([{'id': JBL_ID, 'name': 'Chuột', 'kind': 'mouse'}])
        self.app._apply_headphones_update([])
        self.assertFalse(self.app.btn_connect.winfo_manager())

    def test_nearby_details_toggle_connection_for_paired_audio(self):
        jbl = device('JBL', paired=True, connected=False, id_='jbl-aep')
        jbl['address'] = '0A:1B:2C:3D:4E:3F'
        scanner = FakeScanner()
        scanner.devices = [jbl]
        with patch.object(main.bt_scan, 'NearbyScanner', return_value=scanner), \
                patch.object(main.bt_scan, 'read_details', return_value={}), \
                patch.object(ui_nearby.threading, 'Thread'):
            self.app.show_window()
            self.app.btn_bluetooth.invoke()
            self.app._show_nearby_details(jbl)
        self.assertEqual(self.app._nearby_win.detail.btn_connect.cget('text'), 'Kết nối')
        with patch.object(ui_nearby.bt_connect, 'set_connected', return_value=(True, 'Đã ngắt kết nối')) as call, \
                self.run_threads_inline(), patch.object(main.bt_scan, 'read_details', return_value={}):
            jbl['connected'] = True
            self.app._show_nearby_details(jbl)
            self.assertEqual(self.app._nearby_win.detail.btn_connect.cget('text'), 'Ngắt kết nối')
            self.app._nearby_win.detail.btn_connect.invoke()
        call.assert_called_once_with('0A:1B:2C:3D:4E:3F', False)

    def open_details(self, dev):
        scanner = FakeScanner()
        scanner.devices = [dev]
        with patch.object(main.bt_scan, 'NearbyScanner', return_value=scanner), \
                patch.object(main.bt_scan, 'read_details', return_value={}) as read, \
                patch.object(ui_nearby.threading, 'Thread') as thread:
            self.app.show_window()
            self.app.btn_bluetooth.invoke()
            self.app._show_nearby_details(dev)
        return read, thread

    def test_bound_split_battery_headset_is_never_probed_over_ble(self):
        # Mở chi tiết JBL không được thử nối BLE (làm nhạc giật); pin trái/phải đã có trên bảng chính.
        self.app.battery_device_id = JBL_ID
        jbl = device('JBL CLUB PRO+ TWS', paired=True, connected=True, id_='jbl-aep')
        jbl['address'] = '0A:1B:2C:3D:4E:3F'
        read, thread = self.open_details(jbl)
        thread.assert_not_called()
        read.assert_not_called()
        detail = self.app._nearby_win.detail
        self.assertEqual(detail.fields['Hãng'].cget('text'), '—')
        self.assertEqual(detail.btn_connect.cget('text'), 'Ngắt kết nối')
        texts = [w.cget('text') for w in detail.actions.winfo_children() if isinstance(w, main.ctk.CTkButton)]
        self.assertNotIn('Đọc lại', texts)

    def test_details_button_locks_while_sending_and_reports(self):
        jbl = device('JBL', paired=True, connected=True, id_='jbl-aep')
        jbl['address'] = '0A:1B:2C:3D:4E:3F'
        self.open_details(jbl)
        with patch.object(ui_nearby.bt_connect, 'set_connected', return_value=(False, 'Không ngắt được')), \
                patch.object(ui_nearby.threading, 'Thread'):
            self.app._nearby_win.detail.btn_connect.invoke()
        button = self.app._nearby_win.detail.btn_connect
        self.assertEqual((button.cget('text'), button.cget('state')), ('Đang ngắt…', 'disabled'))
        self.app._finish_device_connection(jbl, (jbl['id'], False), False, 'Không ngắt được')
        self.assertEqual((button.cget('text'), button.cget('state')), ('Ngắt kết nối', 'normal'))
        self.app._finish_device_connection(jbl, (jbl['id'], False), True, 'Đã ngắt kết nối')
        self.assertIsNone(self.app._nearby_win.detail, 'Success returns to the live list')

    def test_ble_only_device_has_no_connect_button(self):
        keyboard = device('Akko', paired=True, le=True, kind='peripheral')
        keyboard['address'] = '0B:1C:2D:3E:4F:05'
        scanner = FakeScanner()
        scanner.devices = [keyboard]
        with patch.object(main.bt_scan, 'NearbyScanner', return_value=scanner), \
                patch.object(main.bt_scan, 'read_details', return_value={}), \
                patch.object(ui_nearby.threading, 'Thread'):
            self.app.show_window()
            self.app.btn_bluetooth.invoke()
            self.app._show_nearby_details(keyboard)
        self.assertFalse(hasattr(self.app._nearby_win.detail, 'btn_connect'))


if __name__ == '__main__':
    unittest.main()
