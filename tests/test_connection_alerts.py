import unittest
import test_app_lifecycle as lifecycle


class ConnectionAlertTests(unittest.TestCase):
    def setUp(self):
        lifecycle.AppLifecycleTests.setUp(self)
        self.app.settings['connection_alert'] = True
        self.sent = []
        self.app._notify = lambda message, title: self.sent.append(message) or True
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def settle(self):
        """Hết thời gian chờ ổn định (CONNECTION_SETTLE_MS)."""
        if self.app._announce_timer:
            self.app.after_cancel(self.app._announce_timer)
        self.app._announce_connection_changes()

    def test_initial_snapshot_silent_then_connect_and_disconnect(self):
        a = {'id': 'a', 'name': 'A', 'battery_level': 70}
        b = {'id': 'b', 'name': 'B'}
        self.app._apply_headphones_update([a])
        self.settle()
        self.assertEqual(self.sent, [])
        self.app._apply_headphones_update([a, b])
        self.assertEqual(self.sent, [], 'Nothing until the connection has settled')
        self.settle()
        self.assertEqual(self.sent, ['Đã kết nối B'])
        self.app._apply_headphones_update([b])
        self.settle()
        self.assertEqual(self.sent[-1], 'Đã ngắt A')

    def test_switching_headsets_is_one_notification(self):
        # Đúng tình huống người dùng gặp: đang có JBL + Akko, ngắt JBL để nghe Akko.
        jbl = {'id': 'jbl', 'name': 'JBL CLUB PRO+ TWS'}
        akko = {'id': 'akko', 'name': 'Akko S9 Ultra_BT', 'battery_level': 90}
        self.app._apply_headphones_update([jbl])
        self.app._apply_headphones_update([jbl, akko])
        self.app._apply_headphones_update([akko])
        self.settle()
        self.assertEqual(self.sent, ['Đã kết nối Akko S9 Ultra_BT · 90%; đã ngắt JBL CLUB PRO+ TWS'])

    def test_flapping_connection_announces_nothing(self):
        # Windows báo chập chờn khi nối/ngắt (từng profile âm thanh): ngắt rồi nối lại trong vài giây.
        jbl = {'id': 'jbl', 'name': 'JBL'}
        akko = {'id': 'akko', 'name': 'Akko'}
        self.app._apply_headphones_update([jbl, akko])
        for devices in ([akko], [jbl, akko], [akko], [jbl, akko]):
            self.app._apply_headphones_update(devices)
        self.settle()
        self.assertEqual(self.sent, [])

    def test_waits_for_the_last_change_before_announcing(self):
        a = {'id': 'a', 'name': 'A'}
        self.app._apply_headphones_update([])
        self.app._apply_headphones_update([a])
        first = self.app._announce_timer
        self.app._apply_headphones_update([a])  # vd. Windows vừa báo thêm số pin
        self.assertNotEqual(self.app._announce_timer, first, 'Each change restarts the wait')
        self.assertEqual(len([t for t in self.app.tk.call('after', 'info')
                              if t == self.app._announce_timer]), 1)
        self.settle()
        self.assertEqual(self.sent, ['Đã kết nối A'])

    def test_errors_do_not_change_alert_baseline(self):
        a = {'id': 'a', 'name': 'A', 'battery_level': 70}
        self.app._apply_headphones_update([a])
        self.app._apply_headphones_update([], 'error')
        self.app._apply_headphones_update([a])
        self.settle()
        self.assertEqual(self.sent, [])
        self.app._apply_headphones_update([])
        self.settle()
        self.assertEqual(self.sent, ['Đã ngắt A'])
        self.app._apply_headphones_update([a])
        self.settle()
        self.assertEqual(self.sent[-1], 'Đã kết nối A · 70%')

    def test_disabled_still_updates_baseline(self):
        self.app._apply_headphones_update([])
        self.app.settings['connection_alert'] = False
        self.app._apply_headphones_update([{'id': 'a', 'name': 'A'}])
        self.settle()
        self.app.settings['connection_alert'] = True
        self.app._apply_headphones_update([{'id': 'a', 'name': 'A'}])
        self.settle()
        self.assertEqual(self.sent, [])


if __name__ == '__main__':
    unittest.main()
