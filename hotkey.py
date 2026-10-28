"""A Win32 thread message loop for Ctrl+Alt+B (and optional extra hotkeys). Never calls Tk."""
import ctypes
from ctypes import wintypes
import logging
import threading

import i18n

i18n.register({
    'Không đăng ký được Ctrl+Alt+B (có thể đang được ứng dụng khác sử dụng).':
        "Couldn't register Ctrl+Alt+B (another app may be using it).",
    'Không thể bật phím tắt Ctrl+Alt+B.': "Couldn't turn on the Ctrl+Alt+B shortcut.",
})

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012


class HotkeyWorker:
    def __init__(self, on_hotkey, on_status=None, *, user32=None, kernel32=None, extra=None):
        self.on_hotkey = on_hotkey
        # {id: (modifiers, virtual key, callback)}; a conflict here only logs, Ctrl+Alt+B still works.
        self.extra = dict(extra or {})
        self.on_status = on_status
        self.error = None
        self._user32 = user32
        self._kernel32 = kernel32
        self._thread = None
        self._thread_id = None
        self._ready = threading.Event()
        self._stopping = threading.Event()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._stopping.clear()
        self._thread = threading.Thread(target=self._run, name='GlobalHotkey', daemon=True)
        self._thread.start()

    def stop(self):
        self._stopping.set()
        if self._thread and self._thread is not threading.current_thread():
            self._ready.wait(2)
            if self._thread_id and self._thread.is_alive():
                if not self._user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0):
                    logging.warning('Cannot post hotkey shutdown message')
            self._thread.join(3)

    def _run(self):
        registered = False
        extra_registered = []
        try:
            if self._user32 is None:
                self._user32 = ctypes.WinDLL('user32', use_last_error=True)
                self._user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
                self._user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
                self._user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
                self._user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
                self._user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            if self._kernel32 is None:
                self._kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
                self._kernel32.GetCurrentThreadId.restype = wintypes.DWORD
            self._thread_id = self._kernel32.GetCurrentThreadId()
            message = wintypes.MSG()
            # Create the queue before stop() is allowed to post WM_QUIT.
            self._user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
            registered = bool(self._user32.RegisterHotKey(None, 1, 0x4003, ord('B')))
            self.error = None if registered else 'Không đăng ký được Ctrl+Alt+B (có thể đang được ứng dụng khác sử dụng).'
            if self.error:
                logging.warning(self.error)
            for hotkey_id, (modifiers, key, _) in self.extra.items() if registered else ():
                if self._user32.RegisterHotKey(None, hotkey_id, modifiers, key):
                    extra_registered.append(hotkey_id)
                else:
                    logging.warning('Cannot register extra hotkey %s', hotkey_id)
            if self.on_status:
                self.on_status(self.error)
            self._ready.set()
            while registered and not self._stopping.is_set():
                result = self._user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == -1:
                    raise OSError('GetMessageW failed')
                if result == 0:
                    break
                if message.message == WM_HOTKEY and message.wParam == 1:
                    self.on_hotkey()
                elif message.message == WM_HOTKEY and message.wParam in extra_registered:
                    self.extra[message.wParam][2]()
        except Exception:
            self.error = 'Không thể bật phím tắt Ctrl+Alt+B.'
            logging.exception(self.error)
            if self.on_status:
                self.on_status(self.error)
        finally:
            self._ready.set()
            if registered:
                self._user32.UnregisterHotKey(None, 1)
                for hotkey_id in extra_registered:
                    self._user32.UnregisterHotKey(None, hotkey_id)
            self._thread_id = None
