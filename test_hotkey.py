import ctypes
from collections import deque
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from hotkey import HotkeyWorker, WM_HOTKEY, WM_QUIT
import test_app_lifecycle as lifecycle


class HotkeyTests(unittest.TestCase):
    def api(self, registered=True):
        messages = deque([(1, WM_HOTKEY), (0, WM_QUIT)])
        def get_message(pointer, *args):
            result, message = messages.popleft()
            pointer._obj.message = message
            pointer._obj.wParam = 1
            return result
        return SimpleNamespace(RegisterHotKey=Mock(return_value=registered),
                               UnregisterHotKey=Mock(), PeekMessageW=Mock(),
                               GetMessageW=get_message, PostThreadMessageW=Mock())

    def test_worker_dispatches_and_unregisters_without_os_calls(self):
        api = self.api()
        received = []
        worker = HotkeyWorker(lambda: received.append('toggle'), user32=api,
                              kernel32=SimpleNamespace(GetCurrentThreadId=lambda: 123))
        worker.start()
        worker._thread.join(1)
        self.assertEqual(received, ['toggle'])
        api.RegisterHotKey.assert_called_once_with(None, 1, 0x4003, ord('B'))
        api.UnregisterHotKey.assert_called_once_with(None, 1)

    def test_registration_conflict_reports_error_without_dispatch(self):
        api = self.api(False)
        statuses = []
        worker = HotkeyWorker(lambda: self.fail('unexpected hotkey'), statuses.append,
                              user32=api, kernel32=SimpleNamespace(GetCurrentThreadId=lambda: 123))
        with self.assertLogs(level='WARNING'):
            worker.start()
            worker._thread.join(1)
        self.assertTrue(worker.error)
        self.assertTrue(statuses[-1])
        api.UnregisterHotKey.assert_not_called()

    def test_stop_posts_quit_to_registered_thread_and_unregisters(self):
        api = self.api()
        waiting = threading.Event()
        quit_received = threading.Event()
        def get_message(*args):
            waiting.set()
            quit_received.wait(2)
            return 0
        api.GetMessageW = get_message
        api.PostThreadMessageW.side_effect = lambda *args: quit_received.set() or True
        worker = HotkeyWorker(lambda: None, user32=api,
                              kernel32=SimpleNamespace(GetCurrentThreadId=lambda: 123))
        worker.start()
        self.assertTrue(waiting.wait(1))
        worker.stop()
        self.assertFalse(worker._thread.is_alive())
        api.PostThreadMessageW.assert_called_once_with(123, WM_QUIT, 0, 0)
        api.UnregisterHotKey.assert_called_once_with(None, 1)


class HotkeyUiTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def test_callback_queues_window_toggle(self):
        import threading
        thread = threading.Thread(target=self.app._on_hotkey)
        thread.start()
        thread.join(1)
        self.assertEqual(self.app.state(), 'withdrawn')
        self.app._drain_ui_queue()
        self.assertEqual(self.app.state(), 'normal')
