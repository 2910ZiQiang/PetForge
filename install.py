#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PetForge · 安装器 —— 把生成好的宠物包装进 dsh-pet，并触发热加载。

做三件事：
  1. 复制 build/<prefix>/pet/ 到插件的用户宠物目录
  2. POST /dsh-pet-7340/reload 让插件重新读取（无需重启宿主应用）
  3. 回读 /config 验证宠物真的注册进去了

用法：
  python install.py --prefix mature
  python install.py --prefix mature --port 19387
  python install.py --prefix mature --no-reload     # 只复制，不热加载
"""

import argparse
import json
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import forge  # noqa: E402  复用 USER_PET_DIR 等路径探测

# 宿主 web 服务端口候选（插件的路由挂在宿主 webServer 上）
PORT_CANDIDATES = (19387, 3080, 19388, 3000)
ROUTE = "/dsh-pet-7340"


def _get(url, timeout=4):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def find_host(port=None):
    """找到承载 dsh-pet 路由的宿主端口，返回 (port, config) 或 (None, None)。"""
    for p in ((port,) if port else PORT_CANDIDATES):
        try:
            status, body = _get(f"http://127.0.0.1:{p}{ROUTE}/config")
            if status == 200:
                return p, json.loads(body)
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            continue
    return None, None


def pet_names(config):
    """从 /config 返回的聚合配置里取出所有宠物名（主配置 + 各宠物包）。"""
    out = []
    if not isinstance(config, dict):
        return out
    for key, section in config.items():
        if isinstance(section, dict) and isinstance(section.get("pets"), list):
            for pet in section["pets"]:
                out.append((key, pet.get("name", "?"), pet.get("id", "?")))
    return out


def reload_plugin(port):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{ROUTE}/reload", method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, r.read().decode("utf-8", "replace")


def main():
    ap = argparse.ArgumentParser(description="把宠物包安装进 dsh-pet")
    ap.add_argument("--prefix", default="mature", help="宠物包前缀（对应 forge.py 的 --prefix）")
    ap.add_argument("--port", type=int, default=None, help="宿主 web 端口（默认自动探测）")
    ap.add_argument("--no-reload", action="store_true", help="只复制文件，不触发热加载")
    ap.add_argument("--dst", default=None, help="目标目录（默认插件的用户宠物目录）")
    args = ap.parse_args()

    src = HERE / "build" / args.prefix / "pet"
    cfg = src / f"{args.prefix}-config.json"
    anim = src / f"{args.prefix}-animation"

    print(f"[1/4] 检查宠物包 build/{args.prefix}/pet/")
    if not cfg.exists():
        raise SystemExit(f"  找不到配置：{cfg}\n  先跑：python forge.py --prefix {args.prefix} --input <角色图>")
    if not anim.is_dir():
        raise SystemExit(f"  找不到动画目录：{anim}")
    webms = list(anim.glob("*.webm"))
    if not webms:
        raise SystemExit(f"  {anim} 里没有 .webm 文件")
    total = sum(p.stat().st_size for p in webms)
    print(f"  ✓ 配置 {cfg.stat().st_size / 1024:.1f} KB，动画 {len(webms)} 个 / {total / 1048576:.1f} MB")

    dst_root = Path(args.dst) if args.dst else forge.USER_PET_DIR
    print(f"[2/4] 安装到 {dst_root}")
    dst_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cfg, dst_root / cfg.name)
    dst_anim = dst_root / anim.name
    if dst_anim.exists():
        shutil.rmtree(dst_anim)
    shutil.copytree(anim, dst_anim)
    print(f"  ✓ {cfg.name} + {anim.name}/（{len(list(dst_anim.glob('*.webm')))} 个动画）")

    if args.no_reload:
        print("[3/4] 跳过热加载（--no-reload）")
        print("[4/4] 完成。记得稍后在插件里重载配置，或重启宿主应用。")
        return

    print("[3/4] 探测宿主并触发热加载")
    port, before = find_host(args.port)
    if port is None:
        tried = ", ".join(str(p) for p in ((args.port,) if args.port else PORT_CANDIDATES))
        print(f"  ✗ 没有在 {tried} 上找到 dsh-pet 路由。")
        print("    文件已复制好，等宿主应用运行后重跑本脚本，或右键桌宠 → 重载配置。")
        return
    print(f"  ✓ 宿主端口 {port}")
    try:
        status, body = reload_plugin(port)
        print(f"  ✓ POST /reload → HTTP {status} {body.strip()[:80]}")
    except (urllib.error.URLError, OSError) as e:
        raise SystemExit(f"  ✗ 热加载失败：{e}")

    print("[4/4] 验证宠物是否注册")
    _, after = find_host(port)
    names = pet_names(after)
    for key, name, pid in names:
        mark = "  ← 本次安装" if key == args.prefix else ""
        print(f"  [{key}] {name}  id={pid}{mark}")
    installed = any(key == args.prefix for key, _, _ in names)
    if installed:
        print(f"\n✅ 成功：宠物包「{args.prefix}」已生效，无需重启。")
    else:
        print(f"\n⚠️ 复制成功，但 /config 里还没有「{args.prefix}」。")
        print("   检查配置文件名与动画目录名是否严格配对：")
        print(f"     {args.prefix}-config.json  +  {args.prefix}-animation/")


if __name__ == "__main__":
    main()
