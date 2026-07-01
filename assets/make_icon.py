#!/usr/bin/env python3
"""生成 AI 记忆助手(MemoryHub)应用图标:深/浅两版 1024px PNG。

设计:圆角方形(macOS 比例)+ 「MH」字标 + 下方一串相连的记忆节点(知识图谱意象)。
极简黑白 + 单一强调色(靛蓝)。依赖 Pillow;用系统 Latin 粗体。
生成后用 sips + iconutil 打包 .icns(见 README「启动 · 图标」)。
用法: python3 assets/make_icon.py
"""
import os

from PIL import Image, ImageDraw, ImageFont

OUT = os.path.dirname(os.path.abspath(__file__))
S = 1024
RAD = int(S * 0.2237)
ACCENT = (91, 80, 230, 255)       # #5B50E6

FONTS = [
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Avenir Next.ttc",
    "/Library/Fonts/Arial Bold.ttf",
]


def pick_font(size):
    best = None
    for path in FONTS:
        if not os.path.exists(path):
            continue
        for i in range(0, 14):
            try:
                f = ImageFont.truetype(path, size, index=i)
            except Exception:
                break
            name = " ".join(f.getname()).lower()
            score = ("bold" in name) * 3 + ("heavy" in name) * 2 + ("helvetica" in name)
            if best is None or score > best[0]:
                best = (score, f)
    if not best:
        raise SystemExit("找不到可用 Latin 字体")
    return best[1]


def render(bg, fg, fname):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=RAD, fill=bg)
    txt = "MH"
    f = pick_font(520)
    b = d.textbbox((0, 0), txt, font=f)
    f = pick_font(max(60, int(520 * int(S * 0.56) / (b[2] - b[0]))))
    b = d.textbbox((0, 0), txt, font=f)
    w, h = b[2] - b[0], b[3] - b[1]
    d.text(((S - w) / 2 - b[0], (S - h) / 2 - b[1] - int(S * 0.085)), txt, font=f, fill=fg)
    # 记忆节点链:•—•—•
    cy = int(S * 0.735)
    xs = [int(S * 0.34), int(S * 0.50), int(S * 0.66)]
    r = int(S * 0.028)
    lw = int(S * 0.016)
    d.line([(xs[0], cy), (xs[2], cy)], fill=ACCENT, width=lw)
    for x in xs:
        d.ellipse([x - r, cy - r, x + r, cy + r], fill=ACCENT)
    img.save(fname)


if __name__ == "__main__":
    render((14, 16, 23, 255), (255, 255, 255, 255), os.path.join(OUT, "icon-dark.png"))
    render((244, 244, 246, 255), (14, 16, 23, 255), os.path.join(OUT, "icon-light.png"))
    print("done: icon-dark.png / icon-light.png @1024")
