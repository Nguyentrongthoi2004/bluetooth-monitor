import time
import unittest

import main
import test_app_lifecycle as lifecycle


class HistoryUiTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def test_history_records_every_connected_id_and_offscreen_fast_pair(self):
        self.app._apply_headphones_update([
            {'id': 'jbl', 'name': 'Same', 'battery_level': 80},
            {'id': 'other', 'name': 'Same', 'battery_level': 40}])
        self.app._apply_battery_update(94, 67, 100, {'left': False, 'right': False})
        self.assertEqual(self.app.history.samples('jbl')[-1]['level'], 67)
        self.assertEqual(self.app.history.samples('other')[-1]['level'], 40)
        self.assertIsNone(self.app.left_level)
        self.app._select_headphone('jbl')
        self.assertEqual(self.app._display_level(), (67, False))

    def test_chart_and_remaining_clear_on_source_loss(self):
        self.app._apply_headphones_update([{'id': 'a', 'name': 'A', 'battery_level': 70}])
        self.app.deiconify()  # biểu đồ chỉ vẽ khi bảng đang hiện
        now = time.time()
        self.app.history._samples = [dict(t=now-1200, id='a', name='A', level=80, source='windows', charging=None),
                                     dict(t=now, id='a', name='A', level=70, source='windows', charging=None)]
        self.app._render_history()
        self.assertIn('2 giờ 20 phút', self.app.lbl_remaining.cget('text'))
        self.assertTrue(self.app.history_canvas.winfo_manager())
        self.app._apply_headphones_update([{'id': 'a', 'name': 'A'}])
        self.assertFalse(self.app.lbl_remaining.winfo_manager())
        self.app._apply_headphones_update([])
        self.assertFalse(self.app.history_canvas.winfo_manager())

    def test_chart_draws_only_the_current_source_to_avoid_zigzag(self):
        self.app._apply_headphones_update([{'id': 'a', 'name': 'A', 'battery_level': 90}])
        now = time.time()
        levels = [(93, 'ble'), (90, 'windows'), (93, 'ble'), (90, 'windows'), (93, 'ble')]
        self.app.history._samples = [dict(t=now - 600 * (len(levels) - i), id='a', name='A', level=level,
                                          source=source, charging=None)
                                     for i, (level, source) in enumerate(levels)]
        self.app.history_canvas.pack(fill='x')
        self.app._draw_history()
        canvas = self.app.history_canvas
        lines = [item for item in canvas.find_all() if canvas.type(item) == 'line'
                 and canvas.itemcget(item, 'fill') == main.ACCENT]
        ys = {round(y, 1) for y in canvas.coords(lines[0])[1::2]}
        self.assertEqual(len(ys), 1, 'Only the BLE samples (all 93%) are drawn')

    def test_unbinding_removes_component_history_ownership(self):
        jbl = {'id': 'jbl', 'name': 'JBL', 'battery_level': 80}
        self.app._apply_headphones_update([jbl])
        self.app._apply_battery_update(94, 67, 90, {'left': False, 'right': False})
        self.app._unbind_fast_pair()
        self.app._record_history()
        self.assertEqual(self.app.history.samples('jbl')[-1]['level'], 80)
        self.app._apply_headphones_update([jbl, {'id': 'other', 'name': 'Other'}])
        self.app._select_headphone('jbl')
        self.assertFalse(self.app._showing_components())

    def test_hidden_window_skips_chart_and_redraws_on_show(self):
        self.app._apply_headphones_update([{'id': 'a', 'name': 'A'}])
        self.app.withdraw()
        self.app._apply_gatt_update('a', 87)
        self.assertFalse(self.app.history_canvas.winfo_manager(), 'Nothing drawn while in the tray')
        self.app.show_window()
        self.assertTrue(self.app.history_canvas.winfo_manager())
        self.assertTrue(self.app.history_canvas.find_all())

    def test_first_gatt_sample_resizes_visible_window_for_chart(self):
        self.app._apply_headphones_update([{'id': 'a', 'name': 'A'}])
        self.app.show_window()
        before = self.app.win_height
        self.app._apply_gatt_update('a', 87)
        self.app.update_idletasks()
        self.assertTrue(self.app.history_canvas.winfo_manager())
        self.assertGreater(self.app.win_height, before + 50)
        self.assertEqual(self.app.win_height, self.app.container.winfo_reqheight() + 4)
