import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import headphone_monitor


class FakeWatcher:
    def __init__(self, status=1):
        self.status = status
        self.stopped = False

    def stop(self):
        self.stopped = True
        self.status = 4


class DeviceWatcherTests(unittest.IsolatedAsyncioTestCase):
    def monitor(self, create):
        reads = []

        async def reader():
            reads.append(asyncio.get_running_loop().time())
            return []

        watcher = headphone_monitor.HeadphoneConnectionMonitor(lambda *args: None, interval=.05,
                                                               reconcile_interval=30)
        watcher._create_watchers = create
        watcher.is_running = True
        watcher._radios = []  # no real radio lookup in tests
        watcher._radios_read_at = float('inf')  # ...and no periodic re-read either
        return watcher, reads, reader

    async def run_worker(self, watcher, reader, seconds):
        # Patches stay active for the whole test, not just the start-up.
        self.enterContext(patch.object(headphone_monitor, 'read_connected_headphones', reader))
        self.enterContext(patch.object(headphone_monitor, 'EVENT_DEBOUNCE_SECONDS', .01))
        task = asyncio.create_task(watcher._worker())
        await asyncio.sleep(seconds)
        return task

    async def stop(self, watcher, task):
        watcher.is_running = False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_watched_idle_does_not_poll_and_event_refreshes(self):
        fakes = [FakeWatcher()]
        watcher, reads, reader = self.monitor(lambda: fakes)
        task = await self.run_worker(watcher, reader, .2)
        try:
            self.assertEqual(len(reads), 1, 'An idle watched monitor must not poll every interval')
            watcher._on_device_event()  # what a WinRT callback does
            await asyncio.sleep(.1)
            self.assertEqual(len(reads), 2)
        finally:
            await self.stop(watcher, task)
        self.assertTrue(fakes[0].stopped, 'Watchers must be stopped on shutdown')

    async def test_falls_back_to_polling_when_watchers_fail(self):
        def broken():
            raise OSError('no watcher')
        watcher, reads, reader = self.monitor(broken)
        with self.assertLogs(level='WARNING'):
            task = await self.run_worker(watcher, reader, .3)
        try:
            self.assertGreaterEqual(len(reads), 3)
        finally:
            await self.stop(watcher, task)

    async def test_polls_quickly_while_a_new_connection_settles(self):
        watcher, reads, _ = self.monitor(lambda: [FakeWatcher()])
        devices = []

        async def reader():
            reads.append(1)
            return list(devices)
        self.enterContext(patch.object(headphone_monitor, 'SETTLE_SECONDS', .3))
        task = await self.run_worker(watcher, reader, .1)
        try:
            settled = len(reads)
            await asyncio.sleep(.2)
            self.assertEqual(len(reads), settled, 'Startup is not a connection change')
            devices.append({'id': 'akko', 'name': 'Akko'})
            watcher._on_device_event()
            await asyncio.sleep(.2)
            self.assertGreaterEqual(len(reads), settled + 3,
                                    'Battery arrives after connecting; keep reading for a while')
        finally:
            await self.stop(watcher, task)

    async def test_radio_off_reports_no_devices_without_enumerating(self):
        updates = []
        watcher = headphone_monitor.HeadphoneConnectionMonitor(lambda *args: updates.append(args))
        watcher._radios = [(SimpleNamespace(state=headphone_monitor.RadioState.OFF), None)]
        reader = AsyncMock(return_value=[{'id': 'akko', 'name': 'Akko'}])
        with patch.object(headphone_monitor, 'read_connected_headphones', reader):
            await watcher.refresh_once()
            reader.assert_not_called()
            self.assertEqual(updates, [([], headphone_monitor.RADIO_OFF)])
            watcher._radios = [(SimpleNamespace(state=headphone_monitor.RadioState.ON), None)]
            await watcher.refresh_once()
        self.assertEqual(updates[-1], ([{'id': 'akko', 'name': 'Akko'}], None))

    def test_device_event_forces_battery_reread(self):
        watcher = headphone_monitor.HeadphoneConnectionMonitor(lambda *args: None)
        watcher._on_device_event()
        self.assertTrue(watcher._force_battery_read)

    async def test_aborted_watcher_is_recreated(self):
        created = []

        def create():
            created.append(FakeWatcher())
            return [created[-1]]
        watcher, reads, reader = self.monitor(create)
        task = await self.run_worker(watcher, reader, .1)
        try:
            created[0].status = 5  # DeviceWatcherStatus.ABORTED
            watcher._on_device_event()  # the stopped event wakes the worker
            await asyncio.sleep(.1)
            self.assertEqual(len(created), 2)
        finally:
            await self.stop(watcher, task)


if __name__ == '__main__':
    unittest.main()
