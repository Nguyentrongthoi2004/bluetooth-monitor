"""Pin trái/phải/hộp cho mọi tai nghe true wireless có Fast Pair, không chỉ chiếc JBL.

Người dùng chọn đúng tai nghe trong danh sách tai nghe đang phát pin gần máy (đối chiếu số pin) — app không gán theo
tên. Liên kết lưu theo định danh Windows của tai nghe: {id thiết bị: địa chỉ Fast Pair}. Chiếc JBL vẫn dùng bộ đọc
riêng (jbl_core); chọn nó trong danh sách thì đi qua liên kết JBL như cũ.
"""
import logging
import time

import customtkinter as ctk

import i18n
import ui_theme as T
from i18n import tr
from ui_theme import blend

PICKER_REFRESH_MS = 1000
PICKER_EMPTY_SECONDS = 20  # chưa thấy tai nghe nào sau chừng này thì giải thích vì sao
# Tai nghe chưa liên kết vừa kết nối: nghe gói Fast Pair chừng này giây. Chỉ khi thấy tai nghe đang phát pin trái/phải/hộp
# mới hiện nút "Xem pin từng bên" — tai nghe chụp tai không phát gói này nên không bao giờ có nút thừa.
DISCOVERY_SECONDS = 30
DISCOVERY_CHECK_MS = 2000

i18n.register({
    'Xem pin từng bên': 'Show each earbud',
    'Tìm “{name}”': 'Find “{name}”',
    'Đây là các tai nghe đang phát pin từng bên ở gần máy. Đối chiếu số pin với tai nghe của '
    'bạn rồi bấm “Chọn”. Chưa thấy thì mở nắp hộp sạc hoặc lấy tai nghe ra.':
        'These earbuds nearby are sending battery levels for each side. Compare the levels with your '
        'earbuds, then click “Choose”. Not listed? Open the case lid or take the earbuds out.',
    'Đang tìm…': 'Searching…',
    'Đóng': 'Close',
    'Chọn': 'Choose',
    'Trái': 'Left',
    'Phải': 'Right',
    'Hộp': 'Case',
    'Chưa thấy tai nghe nào phát pin từng bên. Tai nghe chụp tai, Galaxy Buds và '
    'tai nghe không có Fast Pair không hỗ trợ cách này.':
        "No earbuds sending per-side battery levels found. Over-ear headphones, Galaxy Buds and "
        "earbuds without Fast Pair don't support this.",
})


class SplitBatteryMixin:
    # --- QUÉT VÀ ÁP DỤNG ---
    def _fast_pair_bindings(self):
        return self.settings['fast_pair_bindings']

    def _sync_fast_pair_scanner(self):
        """Chỉ quét khi có tai nghe đã liên kết đang kết nối, hoặc khi bảng chọn đang mở."""
        connected = {device['id'] for device in self.connected_headphones}
        scanner = self.fast_pair_scanner
        scanner.set_watched({address for key, address in self._fast_pair_bindings().items() if key in connected})
        needed = (bool(scanner.watched) or self._split_picker is not None
                  or time.monotonic() < self._split_discovery_until)
        try:
            if needed and not scanner.running:
                scanner.start()
            elif not needed and scanner.running:
                scanner.stop()
        except Exception:
            logging.warning('Cannot scan Fast Pair battery', exc_info=True)

    def _start_split_discovery(self, devices):
        """Gọi khi có tai nghe mới kết nối: nghe thử xem có tai nghe true wireless chưa liên kết nào ở gần không."""
        owners = self._component_owners()
        if not any(device.get('kind', 'headphone') == 'headphone' and device['id'] not in owners
                   for device in devices):
            return
        restart = time.monotonic() >= self._split_discovery_until
        self._split_discovery_until = time.monotonic() + DISCOVERY_SECONDS
        self._sync_fast_pair_scanner()
        if restart:
            self.after(DISCOVERY_CHECK_MS, self._check_split_discovery)

    def _check_split_discovery(self):
        if self._closing:
            return
        owners = self._component_owners()
        for device in self.connected_headphones:
            if (device['id'] not in owners and device['id'] not in self._split_available
                    and self._split_candidates(device['id'], reclaim=False)):
                self._split_available.add(device['id'])
                self._render_connection_status()
        if time.monotonic() < self._split_discovery_until:
            self.after(DISCOVERY_CHECK_MS, self._check_split_discovery)
        else:
            self._sync_fast_pair_scanner()  # hết thời gian nghe thử: tắt quét nếu không còn cần

    def _on_fast_pair_update(self, address, batteries):
        self._enqueue_ui(self._apply_fast_pair_update, address, batteries)

    def _apply_fast_pair_update(self, address, batteries):
        connected = {device['id'] for device in self.connected_headphones}
        device_id = next((key for key, bound in self._fast_pair_bindings().items()
                          if bound == address and key in connected), None)
        if device_id is None:
            return
        if batteries is None:
            # Không còn gói nào: bỏ số pin chi tiết, quay về pin chung của Windows.
            self._apply_components(device_id, None, None, None, {'_fresh': False, '_source': 'fast_pair'})
            return
        (left, left_charging), (right, right_charging), (case, case_charging) = batteries
        self._apply_components(device_id, left, right, case, {
            'left': left_charging, 'right': right_charging, 'case': case_charging, '_fresh': True,
            '_source': 'fast_pair', '_updated_at': time.strftime('%Y-%m-%d %H:%M:%S')})

    def _unbind_split(self, device_id):
        bindings = dict(self._fast_pair_bindings())
        if bindings.pop(device_id, None) is None:
            return False
        self.settings['fast_pair_bindings'] = bindings
        self._save_settings_now()
        self._components.pop(device_id, None)
        self._sync_gatt_targets()
        self._sync_fast_pair_scanner()
        return True

    # --- BẢNG CHỌN TAI NGHE ---
    def _bind_fast_pair_to_active(self):
        """Mở danh sách tai nghe đang phát pin trái/phải/hộp gần máy để chọn đúng tai nghe đang xem."""
        device = self.active_headphone
        if not device:
            return
        self._close_split_picker()
        win = self._split_picker = ctk.CTkToplevel(self)
        win.title(tr("Xem pin từng bên"))
        win.configure(fg_color=T.BG)
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", self._close_split_picker)
        self._style_dialog(win)
        win.device_id, win.opened, win.key, win.rows, win.timer = device['id'], time.monotonic(), None, [], None
        ctk.CTkLabel(win, text=tr("Tìm “{name}”", name=self._device_name(device)), anchor="w", text_color=T.TEXT,
                     font=ctk.CTkFont(size=15, weight="bold")).pack(fill="x", padx=18, pady=(16, 2))
        ctk.CTkLabel(win, anchor="w", justify="left", wraplength=360, text_color=T.MUTED, font=ctk.CTkFont(size=12),
                     text=tr("Đây là các tai nghe đang phát pin từng bên ở gần máy. Đối chiếu số pin với tai nghe của "
                             "bạn rồi bấm “Chọn”. Chưa thấy thì mở nắp hộp sạc hoặc lấy tai nghe ra.")
                     ).pack(fill="x", padx=18, pady=(0, 8))
        win.list = ctk.CTkFrame(win, fg_color="transparent")
        win.list.pack(fill="x", padx=12)
        win.lbl_empty = ctk.CTkLabel(win, text=tr("Đang tìm…"), anchor="w", justify="left", wraplength=360,
                                     text_color=T.DIM, font=ctk.CTkFont(size=12))
        win.lbl_empty.pack(fill="x", padx=18, pady=6)
        footer = ctk.CTkFrame(win, fg_color="transparent")
        footer.pack(fill="x", padx=18, pady=(4, 16))
        win.btn_cancel = ctk.CTkButton(footer, text=tr("Đóng"), width=90, height=30, fg_color=T.CARD_HOVER,
                                       hover_color=T.BORDER, text_color=T.TEXT, command=self._close_split_picker)
        win.btn_cancel.pack(side="right")
        self._center_window(win, 400, 380)
        self._sync_fast_pair_scanner()
        self._refresh_split_picker()

    def _split_candidates(self, device_id, reclaim=True):
        """Tai nghe có thể chọn: bỏ địa chỉ đang thuộc một tai nghe KHÁC đang kết nối.

        reclaim: liên kết của tai nghe không kết nối được chọn lại (vd. bỏ ghép đôi rồi ghép lại: Windows cấp id mới).
        Khi tự quyết định có hiện nút hay không thì không tính chúng (vd. JBL nằm trong hộp mở nắp cạnh tai nghe
        chụp tai không được làm nút hiện trên tai nghe chụp tai)."""
        connected = {device['id'] for device in self.connected_headphones}
        taken = {address for key, address in self._fast_pair_bindings().items()
                 if key != device_id and (key in connected or not reclaim)}
        if self.battery_device_id and self.battery_device_id != device_id and (
                self.battery_device_id in connected or not reclaim):
            taken.add(str(getattr(self.core, 'target_address', '')).upper())
        return [item for item in self.fast_pair_scanner.candidates() if item[0] not in taken]

    @staticmethod
    def _split_text(batteries):
        parts = []
        for label, (level, charging) in zip((tr("Trái"), tr("Phải"), tr("Hộp")), batteries):
            parts.append(f"{label} {'⚡' if charging else ''}{level}%" if level is not None else f"{label} --")
        return " · ".join(parts)

    def _refresh_split_picker(self):
        win = self._split_picker
        if win is None or not win.winfo_exists():
            return
        candidates = self._split_candidates(win.device_id)
        key = tuple((address, batteries) for address, _, batteries in candidates)
        if key != win.key:
            win.key = key
            for row in win.rows:
                row.destroy()
            win.rows = []
            for address, rssi, batteries in candidates:
                row = ctk.CTkFrame(win.list, fg_color=T.CARD, corner_radius=12, border_color=T.BORDER, border_width=1)
                row.pack(fill="x", pady=3)
                text = ctk.CTkFrame(row, fg_color="transparent")
                text.pack(side="left", fill="x", expand=True, padx=12, pady=8)
                ctk.CTkLabel(text, text=self._split_text(batteries), anchor="w", text_color=T.TEXT,
                             font=ctk.CTkFont(size=13, weight="bold")).pack(fill="x")
                signal = tr(self._signal_text(rssi)) if rssi is not None else None
                ctk.CTkLabel(text, text=" · ".join(filter(None, [signal, address])), anchor="w", text_color=T.DIM,
                             font=ctk.CTkFont(size=11)).pack(fill="x")
                row.btn_choose = ctk.CTkButton(row, text=tr("Chọn"), width=70, height=30, fg_color=T.ACCENT,
                                               hover_color=blend(T.ACCENT, "#000000", .85), text_color="#ffffff",
                                               command=lambda chosen=address: self._choose_split(win.device_id, chosen))
                row.btn_choose.pack(side="right", padx=10)
                row.address = address
                win.rows.append(row)
        if candidates:
            win.lbl_empty.configure(text="")
        elif time.monotonic() - win.opened >= PICKER_EMPTY_SECONDS:
            win.lbl_empty.configure(text=tr("Chưa thấy tai nghe nào phát pin từng bên. Tai nghe chụp tai, Galaxy Buds và "
                                            "tai nghe không có Fast Pair không hỗ trợ cách này."))
        win.timer = self.after(PICKER_REFRESH_MS, self._refresh_split_picker)

    def _choose_split(self, device_id, address):
        self._close_split_picker()
        if address == str(getattr(self.core, 'target_address', '')).upper():
            self._unbind_split(device_id)  # tai nghe này chuyển sang bộ đọc riêng của JBL
            self._confirm_bind(device_id)
            return
        # Một địa chỉ chỉ thuộc một tai nghe: liên kết cũ (id trước khi ghép lại) bị thay.
        bindings = {key: bound for key, bound in self._fast_pair_bindings().items() if bound != address}
        bindings[device_id] = address
        self.settings['fast_pair_bindings'] = bindings
        self._save_settings_now()
        self._components.pop(device_id, None)
        self._sync_gatt_targets()
        self._sync_fast_pair_scanner()
        latest = self.fast_pair_scanner.latest(address)
        if latest:
            self._apply_fast_pair_update(address, latest)
        self._refresh_settings_binding()
        self._render_connection_status()
        self._update_tray()

    def _close_split_picker(self):
        win, self._split_picker = self._split_picker, None
        if win is not None:
            if win.timer:
                self.after_cancel(win.timer)
            if win.winfo_exists():
                win.destroy()
        self._sync_fast_pair_scanner()
