"""Tạo các ảnh logo mà gói MSIX/Microsoft Store yêu cầu, từ assets/app_icon.png."""
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SIZES = {  # tên file: (rộng, cao, tỉ lệ icon trong khung)
    'StoreLogo.png': (50, 50, 1.0),
    'Square44x44Logo.png': (44, 44, 1.0),
    'Square44x44Logo.targetsize-44_altform-unplated.png': (44, 44, 1.0),
    'Square150x150Logo.png': (150, 150, 0.66),
    'Wide310x150Logo.png': (310, 150, 0.66),
}


def main(target):
    icon = Image.open(ROOT / 'assets' / 'app_icon.png').convert('RGBA')
    target.mkdir(parents=True, exist_ok=True)
    for name, (width, height, scale) in SIZES.items():
        side = round(min(width, height) * scale)
        canvas = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        canvas.alpha_composite(icon.resize((side, side), Image.LANCZOS), ((width - side) // 2, (height - side) // 2))
        canvas.save(target / name)
    print(len(SIZES), 'assets ->', target)


if __name__ == '__main__':
    main(Path(sys.argv[1]))
