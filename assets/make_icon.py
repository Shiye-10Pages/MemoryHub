#!/usr/bin/env python3
"""生成 MemoryHub 应用图标(十页AI 品牌:深/浅两版 1024px PNG)。

设计:圆角方形(macOS 比例)+ 白/墨「十页」+ 一道靛蓝强调下划线。极简黑白 + 单一强调色。
依赖 Pillow;需系统中文字体(macOS 自带 STHeiti / PingFang)。
生成后可用 sips + iconutil 打包 .icns:
  见仓库 README「启动 · 图标」一节,或直接:
  for … sips -z …；iconutil -c icns MemoryHub.iconset -o MemoryHub.icns
用法: python3 assets/make_icon.py
"""
import os

from PIL import Image, ImageDraw, ImageFont

OUT = os.path.dirname(os.path.abspath(__file__))
S = 1024
RAD = int(S * 0.2237)             # macOS 圆角比例
ACCENT = (91, 80, 230, 255)       # #5B50E6

CANDS = [
    ("/System/Library/Fonts/PingFang.ttc", range(0, 12)),
    ("/System/Library/Fonts/STHeiti Medium.ttc", range(0, 4)),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", range(0, 4)),
]


def pick_font(size):
    best = None
    for path, idxs in CANDS:
        if not os.path.exists(path):
            continue
        for i in idxs:
            try:
                f = ImageFont.truetype(path, size, index=i)
            except Exception:
                continue
            name = " ".join(f.getname()).lower()
            score = ("semibold" in name) * 3 + ("medium" in name) * 2 + ("bold" in name) * 2 + ("heiti" in name)
            if best is None or score > best[0]:
                best = (score, f)
    if not best:
        raise SystemExit("找不到可用中文字体")
    return best[1]


def render(bg, fg, fname):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=RAD, fill=bg)
    txt = "十页"
    f = pick_font(520)
    b = d.textbbox((0, 0), txt, font=f)
    w = b[2] - b[0]
    f = pick_font(max(60, int(520 * int(S * 0.70) / w)))
    b = d.textbbox((0, 0), txt, font=f)
    w, h = b[2] - b[0], b[3] - b[1]
    d.text(((S - w) / 2 - b[0], (S - h) / 2 - b[1] - int(S * 0.045)), txt, font=f, fill=fg)
    uw, uh = int(w * 0.92), max(6, int(S * 0.012))
    ux, uy = (S - uw) // 2, int((S + h) / 2) + int(S * 0.02)
    d.rounded_rectangle([ux, uy, ux + uw, uy + uh], radius=uh // 2, fill=ACCENT)
    img.save(fname)


if __name__ == "__main__":
    render((14, 16, 23, 255), (255, 255, 255, 255), os.path.join(OUT, "icon-dark.png"))
    render((244, 244, 246, 255), (14, 16, 23, 255), os.path.join(OUT, "icon-light.png"))
    print("done: icon-dark.png / icon-light.png @1024")
