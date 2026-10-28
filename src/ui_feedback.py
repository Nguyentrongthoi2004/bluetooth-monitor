"""Góp ý và ủng hộ: người dùng gửi lỗi/ý tưởng qua Google Form (không cần máy chủ), kèm log đã che thông tin riêng.

Chưa cấu hình form (product.FEEDBACK_FORM_URL) thì soạn sẵn góp ý vào clipboard để người dùng tự dán gửi.
"""
import os
import sys
import urllib.parse
import webbrowser

import customtkinter as ctk
from PIL import Image

import i18n
import product
import ui_theme as T
from i18n import tr
from ui_theme import blend

MESSAGE_LIMIT = 2000   # ký tự góp ý
URL_LOG_LIMIT = 1500   # log gửi qua link form (link quá dài trình duyệt/Google cắt); bản clipboard thì đầy đủ
KINDS = ('Báo lỗi', 'Ý tưởng', 'Khác')
# Cùng chỗ main.py tìm icon: bản đóng gói thì assets cạnh mã, chạy từ mã nguồn thì src\ nằm cạnh assets\.
ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')
if not os.path.isdir(ASSETS_DIR):
    ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets')
QR_DISPLAY_WIDTH = 360  # đủ lớn để điện thoại quét từ màn hình (mã có logo/chấm tròn cần nhiều điểm ảnh)

i18n.register({
    'Góp ý': 'Feedback',
    'Góp ý cho Bluetooth Monitor': 'Feedback for Bluetooth Monitor',
    'Báo lỗi': 'Bug', 'Ý tưởng': 'Idea', 'Khác': 'Other',
    'Bạn gặp lỗi gì, hoặc muốn app có thêm gì?': 'What went wrong, or what would you like the app to do?',
    'Kèm log để dễ tìm lỗi (đã che địa chỉ Bluetooth và tên tài khoản Windows)':
        'Attach the log to help find the bug (Bluetooth addresses and Windows account name are hidden)',
    'Gửi': 'Send', 'Hủy': 'Cancel',
    'Hãy viết vài chữ trước khi gửi': 'Please write something first',
    'Đã mở form góp ý': 'Feedback form opened',
    'Kiểm tra rồi bấm Gửi trong trình duyệt. Cảm ơn bạn!': 'Check it and press Submit in your browser. Thank you!',
    'Đã chép góp ý': 'Feedback copied',
    'Dán (Ctrl+V) gửi cho nhà phát triển. Cảm ơn bạn!': 'Paste it (Ctrl+V) to the developer. Thank you!',
    'Ủng hộ tác giả': 'Support the developer',
    'Cảm ơn bạn đã dùng Bluetooth Monitor!': 'Thanks for using Bluetooth Monitor!',
    'App miễn phí. Nếu thấy hữu ích, bạn có thể ủng hộ tác giả bằng cách quét mã bằng app ngân hàng hoặc ví điện '
    'tử. Tùy tâm — không mở khóa thêm tính năng nào.':
        'The app is free. If you find it useful, you can support the developer by scanning a code with a '
        'Vietnamese banking or e-wallet app. Entirely optional — it unlocks nothing.',
    'Quét để chuyển tiền': 'Bank transfer QR',
    'VietinBank · VietQR': 'VietinBank · VietQR',
    'Chép số tài khoản': 'Copy account number',
    'Đã chép số tài khoản': 'Account number copied',
    'Ủng hộ qua web…': 'Support online…',
    'Đóng': 'Close',
    'GÓP Ý & ỦNG HỘ': 'FEEDBACK & SUPPORT',
    'Gửi góp ý…': 'Send feedback…',
    'App miễn phí. Góp ý giúp app tốt hơn; ủng hộ giúp tác giả duy trì app.':
        'The app is free. Feedback makes it better; support keeps it going.',
    '{chars}/{limit} ký tự': '{chars}/{limit} characters',
})


def build_report(kind, message, system, log):
    """Nội dung góp ý dạng chữ (dùng cho clipboard)."""
    parts = [f"[{kind}] {system}", '', message.strip()]
    if log:
        parts += ['', '--- log ---', log]
    return '\n'.join(parts)


def form_link(template, kind, message, system, log):
    """Link Google Form điền sẵn: thay FB_TYPE/FB_MESSAGE/FB_SYSTEM/FB_LOG bằng nội dung đã mã hóa URL."""
    values = {'FB_TYPE': kind, 'FB_MESSAGE': message.strip()[:MESSAGE_LIMIT], 'FB_SYSTEM': system,
              'FB_LOG': (log or '')[-URL_LOG_LIMIT:]}
    link = template
    for token, value in values.items():
        link = link.replace(token, urllib.parse.quote(value, safe=''))
    return link


class FeedbackMixin:
    def _feedback_system(self):
        return (f"{self._app_title()} {self._app_version()} · Windows {sys.getwindowsversion().build} · "
                f"{i18n.language()}")

    def _show_feedback_dialog(self):
        if self._feedback_win is not None and self._feedback_win.winfo_exists():
            self._feedback_win.lift()
            return
        win = self._feedback_win = ctk.CTkToplevel(self)
        win.title(tr('Góp ý'))
        win.configure(fg_color=T.BG)
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", self._close_feedback_dialog)
        self._style_dialog(win)
        ctk.CTkLabel(win, text=tr('Góp ý cho Bluetooth Monitor'), anchor="w", text_color=T.TEXT,
                     font=ctk.CTkFont(size=15, weight="bold")).pack(fill="x", padx=18, pady=(16, 8))
        labels = [tr(kind) for kind in KINDS]
        win.kind = ctk.CTkSegmentedButton(win, values=labels, height=30, selected_color=T.ACCENT,
                                          selected_hover_color=T.ACCENT, unselected_color=T.CARD,
                                          unselected_hover_color=T.CARD_HOVER, text_color=T.TEXT,
                                          command=lambda label: self._feedback_kind_changed())
        win.kind.set(labels[0])
        win.kind.pack(fill="x", padx=18)
        win.kind_value = lambda: KINDS[labels.index(win.kind.get())] if win.kind.get() in labels else KINDS[-1]
        ctk.CTkLabel(win, text=tr('Bạn gặp lỗi gì, hoặc muốn app có thêm gì?'), anchor="w", text_color=T.MUTED,
                     font=ctk.CTkFont(size=12)).pack(fill="x", padx=18, pady=(10, 4))
        win.text = ctk.CTkTextbox(win, height=140, fg_color=T.CARD, border_color=T.BORDER, border_width=1,
                                  text_color=T.TEXT, wrap="word")
        win.text.pack(fill="x", padx=18)
        win.text.bind('<KeyRelease>', lambda event: self._feedback_count())
        win.lbl_count = ctk.CTkLabel(win, text='', anchor="e", text_color=T.DIM, font=ctk.CTkFont(size=11))
        win.lbl_count.pack(fill="x", padx=18)
        win.attach_log = ctk.CTkCheckBox(win, text=tr('Kèm log để dễ tìm lỗi (đã che địa chỉ Bluetooth và tên tài '
                                                      'khoản Windows)'),
                                         text_color=T.MUTED, font=ctk.CTkFont(size=11), fg_color=T.ACCENT,
                                         hover_color=T.ACCENT)
        win.attach_log.select()
        win.attach_log.pack(fill="x", padx=18, pady=(6, 0))
        buttons = ctk.CTkFrame(win, fg_color="transparent")
        buttons.pack(fill="x", padx=18, pady=(12, 16))
        win.btn_send = ctk.CTkButton(buttons, text=tr('Gửi'), width=90, height=30, fg_color=T.ACCENT,
                                     hover_color=blend(T.ACCENT, "#000000", .85), text_color="#ffffff",
                                     command=self._send_feedback)
        win.btn_send.pack(side="right")
        win.btn_cancel = ctk.CTkButton(buttons, text=tr('Hủy'), width=80, height=30, fg_color=T.CARD_HOVER,
                                       hover_color=T.BORDER, text_color=T.TEXT, command=self._close_feedback_dialog)
        win.btn_cancel.pack(side="right", padx=8)
        self._feedback_count()
        self._center_window(win, 440, 420)
        win.text.focus_set()

    def _feedback_kind_changed(self):
        win = self._feedback_win
        # Báo lỗi thì log rất có ích; ý tưởng thì không cần.
        (win.attach_log.select if win.kind_value() == 'Báo lỗi' else win.attach_log.deselect)()

    def _feedback_message(self):
        return self._feedback_win.text.get("1.0", "end").strip()

    def _feedback_count(self):
        win = self._feedback_win
        count = len(self._feedback_message())
        win.lbl_count.configure(text=tr('{chars}/{limit} ký tự', chars=count, limit=MESSAGE_LIMIT),
                                text_color=T.LOW if count > MESSAGE_LIMIT else T.DIM)

    def _send_feedback(self):
        win = self._feedback_win
        message = self._feedback_message()[:MESSAGE_LIMIT]
        if not message:
            win.lbl_count.configure(text=tr('Hãy viết vài chữ trước khi gửi'), text_color=T.WARN)
            return
        kind, system = win.kind_value(), self._feedback_system()
        log = self._feedback_log() if win.attach_log.get() else ''
        if product.feedback_form_ready():
            webbrowser.open(form_link(product.FEEDBACK_FORM_URL, kind, message, system, log))
            title, subtitle = tr('Đã mở form góp ý'), tr('Kiểm tra rồi bấm Gửi trong trình duyệt. Cảm ơn bạn!')
        else:
            self.clipboard_clear()
            self.clipboard_append(build_report(kind, message, system, log))
            title, subtitle = tr('Đã chép góp ý'), tr('Dán (Ctrl+V) gửi cho nhà phát triển. Cảm ơn bạn!')
        self._close_feedback_dialog()
        self._show_toast(title, subtitle, glyph='settings', color=T.GOOD, duration=4000)

    def _close_feedback_dialog(self):
        win, self._feedback_win = self._feedback_win, None
        if win is not None and win.winfo_exists():
            win.destroy()

    @staticmethod
    def _donate_qr():
        """[(đường dẫn ảnh, nhãn)] các mã QR có thật trong assets\\."""
        return [(os.path.join(ASSETS_DIR, name), label) for name, label in product.DONATE_QR
                if os.path.isfile(os.path.join(ASSETS_DIR, name))]

    def _can_donate(self):
        return bool(self._donate_qr() or product.safe_link(product.DONATE_URL))

    def _open_donate(self):
        """Cửa sổ ủng hộ: mã QR (mỗi lần một mã, cỡ lớn để điện thoại quét dễ), số tài khoản, link web."""
        codes = self._donate_qr()
        link = product.safe_link(product.DONATE_URL)
        if not codes:
            if link:
                webbrowser.open(link)
            return
        if self._donate_win is not None and self._donate_win.winfo_exists():
            self._donate_win.lift()
            return
        win = self._donate_win = ctk.CTkToplevel(self)
        win.title(tr('Ủng hộ tác giả'))
        win.configure(fg_color=T.BG)
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", self._close_donate)
        self._style_dialog(win)
        ctk.CTkLabel(win, text=tr('Cảm ơn bạn đã dùng Bluetooth Monitor!'), anchor="w", text_color=T.TEXT,
                     font=ctk.CTkFont(size=15, weight="bold")).pack(fill="x", padx=18, pady=(16, 2))
        ctk.CTkLabel(win, text=tr('App miễn phí. Nếu thấy hữu ích, bạn có thể ủng hộ tác giả bằng cách quét mã bằng '
                                  'app ngân hàng hoặc ví điện tử. Tùy tâm — không mở khóa thêm tính năng nào.'),
                     anchor="w", justify="left", wraplength=360, text_color=T.MUTED,
                     font=ctk.CTkFont(size=12)).pack(fill="x", padx=18, pady=(0, 8))
        win.images = []
        for path, _ in codes:
            with Image.open(path) as source:
                width = QR_DISPLAY_WIDTH
                height = round(source.height * width / source.width)
                win.images.append(ctk.CTkImage(light_image=source.copy(), dark_image=source.copy(),
                                               size=(width, height)))
        labels = [tr(label) for _, label in codes]
        if len(codes) > 1:
            win.choice = ctk.CTkSegmentedButton(win, values=labels, height=30, selected_color=T.ACCENT,
                                                selected_hover_color=T.ACCENT, unselected_color=T.CARD,
                                                unselected_hover_color=T.CARD_HOVER, text_color=T.TEXT,
                                                command=lambda label: self._show_qr(labels.index(label)))
            win.choice.set(labels[0])
            win.choice.pack(fill="x", padx=18, pady=(0, 8))
        win.qr = ctk.CTkLabel(win, text='', image=win.images[0])
        win.qr.pack(padx=18)
        win.qr_index = 0
        buttons = ctk.CTkFrame(win, fg_color="transparent")
        buttons.pack(fill="x", padx=18, pady=(10, 16))
        win.btn_close = ctk.CTkButton(buttons, text=tr('Đóng'), width=80, height=30, fg_color=T.CARD_HOVER,
                                      hover_color=T.BORDER, text_color=T.TEXT, command=self._close_donate)
        win.btn_close.pack(side="right")
        if product.DONATE_BANK_TEXT:
            win.btn_copy = ctk.CTkButton(buttons, text=tr('Chép số tài khoản'), height=30, fg_color=T.ACCENT,
                                         hover_color=blend(T.ACCENT, "#000000", .85), text_color="#ffffff",
                                         command=self._copy_bank_text)
            win.btn_copy.pack(side="left")
        if link:
            win.btn_web = ctk.CTkButton(buttons, text=tr('Ủng hộ qua web…'), height=30, fg_color=T.CARD_HOVER,
                                        hover_color=T.BORDER, text_color=T.TEXT, command=lambda: webbrowser.open(link))
            win.btn_web.pack(side="left", padx=8)
        self._center_window(win, QR_DISPLAY_WIDTH + 60, 640)

    def _show_qr(self, index):
        win = self._donate_win
        win.qr_index = index
        win.qr.configure(image=win.images[index])

    def _copy_bank_text(self):
        self.clipboard_clear()
        self.clipboard_append(product.DONATE_BANK_TEXT)
        self._show_toast(tr('Đã chép số tài khoản'), product.DONATE_BANK_TEXT, glyph='settings', color=T.GOOD,
                         duration=3000)

    def _close_donate(self):
        win, self._donate_win = self._donate_win, None
        if win is not None and win.winfo_exists():
            win.destroy()

    def _build_feedback_section(self, parent):
        """Khối "Góp ý & ủng hộ" trong Cài đặt."""
        box = self._section(parent, tr('GÓP Ý & ỦNG HỘ'))
        ctk.CTkLabel(box, text=tr('App miễn phí. Góp ý giúp app tốt hơn; ủng hộ giúp tác giả duy trì app.'),
                     anchor="w", justify="left", wraplength=300, text_color=T.DIM,
                     font=ctk.CTkFont(size=11)).pack(fill="x", padx=14, pady=(10, 4))
        buttons = ctk.CTkFrame(box, fg_color="transparent")
        buttons.pack(fill="x", padx=14, pady=(0, 12))
        box.btn_feedback = ctk.CTkButton(buttons, text=tr('Gửi góp ý…'), height=30, fg_color=T.ACCENT,
                                         hover_color=blend(T.ACCENT, "#000000", .85), text_color="#ffffff",
                                         command=self._show_feedback_dialog)
        box.btn_feedback.pack(side="left")
        if self._can_donate():
            box.btn_donate = ctk.CTkButton(buttons, text=tr('Ủng hộ tác giả'), height=30, fg_color=T.CARD_HOVER,
                                           hover_color=T.BORDER, text_color=T.TEXT, command=self._open_donate)
            box.btn_donate.pack(side="left", padx=8)
        return box
