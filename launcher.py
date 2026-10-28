import os, sys, traceback
from pathlib import Path
try:
    import main
    main.configure_logging()
    main.migrate_startup_entry()
    # Bản Microsoft Store mở lúc đăng nhập qua StartupTask (không có tham số --minimized): cũng chạy ẩn trong khay.
    app = main.JblFlyoutApp(start_minimized="--minimized" in sys.argv or main.msix_runtime.launched_at_startup())
    # Tới đây là phiên duy nhất (phiên thứ hai đã thoát trong JblFlyoutApp): dọn thư mục giải nén bị bỏ lại, ở nền.
    import threading
    threading.Thread(target=main.cleanup_stale_extractions, daemon=True, name="CleanupTemp").start()
    app.mainloop()
except Exception:
    folder = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Bluetooth Monitor"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "crash_dump.txt").write_text(traceback.format_exc(), encoding="utf-8")
    import ctypes
    message = "Không mở được ứng dụng. Chi tiết: {path}"
    try:
        import i18n
        i18n.register({message: "Couldn't start the app. Details: {path}"})
        if 'main' in sys.modules:
            # Lỗi có thể xảy ra trước khi app kịp đặt ngôn ngữ: đọc lại từ cài đặt.
            i18n.set_language(i18n.resolve(sys.modules['main'].load_settings()['language']))
        message = i18n.tr(message, path=folder / "crash_dump.txt")
    except Exception:
        message = message.format(path=folder / "crash_dump.txt")
    ctypes.windll.user32.MessageBoxW(None, message, "Bluetooth Monitor", 16)
    # Thoát hẳn: luồng icon khay (không phải daemon) và cổng IPC đã mở sẽ giữ một tiến trình "ma",
    # khiến lần mở sau chỉ gửi SHOW tới nó rồi tự tắt, app không bao giờ hiện.
    os._exit(1)
