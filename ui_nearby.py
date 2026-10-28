"""Bảng thiết bị Bluetooth xung quanh: quét, xem thông số, ghép đôi và bỏ ghép đôi."""
import logging
import threading

import customtkinter as ctk

import bt_connect
import bt_scan
import i18n
import ui_theme as T
from ble_battery import address_from_device_id
from i18n import tr, tr_message
from ui_theme import blend

i18n.register({
    'Thiết bị Bluetooth xung quanh': 'Nearby Bluetooth devices',
    'Thiết bị xung quanh': 'Nearby devices',
    'Đang quét…': 'Scanning…',
    'Bấm vào thiết bị để xem thông số. Bật chế độ ghép đôi trên tai '
    'nghe nếu chưa thấy; thiết bị Windows không hiện cũng có thể có ở đây.':
        "Click a device to see its details. Put your headphones in pairing mode if they don't show up; "
        "devices Windows doesn't list may also appear here.",
    'Hiện cả thiết bị không tên': 'Show unnamed devices',
    'Đóng': 'Close',
    'Cài đặt Bluetooth Windows': 'Windows Bluetooth settings',
    'Không quét được — Bluetooth của máy có đang bật không?': "Can't scan — is Bluetooth turned on?",
    'tín hiệu mạnh': 'strong signal',
    'tín hiệu vừa': 'medium signal',
    'tín hiệu yếu': 'weak signal',
    'Đang quét… · thấy {count} thiết bị': 'Scanning… · {count} found',
    'Chưa thấy thiết bị nào.': 'No devices found yet.',
    'Thiết bị không tên': 'Unnamed device',
    'Đang kết nối': 'Connected',
    'Đã ghép đôi': 'Paired',
    'Chưa ghép đôi': 'Not paired',
    'Đang ghép…': 'Pairing…',
    'Ghép đôi': 'Pair',
    'Thông số thiết bị': 'Device details',
    '‹  Quay lại danh sách': '‹  Back to list',
    'Trạng thái': 'Status',
    'Loại kết nối': 'Connection type',
    'Bluetooth thường': 'Bluetooth Classic',
    'Địa chỉ': 'Address',
    'Tín hiệu': 'Signal',
    'Pin': 'Battery',
    'Hãng': 'Manufacturer',
    'Đang đọc…': 'Reading…',
    'Cần ghép đôi để đọc': 'Pair to read',
    'Pin trái/phải/hộp của tai nghe này có trên bảng pin chính. App không đọc thêm qua BLE để '
    'không làm nhạc bị giật.':
        "Left/right/case battery for these earbuds is on the main panel. The app doesn't also read "
        "it over BLE, so music doesn't stutter.",
    'Pin, hãng, model và firmware đọc qua BLE theo chuẩn chung; thiết bị không hỗ trợ sẽ hiện “—”.':
        'Battery, manufacturer, model and firmware are read over standard BLE; devices that '
        'don\'t support it show “—”.',
    'Đang xem mà không kết nối tới thiết bị (không ghép đôi). Pin, hãng, model chỉ đọc được sau khi '
    'bạn chủ động bấm “Ghép đôi”.':
        "Viewing without connecting to the device (no pairing). Battery, manufacturer and model can "
        "only be read after you click “Pair”.",
    'Đọc lại': 'Read again',
    'Kết nối': 'Connect',
    'Ngắt kết nối': 'Disconnect',
    'Bỏ ghép đôi': 'Unpair',
    'thiết bị': 'device',
    'Bỏ ghép đôi “{name}”?': 'Unpair “{name}”?',
    ' Thiết bị đang kết nối sẽ bị ngắt.': ' It will be disconnected.',
    ' Muốn dùng lại phải ghép đôi lại từ đầu.': " To use it again, you'll need to pair it again.",
    'Hủy': 'Cancel',
    'Đang bỏ…': 'Unpairing…',
    'Thiết bị Bluetooth': 'Bluetooth device',
    'Nhập mã dưới đây trên “{name}” để ghép đôi:': 'Enter this code on “{name}” to pair:',
    'Đã nhập xong': 'Done',
    '“{name}” có đang hiện ĐÚNG mã này không? Mã khác nghĩa là có thiết bị khác đang chen vào: '
    'bấm “Không khớp”.':
        'Does “{name}” show EXACTLY this code? A different code means another device is trying to '
        'cut in: click “Doesn\'t match”.',
    'Đúng mã': 'It matches',
    'Không khớp': "Doesn't match",
    'Đang kết nối…': 'Connecting…',
    'Đang ngắt…': 'Disconnecting…',
})


class NearbyMixin:
    # --- THIẾT BỊ BLUETOOTH XUNG QUANH ---
    def _show_nearby_panel(self):
        """Bảng quét thiết bị xung quanh: giữa màn hình, bảng pin tạm ẩn (giống bảng Cài đặt)."""
        if self._nearby_win and self._nearby_win.winfo_exists():
            self._nearby_win.lift()
            return
        if self._settings_win and self._settings_win.winfo_exists():
            self._settings_win.destroy()
            self._settings_win = None
        self.withdraw()
        win = self._nearby_win = ctk.CTkToplevel(self)
        win.title(tr("Thiết bị Bluetooth xung quanh"))
        win.configure(fg_color=T.BG)
        win.resizable(False, False)
        win.attributes("-topmost", True)
        self._center_window(win, 440, 600)
        win.protocol("WM_DELETE_WINDOW", self._close_nearby_panel)
        self._style_dialog(win)

        header = ctk.CTkFrame(win, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=(14, 0))
        ctk.CTkLabel(header, text=tr("Thiết bị xung quanh"), font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=T.TEXT, anchor="w").pack(side="left")
        win.lbl_scan = ctk.CTkLabel(win, text=tr("Đang quét…"), anchor="w", text_color=T.MUTED,
                                    font=ctk.CTkFont(size=12))
        win.lbl_scan.pack(fill="x", padx=18)
        win.lbl_hint = ctk.CTkLabel(win, text=tr("Bấm vào thiết bị để xem thông số. Bật chế độ ghép đôi trên tai "
                                                 "nghe nếu chưa thấy; thiết bị Windows không hiện cũng có thể có ở đây."),
                                    anchor="w", justify="left", wraplength=390,
                                    text_color=T.DIM, font=ctk.CTkFont(size=11))
        win.lbl_hint.pack(fill="x", padx=18, pady=(2, 4))
        win.show_unnamed = ctk.CTkSwitch(win, text=tr("Hiện cả thiết bị không tên"), progress_color=T.ACCENT,
                                         text_color=T.MUTED, font=ctk.CTkFont(size=12),
                                         command=self._refresh_nearby)
        win.show_unnamed.pack(fill="x", padx=18, pady=(2, 4))
        win.list = ctk.CTkScrollableFrame(win, fg_color=T.BG)
        win.list.pack(fill="both", expand=True, padx=8, pady=4)
        win.rows = []
        win.detail = None  # khung chi tiết thay chỗ danh sách khi đang xem một thiết bị
        win.pin_box = None  # hộp so mã ghép đôi, nằm trên chân bảng nên không mất khi đổi màn

        footer = win.footer = ctk.CTkFrame(win, fg_color="transparent")
        footer.pack(fill="x", padx=14, pady=(8, 12))
        win.btn_close = ctk.CTkButton(
            footer, text=tr("Đóng"), width=90, height=32, fg_color=T.ACCENT, text_color="#ffffff",
            hover_color=blend(T.ACCENT, "#000000", .85), font=ctk.CTkFont(size=12, weight="bold"),
            command=self._close_nearby_panel)
        win.btn_close.pack(side="right")
        ctk.CTkButton(footer, text=tr("Cài đặt Bluetooth Windows"), height=32, fg_color="transparent",
                      border_width=1, border_color=T.BORDER, hover_color=T.CARD_HOVER, text_color=T.TEXT,
                      command=self._open_bluetooth_settings).pack(side="right", padx=8)

        try:
            if self._nearby_scanner is None:
                self._nearby_scanner = bt_scan.NearbyScanner()
            self._nearby_scanner.start()
        except Exception:
            logging.warning('Cannot scan nearby Bluetooth devices', exc_info=True)
            win.lbl_scan.configure(text=tr("Không quét được — Bluetooth của máy có đang bật không?"), text_color=T.WARN)
        self._nearby_key = None
        self._refresh_nearby()

    def _close_nearby_panel(self):
        self._dispose_nearby_panel()
        self.show_window()

    def _dispose_nearby_panel(self):
        """Đóng bảng mà không hiện lại bảng pin (dùng khi chuyển sang bảng Cài đặt)."""
        if self._nearby_timer:
            self.after_cancel(self._nearby_timer)  # không thì mở lại bảng sẽ có hai vòng làm mới
            self._nearby_timer = None
        if self._nearby_scanner:
            self._nearby_scanner.stop()  # quét liên tục tốn pin/sóng: chỉ quét khi bảng mở
        if self._nearby_win and self._nearby_win.winfo_exists():
            self._nearby_win.destroy()
        self._nearby_win = None

    @staticmethod
    def _signal_text(signal):
        if signal is None:
            return None
        if signal >= -60:
            return "tín hiệu mạnh"
        if signal >= -75:
            return "tín hiệu vừa"
        return "tín hiệu yếu"

    def _refresh_nearby(self):
        win = self._nearby_win
        if not win or not win.winfo_exists():
            return
        scanner = self._nearby_scanner
        devices = scanner.snapshot(bool(win.show_unnamed.get())) if scanner and scanner.running else []
        if scanner and scanner.running and win.detail is None:
            win.lbl_scan.configure(text=tr("Đang quét… · thấy {count} thiết bị", count=len(devices)), text_color=T.MUTED)
        key = tuple((d['id'], d['paired'], d['connected'], self._signal_text(d['signal']) if d['nearby'] else None,
                     d['id'] in self._pairing_ids) for d in devices)
        if win.detail is None and key != self._nearby_key:
            self._nearby_key = key
            for row in win.rows:
                row.destroy()
            win.rows = [self._nearby_row(win.list, device) for device in devices]
            if not devices:
                empty = ctk.CTkLabel(win.list, text=tr("Chưa thấy thiết bị nào."), text_color=T.DIM)
                empty.pack(pady=20)
                win.rows.append(empty)
        self._nearby_timer = self.after(1500, self._refresh_nearby)

    def _nearby_row(self, parent, device):
        row = ctk.CTkFrame(parent, fg_color=T.CARD, corner_radius=12, border_color=T.BORDER, border_width=1)
        row.pack(fill="x", padx=6, pady=3)
        glyph = {'headphone': 'headphone', 'phone': 'phone', 'computer': 'computer', 'tv': 'tv',
                 'peripheral': 'peripheral'}.get(device['kind'], 'bluetooth')
        active = device['connected']
        ctk.CTkLabel(row, text="", image=self._glyph(glyph, 18, T.ACCENT if active else T.MUTED), width=36, height=36,
                     corner_radius=10, fg_color=T.ACCENT_SOFT if active else T.BG).pack(side="left", padx=(10, 10), pady=8)
        text = ctk.CTkFrame(row, fg_color="transparent")
        text.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(text, text=device['name'] or tr("Thiết bị không tên"), anchor="w", height=18,
                     font=ctk.CTkFont(size=13, weight="bold"), text_color=T.TEXT).pack(fill="x")
        details = [tr("Đang kết nối" if active else "Đã ghép đôi" if device['paired'] else "Chưa ghép đôi")]
        signal = self._signal_text(device['signal']) if device['nearby'] and not device['paired'] else None
        if signal:
            details.append(tr(signal))
        if device['le']:
            details.append("BLE")
        ctk.CTkLabel(text, text=" · ".join(details), anchor="w", height=16, font=ctk.CTkFont(size=11),
                     text_color=T.GOOD if active else T.MUTED).pack(fill="x")
        for widget in (row, text, *text.winfo_children(), *row.winfo_children()):
            widget.bind("<Button-1>", lambda event: self._show_nearby_details(device))
        if not device['paired']:
            pairing = device['id'] in self._pairing_ids
            ctk.CTkButton(row, text=tr("Đang ghép…" if pairing else "Ghép đôi"), width=92, height=30,
                          corner_radius=8, fg_color=T.ACCENT, hover_color=blend(T.ACCENT, "#000000", .85),
                          text_color="#ffffff", state="disabled" if pairing else "normal",
                          command=lambda: self._pair_nearby(device)).pack(side="right", padx=10)
        return row

    def _known_level(self, device):
        """Mức pin app đã biết (tai nghe đang kết nối), ghép theo địa chỉ Bluetooth, không theo tên."""
        address = (device.get('address') or '').replace(':', '').upper()
        for connected in self.connected_headphones:
            if address and (address_from_device_id(connected['id']) or '').replace(':', '') == address:
                return self._device_sample(connected)[:2]
        return None, None

    def _show_nearby_details(self, device):
        win = self._nearby_win
        if not win or not win.winfo_exists():
            return
        for widget in (win.lbl_hint, win.show_unnamed, win.list):
            widget.pack_forget()
        if win.detail is not None:
            win.detail.destroy()
        detail = win.detail = ctk.CTkScrollableFrame(win, fg_color=T.BG)
        detail.pack(fill="both", expand=True, padx=8, pady=4, before=win.footer)
        win.lbl_scan.configure(text=tr("Thông số thiết bị"), text_color=T.MUTED)
        ctk.CTkButton(detail, text=tr("‹  Quay lại danh sách"), anchor="w", height=30, fg_color="transparent",
                      hover_color=T.CARD_HOVER, text_color=T.ACCENT, font=ctk.CTkFont(size=12, weight="bold"),
                      command=self._hide_nearby_details).pack(fill="x", padx=4, pady=(0, 6))
        card = ctk.CTkFrame(detail, fg_color=T.CARD, corner_radius=14, border_color=T.BORDER, border_width=1)
        card.pack(fill="x", padx=4)
        ctk.CTkLabel(card, text=device['name'] or tr("Thiết bị không tên"), anchor="w", wraplength=360,
                     font=ctk.CTkFont(size=16, weight="bold"), text_color=T.TEXT).pack(fill="x", padx=16, pady=(14, 8))
        detail.fields = {}

        def field(label, value):
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=2)
            # fields giữ khóa gốc (tiếng Việt) để code khác tra cứu; chỉ chữ hiện được dịch.
            ctk.CTkLabel(row, text=tr(label), width=120, anchor="w", text_color=T.MUTED,
                         font=ctk.CTkFont(size=12)).pack(side="left")
            value_label = ctk.CTkLabel(row, text=value, anchor="w", justify="left", wraplength=230,
                                       text_color=T.TEXT, font=ctk.CTkFont(size=12, weight="bold"))
            value_label.pack(side="left", fill="x", expand=True)
            detail.fields[label] = value_label

        status = "Đang kết nối" if device['connected'] else "Đã ghép đôi" if device['paired'] else "Chưa ghép đôi"
        field("Trạng thái", tr(status))
        field("Loại kết nối", "Bluetooth Low Energy (BLE)" if device['le'] else tr("Bluetooth thường"))
        field("Địa chỉ", device['address'] or "—")
        signal = device['signal'] if device['nearby'] and not device['paired'] else None
        field("Tín hiệu", f"{signal} dBm · {tr(self._signal_text(signal))}" if signal is not None else "—")
        level, source = self._known_level(device)
        # Chỉ kết nối GATT tới thiết bị ĐÃ ghép đôi: kết nối tới thiết bị lạ có thể khiến Windows/thiết bị
        # tự bật lời mời ghép đôi, trong khi người dùng chỉ muốn xem. Ghép đôi chỉ qua nút "Ghép đôi".
        can_read = device['paired']
        # Tai nghe đã có pin trái/phải/hộp (JBL Fast Pair, AirPods) không có dịch vụ pin BLE; thử nối BLE tới nó
        # chỉ chiếm sóng làm nhạc giật, nên không đọc GATT.
        split_battery = can_read and self._owns_split_battery(device['address'])
        waiting = ("—" if split_battery else tr("Đang đọc…")) if can_read else tr("Cần ghép đôi để đọc")
        field("Pin", f"{level}% · {'BLE' if source == 'ble' else 'Windows' if source == 'windows' else 'Fast Pair'}"
              if level is not None else waiting)
        for label in ("Hãng", "Model", "Firmware"):
            field(label, waiting)
        ctk.CTkFrame(card, fg_color="transparent", height=10).pack()
        note = ("Pin trái/phải/hộp của tai nghe này có trên bảng pin chính. App không đọc thêm qua BLE để "
                "không làm nhạc bị giật." if split_battery else
                "Pin, hãng, model và firmware đọc qua BLE theo chuẩn chung; thiết bị không hỗ trợ sẽ hiện “—”."
                if can_read else
                "Đang xem mà không kết nối tới thiết bị (không ghép đôi). Pin, hãng, model chỉ đọc được sau khi "
                "bạn chủ động bấm “Ghép đôi”.")
        ctk.CTkLabel(detail, text=tr(note), anchor="w", justify="left", wraplength=380, text_color=T.DIM,
                     font=ctk.CTkFont(size=11)).pack(fill="x", padx=8, pady=8)
        actions = ctk.CTkFrame(detail, fg_color="transparent")
        actions.pack(fill="x", padx=4)
        detail.device_id = device['id']
        if can_read:
            if not split_battery:
                ctk.CTkButton(actions, text=tr("Đọc lại"), width=90, height=30, fg_color=T.CARD, border_width=1,
                              border_color=T.BORDER, hover_color=T.CARD_HOVER, text_color=T.TEXT,
                              command=lambda: self._show_nearby_details(device)).pack(side="left", padx=(0, 8))
            # Thiết bị âm thanh Bluetooth thường (tai nghe, loa) mới có driver nhận lệnh kết nối/ngắt.
            # Ngắt chỉ hiện khi máy thật sự ngắt được (không có ở máy dùng Intel Bluetooth Audio Offload).
            connect = not device['connected']
            if (device['kind'] in ('headphone', 'tv') and not device['le'] and device['address']
                    and (connect or bt_connect.can_disconnect(device['address']))):
                detail.btn_connect = ctk.CTkButton(
                    actions, text=tr("Kết nối" if connect else "Ngắt kết nối"), width=110, height=30,
                    fg_color=T.ACCENT if connect else T.CARD, border_width=0 if connect else 1,
                    border_color=T.BORDER, text_color="#ffffff" if connect else T.TEXT,
                    hover_color=blend(T.ACCENT, "#000000", .85) if connect else T.CARD_HOVER,
                    command=lambda: self._details_connect(device, connect))
                detail.btn_connect.pack(side="left")
            detail.btn_unpair = ctk.CTkButton(
                actions, text=tr("Bỏ ghép đôi"), width=110, height=30, fg_color="transparent", border_width=1,
                border_color=T.LOW, hover_color=blend(T.LOW, T.CARD, .15), text_color=T.LOW,
                command=lambda: self._confirm_unpair(device))
            detail.btn_unpair.pack(side="right")
            detail.actions = actions
        else:
            ctk.CTkButton(actions, text=tr("Ghép đôi"), width=100, height=30, fg_color=T.ACCENT,
                          hover_color=blend(T.ACCENT, "#000000", .85), text_color="#ffffff",
                          command=lambda: self._pair_nearby(device)).pack(side="left")

        self._details_request += 1
        request = self._details_request
        if not can_read or split_battery:
            return

        def work():
            details = bt_scan.read_details(device['address'])
            self._enqueue_ui(self._apply_nearby_details, request, details, level is None)
        threading.Thread(target=work, daemon=True, name='BluetoothDetails').start()

    def _confirm_unpair(self, device):
        """Hỏi lại ngay trong bảng (không mở cửa sổ mới) trước khi bỏ ghép đôi."""
        win = self._nearby_win
        if not win or not win.winfo_exists() or win.detail is None or getattr(win.detail, 'confirm', None):
            return
        box = win.detail.confirm = ctk.CTkFrame(win.detail, fg_color=blend(T.LOW, T.CARD, .12), corner_radius=12,
                                                border_color=T.LOW, border_width=1)
        box.pack(fill="x", padx=4, pady=(10, 0))
        warning = tr("Bỏ ghép đôi “{name}”?", name=device['name'] or tr('thiết bị'))
        if device['connected']:
            warning += tr(" Thiết bị đang kết nối sẽ bị ngắt.")
        ctk.CTkLabel(box, text=warning + tr(" Muốn dùng lại phải ghép đôi lại từ đầu."), anchor="w",
                     justify="left", wraplength=360, text_color=T.TEXT,
                     font=ctk.CTkFont(size=12)).pack(fill="x", padx=12, pady=(10, 6))
        buttons = ctk.CTkFrame(box, fg_color="transparent")
        buttons.pack(fill="x", padx=12, pady=(0, 10))
        box.btn_confirm = ctk.CTkButton(buttons, text=tr("Bỏ ghép đôi"), width=110, height=30, fg_color=T.LOW,
                                        hover_color=blend(T.LOW, "#000000", .85), text_color="#ffffff",
                                        command=lambda: self._unpair_nearby(device))
        box.btn_confirm.pack(side="right")
        box.btn_cancel = ctk.CTkButton(buttons, text=tr("Hủy"), width=70, height=30, fg_color=T.CARD,
                                       hover_color=T.CARD_HOVER, text_color=T.TEXT, command=self._cancel_unpair)
        box.btn_cancel.pack(side="right", padx=8)

    def _cancel_unpair(self):
        win = self._nearby_win
        if win and win.winfo_exists() and win.detail is not None and getattr(win.detail, 'confirm', None):
            win.detail.confirm.destroy()
            win.detail.confirm = None

    def _unpair_nearby(self, device):
        win = self._nearby_win
        if win and win.winfo_exists() and win.detail is not None and getattr(win.detail, 'confirm', None):
            win.detail.confirm.btn_confirm.configure(state="disabled", text=tr("Đang bỏ…"))
            win.detail.confirm.btn_cancel.configure(state="disabled")

        def work():
            ok, message = bt_scan.unpair(device['id'])
            self._enqueue_ui(self._finish_unpair, device, ok, message)
        threading.Thread(target=work, daemon=True, name='BluetoothUnpair').start()

    def _finish_unpair(self, device, ok, message):
        self._show_toast(device['name'] or tr("Thiết bị Bluetooth"), tr_message(message), glyph='bluetooth',
                         color=T.GOOD if ok else T.WARN)
        if ok:
            self._hide_nearby_details()
        else:
            self._cancel_unpair()
        self.connection_monitor.trigger_refresh()

    def _apply_nearby_details(self, request, details, fill_battery):
        win = self._nearby_win
        if request != self._details_request or not win or not win.winfo_exists() or win.detail is None:
            return  # đã quay lại danh sách hoặc mở thiết bị khác
        fields = win.detail.fields
        if fill_battery:
            level = details.get('battery')
            fields["Pin"].configure(text=f"{level}% · BLE" if level is not None else "—")
        for label, key in (("Hãng", 'manufacturer'), ("Model", 'model'), ("Firmware", 'firmware')):
            fields[label].configure(text=details.get(key) or "—")

    def _hide_nearby_details(self):
        win = self._nearby_win
        if not win or not win.winfo_exists():
            return
        self._details_request += 1
        if win.detail is not None:
            win.detail.destroy()
            win.detail = None
        win.lbl_hint.pack(fill="x", padx=18, pady=(2, 4), before=win.footer)
        win.show_unnamed.pack(fill="x", padx=18, pady=(2, 4), before=win.footer)
        win.list.pack(fill="both", expand=True, padx=8, pady=4, before=win.footer)
        self._nearby_key = None

    def _pair_nearby(self, device):
        if device['id'] in self._pairing_ids:
            return
        self._pairing_ids.add(device['id'])
        self._nearby_key = None

        def work():
            ok, message = bt_scan.pair(device['id'],
                                       confirm_pin=lambda pin, display: self._wait_pin_answer(device, pin, display))
            self._enqueue_ui(self._finish_pairing, device, ok, message)
        threading.Thread(target=work, daemon=True, name='BluetoothPairing').start()

    def _wait_pin_answer(self, device, pin, display):
        """Gọi từ luồng ghép đôi: chờ người dùng so mã. Hết giờ, đóng bảng hay bấm "Không khớp" = từ chối."""
        answer, done = [], threading.Event()
        self._enqueue_ui(self._ask_pairing_pin, device, pin, display, answer, done)
        done.wait(bt_scan.PIN_TIMEOUT)
        return answer == [True]

    def _ask_pairing_pin(self, device, pin, display, answer, done):
        win = self._nearby_win
        if not win or not win.winfo_exists():
            done.set()
            return
        self._close_pin_box()
        box = win.pin_box = ctk.CTkFrame(win, fg_color=blend(T.ACCENT, T.CARD, .12), corner_radius=12,
                                         border_color=T.ACCENT, border_width=1)
        box.pack(fill="x", padx=14, pady=(6, 0), before=win.footer)
        box.bind('<Destroy>', lambda event: done.set(), add=True)  # bảng đóng: luồng ghép đôi thôi chờ

        def reply(ok):
            if not done.is_set():
                answer.append(ok)
                done.set()
            self._close_pin_box()

        name = device['name'] or tr('thiết bị')
        if display:
            text = tr("Nhập mã dưới đây trên “{name}” để ghép đôi:", name=name)
            yes, no = tr("Đã nhập xong"), tr("Hủy")
        else:
            text = tr("“{name}” có đang hiện ĐÚNG mã này không? Mã khác nghĩa là có thiết bị khác đang chen vào: "
                      "bấm “Không khớp”.", name=name)
            yes, no = tr("Đúng mã"), tr("Không khớp")
        ctk.CTkLabel(box, text=text, anchor="w", justify="left", wraplength=370, text_color=T.TEXT,
                     font=ctk.CTkFont(size=12)).pack(fill="x", padx=12, pady=(10, 2))
        box.lbl_pin = ctk.CTkLabel(box, text=pin, text_color=T.ACCENT, font=ctk.CTkFont(size=26, weight="bold"))
        box.lbl_pin.pack(padx=12)
        buttons = ctk.CTkFrame(box, fg_color="transparent")
        buttons.pack(fill="x", padx=12, pady=(2, 10))
        box.btn_yes = ctk.CTkButton(buttons, text=yes, width=110, height=30, fg_color=T.ACCENT, text_color="#ffffff",
                                    hover_color=blend(T.ACCENT, "#000000", .85), command=lambda: reply(True))
        box.btn_yes.pack(side="right")
        box.btn_no = ctk.CTkButton(buttons, text=no, width=90, height=30, fg_color=T.CARD,
                                   hover_color=T.CARD_HOVER, text_color=T.TEXT, command=lambda: reply(False))
        box.btn_no.pack(side="right", padx=8)

    def _close_pin_box(self):
        win = self._nearby_win
        box = getattr(win, 'pin_box', None) if win else None
        if box is not None:
            win.pin_box = None
            box.destroy()

    # --- KẾT NỐI / NGẮT THIẾT BỊ ĐÃ GHÉP ĐÔI ---
    def _owns_split_battery(self, address):
        """Địa chỉ này có phải tai nghe đang có pin trái/phải/hộp (JBL Fast Pair đã liên kết, AirPods) không."""
        compact = (address or '').replace(':', '').upper()
        return bool(compact) and any(
            (address_from_device_id(key) or '').replace(':', '') == compact for key in self._component_owners())

    def _details_connect(self, device, connect):
        detail = self._nearby_win.detail if self._nearby_win else None
        if detail is not None and getattr(detail, 'btn_connect', None):
            detail.btn_connect.configure(state="disabled", text=tr("Đang kết nối…" if connect else "Đang ngắt…"))
        self._set_device_connection(device, device['address'], connect)

    def _set_device_connection(self, device, address, connect):
        """Kết nối/ngắt tai nghe đã ghép đôi trên luồng nền (chờ Windows xác nhận, tới ~20 giây)."""
        key = (device['id'], connect)
        if key in self._connecting:
            return
        self._connecting.add(key)

        def work():
            ok, message = bt_connect.set_connected(address, connect)
            self._enqueue_ui(self._finish_device_connection, device, key, ok, message)
        threading.Thread(target=work, daemon=True, name='BluetoothConnect').start()

    def _finish_device_connection(self, device, key, ok, message):
        self._connecting.discard(key)
        # Không tự mở cửa sổ nào (Cài đặt Bluetooth…): chỉ báo, người dùng tự quyết.
        self._show_toast(self.settings['aliases'].get(device['id'], device['name']) or tr("Thiết bị Bluetooth"),
                         tr_message(message), glyph='bluetooth', color=T.GOOD if ok else T.WARN)
        win = self._nearby_win
        if win and win.winfo_exists() and win.detail is not None and getattr(win.detail, 'device_id', None) == device['id']:
            if ok:
                self._hide_nearby_details()  # danh sách hiện trạng thái thật khi Windows cập nhật xong
            elif getattr(win.detail, 'btn_connect', None):
                win.detail.btn_connect.configure(state="normal", text=tr("Kết nối" if key[1] else "Ngắt kết nối"))
        self.connection_monitor.trigger_refresh()
        self._render_connection_status()

    def _finish_pairing(self, device, ok, message):
        self._close_pin_box()  # hết giờ chờ mà hộp so mã vẫn còn
        self._pairing_ids.discard(device['id'])
        self._nearby_key = None
        self._show_toast(device['name'] or tr("Thiết bị Bluetooth"), tr_message(message),
                         glyph='bluetooth', color=T.GOOD if ok else T.WARN)
        # Ghép đôi xong Windows thường tự kết nối âm thanh; danh sách tai nghe cập nhật qua sự kiện.
        self.connection_monitor.trigger_refresh()


