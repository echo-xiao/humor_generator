"""
render_post.py — 仿"妈的欧洲账本"风格渲染

Gemini 看图+看文案 → 输出排版指令 → Pillow 渲染
学习博主几十种排版变化，每张图独立设计
"""

import json
import os
import re
import sys
import base64
import time

from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

_PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, _PROJECT_ROOT)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

from google import genai

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

W, H = 1080, 1440

FONT_BOLD_PATH = "/System/Library/Fonts/Hiragino Sans GB.ttc"
FONT_BOLD_IDX = 2
FONT_REG_PATH = "/System/Library/Fonts/Hiragino Sans GB.ttc"
FONT_REG_IDX = 0

STYLE_EXAMPLES_DIR = "/tmp/mama_sample/all_imgs"


def _font(size, bold=True):
    path = FONT_BOLD_PATH if bold else FONT_REG_PATH
    idx = FONT_BOLD_IDX if bold else FONT_REG_IDX
    try:
        return ImageFont.truetype(path, size, index=idx)
    except Exception:
        return ImageFont.truetype("/System/Library/Fonts/STHeiti Medium.ttc", size)


def _tw(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _wrap(draw, text, font, max_w):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for ch in para:
            if _tw(draw, cur + ch, font)[0] > max_w:
                if cur:
                    lines.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
    return lines


def _crop34(img):
    w, h = img.size
    r = W / H
    if w / h > r:
        nw = int(h * r)
        img = img.crop(((w - nw) // 2, 0, (w + nw) // 2, h))
    else:
        nh = int(w / r)
        img = img.crop((0, (h - nh) // 2, w, (h + nh) // 2))
    return img.resize((W, H), Image.LANCZOS)


def _draw_text_element(draw, elem):
    """根据排版指令绘制一个文字元素"""
    text = elem.get("text", "")
    if not text:
        return

    x = max(0, min(elem.get("x", 50), W - 50))
    y = max(0, min(elem.get("y", 400), H - 50))
    font_size = max(20, min(elem.get("font_size", 60), 160))
    bold = elem.get("bold", True)
    color = elem.get("color", "white")
    style = elem.get("style", "shadow")  # shadow / white_bar / black_bar / plain
    max_width = elem.get("max_width", W - 100)

    font = _font(font_size, bold)
    lines = _wrap(draw, text, font, max_width)
    line_h = int(font_size * 1.35)

    # 颜色
    if color == "red":
        fill = (255, 59, 48, 255)
    elif color == "black":
        fill = (20, 20, 20, 255)
    elif color == "gold":
        fill = (230, 180, 50, 255)
    else:
        fill = (255, 255, 255, 255)

    for i, line in enumerate(lines):
        lx, ly = x, y + i * line_h
        lw, lh = _tw(draw, line, font)

        # 确保不超出画布
        if lx + lw > W - 20:
            lx = W - lw - 20
        if ly + lh > H - 20:
            ly = H - lh - 20

        if style == "white_bar":
            padding = max(8, font_size // 5)
            draw.rectangle(
                [lx - padding, ly - padding // 2, lx + lw + padding, ly + lh + padding // 2],
                fill=(255, 255, 255, 235),
            )
            draw.text((lx, ly), line, font=font, fill=(20, 20, 20, 255))

        elif style == "black_bar":
            padding = max(10, font_size // 4)
            draw.rectangle(
                [lx - padding, ly - padding // 2, lx + lw + padding, ly + lh + padding // 2],
                fill=(0, 0, 0, 220),
            )
            draw.text((lx, ly), line, font=font, fill=(255, 255, 255, 255))

        elif style == "shadow":
            shadow = (0, 0, 0, 200)
            for dx, dy in [(-2, -2), (-2, 2), (2, -2), (2, 2), (0, -2), (0, 2), (-2, 0), (2, 0)]:
                draw.text((lx + dx, ly + dy), line, font=font, fill=shadow)
            draw.text((lx + 3, ly + 3), line, font=font, fill=(0, 0, 0, 100))
            draw.text((lx, ly), line, font=font, fill=fill)

        else:  # plain
            draw.text((lx, ly), line, font=font, fill=fill)


def _get_layout(photo_path, main_text, sub_text, is_cover=False, max_retries=3):
    """让 Gemini 看图+文案，输出排版指令"""
    with open(photo_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    if is_cover:
        prompt = (
            '为这张照片设计封面排版。封面要视觉冲击强，在信息流里抓眼球。\n\n'
            f'文案：{main_text}\n\n'
            '封面风格参考：大黑色不透明方块+超大白字，或者超大白字直接叠在暗处。\n'
            '关键词要特别大(100-140px)。\n\n'
        )
    else:
        prompt = (
            '为这张照片设计文字排版，风格参考"妈的欧洲账本"。\n\n'
            f'大标题：{main_text}\n'
            f'小字注释：{sub_text}\n\n'
            '## 博主的排版规律（你必须学习并灵活运用）\n'
            '1. 文字放在照片空白/暗处/纯色区域，绝不遮挡主要物品（食物/产品/动物/建筑细节）\n'
            '2. 如果有人脸，文字必须覆盖脸部\n'
            '3. 同一张图里字号差异很大：关键词可以80-120px，注释30-38px\n'
            '4. style类型：\n'
            '   - shadow: 白字+黑色描边阴影（暗色背景用）\n'
            '   - white_bar: 白色背景条+黑字（亮色/杂色背景用，最常见）\n'
            '   - black_bar: 黑色背景条+白字（需要强调/背景太杂时用）\n'
            '   - plain: 纯色文字无效果（已有高对比度时用）\n'
            '5. 金额用红色(color="red")或白色，字号要大(90-120px)\n'
            '6. 小字注释通常右对齐放右下角，或放在不影响主体的角落\n'
            '7. 大标题偏左对齐居多，但也可以居中或右对齐\n'
            '8. 有时关键词占半个屏幕（"哥本哈根。""那就嫁了吧。"），句号也是设计元素\n'
            '9. 颜色有时跟照片配合（金色产品配gold文字）\n'
            '10. 不要所有元素都用同一种style，要混搭\n\n'
        )

    prompt += (
        '输出JSON，每个元素一个对象：\n'
        '{"elements":[{"text":"文字","x":50,"y":300,"font_size":70,"bold":true,'
        '"color":"white","style":"white_bar","max_width":800}]}\n\n'
        'color: white/black/red/gold\n'
        'style: shadow/white_bar/black_bar/plain\n'
        'x范围0-1080, y范围0-1440\n'
        '元素之间不能重叠。只输出JSON。'
    )

    contents = [prompt]

    # 传入风格参考图（如果存在）
    ref_files = ["orig_09.jpg", "orig_11.jpg", "orig_18.jpg", "orig_28.jpg", "orig_32.jpg"]
    refs_added = 0
    for ref in ref_files:
        ref_path = os.path.join(STYLE_EXAMPLES_DIR, ref)
        if os.path.exists(ref_path) and refs_added < 3:
            if refs_added == 0:
                contents.append("以下是风格参考图：")
            with open(ref_path, "rb") as f:
                contents.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(f.read()).decode()}})
            refs_added += 1

    contents.append("以下是需要排版的照片：")
    contents.append({"inline_data": {"mime_type": "image/jpeg", "data": img_b64}})

    for attempt in range(max_retries):
        try:
            resp = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=contents,
                config={"max_output_tokens": 4096, "thinking_config": {"thinking_budget": 1024}},
            )

            raw = resp.text
            if not raw:
                continue
            raw = raw.strip()

            # 提取 JSON
            if "```" in raw:
                parts = raw.split("```")
                for part in parts:
                    part = part.strip()
                    if part.startswith("json"):
                        part = part[4:].strip()
                    if part.startswith("{"):
                        raw = part
                        break

            # 清理注释和尾逗号
            cleaned_lines = []
            for line in raw.split('\n'):
                in_str = False
                clean = []
                for ci, c in enumerate(line):
                    if c == '"' and (ci == 0 or line[ci-1] != '\\'):
                        in_str = not in_str
                    if not in_str and ci + 1 < len(line) and line[ci:ci+2] == '//':
                        break
                    clean.append(c)
                cleaned_lines.append(''.join(clean).rstrip())
            cleaned = '\n'.join(cleaned_lines)
            cleaned = re.sub(r',(\s*[}\]])', r'\1', cleaned)

            try:
                layout = json.loads(cleaned)
                if "elements" in layout and len(layout["elements"]) > 0:
                    return layout
            except json.JSONDecodeError:
                # 尝试提取 JSON 对象
                match = re.search(r'\{[^{}]*"elements"\s*:\s*\[.*?\]\s*\}', cleaned, re.DOTALL)
                if match:
                    try:
                        layout = json.loads(match.group())
                        if layout.get("elements"):
                            return layout
                    except json.JSONDecodeError:
                        pass

            if attempt < max_retries - 1:
                time.sleep(2)

        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                break

    # Fallback：简单布局
    return _fallback_layout(main_text, sub_text, is_cover)


def _fallback_layout(main_text, sub_text, is_cover):
    """Gemini 失败时的备用布局"""
    elements = []
    if is_cover:
        elements.append({
            "text": main_text, "x": 60, "y": 400,
            "font_size": 90, "bold": True, "color": "white",
            "style": "black_bar", "max_width": 900,
        })
    else:
        elements.append({
            "text": main_text, "x": 45, "y": 350,
            "font_size": 65, "bold": True, "color": "black",
            "style": "white_bar", "max_width": 900,
        })
        if sub_text:
            elements.append({
                "text": sub_text, "x": 500, "y": 1200,
                "font_size": 32, "bold": True, "color": "white",
                "style": "shadow", "max_width": 500,
            })
    return {"elements": elements}


def render_slide(photo_path, main_text, sub_text="", output_path=None, is_cover=False):
    """渲染一张"妈的欧洲账本"风格图片"""
    # 1. Gemini 分析排版
    layout = _get_layout(photo_path, main_text, sub_text, is_cover)

    # 2. 加载照片
    img = Image.open(photo_path).convert("RGBA")
    img = _crop34(img)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    # 3. 绘制每个元素
    for elem in layout.get("elements", []):
        _draw_text_element(draw, elem)

    # 4. 合成输出
    result = Image.alpha_composite(img, layer).convert("RGB")

    if output_path is None:
        base = os.path.splitext(os.path.basename(photo_path))[0]
        out_dir = os.path.join(_PROJECT_ROOT, "output")
        os.makedirs(out_dir, exist_ok=True)
        output_path = os.path.join(out_dir, f"{base}_rendered.jpg")

    result.save(output_path, "JPEG", quality=95)
    return output_path


def render_cover(photo_path, main_text, output_path=None):
    """渲染封面图"""
    return render_slide(photo_path, main_text, "", output_path, is_cover=True)


def render_text_card(main_text, sub_text="", output_path=None):
    """纯文字卡片"""
    img = Image.new("RGBA", (W, H), (250, 248, 244, 255))
    draw = ImageDraw.Draw(img)

    title_font = _font(64)
    lines = _wrap(draw, main_text, title_font, W - 160)
    line_h = 84
    total_h = len(lines) * line_h
    y0 = (H - total_h) // 2 - 60

    for i, line in enumerate(lines):
        draw.text((80, y0 + i * line_h), line, font=title_font, fill=(30, 30, 30))

    sep_y = y0 + total_h + 40
    draw.line([(80, sep_y), (W - 80, sep_y)], fill=(200, 195, 188), width=2)

    if sub_text:
        sf = _font(30, bold=False)
        for i, line in enumerate(sub_text.strip().split("\n")):
            draw.text((80, sep_y + 30 + i * 42), line.strip(), font=sf, fill=(120, 115, 108))

    result = img.convert("RGB")
    if output_path is None:
        out_dir = os.path.join(_PROJECT_ROOT, "output")
        os.makedirs(out_dir, exist_ok=True)
        output_path = os.path.join(out_dir, "text_card.jpg")
    result.save(output_path, "JPEG", quality=95)
    return output_path


def parse_slide_text(slide_text):
    """解析文案 → (大标题, 小字注释)"""
    lines = [l.strip() for l in slide_text.strip().split("\n") if l.strip()]
    if not lines:
        return "", ""
    main, sub, chars = [], [], 0
    for i, line in enumerate(lines):
        chars += len(line)
        if chars <= 25 and i < 2:
            main.append(line)
        else:
            sub.append(line)
    if not main:
        main, sub = [lines[0]], lines[1:]
    return "\n".join(main), "\n".join(sub)
