"""
main.py - Ứng dụng theo dõi pin tai nghe Bluetooth trên Windows.
Flyout gọn đặt cạnh khay hệ thống, pin chi tiết JBL CLUB PRO+ TWS qua Fast Pair,
pin chung do Windows cung cấp cho các tai nghe khác.
Tích hợp System Tray, cảnh báo pin yếu, IPC Socket và Auto-Start.
"""
import os
import sys
import math
import time
import socket
import winreg
import ctypes
import threading
import queue
import logging
import json
import copy
import re
import shutil
import tkinter as tk
import tkinter.font as tkfont
import winsound
from logging.handlers import RotatingFileHandler
from PIL import Image, ImageDraw, ImageFont
import pystray
from pystray import MenuItem as item
import customtkinter as ctk
from customtkinter.windows.widgets.appearance_mode.appearance_mode_tracker import AppearanceModeTracker
from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker

import i18n
from i18n import tr
from jbl_core import JblCore
import headphone_monitor
from headphone_monitor import HeadphoneConnectionMonitor, RADIO_OFF
from ble_battery import GattBatteryMonitor, address_from_device_id
from battery_history import BatteryHistory, estimate_remaining
from hotkey import HotkeyWorker
import apple_pods
import fast_pair
import audio_output  # noqa: F401 — test vá main.audio_output / main.bt_scan
import bt_scan  # noqa: F401
import ui_theme
from ui_theme import blend, get_work_area, level_color
from ui_toast import ToastMixin
from ui_audio import AudioMixin
from ui_nearby import NearbyMixin
from ui_split import SplitBatteryMixin
from ui_feedback import FeedbackMixin
import msix_runtime
from safe_text import redact

# Đường dẫn tài nguyên
APP_DIR = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "Bluetooth Monitor"
OLD_APP_NAME = "JBL Battery Monitor"
_LOCAL = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
DATA_DIR = os.path.join(_LOCAL, APP_NAME) if getattr(sys, "frozen", False) else APP_DIR


def _migrate_data_dir():
    """Bản cũ tên "JBL Battery Monitor": chép cài đặt và lịch sử pin sang thư mục mới (giữ bản cũ)."""
    old = os.path.join(_LOCAL, OLD_APP_NAME)
    if not getattr(sys, "frozen", False) or os.path.exists(DATA_DIR) or not os.path.isdir(old):
        return
    import shutil
    try:
        shutil.copytree(old, DATA_DIR, ignore=shutil.ignore_patterns("app.log*", "crash_dump.txt"))
    except OSError:
        pass  # chạy với dữ liệu mới vẫn hơn là không chạy


_migrate_data_dir()
os.makedirs(DATA_DIR, exist_ok=True)
ASSETS_DIR = os.path.join(APP_DIR, "assets")
ICON_ICO = os.path.join(ASSETS_DIR, "app_icon.ico")
ICON_PNG = os.path.join(ASSETS_DIR, "app_icon.png")
SETTINGS_PATH = os.path.join(DATA_DIR, "headphone_settings.json")

REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_NAME = "Bluetooth_Monitor"
OLD_REG_NAME = "JBL_Battery_Monitor"
IPC_PORT = 29841
# CustomTkinter polls the Windows theme every 30 ms and DPI every 100 ms. The app sets the
# theme itself, so those loops were most of its idle CPU; slow them down.
AppearanceModeTracker.update_loop_interval = 2000
ScalingTracker.update_loop_interval = 1000
UI_POLL_MS = 100
CONNECTION_SETTLE_MS = 4000  # gộp thông báo kết nối/ngắt trong lúc Windows còn báo chập chờn
EXIT_TIMEOUT_SECONDS = 5.0
APP_VERSION = "4.4"

# Bảng màu (Fluent dark, điểm nhấn cam JBL)
BG = "#0e0f13"
CARD = "#17181e"
CARD_HOVER = "#21232b"
BORDER = "#262830"
TRACK = "#2a2c35"
TEXT = "#f5f5f7"
MUTED = "#a1a1aa"
DIM = "#71717a"
ACCENT = "#ff6a13"
GOOD = "#22c55e"
WARN = "#f59e0b"
LOW = "#ef4444"
CHARGING = "#38bdf8"
ACCENT_SOFT = "#3a2416"  # nền ô icon thiết bị: cam pha nền

THEMES = {
    'dark': dict(BG=BG, CARD=CARD, CARD_HOVER=CARD_HOVER, BORDER=BORDER, TRACK=TRACK,
                 TEXT=TEXT, MUTED=MUTED, DIM=DIM, ACCENT=ACCENT, GOOD=GOOD,
                 WARN=WARN, LOW=LOW, CHARGING=CHARGING, ACCENT_SOFT=ACCENT_SOFT),
    'light': dict(BG='#f3f4f7', CARD='#ffffff', CARD_HOVER='#eceef3', BORDER='#dfe1e8',
                  TRACK='#e3e5eb', TEXT='#18181b', MUTED='#51515b', DIM='#666671',
                  ACCENT='#c74a00', GOOD='#15803d', WARN='#a45c00', LOW='#dc2626', CHARGING='#0369a1',
                  ACCENT_SOFT='#fde9dc'),
}
ui_theme.use(THEMES['dark'])  # các module giao diện có màu ngay cả trước khi chọn theme

# Biểu tượng Fluent của Windows 11 (Segoe Fluent Icons; Windows 10 có Segoe MDL2 Assets).
ICON_FONTS = (r"C:\Windows\Fonts\SegoeIcons.ttf", r"C:\Windows\Fonts\segmdl2.ttf")
GLYPHS = {
    'headphone': '\uE7F6', 'mouse': '\uE962', 'keyboard': '\uE765', 'gamepad': '\uE7FC',
    'other': '\uE702', 'bluetooth': '\uE702', 'settings': '\uE713', 'pin': '\uE718',
    'pinned': '\uE840', 'close': '\uE8BB', 'refresh': '\uE72C', 'edit': '\uE70F',
    'case': '\uE7B8', 'earbud': '\uE7F6', 'speaker': '\uE767', 'swap': '\uE8AB', 'phone': '\uE8EA', 'computer': '\uE7F8', 'peripheral': '\uE962',
    'search': '\uE721', 'link': '\uE71B', 'tv': '\uE7F4', 'battery_low': '\uE851',
}

DEFAULT_SETTINGS = {
    "low_battery_alert": True,
    "low_battery_threshold": 20,
    "tray_show_percent": True,
    "full_charge_alert": True,
    "full_charge_level": 100,
    "connection_alert": True,
    "hotkey_enabled": True,
    "show_other_devices": True,
    "theme": 'system',
    # 'system' theo ngôn ngữ Windows; file cài đặt cũ chưa có khóa này được giữ tiếng Việt (load_settings).
    "language": 'system',
    "compact_mode": False,
    "aliases": {},
    "mini_widget": False,
    "mini_widget_pos": None,
    "popup_on_connect": True,
    "alert_overlay": True,
    "alert_sound": True,
    "time_alert_minutes": 15,
    "show_audio_control": True,
    # Địa chỉ quảng bá Fast Pair của chiếc JBL đã liên kết. Gói Fast Pair không mang định danh Windows của tai
    # nghe, nên chỉ đọc đúng địa chỉ này (không đoán theo tên); đổi tai nghe thì sửa giá trị này trong file cài đặt.
    "fast_pair_address": "00:00:00:00:00:00",  # chưa có: chọn qua "Xem pin từng bên" (ui_split.py)
    # Tai nghe true wireless khác: {id thiết bị Windows: địa chỉ Fast Pair} người dùng đã chọn (ui_split.py).
    "fast_pair_bindings": {},
}
TIME_ALERT_CHOICES = (0, 15, 30)
HOTKEY_HINT = 'Ctrl+Alt+B: mở/ẩn cửa sổ · Ctrl+Alt+H: đổi loa ↔ tai nghe'
AUDIO_HOTKEY_ID = 2  # Ctrl+Alt+H: đổi loa ↔ tai nghe
THRESHOLD_CHOICES = (10, 15, 20, 30)
LANGUAGE_CHOICES = ('system', 'vi', 'en')

# Bản dịch tiếng Anh của chuỗi giao diện trong file này (khóa = chuỗi gốc tiếng Việt, xem i18n.py).
_EN = {
    # Thanh trên cùng, thẻ thiết bị
    "Pin tai nghe": "Battery",
    "Thiết bị BT": "Bluetooth devices",
    "Chưa có tai nghe kết nối": "No headphones connected",
    "○ Đang tìm tai nghe": "○ Looking for headphones",
    "Chưa có số pin": "No battery info",
    "Tai trái": "Left",
    "Tai phải": "Right",
    "Hộp sạc": "Case",
    "24 giờ": "24 hours",
    "7 ngày": "7 days",
    "Cập nhật lần cuối: Đang chờ...": "Last updated: waiting...",
    "Kết nối": "Connect",
    "Đang kết nối…": "Connecting…",
    "Xem pin từng bên": "Show each earbud",
    "Âm thanh phát ra": "Sound output",
    "Loa": "Speakers",
    "Tai nghe": "Headphones",
    "THIẾT BỊ ĐANG KẾT NỐI": "CONNECTED DEVICES",
    # Trạng thái kết nối
    "○ Không kết nối · Bluetooth của máy đang tắt": "○ Not connected · Bluetooth is off",
    "○ Chưa đọc được kết nối": "○ Can't read connections",
    "● Trực tiếp · {source}": "● Live · {source}",
    "● Đã kết nối": "● Connected",
    "● Đã kết nối · chưa có pin": "● Connected · no battery info",
    "○ Bluetooth PC tắt": "○ PC Bluetooth is off",
    "○ Không kết nối": "○ Not connected",
    "○ Chưa có tai nghe kết nối": "○ No headphones connected",
    # Số pin
    "Không kết nối": "Not connected",
    "Chưa có tai nghe": "No headphones",
    "Lần cuối: {level}% lúc {time}": "Last seen: {level}% at {time}",
    "Bật tai nghe hoặc kết nối Bluetooth": "Turn on your headphones or connect them via Bluetooth",
    "Windows chưa báo pin cho thiết bị này": "Windows hasn't reported a battery level for this device",
    "Pin tốt": "Good",
    "Pin trung bình": "Fair",
    "Pin yếu": "Low",
    "Đọc trực tiếp, chính xác 1%": "Read directly, accurate to 1%",
    "Windows báo theo bậc 10%": "Reported by Windows in 10% steps",
    "Dữ liệu cũ": "Old data",
    "Cập nhật": "Updated",
    "Chưa có dữ liệu": "No data yet",
    "Pin từ tai nghe (BLE) · cập nhật trực tiếp": "Battery from the headphones (BLE) · live",
    "Pin do Windows cung cấp": "Battery reported by Windows",
    "{prefix}: {time} · từ tai nghe": "{prefix}: {time} · from the headphones",
    "{prefix}: {time}": "{prefix}: {time}",
    "≈ còn {hours} giờ {minutes} phút": "≈ {hours} h {minutes} min left",
    "{days} ngày trước": "{days} days ago",
    "{hours} giờ trước": "{hours} h ago",
    "Đang thu thập dữ liệu…": "Collecting data…",
    "bây giờ": "now",
    "Chưa kết nối": "Not connected",
    # Tên khay
    "{name} | Không kết nối · Bluetooth của máy đang tắt": "{name} | Not connected · Bluetooth is off",
    "{name} | Không kết nối": "{name} | Not connected",
    "{name} | Pin: {level}% ({source})": "{name} | Battery: {level}% ({source})",
    "{name} | Đã kết nối · chưa có pin": "{name} | Connected · no battery info",
    " | Hộp: {level}%": " | Case: {level}%",
    # Menu khay
    "Mở bảng điều khiển": "Open",
    "Làm mới pin ngay": "Refresh battery now",
    "Chọn tai nghe": "Choose headphones",
    "Đổi loa ↔ tai nghe (Ctrl+Alt+H)": "Switch speakers ↔ headphones (Ctrl+Alt+H)",
    "Thông báo pin yếu": "Low battery alerts",
    "Khởi động cùng Windows": "Start with Windows",
    "Cài đặt...": "Settings...",
    "Thiết bị Bluetooth xung quanh…": "Nearby Bluetooth devices…",
    "Cửa sổ nổi": "Floating widget",
    "Thoát": "Exit",
    # Thông báo
    "Đã kết nối {name}": "Connected: {name}",
    "đã ngắt {names}": "disconnected: {names}",
    "Kết nối Bluetooth": "Bluetooth connection",
    "{name}{part} đã đạt {level}% pin.": "{name}{part} has reached {level}%.",
    "Pin đã sạc đầy": "Fully charged",
    "{name} còn {level}% pin. Hãy sạc sớm.": "{name} is at {level}%. Charge it soon.",
    "Pin tai nghe yếu": "Headphone battery low",
    "Pin thiết bị yếu": "Device battery low",
    "{name} còn khoảng {minutes} phút pin ({level}%).": "{name} has about {minutes} min of battery left ({level}%).",
    "Sắp hết pin tai nghe": "Headphone battery almost empty",
    "Sắp hết pin thiết bị": "Device battery almost empty",
    "Đã chép log": "Log copied",
    "Dán (Ctrl+V) vào tin nhắn để gửi": "Paste it (Ctrl+V) into a message to send it",
    "(chưa có log)": "(no log yet)",
    # Cài đặt
    "Cài đặt": "Settings",
    "HỆ THỐNG": "SYSTEM",
    "Hiện % pin trên biểu tượng khay": "Show battery % on the tray icon",
    "Giao diện": "Theme",
    "Hệ thống": "System",
    "Tối": "Dark",
    "Sáng": "Light",
    "Ngôn ngữ": "Language",
    "Theo Windows": "Same as Windows",
    "Tiếng Việt": "Vietnamese",
    "Chế độ gọn": "Compact mode",
    "Nút đổi loa / tai nghe trên bảng pin": "Speakers / headphones switch on the panel",
    "Cửa sổ nổi nhỏ": "Small floating widget",
    "THÔNG BÁO": "NOTIFICATIONS",
    "Thông báo khi pin xuống thấp": "Notify when the battery is low",
    "Ngưỡng cảnh báo": "Alert level",
    "Thông báo sạc đầy": "Notify when fully charged",
    "Ngưỡng sạc đầy": "Full charge level",
    "Thông báo kết nối / ngắt kết nối": "Notify on connect / disconnect",
    "Hiện bảng nhỏ khi vừa kết nối tai nghe": "Show a popup when headphones connect",
    "Cảnh báo nổi trên màn hình (cả khi chơi game)": "On-screen alerts (even while gaming)",
    "Phát âm báo khi pin yếu": "Play a sound when the battery is low",
    "Báo trước khi hết pin (theo ước tính)": "Warn before the battery runs out (estimated)",
    "Tắt": "Off",
    "15 phút": "15 min",
    "30 phút": "30 min",
    "Nâng cao": "Advanced",
    "PHÍM TẮT & THIẾT BỊ": "SHORTCUTS & DEVICES",
    "Phím tắt Ctrl+Alt+B / Ctrl+Alt+H": "Keyboard shortcuts Ctrl+Alt+B / Ctrl+Alt+H",
    HOTKEY_HINT: "Ctrl+Alt+B: show/hide window · Ctrl+Alt+H: switch speakers ↔ headphones",
    "Hiện cả chuột, bàn phím, tay cầm, điện thoại": "Also show mice, keyboards, controllers and phones",
    "BÁO LỖI": "REPORT A PROBLEM",
    "Gặp lỗi? Bấm “Chép log” rồi dán (Ctrl+V) gửi cho người hỗ trợ.":
        "Something wrong? Click “Copy log” and paste it (Ctrl+V) to whoever is helping you.",
    "Chép log": "Copy log",
    "PIN TỪNG BÊN (FAST PAIR)": "EACH EARBUD (FAST PAIR)",
    "Tai nghe true wireless có Fast Pair: chọn đúng tai nghe trong danh sách\n"
    "đang phát pin gần máy. Không tự nhận theo tên tai nghe.":
        "True wireless earbuds with Fast Pair: pick yours from the list of\n"
        "earbuds sending battery levels nearby. Never matched by name.",
    "Chọn tai nghe để xem pin từng bên": "Choose earbuds to see each side",
    "Bỏ": "Unlink",
    "Đã liên kết: {name}": "Linked: {name}",
    "Đã liên kết {count} tai nghe khác (hiện không được chọn).": "{count} other headphones linked (not selected).",
    " Bấm “Bỏ” để gỡ liên kết chiếc JBL.": " Click “Unlink” to unlink the JBL.",
    "Chưa liên kết tai nghe nào.": "No headphones linked.",
    "Lưu & đóng": "Save & close",
    "Thư mục log": "Log folder",
}
i18n.register(_EN)


def configure_logging():
    """Importing the UI for tests must not open files in the source folder."""
    handler = RotatingFileHandler(os.path.join(DATA_DIR, "app.log"), maxBytes=1_000_000,
                                  backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(threadName)s %(message)s",
                        handlers=[handler])
    # Lỗi không bắt được ở luồng chính hoặc luồng nền (Bluetooth, âm thanh, phím tắt) cũng vào log.
    sys.excepthook = lambda kind, value, tb: logging.critical("Unhandled error", exc_info=(kind, value, tb))
    threading.excepthook = lambda args: logging.critical(
        "Unhandled error in thread %s", args.thread.name if args.thread else "?",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback))


LOG_LINES_TO_COPY = 200


def read_log_tail(lines=LOG_LINES_TO_COPY):
    """Phần cuối log kèm phiên bản, để người dùng dán gửi khi báo lỗi.

    Đã che địa chỉ Bluetooth và tên tài khoản Windows: log được dán cho người khác đọc."""
    try:
        with open(os.path.join(DATA_DIR, "app.log"), encoding="utf-8", errors="replace") as file:
            tail = file.readlines()[-lines:]
    except OSError:
        tail = [tr("(chưa có log)") + "\n"]
    return f"{APP_NAME} {APP_VERSION} · Windows {sys.getwindowsversion().build}\n" + redact("".join(tail))


FEEDBACK_LOG_LINES = 60


def is_startup_enabled():
    if msix_runtime.is_packaged():
        return msix_runtime.startup_enabled()  # bản Microsoft Store: StartupTask, không dùng registry
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, REG_NAME)
            return bool(val)
    except Exception:
        return False


STALE_EXTRACTION_SECONDS = 120


def cleanup_stale_extractions():
    """Bản một file giải nén vào %TEMP%\\_MEI… mỗi lần mở; bị tắt cưỡng bức (Task Manager, tắt máy, cập nhật) thì
    thư mục ở lại (~36 MB mỗi lần). Chỉ gọi khi đã là phiên duy nhất đang chạy. Chỉ xóa thư mục của chính app này,
    cũ hơn 2 phút, và phải đổi tên được trước: Windows không cho đổi tên thư mục đang có file mở, nên thư mục một
    phiên khác đang dùng không bao giờ bị xóa dở. Trả về số thư mục đã xóa."""
    own = getattr(sys, '_MEIPASS', None)
    if not getattr(sys, 'frozen', False) or not own or not os.path.basename(own).startswith('_MEI'):
        return 0  # bản thư mục / chạy từ mã nguồn: không giải nén gì
    parent, removed = os.path.dirname(own), 0
    for name in os.listdir(parent):
        folder = os.path.join(parent, name)
        try:
            if (not name.startswith('_MEI') or os.path.normcase(folder) == os.path.normcase(own)
                    or not os.path.isfile(os.path.join(folder, 'assets', 'app_icon.ico'))
                    or not os.path.isdir(os.path.join(folder, 'customtkinter'))
                    or time.time() - os.path.getmtime(folder) < STALE_EXTRACTION_SECONDS):
                continue
            trash = os.path.join(parent, 'BluetoothMonitor-old-' + name)
            os.rename(folder, trash)  # thất bại nếu đang dùng -> bỏ qua
        except OSError:
            continue
        shutil.rmtree(trash, ignore_errors=True)
        removed += 1
    if removed:
        logging.info('Removed %d leftover one-file extraction folder(s)', removed)
    return removed


def startup_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    pythonw_path = os.path.join(sys.prefix, "pythonw.exe")
    return f'"{pythonw_path}" "{os.path.join(APP_DIR, "main.py")}" --minimized'


def migrate_startup_entry():
    """Giữ mục "khởi động cùng Windows" trỏ đúng exe đang chạy.

    Mục của tên cũ (JBL Battery Monitor), hoặc mục trỏ tới exe khác (chuyển từ bản thư mục sang bản một
    file, chép app sang chỗ khác) thì chuyển sang exe hiện tại; người dùng đã tắt thì không bật lại."""
    if msix_runtime.is_packaged():
        return  # bản Microsoft Store dùng StartupTask; mục Run trỏ vào WindowsApps vô dụng
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
            enabled = False
            try:
                winreg.QueryValueEx(key, OLD_REG_NAME)
                winreg.DeleteValue(key, OLD_REG_NAME)
                enabled = True
            except FileNotFoundError:
                pass
            try:
                current, _ = winreg.QueryValueEx(key, REG_NAME)
                enabled = enabled or current != startup_command()
            except FileNotFoundError:
                pass
        if enabled:
            set_startup(True)
    except Exception:
        logging.exception("Cannot migrate startup entry")


def set_startup(enable: bool):
    if msix_runtime.is_packaged():
        return msix_runtime.set_startup(enable)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, REG_NAME, 0, winreg.REG_SZ, startup_command())
            else:
                try:
                    winreg.DeleteValue(key, REG_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        logging.exception("Cannot change startup setting")
        return False


def load_settings():
    """Read local settings; unknown keys are kept so older tools still work."""
    existing = False  # đã có file cài đặt (bản cũ), kể cả file hỏng
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as file:
            existing = True
            data = json.load(file)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    # Bản cũ chưa có khóa này vốn chạy tiếng Việt: giữ tiếng Việt. Chỉ cài mới mới theo ngôn ngữ Windows.
    if data.get('language') not in LANGUAGE_CHOICES:
        data['language'] = 'vi' if existing else DEFAULT_SETTINGS['language']
    for key, value in DEFAULT_SETTINGS.items():
        if key == 'mini_widget_pos':
            continue
        if type(data.get(key)) is not type(value):
            data[key] = copy.deepcopy(value)
    if data["low_battery_threshold"] not in THRESHOLD_CHOICES:
        data["low_battery_threshold"] = DEFAULT_SETTINGS["low_battery_threshold"]
    if data['full_charge_level'] not in (80, 90, 100):
        data['full_charge_level'] = 100
    if data['time_alert_minutes'] not in TIME_ALERT_CHOICES:
        data['time_alert_minutes'] = DEFAULT_SETTINGS['time_alert_minutes']
    if data['theme'] not in ('system', 'dark', 'light'):
        data['theme'] = 'system'
    address = r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}'
    if not re.fullmatch(address, data['fast_pair_address'], re.I):
        data['fast_pair_address'] = DEFAULT_SETTINGS['fast_pair_address']
    # Mỗi địa chỉ thuộc một tai nghe (giữ liên kết sau cùng); chiếc JBL đã có bộ đọc riêng thì không lặp ở đây.
    by_address = {}
    for key, value in data['fast_pair_bindings'].items():
        if (isinstance(key, str) and key and key != data.get('jbl_battery_device_id')
                and isinstance(value, str) and re.fullmatch(address, value, re.I)):
            by_address[value.upper()] = key
    data['fast_pair_bindings'] = {key: value for value, key in by_address.items()}
    data['aliases'] = {key: value.strip() for key, value in data['aliases'].items()
                       if isinstance(key, str) and key and isinstance(value, str) and value.strip()}
    position = data.get('mini_widget_pos')
    data['mini_widget_pos'] = (position if isinstance(position, list) and len(position) == 2
                               and all(type(value) is int for value in position) else None)
    return data


def save_settings(settings):
    temporary = SETTINGS_PATH + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(settings, file, indent=2, ensure_ascii=False)
        os.replace(temporary, SETTINGS_PATH)
        return True
    except OSError:
        logging.exception("Cannot save settings")
        return False


def load_battery_device_id():
    """Local binding for the original JBL reader; never infer ownership from a name."""
    value = load_settings().get('jbl_battery_device_id')
    return value if isinstance(value, str) and value else None


def read_system_theme():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
            return 'light' if winreg.QueryValueEx(key, 'AppsUseLightTheme')[0] else 'dark'
    except OSError:
        return 'dark'


class MiniWidgetWindow(ctk.CTkToplevel):
    # A frameless window has no native titlebar to recolor or temporarily withdraw.
    _deactivate_windows_window_header_manipulation = True


class JblFlyoutApp(ToastMixin, AudioMixin, NearbyMixin, SplitBatteryMixin, FeedbackMixin, ctk.CTk):
    def __init__(self, start_minimized=False):
        # Claim the instance before creating any Tk window, tray icon or BLE worker.
        self.ipc_sock = try_single_instance()
        super().__init__()
        self._ui_queue = queue.Queue()
        self._closing = False
        self.is_pinned = False
        self._ui_poll_id = self.after(UI_POLL_MS, self._drain_ui_queue)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        families = set(tkfont.families(self))
        ctk.ThemeManager.theme["CTkFont"]["family"] = next(
            (family for family in ("Segoe UI Variable Text", "Segoe UI") if family in families),
            ctk.ThemeManager.theme["CTkFont"]["family"])
        self._image_cache = {}

        self.title(APP_NAME)
        self.configure(fg_color=BG)
        self.win_width = 380
        self.win_height = 300
        self.resizable(False, False)

        if os.path.exists(ICON_ICO):
            self.iconbitmap(ICON_ICO)

        self.start_minimized = start_minimized
        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        # Cài đặt người dùng
        self.settings = load_settings()
        i18n.set_language(i18n.resolve(self.settings['language']))
        self._apply_theme(rebuild=False)

        # Trạng thái thiết bị
        self.left_level = None
        self.right_level = None
        self.case_level = None
        self.charging_info = {"left": None, "right": None, "case": None}
        self.is_connected = False
        self.connected_headphones = []
        self.active_headphone = None
        # Tai nghe vừa dùng và mức pin cuối cùng: vẫn hiện (màu xám) sau khi ngắt kết nối.
        self._last_device = None
        self._last_level = None  # (level, "HH:MM")
        self.connection_error = None
        self._connection_snapshot = None  # thiết bị đã báo cho người dùng
        self._pending_snapshot = None  # thay đổi đang chờ ổn định
        self._announce_timer = None
        self._audio_menu_version = 0  # tăng khi thiết bị phát đổi: menu khay dựng lại dấu tích
        self._before_error = None  # (id đã kết nối, id đang chọn) trước một lần quét lỗi
        self.battery_is_fresh = False
        self.windows_battery_level = None
        self.gatt_levels = {}
        self.battery_device_id = load_battery_device_id()
        self.status_text = "Đang tìm dữ liệu tai nghe..."
        self.last_update_str = "Chưa có dữ liệu"
        self._low_alerted = set()
        self._full_alerted = set()
        # Full charge reached but not yet announced (e.g. alerts were off).
        self._full_pending = set()
        self._previous_levels = {}
        self._binding_generation = 0
        self._split_picker = None  # bảng chọn tai nghe để xem pin từng bên
        self._feedback_win = None  # cửa sổ góp ý (ui_feedback.py)
        self._donate_win = None  # cửa sổ ủng hộ (mã QR)
        self._split_discovery_until = 0.0  # đang nghe thử gói Fast Pair tới lúc này (ui_split.py)
        self._split_available = set()  # id tai nghe đã thấy có tai nghe true wireless chưa liên kết ở gần
        self._tray_icon_key = None
        self._tray_menu_key = None
        self._settings_win = None
        self._mini_window = None
        self._mini_label = None
        self._user_position = None
        self.history = BatteryHistory(os.path.join(DATA_DIR, 'battery_history.jsonl'))
        self._components = {}
        # AirPods/Beats đang kết nối (nhận theo tên Windows, xem _sync_apple_scanner) và bộ quét gói Apple.
        self._apple_device_id = None
        self.apple_scanner = apple_pods.ApplePodsScanner(self._on_apple_update)
        self.fast_pair_scanner = fast_pair.FastPairScanner(self._on_fast_pair_update)
        self._chart_days = 1

        # Biến di chuyển cửa sổ (Drag)
        self._drag_start_x = 0
        self._drag_start_y = 0

        self._build_ui()
        self._position_window()

        # Khởi tạo System Tray
        self.tray_icon = None
        self._init_tray()

        # Khởi tạo Core BLE Driver
        self.core = JblCore(
            target_address=self.settings['fast_pair_address'],
            on_update=self._on_battery_update,
            on_status=self._on_status_update,
            state_path=os.path.join(DATA_DIR, "battery_state.json")
        )
        self._update_core_enabled()
        self.core.start()
        self.connection_monitor = HeadphoneConnectionMonitor(self._on_headphones_update)
        self.connection_monitor.set_include_other(self.settings['show_other_devices'])
        self.connection_monitor.start()
        self.gatt_monitor = GattBatteryMonitor(self._on_gatt_update)
        self.gatt_monitor.start()
        self._toast = None
        self._toast_timer = None
        self._time_alerted = set()
        self._previous_output = None  # thiết bị phát trước khi chuyển sang tai nghe
        self._nearby_win = None
        self._nearby_scanner = None
        self._nearby_key = None
        self._nearby_timer = None
        self._pairing_ids = set()
        self._connecting = set()  # (id, kết nối?) đang gửi lệnh và chờ Windows xác nhận
        self._details_request = 0
        self.hotkey_worker = HotkeyWorker(
            self._on_hotkey, self._on_hotkey_status,
            extra={AUDIO_HOTKEY_ID: (0x4003, ord('H'), self._on_audio_hotkey)})
        self.hotkey_error = None
        if self.settings['hotkey_enabled']:
            self.hotkey_worker.start()

        # IPC Socket Single Instance
        start_ipc_listener(self, self.ipc_sock)

        self._load_cached_state()
        self._apply_compact_mode()
        self._history_timer = self.after(60000, self._history_tick)

        if self.start_minimized:
            self.withdraw()
        else:
            self.deiconify()

    def _enqueue_ui(self, callback, *args):
        """Worker threads only enqueue; all Tk calls run on the main thread."""
        if not self._closing:
            self._ui_queue.put((callback, args))

    def _drain_ui_queue(self):
        # Bounded so a burst of worker updates cannot starve Tk's own events.
        for _ in range(100):
            if self._closing:
                break
            try:
                callback, args = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback(*args)
            except Exception:
                logging.exception("UI callback failed")
        if not self._closing:
            self._ui_poll_id = self.after(UI_POLL_MS, self._drain_ui_queue)

    def report_callback_exception(self, exc_type, exc_value, traceback):
        logging.error("Tk callback failed", exc_info=(exc_type, exc_value, traceback))

    def _on_hotkey(self):
        self._enqueue_ui(self.toggle_window)

    def _on_audio_hotkey(self):
        self._enqueue_ui(self._toggle_audio_output)

    def _on_hotkey_status(self, error):
        self._enqueue_ui(self._apply_hotkey_status, error)

    def _apply_hotkey_status(self, error):
        self.hotkey_error = error
        win = self._settings_win
        if win and win.winfo_exists() and hasattr(win, 'lbl_hotkey_status'):
            win.lbl_hotkey_status.configure(text=tr(error or HOTKEY_HINT))

    # --- VỊ TRÍ & KÍCH THƯỚC ---
    def _fit_height(self):
        self.update_idletasks()
        self.win_height = self.container.winfo_reqheight() + 4

    def _position_window(self):
        """Đặt flyout ở góc phải dưới, ngay trên taskbar; giữ vị trí nếu người dùng đã kéo."""
        self._fit_height()
        if self._user_position:
            pos_x, pos_y = self._user_position
        else:
            area = get_work_area()
            if area:
                margin = 12
                pos_x = area[2] - self.win_width - margin
                pos_y = area[3] - self.win_height - margin - 32  # chừa thanh tiêu đề
            else:
                pos_x = (self.winfo_screenwidth() - self.win_width) // 2
                pos_y = (self.winfo_screenheight() - self.win_height) // 2
        self.geometry(f"{self.win_width}x{self.win_height}+{max(pos_x, 0)}+{max(pos_y, 0)}")

    def _resize_to_content(self):
        self._fit_height()
        self.geometry(f"{self.win_width}x{self.win_height}")

    def _load_cached_state(self):
        # Persisted values are history, not proof of current charging state.
        self.left_level = self.right_level = self.case_level = None
        self.charging_info = {"left": None, "right": None, "case": None}
        self.last_update_str = "Chưa có dữ liệu"
        self._update_ui_values()

    # --- GIAO DIỆN ---
    def _style_title_bar(self, window):
        """Tô thanh tiêu đề Windows cùng màu nền app (Windows 11; Windows 10 chỉ đổi sáng/tối)."""
        try:
            window.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
            dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute

            def colorref(color):
                return ctypes.c_int(int(color[5:7] + color[3:5] + color[1:3], 16))
            dark = ctypes.c_int(1 if self._theme == 'dark' else 0)
            dwm(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))  # USE_IMMERSIVE_DARK_MODE
            for attribute, color in ((34, BORDER), (35, BG), (36, MUTED)):  # border/caption/text
                value = colorref(color)
                dwm(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            logging.debug("Cannot style title bar", exc_info=True)

    def _glyph(self, name, size=16, color=None):
        """Biểu tượng Fluent dựng thành ảnh, để không phụ thuộc emoji của font chữ."""
        color = color or MUTED
        key = ('glyph', name, size, color)
        if key not in self._image_cache:
            scale = 4
            image = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
            for path in ICON_FONTS:
                try:
                    font = ImageFont.truetype(path, size * scale)
                    break
                except OSError:
                    font = None
            if font is not None:
                ImageDraw.Draw(image).text((size * scale / 2, size * scale / 2), GLYPHS[name],
                                           font=font, fill=color, anchor="mm")
            self._image_cache[key] = ctk.CTkImage(image, size=(size, size))
        return self._image_cache[key]

    def _ring_image(self, level, color, size=112, width=10):
        """Vòng pin vẽ mịn bằng PIL (canvas Tk không khử răng cưa)."""
        key = ('ring', level, color, TRACK, size)
        if key not in self._image_cache:
            scale = 4
            px, stroke = size * scale, width * scale
            image = Image.new("RGBA", (px, px), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            box = (0, 0, px - 1, px - 1)
            draw.ellipse(box, outline=TRACK, width=stroke)
            if level:
                end = -90 + 360 * level / 100
                draw.arc(box, -90, end, fill=color, width=stroke)
                # Đầu vòng bo tròn
                radius = (px - stroke) / 2
                for angle in (-90, end):
                    cx = px / 2 + radius * math.cos(math.radians(angle))
                    cy = px / 2 + radius * math.sin(math.radians(angle))
                    draw.ellipse((cx - stroke / 2, cy - stroke / 2, cx + stroke / 2, cy + stroke / 2),
                                 fill=color)
            self._image_cache[key] = ctk.CTkImage(image, size=(size, size))
        return self._image_cache[key]

    def _segmented(self, parent, values, command, height=28):
        # Một màu chữ cho cả nút chọn và không chọn, nên nền nút chọn phải nhạt (đọc được cả hai theme).
        # Nút hiện chữ đã dịch; command nhận lại giá trị gốc (tiếng Việt) nên logic cũ không đổi. set() dùng tr(gốc).
        internal = {tr(value): value for value in values}
        return ctk.CTkSegmentedButton(
            parent, values=list(internal), height=height, corner_radius=8, font=ctk.CTkFont(size=12),
            fg_color=BG, unselected_color=BG, unselected_hover_color=CARD_HOVER,
            selected_color=ACCENT_SOFT, selected_hover_color=ACCENT_SOFT, text_color=TEXT,
            command=lambda shown: command(internal.get(shown, shown)))

    def _style_dialog(self, window):
        """CTkToplevel đặt lại icon mặc định sau ~200 ms; đặt icon app và thanh tiêu đề sau đó."""
        def apply():
            if not window.winfo_exists():
                return
            if os.path.exists(ICON_ICO):
                window.iconbitmap(ICON_ICO)
            self._style_title_bar(window)
        window.after(250, apply)

    def _icon_button(self, parent, glyph, command, color=None, size=15):
        return ctk.CTkButton(parent, text="", image=self._glyph(glyph, size, color or MUTED),
                             width=30, height=30, corner_radius=8,
                             fg_color="transparent", hover_color=CARD_HOVER, command=command)

    def _make_battery_row(self, label, glyph):
        row = ctk.CTkFrame(self.battery_list_frame, fg_color=BG, corner_radius=12)
        ctk.CTkLabel(row, text="", image=self._glyph(glyph, 16), width=24).pack(
            side="left", padx=(12, 6), pady=10)
        name = ctk.CTkLabel(row, text=tr(label), font=ctk.CTkFont(size=12),
                            text_color=MUTED, width=64, anchor="w")
        name.pack(side="left")
        value = ctk.CTkLabel(row, text="--%", font=ctk.CTkFont(size=14, weight="bold"),
                             text_color=TEXT, width=62, anchor="e")
        value.pack(side="right", padx=(0, 12))
        bar = ctk.CTkProgressBar(row, height=6, corner_radius=3, fg_color=TRACK,
                                 progress_color=GOOD)
        bar.set(0)
        bar.pack(side="left", fill="x", expand=True, padx=(6, 8))
        return row, name, bar, value

    def _build_ui(self):
        self._alias_entry = None
        self.container = ctk.CTkFrame(self, corner_radius=0, fg_color=BG)
        self.container.pack(fill="both", expand=True)

        # 1. THANH TRÊN CÙNG (kéo để di chuyển)
        self.top_bar = ctk.CTkFrame(self.container, fg_color="transparent", height=34)
        self.top_bar.pack(fill="x", padx=12, pady=(10, 4))

        logo = self._image_cache.get('logo')
        if logo is None and os.path.exists(ICON_PNG):
            with Image.open(ICON_PNG) as source:
                logo = self._image_cache['logo'] = ctk.CTkImage(source.copy(), size=(18, 18))
        self.lbl_top_brand = ctk.CTkLabel(
            self.top_bar, text="  " + tr("Pin tai nghe"), image=logo, compound="left",
            font=ctk.CTkFont(size=13, weight="bold"), text_color=TEXT)
        self.lbl_top_brand.pack(side="left", padx=2)
        for widget in (self.top_bar, self.lbl_top_brand):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._do_drag)

        self.btn_close = self._icon_button(self.top_bar, "close", self.hide_to_tray, size=12)
        self.btn_close.pack(side="right", padx=1)
        self.btn_settings = self._icon_button(self.top_bar, "settings", self._show_settings_dialog)
        self.btn_settings.pack(side="right", padx=1)
        self.btn_pin = self._icon_button(self.top_bar, "pin", self._toggle_pin, color=DIM)
        self.btn_pin.pack(side="right", padx=1)
        # Nút có nền + viền để nhìn ra là nút (bản chữ mờ trước đây trông như nhãn trang trí).
        self.btn_bluetooth = ctk.CTkButton(self.top_bar, text=tr('Thiết bị BT'), width=104, height=30,
                                          image=self._glyph('bluetooth', 14, ACCENT), compound='left',
                                          font=ctk.CTkFont(size=12, weight='bold'), corner_radius=8,
                                          fg_color=CARD, hover_color=CARD_HOVER, border_width=1,
                                          border_color=BORDER, text_color=TEXT,
                                          command=self._show_nearby_panel)
        self.btn_bluetooth.pack(side='right', padx=(1, 6))

        # 2. THẺ THIẾT BỊ
        self.device_card = ctk.CTkFrame(self.container, corner_radius=16, fg_color=CARD,
                                        border_color=BORDER, border_width=1)
        self.device_card.pack(fill="x", padx=12, pady=(2, 10))

        self.title_row = ctk.CTkFrame(self.device_card, fg_color="transparent")
        self.title_row.pack(fill="x", padx=16, pady=(14, 4))

        self.lbl_headphone_icon = ctk.CTkLabel(
            self.title_row, text="", image=self._glyph('headphone', 20, ACCENT),
            width=42, height=42, corner_radius=12, fg_color=ACCENT_SOFT)
        self.lbl_headphone_icon.pack(side="left", padx=(0, 12))

        self.btn_refresh = self._icon_button(self.title_row, "refresh", self._on_refresh_clicked,
                                             color=TEXT)
        self.btn_refresh.configure(fg_color=BG)
        self.btn_refresh.pack(side="right")
        self.btn_alias = self._icon_button(self.title_row, 'edit', self._edit_alias, size=14)
        self.btn_alias.pack(side='right', padx=4)

        self.title_text = ctk.CTkFrame(self.title_row, fg_color="transparent")
        self.title_text.pack(side="left", fill="x", expand=True)
        self.lbl_device_title = ctk.CTkLabel(
            self.title_text, text=tr("Chưa có tai nghe kết nối"),
            font=ctk.CTkFont(size=15, weight="bold"), wraplength=200,
            justify="left", anchor="w", text_color=TEXT)
        self.lbl_device_title.pack(fill="x")
        self.lbl_device_title.bind("<Double-Button-1>", lambda event: self._edit_alias())
        self.lbl_status_dot = ctk.CTkLabel(
            self.title_text, text=tr("○ Đang tìm tai nghe"), anchor="w",
            font=ctk.CTkFont(size=11), text_color=WARN, height=16)
        self.lbl_status_dot.pack(fill="x")

        # 3. KHU VỰC PIN
        self.battery_list_frame = ctk.CTkFrame(self.device_card, fg_color="transparent")
        self.battery_list_frame.pack(fill="x", padx=14, pady=(6, 2))

        # Pin chung do Windows cung cấp: số lớn + thanh
        self.row_device = ctk.CTkFrame(self.battery_list_frame, fg_color="transparent")
        self.lbl_device_battery = ctk.CTkLabel(
            self.row_device, text="--%", image=self._ring_image(0, TRACK), compound="center",
            font=ctk.CTkFont(size=26, weight="bold"), text_color=DIM)
        self.lbl_device_battery.pack(side="left", pady=(4, 6))
        # Thanh pin cũ vẫn giữ giá trị (dùng cho kiểm thử/tương thích) nhưng không hiển thị.
        self.bar_device = ctk.CTkProgressBar(self.row_device, height=10, corner_radius=5,
                                             fg_color=TRACK, progress_color=GOOD)
        self.bar_device.set(0)
        battery_info = ctk.CTkFrame(self.row_device, fg_color="transparent")
        battery_info.pack(side="left", fill="x", expand=True, padx=(18, 0))
        self.lbl_battery_state = ctk.CTkLabel(
            battery_info, text=tr("Chưa có số pin"), anchor="w",
            font=ctk.CTkFont(size=16, weight="bold"), text_color=TEXT)
        self.lbl_battery_state.pack(fill="x")
        self.lbl_battery_source = ctk.CTkLabel(
            battery_info, text="", anchor="w", justify="left", wraplength=170,
            font=ctk.CTkFont(size=12), text_color=MUTED)
        self.lbl_battery_source.pack(fill="x")

        # Pin chi tiết Fast Pair: trái / phải / hộp
        self.row_left, self.lbl_l_name, self.bar_left, self.lbl_l_val = \
            self._make_battery_row("Tai trái", "earbud")
        self.row_right, self.lbl_r_name, self.bar_right, self.lbl_r_val = \
            self._make_battery_row("Tai phải", "earbud")
        self.row_case, self.lbl_c_name, self.bar_case, self.lbl_c_val = \
            self._make_battery_row("Hộp sạc", "case")
        self.bar_case.configure(progress_color=CHARGING)

        self.lbl_remaining = ctk.CTkLabel(self.device_card, text='', anchor='w',
                                          text_color=MUTED, font=ctk.CTkFont(size=12))
        self.chart_controls = self._segmented(
            self.device_card, ['24 giờ', '7 ngày'], height=26,
            command=lambda value: self._set_chart_days(1 if value == '24 giờ' else 7))
        self.chart_controls.set(tr('24 giờ' if getattr(self, '_chart_days', 1) == 1 else '7 ngày'))
        self.history_canvas = tk.Canvas(self.device_card, height=82, bg=CARD,
                                         highlightthickness=0)
        self.history_canvas.bind('<Configure>', lambda event: self._draw_history())

        self.card_footer = ctk.CTkFrame(self.device_card, fg_color="transparent")
        self.card_footer.pack(fill="x", padx=14, pady=(4, 12))
        self.lbl_updated = ctk.CTkLabel(
            self.card_footer, text=tr("Cập nhật lần cuối: Đang chờ..."),
            font=ctk.CTkFont(size=11), text_color=DIM, anchor="w")
        self.lbl_updated.pack(side="left")
        # Tai nghe vừa ngắt (thẻ xám): kết nối lại ngay từ đây, không cần mở Cài đặt Bluetooth.
        self.btn_connect = ctk.CTkButton(
            self.card_footer, text=tr("Kết nối"), width=96, height=26, corner_radius=8, fg_color=ACCENT,
            hover_color=blend(ACCENT, "#000000", .85), text_color="#ffffff", text_color_disabled="#ffffff",
            font=ctk.CTkFont(size=12, weight="bold"), command=self._connect_offline_device)
        self.btn_split = ctk.CTkButton(
            self.card_footer, text=tr("Xem pin từng bên"), width=120, height=26, corner_radius=8,
            fg_color="transparent", border_width=1, border_color=ACCENT, text_color=ACCENT,
            hover_color=CARD_HOVER, font=ctk.CTkFont(size=12, weight="bold"),
            command=self._bind_fast_pair_to_active)

        # 3b. ÂM THANH PHÁT RA (khung giữ chỗ luôn được pack, hàng bên trong ẩn/hiện theo cài đặt)
        self.audio_holder = ctk.CTkFrame(self.container, fg_color="transparent", height=1)
        self.audio_row = ctk.CTkFrame(self.audio_holder, corner_radius=14, fg_color=CARD,
                                      border_color=BORDER, border_width=1)
        ctk.CTkLabel(self.audio_row, text="", image=self._glyph('speaker', 16), width=24).pack(
            side="left", padx=(12, 6), pady=10)
        audio_text = ctk.CTkFrame(self.audio_row, fg_color="transparent")
        audio_text.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(audio_text, text=tr("Âm thanh phát ra"), anchor="w", height=16,
                     font=ctk.CTkFont(size=12, weight="bold"), text_color=TEXT).pack(fill="x")
        self.lbl_audio_device = ctk.CTkLabel(audio_text, text="", anchor="w", height=16,
                                             font=ctk.CTkFont(size=11), text_color=MUTED)
        self.lbl_audio_device.pack(fill="x")
        # Công tắc hai lựa chọn: lựa chọn sáng = đang phát ra đó; bấm bên kia để chuyển.
        self.audio_switch = ctk.CTkSegmentedButton(
            self.audio_row, values=[tr("Loa"), tr("Tai nghe")], width=190, height=32, corner_radius=8,
            font=ctk.CTkFont(size=12, weight="bold"), fg_color=BG, unselected_color=BG,
            unselected_hover_color=CARD_HOVER, selected_color=ACCENT, selected_hover_color=ACCENT,
            text_color="#ffffff" if self._theme == 'dark' else TEXT, text_color_disabled=DIM,
            command=self._on_audio_switch)
        self.audio_switch.pack(side="right", padx=(6, 10), pady=10)
        self._audio_outputs = []

        # 4. DANH SÁCH TAI NGHE (chỉ hiện khi có nhiều hơn một chiếc)
        self.devices_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        self.lbl_devices_header = ctk.CTkLabel(
            self.devices_frame, text=tr("THIẾT BỊ ĐANG KẾT NỐI"), anchor="w",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=DIM)
        self.lbl_devices_header.pack(fill="x", padx=4, pady=(0, 4))
        self.devices_list = ctk.CTkFrame(self.devices_frame, fg_color="transparent")
        self.devices_list.pack(fill="x")
        self._device_buttons = []
        self._device_button_ids = ()

        self.audio_holder.pack(fill="x", padx=12)
        self.bottom_spacer = ctk.CTkFrame(self.container, fg_color="transparent", height=4)
        self.bottom_spacer.pack(fill="x")

    def _render_device_list(self):
        if len(self.connected_headphones) < 2 or self.settings['compact_mode']:
            for button in self._device_buttons:
                button.destroy()
            self._device_buttons = []
            self._device_button_ids = ()
            self.devices_frame.pack_forget()
            return
        ids = tuple(device['id'] for device in self.connected_headphones)
        if ids != self._device_button_ids:
            # Rebuild only when membership or order changes; otherwise update in place.
            for button in self._device_buttons:
                button.destroy()
            self._device_buttons = []
            for device in self.connected_headphones:
                button = ctk.CTkButton(
                    self.devices_list, text="", anchor="w", height=38, corner_radius=12, compound="left",
                    hover_color=CARD_HOVER, border_width=1, font=ctk.CTkFont(size=12),
                    command=lambda key=device['id']: self._select_headphone(key))
                button.pack(fill="x", pady=2)
                self._device_buttons.append(button)
            self._device_button_ids = ids
        active_id = self.active_headphone['id'] if self.active_headphone else None
        for device, button in zip(self.connected_headphones, self._device_buttons):
            level = self.gatt_levels.get(device['id'], device.get('battery_level'))
            suffix = f"   {level}%" if type(level) is int else ""
            is_active = device['id'] == active_id
            button.configure(
                text=f"  {self._device_name(device)}{suffix}",
                image=self._glyph(self._device_glyph(device), 16, ACCENT if is_active else MUTED),
                fg_color=CARD_HOVER if is_active else CARD,
                border_color=ACCENT if is_active else BORDER,
                text_color=TEXT if is_active else MUTED)
        if not self.devices_frame.winfo_manager():
            self.devices_frame.pack(fill="x", padx=12, pady=(0, 6), before=self.audio_holder)

    def _start_drag(self, event):
        self._drag_start_x = event.x
        self._drag_start_y = event.y

    def _do_drag(self, event):
        x = self.winfo_x() + event.x - self._drag_start_x
        y = self.winfo_y() + event.y - self._drag_start_y
        self._user_position = (x, y)
        self.geometry(f"+{x}+{y}")

    def _toggle_pin(self):
        self.is_pinned = not self.is_pinned
        self.attributes("-topmost", self.is_pinned)
        self.btn_pin.configure(image=self._glyph('pinned' if self.is_pinned else 'pin', 15,
                                                 ACCENT if self.is_pinned else DIM))

    # --- CÀI ĐẶT ---
    def _set_setting(self, key, value):
        self.settings[key] = value
        if key == 'hotkey_enabled':
            (self.hotkey_worker.start if value else self.hotkey_worker.stop)()
        if key == 'show_other_devices':
            self.connection_monitor.set_include_other(value)
        if key == 'theme':
            self._apply_theme()
        if key == 'language':
            self._apply_language()
        if key == 'compact_mode':
            self._apply_compact_mode()
        if key == 'aliases':
            self._activate_headphone(self.active_headphone)
            self._render_device_list()
        if key == 'mini_widget':
            self._sync_mini_widget()
        self._update_tray()  # dấu tích trong menu khay
        if key == 'show_audio_control':
            self._render_audio_row()
            self._resize_to_content()
        save_settings(self.settings)
        self._tray_icon_key = None
        self._update_tray()
        self._check_low_battery()
        self._check_full_charge()

    def _apply_theme(self, rebuild=True):
        theme = self.settings['theme']
        if theme == 'system':
            theme = read_system_theme()
        self._theme = theme
        globals().update(THEMES[theme])
        ui_theme.use(THEMES[theme])
        ctk.set_appearance_mode(theme)
        self.configure(fg_color=BG)
        self._style_title_bar(self)
        if not rebuild:
            return
        settings_open = self._settings_win and self._settings_win.winfo_exists()
        if settings_open:
            self._settings_win.destroy()
            self._settings_win = None
        if self._toast and self._toast.winfo_exists():
            self._toast.destroy()
        self._toast = None
        self.container.destroy()
        self._build_ui()
        self._apply_compact_mode()
        self.lbl_device_title.configure(text=self._device_name(self.active_headphone) if self.active_headphone
                                         else tr('Chưa có tai nghe kết nối'))
        self._render_device_icon()
        self._render_connection_status()
        self.btn_pin.configure(image=self._glyph('pinned' if self.is_pinned else 'pin', 15,
                                                 ACCENT if self.is_pinned else DIM))
        if settings_open:
            self._show_settings_dialog()

    def _apply_language(self):
        """Đổi ngôn ngữ ngay: dựng lại giao diện (như đổi theme), menu khay và bảng chọn tai nghe đang mở."""
        i18n.set_language(i18n.resolve(self.settings['language']))
        picker_open = self._split_picker is not None
        self._apply_theme()
        if picker_open:
            self._bind_fast_pair_to_active()
        if self.tray_icon:
            self.tray_icon.menu = self._tray_menu()  # pystray tự update_menu() khi gán menu mới

    def _apply_compact_mode(self):
        if self.settings['compact_mode']:
            self.card_footer.pack_forget()
            self.lbl_status_dot.pack_forget()
        else:
            self.card_footer.pack(fill='x', padx=14, pady=(4, 12))
            self.lbl_status_dot.pack(fill='x')
        self._update_ui_values()
        self._render_device_list()
        self._render_audio_row()
        self._resize_to_content()

    def _sync_gatt_targets(self):
        # Không thử nối BLE tới tai nghe đã có pin trái/phải/hộp (JBL Fast Pair, AirPods): chúng không có dịch vụ
        # pin BLE, mà mỗi lần thử nối (tới 15 giây, lặp lại mãi) chiếm sóng Bluetooth làm nhạc bị giật.
        if getattr(self, 'gatt_monitor', None) is None:
            return  # gọi lúc khởi tạo, trước khi có bộ đọc BLE
        owners = self._component_owners()
        self.gatt_monitor.set_targets({device['id']: address_from_device_id(device['id'])
                                       for device in self.connected_headphones if device['id'] not in owners})

    def _update_core_enabled(self):
        self._sync_gatt_targets()
        # Scan Fast Pair advertisements only while the bound JBL is connected.
        enabled = bool(self.battery_device_id and any(
            device['id'] == self.battery_device_id for device in self.connected_headphones))
        setter = getattr(self.core, 'set_enabled', None)
        if setter:
            setter(enabled)

    def _save_settings_now(self):
        save_settings(self.settings)

    # Cho ui_feedback.py (mixin không import main được).
    @staticmethod
    def _app_title():
        return APP_NAME

    @staticmethod
    def _app_version():
        return APP_VERSION

    @staticmethod
    def _feedback_log():
        return read_log_tail(FEEDBACK_LOG_LINES)  # đã che địa chỉ Bluetooth và tên tài khoản

    def _confirm_bind(self, device_id):
        self._binding_generation += 1
        # Chỉ bỏ số của chiếc JBL cũ/mới; AirPods và tai nghe Fast Pair khác giữ nguyên pin từng bên.
        self._components.pop(self.battery_device_id, None)
        self._components.pop(device_id, None)
        self.battery_device_id = device_id
        self._load_cached_state()
        self.settings['jbl_battery_device_id'] = self.battery_device_id
        save_settings(self.settings)
        self._update_core_enabled()
        self.core.trigger_refresh()
        self._refresh_settings_binding()
        self._render_connection_status()
        self._update_tray()

    def _unbind_fast_pair(self):
        # Tai nghe đang xem liên kết qua danh sách Fast Pair chung: chỉ gỡ liên kết của nó, JBL giữ nguyên.
        if self.active_headphone and self._unbind_split(self.active_headphone['id']):
            self._load_cached_state()
            self.battery_is_fresh = False
            self._refresh_settings_binding()
            self._render_connection_status()
            self._update_tray()
            return
        self._binding_generation += 1
        self._components.pop(self.battery_device_id, None)
        self.battery_device_id = None
        self.settings.pop('jbl_battery_device_id', None)
        save_settings(self.settings)
        self._load_cached_state()
        self.battery_is_fresh = False
        self._update_core_enabled()
        self._refresh_settings_binding()
        self._render_connection_status()
        self._update_tray()

    def _refresh_settings_binding(self):
        win = self._settings_win
        if not win or not win.winfo_exists():
            return
        bound = len(self._fast_pair_bindings()) + (1 if self.battery_device_id else 0)
        active_bound = bool(self.active_headphone) and self.active_headphone['id'] in (
            {self.battery_device_id} | set(self._fast_pair_bindings()))
        if active_bound:
            text = tr("Đã liên kết: {name}", name=self._device_name(self.active_headphone))
        elif bound:
            text = tr("Đã liên kết {count} tai nghe khác (hiện không được chọn).", count=bound)
            if self.battery_device_id:
                text += tr(" Bấm “Bỏ” để gỡ liên kết chiếc JBL.")
        else:
            text = tr("Chưa liên kết tai nghe nào.")
        win.lbl_binding.configure(text=text)
        can_bind = bool(self.active_headphone) and not self._supports_active_battery()
        win.btn_bind.configure(state="normal" if can_bind else "disabled",
                               fg_color=ACCENT if can_bind else CARD_HOVER)
        # "Bỏ" gỡ liên kết của tai nghe đang xem; không thì gỡ liên kết JBL như trước.
        win.btn_unbind.configure(state="normal" if active_bound or self.battery_device_id else "disabled")

    def _section(self, parent, title):
        ctk.CTkLabel(parent, text=title, anchor="w", text_color=DIM,
                     font=ctk.CTkFont(size=10, weight="bold")).pack(fill="x", padx=18, pady=(12, 4))
        frame = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=12,
                             border_color=BORDER, border_width=1)
        frame.pack(fill="x", padx=14)
        return frame

    def _center_window(self, window, width, height=None):
        # CTk nhân kích thước và vị trí với hệ số DPI; màn hình đo bằng pixel thật.
        scale = ScalingTracker.get_window_scaling(window)
        if height is None:
            window.update_idletasks()
            height = window.winfo_reqheight() / scale
        x = max(int((window.winfo_screenwidth() / scale - width) / 2), 0)
        y = max(int((window.winfo_screenheight() / scale - height) / 2) - 20, 0)
        window.geometry(f"{width}x{int(height)}+{x}+{y}")

    def _copy_log(self):
        self.clipboard_clear()
        self.clipboard_append(read_log_tail())
        self._show_toast(tr("Đã chép log"), tr("Dán (Ctrl+V) vào tin nhắn để gửi"), glyph='settings', color=GOOD,
                         duration=3000)

    def _close_settings(self):
        # Mỗi thay đổi đã được lưu ngay khi bấm; ghi lại lần nữa cho chắc rồi trở về bảng pin.
        save_settings(self.settings)
        if self._settings_win and self._settings_win.winfo_exists():
            self._settings_win.destroy()
        self._settings_win = None
        self.show_window()

    def _show_settings_dialog(self):
        if self._settings_win and self._settings_win.winfo_exists():
            self._settings_win.deiconify()
            self._settings_win.lift()
            self._settings_win.focus_force()
            return
        # Chỉ một bảng trên màn hình: ẩn bảng pin khi đang cài đặt, hiện lại khi đóng.
        self._dispose_nearby_panel()
        self.withdraw()
        win = ctk.CTkToplevel(self)
        self._settings_win = win
        win.title(tr("Cài đặt"))
        win.configure(fg_color=BG)
        win.resizable(False, False)
        win.attributes("-topmost", True)
        self._center_window(win, 420, 660)
        win.protocol("WM_DELETE_WINDOW", self._close_settings)
        self._style_dialog(win)

        ctk.CTkLabel(win, text=tr("Cài đặt"), font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=TEXT, anchor="w").pack(fill="x", padx=18, pady=(14, 0))
        win.settings_scroll = ctk.CTkScrollableFrame(win, fg_color=BG)
        win.settings_scroll.pack(fill='both', expand=True, pady=6)
        win.setting_controls = {}
        content = win.settings_scroll

        def setting_switch(parent, key, text):
            switch = ctk.CTkSwitch(parent, text=tr(text), progress_color=ACCENT,
                                   text_color=TEXT, font=ctk.CTkFont(size=12),
                                   command=lambda: self._set_setting(key, switch.get() == 1))
            if self.settings[key]:
                switch.select()
            switch.pack(fill='x', padx=14, pady=6)
            win.setting_controls[key] = switch
            return switch

        # Hệ thống
        system = self._section(content, tr("HỆ THỐNG"))
        startup = ctk.CTkSwitch(system, text=tr("Khởi động cùng Windows"), progress_color=ACCENT,
                                font=ctk.CTkFont(size=12))
        if is_startup_enabled():
            startup.select()

        def toggle_startup():
            wanted = startup.get() == 1
            if not set_startup(wanted):
                # select/deselect do not re-run the command, so a failed write cannot loop.
                (startup.deselect if wanted else startup.select)()
        startup.configure(command=toggle_startup)
        startup.pack(fill="x", padx=14, pady=(12, 6))

        tray = ctk.CTkSwitch(system, text=tr("Hiện % pin trên biểu tượng khay"), progress_color=ACCENT,
                             font=ctk.CTkFont(size=12),
                             command=lambda: self._set_setting("tray_show_percent", tray.get() == 1))
        if self.settings["tray_show_percent"]:
            tray.select()
        tray.pack(fill="x", padx=14, pady=(6, 12))
        ctk.CTkLabel(system, text=tr('Giao diện'), text_color=MUTED, anchor='w').pack(fill='x', padx=14)
        theme_names = {'system': tr('Hệ thống'), 'dark': tr('Tối'), 'light': tr('Sáng')}
        theme_keys = {value: key for key, value in theme_names.items()}
        win.theme_choice = ctk.CTkOptionMenu(
            system, values=list(theme_keys), fg_color=CARD_HOVER, button_color=BORDER,
            text_color=TEXT, command=lambda value: self._set_setting('theme', theme_keys[value]))
        win.theme_choice.set(theme_names[self.settings['theme']])
        win.theme_choice.pack(fill='x', padx=14, pady=(4, 6))
        # Ngôn ngữ: đổi là dựng lại giao diện ngay (cùng đường với đổi theme).
        ctk.CTkLabel(system, text=tr('Ngôn ngữ'), text_color=MUTED, anchor='w').pack(fill='x', padx=14)
        language_names = {'system': tr('Theo Windows'), 'vi': tr('Tiếng Việt'), 'en': 'English'}
        language_keys = {value: key for key, value in language_names.items()}
        win.language_choice = ctk.CTkOptionMenu(
            system, values=list(language_keys), fg_color=CARD_HOVER, button_color=BORDER,
            text_color=TEXT, command=lambda value: self._set_setting('language', language_keys[value]))
        win.language_choice.set(language_names[self.settings['language']])
        win.language_choice.pack(fill='x', padx=14, pady=(4, 6))
        setting_switch(system, 'compact_mode', 'Chế độ gọn')
        setting_switch(system, 'show_audio_control', 'Nút đổi loa / tai nghe trên bảng pin')
        setting_switch(system, 'mini_widget', 'Cửa sổ nổi nhỏ').pack_configure(pady=(6, 12))

        # Cảnh báo
        alerts = self._section(content, tr("THÔNG BÁO"))
        alert = ctk.CTkSwitch(alerts, text=tr("Thông báo khi pin xuống thấp"), progress_color=ACCENT,
                              font=ctk.CTkFont(size=12),
                              command=lambda: self._set_setting("low_battery_alert", alert.get() == 1))
        if self.settings["low_battery_alert"]:
            alert.select()
        alert.pack(fill="x", padx=14, pady=(12, 6))
        ctk.CTkLabel(alerts, text=tr("Ngưỡng cảnh báo"), font=ctk.CTkFont(size=12),
                     text_color=MUTED, anchor="w").pack(fill="x", padx=14)
        threshold = self._segmented(
            alerts, [f"{value}%" for value in THRESHOLD_CHOICES],
            command=lambda value: self._set_setting("low_battery_threshold", int(value.rstrip("%"))))
        threshold.set(f"{self.settings['low_battery_threshold']}%")
        threshold.pack(fill="x", padx=14, pady=(4, 12))
        setting_switch(alerts, 'full_charge_alert', 'Thông báo sạc đầy')
        ctk.CTkLabel(alerts, text=tr('Ngưỡng sạc đầy'), text_color=MUTED, anchor='w',
                     font=ctk.CTkFont(size=12)).pack(fill='x', padx=14)
        win.full_charge_threshold = self._segmented(
            alerts, ['80%', '90%', '100%'],
            command=lambda value: self._set_setting('full_charge_level', int(value.rstrip('%'))))
        win.full_charge_threshold.set(f"{self.settings['full_charge_level']}%")
        win.full_charge_threshold.pack(fill='x', padx=14, pady=(4, 8))
        setting_switch(alerts, 'connection_alert', 'Thông báo kết nối / ngắt kết nối')
        setting_switch(alerts, 'popup_on_connect', 'Hiện bảng nhỏ khi vừa kết nối tai nghe')
        setting_switch(alerts, 'alert_overlay', 'Cảnh báo nổi trên màn hình (cả khi chơi game)')
        setting_switch(alerts, 'alert_sound', 'Phát âm báo khi pin yếu')
        ctk.CTkLabel(alerts, text=tr('Báo trước khi hết pin (theo ước tính)'), text_color=MUTED, anchor='w',
                     font=ctk.CTkFont(size=12)).pack(fill='x', padx=14)
        time_names = {0: 'Tắt', 15: '15 phút', 30: '30 phút'}
        time_keys = {value: key for key, value in time_names.items()}
        win.time_alert = self._segmented(
            alerts, list(time_keys),
            command=lambda value: self._set_setting('time_alert_minutes', time_keys[value]))
        win.time_alert.set(tr(time_names[self.settings['time_alert_minutes']]))
        win.time_alert.pack(fill='x', padx=14, pady=(4, 12))

        # Nâng cao: ít dùng nên thu gọn mặc định (tự mở khi phím tắt đang lỗi).
        win.feedback_box = self._build_feedback_section(content)  # luôn thấy, không giấu trong "Nâng cao"

        advanced = ctk.CTkFrame(content, fg_color="transparent")
        win.advanced_frame = advanced

        def toggle_advanced():
            if advanced.winfo_manager():
                advanced.pack_forget()
            else:
                advanced.pack(fill="x")
            win.btn_advanced.configure(text=("▾  " if advanced.winfo_manager() else "▸  ") + tr("Nâng cao"))
        win.btn_advanced = ctk.CTkButton(
            content, text="▸  " + tr("Nâng cao"), anchor="w", height=32, corner_radius=10,
            fg_color="transparent", hover_color=CARD_HOVER, text_color=MUTED,
            font=ctk.CTkFont(size=12, weight="bold"), command=toggle_advanced)
        win.btn_advanced.pack(fill="x", padx=14, pady=(14, 0))
        win.toggle_advanced = toggle_advanced

        other = self._section(advanced, tr("PHÍM TẮT & THIẾT BỊ"))
        setting_switch(other, 'hotkey_enabled', 'Phím tắt Ctrl+Alt+B / Ctrl+Alt+H').pack_configure(pady=(12, 6))
        win.lbl_hotkey_status = ctk.CTkLabel(
            other, text=tr(self.hotkey_error or HOTKEY_HINT),
            text_color=DIM, font=ctk.CTkFont(size=11), wraplength=290, anchor='w', justify='left')
        win.lbl_hotkey_status.pack(fill='x', padx=14, pady=(0, 6))
        setting_switch(other, 'show_other_devices', 'Hiện cả chuột, bàn phím, tay cầm, điện thoại').pack_configure(pady=(6, 12))

        log_box = self._section(advanced, tr("BÁO LỖI"))
        ctk.CTkLabel(log_box, text=tr("Gặp lỗi? Bấm “Chép log” rồi dán (Ctrl+V) gửi cho người hỗ trợ."),
                     anchor="w", justify="left", wraplength=300, text_color=DIM,
                     font=ctk.CTkFont(size=11)).pack(fill="x", padx=14, pady=(10, 4))
        win.btn_copy_log = ctk.CTkButton(log_box, text=tr("Chép log"), width=100, height=30, fg_color=CARD_HOVER,
                                         hover_color=BORDER, text_color=TEXT, command=self._copy_log)
        win.btn_copy_log.pack(anchor="w", padx=14, pady=(0, 12))

        # Liên kết Fast Pair
        fast_pair = self._section(advanced, tr("PIN TỪNG BÊN (FAST PAIR)"))
        ctk.CTkLabel(fast_pair, text=tr("Tai nghe true wireless có Fast Pair: chọn đúng tai nghe trong danh sách\n"
                                        "đang phát pin gần máy. Không tự nhận theo tên tai nghe."),
                     font=ctk.CTkFont(size=11), text_color=DIM, justify="left",
                     anchor="w").pack(fill="x", padx=14, pady=(10, 2))
        win.lbl_binding = ctk.CTkLabel(fast_pair, text="", font=ctk.CTkFont(size=12),
                                       text_color=TEXT, anchor="w", wraplength=300, justify="left")
        win.lbl_binding.pack(fill="x", padx=14, pady=(2, 6))
        buttons = ctk.CTkFrame(fast_pair, fg_color="transparent")
        buttons.pack(fill="x", padx=14, pady=(0, 12))
        win.btn_bind = ctk.CTkButton(buttons, text=tr("Chọn tai nghe để xem pin từng bên"), height=30,
                                     fg_color=ACCENT, hover_color="#e55a08",
                                     text_color_disabled=DIM,
                                     command=self._bind_fast_pair_to_active)
        win.btn_bind.pack(side="left", fill="x", expand=True, padx=(0, 6))
        win.btn_unbind = ctk.CTkButton(buttons, text=tr("Bỏ"), width=60, height=30,
                                       fg_color=CARD_HOVER, hover_color=BORDER, text_color=TEXT,
                                       command=self._unbind_fast_pair)
        win.btn_unbind.pack(side="right")
        self._refresh_settings_binding()
        if self.hotkey_error:
            toggle_advanced()

        # Chân trang
        footer = ctk.CTkFrame(win, fg_color="transparent")
        footer.pack(fill="x", padx=14, pady=(14, 12))
        ctk.CTkLabel(footer, text=f"{APP_NAME} {APP_VERSION}",
                     font=ctk.CTkFont(size=11), text_color=DIM).pack(side="left", padx=4)
        win.btn_save_close = ctk.CTkButton(
            footer, text=tr("Lưu & đóng"), width=110, height=32, fg_color=ACCENT, text_color="#ffffff",
            hover_color=blend(ACCENT, "#000000", .85), font=ctk.CTkFont(size=12, weight="bold"),
            command=self._close_settings)
        win.btn_save_close.pack(side="right")
        ctk.CTkButton(footer, text=tr("Thư mục log"), width=90, height=30, fg_color="transparent",
                      border_width=1, border_color=BORDER, hover_color=CARD_HOVER, text_color=TEXT,
                      command=lambda: os.startfile(DATA_DIR)).pack(side="right", padx=6)
        win.update_idletasks()

    # --- CẬP NHẬT DỮ LIỆU ---
    def _get_color_for_level(self, level, is_charging=False):
        return level_color(level, is_charging)

    def _on_headphones_update(self, devices, error=None):
        self._enqueue_ui(self._apply_headphones_update, devices, error)

    def _apply_headphones_update(self, devices, error=None):
        # Bluetooth của máy tắt là ngắt kết nối thật (vẫn báo "Đã ngắt kết nối"), không phải lỗi đọc.
        if error is None or error == RADIO_OFF:
            snapshot = {device['id']: dict(device) for device in devices}
            if self._connection_snapshot is None:
                self._connection_snapshot = snapshot  # lúc khởi động: không báo
            else:
                # Chờ kết nối ổn định rồi báo một lần (xem _announce_connection_changes).
                self._pending_snapshot = snapshot
                if self._announce_timer:
                    self.after_cancel(self._announce_timer)
                self._announce_timer = self.after(CONNECTION_SETTLE_MS, self._announce_connection_changes)
        previous_ids = {device['id'] for device in self.connected_headphones}
        old_id = self.active_headphone['id'] if self.active_headphone else None
        # Lỗi quét làm danh sách tạm rỗng: nhớ danh sách và thiết bị đang chọn trước lỗi, để khi đọc lại được
        # thì không coi mọi thiết bị là "vừa kết nối" và nhảy sang tai nghe khác.
        if error and not devices:
            if self.connected_headphones:
                self._before_error = (previous_ids, old_id)
        elif self._before_error:
            previous_ids, old_id = self._before_error
            self._before_error = None
        self.connected_headphones = devices
        self.connection_error = error
        newly_connected = [device for device in devices if device['id'] not in previous_ids]
        current = next((device for device in devices if device['id'] == old_id), None)
        active = newly_connected[-1] if newly_connected else current
        if active is None and devices:
            active = devices[0]
        ids = {device['id'] for device in devices}
        self.gatt_levels = {key: value for key, value in self.gatt_levels.items() if key in ids}
        self._components = {key: value for key, value in self._components.items() if key in ids}
        self._previous_levels = {key: value for key, value in self._previous_levels.items() if key[0] in ids}
        self._update_core_enabled()
        self._sync_apple_scanner()
        self._sync_fast_pair_scanner()
        self._start_split_discovery(newly_connected)
        self._record_history()
        self._activate_headphone(active, reset=bool(error))
        self._render_device_list()
        self._resize_to_content()

    def _announce_connection_changes(self):
        """MỘT thông báo cho cả loạt thay đổi, khi kết nối đã ổn định.

        Nối/ngắt tai nghe (nhất là khi đang có hai chiếc) làm Windows báo chập chờn vài giây: ngắt rồi nối lại, từng
        profile âm thanh một. Báo từng lần thì thông báo nhảy liên tục; chập chờn rồi trở lại như cũ thì không báo."""
        self._announce_timer = None
        previous, snapshot, self._pending_snapshot = self._connection_snapshot, self._pending_snapshot, None
        if previous is None or snapshot is None:
            return
        self._connection_snapshot = snapshot
        current = {device['id']: device for device in self.connected_headphones}
        added = sorted((current.get(key, snapshot[key]) for key in snapshot.keys() - previous.keys()),
                       key=lambda device: self._device_name(device).casefold())
        removed = sorted((previous[key] for key in previous.keys() - snapshot.keys()),
                         key=lambda device: self._device_name(device).casefold())
        if not added and not removed:
            return
        if added and self.settings['popup_on_connect'] and not self._window_visible():
            self._show_device_toast(added[-1])  # bảng nổi đã đủ, không thêm thông báo Windows
            return
        if not self.settings['connection_alert']:
            return
        parts = []
        for device in added:
            level = self._device_sample(device)[0]
            parts.append(tr("Đã kết nối {name}", name=self._device_name(device))
                         + (f' · {level}%' if level is not None else ''))
        if removed:
            parts.append(tr("đã ngắt {names}", names=", ".join(self._device_name(device) for device in removed)))
        message = "; ".join(parts)
        self._notify(message[:1].upper() + message[1:], tr('Kết nối Bluetooth'))

    def _select_headphone(self, device_id):
        """Người dùng chọn tai nghe để hiển thị trong danh sách."""
        device = next((d for d in self.connected_headphones if d['id'] == device_id), None)
        if device is None or device is self.active_headphone:
            return
        self._activate_headphone(device)
        self._render_device_list()
        self._resize_to_content()

    def _activate_headphone(self, device, reset=False):
        if self._alias_entry and (not device or device['id'] != self._alias_entry[1]):
            self._finish_alias(False)
        old = self.active_headphone
        old_key = (old['id'], old['name']) if old else (None, None)
        self.active_headphone = device
        new_key = (device['id'], device['name']) if device else (None, None)
        level = device.get('battery_level') if device else None
        self.windows_battery_level = level if type(level) is int and 0 <= level <= 100 else None
        if new_key[0] != old_key[0] or reset:
            self.battery_is_fresh = False
            self._load_cached_state()
            components = self._components.get(new_key[0])
            if self._supports_active_battery() and components and components[3].get('_fresh') is not False:
                self.left_level, self.right_level, self.case_level, self.charging_info = components
                self.battery_is_fresh = any(value is not None for value in components[:3])
        self.is_connected = device is not None
        if device:
            if self._last_device is None or self._last_device['id'] != device['id']:
                self._last_level = None
            self._last_device = device
        shown = device or self._offline_device()
        name = self._device_name(shown) if shown else tr('Chưa có tai nghe kết nối')
        self.lbl_device_title.configure(text=name, text_color=TEXT if device else DIM)
        self._render_device_icon()
        self.title(f"{APP_NAME} — {name}" if device else APP_NAME)
        self.btn_alias.configure(state='normal' if device else 'disabled')
        self._update_ui_values()
        self._render_connection_status()
        self._update_tray()
        self._refresh_settings_binding()

    def _offline_device(self):
        """Tai nghe vừa ngắt kết nối, hiện màu xám. Khi lỗi đọc kết nối thì không biết nên không hiện."""
        if self.active_headphone or (self.connection_error and self.connection_error != RADIO_OFF):
            return None
        return self._last_device

    def _device_icon(self, device):
        return {'headphone': '🎧', 'mouse': '🖱', 'keyboard': '⌨', 'phone': '📱',
                'gamepad': '🎮', 'other': '🔵'}.get((device or {}).get('kind', 'headphone'), '🔵')

    def _device_glyph(self, device):
        kind = (device or {}).get('kind', 'headphone')
        return kind if kind in GLYPHS else 'other'

    def _render_device_icon(self):
        connected = self.is_connected
        self.lbl_headphone_icon.configure(
            image=self._glyph(self._device_glyph(self.active_headphone or self._offline_device()), 20,
                              ACCENT if connected else DIM),
            fg_color=ACCENT_SOFT if connected else BG)

    def _device_name(self, device):
        name = self.settings['aliases'].get(device['id'], device['name'])
        # Tên thay thế khi thiết bị không có tên là chữ của app, không phải tên thiết bị: dịch khi hiện.
        return tr(name) if name == headphone_monitor.UNNAMED else name

    def _edit_alias(self):
        """Sửa tên ngay tại chỗ: Enter/bấm ra ngoài để lưu, Esc để hủy, để trống = tên Windows."""
        device = self.active_headphone
        if not device or self._alias_entry:
            return
        entry = ctk.CTkEntry(self.title_text, height=30, corner_radius=8, border_width=1,
                             border_color=ACCENT, fg_color=BG, text_color=TEXT,
                             placeholder_text=device['name'], font=ctk.CTkFont(size=15, weight="bold"))
        entry.insert(0, self._device_name(device))
        entry.pack(fill="x", before=self.lbl_device_title)
        self.lbl_device_title.pack_forget()
        self._alias_entry = (entry, device['id'], device['name'])
        entry.bind("<Return>", lambda event: self._finish_alias(True))
        entry.bind("<Escape>", lambda event: self._finish_alias(False))
        entry.bind("<FocusOut>", lambda event: self._finish_alias(True))
        entry.focus_set()
        entry.select_range(0, "end")

    def _finish_alias(self, save):
        if not self._alias_entry:
            return
        entry, device_id, windows_name = self._alias_entry
        self._alias_entry = None  # FocusOut fires again while the entry is destroyed
        value = entry.get().strip()
        self.lbl_device_title.pack(fill="x", before=entry)
        entry.destroy()
        if not save:
            return
        aliases = dict(self.settings['aliases'])
        if value and value not in (windows_name, tr(windows_name)):
            aliases[device_id] = value
        else:
            aliases.pop(device_id, None)
        if aliases != self.settings['aliases']:
            self._set_setting('aliases', aliases)

    def _component_owners(self):
        """Thiết bị có pin trái/phải/hộp: JBL đã liên kết (Fast Pair) và AirPods/Beats đang kết nối."""
        return {key for key in (self.battery_device_id, self._apple_device_id) if key} | set(self._fast_pair_bindings())

    def _supports_active_battery(self):
        # Pin chi tiết chỉ thuộc đúng thiết bị nguồn của nó, không gán cho tai nghe khác.
        return bool(self.active_headphone and self.active_headphone['id'] in self._component_owners())

    # --- AIRPODS / BEATS ---
    @staticmethod
    def _is_apple_headset(device):
        # Gói quảng bá của Apple không mang định danh Windows của thiết bị, nên chỉ có thể nhận theo tên;
        # bù lại, gói chỉ được dùng khi rất gần máy (apple_pods.MIN_RSSI).
        name = device['name'].casefold()
        return 'airpods' in name or 'beats' in name

    def _sync_apple_scanner(self):
        device = next((d for d in self.connected_headphones if self._is_apple_headset(d)), None)
        new_id = device['id'] if device else None
        if new_id != self._apple_device_id:
            self._components.pop(self._apple_device_id, None)
            self._apple_device_id = new_id
            self._sync_gatt_targets()
        try:
            if new_id and not self.apple_scanner.running:
                self.apple_scanner.start()
            elif not new_id and self.apple_scanner.running:
                self.apple_scanner.stop()
        except Exception:
            logging.warning('Cannot scan AirPods battery', exc_info=True)

    def _on_apple_update(self, parsed):
        self._enqueue_ui(self._apply_apple_update, parsed)

    def _apply_apple_update(self, parsed):
        device_id = self._apple_device_id
        if not device_id or not any(d['id'] == device_id for d in self.connected_headphones):
            return
        if parsed is None:
            # Không còn gói nào gần: bỏ số pin chi tiết, quay về pin chung của Windows.
            self._apply_components(device_id, None, None, None, {'_fresh': False, '_source': 'airpods'})
            return
        charging = dict(parsed['charging'], _fresh=True, _source='airpods',
                        _updated_at=time.strftime('%Y-%m-%d %H:%M:%S'))
        self._apply_components(device_id, parsed['left'], parsed['right'], parsed['case'], charging)

    def _on_battery_update(self, left, right, case, charging_dict):
        # Tag the packet with the binding it was received under.
        self._enqueue_ui(self._apply_battery_update, left, right, case, charging_dict,
                         self._binding_generation)

    def _apply_battery_update(self, left, right, case, charging_dict, generation=None):
        if generation is not None and generation != self._binding_generation:
            return  # Queued before a rebind/unbind; it belongs to the old binding.
        if not self.battery_device_id or not any(
                d['id'] == self.battery_device_id for d in self.connected_headphones):
            return
        self._apply_components(self.battery_device_id, left, right, case, charging_dict)

    def _apply_components(self, device_id, left, right, case, charging_dict):
        self._components[device_id] = (left, right, case, dict(charging_dict or {}))
        self._record_history()
        if not (self.active_headphone and self.active_headphone['id'] == device_id):
            return
        self.left_level = left
        self.right_level = right
        self.case_level = case
        self.charging_info = charging_dict or {}
        self.battery_is_fresh = (self.charging_info.get('_fresh') is not False and
                                 any(level is not None for level in (left, right, case)))
        updated_at = self.charging_info.get("_updated_at")
        if updated_at:
            self.last_update_str = updated_at.split()[-1]
        elif any(level is not None for level in (left, right, case)):
            self.last_update_str = time.strftime("%H:%M:%S")
        self._update_ui_values()
        self._update_tray()
        self._render_connection_status()

    def _on_status_update(self, connected, text):
        self._enqueue_ui(self._apply_status_update, connected, text)

    def _apply_status_update(self, connected, text):
        self.status_text = text
        if self.active_headphone and self.active_headphone['id'] in (
                {self._apple_device_id} | set(self._fast_pair_bindings())):
            self._render_connection_status()
            return  # trạng thái của bộ quét JBL không nói gì về pin AirPods / tai nghe Fast Pair khác
        self.battery_is_fresh = connected and self._supports_active_battery()
        self._render_connection_status()

    def _render_connection_status(self):
        if self.connection_error == RADIO_OFF:
            text, color = "○ Không kết nối · Bluetooth của máy đang tắt", WARN
        elif self.connection_error:
            text, color = "○ Chưa đọc được kết nối", WARN
        elif self.is_connected and self.battery_is_fresh:
            source = 'AirPods' if self.charging_info.get('_source') == 'airpods' else 'Fast Pair'
            text, color = tr("● Trực tiếp · {source}", source=source), GOOD
        elif self.is_connected and self._general_level()[0] is not None:
            text, color = "● Đã kết nối", GOOD
        elif self.is_connected:
            text, color = "● Đã kết nối · chưa có pin", GOOD
        elif self.status_text == "Bluetooth PC đang tắt":
            text, color = "○ Bluetooth PC tắt", WARN
        elif self._offline_device():
            text, color = "○ Không kết nối", DIM
        else:
            text, color = "○ Chưa có tai nghe kết nối", WARN
        self.lbl_status_dot.configure(text=tr(text), text_color=color)
        self._render_device_icon()
        self._render_connect_button()
        self._render_split_button()

    def _render_split_button(self):
        """Tai nghe đang xem chưa có pin từng bên: lối tắt tới danh sách chọn tai nghe (ui_split.py)."""
        device = self.active_headphone
        # Tai nghe đã liên kết qua danh sách chung mà không còn số (vd. hãng đổi địa chỉ quảng bá định kỳ):
        # hiện lại lối tắt để chọn lại.
        lost = (device is not None and device['id'] in self._fast_pair_bindings()
                and not self._showing_components())
        # Chỉ hiện khi thật sự thấy tai nghe true wireless chưa liên kết phát pin gần máy (tai nghe chụp tai: không).
        show = (device is not None and device.get('kind', 'headphone') == 'headphone'
                and ((device['id'] not in self._component_owners() and device['id'] in self._split_available)
                     or lost))
        if not show:
            self.btn_split.pack_forget()
        elif not self.btn_split.winfo_manager():
            self.btn_split.pack(side="right")

    def _offline_address(self):
        """Địa chỉ của tai nghe vừa ngắt nếu app có thể kết nối lại nó (chỉ thiết bị âm thanh Bluetooth thường)."""
        offline = self._offline_device()
        if not offline or offline.get('kind', 'headphone') != 'headphone':
            return None
        return address_from_device_id(offline['id'])

    def _render_connect_button(self):
        offline = self._offline_device()
        if not self._offline_address():
            self.btn_connect.pack_forget()
            return
        waiting = (offline['id'], True) in self._connecting
        self.btn_connect.configure(text=tr("Đang kết nối…" if waiting else "Kết nối"),
                                   state="disabled" if waiting else "normal")
        if not self.btn_connect.winfo_manager():
            self.btn_connect.pack(side="right")

    def _connect_offline_device(self):
        address = self._offline_address()
        if address:
            self._set_device_connection(self._offline_device(), address, True)
            self._render_connect_button()

    def _set_component(self, level, charging, bar, label):
        if level is not None:
            label.configure(text=f"⚡ {level}%" if charging else f"{level}%",
                            text_color=CHARGING if charging else TEXT)
            bar.set(level / 100.0)
            bar.configure(progress_color=level_color(level, charging))
        else:
            label.configure(text="--%", text_color=DIM)
            bar.set(0)
            bar.configure(progress_color=bar.cget("fg_color"))

    def _showing_components(self):
        return self.battery_is_fresh and any(
            value is not None for value in (self.left_level, self.right_level, self.case_level))

    def _update_ui_values(self):
        level, source = self._general_level()
        if self.settings['compact_mode']:
            level = self._display_level()[0]
        self.lbl_device_battery.configure(
            text=f"{level}%" if level is not None else "--%",
            text_color=TEXT if level is not None else DIM,
            image=self._ring_image(level or 0, level_color(level) if level is not None else TRACK))
        if level is not None and self.is_connected:
            self._last_level = (level, time.strftime("%H:%M"))
        if not self.is_connected:
            offline = self._offline_device()
            state = "Không kết nối" if offline else "Chưa có tai nghe"
            hint = (tr("Lần cuối: {level}% lúc {time}", level=self._last_level[0], time=self._last_level[1])
                    if offline and self._last_level else "Bật tai nghe hoặc kết nối Bluetooth")
        elif level is None:
            state, hint = "Chưa có số pin", "Windows chưa báo pin cho thiết bị này"
        else:
            state = "Pin tốt" if level > 50 else "Pin trung bình" if level > 20 else "Pin yếu"
            hint = ("Đọc trực tiếp, chính xác 1%" if source == "ble"
                    else "Windows báo theo bậc 10%")
        self.lbl_battery_state.configure(text=tr(state), text_color=TEXT if self.is_connected else DIM)
        self.lbl_battery_source.configure(text=tr(hint))
        self.bar_device.set(level / 100 if level is not None else 0)
        self.bar_device.configure(progress_color=level_color(level)
                                  if level is not None else self.bar_device.cget('fg_color'))
        show_components = self._showing_components() and not self.settings['compact_mode']
        rows = (self.row_left, self.row_right, self.row_case)
        layout_changed = bool(self.row_device.winfo_manager()) == show_components
        if show_components:
            self.row_device.pack_forget()
            for row in rows:
                row.pack(fill="x", pady=3)
        else:
            for row in rows:
                row.pack_forget()
            self.row_device.pack(fill="x", pady=2)

        self._set_component(self.left_level, self.charging_info.get("left", False),
                            self.bar_left, self.lbl_l_val)
        self._set_component(self.right_level, self.charging_info.get("right", False),
                            self.bar_right, self.lbl_r_val)
        # Only the case's own charging flag describes whether the case is charging.
        self._set_component(self.case_level, self.charging_info.get("case", False),
                            self.bar_case, self.lbl_c_val)

        prefix = "Dữ liệu cũ" if self.charging_info.get("_fresh") is False and self.last_update_str != "Chưa có dữ liệu" else "Cập nhật"
        # last_update_str là giờ hoặc "Chưa có dữ liệu" (giá trị so sánh): chỉ dịch lúc hiện.
        updated = tr(self.last_update_str)
        if not show_components and level is not None:
            self.lbl_updated.configure(text=tr("Pin từ tai nghe (BLE) · cập nhật trực tiếp"
                                               if source == "ble" else "Pin do Windows cung cấp"))
        elif show_components:
            self.lbl_updated.configure(text=tr("{prefix}: {time} · từ tai nghe", prefix=tr(prefix), time=updated))
        else:
            self.lbl_updated.configure(text=tr("{prefix}: {time}", prefix=tr(prefix), time=updated))
        if layout_changed and self.state() != "withdrawn":
            self._resize_to_content()
        self._check_low_battery()
        self._render_history()
        self._sync_mini_widget()
        self._refresh_device_toast()

    def _display_level(self):
        """Mức pin đại diện (thấp nhất của hai tai, hoặc pin Windows) và trạng thái sạc."""
        if self._showing_components():
            pairs = [(level, self.charging_info.get(side)) for level, side in
                     ((self.left_level, "left"), (self.right_level, "right")) if level is not None]
            if pairs:
                return min(level for level, _ in pairs), any(charging for _, charging in pairs)
            return None, False
        return self._general_level()[0], False

    def _general_level(self):
        """Pin chung: ưu tiên BLE Battery Service đang kết nối, sau đó pin Windows."""
        if self.active_headphone:
            level = self.gatt_levels.get(self.active_headphone['id'])
            if level is not None:
                return level, "ble"
        if self.windows_battery_level is not None:
            return self.windows_battery_level, "windows"
        return None, None

    def _on_gatt_update(self, device_id, level):
        self._enqueue_ui(self._apply_gatt_update, device_id, level)

    def _apply_gatt_update(self, device_id, level):
        if not any(device['id'] == device_id for device in self.connected_headphones):
            return
        if level is None:
            self.gatt_levels.pop(device_id, None)
        else:
            self.gatt_levels[device_id] = level
        self._record_history()
        if self.active_headphone and self.active_headphone['id'] == device_id:
            self._update_ui_values()
            self._render_connection_status()
            self._update_tray()
        if len(self.connected_headphones) > 1:
            self._render_device_list()

    def _device_sample(self, device):
        components = self._components.get(device['id'])
        if device['id'] in self._component_owners() and components and components[3].get('_fresh') is not False:
            pairs = [(value, components[3].get(side)) for value, side in
                     zip(components[:2], ('left', 'right')) if value is not None]
            if pairs:
                flags = [flag for _, flag in pairs]
                charging = True if any(flags) else (False if all(flag is False for flag in flags) else None)
                return min(value for value, _ in pairs), 'fast_pair', charging
        level = self.gatt_levels.get(device['id'])
        if level is not None:
            return level, 'ble', None
        level = device.get('battery_level')
        return (level, 'windows', None) if type(level) is int and 0 <= level <= 100 else (None, None, None)

    def _record_history(self):
        for device in self.connected_headphones:
            level, source, charging = self._device_sample(device)
            if level is not None:
                self.history.record(device['id'], device['name'], level, source, charging)
        self._check_full_charge()

    def _check_full_charge(self):
        threshold = self.settings['full_charge_level']
        for device in self.connected_headphones:
            key = device['id']
            components = self._components.get(key)
            if components and components[3].get('_fresh') is not False:
                self._previous_levels.pop((key, 'device'), None)
                readings = [(side, value, components[3].get(side) is True, 'fast_pair')
                            for side, value in zip(('left', 'right', 'case'), components[:3])]
            else:
                for side in ('left', 'right', 'case'):
                    self._previous_levels.pop((key, side), None)
                level, source, _ = self._device_sample(device)
                previous = self._previous_levels.get((key, 'device'))
                rising = (level is not None and previous is not None and
                          previous[1] == source and level > previous[0])
                readings = [('device', level, rising, source)]
            for side, level, charging, source in readings:
                alert_key = (key, side)
                if level is None:
                    self._previous_levels.pop(alert_key, None)
                    continue
                previous = self._previous_levels.get(alert_key)
                # Charging often stops in the same packet that reports the full level.
                was_charging = bool(previous and len(previous) > 2 and previous[2])
                self._previous_levels[alert_key] = (level, source, charging)
                if level < threshold - 5:
                    self._full_alerted.discard(alert_key)
                    self._full_pending.discard(alert_key)
                elif level >= threshold and (charging or was_charging):
                    self._full_pending.add(alert_key)
                if (alert_key in self._full_pending and alert_key not in self._full_alerted
                        and self.settings['full_charge_alert']):
                    part = {'left': 'Tai trái', 'right': 'Tai phải', 'case': 'Hộp sạc'}.get(side)
                    part = ' · ' + tr(part) if part else ''
                    if self._alert(tr("{name}{part} đã đạt {level}% pin.", name=self._device_name(device),
                                      part=part, level=level), tr('Pin đã sạc đầy'), urgent=False):
                        self._full_alerted.add(alert_key)

    def _history_tick(self):
        if self._closing:
            return
        if time.time() - self.history._last_prune >= 3600:
            self.history.prune()
        self._record_history()
        self._render_history()
        self._check_low_battery()  # thiết bị không đang xem chỉ được kiểm ở đây và khi danh sách đổi
        self._check_time_alert()
        self._history_timer = self.after(60000, self._history_tick)

    def _set_chart_days(self, days):
        self._chart_days = days
        self._render_history()
        self._resize_to_content()

    def _render_history(self):
        # Bảng ẩn trong khay phần lớn thời gian: không vẽ biểu đồ không ai thấy; show_window vẽ lại.
        if self.state() == 'withdrawn':
            return
        widgets =(self.lbl_remaining, self.chart_controls, self.history_canvas)
        before = tuple(bool(widget.winfo_manager()) for widget in widgets)
        self._render_history_content()
        if before != tuple(bool(widget.winfo_manager()) for widget in widgets):
            self._resize_to_content()

    def _render_history_content(self):
        self.lbl_remaining.pack_forget()
        self.chart_controls.pack_forget()
        self.history_canvas.pack_forget()
        if not self.active_headphone or self.settings.get('compact_mode'):
            return
        key = self.active_headphone['id']
        samples = self.history.samples(key)
        level, _, charging = self._device_sample(self.active_headphone)
        remaining = estimate_remaining(samples) if level is not None and not charging else None
        if remaining is not None:
            minutes = round(remaining)
            self.lbl_remaining.configure(text=tr('≈ còn {hours} giờ {minutes} phút',
                                                 hours=minutes // 60, minutes=minutes % 60))
            self.lbl_remaining.pack(fill='x', padx=14, before=self.card_footer)
        now = time.time()
        samples = [s for s in samples if s['t'] >= now - self._chart_days * 86400]
        if not samples:
            return
        self.chart_controls.pack(fill='x', padx=14, pady=(6, 0), before=self.card_footer)
        self.history_canvas.pack(fill='x', padx=14, pady=4, before=self.card_footer)
        self._draw_history()

    def _draw_history(self):
        self.history_canvas.delete('all')
        if not self.active_headphone:
            return
        now = time.time()
        samples = sorted(self.history.samples(self.active_headphone['id'],
                                              since=now - self._chart_days * 86400),
                         key=lambda sample: sample['t'])
        # BLE (1%) và Windows (bậc 10%) xen kẽ khi BLE chập chờn làm đường răng cưa 90↔93:
        # chỉ vẽ theo nguồn của mẫu mới nhất nếu nguồn đó có đủ dữ liệu.
        if samples:
            same_source = [s for s in samples if s.get('source') == samples[-1].get('source')]
            if len(same_source) >= 2:
                samples = same_source
        if not samples:
            return
        canvas = self.history_canvas
        canvas.configure(bg=CARD)
        width = max(canvas.winfo_width(), 300)
        left, right, top, bottom = 4, width - 30, 6, 64
        # Grid at 100/50/0% with labels on the right.
        for level in (100, 50, 0):
            y = bottom - level / 100 * (bottom - top)
            canvas.create_line(left, y, right, y, fill=BORDER, dash=(2, 3))
            canvas.create_text(right + 4, y, text=f'{level}', anchor='w', fill=DIM,
                               font=('Segoe UI', 8))
        # The time axis fits the data (at least one hour) instead of always spanning the
        # whole 24h/7d window, so a short history is not squashed into the right edge.
        span = min(self._chart_days * 86400, max(now - samples[0]['t'], 3600))
        start = now - span
        points = [(left + (s['t'] - start) / span * (right - left),
                   bottom - s['level'] / 100 * (bottom - top)) for s in samples]
        if len(points) > 1:
            # Vùng tô dưới đường: Tk không có alpha nên pha sẵn màu nhấn với nền thẻ.
            area = [(points[0][0], bottom), *points, (points[-1][0], bottom)]
            canvas.create_polygon(*[v for point in area for v in point],
                                  fill=blend(ACCENT, CARD, .12), outline='')
            canvas.create_line(*[v for point in points for v in point], fill=ACCENT, width=2,
                               joinstyle='round', capstyle='round')
        x, y = points[-1]
        canvas.create_oval(x - 6, y - 6, x + 6, y + 6, fill=blend(ACCENT, CARD, .3), outline='')
        canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=ACCENT, outline=CARD, width=1)
        hours = span / 3600
        ago = (tr('{days} ngày trước', days=round(hours / 24)) if hours >= 48
               else tr('{hours} giờ trước', hours=max(1, round(hours))))
        canvas.create_text(left, bottom + 10, text=ago if len(points) > 1 else tr('Đang thu thập dữ liệu…'),
                           anchor='w', fill=DIM, font=('Segoe UI', 8))
        canvas.create_text(right, bottom + 10, text=tr('bây giờ'), anchor='e', fill=DIM,
                           font=('Segoe UI', 8))

    def _low_battery_level(self):
        """Lowest level that is not charging; a charging earbud must not hide the other."""
        if self._showing_components():
            known = [(level, self.charging_info.get(side) is True) for level, side in
                     ((self.left_level, 'left'), (self.right_level, 'right')) if level is not None]
            levels = [level for level, charging in known if not charging]
            if levels:
                return min(levels), False
            return None, bool(known)  # every known earbud is charging
        return self._general_level()[0], False

    def _low_battery_reading(self, device):
        """Như _low_battery_level nhưng cho một thiết bị bất kỳ đang kết nối (không chỉ thiết bị đang xem)."""
        if device is self.active_headphone:
            return self._low_battery_level()
        components = self._components.get(device['id'])
        if device['id'] in self._component_owners() and components and components[3].get('_fresh') is not False:
            known = [(level, components[3].get(side) is True)
                     for level, side in zip(components[:2], ('left', 'right')) if level is not None]
            if known:
                levels = [level for level, charging in known if not charging]
                return (min(levels), False) if levels else (None, True)
        level, _, charging = self._device_sample(device)
        return level, charging is True

    @staticmethod
    def _alert_title(device, headphone_title, other_title):
        return headphone_title if device.get('kind', 'headphone') == 'headphone' else other_title

    def _check_low_battery(self):
        # Mọi thiết bị đang kết nối, không chỉ thiết bị đang xem: tai nghe thứ hai hay chuột cũng có thể hết pin.
        threshold = self.settings["low_battery_threshold"]
        for device in list(self.connected_headphones):
            level, charging = self._low_battery_reading(device)
            key = device['id']
            if level is None and not charging:
                continue  # missing data is not a recovery; keep the latch
            if charging or level > threshold + 5:
                self._low_alerted.discard(key)
            elif (level <= threshold and key not in self._low_alerted
                  and self.settings["low_battery_alert"]):
                if self._alert(tr("{name} còn {level}% pin. Hãy sạc sớm.", name=self._device_name(device), level=level),
                               self._alert_title(device, tr("Pin tai nghe yếu"), tr("Pin thiết bị yếu"))):
                    self._low_alerted.add(key)

    def _alert(self, message, title, urgent=True):
        """Cảnh báo pin: thông báo Windows + bảng nổi + âm báo. Windows thường chặn thông báo khi đang
        chơi game toàn màn hình, nên bảng nổi và âm báo là đường dự phòng."""
        shown = False
        if self.settings['alert_overlay']:
            self._show_toast(title, message, glyph='battery_low' if urgent else 'headphone',
                             color=LOW if urgent else GOOD, duration=8000)
            shown = True
        if urgent and self.settings['alert_sound']:
            try:
                winsound.PlaySound('SystemExclamation',
                                   winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            except RuntimeError:
                logging.debug('Cannot play alert sound', exc_info=True)
        return self._notify(message, title) or shown

    def _check_time_alert(self):
        """Báo trước khi hết pin theo thời gian ước tính (khi có đủ lịch sử xả pin)."""
        minutes = self.settings['time_alert_minutes']
        if not minutes:
            return
        for device in list(self.connected_headphones):
            key = device['id']
            level, _, charging = self._device_sample(device)
            if level is None:
                continue
            remaining = None if charging else estimate_remaining(self.history.samples(key))
            if charging or (remaining is not None and remaining > minutes + 10):
                self._time_alerted.discard(key)
            elif remaining is not None and remaining <= minutes and key not in self._time_alerted:
                if self._alert(tr("{name} còn khoảng {minutes} phút pin ({level}%).", name=self._device_name(device),
                                  minutes=max(1, round(remaining)), level=level),
                               self._alert_title(device, tr('Sắp hết pin tai nghe'), tr('Sắp hết pin thiết bị'))):
                    self._time_alerted.add(key)

    def _notify(self, message, title):
        """Return True only if the notification was actually handed to Windows."""
        icon = self.tray_icon
        if not icon or not getattr(icon, "visible", False):
            return False
        try:
            icon.notify(message, title)
            return True
        except Exception:
            logging.exception("Cannot show notification")
            return False

    def _sync_mini_widget(self):
        if not self.settings['mini_widget']:
            if self._mini_window and self._mini_window.winfo_exists():
                self._mini_window.destroy()
            self._mini_window = self._mini_label = None
            return
        if not self._mini_window or not self._mini_window.winfo_exists():
            win = MiniWidgetWindow(self)
            self._mini_window = win
            win.overrideredirect(True)
            win.attributes('-topmost', True)
            win.attributes('-alpha', 0.9)
            position = self.settings['mini_widget_pos'] or [40, 40]
            # Positions are physical screen pixels; CTk.geometry scales offsets by DPI.
            tk.Toplevel.geometry(win, f'+{position[0]}+{position[1]}')
            self._mini_label = ctk.CTkLabel(win, text='', font=ctk.CTkFont(size=13, weight='bold'))
            self._mini_label.pack(padx=10, pady=5)
            for widget in (win, self._mini_label):
                widget.bind('<ButtonPress-1>', self._start_mini_drag)
                widget.bind('<B1-Motion>', self._move_mini)
                widget.bind('<ButtonRelease-1>', self._save_mini_position)
                widget.bind('<Double-Button-1>', lambda event: self.show_window())
        device = self.active_headphone
        text = '🎧 ' + tr('Chưa kết nối')
        if device:
            name = self._device_name(device)
            if self._showing_components():
                # The flash sits next to the part that is charging; a charging case alone
                # must not look like charging earbuds.
                parts = []
                for tag, level, side in (('L', self.left_level, 'left'), ('R', self.right_level, 'right')):
                    value = level if level is not None else '--'
                    parts.append(f"{tag} {'⚡' if self.charging_info.get(side) is True else ''}{value}")
                text = f"{name} · {' · '.join(parts)}"
            else:
                level = self._general_level()[0]
                value = f'{level}%' if level is not None else '--%'
                text = f'{self._device_icon(device)} {name} · {value}'
        self._mini_window.configure(fg_color=CARD)
        self._mini_label.configure(text=text, text_color=TEXT)

    def _start_mini_drag(self, event):
        self._mini_drag_offset = (event.x_root - self._mini_window.winfo_x(),
                                  event.y_root - self._mini_window.winfo_y())

    def _move_mini(self, event):
        x = event.x_root - self._mini_drag_offset[0]
        y = event.y_root - self._mini_drag_offset[1]
        tk.Toplevel.geometry(self._mini_window, f'+{x}+{y}')

    def _save_mini_position(self, event):
        self._mini_window.update_idletasks()
        self._set_setting('mini_widget_pos', [self._mini_window.winfo_x(), self._mini_window.winfo_y()])

    def _complete_status_request(self, result, done):
        """Runs on the Tk thread for the IPC STATUS command."""
        try:
            active = self.active_headphone
            level, source = self._general_level()
            result.update({
                "left": self.left_level,
                "right": self.right_level,
                "case": self.case_level,
                "charging": self.charging_info,
                "connected": self.is_connected,
                "device_name": active['name'] if active else None,
                "device_id": active['id'] if active else None,
                "connected_headphones": [dict(device) for device in self.connected_headphones],
                "connection_error": self.connection_error,
                "battery_fresh": self.battery_is_fresh,
                "battery_level": level,
                "battery_source": {'ble': 'ble_gatt', 'windows': 'windows_pnp'}.get(source),
                "windows_battery_level": self.windows_battery_level,
                "component_battery_source": 'fast_pair' if self.battery_is_fresh else None,
                "status": self.status_text,
                "updated_at": self.last_update_str,
            })
        finally:
            done.set()

    def _on_refresh_clicked(self):
        self.btn_refresh.configure(state="disabled")
        self.core.trigger_refresh()
        self.connection_monitor.trigger_refresh()
        self.gatt_monitor.trigger_refresh()
        self.after(1000, lambda: self.btn_refresh.configure(state="normal"))

    def _open_bluetooth_settings(self):
        try:
            os.startfile('ms-settings:bluetooth')
        except OSError:
            logging.exception('Cannot open Bluetooth settings')

    def show_window(self):
        # Đọc lại thiết bị phát (có thể đã cắm/rút khi bảng ẩn) trước khi tính chiều cao cửa sổ.
        self._render_audio_row()
        self._audio_menu_version += 1  # Windows có thể đã đổi thiết bị phát: menu khay đọc lại
        self._position_window()
        self.deiconify()
        self.state("normal")
        self._style_title_bar(self)
        self.attributes("-topmost", True)
        self.lift()
        self.focus_force()
        self.after(500, lambda: self.attributes("-topmost", self.is_pinned))
        self._update_ui_values()

    def hide_to_tray(self):
        self.withdraw()

    def toggle_window(self):
        if self.state() == "withdrawn" or not self.winfo_viewable():
            self.show_window()
        else:
            self.hide_to_tray()

    # --- SYSTEM TRAY ---
    def _create_tray_image(self):
        if getattr(self, "_logo_image", None) is not None:
            return self._logo_image
        if os.path.exists(ICON_PNG):
            try:
                with Image.open(ICON_PNG) as source:
                    self._logo_image = source.copy()
                return self._logo_image
            except Exception:
                pass
        img = Image.new("RGBA", (64, 64), color=(0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([8, 8, 56, 56], fill=(255, 106, 19))
        return img

    def _create_percent_image(self, level, charging):
        """Biểu tượng khay: vòng pin cùng kiểu bảng pin, số % ở giữa (vẽ lớn rồi thu nhỏ cho mịn)."""
        scale, size = 4, 64
        px = size * scale
        img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        dark = THEMES['dark']
        # Khay luôn dùng bảng màu tối, không đổi theo giao diện app.
        color = level_color(level, charging, dark)
        stroke = 8 * scale
        # Nền tròn tối để số đọc rõ trên cả taskbar sáng và tối.
        draw.ellipse((0, 0, px - 1, px - 1), fill=dark['CARD'])
        draw.ellipse((0, 0, px - 1, px - 1), outline=dark['TRACK'], width=stroke)
        if level:
            end = -90 + 360 * level / 100
            draw.arc((0, 0, px - 1, px - 1), -90, end, fill=color, width=stroke)
            radius = (px - stroke) / 2
            for angle in (-90, end):
                cx = px / 2 + radius * math.cos(math.radians(angle))
                cy = px / 2 + radius * math.sin(math.radians(angle))
                draw.ellipse((cx - stroke / 2, cy - stroke / 2, cx + stroke / 2, cy + stroke / 2), fill=color)
        text = str(level)
        try:
            font = ImageFont.truetype("seguisb.ttf", (24 if level == 100 else 30) * scale)
        except OSError:
            try:
                font = ImageFont.truetype("arialbd.ttf", (24 if level == 100 else 30) * scale)
            except OSError:
                font = ImageFont.load_default()
        draw.text((px / 2, px / 2), text, font=font, anchor="mm",
                  fill=color if level <= 20 or charging else dark['TEXT'])
        return img.resize((size, size), Image.LANCZOS)

    def _create_offline_image(self):
        """Logo app chuyển xám: tai nghe vừa dùng đang không kết nối."""
        if getattr(self, "_offline_image", None) is None:
            logo = self._create_tray_image().convert("RGBA")
            gray = logo.convert("LA").convert("RGBA")
            gray.putalpha(logo.getchannel("A").point(lambda alpha: alpha * 2 // 3))
            self._offline_image = gray
        return self._offline_image

    def _tray_device_items(self):
        devices = list(self.connected_headphones)
        active_id = self.active_headphone['id'] if self.active_headphone else None
        if not devices:
            return [item(tr("Chưa có tai nghe kết nối"), None, enabled=False)]
        def select(key):
            # pystray rejects actions taking more than (icon, item), so no default-arg lambdas.
            return lambda: self._enqueue_ui(self._select_headphone, key)

        return [item(self._device_name(device), select(device['id']),
                     checked=lambda menu_item, key=device['id']: key == active_id,
                     radio=True)
                for device in devices]

    def _tray_menu(self):
        """Menu khay theo ngôn ngữ hiện tại; đổi ngôn ngữ thì dựng lại (_apply_language)."""
        return pystray.Menu(
            item(tr("Mở bảng điều khiển"), lambda: self._enqueue_ui(self.show_window), default=True),
            item(tr("Làm mới pin ngay"), lambda: self._enqueue_ui(self._on_refresh_clicked)),
            item(tr("Chọn tai nghe"), pystray.Menu(self._tray_device_items)),
            item(tr("Âm thanh phát ra"), pystray.Menu(self._tray_audio_items)),
            item(tr("Đổi loa ↔ tai nghe (Ctrl+Alt+H)"), lambda: self._enqueue_ui(self._toggle_audio_output)),
            pystray.Menu.SEPARATOR,
            item(tr("Thông báo pin yếu"), lambda: self._enqueue_ui(
                self._set_setting, "low_battery_alert", not self.settings["low_battery_alert"]),
                checked=lambda menu_item: self.settings["low_battery_alert"]),
            item(tr("Khởi động cùng Windows"), self._toggle_tray_startup, checked=lambda item: is_startup_enabled()),
            item(tr("Cài đặt..."), lambda: self._enqueue_ui(self._show_settings_dialog)),
            item(tr('Thiết bị Bluetooth xung quanh…'), lambda: self._enqueue_ui(self._show_nearby_panel)),
            item('Bluetooth…', lambda: self._enqueue_ui(self._open_bluetooth_settings)),
            item(tr('Cửa sổ nổi'), lambda: self._enqueue_ui(
                self._set_setting, 'mini_widget', not self.settings['mini_widget']),
                checked=lambda menu_item: self.settings['mini_widget']),
            pystray.Menu.SEPARATOR,
            item(tr('Gửi góp ý…'), lambda: self._enqueue_ui(self._show_feedback_dialog)),
            item(tr("Thoát"), lambda: self._enqueue_ui(self._exit_app))
        )

    def _init_tray(self):
        initial_img = self._create_tray_image()
        self.tray_icon = pystray.Icon(
            "BluetoothMonitor",
            initial_img,
            tr("Chưa có tai nghe kết nối"),
            menu=self._tray_menu()
        )
        self.tray_icon.run_detached()

    def _toggle_tray_startup(self, icon, item):
        set_startup(not is_startup_enabled())

    def _update_tray_icon(self):
        level, charging = self._display_level() if self.active_headphone else (None, False)
        if not self.settings["tray_show_percent"]:
            level = None
        offline = self._offline_device() is not None
        key = ('offline', False) if offline else (level, charging)
        if key == self._tray_icon_key:
            return
        self._tray_icon_key = key
        if offline:
            self.tray_icon.icon = self._create_offline_image()
        else:
            self.tray_icon.icon = (self._create_percent_image(level, charging) if level is not None
                                   else self._create_tray_image())

    def _update_tray_menu(self):
        active_id = self.active_headphone['id'] if self.active_headphone else None
        # pystray (win32) chỉ dựng lại menu khi update_menu(): mọi thứ có dấu tích phải nằm trong khóa này.
        key = (tuple((d['id'], self._device_name(d)) for d in self.connected_headphones), active_id,
               self.settings['low_battery_alert'], self.settings['mini_widget'], self._audio_menu_version)
        if key != self._tray_menu_key:
            self._tray_menu_key = key
            self.tray_icon.update_menu()

    def _tray_title(self):
        if not self.active_headphone:
            offline = self._offline_device()
            if offline and self.connection_error == RADIO_OFF:
                return tr("{name} | Không kết nối · Bluetooth của máy đang tắt", name=self._device_name(offline))
            if offline:
                return tr("{name} | Không kết nối", name=self._device_name(offline))
            return tr(self.connection_error or "Chưa có tai nghe kết nối")
        name = self._device_name(self.active_headphone)
        level, source = self._general_level()
        if not self.battery_is_fresh and level is not None:
            return tr("{name} | Pin: {level}% ({source})", name=name, level=level,
                      source='BLE' if source == 'ble' else 'Windows')
        if not self._supports_active_battery():
            return tr("{name} | Đã kết nối · chưa có pin", name=name)
        l_str = f"{self.left_level}%" if self.left_level is not None else "--"
        r_str = f"{self.right_level}%" if self.right_level is not None else "--"
        if self.charging_info.get("left"):
            l_str = "⚡" + l_str
        if self.charging_info.get("right"):
            r_str = "⚡" + r_str
        title = f"{name} | L: {l_str} | R: {r_str}"
        if self.case_level is not None:
            title += tr(" | Hộp: {level}%", level=self.case_level)
        return title

    def _update_tray(self):
        if not self.tray_icon:
            return
        try:
            self.tray_icon.title = self._tray_title()[:127]
        except Exception:
            logging.debug("Cannot update tray title", exc_info=True)
        for update in (self._update_tray_icon, self._update_tray_menu):
            try:
                update()
            except Exception:
                logging.debug("Cannot update tray", exc_info=True)

    def _exit_app(self, *args):
        self._closing = True
        self.after_cancel(self._ui_poll_id)
        self.after_cancel(self._history_timer)
        self.ipc_sock.close()
        if self._nearby_scanner:
            self._nearby_scanner.stop()
        self.apple_scanner.stop()
        self.fast_pair_scanner.stop()
        def stop(worker):
            try:
                worker.stop()
            except Exception:
                logging.exception('Cannot stop %s', type(worker).__name__)
        # Dừng song song với chung một hạn chót: lần lượt từng cái (mỗi cái chờ tới 3–5 giây) làm app đứng
        # hơn 10 giây khi thoát nếu một luồng Bluetooth bị treo.
        stoppers = [threading.Thread(target=stop, args=(worker,), daemon=True)
                    for worker in (self.hotkey_worker, self.connection_monitor, self.gatt_monitor, self.core)]
        for stopper in stoppers:
            stopper.start()
        deadline = time.monotonic() + EXIT_TIMEOUT_SECONDS
        for stopper in stoppers:
            stopper.join(max(0.0, deadline - time.monotonic()))
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.destroy()
        sys.exit(0)


def try_single_instance():
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Windows cho tiến trình khác bind đè cổng bằng SO_REUSEADDR (cướp cổng) nếu không giữ độc quyền.
    server_sock.setsockopt(socket.SOL_SOCKET, getattr(socket, 'SO_EXCLUSIVEADDRUSE', socket.SO_REUSEADDR), 1)
    try:
        server_sock.bind(("127.0.0.1", IPC_PORT))
        server_sock.listen(5)
    except OSError:
        server_sock.close()
        try:
            with socket.create_connection(("127.0.0.1", IPC_PORT), timeout=2) as client:
                client.sendall(b"SHOW")
        except OSError as error:
            raise RuntimeError("Cannot contact the existing Bluetooth Monitor instance") from error
        sys.exit(0)
    server_sock.settimeout(0.5)
    return server_sock


def start_ipc_listener(app, server_sock):
    def handle(conn):
        with conn:
            conn.settimeout(2)
            data = conn.recv(1024).strip()
            if data == b"SHOW":
                app._enqueue_ui(app.show_window)
            elif data == b"REFRESH":
                app._enqueue_ui(app._on_refresh_clicked)
            elif data == b"STATUS":
                # App state belongs to the Tk thread; ask it for one consistent snapshot.
                result, done = {}, threading.Event()
                app._enqueue_ui(app._complete_status_request, result, done)
                payload = result if done.wait(2) else {"error": "busy"}
                conn.sendall(json.dumps(payload).encode("utf-8"))

    def listen_ipc():
        while not app._closing:
            try:
                conn, _ = server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                handle(conn)
            except OSError:
                # Kết nối bỏ lửng/ngắt giữa chừng: bỏ qua, không ghi traceback (tránh bị làm đầy log).
                logging.debug("IPC connection dropped", exc_info=True)
            except Exception:
                # One bad request must never stop the listener.
                logging.exception("IPC request failed")

    threading.Thread(target=listen_ipc, daemon=True, name="IpcListener").start()


JblApp = JblFlyoutApp

if __name__ == "__main__":
    configure_logging()
    start_mini = "--minimized" in sys.argv
    app = JblFlyoutApp(start_minimized=start_mini)
    app.mainloop()
