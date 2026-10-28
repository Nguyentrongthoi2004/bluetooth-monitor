import threading
import unittest
from unittest.mock import patch
import main
import test_app_lifecycle as lifecycle


class BluetoothSettingsTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def test_settings_button_in_scan_panel_opens_windows_bluetooth_settings(self):
        with patch.object(main.bt_scan, 'NearbyScanner'):
            self.app.btn_bluetooth.invoke()
        main.os.startfile.assert_not_called()
        self.app._open_bluetooth_settings()
        main.os.startfile.assert_called_once_with('ms-settings:bluetooth')

    def test_tray_callback_queues_os_action(self):
        menu_item = next(i for i in self.app.tray_icon.menu if i.text == 'Bluetooth…')
        thread = threading.Thread(target=lambda: menu_item(self.app.tray_icon))
        thread.start()
        thread.join(1)
        main.os.startfile.assert_not_called()
        self.app._drain_ui_queue()
        main.os.startfile.assert_called_once_with('ms-settings:bluetooth')

    def test_settings_launch_failure_is_logged(self):
        main.os.startfile.side_effect = OSError('Unavailable')
        with self.assertLogs(level='ERROR'):
            self.app._open_bluetooth_settings()
