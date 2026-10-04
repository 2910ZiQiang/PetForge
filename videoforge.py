#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PetForge · 视频版 —— 把 AI 生成的绿幕动作视频转成 dsh-pet 的 VP9-Alpha 透明动画

与静图版（forge.py）的区别：
  * 动作用的是真实视频，不是程序化模板 → 呼吸/挥手/跳跃都是真的
  * 用 numpy 做抠像与去绿溢（比纯 Python 快两个数量级）
  * 以每个视频的**第一帧**做标定（官方提示词要求首帧必须是标准正面站姿），
    再把同一变换套用到该视频所有帧 → 帧间不抖，视频间大小一致

用法：
  python videoforge.py                      # 转换 input/videos/ 下所有视频
  python videoforge.py --only 待机呼吸休闲    # 只转一个
  python videoforge.py --preview            # 只导一帧预览，不编码
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import forge  # noqa: E402  复用 ffmpeg 路径与 JSONC 处理

FFMPEG = forge.FFMPEG
HERE = Path(__file__).resolve().parent
VIDEO_DIR = HERE / "input" / "videos"
WORK = HERE / "work_video"
PACK_ANIM = HERE / "build" / "mature" / "pet" / "mature-animation"

CANVAS = (960, 540)     # 比 640x360 高一档：屏幕显示时是「缩小」而不是「放大」，所以更锐
TARGET_H = 495          # 角色在画布里的高度（= 540 × 0.9167）
BOTTOM = 522            # 脚底在画布里的 y
KEY = np.array([0, 255, 0], dtype=np.float32)
TOL = 70.0              # 色距小于此值 → 全透明
FEATHER = 26.0          # 到 TOL+FEATHER 之间 → 半透明过渡
EXTRACT_W = 1280        # 中间帧不降采样，保住源视频的全部细节
SHARPEN = (1.1, 55, 2)  # 反锐化蒙版 (半径, 强度%, 阈值) —— 补偿 AI 视频的软
CRF = 28
FPS = 24
ERODE = 2               # 收边像素数（分辨率越高，等效值要越大）

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi"}


def sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise SystemExit("命令失败:\n  " + " ".join(str(c) for c in cmd) + "\n" + r.stderr[-1200:])
    return r


def probe(path):
    """返回 (宽, 高, 时长秒)"""
    r = subprocess.run([str(FFMPEG), "-hide_banner", "-i", str(path)],
                       capture_output=True, text=True)
    txt = r.stderr
    import re
    m = re.search(r"Video:.*?,\s*(\d+)x(\d+)", txt)
    d = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", txt)
    w, h = (int(m.group(1)), int(m.group(2))) if m else (1280, 720)
    secs = 10.0
    if d:
        secs = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3))
    return w, h, secs


def extract(path, outdir):
    """解码成 PNG 帧（降采样，24fps，最多 10 秒）"""
    if outdir.exists():
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    w, h, secs = probe(path)
    nh = max(2, int(round(EXTRACT_W * h / w / 2) * 2))
    dur = min(secs, 10.0)
    sh([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(path), "-t", f"{dur:.2f}",
        "-vf", f"fps={FPS},scale={EXTRACT_W}:{nh}:flags=bilinear",
        str(outdir / "%04d.png")])
    return sorted(outdir.glob("*.png"))


def detect_key(rgb: np.ndarray):
    """从四角采样背景色（AI 生成的绿未必是纯 #00FF00）"""
    h, w, _ = rgb.shape
    pts = [rgb[2, 2], rgb[2, w - 3], rgb[h - 3, 2], rgb[h - 3, w - 3]]
    return np.median(np.stack(pts).astype(np.float32), axis=0)


def erode_alpha(a: np.ndarray, rounds=1):
    """alpha 向内收，切掉最外圈残留（去绿边的关键）"""
    for _ in range(rounds):
        p = np.pad(a, 1, mode="constant", constant_values=0)
        a = np.minimum.reduce([
            p[0:-2, 0:-2], p[0:-2, 1:-1], p[0:-2, 2:],
            p[1:-1, 0:-2], p[1:-1, 1:-1], p[1:-1, 2:],
            p[2:, 0:-2], p[2:, 1:-1], p[2:, 2:],
        ])
    return a


def bleed_colors(rgb: np.ndarray, alpha: np.ndarray, rounds=2):
    """把不透明像素的颜色向外扩散到半透明像素，消除绿边脏色"""
    out = rgb.copy()
    solid = alpha > 235
    for _ in range(rounds):
        acc = np.zeros_like(out)
        cnt = np.zeros(alpha.shape, dtype=np.float32)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                sh = np.roll(np.roll(out, dy, 0), dx, 1)
                m = np.roll(np.roll(solid, dy, 0), dx, 1).astype(np.float32)
                acc += sh * m[:, :, None]
                cnt += m
        need = (~solid) & (alpha > 0) & (cnt > 0)
        if not need.any():
            break
        out[need] = acc[need] / cnt[need][:, None]
        solid = solid | need
    return out


def key_frame(rgb: np.ndarray, key=None, tol=TOL, feather=FEATHER,
              erode=ERODE, bleed=2, erase=(0.86, 0.86, 1.0, 1.0)):
    """绿幕抠像 + 去绿溢 + 收边 + 颜色外扩，返回 RGBA uint8

    erase: 按比例清除右下角水印的框 (x0, y0, x1, y1)，传 None 关闭。
    """
    k = detect_key(rgb) if key is None else np.asarray(key, dtype=np.float32)
    f = rgb.astype(np.float32)
    d = np.sqrt(((f - k) ** 2).sum(axis=2))
    alpha = np.clip((d - tol) / feather, 0.0, 1.0)

    # 去绿溢：绿通道压到不超过 R/B 最大值
    f[:, :, 1] = np.minimum(f[:, :, 1], np.maximum(f[:, :, 0], f[:, :, 2]))

    a255 = alpha * 255.0
    if erase:
        h, w = a255.shape
        x0, y0, x1, y1 = erase
        a255[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)] = 0.0
    if erode:
        a255 = erode_alpha(a255, erode)
    if bleed:
        f = bleed_colors(f, a255, bleed)

    return np.dstack([f, a255]).clip(0, 255).astype(np.uint8)


def bbox_of(rgba: np.ndarray, thr=24):
    a = rgba[:, :, 3]
    ys, xs = np.where(a > thr)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def convert_one(name, src, preview_only=False):
    t0 = time.time()
    frames = extract(src, WORK / name)
    if not frames:
        raise SystemExit(f"{name}: 没解出帧")

    # 标定：用第一帧（官方要求首帧 = 标准正面站姿）
    first = np.asarray(Image.open(frames[0]).convert("RGB"))
    rgba0 = key_frame(first)
    bb = bbox_of(rgba0)
    if bb is None:
        raise SystemExit(f"{name}: 第一帧抠完是全透明，检查绿幕或容差")
    x0, y0, x1, y1 = bb
    ch = y1 - y0
    scale = TARGET_H / float(ch)
    # 以包围盒中心为水平基准，脚底对齐 BOTTOM
    cx = (x0 + x1) / 2.0
    crop_w = CANVAS[0] / scale
    crop_h = CANVAS[1] / scale
    left = cx - crop_w / 2.0
    top = y1 - crop_h * (BOTTOM / float(CANVAS[1]))

    print(f"      标定: 包围盒 {x1-x0}x{ch}  缩放 {scale:.3f}  裁剪原点 ({left:.0f},{top:.0f})")

    outdir = WORK / f"{name}__out"
    if outdir.exists():
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    W, H = Image.open(frames[0]).size
    for i, fp in enumerate(frames):
        rgb = np.asarray(Image.open(fp).convert("RGB"))
        rgba = key_frame(rgb)
        img = Image.fromarray(rgba, "RGBA")
        # 用仿射把裁剪区映射到画布（一次重采样，不糊）
        a = scale
        img = img.transform(
            CANVAS, Image.AFFINE,
            (1.0 / a, 0.0, left, 0.0, 1.0 / a, top),
            resample=Image.BICUBIC)
        if SHARPEN:
            img = img.filter(ImageFilter.UnsharpMask(
                radius=SHARPEN[0], percent=SHARPEN[1], threshold=SHARPEN[2]))
        img.save(outdir / f"{i:04d}.png")
        if preview_only and i >= 2:
            break

    if preview_only:
        # 拼一张预览：第一帧贴棋盘格
        first_out = Image.open(outdir / "0000.png")
        import prep
        prep.preview(first_out, HERE / "build" / f"preview-{name}.png")
        print(f"      预览: build/preview-{name}.png  ({time.time()-t0:.0f}s)")
        return None

    out_webm = PACK_ANIM / f"{name}.webm"
    sh([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
        "-framerate", str(FPS), "-i", str(outdir / "%04d.png"),
        "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
        "-b:v", "0", "-crf", str(CRF), "-auto-alt-ref", "0",
        "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1",
        str(out_webm)])
    shutil.rmtree(outdir, ignore_errors=True)
    shutil.rmtree(WORK / name, ignore_errors=True)
    kb = out_webm.stat().st_size / 1024
    print(f"      -> {name}.webm  {kb:.0f}KB  共 {len(frames)} 帧  {time.time()-t0:.0f}s")
    return name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()

    if not PACK_ANIM.exists():
        raise SystemExit(f"找不到宠物包动画目录：{PACK_ANIM}\n先跑 forge.py 生成基础包")

    vids = sorted(p for p in VIDEO_DIR.glob("*") if p.suffix.lower() in VIDEO_EXT)
    if not vids:
        print(f"{VIDEO_DIR} 里还没有视频。")
        print("把 AI 生成的绿幕视频按动作名放进去（例：待机呼吸休闲.mp4），再跑一次。")
        return

    if args.only:
        want = {x.strip() for x in args.only.split(",") if x.strip()}
        vids = [v for v in vids if v.stem in want]

    print(f"待处理 {len(vids)} 个视频\n")
    done = []
    for v in vids:
        print(f"[{v.stem}]")
        try:
            r = convert_one(v.stem, v, preview_only=args.preview)
            if r:
                done.append(r)
        except SystemExit as e:
            print(f"      跳过: {e}")
        print()

    if done and not args.preview:
        print("已替换动画:", ", ".join(done))
        print("\n安装并生效：")
        print("  1) 复制宠物包到 ~/.dsh/dsh-pet/pet/")
        print("  2) POST http://127.0.0.1:19387/dsh-pet-7340/reload")


if __name__ == "__main__":
    main()
