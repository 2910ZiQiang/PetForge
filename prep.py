#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PetForge · 预处理：绿幕抠图 + 去绿溢 + 清边
被 forge.py 调用，也可单独跑来做抠图预检。
"""

import math
from PIL import Image, ImageFilter


def remove_solid_background(img, key_hex, tol=40, feather=10):
    """按距离把接近 key_hex 的像素抠掉；feather 区间做半透明过渡。"""
    img = img.convert("RGBA")
    key = tuple(int(key_hex[i:i + 2], 16) for i in (0, 2, 4))
    px = img.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            d = math.sqrt((r - key[0]) ** 2 + (g - key[1]) ** 2 + (b - key[2]) ** 2)
            if d <= tol:
                px[x, y] = (r, g, b, 0)
            elif d <= tol + feather:
                px[x, y] = (r, g, b, int(a * (d - tol) / float(feather)))
    return img


def despill(img, key_channel="g", amount=1.0):
    """
    去绿溢：绿幕拍摄/生成时，边缘半透明像素会把绿色带进角色。
    做法：把绿通道压到不超过 R/B 的最大值（角色本身不含纯绿，所以安全）。
    amount=1.0 表示完全压制。
    """
    img = img.convert("RGBA")
    px = img.load()
    w, h = img.size
    idx = {"r": 0, "g": 1, "b": 2}[key_channel]
    for y in range(h):
        for x in range(w):
            c = list(px[x, y])
            a = c[3]
            if a == 0:
                continue
            others = [c[i] for i in range(3) if i != idx]
            m = max(others)
            if c[idx] > m:
                c[idx] = int(round(m + (c[idx] - m) * (1.0 - amount)))
                px[x, y] = tuple(c)
    return img


def bleed_edges(img, rounds=2, opaque_min=235):
    """
    清边：把半透明像素的颜色替换为邻近不透明像素的颜色，
    消除绿幕残色 / 深色描边造成的脏边。
    """
    img = img.convert("RGBA")
    w, h = img.size
    for _ in range(rounds):
        src = list(img.getdata())
        px = img.load()
        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                if a == 0 or a >= opaque_min:
                    continue
                acc = [0, 0, 0]
                cnt = 0
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < w and 0 <= ny < h:
                            nr, ng, nb, na = src[ny * w + nx]
                            if na >= opaque_min:
                                acc[0] += nr
                                acc[1] += ng
                                acc[2] += nb
                                cnt += 1
                if cnt:
                    px[x, y] = (acc[0] // cnt, acc[1] // cnt, acc[2] // cnt, a)
    return img


def erode_alpha(img, radius=1):
    """把 alpha 向内收 1px，切掉最外圈残留。对细节多的图慎用。"""
    img = img.convert("RGBA")
    a = img.getchannel("A").filter(ImageFilter.MinFilter(radius * 2 + 1))
    img.putalpha(a)
    return img


def matte(src_path, key_hex="00FF00", tol=40, feather=10,
          erase_boxes=(), despill_amount=1.0, bleed_rounds=2, erode=0):
    """完整抠图流水线：可选区域清除 → 抠色 → 去绿溢 → 清边 → 收边。"""
    img = Image.open(src_path).convert("RGBA")
    w, h = img.size

    for bx in erase_boxes:
        x0, y0, x1, y1 = [int(round(v)) for v in bx]
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, x1), min(h, y1)
        px = img.load()
        for y in range(y0, y1):
            for x in range(x0, x1):
                px[x, y] = (0, 0, 0, 0)

    if key_hex:
        img = remove_solid_background(img, key_hex, tol=tol, feather=feather)
        img = despill(img, "g", amount=despill_amount)
        img = bleed_edges(img, rounds=bleed_rounds)
    if erode:
        img = erode_alpha(img, erode)
    return img


def checker_bg(size, s=16):
    c = Image.new("RGB", size, (255, 255, 255))
    p = c.load()
    for y in range(size[1]):
        for x in range(size[0]):
            if ((x // s) + (y // s)) % 2:
                p[x, y] = (205, 205, 205)
    return c


def preview(img, out_path, max_size=(760, 1140)):
    ch = checker_bg(img.size)
    ch.paste(img, (0, 0), img)
    ch.thumbnail(max_size)
    ch.save(out_path)
    return out_path


if __name__ == "__main__":
    import sys
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "matte-preview.png"
    m = matte(src)
    bb = m.getchannel("A").getbbox()
    print("bbox:", bb)
    preview(m, out)
    print("preview:", out)
