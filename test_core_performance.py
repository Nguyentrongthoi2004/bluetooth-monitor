import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import jbl_core


class CorePerformanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_core_leaves_scanner_clears_data_and_idles(self):
        updates, entered, exited = [], [], []
        core = jbl_core.JblCore(on_update=lambda *args: updates.append(args))
        self.assertTrue(hasattr(core, 'set_enabled'))
        core._loop = asyncio.get_running_loop()
        core._refresh_event = asyncio.Event()
        core.is_running = True
        core.battery_left = 42
        core.set_enabled(False)
        class Scanner:
            def __init__(self, callback, **options):
                pass
            async def __aenter__(self):
                entered.append(True)
                return self
            async def __aexit__(self, *args):
                exited.append(True)
        with patch.object(jbl_core, 'BleakScanner', Scanner):
            task = asyncio.create_task(core._main_worker())
            try:
                for _ in range(20):
                    await asyncio.sleep(0)
                self.assertEqual(entered, [])
                self.assertIsNone(core.battery_left)
                core.set_enabled(True)
                for _ in range(20):
                    await asyncio.sleep(0)
                self.assertEqual(len(entered), 1)
                core.set_enabled(False)
                for _ in range(20):
                    await asyncio.sleep(0)
                self.assertEqual(len(exited), 1)
                self.assertEqual(len(entered), 1)
                self.assertEqual(updates[-1][:3], (None, None, None))
            finally:
                core.is_running = False
                core.trigger_refresh()
                await asyncio.wait_for(task, .2)

