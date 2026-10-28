"""Chạy test: mỗi file test một tiến trình riêng (chạy chung một tiến trình bị treo vì Tk/WinRT).

    python run_tests.py                     # tất cả
    python run_tests.py test_security ...   # vài file
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TESTS = ROOT / 'tests'


def main(names):
    modules = names or sorted(path.stem for path in TESTS.glob('test_*.py'))
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'), PYTHONIOENCODING='utf-8')
    total, failed, started = 0, [], time.monotonic()
    for module in modules:
        result = subprocess.run([sys.executable, '-m', 'unittest', module], cwd=TESTS, env=env,
                                capture_output=True, text=True, encoding='utf-8', errors='replace')
        output = result.stdout + result.stderr
        match = re.search(r'Ran (\d+) tests?', output)
        count = int(match.group(1)) if match else 0
        total += count
        ok = result.returncode == 0
        print(f"{'OK  ' if ok else 'FAIL'} {module:<34} {count:>3} tests")
        if not ok:
            failed.append(module)
            print(output[-3000:])
    print(f"\n{total} tests in {len(modules)} files, {time.monotonic() - started:.0f}s — "
          + (f"FAILED: {', '.join(failed)}" if failed else 'all passed'))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
