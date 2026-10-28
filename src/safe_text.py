"""Làm sạch chữ đến từ bên ngoài và che thông tin riêng trước khi rời khỏi máy.

Tên Bluetooth do chính thiết bị tự đặt: ai ở gần cũng phát được một tên bất kỳ, kể cả tên giả dạng
thông báo ("Windows: gọi 1900… để mở khóa") hoặc chứa ký tự đảo chiều chữ để trông như tên khác.
"""
import re
import unicodedata

NAME_LIMIT = 48
# Ký tự vô hình hoặc điều khiển: xuống dòng, tab, đảo chiều chữ (U+202E…), khoảng trắng độ rộng 0, vùng riêng.
_HIDDEN_CATEGORIES = {'Cc', 'Cf', 'Co', 'Cs', 'Cn'}
_MAC = re.compile(r'(?<![0-9A-Fa-f])[0-9A-Fa-f]{2}([:-])(?:[0-9A-Fa-f]{2}\1){4}[0-9A-Fa-f]{2}(?![0-9A-Fa-f])')
_BARE_MAC = re.compile(r'(?<![0-9A-Za-z])[0-9A-F]{12}(?![0-9A-Za-z])')
_USER_DIR = re.compile(r'(?i)([a-z]:[\\/]+Users[\\/]+)[^\\/\s"\']+')


def clean_name(name, fallback='', limit=NAME_LIMIT):
    """Tên hiển thị an toàn: bỏ ký tự vô hình/điều khiển, gộp khoảng trắng, giới hạn độ dài."""
    if not isinstance(name, str):
        return fallback
    text = ''.join(' ' if ch.isspace() else ch for ch in unicodedata.normalize('NFC', name)
                   if ch.isspace() or unicodedata.category(ch) not in _HIDDEN_CATEGORIES)
    text = ' '.join(text.split())
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + '…'
    return text or fallback


def redact(text):
    """Che địa chỉ Bluetooth (giữ 2 số cuối để còn phân biệt thiết bị) và tên tài khoản Windows."""
    text = _MAC.sub(lambda match: f'XX{match.group(1)}' * 5 + match.group(0)[-2:].upper(), text)
    text = _BARE_MAC.sub(lambda match: 'X' * 10 + match.group(0)[-2:], text)
    return _USER_DIR.sub(r'\1***', text)
