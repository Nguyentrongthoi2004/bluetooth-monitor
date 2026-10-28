"""Liệt kê và đổi thiết bị phát âm thanh mặc định của Windows (loa ↔ tai nghe).

Dùng thẳng COM qua ctypes để không cần thêm thư viện. IPolicyConfig không được Microsoft công bố
nhưng là cách các app như Kopfhörer-Akku, EarTrumpet đổi thiết bị mặc định; mọi lỗi đều được bắt
và trả về False/[] để app không bao giờ hỏng vì phần này.
"""
import ctypes
from ctypes import wintypes
import logging
import os
import threading
import uuid
from xml.etree import ElementTree

import i18n

UNNAMED = 'Thiết bị âm thanh'  # tên thay thế, dịch khi hiện
i18n.register({UNNAMED: 'Audio device'})

CLSCTX_ALL = 0x17
COINIT_APARTMENTTHREADED = 0x2
E_RENDER = 0
DEVICE_STATE_ACTIVE = 0x1
STGM_READ = 0
VT_LPWSTR = 31
ROLES = (0, 1, 2)  # eConsole, eMultimedia, eCommunications

CLSID_MMDeviceEnumerator = 'BCDE0395-E52F-467C-8E3D-C4579291692E'
IID_IMMDeviceEnumerator = 'A95664D2-9614-4F35-A746-DE8DB63617E6'
CLSID_PolicyConfigClient = '870AF99C-171D-4F9E-AF0D-E63DF40C2BC9'
IID_IPolicyConfig = 'F8679F50-850A-41CF-9C72-430F290290C8'


class GUID(ctypes.Structure):
    _fields_ = [('data', ctypes.c_ubyte * 16)]

    @classmethod
    def parse(cls, text):
        guid = cls()
        ctypes.memmove(guid.data, uuid.UUID(text).bytes_le, 16)
        return guid


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [('fmtid', GUID), ('pid', wintypes.DWORD)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [('vt', ctypes.c_ushort), ('reserved', ctypes.c_ushort * 3),
                ('value', ctypes.c_void_p), ('extra', ctypes.c_void_p)]


PKEY_Device_FriendlyName = PROPERTYKEY(GUID.parse('A45C254E-DF1C-4EFD-8020-67D146A850E0'), 14)

_local = threading.local()


def _init_com():
    # Mỗi luồng gọi (Tk, khay pystray) cần COM đã khởi tạo; S_FALSE/đã khởi tạo khác chế độ đều dùng được.
    if getattr(_local, 'ready', False):
        return
    try:
        ctypes.windll.ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    except OSError:
        pass
    _local.ready = True


def _method(pointer, index, *argtypes):
    """Hàm thứ `index` trong vtable của một con trỏ COM."""
    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    prototype = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
    function = prototype(vtable[index])
    return lambda *args: function(pointer, *args)


def _release(pointer):
    if pointer:
        vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])(pointer)


def _create(clsid, iid):
    pointer = ctypes.c_void_p()
    ctypes.windll.ole32.CoCreateInstance(ctypes.byref(GUID.parse(clsid)), None, CLSCTX_ALL,
                                         ctypes.byref(GUID.parse(iid)), ctypes.byref(pointer))
    if not pointer:
        raise OSError('CoCreateInstance failed')
    return pointer


def _device_id(device):
    text = ctypes.c_wchar_p()
    _method(device, 5, ctypes.POINTER(ctypes.c_wchar_p))(ctypes.byref(text))  # IMMDevice::GetId
    try:
        return text.value
    finally:
        ctypes.windll.ole32.CoTaskMemFree(text)


def _device_name(device):
    store = ctypes.c_void_p()
    _method(device, 4, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))(STGM_READ, ctypes.byref(store))
    try:
        value = PROPVARIANT()
        _method(store, 5, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))(
            ctypes.byref(PKEY_Device_FriendlyName), ctypes.byref(value))  # IPropertyStore::GetValue
        try:
            return ctypes.wstring_at(value.value) if value.vt == VT_LPWSTR and value.value else None
        finally:
            ctypes.windll.ole32.PropVariantClear(ctypes.byref(value))
    finally:
        _release(store)


def list_outputs():
    """[(id, tên)] các thiết bị phát đang hoạt động; [] nếu không đọc được."""
    try:
        _init_com()
        enumerator = _create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator)
        try:
            collection = ctypes.c_void_p()
            _method(enumerator, 3, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))(
                E_RENDER, DEVICE_STATE_ACTIVE, ctypes.byref(collection))  # EnumAudioEndpoints
            try:
                count = wintypes.UINT()
                _method(collection, 3, ctypes.POINTER(wintypes.UINT))(ctypes.byref(count))
                outputs = []
                for index in range(count.value):
                    device = ctypes.c_void_p()
                    _method(collection, 4, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p))(
                        index, ctypes.byref(device))
                    try:
                        outputs.append((_device_id(device), _device_name(device) or UNNAMED))
                    finally:
                        _release(device)
                return outputs
            finally:
                _release(collection)
        finally:
            _release(enumerator)
    except Exception:
        logging.debug('Cannot list audio outputs', exc_info=True)
        return []


def default_output_id():
    try:
        _init_com()
        enumerator = _create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator)
        try:
            device = ctypes.c_void_p()
            _method(enumerator, 4, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
                E_RENDER, 1, ctypes.byref(device))  # GetDefaultAudioEndpoint(eRender, eMultimedia)
            try:
                return _device_id(device)
            finally:
                _release(device)
        finally:
            _release(enumerator)
    except Exception:
        logging.debug('Cannot read default audio output', exc_info=True)
        return None


def set_default_output(device_id):
    """Đặt thiết bị phát mặc định cho cả nhạc, phim và cuộc gọi. Trả về True nếu thành công."""
    try:
        _init_com()
        policy = _create(CLSID_PolicyConfigClient, IID_IPolicyConfig)
        try:
            set_default = _method(policy, 13, ctypes.c_wchar_p, ctypes.c_int)  # SetDefaultEndpoint
            for role in ROLES:
                set_default(device_id, role)
            return True
        finally:
            _release(policy)
    except Exception:
        logging.warning('Cannot switch audio output', exc_info=True)
        return False


FXSOUND_SETTINGS = os.path.join(os.environ.get('APPDATA', ''), 'FxSound', 'FxSound.settings')


def fxsound_output_id(path=None):
    """Thiết bị phát thật mà FxSound đang đưa âm thanh ra (FxSound tự ghi trong file cài đặt của nó).

    FxSound là thiết bị ảo: Windows phát vào FxSound, FxSound xử lý rồi phát ra thiết bị này. None nếu không rõ."""
    try:
        root = ElementTree.parse(path or FXSOUND_SETTINGS).getroot()
    except (OSError, ElementTree.ParseError):
        return None
    values = {value.get('name'): value.get('val') for value in root.iter('VALUE')}
    return values.get('output_device_id') or None


def next_output(outputs, current_id):
    """Thiết bị kế tiếp sau thiết bị hiện tại (vòng lại đầu danh sách)."""
    if not outputs:
        return None
    ids = [device_id for device_id, _ in outputs]
    index = ids.index(current_id) if current_id in ids else -1
    return outputs[(index + 1) % len(outputs)]
