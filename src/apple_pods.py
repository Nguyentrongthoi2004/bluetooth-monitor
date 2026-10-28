"""Pin trái/phải/hộp của AirPods và Beats từ gói quảng bá BLE "proximity pairing" của Apple.

AirPods tự phát gói này (mã hãng 0x004C, loại 0x07), đọc được mà không cần kết nối GATT. Địa chỉ
quảng bá là ngẫu nhiên nên không ghép chắc chắn được với đúng chiếc đã kết nối: chỉ nhận gói rất gần
máy (tín hiệu mạnh) và lấy gói mạnh nhất gần đây — cách OpenPods/AirPodsDesktop vẫn dùng.
"""
import logging
import threading
import time

APPLE_COMPANY_ID = 0x004C
PROXIMITY_TYPE = 0x07
MIN_LENGTH = 9  # loại, độ dài, 0x01, model (2), trạng thái, pin hai tai, cờ sạc + pin hộp
# Gói yếu hơn mức này coi như AirPods của người khác.
MIN_RSSI = -65
WINDOW_SECONDS = 10  # chọn gói mạnh nhất trong khoảng này
STALE_SECONDS = 30   # không còn gói nào gần thì bỏ số pin
MODELS = {
    0x0220: 'AirPods', 0x0F20: 'AirPods 2', 0x1320: 'AirPods 3', 0x0E20: 'AirPods Pro',
    0x1420: 'AirPods Pro 2', 0x2420: 'AirPods Pro 2', 0x0A20: 'AirPods Max',
    0x0B20: 'Powerbeats Pro', 0x0520: 'BeatsX', 0x1020: 'Beats Flex', 0x0620: 'Beats Solo3',
    0x0920: 'Beats Studio3', 0x1120: 'Beats Studio Buds', 0x0320: 'Powerbeats3',
}


def _level(nibble):
    # 0..10 là mức 0..100%; 15 là "không có" (tai đang ở trong hộp đóng hoặc mất kết nối).
    return nibble * 10 if nibble <= 10 else None


def parse(data):
    """Dữ liệu sau mã hãng Apple -> dict pin, hoặc None nếu không phải gói proximity pairing."""
    data = bytes(data)
    if len(data) < MIN_LENGTH or data[0] != PROXIMITY_TYPE or data[1] < MIN_LENGTH - 2:
        return None
    model = (data[3] << 8) | data[4]
    status, pods, flags_case = data[5], data[6], data[7]
    # Gói có thể phát từ tai trái hoặc phải; bit 0x20 của trạng thái cho biết thứ tự hai nửa byte.
    flipped = not status & 0x20
    left_nibble, right_nibble = (pods >> 4, pods & 0x0F) if flipped else (pods & 0x0F, pods >> 4)
    charging = flags_case >> 4
    result = {
        'model': MODELS.get(model, 'Tai nghe Apple/Beats'),
        'left': _level(left_nibble),
        'right': _level(right_nibble),
        'case': _level(flags_case & 0x0F),
        'charging': {
            'left': bool(charging & (0b10 if flipped else 0b01)),
            'right': bool(charging & (0b01 if flipped else 0b10)),
            'case': bool(charging & 0b100),
        },
    }
    if result['left'] is None and result['right'] is None:
        return None
    return result


class ApplePodsScanner:
    """Quét gói Apple chỉ khi đang có AirPods/Beats kết nối; bộ lọc mã hãng chạy trong Bluetooth stack."""

    def __init__(self, on_update):
        self.on_update = on_update  # gọi từ luồng WinRT/timer; app tự chuyển về luồng giao diện
        self._watcher = None
        self._lock = threading.Lock()
        self._seen = []  # [(monotonic, rssi, parsed)]
        self._timer = None
        self._last = None
        self._generation = 0

    @property
    def running(self):
        return self._watcher is not None

    def start(self):
        if self._watcher is not None:
            return
        from winrt.windows.devices.bluetooth.advertisement import (
            BluetoothLEAdvertisementWatcher, BluetoothLEManufacturerData, BluetoothLEScanningMode)
        watcher = BluetoothLEAdvertisementWatcher()
        watcher.scanning_mode = BluetoothLEScanningMode.PASSIVE
        apple = BluetoothLEManufacturerData()
        apple.company_id = APPLE_COMPANY_ID
        watcher.advertisement_filter.advertisement.manufacturer_data.append(apple)
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
                logging.debug('Cannot stop Apple scanner', exc_info=True)
        with self._lock:
            self._seen.clear()
        self._last = None

    def _on_received(self, sender, args):
        try:
            rssi = args.raw_signal_strength_in_dbm
            if rssi < MIN_RSSI:
                return
            for section in args.advertisement.get_manufacturer_data_by_company_id(APPLE_COMPANY_ID):
                parsed = parse(bytes(section.data))
                if parsed:
                    with self._lock:
                        self._seen.append((time.monotonic(), rssi, parsed))
        except Exception:
            logging.debug('Bad Apple advertisement', exc_info=True)

    def best(self, now=None):
        """Gói mạnh nhất trong WINDOW_SECONDS; None nếu đã quá STALE_SECONDS không thấy gói nào gần."""
        now = time.monotonic() if now is None else now
        with self._lock:
            self._seen = [entry for entry in self._seen if now - entry[0] <= STALE_SECONDS]
            recent = [entry for entry in self._seen if now - entry[0] <= WINDOW_SECONDS]
            pool = recent or self._seen
            return max(pool, key=lambda entry: entry[1])[2] if pool else None

    def _schedule(self, generation=None):
        generation = self._generation if generation is None else generation
        # Mỗi lần start() là một "thế hệ": tick của lần chạy trước (stop rồi start nhanh) tự dừng, không nhân đôi.
        if self._watcher is None or generation != self._generation:
            return
        self._timer = threading.Timer(2.0, self._tick, args=(generation,))
        self._timer.daemon = True
        self._timer.start()

    def _tick(self, generation):
        if generation != self._generation:
            return
        best = self.best()
        if best != self._last:
            self._last = best
            try:
                self.on_update(best)
            except Exception:
                logging.exception('Apple battery update failed')
        self._schedule(generation)
