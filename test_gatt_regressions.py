import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import ble_battery


class GattRegressionTests(unittest.IsolatedAsyncioTestCase):
    def monitor(self):
        updates = []
        monitor = ble_battery.GattBatteryMonitor(lambda *args: updates.append(args))
        monitor._loop = asyncio.get_running_loop()
        monitor._links = asyncio.Semaphore(3)
        monitor.is_running = True
        return monitor, updates

    def client(self, reads, *, disconnect=False):
        class Client:
            def __init__(self, address, timeout, disconnected_callback):
                self.disconnect_callback = disconnected_callback
                self.is_connected = True
                self.services = SimpleNamespace(get_characteristic=lambda _: SimpleNamespace(properties=[]))
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                self.is_connected = False
            async def read_gatt_char(self, char):
                reads.append(True)
                if disconnect:
                    def lost():
                        self.is_connected = False
                        self.disconnect_callback(self)
                    asyncio.get_running_loop().call_soon(lost)
                return bytes([42 + len(reads) - 1])
        return Client

    async def test_polling_disconnect_publishes_none_without_waiting_60_seconds(self):
        monitor, updates = self.monitor()
        with patch.object(ble_battery, 'BleakClient', self.client([], disconnect=True)):
            task = asyncio.create_task(monitor._session('a', '00:11:22:33:44:55'))
            try:
                await asyncio.wait_for(asyncio.shield(task), .1)
                self.assertEqual(updates[-1], ('a', None))
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_refresh_interrupts_retry_wait(self):
        monitor, updates = self.monitor()
        self.assertTrue(hasattr(monitor, 'trigger_refresh'))
        attempts = asyncio.Queue()
        async def failed(*args):
            attempts.put_nowait(True)
            return False
        monitor._session = failed
        task = asyncio.create_task(monitor._watch('a', 'address'))
        try:
            await asyncio.wait_for(attempts.get(), .1)
            monitor.trigger_refresh()
            await asyncio.wait_for(attempts.get(), .1)
        finally:
            task.cancel()
            await task

    async def test_refresh_reads_active_polling_session_immediately(self):
        monitor, updates = self.monitor()
        self.assertTrue(hasattr(monitor, 'trigger_refresh'))
        reads = []
        with patch.object(ble_battery, 'BleakClient', self.client(reads)):
            task = asyncio.create_task(monitor._watch('a', 'address'))
            try:
                for _ in range(10):
                    await asyncio.sleep(0)
                self.assertEqual(len(reads), 1)
                monitor.trigger_refresh()
                for _ in range(20):
                    await asyncio.sleep(0)
                self.assertEqual(len(reads), 2)
                self.assertEqual(updates[-1], ('a', 43))
            finally:
                task.cancel()
                await task

    async def test_shutdown_waits_for_slow_async_finalizer(self):
        monitor, updates = self.monitor()
        finished = []
        async def session():
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(.75)
                finished.append(True)
        task = asyncio.create_task(session())
        await asyncio.sleep(0)
        monitor._tasks['a'] = task
        with patch.object(monitor._loop, 'stop') as stop:
            monitor._cancel_all()
            await asyncio.sleep(.6)
            stop.assert_not_called()
            await asyncio.sleep(.3)
            self.assertEqual(finished, [True])
            stop.assert_called_once_with()

