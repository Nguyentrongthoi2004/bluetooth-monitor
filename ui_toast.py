"""Bảng nổi nhỏ góc phải dưới: popup khi kết nối và cảnh báo pin (hiện cả khi chơi game)."""
import ctypes
import logging
import tkinter as tk

import customtkinter as ctk

import i18n
import ui_theme as T
from i18n import tr
from ui_theme import blend, get_work_area, level_color

i18n.register({
    'Đã kết nối · {level}%': 'Connected · {level}%',
    'Đã kết nối · đang lấy số pin…': 'Connected · getting battery level…',
})


class ToastWindow(ctk.CTkToplevel):
    """Bảng nhỏ không viền, tự ẩn: popup khi kết nối và cảnh báo pin (hiện cả khi đang chơi game)."""
    _deactivate_windows_window_header_manipulation = True




class ToastMixin:
    # --- BẢNG NỔI ---
    def _window_visible(self):
        return self.state() == 'normal' and bool(self.winfo_viewable())

    def _show_toast(self, title, subtitle, glyph='headphone', color=None, duration=5000, device_id=None):
        if self._closing:
            return
        win = self._toast
        if not win or not win.winfo_exists():
            win = self._toast = ToastWindow(self)
            win.withdraw()
            win.overrideredirect(True)
            win.attributes('-topmost', True)
            win.configure(fg_color=T.BORDER)
            card = ctk.CTkFrame(win, fg_color=T.CARD, corner_radius=0)
            card.pack(fill='both', expand=True, padx=1, pady=1)
            win.icon = ctk.CTkLabel(card, text='', width=42, height=42, corner_radius=12)
            win.icon.pack(side='left', padx=(14, 12), pady=14)
            text = ctk.CTkFrame(card, fg_color='transparent')
            text.pack(side='left', fill='x', expand=True, padx=(0, 16))
            win.lbl_title = ctk.CTkLabel(text, text='', anchor='w', width=230, wraplength=230, justify='left',
                                         font=ctk.CTkFont(size=14, weight='bold'), text_color=T.TEXT)
            win.lbl_title.pack(fill='x')
            win.lbl_subtitle = ctk.CTkLabel(text, text='', anchor='w', width=230, wraplength=230,
                                            justify='left', font=ctk.CTkFont(size=12))
            win.lbl_subtitle.pack(fill='x')
            for widget in (win, card, win.icon, text, win.lbl_title, win.lbl_subtitle):
                widget.bind('<Button-1>', lambda event: (self._hide_toast(), self.show_window()))
        win.device_id = device_id
        win.icon.configure(image=self._glyph(glyph, 20, color or T.ACCENT),
                           fg_color=blend(color or T.ACCENT, T.CARD, .16))
        win.lbl_title.configure(text=title)
        win.lbl_subtitle.configure(text=subtitle, text_color=color or T.MUTED)
        win.update_idletasks()
        width, height = win.winfo_reqwidth(), win.winfo_reqheight()
        area = get_work_area() or (0, 0, win.winfo_screenwidth(), win.winfo_screenheight())
        # Vị trí tính bằng pixel thật; CTk.geometry sẽ nhân thêm hệ số DPI nên gọi bản của Tk.
        tk.Toplevel.geometry(win, f'+{area[2] - width - 16}+{area[3] - height - 16}')
        self._make_no_activate(win)
        win.deiconify()
        win.lift()
        self._make_no_activate(win)
        if self._toast_timer:
            self.after_cancel(self._toast_timer)
        self._toast_timer = self.after(duration, self._hide_toast)

    def _make_no_activate(self, window):
        # Không cướp focus của game/video khi bảng nổi xuất hiện, và không hiện trên taskbar.
        try:
            hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, style | 0x08000000 | 0x00000080)
        except Exception:
            logging.debug('Cannot set toast window style', exc_info=True)

    def _hide_toast(self):
        self._toast_timer = None
        if self._toast and self._toast.winfo_exists():
            self._toast.withdraw()
            self._toast.device_id = None

    def _show_device_toast(self, device):
        level = self._device_sample(device)[0]
        self._show_toast(self._device_name(device),
                         tr('Đã kết nối · {level}%', level=level) if level is not None
                         else tr('Đã kết nối · đang lấy số pin…'),
                         glyph=self._device_glyph(device), color=level_color(level) if level is not None else T.GOOD,
                         device_id=device['id'])

    def _refresh_device_toast(self):
        """Số pin thường tới sau kết nối vài giây: cập nhật bảng nổi đang hiện."""
        toast = self._toast
        if not toast or not toast.winfo_exists() or not getattr(toast, 'device_id', None):
            return
        device = next((d for d in self.connected_headphones if d['id'] == toast.device_id), None)
        if device is None:
            return
        level = self._device_sample(device)[0]
        if level is not None:
            toast.lbl_subtitle.configure(text=tr('Đã kết nối · {level}%', level=level), text_color=level_color(level))
            toast.icon.configure(image=self._glyph(self._device_glyph(device), 20, level_color(level)))
