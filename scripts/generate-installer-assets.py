#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate-installer-assets.py — FrameWeave 安装包品牌资产生成器
================================================================
基于 web/src/Logo.tsx 的「流光取景框」SVG 定义（取景框 + 播放三角 + 帧线 +
青角高光，紫青渐变，深空底），用 Pillow 栅格化出：

  desktop/build/icon.ico               应用/安装器图标（16/24/32/48/64/128/256）
  desktop/build/icon.png               512 master 参考图
  desktop/build/installerHeader.bmp    150x57  NSIS 头部横幅（MUI_HEADERIMAGE_BITMAP）
  desktop/build/installerSidebar.bmp   164x314 NSIS 欢迎/完成页侧栏（MUI_WELCOMEFINISHPAGE_BITMAP）
  desktop/build/uninstallerSidebar.bmp 164x314 卸载页侧栏（MUI_UNWELCOMEFINISHPAGE_BITMAP）
  desktop/build/installerHeaderIcon.ico         one-click / SpiderBanner 头图标
  desktop/build/uninstallerIcon.ico            卸载器图标（electron-builder 默认文件名）

用法: backend/.venv/Scripts/python.exe scripts/generate-installer-assets.py
依赖: Pillow（仅此一个，无需 cairosvg）。
"""
import math
import os

from PIL import Image, ImageDraw, ImageFilter

# ---------------------------------------------------------------- 常量
SS = 6                                    # 超采样倍率（48 视窗 → 288 画布）
GRAD_A, GRAD_M, GRAD_B = (0xA5, 0xB4, 0xFF), (0x7C, 0x8C, 0xFF), (0x22, 0xD3, 0xEE)
CYAN = (0x22, 0xD3, 0xEE)
BG_CENTER = (0x12, 0x17, 0x36)
BG_EDGE = (0x03, 0x04, 0x0C)

def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))

def clamp01(v):
    return 0.0 if v < 0 else (1.0 if v > 1 else v)

def grad_color(t):
    t = clamp01(t)
    if t <= 0.55:
        return lerp(GRAD_A, GRAD_M, t / 0.55)
    return lerp(GRAD_M, GRAD_B, (t - 0.55) / 0.45)

V = 48.0
P1 = (6.0, 5.0)
P2 = (43.0, 43.0)

def grad_t(x, y):
    dx, dy = P2[0] - P1[0], P2[1] - P1[1]
    return ((x - P1[0]) * dx + (y - P1[1]) * dy) / (dx * dx + dy * dy)

# ---------------------------------------------------------------- 工具
def canvas(w, h):
    return Image.new("RGBA", (w, h), (0, 0, 0, 0))

def mask_canvas(w, h):
    return Image.new("L", (w, h), 0)

def fill_grad_from_mask(mask, img, ss):
    """把 mask(灰度或RGBA, 取 alpha) 形状用视窗坐标渐变填充到 img(RGBA)"""
    w, h = img.size
    px = img.load()
    mpx = mask.load()
    for yy in range(h):
        for xx in range(w):
            a = mpx[xx, yy]
            if isinstance(a, tuple):
                a = a[3]
            if a == 0:
                continue
            t = grad_t(xx / ss, yy / ss)
            c = grad_color(t)
            px[xx, yy] = (c[0], c[1], c[2], a)

def make_frame_ring(ss):
    """流光取景框外框 mask：圆角矩形 − 右上斜切 − (腐蚀内缩 = 均匀描边)。
       采用「外轮廓 − 腐蚀(外轮廓)」得到恒定 2.8 单位描边，斜切角自动平行内缩。"""
    w = int(V * ss)
    outer = mask_canvas(w, w)
    d = ImageDraw.Draw(outer)
    d.rounded_rectangle([5.5 * ss, 5.5 * ss, 42.5 * ss, 42.5 * ss], radius=7.5 * ss, fill=255)
    # 右上斜切：切掉斜线 (31.7,5.5)->(42.5,16) 延长线上方区域
    cham_slope = (16.0 - 5.5) / (42.5 - 31.7)          # 0.9722
    y_at_48 = 5.5 + cham_slope * (48.0 - 31.7)
    d.polygon([(31.7 * ss, 0), (48 * ss, 0), (48 * ss, y_at_48 * ss), (31.7 * ss, 5.5 * ss)], fill=0)
    # 腐蚀：外轮廓内缩 2.8 单位 → 内轮廓（自动保留斜切角）；kernel 半径 = 2.8*ss 像素
    r = int(round(2.8 * ss))
    k = 2 * r + 1
    inner = outer.filter(ImageFilter.MinFilter(k))
    ring = outer.copy()
    rpx = ring.load()
    ipx = inner.load()
    for yy in range(w):
        for xx in range(w):
            if ipx[xx, yy] > 0:
                rpx[xx, yy] = 0
    return ring

def make_logo(ss, include_bg=True):
    """渲染完整流光取景框 48 视窗 → RGBA (48*ss)²"""
    w = h = int(V * ss)
    img = canvas(w, h)

    if include_bg:
        # 深空背景：中心微亮 → 边缘压暗
        bg = canvas(w, h)
        db = ImageDraw.Draw(bg)
        cx, cy, rmax = 24.0 * ss, 20.0 * ss, 30.0 * ss
        for yy in range(h):
            for xx in range(w):
                r = math.hypot(xx - cx, yy - cy) / rmax
                t = clamp01(r)
                c = lerp(BG_CENTER, BG_EDGE, t * t)
                bg.putpixel((xx, yy), (c[0], c[1], c[2], 255))
        # 星云光斑（紫 / 青）
        neb = canvas(w, h)
        for (ncx, ncy, nr, col, st) in [
            (14.5 * ss, 13.0 * ss, 26.0 * ss, GRAD_M, 46),
            (37.0 * ss, 36.5 * ss, 30.0 * ss, CYAN, 42),
        ]:
            for yy in range(h):
                for xx in range(w):
                    r = math.hypot(xx - ncx, yy - ncy) / nr
                    if r < 1:
                        a = int(st * (1 - r) * (1 - r))
                        if a > 0:
                            old = neb.getpixel((xx, yy))
                            neb.putpixel((xx, yy), (col[0], col[1], col[2], max(old[3], a)))
        bg.alpha_composite(neb)
        img.alpha_composite(bg)

    # 取景框：渐变描边 + 双层发光
    ring = make_frame_ring(ss)
    ring_img = canvas(w, h)
    fill_grad_from_mask(ring, ring_img, ss)
    for (blur, alpha) in [(2.6 * ss, 0.38), (1.1 * ss, 0.55)]:
        glow = ring.filter(ImageFilter.GaussianBlur(blur))
        layer = canvas(w, h)
        ld = ImageDraw.Draw(layer)
        for yy in range(h):
            for xx in range(w):
                a = glow.getpixel((xx, yy))
                if a > 0:
                    c = grad_color(grad_t(xx / ss, yy / ss))
                    ld.point((xx, yy), fill=(c[0], c[1], c[2], int(a * alpha)))
        img.alpha_composite(layer)
    img.alpha_composite(ring_img)

    # 播放三角（渐变填充 + 微光）
    tri_mask = mask_canvas(w, h)
    tm = ImageDraw.Draw(tri_mask)
    tri_pts = [(19 * ss, 17 * ss), (32 * ss, 24 * ss), (19 * ss, 31 * ss)]
    tm.polygon(tri_pts, fill=255)
    tri = canvas(w, h)
    fill_grad_from_mask(tri_mask, tri, ss)
    tg = tri_mask.filter(ImageFilter.GaussianBlur(1.0 * ss))
    layer = canvas(w, h)
    ld = ImageDraw.Draw(layer)
    for yy in range(h):
        for xx in range(w):
            a = tg.getpixel((xx, yy))
            if a > 0:
                c = grad_color(grad_t(xx / ss, yy / ss))
                ld.point((xx, yy), fill=(c[0], c[1], c[2], int(a * 0.45)))
    img.alpha_composite(layer)
    img.alpha_composite(tri)

    # 底部帧线（渐变）
    for (x0, x1) in [(11.5, 21.5), (25.5, 34.5)]:
        seg = mask_canvas(w, h)
        sd = ImageDraw.Draw(seg)
        sd.line([x0 * ss, 36.5 * ss, x1 * ss, 36.5 * ss], fill=255, width=max(1, int(2.4 * ss)))
        seg_img = canvas(w, h)
        fill_grad_from_mask(seg, seg_img, ss)
        img.alpha_composite(seg_img)

    # 青角高光：折角 + 圆点
    corner = canvas(w, h)
    cd = ImageDraw.Draw(corner)
    cd.line([32 * ss, 9.5 * ss, 32 * ss, 16 * ss], fill=CYAN + (200,), width=max(1, int(1.8 * ss)))
    cd.line([32 * ss, 16 * ss, 38.5 * ss, 16 * ss], fill=CYAN + (200,), width=max(1, int(1.8 * ss)))
    cd.ellipse([(40.5 - 1.4) * ss, (7.5 - 1.4) * ss, (40.5 + 1.4) * ss, (7.5 + 1.4) * ss], fill=CYAN + (255,))
    img.alpha_composite(corner)

    return img

def downscale(img, size):
    return img.resize((size, size), Image.LANCZOS)

def save_ico(path, master, sizes):
    master.save(path, format="ICO", sizes=[(s, s) for s in sizes])
    print(f"[ok] {os.path.relpath(path)}: {len(sizes)} 尺寸 {sizes}")

def save_bmp(path, img, size):
    rgb = img.convert("RGB").resize(size, Image.LANCZOS)
    rgb.save(path, format="BMP")
    print(f"[ok] {os.path.relpath(path)}: {size[0]}x{size[1]} 24bpp")

# ---------------------------------------------------------------- 主流程
def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    outdir = os.path.join(root, "desktop", "build")
    os.makedirs(outdir, exist_ok=True)

    logo256 = downscale(make_logo(SS, include_bg=True), 256)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    save_ico(os.path.join(outdir, "icon.ico"), logo256, sizes)
    save_ico(os.path.join(outdir, "installerIcon.ico"), logo256, sizes)
    save_ico(os.path.join(outdir, "uninstallerIcon.ico"), logo256, sizes)
    save_ico(os.path.join(outdir, "installerHeaderIcon.ico"), logo256, sizes)
    downscale(make_logo(SS, include_bg=True), 512).save(os.path.join(outdir, "icon.png"), format="PNG")
    print(f"[ok] {os.path.relpath(os.path.join(outdir, 'icon.png'))}: 512x512 PNG")

    # NSIS 头部横幅 150x57：左白（标题文字区）+ 右深空 + 小 logo
    header = Image.new("RGBA", (150, 57), (0, 0, 0, 0))
    base = Image.new("RGB", (150, 57), (255, 255, 255))
    hd = ImageDraw.Draw(base)
    for xx in range(150):
        t = clamp01((xx - 74) / 76.0)
        c = (245, 245, 250) if t <= 0 else lerp((0x0B, 0x0E, 0x1E), (0x1E, 0x26, 0x52), t)
        hd.line([xx, 0, xx, 57], fill=c)
    neb = Image.new("RGBA", (150, 57), (0, 0, 0, 0))
    nd = ImageDraw.Draw(neb)
    for (cx, cy, r, col, st) in [(128, 30, 46, GRAD_M, 60), (142, 12, 40, CYAN, 50)]:
        for yy in range(57):
            for xx in range(150):
                dist = math.hypot(xx - cx, yy - cy) / r
                if dist < 1:
                    a = int(st * (1 - dist) ** 2)
                    if a > 0:
                        old = neb.getpixel((xx, yy))
                        neb.putpixel((xx, yy), (col[0], col[1], col[2], max(old[3], a)))
    base = Image.alpha_composite(base.convert("RGBA"), neb)
    logo = downscale(make_logo(SS, include_bg=False), 44)
    base.alpha_composite(logo, (150 - 44 - 10, (57 - 44) // 2))
    save_bmp(os.path.join(outdir, "installerHeader.bmp"), base, (150, 57))

    # 侧栏 164x314（欢迎/完成页）
    sb = Image.new("RGBA", (164, 314), (0, 0, 0, 0))
    sbase = Image.new("RGB", (164, 314), BG_EDGE)
    bd = ImageDraw.Draw(sbase)
    for yy in range(314):
        t = clamp01(yy / 314.0)
        c = lerp(BG_CENTER, BG_EDGE, t * t)
        bd.line([0, yy, 163, yy], fill=c)
    neb2 = Image.new("RGBA", (164, 314), (0, 0, 0, 0))
    nd2 = ImageDraw.Draw(neb2)
    for (cx, cy, r, col, st) in [(52, 66, 96, GRAD_M, 64), (128, 250, 110, CYAN, 56)]:
        for yy in range(314):
            for xx in range(164):
                dist = math.hypot(xx - cx, yy - cy) / r
                if dist < 1:
                    a = int(st * (1 - dist) ** 2)
                    if a > 0:
                        old = neb2.getpixel((xx, yy))
                        neb2.putpixel((xx, yy), (col[0], col[1], col[2], max(old[3], a)))
    for yy in range(278, 314):
        t = (yy - 278) / 36.0
        c = lerp(GRAD_M, GRAD_B, t)
        bd.line([0, yy, 163, yy], fill=c)
    sb = Image.alpha_composite(sbase.convert("RGBA"), neb2)
    logo = downscale(make_logo(SS, include_bg=False), 108)
    sb.alpha_composite(logo, ((164 - 108) // 2, 78))
    save_bmp(os.path.join(outdir, "installerSidebar.bmp"), sb, (164, 314))
    save_bmp(os.path.join(outdir, "uninstallerSidebar.bmp"), sb, (164, 314))

    print("\n[complete] 品牌资产已生成至 desktop/build/")

if __name__ == "__main__":
    main()