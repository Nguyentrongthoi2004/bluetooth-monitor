import unittest
from unittest.mock import Mock

import apple_pods
import test_app_lifecycle as lifecycle


def packet(status, pods, flags_case, model=(0x0E, 0x20)):
    # loại 0x07, độ dài 0x19, 0x01, model, trạng thái, pin hai tai, cờ sạc|pin hộp, rồi phần mã hóa.
    return bytes([0x07, 0x19, 0x01, *model, status, pods, flags_case]) + bytes(19)


class ParseTests(unittest.TestCase):
    def test_reads_left_right_case_and_charging(self):
        parsed = apple_pods.parse(packet(0x2B, 0x89, 0x26))
        self.assertEqual(parsed['model'], 'AirPods Pro')
        self.assertEqual((parsed['left'], parsed['right'], parsed['case']), (90, 80, 60))
        self.assertEqual(parsed['charging'], {'left': False, 'right': True, 'case': False})

    def test_flipped_packet_swaps_ears(self):
        parsed = apple_pods.parse(packet(0x0B, 0x89, 0x26))
        self.assertEqual((parsed['left'], parsed['right']), (80, 90))
        self.assertEqual(parsed['charging'], {'left': True, 'right': False, 'case': False})

    def test_unknown_levels_and_other_packets(self):
        parsed = apple_pods.parse(packet(0x2B, 0xF9, 0x4F))  # phải không rõ, hộp không rõ, hộp đang sạc
        self.assertEqual((parsed['left'], parsed['right'], parsed['case']), (90, None, None))
        self.assertTrue(parsed['charging']['case'])
        self.assertIsNone(apple_pods.parse(packet(0x2B, 0xFF, 0x0F)), 'No earbud level at all')
        self.assertIsNone(apple_pods.parse(bytes([0x10, 0x05, 0x01, 0x18])), 'Other Apple packet types')
        self.assertIsNone(apple_pods.parse(b''))
        self.assertEqual(apple_pods.parse(packet(0x2B, 0xA0, 0x0A))['left'], 0)
        self.assertEqual(apple_pods.parse(packet(0x2B, 0x0A, 0x0A))['left'], 100)

    def test_unknown_model_still_reads_battery(self):
        self.assertEqual(apple_pods.parse(packet(0x2B, 0x55, 0x05, model=(0x77, 0x20)))['model'],
                         'Tai nghe Apple/Beats')


class ScannerTests(unittest.TestCase):
    def test_best_prefers_strongest_recent_packet_and_expires(self):
        scanner = apple_pods.ApplePodsScanner(lambda parsed: None)
        near, far, old = {'left': 90}, {'left': 10}, {'left': 50}
        scanner._seen = [(100.0, -40, old), (115.0, -60, far), (118.0, -45, near)]
        self.assertEqual(scanner.best(now=120.0), near, 'Old packet is outside the window')
        self.assertEqual(scanner.best(now=133.0), near, 'Recent window empty: last nearby packet')
        self.assertIsNone(scanner.best(now=200.0), 'Everything stale')


class AirPodsUiTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def test_connected_airpods_show_left_right_case_from_advertisements(self):
        scanner = self.app.apple_scanner = Mock(running=False)
        scanner.start.side_effect = lambda: setattr(scanner, 'running', True)
        scanner.stop.side_effect = lambda: setattr(scanner, 'running', False)
        pods = {'id': 'pods', 'name': 'AirPods Pro của Long', 'battery_level': 80}
        self.app._apply_headphones_update([pods])
        scanner.start.assert_called_once()
        self.app._apply_apple_update(apple_pods.parse(packet(0x2B, 0x89, 0x26)))
        self.assertEqual((self.app.left_level, self.app.right_level, self.app.case_level), (90, 80, 60))
        self.assertTrue(self.app.row_left.winfo_manager())
        self.assertIn('AirPods', self.app.lbl_status_dot.cget('text'))
        self.app._apply_apple_update(None)  # gói đã cũ
        self.assertFalse(self.app._showing_components())
        self.app._apply_headphones_update([])
        scanner.stop.assert_called_once()

    def test_non_apple_headphone_never_gets_apple_battery(self):
        self.app.apple_scanner = Mock(running=False)
        self.app._apply_headphones_update([{'id': 'akko', 'name': 'Akko S9 Ultra_BT'}])
        self.app.apple_scanner.start.assert_not_called()
        self.app._apply_apple_update(apple_pods.parse(packet(0x2B, 0x89, 0x26)))
        self.assertIsNone(self.app.left_level)


if __name__ == '__main__':
    unittest.main()
