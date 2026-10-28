"""Vẽ lại icon ứng dụng: assets/app_icon.png (256px) và assets/app_icon.ico (16-256px).

Chạy: python build_tools/make_icon.py
"""
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
S = 1024  # vẽ lớn rồi thu nhỏ để cạnh mượt
TOP, BOTTOM = (255, 138, 31), (232, 60, 42)
WHITE = (255, 255, 255, 255)


def gradient():
    # Chuyển màu chéo từ góc trên trái xuống góc dưới phải.
    grad = Image.new("RGBA", (S, S))
    pixels = grad.load()
    for y in range(S):
        for x in range(S):
            t = (x + y) / (2 * S - 2)
            pixels[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)) + (255,)
    return grad


def draw_icon():
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((24, 24, S - 24, S - 24), radius=230, fill=255)
    icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    icon.paste(gradient(), mask=mask)

    d = ImageDraw.Draw(icon)
    # Vòm tai nghe
    d.arc((236, 214, 788, 766), start=180, end=360, fill=WHITE, width=78)
    # Hai củ tai
    d.rounded_rectangle((200, 470, 352, 778), radius=70, fill=WHITE)
    d.rounded_rectangle((672, 470, 824, 778), radius=70, fill=WHITE)
    # Cục pin ở giữa, đầy 3/4
    d.rounded_rectangle((418, 592, 590, 704), radius=26, outline=WHITE, width=26)
    d.rounded_rectangle((592, 628, 618, 668), radius=8, fill=WHITE)
    d.rounded_rectangle((448, 622, 530, 674), radius=10, fill=WHITE)
    return icon


def main():
    icon = draw_icon()
    icon.resize((256, 256), Image.LANCZOS).save(os.path.join(ASSETS, "app_icon.png"))
    icon.save(os.path.join(ASSETS, "app_icon.ico"),
              sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    main()
