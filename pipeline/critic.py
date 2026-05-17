"""
critic.py — 渲染结果自动质检 + 修复建议

渲染后自动检查每张图，返回问题和可执行的修复指令。
调用方（Claude）根据修复指令自动处理，无需人工介入。

检查项：
1. 是否有背景照片（不是纯色底）
2. 文字是否完整可读
3. 图文是否相关
"""

import base64
import json
import os
import re
import sys

from PIL import Image, ImageStat

_PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

from google import genai

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=GEMINI_API_KEY)


def _is_plain_background(img_path, threshold=15):
    """检测是否纯色背景"""
    img = Image.open(img_path).convert("RGB")
    stat = ImageStat.Stat(img)
    avg_std = sum(stat.stddev) / 3
    return avg_std < threshold, round(avg_std, 1)


def _gemini_check_slide(img_path, slide_text):
    """Gemini 视觉质检单张图"""
    with open(img_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    prompt = f"""检查这张小红书帖子图片的渲染质量。

对应文案：
{slide_text}

用JSON回答：
{{
  "has_photo": true/false,
  "text_complete": true/false,
  "text_readable": true/false,
  "image_relevant": true/false,
  "issues": ["问题描述"],
  "score": 1-10
}}
只输出JSON。"""

    try:
        resp = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                prompt,
                {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}},
            ],
            config={"max_output_tokens": 512, "thinking_config": {"thinking_budget": 256}},
        )
        raw = resp.text.strip()
        if "```" in raw:
            for part in raw.split("```"):
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{"):
                    raw = part
                    break
        return json.loads(raw)
    except Exception:
        return {"has_photo": False, "text_complete": True, "text_readable": True,
                "image_relevant": False, "issues": [], "score": 5}


def critique_post(post_dir):
    """
    质检渲染结果，返回问题列表和修复指令。

    Returns:
        dict with:
        - pass: bool
        - score: float
        - slides: list of slide results
        - fixes: list of fix instructions (actionable)
        - report: str (human-readable summary)
    """
    post_path = os.path.join(post_dir, "post.txt")
    if not os.path.exists(post_path):
        return {"error": "找不到 post.txt", "pass": False, "fixes": [], "report": "找不到文案文件"}

    with open(post_path, "r", encoding="utf-8") as f:
        post_text = f.read()

    # 解析文案
    slide_texts = {}
    parts = re.split(r"===图(\d+)===", post_text)
    for i in range(1, len(parts), 2):
        num = int(parts[i])
        text = parts[i + 1].strip() if i + 1 < len(parts) else ""
        slide_texts[num] = text

    slide_files = sorted([
        f for f in os.listdir(post_dir)
        if f.startswith("slide_") and f.endswith(".jpg")
    ])

    if not slide_files:
        return {"error": "没有渲染图片", "pass": False, "fixes": [], "report": "没有找到渲染图片"}

    slides = []
    fixes = []
    total_score = 0

    for sf in slide_files:
        num = int(sf.replace("slide_", "").replace(".jpg", ""))
        img_path = os.path.join(post_dir, sf)
        text = slide_texts.get(num, "")

        # 本地快检
        is_plain, complexity = _is_plain_background(img_path)

        # Gemini 视觉检查
        gemini = _gemini_check_slide(img_path, text)

        issues = []
        slide_fixes = []

        # 问题1: 没有背景图
        if is_plain or not gemini.get("has_photo", True):
            issues.append("缺少背景照片")
            slide_fixes.append({
                "type": "need_photo",
                "slide": num,
                "text": text,
                "reason": "这张图是纯色底，需要一张相关照片作为背景",
            })

        # 问题2: 文字截断
        if not gemini.get("text_complete", True):
            issues.append("文字被截断或溢出")
            slide_fixes.append({
                "type": "text_overflow",
                "slide": num,
                "text": text,
                "reason": "文案太长导致文字被截断，需要精简文案或拆成两张图",
            })

        # 问题3: 文字不可读
        if not gemini.get("text_readable", True):
            issues.append("文字不清晰")
            slide_fixes.append({
                "type": "text_unreadable",
                "slide": num,
                "reason": "文字和背景对比度不够，需要重新渲染（换照片或换文字样式）",
            })

        # 问题4: 图文不相关
        if gemini.get("has_photo") and not gemini.get("image_relevant", True):
            issues.append("背景照片与文案不相关")
            slide_fixes.append({
                "type": "irrelevant_photo",
                "slide": num,
                "text": text,
                "reason": "背景图和文案内容不匹配，需要换一张更相关的照片",
            })

        # Gemini 发现的其他问题
        for gi in gemini.get("issues", []):
            if gi not in issues:
                issues.append(gi)

        score = gemini.get("score", 5)
        total_score += score

        slides.append({
            "slide": num,
            "score": score,
            "has_photo": not is_plain,
            "complexity": complexity,
            "issues": issues,
            "text_preview": text[:40],
        })
        fixes.extend(slide_fixes)

    avg_score = total_score / len(slides)
    no_photo = sum(1 for s in slides if not s["has_photo"])
    problem_count = sum(1 for s in slides if s["issues"])

    # 生成报告
    lines = [f"质检完成: {len(slides)}张图, 平均{avg_score:.1f}分, {no_photo}张缺照片, {problem_count}张有问题"]
    for s in slides:
        icon = "x" if s["issues"] else "o"
        bg = "有图" if s["has_photo"] else "无图"
        lines.append(f"  [{icon}] 图{s['slide']} ({bg}, {s['score']}分) {s['text_preview']}")
        for issue in s["issues"]:
            lines.append(f"      - {issue}")

    if fixes:
        lines.append(f"\n需要修复 {len(fixes)} 个问题:")
        for fix in fixes:
            lines.append(f"  图{fix['slide']}: [{fix['type']}] {fix['reason']}")

    passed = no_photo <= 1 and avg_score >= 6 and problem_count <= 2

    return {
        "pass": passed,
        "score": round(avg_score, 1),
        "slides": slides,
        "fixes": fixes,
        "report": "\n".join(lines),
        "no_photo_count": no_photo,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("post_dir")
    args = parser.parse_args()
    result = critique_post(args.post_dir)
    print(result["report"])
