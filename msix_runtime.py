"""Chạy dưới dạng gói MSIX (Microsoft Store): "khởi động cùng Windows" qua StartupTask thay cho registry Run.

App trong gói nằm ở thư mục WindowsApps có phiên bản trong tên; Windows không chạy mục Run trỏ vào đó, và Store
yêu cầu dùng StartupTask (khai báo trong AppxManifest, TaskId bên dưới). Ngoài gói, các hàm này không được gọi.
"""
import asyncio
import ctypes
import logging
from ctypes import wintypes

TASK_ID = 'BluetoothMonitorStartup'
APPMODEL_ERROR_NO_PACKAGE = 15700


def is_packaged():
    """App có đang chạy trong gói MSIX không (GetCurrentPackageFullName)."""
    try:
        length = wintypes.UINT(0)
        result = ctypes.windll.kernel32.GetCurrentPackageFullName(ctypes.byref(length), None)
        return result != APPMODEL_ERROR_NO_PACKAGE
    except (AttributeError, OSError):
        return False  # Windows quá cũ: không có gói


def _task():
    from winrt.windows.applicationmodel import StartupTask

    async def get():
        return await StartupTask.get_async(TASK_ID)
    return asyncio.run(get())


def startup_enabled():
    try:
        from winrt.windows.applicationmodel import StartupTaskState
        return _task().state in (StartupTaskState.ENABLED, StartupTaskState.ENABLED_BY_POLICY)
    except Exception:
        logging.debug('Cannot read startup task', exc_info=True)
        return False


def set_startup(enable):
    """Bật/tắt. Người dùng đã tắt trong Task Manager › Startup thì app không tự bật lại được (Windows chặn):
    trả về False để giao diện báo người dùng bật ở đó."""
    try:
        from winrt.windows.applicationmodel import StartupTaskState
        task = _task()
        if not enable:
            task.disable()
            return True

        async def request():
            return await task.request_enable_async()
        return asyncio.run(request()) in (StartupTaskState.ENABLED, StartupTaskState.ENABLED_BY_POLICY)
    except Exception:
        logging.warning('Cannot change startup task', exc_info=True)
        return False


def launched_at_startup():
    """Windows mở app lúc đăng nhập (StartupTask): chạy ẩn trong khay như tham số --minimized của bản thường."""
    try:
        from winrt.windows.applicationmodel import AppInstance
        from winrt.windows.applicationmodel.activation import ActivationKind
        args = AppInstance.get_activated_event_args()
        return args is not None and args.kind == ActivationKind.STARTUP_TASK
    except Exception:
        logging.debug('Cannot read activation kind', exc_info=True)
        return False
