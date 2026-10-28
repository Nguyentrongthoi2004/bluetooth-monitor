"""Góp ý qua Google Form / clipboard, nút ủng hộ, và chạy trong gói MSIX (Microsoft Store)."""
import urllib.parse
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main
import msix_runtime
import product
import test_app_lifecycle as lifecycle
import ui_feedback

FORM = ('https://docs.google.com/forms/d/e/abc/viewform?usp=pp_url&entry.1=FB_TYPE&entry.2=FB_MESSAGE'
        '&entry.3=FB_SYSTEM&entry.4=FB_LOG')


class FormLinkTests(unittest.TestCase):
    def test_tokens_are_replaced_and_url_encoded(self):
        link = ui_feedback.form_link(FORM, 'Báo lỗi', 'Pin sai & giật\nnhạc', 'Bluetooth Monitor 4.2', 'log')
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)
        self.assertEqual(query['entry.1'], ['Báo lỗi'])
        self.assertEqual(query['entry.2'], ['Pin sai & giật\nnhạc'])
        self.assertEqual(query['entry.4'], ['log'])
        self.assertNotIn('FB_', link)

    def test_long_log_is_cut_from_the_start(self):
        link = ui_feedback.form_link(FORM, 'Khác', 'x', 's', 'A' * 5000 + 'END')
        log = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)['entry.4'][0]
        self.assertEqual(len(log), ui_feedback.URL_LOG_LIMIT)
        self.assertTrue(log.endswith('END'), 'Newest log lines are kept')

    def test_only_complete_google_form_links_are_used(self):
        self.assertTrue(product.feedback_form_ready(FORM))
        self.assertFalse(product.feedback_form_ready(None))
        self.assertFalse(product.feedback_form_ready(FORM.replace('&entry.4=FB_LOG', '')), 'Missing token')
        self.assertFalse(product.feedback_form_ready(FORM.replace('https://docs.google.com', 'https://evil.example')))
        self.assertIsNone(product.safe_link('javascript:alert(1)'))
        self.assertIsNone(product.safe_link('file:///C:/Windows'))
        self.assertEqual(product.safe_link('https://ko-fi.com/x'), 'https://ko-fi.com/x')


class FeedbackDialogTests(unittest.TestCase):
    setUp = lifecycle.AppLifecycleTests.setUp
    tearDown = lifecycle.AppLifecycleTests.tearDown

    def open(self, text):
        self.app._show_feedback_dialog()
        win = self.app._feedback_win
        win.text.insert('1.0', text)
        return win

    def test_without_form_feedback_goes_to_clipboard_with_redacted_log(self):
        from pathlib import Path
        Path(main.DATA_DIR, 'app.log').write_text('INFO battery for 0B:1C:2D:3E:4F:05: 93%\n', encoding='utf-8')
        with patch.object(product, 'FEEDBACK_FORM_URL', None), patch.object(ui_feedback.webbrowser, 'open') as browser:
            win = self.open('Pin tai phải không hiện')
            win.btn_send.invoke()
        browser.assert_not_called()
        copied = self.app.clipboard_get()
        self.assertIn('Pin tai phải không hiện', copied)
        self.assertIn('[Báo lỗi]', copied)
        self.assertIn('XX:XX:XX:XX:XX:05', copied)
        self.assertNotIn('0B:1C:2D:3E', copied)
        self.assertIsNone(self.app._feedback_win)

    def test_with_form_the_browser_opens_prefilled(self):
        with patch.object(product, 'FEEDBACK_FORM_URL', FORM), \
                patch.object(ui_feedback.webbrowser, 'open') as browser:
            win = self.open('Thêm Galaxy Buds')
            win.kind.set(win.kind.cget('values')[1])
            self.app._feedback_kind_changed()
            self.assertFalse(win.attach_log.get(), 'Ideas do not need a log')
            win.btn_send.invoke()
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(browser.call_args.args[0]).query)
        self.assertEqual(query['entry.1'], ['Ý tưởng'])
        self.assertEqual(query['entry.2'], ['Thêm Galaxy Buds'])
        self.assertIn(main.APP_VERSION, query['entry.3'][0])
        self.assertNotIn('entry.4', query, 'No log when unticked')

    def test_empty_feedback_is_not_sent(self):
        with patch.object(ui_feedback.webbrowser, 'open') as browser:
            win = self.open('   ')
            win.btn_send.invoke()
        browser.assert_not_called()
        self.assertIsNotNone(self.app._feedback_win)

    def test_no_donate_button_without_qr_or_link(self):
        with patch.object(product, 'DONATE_URL', None), patch.object(product, 'DONATE_QR', ()):
            self.app._show_settings_dialog()
            self.assertFalse(hasattr(self.app._settings_win.feedback_box, 'btn_donate'))

    def test_link_only_opens_the_browser(self):
        with patch.object(product, 'DONATE_URL', 'https://ko-fi.com/x'), patch.object(product, 'DONATE_QR', ()), \
                patch.object(ui_feedback.webbrowser, 'open') as browser:
            self.app._show_settings_dialog()
            self.app._settings_win.feedback_box.btn_donate.invoke()
        browser.assert_called_once_with('https://ko-fi.com/x')
        self.assertIsNone(self.app._donate_win)

    def test_qr_dialog_also_offers_the_web_page(self):
        with patch.object(product, 'DONATE_URL', 'https://ko-fi.com/thoinguyen'), \
                patch.object(ui_feedback.webbrowser, 'open') as browser:
            self.app._open_donate()
            self.app._donate_win.btn_web.invoke()
        browser.assert_called_once_with('https://ko-fi.com/thoinguyen')

    def test_shipped_donate_link_is_safe(self):
        self.assertEqual(product.safe_link(product.DONATE_URL), product.DONATE_URL)

    def test_qr_codes_are_shown_large_one_at_a_time(self):
        self.enterContext(patch.object(product, 'DONATE_URL', None))
        self.app._show_settings_dialog()
        self.app._settings_win.feedback_box.btn_donate.invoke()
        win = self.app._donate_win
        self.assertEqual(len(win.images), 2, 'Both QR images are bundled in assets')
        self.assertEqual(win.images[0].cget('size')[0], ui_feedback.QR_DISPLAY_WIDTH)
        self.assertIs(win.qr.cget('image'), win.images[0])
        win.choice.set(win.choice.cget('values')[1])
        self.app._show_qr(1)
        self.assertIs(win.qr.cget('image'), win.images[1])
        self.assertFalse(hasattr(win, 'btn_web'), 'No web link configured')
        win.btn_copy.invoke()
        self.assertEqual(self.app.clipboard_get(), product.DONATE_BANK_TEXT)
        win.btn_close.invoke()
        self.assertIsNone(self.app._donate_win)

    def test_qr_images_keep_full_resolution(self):
        from PIL import Image
        for name, _ in product.DONATE_QR:
            with Image.open(ui_feedback.os.path.join(ui_feedback.ASSETS_DIR, name)) as image:
                self.assertGreaterEqual(min(image.size), 900, f'{name} must not be downscaled (scannability)')

    def test_tray_menu_has_feedback(self):
        texts = [item.text if isinstance(item.text, str) else item.text(item) for item in self.app.tray_icon.menu]
        self.assertTrue(any('góp ý' in text.casefold() for text in texts))


class MsixRuntimeTests(unittest.TestCase):
    def test_not_packaged_when_windows_says_no_package(self):
        kernel = SimpleNamespace(GetCurrentPackageFullName=lambda length, name: msix_runtime.APPMODEL_ERROR_NO_PACKAGE)
        with patch.object(msix_runtime.ctypes, 'windll', SimpleNamespace(kernel32=kernel)):
            self.assertFalse(msix_runtime.is_packaged())
        kernel = SimpleNamespace(GetCurrentPackageFullName=lambda length, name: 122)  # ERROR_INSUFFICIENT_BUFFER
        with patch.object(msix_runtime.ctypes, 'windll', SimpleNamespace(kernel32=kernel)):
            self.assertTrue(msix_runtime.is_packaged())

    def test_this_test_run_is_not_packaged(self):
        self.assertFalse(msix_runtime.is_packaged())

    def test_startup_task_enable_disable(self):
        from winrt.windows.applicationmodel import StartupTaskState
        task = Mock(state=StartupTaskState.DISABLED)

        async def request():
            return StartupTaskState.ENABLED
        task.request_enable_async = request
        with patch.object(msix_runtime, '_task', return_value=task):
            self.assertFalse(msix_runtime.startup_enabled())
            self.assertTrue(msix_runtime.set_startup(True))
            self.assertTrue(msix_runtime.set_startup(False))
        task.disable.assert_called_once()

    def test_user_disabled_startup_cannot_be_forced(self):
        from winrt.windows.applicationmodel import StartupTaskState
        task = Mock()

        async def request():
            return StartupTaskState.DISABLED_BY_USER
        task.request_enable_async = request
        with patch.object(msix_runtime, '_task', return_value=task):
            self.assertFalse(msix_runtime.set_startup(True))

    def test_packaged_app_uses_startup_task_not_registry(self):
        with patch.object(main.msix_runtime, 'is_packaged', return_value=True), \
                patch.object(main.msix_runtime, 'set_startup', return_value=True) as task, \
                patch.object(main.winreg, 'SetValueEx') as registry:
            self.assertTrue(main.set_startup(True))
        task.assert_called_once_with(True)
        registry.assert_not_called()
        with patch.object(main.msix_runtime, 'is_packaged', return_value=True), \
                patch.object(main.msix_runtime, 'startup_enabled', return_value=True), \
                patch.object(main.winreg, 'OpenKey') as registry:
            self.assertTrue(main.is_startup_enabled())
        registry.assert_not_called()

    def test_packaged_app_never_migrates_registry_entries(self):
        with patch.object(main.msix_runtime, 'is_packaged', return_value=True), \
                patch.object(main.winreg, 'OpenKey') as registry:
            main.migrate_startup_entry()
        registry.assert_not_called()


if __name__ == '__main__':
    unittest.main()
