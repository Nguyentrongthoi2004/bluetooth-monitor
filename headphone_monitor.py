"""Observe Windows headphone connections independently of battery beacons."""
import asyncio
import logging
import re
import contextvars
import threading
import time

from winrt.windows.devices.bluetooth import BluetoothDevice, BluetoothLEDevice, BluetoothConnectionStatus
from winrt.windows.devices.enumeration import DeviceInformation, DeviceInformationKind
from winrt.windows.devices.radios import Radio, RadioKind, RadioState
from winrt.windows.foundation import IPropertyValue

import i18n
from safe_text import clean_name

CONNECTED_KEY = 'System.Devices.Aep.IsConnected'
MAJOR_KEY = 'System.Devices.Aep.Bluetooth.Cod.Major'
MINOR_KEY = 'System.Devices.Aep.Bluetooth.Cod.Minor'
ADDRESS_KEY = 'System.Devices.Aep.DeviceAddress'
APPEARANCE_KEY = 'System.Devices.Aep.Bluetooth.Le.Appearance'
BATTERY_KEY = '{104EA319-6EE2-4701-BD47-8DDBF425BBE5} 2'
# Reported as the connection "error" when the PC's own Bluetooth is off: Windows can keep
# IsConnected=True on device nodes for a while after the radio is switched off.
RADIO_OFF = 'Bluetooth của máy đang tắt'
READ_FAILED = 'Không đọc được kết nối Bluetooth'
UNNAMED = 'Thiết bị Bluetooth'  # tên thay thế khi thiết bị không có tên
# Các giá trị trên được so sánh trong main.py: giữ tiếng Việt, chỉ dịch lúc hiện.
i18n.register({
    RADIO_OFF: 'Bluetooth is turned off',
    READ_FAILED: "Can't read Bluetooth connections",
    UNNAMED: 'Bluetooth device',
})
BATTERY_QUERY_TIMEOUT = 2.0
# With DeviceWatcher events, a full re-read is only a periodic reconciliation (and the
# refresh of Windows battery levels, which raise no event). Polling every 2 s remains
# the fallback when watchers cannot run.
WATCH_RECONCILE_SECONDS = 30.0
EVENT_DEBOUNCE_SECONDS = 0.3
DISCOVERY_FAILURES_BEFORE_ERROR = 3  # lỗi quét liên tiếp trước khi báo lỗi và xóa danh sách
RADIO_RETRY_SECONDS = 30.0
# Windows fills in the battery level (and settles IsConnected) a few seconds after a
# headset (dis)connects, so poll quickly for a short while after the device set changes.
SETTLE_SECONDS = 20.0
_WATCHER_HEALTHY = (1, 2)  # DeviceWatcherStatus.STARTED, ENUMERATION_COMPLETED
# Audio/video: wearable headset, hands-free, headphones. Not phones or mice.
HEADPHONE_MINOR_CLASSES = {1, 2, 6}


def _battery_value(info):
    value = info.properties.lookup(BATTERY_KEY)
    if value is None:
        return None
    prop = value.as_(IPropertyValue)
    for getter in ('get_uint8', 'get_int16', 'get_uint16', 'get_int32',
                   'get_uint32', 'get_int64', 'get_uint64', 'get_string'):
        try:
            level = int(getattr(prop, getter)())
            return level if 0 <= level <= 100 else None
        except (TypeError, ValueError, OSError):
            continue
    return None


async def read_windows_batteries(include_other=False):
    """Read Windows-reported levels, keyed by the remote Bluetooth address.

    These properties may be cached by Windows; a read is not a new radio sample.
    Never join on display names, which can be duplicated or changed.
    """
    selector = 'System.Devices.DeviceInstanceId:~~"BTHENUM"'
    if include_other:
        selector = '(' + selector + ' OR System.Devices.DeviceInstanceId:~~"BTHLE")'
    nodes = await DeviceInformation.find_all_async_with_kind_aqs_filter_and_additional_properties(
        selector, [BATTERY_KEY],
        DeviceInformationKind.DEVICE)
    levels = {}
    for node in nodes:
        try:
            level = _battery_value(node)
            addresses = set(re.findall(r'(?:^|[\\&_])([0-9A-F]{12})(?=[\\&_]|$)',
                                       node.id.upper()))
            if level is not None and len(addresses) == 1:
                address = addresses.pop()
                levels.setdefault(address, set()).add(level)
        except (KeyError, OSError, TypeError, ValueError):
            continue
    # Conflicting properties do not establish which value belongs on the UI.
    return {address: next(iter(values)) for address, values in levels.items()
            if len(values) == 1}


def _read_property(info, key, getter):
    value = info.properties.lookup(key)
    if value is None:
        return None
    return getattr(value.as_(IPropertyValue), getter)()


# Windows battery properties change rarely; re-reading them every 2s cost most of
# the polling CPU. The monitor installs a cached reader for its own refreshes.
BATTERY_CACHE_SECONDS = 20.0
_battery_reader = contextvars.ContextVar('battery_reader', default=None)


async def read_connected_headphones():
    return await read_connected_devices(False)


def _classic_kind(major, minor):
    if major == 4 and minor in HEADPHONE_MINOR_CLASSES:
        return 'headphone'
    if major == 2:
        return 'phone'
    if major == 5 and minor is not None:
        if minor & 0x20:
            return 'mouse'
        if minor & 0x10:
            return 'keyboard'
        if (minor & 0x0F) in (1, 2):
            return 'gamepad'
    return 'other'


def _ble_kind(appearance):
    # Bluetooth SIG Assigned Numbers: HID keyboard/mouse/joystick/gamepad.
    return {0x3C1: 'keyboard', 0x3C2: 'mouse', 0x3C3: 'gamepad',
            0x3C4: 'gamepad'}.get(appearance, 'other')


async def read_connected_devices(include_other=False):
    devices = await asyncio.wait_for(
        DeviceInformation.find_all_async_aqs_filter_and_additional_properties(
            BluetoothDevice.get_device_selector(), [CONNECTED_KEY, MAJOR_KEY, MINOR_KEY, ADDRESS_KEY]),
        timeout=3)
    headphones = []
    addresses = {}
    for device in devices:
        try:
            connected = _read_property(device, CONNECTED_KEY, 'get_boolean')
            major = _read_property(device, MAJOR_KEY, 'get_uint16')
            minor = _read_property(device, MINOR_KEY, 'get_uint16')
            # Thiết bị khác: chuột/bàn phím/tay cầm (5) và điện thoại (2), ví dụ iPhone đã ghép đôi.
            if connected and ((major == 4 and minor in HEADPHONE_MINOR_CLASSES) or
                              (include_other and major in (2, 5))):
                name = clean_name(device.name, 'Tai nghe Bluetooth')
                headphones.append({'id': device.id, 'name': name})
                if include_other:
                    headphones[-1]['kind'] = _classic_kind(major, minor)
                address = _read_property(device, ADDRESS_KEY, 'get_string')
                if address:
                    address = address.replace(':', '').replace('-', '').upper()
                    if re.fullmatch(r'[0-9A-F]{12}', address):
                        addresses[device.id] = address
        except (KeyError, OSError, TypeError, ValueError):
            # A node can disappear between enumeration and property access.
            continue
    if include_other:
        ble_devices = await asyncio.wait_for(
            DeviceInformation.find_all_async_aqs_filter_and_additional_properties(
                BluetoothLEDevice.get_device_selector_from_connection_status(BluetoothConnectionStatus.CONNECTED),
                [CONNECTED_KEY, ADDRESS_KEY, APPEARANCE_KEY]), timeout=3)
        for device in ble_devices:
            try:
                address = _read_property(device, ADDRESS_KEY, 'get_string')
                address = address.replace(':', '').replace('-', '').upper() if address else None
                if device.id in {d['id'] for d in headphones} or (address and address in addresses.values()):
                    continue
                headphones.append({'id': device.id, 'name': clean_name(device.name, UNNAMED),
                                   'kind': _ble_kind(_read_property(device, APPEARANCE_KEY, 'get_uint16'))})
                if address and re.fullmatch(r'[0-9A-F]{12}', address):
                    addresses[device.id] = address
            except (KeyError, OSError, TypeError, ValueError):
                continue
    if addresses:
        try:
            reader = _battery_reader.get()
            query = (reader(include_other, frozenset(addresses)) if reader else
                     read_windows_batteries(True) if include_other else read_windows_batteries())
            batteries = await asyncio.wait_for(query, timeout=BATTERY_QUERY_TIMEOUT)
        except Exception:
            # Keep connection discovery working, but do not retain an old level.
            logging.debug('Windows battery query failed', exc_info=True)
            batteries = {}
        for device in headphones:
            level = batteries.get(addresses.get(device['id']))
            if level is not None:
                device['battery_level'] = level
    return sorted(headphones, key=lambda device: (device['name'].casefold(), device['id']))


class HeadphoneConnectionMonitor:
    def __init__(self, on_update, interval=2.0, reconcile_interval=WATCH_RECONCILE_SECONDS):
        self.on_update = on_update
        self.interval = interval  # polling fallback
        self.reconcile_interval = reconcile_interval
        self._watchers = []
        self.is_running = False
        self._thread = None
        self._loop = None
        self._task = None
        self._wake = None
        self._last_snapshot = None
        self.include_other = False
        self._battery_cache = None  # (key, monotonic time, levels)
        self._force_battery_read = False
        self._settle_until = 0.0
        self._radios = None  # [(radio, state_changed token)]
        self._radios_read_at = 0.0
        self._failures = 0  # lần quét lỗi liên tiếp

    async def _cached_batteries(self, include_other, device_ids):
        key = (include_other, device_ids)
        now = time.monotonic()
        cache = self._battery_cache
        if (self._force_battery_read or cache is None or cache[0] != key
                or now - cache[1] >= BATTERY_CACHE_SECONDS):
            self._force_battery_read = False
            levels = await (read_windows_batteries(True) if include_other else read_windows_batteries())
            self._battery_cache = (key, now, levels)
            return levels
        return cache[2]

    def set_include_other(self, enabled):
        self.include_other = bool(enabled)
        self.trigger_refresh()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self.is_running = True
        self._last_snapshot = None
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name='HeadphoneConnections')
        self._thread.start()

    def stop(self):
        self.is_running = False
        try:
            if self._loop and self._task:
                self._loop.call_soon_threadsafe(self._task.cancel)
        except RuntimeError:
            pass
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)

    def trigger_refresh(self):
        self._force_battery_read = True
        try:
            if self._loop and self._wake:
                self._loop.call_soon_threadsafe(self._wake.set)
        except RuntimeError:
            pass

    async def _watch_radios(self):
        # Không thấy radio nào (adapter chưa sẵn sàng lúc khởi động máy, dongle USB vừa cắm): đọc lại sau ít lâu.
        if self._radios or (self._radios == [] and time.monotonic() - self._radios_read_at < RADIO_RETRY_SECONDS):
            return
        self._unwatch_radios()
        self._radios_read_at = time.monotonic()
        try:
            radios = [radio for radio in await Radio.get_radios_async() if radio.kind == RadioKind.BLUETOOTH]
            self._radios = [(radio, radio.add_state_changed(lambda sender, args: self._on_device_event()))
                            for radio in radios]
        except Exception:
            logging.debug('Cannot read Bluetooth radio state', exc_info=True)
            self._radios = []

    def _unwatch_radios(self):
        for radio, token in self._radios or ():
            try:
                radio.remove_state_changed(token)
            except Exception:
                pass
        self._radios = None

    def _radio_off(self):
        try:
            return bool(self._radios) and all(radio.state != RadioState.ON for radio, _ in self._radios)
        except Exception:
            return False  # unknown radio state must not hide real connections

    async def refresh_once(self):
        await self._watch_radios()
        if self._radio_off():
            self._publish([], RADIO_OFF)
            return
        error = None
        token = _battery_reader.set(self._cached_batteries)
        try:
            include_other = self.include_other
            devices = await asyncio.wait_for(read_connected_devices(True) if include_other
                                             else read_connected_headphones(), timeout=8)
            if include_other != self.include_other:
                return  # A settings change superseded this in-flight enumeration.
        except Exception as exc:
            self._failures += 1
            if (self._failures < DISCOVERY_FAILURES_BEFORE_ERROR and self._last_snapshot
                    and self._last_snapshot[1] is None):
                # Lỗi chập chờn (hay gặp ngay sau khi máy thức dậy): giữ danh sách cũ, thử lại sau 2 giây thay vì
                # xóa hết thiết bị, ngắt BLE rồi chọn nhầm tai nghe khi chúng "kết nối lại".
                logging.info('Headphone discovery failed (%d), retrying: %r', self._failures, exc)
                self._settle_until = time.monotonic() + SETTLE_SECONDS
                return
            devices = []
            error = READ_FAILED
            if self._last_snapshot != ((), error):
                logging.warning('Headphone discovery failed: %r', exc)
        else:
            self._failures = 0
        finally:
            _battery_reader.reset(token)
        self._publish(devices, error)

    def _publish(self, devices, error):
        snapshot = (tuple((d['id'], d['name'], d.get('battery_level'), d.get('kind')) for d in devices), error)
        if self._last_snapshot and {entry[0] for entry in self._last_snapshot[0]} != {d['id'] for d in devices}:
            self._settle_until = time.monotonic() + SETTLE_SECONDS
        if snapshot != self._last_snapshot:
            self._last_snapshot = snapshot
            self.on_update(devices, error)

    def _run(self):
        asyncio.run(self._worker())

    def _create_watchers(self):
        """Watch Bluetooth connections and Windows battery levels; events only wake the worker."""
        watchers = []
        try:
            self._start_watchers(watchers)
        except Exception:
            # Hỏng giữa chừng: dừng các watcher đã chạy, nếu không mỗi lần thử lại (2 giây) lại rò thêm.
            self._watchers, previous = watchers, self._watchers
            self._stop_watchers()
            self._watchers = previous
            raise
        return watchers

    def _start_watchers(self, watchers):
        battery_selector = ('(System.Devices.DeviceInstanceId:~~"BTHENUM"'
                            ' OR System.Devices.DeviceInstanceId:~~"BTHLE")')
        for selector, prop, kind in (
                (BluetoothDevice.get_device_selector(), CONNECTED_KEY, None),
                (BluetoothLEDevice.get_device_selector_from_connection_status(
                    BluetoothConnectionStatus.CONNECTED), CONNECTED_KEY, None),
                (battery_selector, BATTERY_KEY, DeviceInformationKind.DEVICE)):
            if kind is None:
                watcher = DeviceInformation.create_watcher_aqs_filter_and_additional_properties(
                    selector, [prop])
            else:
                watcher = DeviceInformation.create_watcher_with_kind_aqs_filter_and_additional_properties(
                    selector, [prop], kind)
            for add in (watcher.add_added, watcher.add_updated, watcher.add_removed):
                add(lambda sender, args: self._on_device_event())
            watcher.add_stopped(lambda sender, args: self._on_device_event())
            watchers.append(watcher)
            watcher.start()

    def _stop_watchers(self):
        for watcher in self._watchers:
            try:
                if watcher.status in _WATCHER_HEALTHY:
                    watcher.stop()
            except Exception:
                logging.debug('Cannot stop device watcher', exc_info=True)
        self._watchers = []

    def _watchers_healthy(self):
        try:
            return bool(self._watchers) and all(w.status in _WATCHER_HEALTHY for w in self._watchers)
        except Exception:
            return False

    def _ensure_watchers(self):
        if self._watchers_healthy():
            return True
        self._stop_watchers()
        try:
            self._watchers = self._create_watchers()
        except Exception:
            logging.warning('Device watcher unavailable; polling every %ss', self.interval,
                            exc_info=True)
            self._watchers = []
        return bool(self._watchers)

    def _on_device_event(self):
        # Called on WinRT threads: only hand a wake-up to the asyncio loop.
        # Connection and battery events are exactly when cached levels go stale.
        self._force_battery_read = True
        loop, wake = self._loop, self._wake
        try:
            if loop and wake:
                loop.call_soon_threadsafe(wake.set)
        except RuntimeError:
            pass

    async def _worker(self):
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.current_task()
        self._wake = asyncio.Event()
        try:
            while self.is_running:
                self._wake.clear()
                await self.refresh_once()
                watching = self._ensure_watchers()
                settling = time.monotonic() < self._settle_until
                if settling:
                    self._force_battery_read = True
                try:
                    await asyncio.wait_for(self._wake.wait(),
                                           timeout=self.reconcile_interval if watching and not settling
                                           else self.interval)
                    # Connecting a headset raises a burst of events; read once after it.
                    await asyncio.sleep(EVENT_DEBOUNCE_SECONDS)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass
        finally:
            self._stop_watchers()
            self._unwatch_radios()
            self.is_running = False
            self._loop = self._task = self._wake = None
