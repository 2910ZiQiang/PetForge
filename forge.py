#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PetForge —— 把一张角色图做成 dsh-pet 能用的【透明动画宠物包】

输入：一张 RGBA 透明底 PNG；或纯色背景图（用 --key 抠掉背景色）
输出：一个宠物包（配置 + VP9-Alpha 透明 webm），放进 ~/.dsh/dsh-pet/pet/ 即可生效

关键坑（已实测）：
  * 解码必须显式 -c:v libvpx-vp9，否则透明层全丢（alpha 全 255 → 黑底）
  * 编码用 -pix_fmt yuva420p + -auto-alt-ref 0，否则 alpha 不保
  * 本机只有 4 核、ffmpeg 是 2018 老版本，VP9-Alpha 约 0.4 秒/帧
    → 所以只编码少量「动作模板」，再复制到全部动画名，避免 1 小时以上的编码

用法：
  python forge.py --input 立绘.png --name "小鲸·成熟版" --prefix mature
  python forge.py --input 立绘.png --key 00FF00 --name "小鲸·成熟版" --prefix mature
"""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

# ---------------------------------------------------------------- 常量

def _find_ffmpeg() -> Path:
    cand = [
        Path(r"C:\APP\deepseek\.caches\ffget\node_modules\.pnpm"
             r"\@ffmpeg-installer+win32-x64@4.1.0\node_modules"
             r"\@ffmpeg-installer\win32-x64\ffmpeg.exe"),
    ]
    for c in cand:
        if c.exists():
            return c
    root = Path(r"C:\APP\deepseek\.caches\ffget")
    if root.exists():
        for p in root.rglob("ffmpeg.exe"):
            return p
    return cand[0]


FFMPEG = _find_ffmpeg()

PLUGIN_DIR = Path(os.environ["USERPROFILE"]) / ".dsh" / "profiles" / "desktop" / "node_modules" / "dsh-pet"
DEFAULT_CONFIG = PLUGIN_DIR / "assets" / "config.jsonc"
BUNDLED_WEBM = PLUGIN_DIR / "assets" / "webm"
USER_PET_DIR = Path(os.environ["USERPROFILE"]) / ".dsh" / "dsh-pet" / "pet"

CANVAS = (640, 360)
FPS = 24

# ---------------------------------------------------------------- JSONC

def strip_jsonc(text: str) -> str:
    """去掉 JSONC 的注释（字符串内的 // 与 /* 不动），再抹掉尾逗号。"""
    out = []
    i, n = 0, len(text)
    in_str = False
    esc = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    cleaned = "".join(out)
    cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
    return cleaned


# ---------------------------------------------------------------- 图像处理

def remove_solid_background(img: Image.Image, key_hex: str, tol: int = 60, feather: int = 28) -> Image.Image:
    """把近似 key_hex 的像素抠成透明，并对边缘做羽化，避免硬锯齿。"""
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
                k = (d - tol) / float(feather)
                px[x, y] = (r, g, b, int(a * k))
    return img


def decontaminate_edges(img: Image.Image, rounds: int = 2) -> Image.Image:
    """把半透明边缘像素的颜色向外扩散，去掉绿边/黑边残留。"""
    img = img.convert("RGBA")
    px = img.load()
    w, h = img.size
    for _ in range(rounds):
        src = list(img.getdata())
        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                if a == 0 or a > 200:
                    continue
                acc = [0, 0, 0]
                cnt = 0
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < w and 0 <= ny < h:
                            nr, ng, nb, na = src[ny * w + nx]
                            if na >= 240:
                                acc[0] += nr
                                acc[1] += ng
                                acc[2] += nb
                                cnt += 1
                if cnt:
                    px[x, y] = (acc[0] // cnt, acc[1] // cnt, acc[2] // cnt, a)
    return img


def prepare_base(img: Image.Image, target_h: int) -> Image.Image:
    """裁到内容包围盒，等比缩放到目标高度。"""
    img = img.convert("RGBA")
    bbox = img.getchannel("A").getbbox()
    if bbox:
        img = img.crop(bbox)
    if img.height <= 0:
        raise SystemExit("输入图的透明区域为空，检查一下图是不是全透明")
    ratio = target_h / float(img.height)
    new_w = max(1, int(round(img.width * ratio)))
    return img.resize((new_w, target_h), Image.LANCZOS)


def render(base: Image.Image, *, scale_y=1.0, scale_x=1.0, rot=0.0, dx=0.0, dy=0.0) -> Image.Image:
    """把 base 按给定形变画到 640x360 画布上（底部对齐，脚底不飘）。"""
    w = max(1, int(round(base.width * scale_x)))
    h = max(1, int(round(base.height * scale_y)))
    img = base.resize((w, h), Image.LANCZOS)
    if abs(rot) > 0.01:
        img = img.rotate(rot, resample=Image.BICUBIC, expand=True)
    canvas = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    x = (CANVAS[0] - img.width) // 2 + int(round(dx))
    y = CANVAS[1] - img.height + int(round(dy))
    canvas.alpha_composite(img, (max(-CANVAS[0], x), max(-CANVAS[1], y)))
    return canvas


# ---------------------------------------------------------------- 动作模板

def frames_breathe(n):
    for i in range(n):
        t = i / float(n) * 2 * math.pi
        yield render(BASE, scale_y=1 + 0.016 * math.sin(t),
                     scale_x=1 + 0.008 * math.cos(t),
                     rot=0.9 * math.sin(t + 0.7),
                     dx=1.8 * math.sin(t + 1.3))


def frames_bob(n):
    for i in range(n):
        t = i / float(n) * 2 * math.pi
        yield render(BASE, scale_y=1 + 0.012 * math.sin(t),
                     dy=-2.5 * abs(math.sin(t)),
                     rot=1.6 * math.sin(2 * t))


def frames_hop(n):
    for i in range(n):
        p = i / float(n)
        up = math.sin(math.pi * p) ** 1.5
        yield render(BASE,
                     scale_y=1 + 0.06 * up - 0.05 * max(0.0, math.sin(math.pi * (p - 0.75) * 4)),
                     scale_x=1 - 0.04 * up,
                     dy=-26 * up)


def frames_tilt(n):
    for i in range(n):
        p = i / float(n)
        env = math.sin(math.pi * p)
        yield render(BASE, rot=7.5 * math.sin(2 * math.pi * p) * env,
                     dx=6 * math.sin(4 * math.pi * p) * env,
                     scale_y=1 + 0.01 * env)


def frames_stretch(n):
    for i in range(n):
        p = i / float(n)
        s = math.sin(math.pi * p) ** 1.2
        yield render(BASE, scale_y=1 + 0.085 * s, scale_x=1 - 0.05 * s,
                     rot=-1.5 * s, dy=-6 * s)


def frames_swing(n):
    for i in range(n):
        t = i / float(n) * 2 * math.pi
        yield render(BASE, rot=5.5 * math.sin(t),
                     dx=4.5 * math.sin(t), dy=-3 * abs(math.sin(t)),
                     scale_y=1 + 0.01 * math.cos(t))


TEMPLATES = {
    "breathe": (frames_breathe, 192, 8.0),   # 8 秒循环
    "bob":     (frames_bob, 72, 3.0),
    "hop":     (frames_hop, 36, 1.5),
    "tilt":    (frames_tilt, 40, 1.67),
    "stretch": (frames_stretch, 56, 2.33),
    "swing":   (frames_swing, 60, 2.5),
}


def pick_template(name: str) -> str:
    if "拖拽" in name:
        return "swing"
    if "懒腰" in name:
        return "stretch"
    if name.startswith("点击回应"):
        return "hop" if ("开心" in name or "跃动" in name or "元气" in name) else "tilt"
    if name.startswith("被") or "吓" in name:
        return "tilt"
    if "吃" in name or "喝" in name or "吹" in name:
        return "bob"
    if "睡" in name or "惊醒" in name:
        return "breathe"
    return "breathe"


# ---------------------------------------------------------------- 编码

def encode(frames_iter, count: int, out_path: Path, work: Path):
    fdir = work / out_path.stem
    if fdir.exists():
        shutil.rmtree(fdir)
    fdir.mkdir(parents=True, exist_ok=True)
    for i, frame in enumerate(frames_iter):
        frame.save(fdir / f"{i:04d}.png")
    cmd = [
        str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
        "-framerate", str(FPS), "-i", str(fdir / "%04d.png"),
        "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
        "-b:v", "0", "-crf", "34", "-auto-alt-ref", "0",
        "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"编码失败 {out_path.name}:\n{r.stderr[-1500:]}")
    shutil.rmtree(fdir, ignore_errors=True)
    return out_path.stat().st_size


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="把一张角色图做成 dsh-pet 透明动画宠物包")
    ap.add_argument("--input", required=True, help="角色图（透明底 PNG，或纯色背景图）")
    ap.add_argument("--key", default="", help="要抠掉的背景色，如 00FF00（不给则按已有 alpha 处理）")
    ap.add_argument("--name", default="小鲸·成熟版", help="宠物显示名")
    ap.add_argument("--prefix", default="mature", help="宠物包前缀（决定目录名）")
    ap.add_argument("--height", type=int, default=330, help="角色在 640x360 画布里的高度")
    ap.add_argument("--out", default="", help="输出目录（默认 ./build/<prefix>）")
    ap.add_argument("--only", default="", help="只生成这些模板，逗号分隔（调试用）")
    args = ap.parse_args()

    global BASE

    here = Path(__file__).resolve().parent
    out_root = Path(args.out) if args.out else here / "build" / args.prefix
    work = here / "work"
    work.mkdir(parents=True, exist_ok=True)

    src = Path(args.input)
    if not src.exists():
        raise SystemExit(f"找不到输入图：{src}")

    print(f"[1/5] 读取角色图 {src.name}")
    img = Image.open(src)
    if args.key:
        print(f"      抠掉背景色 #{args.key.upper()}")
        img = remove_solid_background(img, args.key)
        img = decontaminate_edges(img)
    BASE = prepare_base(img, args.height)
    print(f"      角色尺寸 {BASE.width}x{BASE.height}（画布 {CANVAS[0]}x{CANVAS[1]}）")

    anim_dir = out_root / "pet" / f"{args.prefix}-animation"
    if anim_dir.exists():
        shutil.rmtree(anim_dir)
    anim_dir.mkdir(parents=True, exist_ok=True)

    names = sorted(p.stem for p in BUNDLED_WEBM.glob("*.webm"))
    if not names:
        raise SystemExit("读不到插件自带的动画名清单")
    print(f"[2/5] 插件共 {len(names)} 个动画名，按关键词归类到 6 个动作模板")

    wanted = set(x.strip() for x in args.only.split(",") if x.strip()) or set(TEMPLATES)
    tpl_file = {}
    t0 = time.time()
    for i, (tpl, (fn, count, secs)) in enumerate(TEMPLATES.items(), 1):
        if tpl not in wanted:
            continue
        out = work / f"__tpl_{tpl}.webm"
        print(f"      [{i}/{len(TEMPLATES)}] 编码模板 {tpl}（{count} 帧 / {secs:.1f}s）…", end="", flush=True)
        size = encode(fn(count), count, out, work)
        tpl_file[tpl] = out
        print(f" {size/1024:.0f}KB，用时 {time.time()-t0:.0f}s")
        t0 = time.time()

    if not wanted.issubset(set(tpl_file)):
        raise SystemExit("模板没编全")

    print(f"[3/5] 复制模板到 {len(names)} 个动画名")
    fallback = next(iter(tpl_file.values()))
    assign = {}
    for nm in names:
        tpl = pick_template(nm)
        src_file = tpl_file.get(tpl, fallback)
        assign[tpl if tpl in tpl_file else "(fallback)"] = assign.get(tpl if tpl in tpl_file else "(fallback)", 0) + 1
        shutil.copyfile(src_file, anim_dir / f"{nm}.webm")
    for tpl, cnt in sorted(assign.items()):
        print(f"      {tpl:<8} -> {cnt} 个动画")

    print("[4/5] 生成宠物包配置")
    cfg = json.loads(strip_jsonc(DEFAULT_CONFIG.read_text(encoding="utf-8")))
    cfg["pets"] = [{
        "id": f"{args.prefix}1",
        "name": args.name,
        "size": 430,
        "balanceEnabled": True,
        "whisperEnabled": True,
        "workStatusEnabled": True,
        "display": "desktop",
        "position": {"corner": "bottom-right", "marginX": 40, "marginY": 90},
    }]
    cfg_path = out_root / "pet" / f"{args.prefix}-config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    total = sum(p.stat().st_size for p in anim_dir.glob("*.webm"))
    print(f"[5/5] 完成")
    print(f"      宠物包: {out_root / 'pet'}")
    print(f"      动画  : {len(names)} 个，共 {total/1024/1024:.1f} MB")
    print()
    print("安装（复制到插件用户目录，扫到即生效、无需重启）：")
    print(f'  xcopy /E /I /Y "{out_root / "pet"}" "{USER_PET_DIR}"')


if __name__ == "__main__":
    main()
