"""Pin trái/phải/hộp của tai nghe true wireless bất kỳ từ gói quảng bá Fast Pair (Sony, Pixel Buds, JBL…).

Gói Fast Pair không mang định danh Windows của tai nghe, nên không tự gán theo tên: mỗi tai nghe được người dùng
liên kết một lần với địa chỉ quảng bá của nó, bằng cách chọn trong danh sách tai nghe đang phát pin gần máy (đối chiếu
số pin). Tai nghe chụp tai chỉ phát một mức pin (không có trái/phải/hộp) nên không bao giờ có trong danh sách.
Quét thụ động và lọc ngay trong Windows (chỉ gói Fast Pair) để không làm nhạc Bluetooth bị giật, không tốn CPU.
"""
import logging
import threading
import time

from jbl_core import fast_pair_filter, parse_fast_pair_battery

SERVICE_DATA = 0x16
FAST_PAIR_UUID = bytes([0x2C, 0xFE])  # 0xFE2C, little-endian
STALE_SECONDS = 30       # quá lâu không thấy gói pin của tai nghe đã liên kết: bỏ số pin chi tiết
CANDIDATE_SECONDS = 20   # tai nghe phát pin trong khoảng này mới hiện trong danh sách chọn
TICK_SECONDS = 2.0


def format_address(value):
    text = f'{value:012X}'
    return ':'.join(text[index:index + 2] for index in range(0, 12, 2))


def battery_payload(sections):
    """Phần dữ liệu Fast Pair (sau UUID 0xFE2C) trong các data section của một gói quảng bá, hoặc None."""
    for section in sections:
        if section.data_type == SERVICE_DATA:
            data = bytes(section.data)
            if data[:2] == FAST_PAIR_UUID:
                return data[2:]
    return None


class FastPairScanner:
    def __init__(self, on_update):
        self.on_update = on_update  # on_update(address, batteries | None), gọi từ luồng nền
        self.watched = set()        # địa chỉ của các tai nghe đã liên kết đang kết nối
        self._watcher = None
        self._lock = threading.Lock()
        self._seen = {}             # địa chỉ -> (monotonic, rssi, batteries)
        self._published = {}
        self._timer = None
        self._generation = 0

    @property
    def running(self):
        return self._watcher is not None

    def set_watched(self, addresses):
        """Tai nghe đã liên kết đang kết nối. Tai nghe vừa rời danh sách quên luôn giá trị đã gửi: kết nối lại thì
        gửi lại ngay, kể cả khi số pin không đổi."""
        self.watched = set(addresses)
        for address in list(self._published):
            if address not in self.watched:
                del self._published[address]

    def start(self):
        if self._watcher is not None:
            return
        from winrt.windows.devices.bluetooth.advertisement import (BluetoothLEAdvertisementWatcher,
                                                                   BluetoothLEScanningMode)
        watcher = BluetoothLEAdvertisementWatcher()
        watcher.scanning_mode = BluetoothLEScanningMode.PASSIVE
        watcher.advertisement_filter = fast_pair_filter()
        watcher.add_received(self._on_received)
        watcher.start()
        self._watcher = watcher
        self._schedule()

    def stop(self):
        self._generation += 1
        watcher, self._watcher = self._watcher, None
        if self._timer:
            self._timer.cancel()
            self._timer = None
        if watcher is not None:
            try:
                watcher.stop()
            except Exception:
                logging.debug('Cannot stop Fast Pair scanner', exc_info=True)
        with self._lock:
            self._seen.clear()
        self._published.clear()

    def _on_received(self, sender, args):
        try:
            payload = battery_payload(args.advertisement.data_sections)
            batteries = parse_fast_pair_battery(payload) if payload else None
            if batteries is None:
                return
            with self._lock:
                self._seen[format_address(args.bluetooth_address)] = (
                    time.monotonic(), args.raw_signal_strength_in_dbm, batteries)
        except Exception:
            logging.debug('Bad Fast Pair advertisement', exc_info=True)

    def latest(self, address, now=None):
        now = time.monotonic() if now is None else now
        with self._lock:
            entry = self._seen.get(address)
        return entry[2] if entry and now - entry[0] <= STALE_SECONDS else None

    def candidates(self, now=None):
        """[(địa chỉ, rssi, batteries)] tai nghe vừa phát pin trái/phải/hộp, tín hiệu mạnh nhất trước."""
        now = time.monotonic() if now is None else now
        with self._lock:
            items = [(address, rssi, batteries) for address, (seen, rssi, batteries) in self._seen.items()
                     if now - seen <= CANDIDATE_SECONDS]
        return sorted(items, key=lambda item: -(item[1] if item[1] is not None else -999))

    def _schedule(self, generation=None):
        generation = self._generation if generation is None else generation
        # Tick của lần chạy trước (stop rồi start ngay) không được nối thêm một chuỗi hẹn giờ thứ hai.
        if self._watcher is None or generation != self._generation:
            return
        self._timer = threading.Timer(TICK_SECONDS, self._tick, args=(generation,))
        self._timer.daemon = True
        self._timer.start()

    def _prune(self, now):
        with self._lock:
            for address in [key for key, entry in self._seen.items() if now - entry[0] > STALE_SECONDS]:
                del self._seen[address]

    def _tick(self, generation):
        if generation != self._generation:
            return
        self._prune(time.monotonic())  # địa chỉ cũ (tai nghe đi xa, địa chỉ xoay vòng) không tích lũy cả ngày
        for address in list(self.watched):
            value = self.latest(address)
            if self._published.get(address, 'never') != value:
                self._published[address] = value
                try:
                    self.on_update(address, value)
                except Exception:
                    logging.exception('Fast Pair battery update failed')
        self._schedule(generation)
