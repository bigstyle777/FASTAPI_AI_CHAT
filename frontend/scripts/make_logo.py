# -*- coding: utf-8 -*-
"""把黄色卡通形象图片处理成软件 logo：
1. 检测奶油色圆形背景的边界（利用 R-B 色差，白色/灰色水印不会被误判）
2. 以圆形裁剪，圆外区域转为透明（4x 超采样抗锯齿）
3. 输出 logo.png（512x512）与 favicon.ico（16/32/48）
"""
from pathlib import Path

from PIL import Image, ImageDraw

SRC = Path(r"C:/Users/bigstyle/Desktop/ad1f22bc9e2a91002861104ce31ea894.jpg")
OUT_DIR = Path(r"C:/Users/bigstyle/AIChatPro/frontend/public")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def find_circle(img: Image.Image) -> tuple[int, int, int]:
    """返回 (cx, cy, r)：奶油色圆盘的圆心和半径。"""
    small = img.convert("RGB").resize((256, 256))
    px = small.load()
    w, h = small.size
    xs, ys = [], []
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            # 奶油色：R、G 高且 R 明显大于 B；白色和灰色水印各通道接近，被排除
            if r > 235 and g > 225 and r - b > 10:
                xs.append(x)
                ys.append(y)
    if not xs:
        raise RuntimeError("未检测到奶油色圆形区域")
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    scale = img.width / small.width
    cx = int((x0 + x1) / 2 * scale)
    cy = int((y0 + y1) / 2 * scale)
    r = int(min(x1 - x0, y1 - y0) / 2 * scale)
    return cx, cy, r


def main() -> None:
    img = Image.open(SRC).convert("RGB")
    cx, cy, r = find_circle(img)
    print(f"原图 {img.size}, 圆心=({cx},{cy}), 半径={r}")

    # 稍微收缩半径，避免边缘混入白色背景
    r = int(r * 0.985)

    # 4x 超采样绘制圆形蒙版，再缩小获得平滑边缘
    ss = 4
    size = 2 * r
    mask_big = Image.new("L", (size * ss, size * ss), 0)
    draw = ImageDraw.Draw(mask_big)
    draw.ellipse((0, 0, size * ss - 1, size * ss - 1), fill=255)
    mask = mask_big.resize((size, size), Image.LANCZOS)

    # 裁剪出圆盘区域并应用蒙版
    box = (cx - r, cy - r, cx + r, cy + r)
    disc = img.crop(box)
    logo = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    logo.paste(disc, (0, 0), mask)

    # 输出 512x512 logo
    logo_512 = logo.resize((512, 512), Image.LANCZOS)
    logo_path = OUT_DIR / "logo.png"
    logo_512.save(logo_path, "PNG")
    print(f"logo 已保存: {logo_path}")

    # favicon（白色底，避免旧浏览器对透明的兼容问题）
    fav = Image.new("RGBA", (48, 48), (255, 255, 255, 255))
    fav_48 = logo.resize((48, 48), Image.LANCZOS)
    fav.paste(fav_48, (0, 0), fav_48)
    fav_path = OUT_DIR / "favicon.ico"
    fav.save(fav_path, "ICO", sizes=[(16, 16), (32, 32), (48, 48)])
    print(f"favicon 已保存: {fav_path}")


if __name__ == "__main__":
    main()
