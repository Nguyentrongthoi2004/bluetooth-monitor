import asyncio
from pathlib import Path
from types import SimpleNamespace
from app_test_support import TemporaryDirectory
import unittest
from unittest.mock import patch
from winrt.windows.devices.bluetooth.advertisement import BluetoothLEAdvertisementWatcherStatus as Status
import jbl_core
import test_app_lifecycle as lifecycle

class ScannerRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_stopped_windows_receiver_restarts_and_delivers_fresh_data(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as folder:
            updates = []
            core = jbl_core.JblCore(on_update=lambda *args:updates.append(args),state_path=Path(folder)/'state.json')
            core.is_running = True
            attempts = []
            async def yield_once(timeout):
                await asyncio.sleep(0)
            core._wait_for_refresh = yield_once
            class FakeScanner:
                def __init__(self, callback, **options):
                    self.callback = callback
                    self.number = len(attempts)
                    attempts.append(self)
                    self._backend = SimpleNamespace(watcher=SimpleNamespace(status=Status.STOPPED if self.number==0 else Status.STARTED))
                async def __aenter__(self):
                    if self.number:
                        self.callback(SimpleNamespace(address=core.target_address),SimpleNamespace(service_data={jbl_core.FAST_PAIR_SERVICE:bytes.fromhex('003000000011e9335e4364')}))
                        core.is_running = False
                    return self
                async def __aexit__(self,*args):
                    pass
            with patch.object(jbl_core,'BleakScanner',FakeScanner):
                await asyncio.wait_for(core._main_worker(),timeout=.25)
            self.assertEqual(updates[-1][:3],(94,67,100))
            self.assertTrue(core.is_connected)
            self.assertEqual(len(attempts),2)

class MissingDataUiTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown
    def test_unknown_battery_bars_do_not_show_colored_progress(self):
        self.app._apply_headphones_update([{'id': 'jbl', 'name': 'JBL CLUB PRO+ TWS'}])
        self.app._apply_battery_update(None,None,None,{'_fresh':False})
        for bar in (self.app.bar_left,self.app.bar_right,self.app.bar_case):
            self.assertEqual(bar.cget('progress_color'),bar.cget('fg_color'))
    def test_expired_timestamp_is_identified_as_old(self):
        self.app._apply_headphones_update([{'id': 'jbl', 'name': 'JBL CLUB PRO+ TWS'}])
        self.app._apply_battery_update(None,None,None,{'_fresh':False,'_updated_at':'2026-10-13 15:39:38'})
        self.assertIn('cũ',self.app.lbl_updated.cget('text').lower())

if __name__=='__main__':
    unittest.main()
