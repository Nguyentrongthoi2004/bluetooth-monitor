import time
import unittest
from unittest.mock import patch

import main
import test_app_lifecycle as lifecycle

AKKO = {'id': 'akko', 'name': 'Akko S9 Ultra_BT', 'battery_level': 80}
SPEAKERS = ('spk', 'Speakers (Realtek(R) Audio)')
FX = ('fx', 'FxSound Speakers (FxSound Audio Enhancer)')
HEADPHONES = ('hp', 'Headphones (Akko S9 Ultra_BT)')


class ConnectPopupTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def test_popup_replaces_connect_notification_and_updates_battery(self):
        sent = []
        self.app._notify = lambda message, title: sent.append(message) or True
        self.app.settings.update(popup_on_connect=True, connection_alert=True)
        self.app._apply_headphones_update([])
        self.app._apply_headphones_update([{'id': 'akko', 'name': 'Akko S9 Ultra_BT'}])
        self.app.after_cancel(self.app._announce_timer)
        self.app._announce_connection_changes()  # kết nối đã ổn định
        toast = self.app._toast
        self.assertTrue(toast.winfo_exists())
        self.assertEqual(toast.lbl_title.cget('text'), 'Akko S9 Ultra_BT')
        self.assertIn('đang lấy số pin', toast.lbl_subtitle.cget('text'))
        self.assertEqual(sent, [], 'No duplicate Windows notification')
        # Windows fills in the level a few seconds later; the popup follows it.
        self.app._apply_headphones_update([AKKO])
        self.assertIn('80%', toast.lbl_subtitle.cget('text'))
        self.app._hide_toast()
        self.assertEqual(toast.state(), 'withdrawn')


class AlertTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def test_low_battery_shows_overlay_and_plays_sound_even_if_windows_blocks(self):
        self.app._notify = lambda message, title: False  # e.g. suppressed while gaming
        self.app.settings.update(low_battery_alert=True, low_battery_threshold=20,
                                 alert_overlay=True, alert_sound=True)
        with patch.object(main.winsound, 'PlaySound') as play:
            self.app._apply_headphones_update([dict(AKKO, battery_level=15)])
        play.assert_called_once()
        self.assertIn('15%', self.app._toast.lbl_subtitle.cget('text'))
        self.assertIn('akko', self.app._low_alerted)

    def test_time_alert_once_per_discharge(self):
        sent = []
        self.app._notify = lambda message, title: sent.append(message) or True
        self.app.settings['time_alert_minutes'] = 15
        self.app._apply_headphones_update([dict(AKKO, battery_level=30)])
        with patch.object(main, 'estimate_remaining', return_value=12):
            self.app._check_time_alert()
            self.app._check_time_alert()
        self.assertEqual(len(sent), 1)
        self.assertIn('12 phút', sent[0])
        with patch.object(main, 'estimate_remaining', return_value=60):
            self.app._check_time_alert()
        with patch.object(main, 'estimate_remaining', return_value=10):
            self.app._check_time_alert()
        self.assertEqual(len(sent), 2)
        self.app.settings['time_alert_minutes'] = 0
        with patch.object(main, 'estimate_remaining', return_value=1):
            self.app._check_time_alert()
        self.assertEqual(len(sent), 2)


class AudioOutputTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def toggle(self, current, outputs=(SPEAKERS, FX, HEADPHONES)):
        with patch.object(main.audio_output, 'list_outputs', return_value=list(outputs)), \
                patch.object(main.audio_output, 'default_output_id', return_value=current), \
                patch.object(main.audio_output, 'set_default_output', return_value=True) as switch:
            self.app._toggle_audio_output()
        return switch.call_args[0][0] if switch.called else None

    def test_toggle_goes_to_headphone_then_to_real_speakers(self):
        self.app._apply_headphones_update([AKKO])
        self.assertEqual(self.toggle('fx'), 'hp')
        self.assertEqual(self.toggle('hp'), 'spk')
        self.assertIn('Realtek', self.app._toast.lbl_subtitle.cget('text'))

    def test_without_headphone_output_there_is_nothing_to_switch(self):
        self.assertIsNone(self.toggle('spk', (SPEAKERS, FX)), 'No headphone: nothing to switch to')
        self.assertIsNone(self.toggle('spk', (SPEAKERS,)))

    def test_next_output_wraps(self):
        outputs = [SPEAKERS, FX]
        self.assertEqual(main.audio_output.next_output(outputs, 'fx'), SPEAKERS)
        self.assertEqual(main.audio_output.next_output(outputs, 'missing'), SPEAKERS)
        self.assertIsNone(main.audio_output.next_output([], 'fx'))


class AudioRowTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def run_switch(self, outputs, current):
        state = {'current': current}
        patches = (patch.object(main.audio_output, 'list_outputs', return_value=list(outputs)),
                   patch.object(main.audio_output, 'default_output_id', side_effect=lambda: state['current']),
                   patch.object(main.audio_output, 'set_default_output',
                                side_effect=lambda device_id: state.update(current=device_id) or True))
        for replacement in patches:
            self.enterContext(replacement)
        self.app.show_window()
        return state

    def test_fxsound_is_never_the_speaker_choice(self):
        self.app._apply_headphones_update([AKKO])
        state = self.run_switch([SPEAKERS, FX, HEADPHONES], 'fx')
        self.assertEqual(list(self.app.audio_switch.cget('values')), ['Loa', 'Tai nghe'])
        self.assertEqual(self.app.audio_switch.get(), '', 'FxSound is neither choice')
        self.app._on_audio_switch('Tai nghe')
        self.assertEqual(state['current'], 'hp')
        self.app._on_audio_switch('Loa')
        self.assertEqual(state['current'], 'spk', 'Real speakers, not FxSound')
        self.app._toggle_audio_output()
        self.assertEqual(state['current'], 'hp')
        self.app._toggle_audio_output()
        self.assertEqual(state['current'], 'spk')

    def test_fxsound_feeding_headphones_shows_headphones_and_keeps_enhancement(self):
        # Ảnh người dùng: mặc định là FxSound, công tắc trống. FxSound đang phát ra tai nghe -> "Tai nghe".
        self.app._apply_headphones_update([AKKO])
        main.audio_output.fxsound_output_id.return_value = 'hp'  # bản giả của fixture chung
        state = self.run_switch([SPEAKERS, FX, HEADPHONES], 'fx')
        self.assertEqual(self.app.audio_switch.get(), 'Tai nghe')
        self.assertIn('qua FxSound', self.app.lbl_audio_device.cget('text'))
        self.app._on_audio_switch('Tai nghe')
        self.assertEqual(state['current'], 'fx', 'Already on headphones through FxSound: nothing changes')
        self.app._on_audio_switch('Loa')
        self.assertEqual(state['current'], 'spk')
        self.assertEqual(self.app.audio_switch.get(), 'Loa')
        self.app._on_audio_switch('Tai nghe')
        self.assertEqual(state['current'], 'fx', 'Back to headphones goes back through FxSound')
        self.app._toggle_audio_output()
        self.assertEqual(state['current'], 'spk')

    def test_fxsound_feeding_speakers_shows_speakers(self):
        self.app._apply_headphones_update([AKKO])
        main.audio_output.fxsound_output_id.return_value = 'spk'
        state = self.run_switch([SPEAKERS, FX, HEADPHONES], 'fx')
        self.assertEqual(self.app.audio_switch.get(), 'Loa')
        self.app._on_audio_switch('Tai nghe')
        self.assertEqual(state['current'], 'hp', 'FxSound feeds the speakers, so headphones are direct')

    def test_without_enhancer_switch_is_speaker_vs_headphone(self):
        self.app._apply_headphones_update([AKKO])
        state = self.run_switch([SPEAKERS, HEADPHONES], 'spk')
        self.assertEqual(list(self.app.audio_switch.cget('values')), ['Loa', 'Tai nghe'])
        self.assertEqual(self.app.audio_switch.get(), 'Loa')
        self.app._on_audio_switch('Tai nghe')
        self.assertEqual(state['current'], 'hp')
        self.app._on_audio_switch('Loa')
        self.assertEqual(state['current'], 'spk')

    def test_setting_and_compact_mode_hide_the_row(self):
        with patch.object(main.audio_output, 'list_outputs', return_value=[SPEAKERS, FX]):
            self.app.show_window()
            self.assertTrue(self.app.audio_row.winfo_manager())
            self.app._set_setting('show_audio_control', False)
            self.assertFalse(self.app.audio_row.winfo_manager())
            self.app._set_setting('show_audio_control', True)
            self.assertTrue(self.app.audio_row.winfo_manager())
            self.app._set_setting('compact_mode', True)
            self.assertFalse(self.app.audio_row.winfo_manager())

    def test_switch_disabled_without_headphone_output(self):
        with patch.object(main.audio_output, 'list_outputs', return_value=[SPEAKERS, FX]),                 patch.object(main.audio_output, 'default_output_id', return_value='fx'):
            self.app.show_window()
        self.assertEqual(self.app.audio_switch.cget('state'), 'disabled')


class FxSoundSettingsTests(unittest.TestCase):
    def read(self, content):
        from pathlib import Path
        from app_test_support import TemporaryDirectory
        with TemporaryDirectory() as folder:
            path = Path(folder, 'FxSound.settings')
            if content is not None:
                path.write_text(content, encoding='utf-8')
            return main.audio_output.fxsound_output_id(str(path))

    def test_reads_output_device_from_fxsound_settings(self):
        content = ('<?xml version="1.0" encoding="UTF-8"?>\n<PROPERTIES>\n'
                   '  <VALUE name="output_device_id" val="{0.0.0.00000000}.{95cb0ce2}"/>\n'
                   '  <VALUE name="output_device_name" val="Headphones (JBL CLUB PRO+ TWS)"/>\n</PROPERTIES>')
        self.assertEqual(self.read(content), '{0.0.0.00000000}.{95cb0ce2}')

    def test_missing_or_broken_settings_mean_unknown(self):
        self.assertIsNone(self.read(None))
        self.assertIsNone(self.read('<PROPERTIES><VALUE name="x"'))
        self.assertIsNone(self.read('<PROPERTIES></PROPERTIES>'))


class SettingsTests(unittest.TestCase):
    def test_time_alert_choice_is_validated(self):
        self.assertIn('time_alert_minutes', main.DEFAULT_SETTINGS)
        self.assertIn(main.DEFAULT_SETTINGS['time_alert_minutes'], main.TIME_ALERT_CHOICES)


if __name__ == '__main__':
    unittest.main()
