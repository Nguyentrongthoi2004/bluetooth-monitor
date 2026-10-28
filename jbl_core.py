"""Live battery data from Google Fast Pair advertisements.

Format: https://developers.google.com/nearby/fast-pair/specifications/extensions/batterynotification
RACE requests received no battery response on this JBL CLUB PRO+ TWS.
"""
import asyncio
import json
import logging
from pathlib import Path
import threading
import time
from bleak import BleakScanner
from bleak.exc import BleakBluetoothNotAvailableReason
from winrt.windows.devices.bluetooth.advertisement import BluetoothLEAdvertisementWatcherStatus

import i18n

FAST_PAIR_SERVICE = "0000fe2c-0000-1000-8000-00805f9b34fb"
# Trạng thái gửi qua on_status giữ tiếng Việt (main.py so sánh "Bluetooth PC đang tắt"); dịch khi hiện.
i18n.register({
    "Đã thấy tai nghe · chờ dữ liệu pin": "Headphones found · waiting for battery data",
    "Dữ liệu trực tiếp": "Live data",
    "Dữ liệu đã cũ · đang chờ tai nghe": "Data is out of date · waiting for headphones",
    "Tạm dừng quét Fast Pair": "Fast Pair scan paused",
    "Đang tìm dữ liệu tai nghe...": "Looking for headphone data...",
    "Bluetooth PC đang tắt": "PC Bluetooth is off",
    "Đang khôi phục Bluetooth ({error})": "Recovering Bluetooth ({error})",
})
STALE_SECONDS = 20.0


def parse_fast_pair_battery(data):
    """Read length/type fields instead of searching arbitrary bytes for headers."""
    if len(data) < 2 or data[0] != 0:
        return None
    position = 1
    batteries = None
    while position < len(data):
        header = data[position]
        position += 1
        length, field_type = header >> 4, header & 0x0F
        if length == 0 or position + length > len(data):
            return None
        field = data[position:position + length]
        position += length
        if field_type in (3, 4):
            if length != 3 or batteries is not None:
                return None
            values = []
            for raw in field:
                level = raw & 0x7F
                if level == 0x7F:
                    values.append((None, None))
                elif level <= 100:
                    values.append((level, bool(raw & 0x80)))
                else:
                    return None
            batteries = tuple(values)
    return batteries


def fast_pair_filter():
    """Bộ lọc chạy trong Bluetooth stack của Windows: chỉ gói có Service Data của Fast Pair (UUID 0xFE2C).

    Không lọc thì Python phải xử lý mọi gói BLE xung quanh (TV, điện thoại…): đo được ~10 lần nhiều gói hơn và
    ~9 lần tốn CPU hơn, mà vẫn nhận đủ gói pin của tai nghe."""
    from winrt.windows.devices.bluetooth.advertisement import (BluetoothLEAdvertisementBytePattern,
                                                               BluetoothLEAdvertisementFilter)
    from winrt.windows.storage.streams import DataWriter
    pattern = BluetoothLEAdvertisementBytePattern()
    pattern.data_type = 0x16  # Service Data - 16-bit UUID
    pattern.offset = 0
    writer = DataWriter()
    writer.write_bytes(bytes([0x2C, 0xFE]))  # 0xFE2C, little-endian
    pattern.data = writer.detach_buffer()
    advertisement_filter = BluetoothLEAdvertisementFilter()
    advertisement_filter.byte_patterns.append(pattern)
    return advertisement_filter


class JblCore:
    def __init__(self, target_address="00:00:00:00:00:00", on_update=None,
                 on_status=None, state_path=None):
        self.target_address = target_address
        self.on_update = on_update
        self.on_status = on_status
        self.state_path = Path(state_path) if state_path else Path(__file__).with_name("battery_state.json")
        self.is_connected = False
        self.is_running = False
        self.battery_left = self.battery_right = self.battery_case = None
        self.charging_left = self.charging_right = self.charging_case = None
        self.last_updated = None
        self._last_received = None
        self._last_seen = None
        self._last_status = None
        self._last_saved = 0.0
        self._last_saved_values = None
        self._thread = None
        self._loop = None
        self._refresh_event = None
        self._started_at = None
        self._restart_requested = False
        # Scanning every nearby advertisement costs CPU; the app enables it only
        # while the bound JBL is connected.
        self.enabled = True

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        self._started_at = time.monotonic()
        self._thread = threading.Thread(target=self._run_event_loop, daemon=True, name="JblBleWorker")
        self._thread.start()

    def stop(self):
        self.is_running = False
        self.trigger_refresh()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)

    def trigger_refresh(self):
        if self.is_running:
            self._restart_requested = True
        if self._loop and self._loop.is_running() and self._refresh_event:
            try:
                self._loop.call_soon_threadsafe(self._refresh_event.set)
            except RuntimeError:
                pass

    def set_enabled(self, enabled):
        """Thread-safe; the worker reacts within one wait cycle."""
        enabled = bool(enabled)
        if enabled == self.enabled:
            return
        self.enabled = enabled
        if self._loop and self._loop.is_running() and self._refresh_event:
            try:
                self._loop.call_soon_threadsafe(self._refresh_event.set)
            except RuntimeError:
                pass

    def _clear_data(self):
        had_data = self._last_received is not None or any(
            value is not None for value in (self.battery_left, self.battery_right, self.battery_case))
        self._last_received = None
        self.battery_left = self.battery_right = self.battery_case = None
        self.charging_left = self.charging_right = self.charging_case = None
        return had_data

    def _run_event_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._refresh_event = asyncio.Event()
        try:
            self._loop.run_until_complete(self._main_worker())
        except Exception:
            logging.exception("Battery worker failed")
        finally:
            self._loop.close()

    def _set_status(self, fresh, text):
        self.is_connected = fresh
        if self._last_status != (fresh, text):
            self._last_status = (fresh, text)
            if self.on_status:
                self.on_status(fresh, text)

    def _on_advertisement(self, device, advertisement):
        if device.address.casefold() != self.target_address.casefold():
            return
        self._last_seen = time.monotonic()
        data = advertisement.service_data.get(FAST_PAIR_SERVICE)
        batteries = parse_fast_pair_battery(data) if data else None
        if batteries is None:
            if self._last_received is None:
                self._set_status(False, "Đã thấy tai nghe · chờ dữ liệu pin")
            return
        previous = self._last_received
        self._last_received = time.monotonic()
        self.last_updated = time.strftime("%Y-%m-%d %H:%M:%S")
        (self.battery_left, self.charging_left), (self.battery_right, self.charging_right), (self.battery_case, self.charging_case) = batteries
        self._set_status(True, "Dữ liệu trực tiếp")
        self._publish(fresh=True)
        if previous is None and self._started_at is not None:
            logging.info("First live battery packet after %.3fs: L=%s R=%s C=%s charging=%s",
                         time.monotonic()-self._started_at, self.battery_left,
                         self.battery_right, self.battery_case,
                         (self.charging_left,self.charging_right,self.charging_case))
        self._save_state()

    def _publish(self, fresh):
        if self.on_update:
            self.on_update(self.battery_left, self.battery_right, self.battery_case, {
                "left": self.charging_left, "right": self.charging_right,
                "case": self.charging_case, "_fresh": fresh,
                "_updated_at": self.last_updated, "_source": "fast_pair",
            })

    def _save_state(self):
        values = (self.battery_left,self.battery_right,self.battery_case,
                  self.charging_left,self.charging_right,self.charging_case)
        now = time.monotonic()
        if values == self._last_saved_values and now-self._last_saved < 5:
            return
        state = dict(zip(("left","right","case","charging_left","charging_right","charging_case"),values))
        state.update(updated_at=self.last_updated, source="fast_pair", schema_version=2)
        temporary = self.state_path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(state,indent=2),encoding="utf-8")
            temporary.replace(self.state_path)
            self._last_saved_values, self._last_saved = values, now
        except OSError:
            logging.exception("Cannot save battery history")

    def _expire_stale_data(self):
        if self._last_received is not None and time.monotonic()-self._last_received > STALE_SECONDS:
            self._last_received = None
            self.battery_left = self.battery_right = self.battery_case = None
            self.charging_left = self.charging_right = self.charging_case = None
            self._publish(fresh=False)
            self._set_status(False,"Dữ liệu đã cũ · đang chờ tai nghe")

    async def _wait_for_refresh(self, timeout):
        try:
            await asyncio.wait_for(self._refresh_event.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        self._refresh_event.clear()

    async def _main_worker(self):
        retry_seconds = 1.0
        last_error = None
        while self.is_running:
            if not self.enabled:
                if self._clear_data():
                    self._publish(fresh=False)
                self._set_status(False, "Tạm dừng quét Fast Pair")
                await self._wait_for_refresh(5)
                continue
            try:
                self._set_status(False,"Đang tìm dữ liệu tai nghe...")
                # Quét THỤ ĐỘNG: chỉ nghe gói quảng bá (pin Fast Pair nằm sẵn trong đó). Quét chủ động gửi gói hỏi
                # liên tục trên cùng sóng với nhạc Bluetooth, làm tiếng tai nghe bị giật.
                async with BleakScanner(self._on_advertisement, scanning_mode="passive",
                                        AdvertisementFilter=fast_pair_filter()) as scanner:
                    retry_seconds = 1.0
                    last_error = None
                    while self.is_running and self.enabled:
                        if self._restart_requested:
                            self._restart_requested = False
                            logging.info("Restarting Bluetooth receiver on refresh")
                            break
                        watcher = getattr(getattr(scanner, "_backend", None), "watcher", None)
                        if watcher is not None and watcher.status != BluetoothLEAdvertisementWatcherStatus.STARTED:
                            raise RuntimeError("Windows Bluetooth receiver stopped")
                        self._expire_stale_data()
                        await self._wait_for_refresh(.5)
            except Exception as error:
                # Repeated identical failures get one line instead of a traceback each retry.
                if repr(error) != last_error:
                    logging.exception("Bluetooth scanner failed")
                else:
                    logging.debug("Bluetooth scanner still failing: %r", error)
                last_error = repr(error)
                self._clear_data()
                self._publish(fresh=False)
                if getattr(error, "reason", None) == BleakBluetoothNotAvailableReason.POWERED_OFF:
                    self._set_status(False,"Bluetooth PC đang tắt")
                else:
                    self._set_status(False,f"Đang khôi phục Bluetooth ({type(error).__name__})")
                if self.is_running:
                    await self._wait_for_refresh(retry_seconds)
                    retry_seconds = min(retry_seconds*2,3.0)
