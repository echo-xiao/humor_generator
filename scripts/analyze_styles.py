"""
analyze_styles.py — 重新分析博主原图，提取精确的框尺寸和排版参数

输出新的 style_database.json，每个文字元素包含：
- 框类型(white_bar/black_bar/black_block/plain)
- 框的像素级 bounding box (box_x, box_y, box_w, box_h)
- 框内 padding
- 框内包含几行文字
- 文字的精确位置、字号、颜色
"""

import json
import os
import sys
import base64
import time
import re
import glob

from dotenv import load_dotenv

_PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _PROJECT_ROOT)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

from google import genai

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)

ALL_IMGS_DIR = "/tmp/mama_sample/all_imgs"
COVERS_DIR = "/tmp/mama_sample/covers2"
OUTPUT_PATH = os.path.join(_PROJECT_ROOT, "data", "style_database_v2.json")

PROMPT = """你是一个排版分析专家。这张图片是小红书博主的帖子（画布1080x1440像素）。

请精确分析图上每一个文字元素的排版参数。对每个文字元素，输出：

1. text: 文字内容
2. role: title(大标题) / keyword(关键词/地名) / annotation(注释/小字) / price(金额) / hashtag(话题标签)
3. font_size: 估算字号(像素)，参考画布宽1080
4. color: 文字颜色 white/black/red/gold
5. bold: 是否粗体 true/false
6. x: 文字左上角x坐标(像素)
7. y: 文字左上角y坐标(像素)
8. text_width: 文字总宽度(像素)
9. text_height: 文字总高度(像素，含多行)

10. bg_type: 文字背景类型
    - "none": 无背景，直接叠在图上
    - "white_bar": 白色条/块
    - "black_bar": 黑色条/块
    - "black_block": 大面积黑色方块（封面用）

11. 如果bg_type不是"none"，还需要：
    - box_x: 背景框左上角x
    - box_y: 背景框左上角y
    - box_w: 背景框宽度
    - box_h: 背景框高度
    - box_opacity: 背景框不透明度 0-255
    - padding_x: 文字到框左右边的距离
    - padding_y: 文字到框上下边的距离
    - lines_in_box: 框内包含几行文字

12. is_cover: 这张图是否是封面图(大黑块+大字) true/false
13. photo_brightness: 照片整体亮度 "亮"/"暗"/"混合"

输出纯JSON，格式：
{"elements":[...], "is_cover": false, "photo_brightness": "暗"}

注意：
- 坐标基于1080x1440画布
- 仔细测量框的实际像素尺寸，不要估算太粗
- 如果多行文字共用一个背景框，它们算一个元素，lines_in_box>1
- 只输出JSON，不要其他文字
"""


def analyze_one_image(img_path, max_retries=3):
    """分析单张图片"""
    with open(img_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    contents = [PROMPT, {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}}]

    for attempt in range(max_retries):
        try:
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=contents,
                config={"max_output_tokens": 8192, "thinking_config": {"thinking_budget": 2048}},
            )
            raw = resp.text
            if not raw:
                continue
            raw = raw.strip()

            # 提取JSON
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

            result = json.loads(cleaned)
            if "elements" in result and len(result["elements"]) > 0:
                return result

        except Exception as e:
            print(f"  attempt {attempt+1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(3)

    return None


def main():
    # 收集所有图片
    imgs = sorted(glob.glob(os.path.join(ALL_IMGS_DIR, "orig_*.jpg")))
    covers = sorted(glob.glob(os.path.join(COVERS_DIR, "cover_*.jpg")))

    print(f"Found {len(imgs)} content images, {len(covers)} cover images")

    results = []

    for img_path in imgs + covers:
        fname = os.path.basename(img_path)
        print(f"Analyzing {fname}...")

        result = analyze_one_image(img_path)
        if result:
            result["_source_file"] = fname
            results.append(result)
            print(f"  OK: {len(result['elements'])} elements")
        else:
            print(f"  FAILED")

        time.sleep(1)  # rate limit

    # 保存
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\nSaved {len(results)} entries to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
