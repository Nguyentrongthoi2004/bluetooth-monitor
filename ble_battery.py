"""Standard BLE Battery Service (0x180F / Battery Level 0x2A19) for connected headphones.

Only the remote address of a Windows-connected headphone is contacted; nearby devices
are never adopted by name. A level is published only while the GATT link is up, so a
lost link clears the value instead of leaving an old percentage on screen.
"""
import asyncio
import logging
import re
import threading

from bleak import BleakClient

BATTERY_LEVEL_CHAR = "00002a19-0000-1000-8000-00805f9b34fb"
CONNECT_TIMEOUT = 15.0
RETRY_SECONDS = (20, 60, 180)
# After this many failures retries slow down to SLOW_RETRY_SECONDS.
MAX_FAILURES = len(RETRY_SECONDS)
SLOW_RETRY_SECONDS = 600
POLL_SECONDS = 60
SHUTDOWN_SECONDS = 3.0
MAX_LINKS = 3


def address_from_device_id(device_id):
    """Windows ids look like 'Bluetooth#Bluetooth<local>-<remote>'; return the remote."""
    match = re.search(r"-((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})$", device_id or "")
    return match.group(1).upper() if match else None


def parse_battery_level(data):
    if not data:
        return None
    level = data[0]
    return level if 0 <= level <= 100 else None


class GattBatteryMonitor:
    def __init__(self, on_update):
        self.on_update = on_update
        self.is_running = False
        self._thread = None
        self._loop = None
        self._tasks = {}
        self._links = None
        self._wakes = set()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self.is_running = True
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), daemon=True,
                                        name="GattBattery")
        self._thread.start()
        ready.wait(2)

    def stop(self):
        self.is_running = False
        loop = self._loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(self._cancel_all)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    def set_targets(self, devices):
        """devices: {device_id: address}. Thread-safe."""
        loop = self._loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(self._apply_targets, dict(devices))

    def _run(self, ready):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._links = asyncio.Semaphore(MAX_LINKS)
        ready.set()
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()
            self._loop = None

    def _cancel_all(self):
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            task.cancel()

        async def finish():
            # Let cancelled sessions close their GATT links before the loop stops.
            if tasks:
                await asyncio.wait(tasks, timeout=SHUTDOWN_SECONDS)
            self._loop.stop()
        self._loop.create_task(finish())

    def trigger_refresh(self):
        """Interrupt retry waits and read active polling links now. Thread-safe."""
        loop = self._loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(self._wake_all)

    def _wake_all(self):
        for wake in list(self._wakes):
            wake.set()

    def _apply_targets(self, devices):
        for device_id in list(self._tasks):
            if device_id not in devices:
                self._tasks.pop(device_id).cancel()
                self._publish(device_id, None)
        for device_id, address in devices.items():
            if device_id not in self._tasks and address:
                self._tasks[device_id] = self._loop.create_task(self._watch(device_id, address))

    def _publish(self, device_id, level):
        try:
            self.on_update(device_id, level)
        except Exception:
            logging.exception("GATT battery callback failed")

    async def _wait(self, wake, delay):
        """Sleep up to delay seconds; trigger_refresh() ends the wait early."""
        try:
            await asyncio.wait_for(wake.wait(), delay)
        except asyncio.TimeoutError:
            pass
        wake.clear()

    async def _watch(self, device_id, address):
        failures = 0
        wake = asyncio.Event()
        self._wakes.add(wake)
        try:
            while self.is_running:
                ok = await self._session(device_id, address, wake)
                self._publish(device_id, None)
                failures = 0 if ok else failures + 1
                if failures == MAX_FAILURES:
                    logging.info("No BLE battery service for %s; using Windows battery", address)
                if not failures:
                    delay = 5
                elif failures <= MAX_FAILURES:
                    delay = RETRY_SECONDS[failures - 1]
                else:
                    # Some headsets expose their LE side only at times; keep checking slowly.
                    delay = SLOW_RETRY_SECONDS
                await self._wait(wake, delay)
        except asyncio.CancelledError:
            pass
        finally:
            self._wakes.discard(wake)

    async def _session(self, device_id, address, wake=None):
        """Return True if a battery value was received during this link."""
        received = False
        disconnected = asyncio.Event()
        wake = wake or asyncio.Event()

        def on_notify(_, data):
            level = parse_battery_level(data)
            if level is not None:
                self._publish(device_id, level)

        # MAX_LINKS giới hạn số lần KẾT NỐI cùng lúc (Windows kết nối song song hay lỗi), không phải số link đang
        # mở: giữ suất suốt đời link thì chuột/bàn phím/tay cầm dùng notify chiếm hết, tai nghe không bao giờ tới lượt.
        await self._links.acquire()
        holding = True

        def release_slot():
            nonlocal holding
            if holding:
                holding = False
                self._links.release()

        try:
            client = BleakClient(address, timeout=CONNECT_TIMEOUT,
                                 disconnected_callback=lambda _: disconnected.set())
            async with client:
                char = client.services.get_characteristic(BATTERY_LEVEL_CHAR)
                if char is None:
                    return False
                level = parse_battery_level(await client.read_gatt_char(char))
                if level is None:
                    return False
                received = True
                release_slot()  # đã nối và đọc xong: nhường lượt kết nối cho thiết bị khác
                self._publish(device_id, level)
                logging.info("BLE battery for %s: %s%%", address, level)
                if "notify" in char.properties:
                    await client.start_notify(char, on_notify)
                    await disconnected.wait()
                else:
                    # Without notify, poll gently while the link stays up; a refresh
                    # reads at once and a lost link ends the session immediately.
                    while client.is_connected and not disconnected.is_set():
                        waiters = [asyncio.ensure_future(disconnected.wait()),
                                   asyncio.ensure_future(wake.wait())]
                        try:
                            await asyncio.wait(waiters, timeout=POLL_SECONDS,
                                               return_when=asyncio.FIRST_COMPLETED)
                        finally:
                            for waiter in waiters:
                                waiter.cancel()
                        wake.clear()
                        if disconnected.is_set() or not client.is_connected:
                            break
                        level = parse_battery_level(await client.read_gatt_char(char))
                        if level is not None:
                            self._publish(device_id, level)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logging.debug("BLE battery link to %s failed: %r", address, error)
        finally:
            release_slot()
            if received:
                # Never leave a percentage on screen after the link is gone.
                self._publish(device_id, None)
        return received
