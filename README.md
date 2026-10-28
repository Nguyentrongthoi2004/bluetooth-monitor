<p align="center"><img src="assets/app_icon.png" width="96" alt=""></p>

<h1 align="center">Bluetooth Monitor</h1>

<p align="center">
See your Bluetooth headphones' battery — left, right and case — right from the Windows taskbar.<br>
Free · no ads · no data collection · English &amp; Tiếng Việt
</p>

---

## Features

- **Battery in the tray icon**, with the percentage always visible.
- **Left, right and case** for true wireless earbuds that broadcast Google Fast Pair battery data (many Sony, JBL and
  Pixel Buds models), and for Apple AirPods / Beats.
- **1% readings** for devices with the standard Bluetooth Battery Service; the Windows battery level for the rest.
- **Alerts that show over full-screen games**: low battery, "running out soon" (from your usage history) and fully
  charged. Several headsets connecting or disconnecting at once give one notification, not a stream of them.
- **Battery history** chart (24 h / 7 days) and an estimate of the time left.
- **Speakers ↔ headphones** switch in one click or with Ctrl+Alt+H. Works with FxSound.
- **Nearby Bluetooth devices**: details, pair (with PIN confirmation), unpair, connect. Nothing happens unless you
  press the button.
- Mice, keyboards, controllers and paired phones in the same list. Light/dark theme, compact mode, floating mini
  widget.

Per-earbud battery depends on what your earbuds broadcast. Over-ear headphones report a single level. Samsung Galaxy
Buds are not supported yet.

## Download

Download `Bluetooth_Monitor_Setup.exe` (installer) or `Bluetooth Monitor.exe` (portable, single file) from
[**Releases**](../../releases). The files are not code-signed yet, so Windows SmartScreen may warn: choose
**More info › Run anyway**.

Requirements: Windows 10 (version 2004) or Windows 11 with Bluetooth.

## Privacy

The app runs entirely on your PC and makes no internet connections. Read the full policy in
[docs/PRIVACY.md](docs/PRIVACY.md).

## Feedback & support

- Report bugs or ideas in the app: **Settings › Feedback & support › Send feedback**, or open an
  [issue](../../issues).
- If the app is useful to you: [**ko-fi.com/thoinguyen**](https://ko-fi.com/thoinguyen) ☕. In Vietnam you can also use
  the bank-transfer QR codes inside the app. Support is optional and unlocks nothing.

## Build from source

```powershell
python -m pip install -r requirements.txt
python src\main.py                  # run from source
python run_tests.py                 # all tests (each file in its own process)
python run_tests.py test_security   # one test file
.\build_portable.ps1                # dist\: one-file exe, folder build, installer (if Inno Setup 6 is installed)
.\build_msix.ps1                    # Microsoft Store package
```

```text
src/          the app (entry point: launcher.py → main.py)
tests/        unit tests (test_*.py)
assets/       icon and donation QR codes
docs/         privacy policy, Store listing and publishing guide, changelog (Vietnamese)
packaging/    installer (Inno Setup) and MSIX manifest
build_tools/  helper scripts used by the build
```

| File in `src/` | What it does |
|---|---|
| `main.py` | App window, tray, settings, alerts |
| `headphone_monitor.py` | Watches Bluetooth connections (WinRT DeviceWatcher) |
| `jbl_core.py`, `fast_pair.py`, `apple_pods.py` | Per-earbud battery from Fast Pair / Apple advertisements (passive scan) |
| `ble_battery.py` | Standard Bluetooth Battery Service (GATT) |
| `bt_scan.py`, `bt_connect.py` | Nearby devices, pairing, connect/disconnect |
| `audio_output.py`, `ui_audio.py` | Speakers ↔ headphones switch |
| `ui_*.py` | Panels: toast, nearby devices, per-earbud picker, feedback & support |
| `i18n.py` | Vietnamese / English |
| `safe_text.py` | Cleans device names; hides addresses in shared logs |
| `product.py` | Release settings: feedback form, donation, Store identity |

## License

[MIT](LICENSE) © 2026 Nguyen Trong Thoi. Third-party components and their licenses are listed in
[THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).

---

## Tiếng Việt

**Bluetooth Monitor** hiện pin tai nghe Bluetooth (tai trái, tai phải, hộp sạc) ngay trên thanh taskbar Windows.
Miễn phí, không quảng cáo, không thu thập dữ liệu.

- Tải bản cài hoặc bản một file ở mục [**Releases**](../../releases).
- Góp ý ngay trong app: **Cài đặt › Góp ý & ủng hộ**.
- Ủng hộ tác giả bằng mã QR trong app, hoặc qua [Ko-fi](https://ko-fi.com/thoinguyen).
- Lịch sử thay đổi: [docs/CHANGELOG.vi.txt](docs/CHANGELOG.vi.txt).
