$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONPATH = Join-Path $PSScriptRoot 'build_tools'
$python = 'C:\Python314\python.exe'
$dist = Join-Path $PSScriptRoot 'dist'
$build = Join-Path $PSScriptRoot 'build'

# Thông tin phiên bản nhúng vào exe (Properties › Details).
$versionFile = Join-Path $build 'version_info.txt'
$version = & $python (Join-Path $PSScriptRoot 'build_tools\write_version_info.py') $versionFile
if ($LASTEXITCODE -ne 0) { throw 'Cannot write version info' }

# Ghi công giấy phép thư viện đóng gói kèm (bắt buộc với LGPL/MIT/BSD), luôn khớp phiên bản thư viện đang dùng.
& $python (Join-Path $PSScriptRoot 'build_tools\write_notices.py') (Join-Path $PSScriptRoot 'THIRD_PARTY_NOTICES.txt')
if ($LASTEXITCODE -ne 0) { throw 'Cannot write third-party notices' }

# numpy và bộ giải mã AVIF chỉ bị PIL tham chiếu tùy chọn, app không dùng: bỏ ra cho gói nhẹ (77 MB -> 42 MB).
# Đường dẫn tuyệt đối vì --specpath đổi gốc của đường dẫn tương đối.
$common = @('--noconfirm', '--windowed', '--name', 'Bluetooth Monitor',
            '--icon', (Join-Path $PSScriptRoot 'assets\app_icon.ico'),
            '--add-data', ((Join-Path $PSScriptRoot 'assets') + ';assets'),
            '--add-data', ((Join-Path $PSScriptRoot 'THIRD_PARTY_NOTICES.txt') + ';.'),
            '--version-file', $versionFile,
            '--collect-all', 'customtkinter', '--collect-all', 'bleak', '--collect-submodules', 'winrt',
            '--exclude-module', 'numpy', '--exclude-module', 'PIL.AvifImagePlugin', '--exclude-module', 'PIL._avif',
            '--specpath', $build)

# 1) Bản thư mục: khởi động nhanh nhất; dùng cho zip portable và bộ cài đặt.
& $python -m PyInstaller @common --onedir --distpath $dist --workpath (Join-Path $build 'onedir') launcher.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }

# 2) Bản một file: dist\Bluetooth Monitor.exe, chép đi đâu chạy đó (mỗi lần mở tự giải nén vào thư mục tạm).
& $python -m PyInstaller @common --onefile --distpath $dist --workpath (Join-Path $build 'onefile') launcher.py
if ($LASTEXITCODE -ne 0) { throw 'One-file build failed' }

# Bộ cài đặt: chỉ khi đã cài Inno Setup 6 (miễn phí); không có thì bỏ qua, bản portable vẫn dùng được.
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    & $iscc "/DAppVersion=$version" (Join-Path $PSScriptRoot 'installer\BluetoothMonitor.iss')
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
} else {
    Write-Host 'Chưa cài Inno Setup 6: bỏ qua bộ cài đặt (https://jrsoftware.org/isinfo.php).'
}
