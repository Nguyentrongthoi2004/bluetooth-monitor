import socket
import threading
import time
import unittest
from app_test_support import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

import bt_connect
import main


class OfflineCore:
    def __init__(self, **kwargs):
        self.error = None

    def start(self):
        pass

    def stop(self):
        pass

    def trigger_refresh(self):
        pass

    def set_targets(self, devices):
        pass

    def set_include_other(self, enabled):
        pass


class OfflineFastPair:
    """Bộ quét Fast Pair giả: test không bao giờ bật Bluetooth thật. items/values do từng test đặt."""

    def __init__(self, on_update):
        self.on_update = on_update
        self.watched = set()
        self.running = False
        self.items = []
        self.values = {}

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def set_watched(self, addresses):
        self.watched = set(addresses)

    def candidates(self):
        return list(self.items)

    def latest(self, address):
        return self.values.get(address)


class AppLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        folder = self.temp_dir.name
        reservation = socket.socket()
        reservation.bind(('127.0.0.1', 0))
        self.port = reservation.getsockname()[1]
        reservation.close()
        self.patches = [
            patch.object(main, 'DATA_DIR', folder),
            patch.object(main, 'SETTINGS_PATH', str(Path(folder, 'settings.json'))),
            patch.object(main, 'is_startup_enabled', return_value=False),
            patch.object(main, 'set_startup', return_value=True),
            patch.object(main, 'get_work_area', return_value=None),
            patch.object(main, 'read_system_theme', return_value='dark'),
            # Settings mới = 'system': không phụ thuộc ngôn ngữ Windows của máy chạy test (mặc định tiếng Việt).
            patch.object(main.i18n, 'system_language', return_value=getattr(self, 'system_language', 'vi')),
            patch.object(main.os, 'startfile'),
            patch.object(main, 'IPC_PORT', self.port),
            patch.object(main, 'JblCore', OfflineCore),
            patch.object(main, 'HeadphoneConnectionMonitor', lambda *args, **kwargs: OfflineCore()),
            patch.object(main, 'GattBatteryMonitor', lambda *args, **kwargs: OfflineCore()),
            patch.object(main, 'HotkeyWorker', lambda *args, **kwargs: OfflineCore()),
            patch.object(main.pystray.Icon, 'run_detached', lambda icon: None),
            # Never touch the real Windows audio devices from tests.
            patch.object(main.audio_output, 'list_outputs', return_value=[]),
            patch.object(main.audio_output, 'default_output_id', return_value=None),
            patch.object(main.audio_output, 'set_default_output', return_value=False),
            patch.object(main.audio_output, 'fxsound_output_id', return_value=None),
            patch.object(main.fast_pair, 'FastPairScanner', OfflineFastPair),
            # Không đụng driver âm thanh thật; từng test tự đặt khi cần.
            patch.object(bt_connect, 'can_disconnect', return_value=True),
        ]
        for replacement in self.patches:
            replacement.start()
        self.app = main.JblFlyoutApp(start_minimized=True)
        # Legacy notification tests exercise low battery only; connection tests opt in.
        self.app.settings['connection_alert'] = False
        # Popups and sounds are opt-in per test, so they never appear while the suite runs.
        self.app.settings.update(popup_on_connect=False, alert_overlay=False, alert_sound=False)
        # The real app stores the original JBL's Windows identity in local settings.
        self.app.battery_device_id = 'jbl'

    def tearDown(self):
        self.app._closing = True
        self.app.ipc_sock.close()
        def widgets(parent):
            yield parent
            for child in parent.winfo_children():
                yield from widgets(child)
        owners = list(widgets(self.app))
        for callback_id in self.app.tk.call('after', 'info'):
            script = self.app.tk.call('after', 'info', callback_id)[0]
            command = self.app.tk.splitlist(script)[0]
            owner = next((widget for widget in owners if command in (widget._tclCommands or [])), self.app)
            owner.after_cancel(callback_id)
        self.app.destroy()
        for replacement in reversed(self.patches):
            replacement.stop()
        self.temp_dir.cleanup()

    def pump_until_shown(self, timeout=3):
        # after(quit)+mainloop() can hang: CTk.mainloop runs a nested update() first and a
        # quit() landing there is reset when the real mainloop starts. Pump explicitly.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.app.state() != 'normal':
            self.app.update()
            time.sleep(.01)

    def test_tray_click_queues_show_without_touching_tk_from_worker(self):
        # A tray click must return even while Tk is busy or not yet pumping.
        errors = []

        def click():
            try:
                list(self.app.tray_icon.menu)[0](self.app.tray_icon)
            except Exception as error:
                errors.append(str(error))

        worker = threading.Thread(target=click)
        worker.start()
        worker.join(2)
        self.assertFalse(worker.is_alive(), 'Tray callback blocked on Tk')
        self.assertEqual(errors, [], 'Tray callback used Tk from another thread')
        self.pump_until_shown()
        self.assertEqual(self.app.state(), 'normal')

    def test_reopening_shortcut_shows_existing_window(self):
        with self.assertRaises(SystemExit):
            main.try_single_instance()
        self.pump_until_shown()
        self.assertEqual(self.app.state(), 'normal')

    def test_duplicate_launch_does_not_create_another_tray_icon(self):
        created = []
        real_icon = main.pystray.Icon

        def track_icon(*args, **kwargs):
            created.append(True)
            return real_icon(*args, **kwargs)

        with patch.object(main.pystray, 'Icon', track_icon):
            with self.assertRaises(SystemExit):
                main.JblFlyoutApp(start_minimized=True)
        self.assertEqual(created, [], 'Duplicate launch created an extra tray icon')

    def test_pin_can_be_toggled_on_first_open(self):
        self.app._toggle_pin()
        self.assertTrue(self.app.is_pinned)
        self.app._toggle_pin()
        self.assertFalse(self.app.is_pinned)


if __name__ == '__main__':
    unittest.main()
