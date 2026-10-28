"""Ngôn ngữ giao diện: tiếng Việt (gốc) và tiếng Anh.

Chuỗi gốc trong code là tiếng Việt và cũng là khóa tra bản dịch: tr("Đã kết nối {name}", name=...).
Chỉ dịch ở chỗ HIỂN THỊ; chuỗi dùng để so sánh trong code (trạng thái, mã lỗi) giữ nguyên tiếng Việt.
Mỗi module tự đăng ký bản dịch của mình bằng register({...}) để không ai phải sửa chung một từ điển lớn.
"""
import ctypes
import logging
import re

LANGUAGES = ('vi', 'en')
_catalogs = {'en': {}}
_language = 'vi'
_patterns = {}  # mẫu có {chỗ trống} -> regex, cho tr_message


def register(english):
    """Thêm bản dịch tiếng Anh: {chuỗi gốc tiếng Việt: bản tiếng Anh}. Cùng khóa thì bản sau ghi đè."""
    _catalogs['en'].update(english)


def system_language():
    """'vi' nếu ngôn ngữ giao diện Windows là tiếng Việt, ngược lại 'en'."""
    try:
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return 'vi' if lang_id & 0x3FF == 0x2A else 'en'  # LANG_VIETNAMESE
    except Exception:
        logging.debug('Cannot read Windows UI language', exc_info=True)
        return 'en'


def resolve(setting):
    """Giá trị cài đặt ('system' / 'vi' / 'en') -> ngôn ngữ thật."""
    return setting if setting in LANGUAGES else system_language()


def set_language(language):
    global _language
    _language = language if language in LANGUAGES else 'vi'


def language():
    return _language


def tr(text, **values):
    """Bản dịch của chuỗi gốc (tiếng Việt) theo ngôn ngữ hiện tại; thiếu bản dịch thì dùng chuỗi gốc."""
    if _language != 'vi':
        text = _catalogs.get(_language, {}).get(text, text)
    return text.format(**values) if values else text


def tr_message(text):
    """Dịch một thông báo ĐÃ ghép giá trị (vd. luồng nền trả về 'Không ghép đôi được (FAILED)').

    Khớp nguyên chuỗi trước, rồi khớp các mẫu có {chỗ trống} đã đăng ký; không khớp thì giữ nguyên."""
    if _language == 'vi' or not isinstance(text, str):
        return text
    catalog = _catalogs.get(_language, {})
    if text in catalog:
        return catalog[text]
    for source, target in catalog.items():
        if '{' not in source:
            continue
        pattern = _patterns.get(source)
        if pattern is None:
            parts = re.split(r'\{(\w+)\}', source)
            if len(set(parts[1::2])) != len(parts[1::2]):
                continue  # một chỗ trống lặp lại: không dùng làm mẫu
            pattern = _patterns[source] = re.compile(''.join(
                f'(?P<{part}>.+?)' if index % 2 else re.escape(part) for index, part in enumerate(parts)), re.S)
        match = pattern.fullmatch(text)
        if match:
            return target.format(**match.groupdict())
    return text
