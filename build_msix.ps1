$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
# Gói MSIX cho Microsoft Store, từ bản thư mục dist\Bluetooth Monitor (chạy build_portable.ps1 trước).
# Store tự ký gói khi phát hành, nên không cần chứng chỉ. Gói build ở đây chưa ký: chỉ để nộp lên Partner Center.
$python = 'C:\Python314\python.exe'
$source = Join-Path $PSScriptRoot 'dist\Bluetooth Monitor'
if (-not (Test-Path (Join-Path $source 'Bluetooth Monitor.exe'))) { throw 'Chưa có dist\Bluetooth Monitor: chạy build_portable.ps1 trước' }

# makeappx từ gói NuGet chính thức Microsoft.Windows.SDK.BuildTools (không cần cài Windows SDK).
$makeappx = Get-ChildItem (Join-Path $PSScriptRoot 'tools\sdk-buildtools') -Recurse -Filter makeappx.exe |
    Where-Object { $_.FullName -like '*\x64\*' } | Select-Object -First 1
if (-not $makeappx) { throw 'Thiếu tools\sdk-buildtools (gói NuGet Microsoft.Windows.SDK.BuildTools)' }

# Phiên bản MSIX phải có 4 phần, phần cuối là 0 (Store yêu cầu): "4.1" -> 4.1.0.0
$version = (Select-String -Path 'main.py' -Pattern '^APP_VERSION = "(.+)"').Matches[0].Groups[1].Value
$parts = @($version.Split('.')) + @('0', '0', '0')
$msixVersion = ($parts[0..2] -join '.') + '.0'

$identity = & $python -c "import product; print(product.MSIX_IDENTITY_NAME); print(product.MSIX_PUBLISHER); print(product.MSIX_PUBLISHER_DISPLAY)"
if ($LASTEXITCODE -ne 0) { throw 'Cannot read product.py' }

$layout = Join-Path $PSScriptRoot 'build\msix\layout'
if (Test-Path $layout) { Remove-Item $layout -Recurse -Force }
New-Item -ItemType Directory -Force $layout | Out-Null
Copy-Item (Join-Path $source '*') $layout -Recurse
& $python (Join-Path $PSScriptRoot 'build_tools\make_msix_assets.py') (Join-Path $layout 'Assets')
if ($LASTEXITCODE -ne 0) { throw 'Cannot create MSIX assets' }
Copy-Item 'THIRD_PARTY_NOTICES.txt' $layout -ErrorAction SilentlyContinue

$manifest = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'packaging\AppxManifest.template.xml'))
$manifest = $manifest.Replace('{{IDENTITY_NAME}}', $identity[0]).Replace('{{PUBLISHER}}', $identity[1])
$manifest = $manifest.Replace('{{PUBLISHER_DISPLAY}}', $identity[2]).Replace('{{VERSION}}', $msixVersion)
[IO.File]::WriteAllText((Join-Path $layout 'AppxManifest.xml'), $manifest, (New-Object Text.UTF8Encoding $false))

$output = Join-Path $PSScriptRoot "dist\BluetoothMonitor_$msixVersion`_x64.msix"
& $makeappx.FullName pack /o /d $layout /p $output
if ($LASTEXITCODE -ne 0) { throw 'makeappx failed' }
Write-Host "MSIX: $output"
