"""Ghi THIRD_PARTY_NOTICES.txt: thư viện đóng gói kèm app, phiên bản, giấy phép và toàn văn giấy phép nếu có."""
import re
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOP_LEVEL = ['customtkinter', 'bleak', 'pystray', 'pillow', 'winrt-runtime']
EXTRA = """\
Python {python} — Python Software Foundation License (https://docs.python.org/3/license.html)
Tcl/Tk (bundled with Python) — Tcl/Tk License (BSD-style) (https://www.tcl.tk/software/tcltk/license.html)
PyInstaller bootloader — GPL-2.0 with the PyInstaller bootloader exception, which permits distributing
  applications built with it under any license (https://pyinstaller.org/en/stable/license.html)
"""


MIT_TEXT = """\
MIT License

Copyright (c) the pywinrt authors and Microsoft Corporation.

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE."""


def requirement_names(dist):
    names = []
    for requirement in dist.requires or []:
        if ';' in requirement and 'platform_system == "Windows"' not in requirement and 'sys_platform == "win32"' not in requirement:
            continue  # phụ thuộc chỉ dành cho hệ điều hành khác / tùy chọn
        names.append(re.split(r'[\s;<>=!~\[(]', requirement, maxsplit=1)[0])
    return names


def collect():
    seen, queue = {}, list(TOP_LEVEL)
    queue += [dist.metadata['Name'] for dist in metadata.distributions()
              if (dist.metadata['Name'] or '').lower().startswith('winrt-windows')]
    while queue:
        name = queue.pop()
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        key = dist.metadata['Name'].lower()
        if key in seen:
            continue
        seen[key] = dist
        queue += requirement_names(dist)
    return sorted(seen.values(), key=lambda dist: dist.metadata['Name'].lower())


def license_text(dist):
    for file in dist.files or []:
        if re.search(r'(LICEN[CS]E|COPYING|NOTICE)', file.name, re.I):
            try:
                return Path(dist.locate_file(file)).read_text(encoding='utf-8', errors='replace').strip()
            except OSError:
                continue
    return None


def main(target):
    lines = ['Bluetooth Monitor — third-party software notices', '=' * 50, '',
             'This application bundles the following open-source components.', '',
             EXTRA.format(python='.'.join(map(str, sys.version_info[:3])))]
    texts = []
    for dist in collect():
        meta = dist.metadata
        license_name = meta.get('License-Expression') or meta.get('License') or ''
        if not license_name or len(license_name) > 80:
            classifiers = [c.split('::')[-1].strip() for c in meta.get_all('Classifier') or [] if c.startswith('License')]
            license_name = ', '.join(classifiers) or 'see license text below'
        url = meta.get('Home-page') or next((value.split(',', 1)[-1].strip() for value in meta.get_all('Project-URL') or []
                                             if 'source' in value.lower() or 'home' in value.lower()), '')
        lines.append(f"{meta['Name']} {dist.version} — {license_name}" + (f" ({url})" if url else ''))
        text = license_text(dist)
        if text:
            texts.append(f"\n{'-' * 70}\n{meta['Name']} {dist.version}\n{'-' * 70}\n{text}\n")
    lines.append('')
    lines.append('pystray is licensed under LGPL-3.0: you may replace it with a modified version. Its source code is '
                 'available at https://github.com/moses-palmer/pystray and is used unmodified.')
    # Gói winrt-* (pywinrt, MIT) không kèm file giấy phép trong bản cài: ghi toàn văn MIT một lần cho cả nhóm.
    texts.append(f"\n{'-' * 70}\nwinrt-runtime and winrt-Windows.* (pywinrt, https://github.com/pywinrt/pywinrt)\n"
                 f"{'-' * 70}\n{MIT_TEXT}\n")
    target.write_text('\n'.join(lines) + '\n' + ''.join(texts), encoding='utf-8')
    print(len(lines), 'lines,', len(texts), 'license texts ->', target)


if __name__ == '__main__':
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'THIRD_PARTY_NOTICES.txt')
