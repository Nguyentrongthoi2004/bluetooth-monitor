import json
import queue
import socket
import threading
import unittest
from unittest.mock import Mock, patch

import main
import test_app_lifecycle as lifecycle


class ReviewUiTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def capture(self):
        sent = []
        self.app._notify = lambda message, title: sent.append((message, title)) or True
        return sent

    def test_queued_fast_pair_packet_cannot_cross_unbind_and_rebind(self):
        self.app._apply_headphones_update([{'id': 'jbl', 'name': 'Same'},
                                           {'id': 'other', 'name': 'Same'}])
        self.app._on_battery_update(5, 40, 70, {'left': False})
        self.app._unbind_fast_pair()
        self.app.battery_device_id = 'other'
        self.app._drain_ui_queue()
        self.assertEqual(self.app._components, {})
        self.assertEqual(self.app.history.samples('other'), [])

    def test_binding_picker_cancel_and_choose(self):
        # Chiếc JBL đã liên kết với id cũ (không kết nối), vd. sau khi ghép đôi lại: chọn lại được cho id mới.
        self.app.core.target_address = '00:11:22:33:44:55'
        self.app._apply_headphones_update([{'id': 'other', 'name': 'Same'}])
        self.app.fast_pair_scanner.items = [('00:11:22:33:44:55', -50, ((80, False), (70, False), (None, None)))]
        self.app._bind_fast_pair_to_active()
        picker = self.app._split_picker
        self.assertEqual([row.address for row in picker.rows], ['00:11:22:33:44:55'])
        picker.btn_cancel.invoke()
        self.assertIsNone(self.app._split_picker)
        self.assertEqual(self.app.battery_device_id, 'jbl', 'Closing the list changes nothing')
        self.app._bind_fast_pair_to_active()
        self.app._split_picker.rows[0].btn_choose.invoke()
        self.assertEqual(self.app.battery_device_id, 'other')
        self.assertEqual(main.load_settings()['jbl_battery_device_id'], 'other')

    def test_core_only_enabled_for_present_bound_id(self):
        self.app.core.set_enabled = Mock()
        self.app._apply_headphones_update([{'id': 'other', 'name': 'JBL'}])
        self.app.core.set_enabled.assert_called_with(False)
        self.app._apply_headphones_update([{'id': 'jbl', 'name': 'Renamed'}])
        self.app.core.set_enabled.assert_called_with(True)
        self.app._unbind_fast_pair()
        self.app.core.set_enabled.assert_called_with(False)

    def test_low_battery_uses_lowest_noncharging_earbud(self):
        sent = self.capture()
        self.app.settings['full_charge_alert'] = False
        self.app._apply_headphones_update([{'id': 'jbl', 'name': 'JBL'}])
        self.app._apply_battery_update(5, 100, 80, {'left': False, 'right': True})
        self.assertEqual(len(sent), 1)
        self.assertIn('5%', sent[0][0])

    def test_full_charge_at_same_time_charging_stops_alerts_once(self):
        sent = self.capture()
        self.app._apply_headphones_update([{'id': 'jbl', 'name': 'JBL'}])
        self.app._apply_battery_update(99, 70, 80, {'left': True})
        self.app._apply_battery_update(100, 70, 80, {'left': False})
        self.app._apply_battery_update(100, 70, 80, {'left': False})
        self.assertEqual(len(sent), 1)
        self.assertIn('Tai trái', sent[0][0])

    def test_disabled_low_alert_is_rechecked_on_enable(self):
        sent = self.capture()
        self.app.settings['low_battery_alert'] = False
        self.app._apply_headphones_update([{'id': 'a', 'name': 'A', 'battery_level': 5}])
        self.assertNotIn('a', self.app._low_alerted)
        self.app._set_setting('low_battery_alert', True)
        self.assertEqual(len(sent), 1)

    def test_disabled_full_alert_is_rechecked_after_completion(self):
        sent = self.capture()
        self.app.settings['full_charge_alert'] = False
        self.app._apply_headphones_update([{'id': 'jbl', 'name': 'JBL'}])
        self.app._apply_battery_update(99, 70, 80, {'left': True})
        self.app._apply_battery_update(100, 70, 80, {'left': False})
        self.assertNotIn(('jbl', 'left'), self.app._full_alerted)
        self.app._set_setting('full_charge_alert', True)
        self.assertEqual(len(sent), 1)

    def test_failed_notifications_do_not_consume_latches(self):
        self.app._notify = Mock(return_value=False)
        self.app._apply_headphones_update([{'id': 'jbl', 'name': 'JBL'}])
        self.app._apply_battery_update(5, 100, 80, {'left': False, 'right': True})
        self.assertEqual(self.app._low_alerted, set())
        self.assertEqual(self.app._full_alerted, set())
        sent = self.capture()
        self.app._apply_battery_update(5, 100, 80, {'left': False, 'right': True})
        self.assertEqual(len(sent), 2)

    def test_notify_reports_actual_attempt(self):
        icon = Mock(visible=False)
        self.app.tray_icon = icon
        self.assertIs(self.app._notify('m', 't'), False)
        icon.visible = True
        icon.notify.side_effect = RuntimeError('notification failed')
        with self.assertLogs(level='ERROR'):
            self.assertIs(self.app._notify('m', 't'), False)
        icon.notify.side_effect = None
        self.assertIs(self.app._notify('m', 't'), True)

    def test_manual_refresh_includes_gatt(self):
        self.app.gatt_monitor.trigger_refresh = Mock()
        self.app._on_refresh_clicked()
        self.app.gatt_monitor.trigger_refresh.assert_called_once_with()

    def test_theme_rebuild_preserves_chart_selection(self):
        self.app._set_chart_days(7)
        self.app._set_setting('theme', 'light')
        self.assertEqual(self.app.chart_controls.get(), '7 ngày')

    def test_fast_pair_and_gatt_render_history_once_each(self):
        self.app._apply_headphones_update([{'id': 'jbl', 'name': 'JBL'}])
        with patch.object(self.app, '_render_history') as render:
            self.app._apply_battery_update(80, 70, 60, {'left': False})
            self.assertEqual(render.call_count, 1)
            render.reset_mock()
            self.app._apply_gatt_update('jbl', 40)
            self.assertEqual(render.call_count, 1)

    def test_device_buttons_reused_until_order_changes(self):
        devices = [{'id': 'a', 'name': 'Same', 'battery_level': 80},
                   {'id': 'b', 'name': 'Same', 'battery_level': 50}]
        self.app._apply_headphones_update(devices)
        buttons = list(self.app._device_buttons)
        self.app._apply_gatt_update('a', 42)
        self.app._select_headphone('a')
        self.assertEqual(self.app._device_buttons, buttons)
        self.assertIn('42%', buttons[0].cget('text'))
        self.assertEqual(buttons[0].cget('border_color'), main.ACCENT)
        self.app._apply_headphones_update(list(reversed(devices)))
        self.assertNotEqual(self.app._device_buttons, buttons)

    def test_ui_queue_yields_after_100_items(self):
        self.app._ui_queue = queue.Queue()
        seen = []
        for number in range(150):
            self.app._enqueue_ui(seen.append, number)
        self.app._drain_ui_queue()
        self.assertEqual(seen, list(range(100)))
        self.app._drain_ui_queue()
        self.assertEqual(seen, list(range(150)))


class IpcRegressionTests(unittest.TestCase):
    def listener(self, app, connections):
        server = Mock()
        server.accept.side_effect = [(conn, None) for conn in connections] + [OSError('closed')]
        threads = []
        real_thread = threading.Thread
        with patch.object(main.threading, 'Thread', side_effect=lambda **kw: threads.append(real_thread(**kw)) or threads[-1]):
            main.start_ipc_listener(app, server)
        return threads[0]

    def connection(self, command):
        conn = Mock()
        conn.__enter__ = Mock(return_value=conn)
        conn.__exit__ = Mock(return_value=False)
        conn.recv.return_value = command
        return conn

    def test_status_is_requested_on_ui_thread_and_busy_on_timeout(self):
        class QueuedApp:
            _closing = False
            def __init__(self):
                self.requests = queue.Queue()
            def _enqueue_ui(self, callback, *args):
                self.requests.put((callback, args))
            def _complete_status_request(self, result, done):
                result.update(device_id='a', battery_level=42)
                done.set()
        app = QueuedApp()
        conn = self.connection(b'STATUS')
        thread = self.listener(app, [conn])
        callback, args = app.requests.get(timeout=1)
        callback(*args)
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(json.loads(conn.sendall.call_args.args[0]), {'device_id': 'a', 'battery_level': 42})
        busy = self.connection(b'STATUS')
        thread = self.listener(app, [busy])
        thread.join(3)
        self.assertEqual(json.loads(busy.sendall.call_args.args[0]), {'error': 'busy'})

    def test_bad_connection_does_not_kill_listener_and_refresh_is_queued(self):
        app = Mock(_closing=False)
        bad = self.connection(b'STATUS')
        bad.recv.side_effect = ValueError('malformed request')
        refresh = self.connection(b'REFRESH')
        with self.assertLogs(level='ERROR'):
            thread = self.listener(app, [bad, refresh])
            thread.join(1)
        app._enqueue_ui.assert_called_once_with(app._on_refresh_clicked)

