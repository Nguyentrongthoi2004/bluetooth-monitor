import unittest
from unittest.mock import Mock, patch
import test_app_lifecycle as lifecycle


class WorkerShutdownTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def test_shutdown_stops_every_worker_even_if_one_stop_raises(self):
        workers = [self.app.hotkey_worker, self.app.connection_monitor,
                   self.app.gatt_monitor, self.app.core]
        for worker in workers:
            worker.stop = Mock()
        workers[0].stop.side_effect = RuntimeError('failed stop')
        with patch.object(self.app, 'destroy'), self.assertLogs(level='ERROR'), self.assertRaises(SystemExit):
            self.app._exit_app()
        for worker in workers:
            worker.stop.assert_called_once_with()
        self.assertTrue(self.app._closing)
