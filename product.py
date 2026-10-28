"""Thông tin sản phẩm khi phát hành (Microsoft Store, GitHub). Điền một lần ở đây, không rải khắp code.

Để trống (None) thì tính năng tương ứng tự ẩn hoặc dùng cách dự phòng, app vẫn chạy bình thường.
"""

# Góp ý qua Google Form, không cần máy chủ riêng. Cách lấy link: xem STORE_GUIDE.md, mục "Google Form góp ý".
# Dán "đường liên kết điền sẵn" của form, trong đó các ô được điền đúng các chữ FB_TYPE, FB_MESSAGE, FB_SYSTEM,
# FB_LOG. App thay các chữ này bằng nội dung thật. Chưa có link: góp ý được chép vào clipboard để tự gửi.
FEEDBACK_FORM_URL = None

# Nút "Ủng hộ": mã QR chuyển khoản (ảnh trong assets\, giữ nguyên độ phân giải để quét được) và/hoặc trang web
# (Ko-fi, Buy Me a Coffee…). Không có cái nào thì không hiện nút. Ủng hộ là tùy tâm, không mở khóa tính năng.
DONATE_QR = (
    ('donate_qr_transfer.png', 'Quét để chuyển tiền'),
    ('donate_qr_vietinbank.png', 'VietinBank · VietQR'),
)
DONATE_BANK_TEXT = 'VietinBank · 102873285098 · NGUYEN TRONG THOI'  # cho người chuyển khoản trên máy tính
DONATE_URL = 'https://ko-fi.com/thoinguyen'  # ủng hộ quốc tế (PayPal qua Ko-fi)

# Trang chủ / mã nguồn (GitHub) và chính sách quyền riêng tư công khai (Store yêu cầu có link).
HOMEPAGE_URL = 'https://github.com/Nguyentrongthoi2004/bluetooth-monitor'
PRIVACY_URL = 'https://github.com/Nguyentrongthoi2004/bluetooth-monitor/blob/main/PRIVACY.md'

# Định danh gói MSIX: Partner Center cấp khi bạn đặt tên app trên Microsoft Store
# (Product management › Product identity). Giá trị dưới đây chỉ để build thử.
MSIX_IDENTITY_NAME = 'BluetoothMonitor.Local'
MSIX_PUBLISHER = 'CN=BluetoothMonitorLocal'
MSIX_PUBLISHER_DISPLAY = 'Bluetooth Monitor'

FEEDBACK_TOKENS = ('FB_TYPE', 'FB_MESSAGE', 'FB_SYSTEM', 'FB_LOG')
TRUSTED_FORM_PREFIXES = ('https://docs.google.com/forms/', 'https://forms.gle/')


def feedback_form_ready(url=None):
    url = FEEDBACK_FORM_URL if url is None else url
    return bool(url) and url.startswith(TRUSTED_FORM_PREFIXES) and all(token in url for token in FEEDBACK_TOKENS)


def safe_link(url):
    """Chỉ mở link https (link sản phẩm do người phát hành điền, nhưng vẫn không mở giao thức lạ)."""
    return url if isinstance(url, str) and url.startswith('https://') else None
