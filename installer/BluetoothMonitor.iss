; Bộ cài đặt Bluetooth Monitor (Inno Setup 6, miễn phí: https://jrsoftware.org/isinfo.php)
; Tạo bằng build_portable.ps1 (tự gọi ISCC.exe nếu đã cài Inno Setup), hoặc mở file này trong Inno Setup.
; Cài cho riêng người dùng hiện tại: không cần quyền quản trị.

#define AppName "Bluetooth Monitor"
#ifndef AppVersion
  #define AppVersion "0.0"
#endif

[Setup]
AppId={{6F3B8E2A-5C41-4C8E-9C7B-2D1A4F0B9E61}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppName}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Bluetooth_Monitor_Setup
SetupIconFile=..\assets\app_icon.ico
UninstallDisplayIcon={app}\{#AppName}.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Tạo biểu tượng ngoài Desktop"; Flags: unchecked
Name: "startup"; Description: "Khởi động cùng Windows"; Flags: unchecked

[Files]
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "..\THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\PRIVACY.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppName}.exe"
Name: "{group}\Gỡ cài đặt {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; Tasks: desktopicon

[Registry]
; Cùng tên mục mà app dùng cho "Khởi động cùng Windows", nên bật/tắt trong app vẫn khớp.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Bluetooth_Monitor"; ValueData: """{app}\{#AppName}.exe"" --minimized"; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppName}.exe"; Description: "Mở {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Chỉ tắt bản nằm trong thư mục cài đặt; bản portable cùng tên chạy ở chỗ khác không bị đụng tới.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Get-Process -Name '{#AppName}' -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -like '{app}\*' } | Stop-Process -Force"""; Flags: runhidden; RunOnceId: "StopApp"
