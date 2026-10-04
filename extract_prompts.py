#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从官方提示词文件里抽出「通用前缀 + 单个动作」，生成可直接粘贴的成品提示词。
"""
import re
import sys
from pathlib import Path

SRC = Path(r"C:\APP\deepseek\PetForge\prompts-official\桌面宠物 10 秒动作提示词.md")
OUT = Path(r"C:\APP\deepseek\PetForge\prompts-ready")

# 优先做这些（3 个必做 + 9 个推荐）
MUST = ["待机呼吸休闲", "点击回应-开心跃动", "被鼠标拖拽悬空反馈"]
NICE = ["点击回应-害羞惊讶", "点击回应-傲娇生气", "超大伸懒腰",
        "原地漂浮踏步", "东张西望", "打瞌睡被惊醒",
        "女仆屈膝礼仪", "原地下蹲压缩", "悠闲哼歌"]


def parse(md: str):
    """返回 (通用前缀, {动作名: 正文})"""
    parts = re.split(r"^##\s+", md, flags=re.M)
    prefix = ""
    actions = {}
    for p in parts:
        if not p.strip():
            continue
        lines = p.split("\n")
        title = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
        if title == "通用前缀":
            prefix = body
        else:
            actions[title] = body
    return prefix, actions


def main():
    md = SRC.read_text(encoding="utf-8")
    prefix, actions = parse(md)
    print(f"通用前缀 {len(prefix)} 字符，共 {len(actions)} 个动作")

    OUT.mkdir(parents=True, exist_ok=True)

    order = [n for n in MUST + NICE if n in actions]
    missing = [n for n in MUST + NICE if n not in actions]
    if missing:
        print("警告：以下动作在官方文件里没找到:", missing)

    for i, name in enumerate(order, 1):
        tag = "必做" if name in MUST else "推荐"
        text = (
            "【通用前缀 —— 每次生成都必须原样带上】\n\n"
            + prefix
            + "\n\n---\n\n【本次动作】\n\n## "
            + name
            + "\n\n"
            + actions[name]
        )
        p = OUT / f"{i:02d}-{tag}-{name}.txt"
        p.write_text(text, encoding="utf-8")
        print(f"  {p.name}  {len(text)} 字符")

    combined = []
    for i, name in enumerate(order, 1):
        tag = "必做" if name in MUST else "推荐"
        combined.append(f"{'='*70}\n### {i}. [{tag}] {name}\n{'='*70}\n")
        combined.append(prefix + "\n\n" + actions[name] + "\n")
    (OUT / "全部提示词-合集.txt").write_text("\n".join(combined), encoding="utf-8")
    print(f"  全部提示词-合集.txt")

    (OUT / "说明.md").write_text(
        "# 怎么用这些提示词\n\n"
        "## 一、用什么工具\n\n"
        "任何支持**图生视频（首帧参考图）**的 AI 视频工具都行：\n\n"
        "- 豆包（官方素材就是用它做的）\n"
        "- 即梦 / 可灵 / 海螺 / 通义万相 / Runway / Pika\n\n"
        "## 二、操作步骤（每个动作重复一次）\n\n"
        "1. 上传**角色参考图**：`C:\\APP\\deepseek\\PetForge\\input\\角色-原图.png`\n"
        "   （必须用这张，它是绿幕标准正面站姿，是所有动画的基准帧）\n"
        "2. 把 `prompts-ready` 里对应的 `.txt` **全文粘贴**到提示词框\n"
        "3. 生成 **10 秒**视频，比例选 **16:9**\n"
        "4. 下载后按动作名保存到这个目录：\n\n"
        "```\nC:\\APP\\deepseek\\PetForge\\input\\videos\\\n```\n\n"
        "文件名就用动作名，例如：\n\n"
        "```\n待机呼吸休闲.mp4\n点击回应-开心跃动.mp4\n被鼠标拖拽悬空反馈.mp4\n```\n\n"
        "5. 生成完告诉我，我自动完成抠像 + 对齐 + 编码 + 替换宠物包\n\n"
        "## 三、先做 3 个验证\n\n"
        "建议**先只做「必做」那 3 个**，我跑通一遍确认效果，再批量做剩下的。\n"
        "这样万一工具有问题，不会白做一堆。\n\n"
        "## 四、注意事项\n\n"
        "- **背景必须纯 #00FF00**，有阴影/渐变会导致抠图脏边\n"
        "- **不要裁切**：头顶和脚底都要留出绿色空间\n"
        "- **全程原地**：不能有左右或上下位移（提示词里已强制，但生成结果要抽查）\n"
        "- 视频里角色大小要**保持一致**，否则动画之间会跳大小\n",
        encoding="utf-8")
    print("  说明.md")


if __name__ == "__main__":
    main()
