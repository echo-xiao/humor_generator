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
COVER_EXAMPLES_DIR = "/tmp/mama_sample/covers2"
DESIGN_GUIDE_PATH = os.path.join(_PROJECT_ROOT, "data", "design_guide.json")


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


STYLE_DB_PATH = os.path.join(_PROJECT_ROOT, "data", "style_database.json")


def _get_photo_brightness(img):
    """判断照片整体亮度"""
    from PIL import ImageStat
    stat = ImageStat.Stat(img.convert("L"))
    return stat.mean[0]  # 0-255, >140 算亮


def _load_style_templates():
    """从样式数据库加载模板"""
    if not os.path.exists(STYLE_DB_PATH):
        return []
    with open(STYLE_DB_PATH, "r") as f:
        return json.load(f)


def _find_matching_template(brightness, num_title_chars, num_sub_chars, has_price):
    """根据照片特征从样式数据库中找最匹配的模板"""
    db = _load_style_templates()
    if not db:
        return None

    is_bright = brightness > 140

    best = None
    best_score = -1

    for entry in db:
        if entry.get("is_cover"):
            continue  # 封面用固定模板

        elems = entry.get("text_elements", entry.get("elements", []))
        if not elems:
            continue

        photo_bright = entry.get("photo_brightness", "") == "亮"
        score = 0

        # 亮度匹配
        if photo_bright == is_bright:
            score += 5

        # 文字数量匹配
        title_elems = [e for e in elems if e.get("role") in ("title", "keyword", "subtitle")]
        sub_elems = [e for e in elems if e.get("role") in ("annotation",)]
        if len(title_elems) > 0 and num_title_chars > 0:
            score += 2
        if len(sub_elems) > 0 and num_sub_chars > 0:
            score += 2
        if not sub_elems and not num_sub_chars:
            score += 1

        # 金额匹配
        price_elems = [e for e in elems if e.get("role") == "price"]
        if bool(price_elems) == has_price:
            score += 3

        if score > best_score:
            best_score = score
            best = entry

    return best


def _apply_template_style(elements, template, brightness):
    """将模板的样式应用到Gemini返回的位置上"""
    if not template:
        # 无模板，用默认
        is_bright = brightness > 140
        default_style = "white_bar" if is_bright else "shadow"
        default_color = "black" if is_bright else "white"
        for elem in elements:
            text = elem.get("text", "")
            if re.search(r'[¥$￥]\d', text):
                elem["style"] = "shadow"
                elem["color"] = "red"
            else:
                elem["style"] = default_style
                elem["color"] = default_color
        return

    template_elems = template.get("text_elements", template.get("elements", []))

    # 按 role 分类模板元素的样式
    role_styles = {}
    for te in template_elems:
        role = te.get("role", "title")
        role_styles[role] = {
            "style": te.get("style", "shadow"),
            "color": te.get("color", "white"),
        }

    for elem in elements:
        text = elem.get("text", "")
        fs = elem.get("font_size", 60)

        # 判断角色
        if re.search(r'[¥$￥]\d', text):
            elem["style"] = "shadow"
            elem["color"] = "red"
        elif fs >= 70:
            rs = role_styles.get("title", role_styles.get("keyword", {"style": "shadow", "color": "white"}))
            elem["style"] = rs["style"]
            elem["color"] = rs["color"]
        else:
            rs = role_styles.get("annotation", {"style": "shadow", "color": "white"})
            elem["style"] = rs["style"]
            elem["color"] = rs["color"]


def _get_layout(photo_path, main_text, sub_text, is_cover=False, max_retries=3):
    """Gemini 决定位置，样式数据库决定风格"""
    with open(photo_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    prompt = (
        '看这张照片（画布1080x1440），我要在上面放文字。\n\n'
        f'大标题：{main_text}\n'
        f'小字注释：{sub_text}\n\n'
        '请告诉我每段文字应该放在什么位置（x,y坐标），以及合适的字号。\n'
        '规则：\n'
        '1. 文字放在空白/暗处/纯色区域\n'
        '2. 可以稍微挡一点主体\n'
        '3. 人脸必须被文字覆盖\n'
        '4. 大标题偏左，注释放右下角\n'
        '5. 元素不能重叠\n\n'
        '输出JSON：\n'
        '{"elements":[{"text":"文字","x":50,"y":300,"font_size":70,"max_width":800}]}\n'
        '只输出JSON。'
    )

    contents = [prompt]
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


def _render_cover_fixed(photo_path, main_text, output_path):
    """封面图：固定黑块+超大白字，不依赖Gemini"""
    img = Image.open(photo_path).convert("RGBA")
    img = _crop34(img)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    # 文案拆行
    lines = [l.strip() for l in main_text.strip().split("\n") if l.strip()]

    # 每行字号：短的关键词更大
    sized_lines = []
    for line in lines:
        n = len(line)
        if n <= 2:
            fs = 160
        elif n <= 4:
            fs = 120
        elif n <= 6:
            fs = 100
        elif n <= 10:
            fs = 80
        else:
            fs = 65
        sized_lines.append((line, fs))

    # 计算总高度和最大宽度
    total_h = 0
    max_w = 0
    line_metrics = []
    for line, fs in sized_lines:
        font = _font(fs)
        wrapped = _wrap(draw, line, font, W - 200)
        lh = int(fs * 1.3)
        for wl in wrapped:
            tw, th = _tw(draw, wl, font)
            line_metrics.append((wl, fs, font, tw, lh))
            max_w = max(max_w, tw)
            total_h += lh

    # 黑色方块
    block_pad_x = 50
    block_pad_y = 35
    block_w = max_w + block_pad_x * 2
    block_h = total_h + block_pad_y * 2

    # 方块位置：居中偏左偏上
    block_x = max(30, (W - block_w) // 2 - 30)
    block_y = max(80, int(H * 0.22))

    # 确保方块不超出画布
    if block_x + block_w > W - 20:
        block_w = W - block_x - 20
    if block_y + block_h > H - 100:
        block_y = H - block_h - 100

    draw.rectangle(
        [block_x, block_y, block_x + block_w, block_y + block_h],
        fill=(0, 0, 0, 250),
    )

    # 画白字
    cy = block_y + block_pad_y
    for wl, fs, font, tw, lh in line_metrics:
        # 左对齐在方块内
        cx = block_x + block_pad_x
        draw.text((cx, cy), wl, font=font, fill=(255, 255, 255, 255))
        cy += lh

    result = Image.alpha_composite(img, layer).convert("RGB")
    result.save(output_path, "JPEG", quality=95)
    return output_path


def render_slide(photo_path, main_text, sub_text="", output_path=None, is_cover=False):
    """渲染一张"妈的欧洲账本"风格图片"""
    if output_path is None:
        base = os.path.splitext(os.path.basename(photo_path))[0]
        out_dir = os.path.join(_PROJECT_ROOT, "output")
        os.makedirs(out_dir, exist_ok=True)
        output_path = os.path.join(out_dir, f"{base}_rendered.jpg")

    # 封面用固定模板（不依赖Gemini），内页用Gemini智能排版
    if is_cover:
        return _render_cover_fixed(photo_path, main_text, output_path)

    # 1. 加载照片 + 判断亮度
    img = Image.open(photo_path).convert("RGBA")
    img = _crop34(img)
    brightness = _get_photo_brightness(img)

    # 2. Gemini 决定位置
    layout = _get_layout(photo_path, main_text, sub_text, is_cover)
    elements = layout.get("elements", [])

    # 3. 从样式数据库匹配模板，应用风格
    has_price = bool(re.search(r'[¥$￥]\d', main_text))
    template = _find_matching_template(brightness, len(main_text), len(sub_text), has_price)
    _apply_template_style(elements, template, brightness)

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    # 4. 绘制每个元素
    for elem in elements:
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
