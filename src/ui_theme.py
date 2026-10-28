"""Bảng màu hiện tại và hàm vẽ dùng chung cho các phần giao diện.

main.py giữ THEMES và gọi use() mỗi khi đổi giao diện sáng/tối; các module giao diện đọc màu qua
`ui_theme.BG`, `ui_theme.TEXT`... lúc vẽ nên luôn theo theme đang dùng.
"""
import ctypes


def use(palette):
    globals().update(palette)


def blend(color, base, amount):
    """Pha `amount` phần `color` vào `base` (hex #rrggbb)."""
    a = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(base[i:i + 2], 16) for i in (1, 3, 5)]
    return '#' + ''.join(f'{round(y + (x - y) * amount):02x}' for x, y in zip(a, b))


def get_work_area():
    """Vùng màn hình không bị taskbar che (left, top, right, bottom)."""
    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
    rect = RECT()
    try:
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        pass
    return None


def level_color(level, is_charging=False, palette=None):
    palette = palette or globals()
    if is_charging:
        return palette['CHARGING']
    if level is None:
        return palette['DIM']
    if level > 40:
        return palette['GOOD']
    if level > 20:
        return palette['WARN']
    return palette['LOW']
