"""Quét thiết bị Bluetooth xung quanh (Classic + BLE), giống trang "Thêm thiết bị" của Windows.

Dùng DeviceWatcher trên AssociationEndpoint: thấy cả thiết bị chưa ghép đôi đang ở chế độ tìm kiếm,
kèm cường độ tín hiệu. Callback WinRT chỉ ghi vào bộ nhớ có khóa; giao diện tự đọc snapshot().
"""
import asyncio
import logging
import threading
import time

from winrt.windows.devices.enumeration import (DeviceInformation, DeviceInformationKind, DevicePairingKinds,
                                                DevicePairingResultStatus)
from winrt.windows.foundation import IPropertyValue

import i18n
from safe_text import clean_name

CLASSIC_PROTOCOL = '{e0cbf06c-cd8b-4647-bb8a-263b43f0f974}'
LE_PROTOCOL = '{bb7bb05e-5972-42b5-94fc-76eaa7084d49}'
SELECTOR = (f'(System.Devices.Aep.ProtocolId:="{CLASSIC_PROTOCOL}"'
            f' OR System.Devices.Aep.ProtocolId:="{LE_PROTOCOL}")')
PAIRED_KEY = 'System.Devices.Aep.IsPaired'
CONNECTED_KEY = 'System.Devices.Aep.IsConnected'
SIGNAL_KEY = 'System.Devices.Aep.SignalStrength'
ADDRESS_KEY = 'System.Devices.Aep.DeviceAddress'
MAJOR_KEY = 'System.Devices.Aep.Bluetooth.Cod.Major'
MINOR_KEY = 'System.Devices.Aep.Bluetooth.Cod.Minor'
PROPERTIES = [PAIRED_KEY, CONNECTED_KEY, SIGNAL_KEY, ADDRESS_KEY, MAJOR_KEY, MINOR_KEY]
# Thiết bị không còn phát tín hiệu sau chừng này giây thì coi như đã đi xa.
STALE_SECONDS = 60
MAJOR_KINDS = {1: 'computer', 2: 'phone', 4: 'tv', 5: 'peripheral'}
HEADPHONE_MINOR_CLASSES = {1, 2, 6}  # nhóm âm thanh/video: tai nghe, rảnh tay, headphones

PAIRING_MESSAGES = {
    DevicePairingResultStatus.PAIRED: 'Đã ghép đôi',
    DevicePairingResultStatus.ALREADY_PAIRED: 'Thiết bị đã được ghép đôi từ trước',
    DevicePairingResultStatus.NOT_READY_TO_PAIR: 'Thiết bị chưa ở chế độ ghép đôi',
    DevicePairingResultStatus.AUTHENTICATION_TIMEOUT: 'Hết thời gian xác nhận',
    DevicePairingResultStatus.CONNECTION_REJECTED: 'Thiết bị từ chối kết nối',
    DevicePairingResultStatus.PAIRING_CANCELED: 'Đã hủy ghép đôi',
    DevicePairingResultStatus.REJECTED_BY_HANDLER: 'Đã hủy ghép đôi',
    DevicePairingResultStatus.OPERATION_ALREADY_IN_PROGRESS: 'Đang có một lần ghép đôi khác, thử lại sau vài giây',
    DevicePairingResultStatus.FAILED: 'Ghép đôi thất bại — bật chế độ ghép đôi trên tai nghe rồi thử lại',
}
# Thông báo trả về giữ tiếng Việt; giao diện dịch lúc hiện (i18n.tr_message).
i18n.register({
    'Đã ghép đôi': 'Paired',
    'Thiết bị đã được ghép đôi từ trước': 'Already paired',
    'Thiết bị chưa ở chế độ ghép đôi': "The device isn't in pairing mode",
    'Hết thời gian xác nhận': 'Confirmation timed out',
    'Thiết bị từ chối kết nối': 'The device refused the connection',
    'Đã hủy ghép đôi': 'Pairing canceled',
    'Đang có một lần ghép đôi khác, thử lại sau vài giây': 'Another pairing is in progress. Try again in a few seconds',
    'Ghép đôi thất bại — bật chế độ ghép đôi trên tai nghe rồi thử lại':
        'Pairing failed — put your headphones in pairing mode and try again',
    'Không ghép đôi được thiết bị này': "Couldn't pair this device",
    'Không ghép đôi được ({code})': "Couldn't pair ({code})",
    'Thiết bị đã không còn ghép đôi': 'The device is no longer paired',
    'Đã bỏ ghép đôi': 'Unpaired',
    'Không bỏ ghép đôi được ({code})': "Couldn't unpair ({code})",
    'Không bỏ ghép đôi được thiết bị này': "Couldn't unpair this device",
})


def _read(info, key, getter):
    try:
        value = info.properties.lookup(key)
    except (KeyError, OSError):
        return None
    if value is None:
        return None
    try:
        return getattr(value.as_(IPropertyValue), getter)()
    except (TypeError, ValueError, OSError):
        return None


def _kind(major, minor):
    if major == 4 and minor in HEADPHONE_MINOR_CLASSES:
        return 'headphone'
    return MAJOR_KINDS.get(major, 'other')


def describe(info, seen_at):
    address = _read(info, ADDRESS_KEY, 'get_string')
    return {
        'id': info.id,
        'name': clean_name(info.name),  # thiết bị lạ tự đặt tên: không tin
        'address': address.upper() if address else None,
        'le': LE_PROTOCOL.lower() in info.id.lower() or info.id.startswith('BluetoothLE#'),
        'paired': bool(_read(info, PAIRED_KEY, 'get_boolean')),
        'connected': bool(_read(info, CONNECTED_KEY, 'get_boolean')),
        'signal': _read(info, SIGNAL_KEY, 'get_int32'),
        'kind': _kind(_read(info, MAJOR_KEY, 'get_uint16'), _read(info, MINOR_KEY, 'get_uint16')),
        'seen': seen_at,
    }


class NearbyScanner:
    def __init__(self):
        self._lock = threading.Lock()
        self._infos = {}
        self._seen = {}
        self._watcher = None

    @property
    def running(self):
        return self._watcher is not None

    def start(self):
        if self._watcher is not None:
            return
        with self._lock:
            self._infos.clear()
            self._seen.clear()
        watcher = DeviceInformation.create_watcher_with_kind_aqs_filter_and_additional_properties(
            SELECTOR, PROPERTIES, DeviceInformationKind.ASSOCIATION_ENDPOINT)
        watcher.add_added(self._on_added)
        watcher.add_updated(self._on_updated)
        watcher.add_removed(self._on_removed)
        watcher.start()
        self._watcher = watcher

    def stop(self):
        watcher, self._watcher = self._watcher, None
        if watcher is not None:
            try:
                if watcher.status in (1, 2):  # STARTED, ENUMERATION_COMPLETED
                    watcher.stop()
            except Exception:
                logging.debug('Cannot stop nearby scanner', exc_info=True)

    def _on_added(self, sender, info):
        with self._lock:
            self._infos[info.id] = info
            self._seen[info.id] = time.monotonic()

    def _on_updated(self, sender, update):
        with self._lock:
            info = self._infos.get(update.id)
            if info is not None:
                try:
                    info.update(update)
                except Exception:
                    return
                # Chỉ tín hiệu mới chứng tỏ thiết bị vẫn ở gần.
                if update.properties.has_key(SIGNAL_KEY):
                    self._seen[update.id] = time.monotonic()

    def _on_removed(self, sender, update):
        with self._lock:
            self._infos.pop(update.id, None)
            self._seen.pop(update.id, None)

    def snapshot(self, include_unnamed=False):
        """Danh sách thiết bị: đang kết nối, đã ghép đôi, rồi theo tín hiệu mạnh nhất."""
        now = time.monotonic()
        with self._lock:
            items = [describe(info, self._seen.get(key, 0)) for key, info in self._infos.items()]
        devices = []
        for device in items:
            if not include_unnamed and not device['name']:
                continue
            nearby = device['signal'] is not None and now - device['seen'] <= STALE_SECONDS
            if not (nearby or device['paired']):
                continue
            device['nearby'] = nearby
            devices.append(device)
        # Một thiết bị có thể có cả bản Classic lẫn BLE; giữ bản Classic (âm thanh) nếu trùng địa chỉ.
        unique = {}
        for device in sorted(devices, key=lambda d: d['le']):
            unique.setdefault(device['address'] or device['id'], device)
        return sorted(unique.values(), key=lambda d: (not d['connected'], not d['paired'],
                                                      -(d['signal'] if d['nearby'] else -999),
                                                      d['name'].casefold()))


PIN_TIMEOUT = 60  # giây chờ người dùng so mã; hết giờ coi như không đồng ý


def answer_pairing_request(args, confirm_pin):
    """Trả lời một bước xác nhận ghép đôi của Windows.

    confirm_pin(pin, display) -> bool hỏi người dùng (chặn luồng gọi). Mã so khớp/mã hiển thị là thứ chống
    kẻ gian chen giữa hai thiết bị (MITM), nên KHÔNG BAO GIỜ tự đồng ý khi chưa có người so mã.
    """
    kind = args.pairing_kind
    if kind == DevicePairingKinds.CONFIRM_ONLY:
        args.accept()  # kiểu "chỉ xác nhận" không có mã để so; người dùng đã chủ động bấm "Ghép đôi"
    elif kind == DevicePairingKinds.PROVIDE_PIN:
        args.accept_with_pin('0000')  # tai nghe đời cũ dùng mã cố định 0000
    elif kind in (DevicePairingKinds.DISPLAY_PIN, DevicePairingKinds.CONFIRM_PIN_MATCH) and args.pin and confirm_pin:
        deferral = args.get_deferral()  # giữ yêu cầu mở trong lúc chờ người dùng trả lời
        try:
            confirmed = confirm_pin(args.pin, kind == DevicePairingKinds.DISPLAY_PIN) is True
        except Exception:
            logging.exception('Cannot ask the user to confirm the pairing code')
            confirmed = False
        try:
            if confirmed:
                args.accept()
        finally:
            deferral.complete()
    # Kiểu khác (mật khẩu, không có mã, không ai xác nhận): không trả lời = Windows hủy ghép đôi.


async def _pair(device_id, confirm_pin):
    info = await DeviceInformation.create_from_id_async(device_id)
    if info.pairing.is_paired:
        return DevicePairingResultStatus.ALREADY_PAIRED
    if not info.pairing.can_pair:
        return DevicePairingResultStatus.NOT_READY_TO_PAIR
    # Ghép đôi "cơ bản" từ app desktop thường thất bại (FAILED) với tai nghe vì không ai trả lời bước
    # xác nhận. Ghép tùy chỉnh: app tự trả lời, mã cần so thì hỏi người dùng (answer_pairing_request).
    custom = info.pairing.custom
    token = custom.add_pairing_requested(lambda sender, args: answer_pairing_request(args, confirm_pin))
    try:
        kinds = (DevicePairingKinds.CONFIRM_ONLY | DevicePairingKinds.DISPLAY_PIN
                 | DevicePairingKinds.PROVIDE_PIN | DevicePairingKinds.CONFIRM_PIN_MATCH)
        result = await custom.pair_async(kinds)
    finally:
        custom.remove_pairing_requested(token)
    return result.status


def pair(device_id, confirm_pin=None):
    """Chạy trên luồng nền. Trả về (thành công, thông báo)."""
    try:
        status = asyncio.run(_pair(device_id, confirm_pin))
    except Exception as exc:
        logging.warning('Pairing failed: %r', exc)
        return False, 'Không ghép đôi được thiết bị này'
    ok = status in (DevicePairingResultStatus.PAIRED, DevicePairingResultStatus.ALREADY_PAIRED)
    if not ok:
        logging.warning('Pairing %s ended with %s', device_id, status.name)
    return ok, PAIRING_MESSAGES.get(status, f'Không ghép đôi được ({status.name})')


# --- Thông số qua GATT (BLE): pin chuẩn 0x180F và Device Information 0x180A ---
def _uuid16(short):
    import uuid
    return uuid.UUID(f'0000{short:04x}-0000-1000-8000-00805f9b34fb')


GATT_FIELDS = {
    0x180F: {0x2A19: 'battery'},
    0x180A: {0x2A29: 'manufacturer', 0x2A24: 'model', 0x2A26: 'firmware', 0x2A27: 'hardware',
             0x2A28: 'software'},
}
DETAILS_TIMEOUT = 12


async def _read_details(address):
    from winrt.windows.devices.bluetooth import BluetoothLEDevice, BluetoothCacheMode
    from winrt.windows.devices.bluetooth.genericattributeprofile import GattCommunicationStatus
    device = await BluetoothLEDevice.from_bluetooth_address_async(int(address.replace(':', ''), 16))
    if device is None:
        return {}
    details = {}
    try:
        for service_id, fields in GATT_FIELDS.items():
            services = await device.get_gatt_services_for_uuid_with_cache_mode_async(
                _uuid16(service_id), BluetoothCacheMode.UNCACHED)
            if services.status != GattCommunicationStatus.SUCCESS:
                continue
            for service in services.services:
                try:
                    for char_id, field in fields.items():
                        found = await service.get_characteristics_for_uuid_with_cache_mode_async(
                            _uuid16(char_id), BluetoothCacheMode.UNCACHED)
                        if found.status != GattCommunicationStatus.SUCCESS or not found.characteristics:
                            continue
                        result = await found.characteristics[0].read_value_with_cache_mode_async(
                            BluetoothCacheMode.UNCACHED)
                        if result.status != GattCommunicationStatus.SUCCESS:
                            continue
                        data = bytes(result.value)
                        if field == 'battery':
                            if data and data[0] <= 100:
                                details[field] = data[0]
                        else:
                            text = clean_name(data.decode('utf-8', 'replace'))
                            if text:
                                details[field] = text
                finally:
                    service.close()
    finally:
        device.close()
    return details


def read_details(address):
    """Chạy trên luồng nền. {} nếu thiết bị không có BLE hoặc không trả lời kịp."""
    if not address:
        return {}

    async def run():
        return await asyncio.wait_for(_read_details(address), DETAILS_TIMEOUT)
    try:
        return asyncio.run(run())
    except Exception as exc:
        logging.info('Cannot read GATT details for %s: %r', address, exc)
        return {}


async def _unpair(device_id):
    from winrt.windows.devices.enumeration import DeviceUnpairingResultStatus
    info = await DeviceInformation.create_from_id_async(device_id)
    if not info.pairing.is_paired:
        return True, 'Thiết bị đã không còn ghép đôi'
    result = await info.pairing.unpair_async()
    ok = result.status in (DeviceUnpairingResultStatus.UNPAIRED, DeviceUnpairingResultStatus.ALREADY_UNPAIRED)
    return ok, 'Đã bỏ ghép đôi' if ok else f'Không bỏ ghép đôi được ({result.status.name})'


def unpair(device_id):
    """Chạy trên luồng nền; chỉ gọi sau khi người dùng xác nhận. Trả về (thành công, thông báo)."""
    try:
        return asyncio.run(_unpair(device_id))
    except Exception as exc:
        logging.warning('Unpairing failed: %r', exc)
        return False, 'Không bỏ ghép đôi được thiết bị này'
