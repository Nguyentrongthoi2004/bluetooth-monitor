"""Ghi thông tin phiên bản cho exe (Properties › Details): exe có tên/phiên bản rõ ràng ít bị Defender nghi ngờ hơn."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
version = re.search(r'^APP_VERSION = "(.+)"', (ROOT / 'main.py').read_text(encoding='utf-8'), re.M).group(1)
parts = [int(part) for part in version.split('.')] + [0, 0, 0]
numbers = ', '.join(str(part) for part in parts[:4])
target = Path(sys.argv[1])
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({numbers}), prodvers=({numbers}), mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Bluetooth Monitor'),
      StringStruct('FileDescription', 'Bluetooth Monitor'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'Bluetooth Monitor'),
      StringStruct('OriginalFilename', 'Bluetooth Monitor.exe'),
      StringStruct('ProductName', 'Bluetooth Monitor'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding='utf-8')
print(version)
