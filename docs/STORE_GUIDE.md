# Phát hành Bluetooth Monitor — việc bạn cần làm

Phần code, đóng gói và giấy tờ đã sẵn. Còn lại là những việc cần tài khoản của bạn. Làm theo thứ tự dưới đây. Mỗi
bước xong, gửi mình phần **in đậm** để mình điền vào app và build lại.

## 1. Google Form góp ý (10 phút)

1. Vào https://forms.google.com và tạo form mới tên "Góp ý Bluetooth Monitor".
2. Thêm 4 câu hỏi, đúng thứ tự:
   - **Loại góp ý**: Trả lời ngắn
   - **Nội dung**: Đoạn
   - **Phiên bản và máy**: Trả lời ngắn
   - **Log**: Đoạn
   Không bật "Bắt buộc" cho câu nào. Không bật thu thập email (vì app hứa không thu thập dữ liệu).
3. Bấm ⋮ (góc phải trên) › **Lấy đường liên kết điền sẵn** (Get pre-filled link).
4. Gõ đúng: `FB_TYPE` vào "Loại góp ý", `FB_MESSAGE` vào "Nội dung", `FB_SYSTEM` vào "Phiên bản và máy",
   `FB_LOG` vào "Log". Bấm **Lấy đường liên kết** › **Sao chép đường liên kết**.
5. → Gửi mình **đường liên kết điền sẵn** đó.
6. Xem góp ý: tab "Câu trả lời" › biểu tượng Google Sheets.

Chưa có link này thì nút "Gửi góp ý" vẫn chạy: nội dung được chép vào clipboard để người dùng tự dán gửi.

## 2. Nút ủng hộ — ĐÃ XONG

App đã có 2 mã QR chuyển khoản (ảnh trong `assets\donate_qr_*.png`, thông tin trong `src/product.py`). Tên chủ tài khoản
và số tài khoản sẽ công khai với mọi người dùng. Ủng hộ quốc tế: trang Ko-fi https://ko-fi.com/thoinguyen
(nối PayPal) hiện qua nút "Ủng hộ qua web…" trong cùng cửa sổ.
Lưu ý Store: ủng hộ phải là tùy tâm, không mở khóa tính năng. App đã ghi rõ điều này trong cửa sổ ủng hộ.

## 3. Trang chính sách quyền riêng tư (10 phút)

Store bắt buộc có link chính sách quyền riêng tư. Nội dung đã viết sẵn trong `docs/PRIVACY.md`. Đăng nó công khai, cách dễ
nhất:
- ĐÃ XONG: https://github.com/Nguyentrongthoi2004/bluetooth-monitor/blob/main/docs/PRIVACY.md (đã điền sẵn trong app), hoặc
- Tạo một trang Google Sites / Notion công khai rồi dán nội dung vào.

→ Gửi mình **link trang chính sách**.

## 4. Tài khoản Microsoft Store (Partner Center)

1. Vào https://partner.microsoft.com/dashboard › đăng ký tài khoản **cá nhân** (Individual). Theo mình biết, Microsoft
   đã bỏ phí đăng ký cho tài khoản cá nhân; xem lại khi đăng ký. Cần xác minh danh tính.
2. **Apps and games** › **New product** › **MSIX or PWA app** › đặt tên. Nếu "Bluetooth Monitor" đã có người dùng thì
   chọn tên khác, và báo mình để đổi trong app. Không dùng JBL, AirPods hay tên hãng khác trong tên app.
3. Vào **Product management › Product identity**. → Gửi mình 3 dòng:
   - **Package/Identity/Name**
   - **Package/Identity/Publisher** (dạng `CN=...`)
   - **Package/Properties/PublisherDisplayName**

## 5. Mình làm khi có đủ thông tin trên

- Điền vào `src/product.py`, build lại: `build_portable.ps1` rồi `build_msix.ps1`.
- Ra file `dist\BluetoothMonitor_<phiên bản>_x64.msix` để bạn tải lên Partner Center.

## 6. Nộp lên Store (bạn làm trong Partner Center)

- **Pricing and availability:** Free, chọn các thị trường (có thể chọn tất cả).
- **Properties:** Category "Utilities & tools". Ở mục quyền riêng tư, chọn có thu thập dữ liệu cá nhân hay không: **No**.
- **Age ratings:** trả lời bảng câu hỏi; app không có nội dung nhạy cảm.
- **Packages:** tải file `.msix` lên.
- **Store listings:** thêm tiếng Việt và tiếng Anh, dán nội dung từ `docs/STORE_LISTING.md` kèm 3–5 ảnh chụp màn hình.
- **Submission options / Notes for certification**, dán:
  > Desktop (full trust) app. Needs a Bluetooth adapter; with no Bluetooth device connected it shows
  > "No headphones connected". It makes no internet connections. runFullTrust is needed for Bluetooth/audio Win32 APIs.
- Bấm **Submit**. Microsoft duyệt thường mất vài ngày.

## Lưu ý

- **Giấy phép:** `THIRD_PARTY_NOTICES.txt` đã được đóng gói kèm app. pystray dùng giấy phép LGPL: nếu sau này sửa
  pystray thì phải công bố phần sửa đó.
- **Hỗ trợ người dùng:** app mới chạy thật trên một máy. Sau khi phát hành, theo dõi góp ý thường xuyên trong vài
  tuần đầu.
- **Cập nhật:** mỗi bản mới, mình tăng phiên bản và build MSIX mới; bạn tạo "Update submission" rồi tải lên. Store tự
  cập nhật cho người dùng.
