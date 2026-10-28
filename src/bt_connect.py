"""Kết nối / ngắt tai nghe, loa Bluetooth ĐÃ ghép đôi, như nút Connect/Disconnect trong Cài đặt Windows.

Windows không có API công khai cho việc này. Cách ToothTray và các app tương tự dùng: gửi lệnh
KSPROPERTY_ONESHOT_RECONNECT / DISCONNECT tới driver âm thanh Bluetooth (KS filter) của thiết bị. Driver được
tìm theo địa chỉ Bluetooth nằm trong đường dẫn của nó (…&0b1c2d3e4f05_c00000000#…), không theo tên.
Chỉ áp dụng cho thiết bị âm thanh Bluetooth Classic; chuột, bàn phím, điện thoại không có driver này.
Máy dùng Intel Bluetooth Audio Offload: khi tai nghe đang nối, âm thanh đi qua driver Intel (intcbt…), driver này
nhận lệnh nhưng không ngắt; vì vậy set_connected chờ Windows xác nhận trạng thái thật rồi mới báo.
Mọi lỗi trả về (False, thông báo) để app không bao giờ hỏng vì phần này. Chạy trên luồng nền.
"""
import asyncio
import ctypes
import logging
import re
import time
from ctypes import wintypes

import audio_output as ao
import i18n

IID_IDeviceTopology = '2A07407E-6497-4A18-9787-32F79BD0D98F'
IID_IKsControl = '28F54685-06FD-11D2-B27A-00A0C9223196'
KSPROPSETID_BtAudio = '7FA06C40-B8F6-4C7E-8556-E8C33A12E54D'
KSPROPERTY_ONESHOT_RECONNECT = 0
KSPROPERTY_ONESHOT_DISCONNECT = 1
KSPROPERTY_TYPE_GET = 1
DEVICE_STATEMASK_ALL = 0xF
DEVICE_STATE_ACTIVE = 0x1
DATA_FLOWS = (0, 1)  # eRender (tai nghe/loa), eCapture (micro rảnh tay)


class KSPROPERTY(ctypes.Structure):
    _fields_ = [('set', ao.GUID), ('id', wintypes.ULONG), ('flags', wintypes.ULONG)]


def _compact(address):
    text = re.sub(r'[^0-9A-Fa-f]', '', address or '').lower()
    return text if len(text) == 12 else None


def filter_matches(filter_id, address):
    """Driver này có thuộc đúng thiết bị có địa chỉ này không (so địa chỉ, không so tên)."""
    compact = _compact(address)
    return bool(compact and filter_id and 'bthenum' in filter_id.lower()
                and re.search(rf'[#&]{compact}_', filter_id.lower()))


def _activate(device, iid):
    pointer = ctypes.c_void_p()
    ao._method(device, 3, ctypes.POINTER(ao.GUID), wintypes.DWORD, ctypes.c_void_p,
               ctypes.POINTER(ctypes.c_void_p))(ctypes.byref(ao.GUID.parse(iid)), ao.CLSCTX_ALL, None,
                                                ctypes.byref(pointer))  # IMMDevice::Activate
    if not pointer:
        raise OSError('Activate failed')
    return pointer


def _filter_id(endpoint):
    """Đường dẫn KS filter mà endpoint âm thanh nối tới (IDeviceTopology › IConnector)."""
    topology = _activate(endpoint, IID_IDeviceTopology)
    try:
        connector = ctypes.c_void_p()
        ao._method(topology, 4, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p))(0, ctypes.byref(connector))
        try:
            text = ctypes.c_wchar_p()
            ao._method(connector, 10, ctypes.POINTER(ctypes.c_wchar_p))(ctypes.byref(text))  # GetDeviceIdConnectedTo
            try:
                return text.value
            finally:
                ctypes.windll.ole32.CoTaskMemFree(text)
        finally:
            ao._release(connector)
    finally:
        ao._release(topology)


def _endpoint_state(endpoint):
    state = wintypes.DWORD()
    ao._method(endpoint, 6, ctypes.POINTER(wintypes.DWORD))(ctypes.byref(state))  # IMMDevice::GetState
    return state.value


def can_disconnect(address):
    """Ngắt từ app có tác dụng không: chỉ khi endpoint âm thanh Bluetooth của thiết bị đang hoạt động.

    Máy dùng Intel Bluetooth Audio Offload phát qua driver Intel; endpoint Bluetooth thường thì "không có mặt", lệnh
    ngắt bị bỏ qua. Khi đó app không hiện nút "Ngắt kết nối" (thay vì hiện rồi bấm không được)."""
    if not _compact(address):
        return False
    try:
        ao._init_com()
        enumerator = ao._create(ao.CLSID_MMDeviceEnumerator, ao.IID_IMMDeviceEnumerator)
        try:
            return bool(_filters(enumerator, address, active_only=True))
        finally:
            ao._release(enumerator)
    except Exception:
        logging.debug('Cannot check Bluetooth audio driver', exc_info=True)
        return False


def _filters(enumerator, address, active_only=False):
    found = []
    for flow in DATA_FLOWS:
        collection = ctypes.c_void_p()
        ao._method(enumerator, 3, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))(
            flow, DEVICE_STATEMASK_ALL, ctypes.byref(collection))
        try:
            count = wintypes.UINT()
            ao._method(collection, 3, ctypes.POINTER(wintypes.UINT))(ctypes.byref(count))
            for index in range(count.value):
                endpoint = ctypes.c_void_p()
                ao._method(collection, 4, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p))(index, ctypes.byref(endpoint))
                try:
                    if active_only and _endpoint_state(endpoint) != DEVICE_STATE_ACTIVE:
                        continue
                    filter_id = _filter_id(endpoint)
                except OSError:
                    continue
                finally:
                    ao._release(endpoint)
                if filter_matches(filter_id, address) and filter_id not in found:
                    found.append(filter_id)
        finally:
            ao._release(collection)
    return found


def _send(enumerator, filter_id, command):
    device = ctypes.c_void_p()
    ao._method(enumerator, 5, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p))(filter_id, ctypes.byref(device))
    try:
        control = _activate(device, IID_IKsControl)
        try:
            prop = KSPROPERTY(ao.GUID.parse(KSPROPSETID_BtAudio), command, KSPROPERTY_TYPE_GET)
            returned = wintypes.ULONG()
            ao._method(control, 3, ctypes.POINTER(KSPROPERTY), wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG,
                       ctypes.POINTER(wintypes.ULONG))(ctypes.byref(prop), ctypes.sizeof(prop), None, 0,
                                                       ctypes.byref(returned))  # IKsControl::KsProperty
        finally:
            ao._release(control)
    finally:
        ao._release(device)


VERIFY_SECONDS = {True: 20.0, False: 10.0}  # chờ Windows báo đã nối / đã ngắt
VERIFY_POLL_SECONDS = 0.5
CANNOT_DISCONNECT = 'Máy này không cho app ngắt tai nghe đang phát — hãy ngắt trong Cài đặt Bluetooth'
# Thông báo trả về giữ tiếng Việt (CANNOT_DISCONNECT còn được so sánh); giao diện dịch lúc hiện.
i18n.register({
    CANNOT_DISCONNECT: "This PC doesn't let apps disconnect headphones that are playing — "
                       "disconnect them in Bluetooth settings",
    'Không rõ địa chỉ Bluetooth của thiết bị': "The device's Bluetooth address is unknown",
    'Đã kết nối': 'Connected',
    'Đã ngắt kết nối': 'Disconnected',
    'Tai nghe chưa nối — bật tai nghe, để gần máy rồi thử lại':
        "The headphones didn't connect — turn them on, bring them closer and try again",
    'Chỉ tai nghe/loa Bluetooth mới kết nối được từ app':
        'Only Bluetooth headphones and speakers can be connected from the app',
    'Windows chưa sẵn sàng kết nối thiết bị này — thử lại trong Cài đặt Bluetooth':
        "Windows isn't ready to connect this device — try again in Bluetooth settings",
    'Đã gửi lệnh': 'Command sent',
    'Không kết nối được thiết bị này': "Couldn't connect this device",
})


def is_connected(address):
    """Trạng thái kết nối thật theo Windows (True/False), None nếu không đọc được."""
    from winrt.windows.devices.bluetooth import BluetoothConnectionStatus, BluetoothDevice

    async def read():
        device = await BluetoothDevice.from_bluetooth_address_async(int(_compact(address), 16))
        return None if device is None else device.connection_status == BluetoothConnectionStatus.CONNECTED
    try:
        return asyncio.run(read())
    except Exception:
        logging.debug('Cannot read Bluetooth connection status', exc_info=True)
        return None


def set_connected(address, connect):
    """Kết nối (connect=True) hoặc ngắt, rồi CHỜ Windows xác nhận. Trả về (thành công thật, thông báo).

    Driver nhận lệnh chưa chắc đã làm: máy dùng Intel Bluetooth Audio Offload nhận lệnh ngắt nhưng không ngắt.
    Vì vậy chỉ báo thành công khi Windows thật sự đổi trạng thái."""
    if not _compact(address):
        return False, 'Không rõ địa chỉ Bluetooth của thiết bị'
    if is_connected(address) == connect:
        return True, 'Đã kết nối' if connect else 'Đã ngắt kết nối'
    ok, message = _send_command(address, connect)
    if not ok:
        return ok, message
    deadline = time.monotonic() + VERIFY_SECONDS[connect]
    while time.monotonic() < deadline:
        time.sleep(VERIFY_POLL_SECONDS)
        if is_connected(address) == connect:
            return True, 'Đã kết nối' if connect else 'Đã ngắt kết nối'
    logging.info('Bluetooth %s command for %s had no effect', 'connect' if connect else 'disconnect', address)
    return False, 'Tai nghe chưa nối — bật tai nghe, để gần máy rồi thử lại' if connect else CANNOT_DISCONNECT


def _send_command(address, connect):
    """Gửi lệnh tới driver âm thanh Bluetooth của thiết bị. (đã gửi được, thông báo lỗi)."""
    try:
        ao._init_com()
        enumerator = ao._create(ao.CLSID_MMDeviceEnumerator, ao.IID_IMMDeviceEnumerator)
        try:
            filters = _filters(enumerator, address)
            if not filters:
                return False, 'Chỉ tai nghe/loa Bluetooth mới kết nối được từ app'
            command = KSPROPERTY_ONESHOT_RECONNECT if connect else KSPROPERTY_ONESHOT_DISCONNECT
            sent = 0
            for filter_id in filters:
                try:
                    _send(enumerator, filter_id, command)
                    sent += 1
                except OSError:
                    logging.debug('Bluetooth audio command failed on %s', filter_id, exc_info=True)
            if not sent:
                return False, ('Windows chưa sẵn sàng kết nối thiết bị này — thử lại trong Cài đặt Bluetooth'
                               if connect else CANNOT_DISCONNECT)
            return True, 'Đã gửi lệnh'
        finally:
            ao._release(enumerator)
    except Exception:
        logging.warning('Bluetooth connect/disconnect failed', exc_info=True)
        return False, 'Không kết nối được thiết bị này'
