"""
render_post.py — 仿"妈的欧洲账本"风格渲染

方案：Gemini只选模式和位置区域，代码精确控制排版（不再让AI输出坐标）
"""

import json
import os
import re
import sys
import base64

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

# 字体：Hiragino Sans GB W6（粗体）最接近博主字体
FONT_BOLD_PATH = "/System/Library/Fonts/Hiragino Sans GB.ttc"
FONT_BOLD_IDX = 2  # W6 Bold
FONT_REG_PATH = "/System/Library/Fonts/Hiragino Sans GB.ttc"
FONT_REG_IDX = 0   # W3 Regular


def _font(size, bold=True):
    path = FONT_BOLD_PATH if bold else FONT_REG_PATH
    idx = FONT_BOLD_IDX if bold else FONT_REG_IDX
    try:
        return ImageFont.truetype(path, size, index=idx)
    except Exception:
        return ImageFont.truetype("/System/Library/Fonts/STHeiti Medium.ttc", size)


def _tw(draw, text, font):
    """文字宽度"""
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _wrap(draw, text, font, max_w):
    """自动换行"""
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
    """裁剪到3:4"""
    w, h = img.size
    r = W / H
    if w / h > r:
        nw = int(h * r)
        img = img.crop(((w - nw) // 2, 0, (w + nw) // 2, h))
    else:
        nh = int(w / r)
        img = img.crop((0, (h - nh) // 2, w, (h + nh) // 2))
    return img.resize((W, H), Image.LANCZOS)


def _draw_white_bar_text(draw, x, y, text, font, padding=14):
    """白底黑字标题条（博主最常用的样式）"""
    tw, th = _tw(draw, text, font)
    # 白色背景条
    draw.rectangle(
        [x - padding, y - padding // 2, x + tw + padding, y + th + padding // 2],
        fill=(255, 255, 255, 235),
    )
    # 黑字
    draw.text((x, y), text, font=font, fill=(20, 20, 20, 255))
    return tw, th


def _draw_shadow_text(draw, x, y, text, font, color=(255, 255, 255, 255)):
    """带描边阴影的文字（暗色照片用）"""
    shadow = (0, 0, 0, 200)
    for dx, dy in [(-2, -2), (-2, 2), (2, -2), (2, 2), (0, -2), (0, 2), (-2, 0), (2, 0)]:
        draw.text((x + dx, y + dy), text, font=font, fill=shadow)
    draw.text((x + 3, y + 3), text, font=font, fill=(0, 0, 0, 100))
    draw.text((x, y), text, font=font, fill=color)
    return _tw(draw, text, font)


def _analyze_photo(photo_path):
    """让Gemini分析照片，只返回简单指令"""
    with open(photo_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    prompt = (
        "分析这张照片用于文字排版。只回答以下3个问题，每行一个答案：\n"
        "1. 照片整体是亮色还是暗色？（亮/暗）\n"
        "2. 照片哪个区域有空白/纯色适合放大标题？（上/中/下）\n"
        "3. 照片右下角区域是否适合放小字？（是/否）\n"
        "只输出3行，如：亮\n上\n是"
    )

    try:
        resp = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[prompt, {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}}],
            config={"max_output_tokens": 50, "thinking_config": {"thinking_budget": 256}},
        )
        lines = resp.text.strip().split("\n")
        brightness = "暗" if "暗" in lines[0] else "亮"
        position = "上"
        for l in lines:
            if "中" in l:
                position = "中"
            elif "下" in l:
                position = "下"
            elif "上" in l:
                position = "上"
        sub_ok = "否" not in lines[-1] if len(lines) > 2 else True
        return brightness, position, sub_ok
    except Exception:
        return "亮", "中", True


def render_cover(photo_path, main_text, output_path=None):
    """
    渲染封面图（第1张）——大黑色方块 + 超大白字

    风格：视觉冲击强，在小红书信息流里抓眼球
    """
    img = Image.open(photo_path).convert("RGBA")
    img = _crop34(img)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    # 文字拆行
    lines = [l.strip() for l in main_text.strip().split("\n") if l.strip()]

    # 根据文字量决定字号
    max_chars = max(len(l) for l in lines) if lines else 1
    if max_chars <= 4:
        font_size = 140
    elif max_chars <= 6:
        font_size = 110
    elif max_chars <= 10:
        font_size = 85
    else:
        font_size = 70

    title_font = _font(font_size)

    # 计算每行实际宽度和总高度
    max_block_w = W - 80
    rendered_lines = []
    for line in lines:
        # 如果一行太长，自动换行
        wrapped = _wrap(draw, line, title_font, max_block_w - 80)
        rendered_lines.extend(wrapped)

    line_h = int(font_size * 1.35)
    total_text_h = len(rendered_lines) * line_h
    max_line_w = max((_tw(draw, l, title_font)[0] for l in rendered_lines), default=0)

    # 黑色方块：居中偏上，紧贴文字
    block_pad_x = 50
    block_pad_y = 40
    block_w = max_line_w + block_pad_x * 2
    block_h = total_text_h + block_pad_y * 2

    block_x = (W - block_w) // 2 - 20  # 稍偏左
    block_y = int(H * 0.28) - block_h // 2

    # 画黑色方块
    draw.rectangle(
        [block_x, block_y, block_x + block_w, block_y + block_h],
        fill=(0, 0, 0, 235),
    )

    # 画白色大字（居中在方块内）
    for i, line in enumerate(rendered_lines):
        lw, _ = _tw(draw, line, title_font)
        x = block_x + (block_w - lw) // 2
        y = block_y + block_pad_y + i * line_h
        draw.text((x, y), line, font=title_font, fill=(255, 255, 255, 255))

    result = Image.alpha_composite(img, layer).convert("RGB")

    if output_path is None:
        base = os.path.splitext(os.path.basename(photo_path))[0]
        out_dir = os.path.join(_PROJECT_ROOT, "output")
        os.makedirs(out_dir, exist_ok=True)
        output_path = os.path.join(out_dir, f"{base}_cover.jpg")

    result.save(output_path, "JPEG", quality=95)
    return output_path


def render_slide(photo_path, main_text, sub_text="", output_path=None, is_cover=False):
    """
    渲染一张"妈的欧洲账本"风格图片

    Args:
        photo_path: 照片路径
        main_text: 大标题文字
        sub_text: 小字注释
        output_path: 输出路径
        is_cover: 是否为封面图（第1张）
    """
    if is_cover:
        return render_cover(photo_path, main_text, output_path)
    # 1. 分析照片
    brightness, title_pos, sub_ok = _analyze_photo(photo_path)

    # 2. 加载和裁剪照片
    img = Image.open(photo_path).convert("RGBA")
    img = _crop34(img)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    # 3. 提取金额
    price_match = re.search(r'[-]?[¥$￥]\s*[\d,]+\.?\d*', main_text)
    price = price_match.group(0) if price_match else None
    clean_main = main_text.replace(price, "").strip() if price else main_text

    # 4. 决定排版模式
    use_white_bar = (brightness == "亮")  # 亮色照片用白底黑字，暗色照片用白字

    # 5. 标题位置
    margin_left = 45
    max_title_w = W - margin_left * 2 - 30
    title_font_size = 65

    # 根据文字长度调整字号
    test_font = _font(title_font_size)
    test_lines = _wrap(draw, clean_main, test_font, max_title_w)
    if len(test_lines) > 3:
        title_font_size = 55
    elif len(test_lines) <= 1 and len(clean_main) <= 8:
        title_font_size = 80

    title_font = _font(title_font_size)
    title_lines = _wrap(draw, clean_main, title_font, max_title_w)
    line_h = int(title_font_size * 1.45)

    # Y起始位置
    if title_pos == "上":
        title_y = 100
    elif title_pos == "下":
        title_y = H - 200 - len(title_lines) * line_h
    else:  # 中
        title_y = (H - len(title_lines) * line_h) // 2 - 80

    # 6. 绘制金额（红色大字）
    if price:
        price_font = _font(100)
        if use_white_bar:
            pw, ph = _draw_white_bar_text(draw, W - 350, title_y - 120, price, price_font, padding=16)
        else:
            _draw_shadow_text(draw, W - 350, title_y - 120, price, price_font, color=(255, 59, 48, 255))

    # 7. 绘制大标题
    for i, line in enumerate(title_lines):
        x = margin_left
        y = title_y + i * line_h

        if use_white_bar:
            _draw_white_bar_text(draw, x, y, line, title_font, padding=14)
        else:
            _draw_shadow_text(draw, x, y, line, title_font)

    # 8. 绘制小字注释（右下角，右对齐）
    if sub_text:
        sub_font = _font(32, bold=True)
        sub_lines = [l.strip() for l in sub_text.strip().split("\n") if l.strip()]
        sub_line_h = 44
        margin_right = 50
        margin_bottom = 60

        sub_start_y = H - margin_bottom - len(sub_lines) * sub_line_h

        # 确保不和标题重叠
        title_bottom = title_y + len(title_lines) * line_h + 30
        if sub_start_y < title_bottom:
            sub_start_y = title_bottom + 20

        for i, line in enumerate(sub_lines):
            sw, sh = _tw(draw, line, sub_font)
            x = W - sw - margin_right
            y = sub_start_y + i * sub_line_h

            if use_white_bar:
                # 小字也用白底黑字，但padding更小
                _draw_white_bar_text(draw, x, y, line, sub_font, padding=8)
            else:
                _draw_shadow_text(draw, x, y, line, sub_font)

    # 9. 合成输出
    result = Image.alpha_composite(img, layer).convert("RGB")

    if output_path is None:
        base = os.path.splitext(os.path.basename(photo_path))[0]
        out_dir = os.path.join(_PROJECT_ROOT, "output")
        os.makedirs(out_dir, exist_ok=True)
        output_path = os.path.join(out_dir, f"{base}_rendered.jpg")

    result.save(output_path, "JPEG", quality=95)
    return output_path


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


if __name__ == "__main__":
    out_dir = os.path.join(_PROJECT_ROOT, "output")
    os.makedirs(out_dir, exist_ok=True)

    # 测试用你自己的照片
    test_photo = os.path.join(_PROJECT_ROOT, "output", "test_photo2.jpg")
    if os.path.exists(test_photo):
        out = render_slide(
            test_photo,
            "在LA待业的第47天\n海浪不懂KPI",
            "但它每天准时打卡\n比我勤快",
            os.path.join(out_dir, "v3_render_1.jpg"),
        )
        print(f"Render 1: {out}")

        out2 = render_slide(
            test_photo,
            "¥50买了杯果汁\n折合老家一周菜钱",
            "数据不会骗人\n但洛杉矶会",
            os.path.join(out_dir, "v3_render_2.jpg"),
        )
        print(f"Render 2: {out2}")

    out3 = render_text_card(
        "绕了一圈回到原点\n但原点涨价了",
        "美本→回国大厂→失业归海\n完整闭环",
        os.path.join(out_dir, "v3_text_card.jpg"),
    )
    print(f"Text card: {out3}")
