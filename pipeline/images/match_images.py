"""
match_images.py — 根据确认的文案匹配 Google Photos 图片

输入：确认后的帖子文案（===图1=== 格式）
输出：每张图推荐的照片列表

用法：
  python pipeline/images/match_images.py --text "===图1===\n在巴黎租了个阁楼..."
  或作为模块导入使用
"""

import json
import os
import sys
import re
import logging

from dotenv import load_dotenv

_PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, _PROJECT_ROOT)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

from google import genai
from pipeline.images.photo_index import load_index, search_photos, get_drive_creds

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=GEMINI_API_KEY)


def parse_slides(post_text):
    """解析帖子文案为 slide 列表"""
    slides = []
    parts = re.split(r"===图(\d+)===", post_text)
    # parts: ['', '1', 'text1', '2', 'text2', ...]
    for i in range(1, len(parts), 2):
        slide_num = int(parts[i])
        text = parts[i + 1].strip() if i + 1 < len(parts) else ""
        slides.append({"num": slide_num, "text": text})
    return slides


MATCH_PROMPT = """你是一个图片编辑，需要为小红书帖子的每张图选配图片。

以下是帖子的每张图文案：
{slides_text}

以下是可用的照片库索引（每张照片有描述和关键词）：
{photo_samples}

请为每张图推荐最合适的照片。考虑：
1. 地点优先：文案提到某个城市/国家，优先选该地拍摄的照片
2. 内容相关性（文案提到食物就配食物照片）
3. 氛围匹配（吐槽的内容配"平淡/日常"的照片更好，不要配太美的风景）
4. 如果文案是纯吐槽/感慨没有具体场景，可以配一张日常/随意的照片
5. 尽量不重复使用同一张照片

输出 JSON 格式：
[
  {{"slide": 1, "photo_id": "xxx", "reason": "为什么选这张"}},
  ...
]

如果某张图在库里确实找不到合适的，photo_id 写 null，reason 写需要什么样的图。"""


def match_images_for_post(post_text, top_k_per_slide=3):
    """为帖子的每张图匹配照片

    Args:
        post_text: 确认后的帖子文案（===图1=== 格式）
        top_k_per_slide: 每张图推荐几张候选

    Returns:
        list of {slide, candidates: [{id, name, score, reason, ...}], text}
    """
    index = load_index()
    if not index:
        logging.error("照片索引为空，请先运行 photo_index.py 构建索引")
        return []

    slides = parse_slides(post_text)
    if not slides:
        logging.error("无法解析帖子文案")
        return []

    # Step 1: 用 Gemini 为每张图生成搜索关键词
    keyword_prompt = f"""以下是一篇小红书帖子的每张图文案。请为每张图提取 3-5 个用于搜索配图的关键词。

注意：
1. 关键词应包含文案提到的地理位置（城市、国家、地标等），例如文案提到洛杉矶就要包含"洛杉矶"或"Los Angeles"
2. 关键词要具体，优先使用场景/地点/物体等名词
3. 同时提供中文和英文关键词以提高匹配率

{chr(10).join(f"图{s['num']}: {s['text'][:200]}" for s in slides)}

输出 JSON：[{{"slide": 1, "keywords": ["关键词1", "关键词2", ...]}}, ...]"""

    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash", contents=keyword_prompt
    )
    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    try:
        slide_keywords = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: 用文案本身做关键词
        slide_keywords = [{"slide": s["num"], "keywords": s["text"][:100].split()} for s in slides]

    # Step 2: 为每张图搜索候选照片
    all_candidates = {}
    used_ids = set()

    for sk in slide_keywords:
        slide_num = sk["slide"]
        keywords = sk.get("keywords", [])
        query = " ".join(keywords)

        candidates = search_photos(query, index, top_k=top_k_per_slide * 3)
        # 去重：避免多张图用同一张照片
        filtered = []
        for c in candidates:
            if c["id"] not in used_ids:
                filtered.append(c)
            if len(filtered) >= top_k_per_slide:
                break

        if filtered:
            used_ids.add(filtered[0]["id"])  # 标记第一候选为已用

        all_candidates[slide_num] = {
            "keywords": keywords,
            "candidates": filtered,
        }

    # Step 3: 用 Gemini 做最终选择
    slides_text = "\n".join(f"图{s['num']}: {s['text'][:300]}" for s in slides)

    photo_lines = []
    for slide_num, data in all_candidates.items():
        photo_lines.append(f"\n--- 图{slide_num}的候选照片 ---")
        for c in data["candidates"]:
            photo_lines.append(
                f"  ID={c['id']} | {c.get('content', '无描述')} | "
                f"关键词: {c.get('keywords', '')} | 场景: {c.get('scene_type', '')}"
            )
    photo_samples = "\n".join(photo_lines)

    prompt = MATCH_PROMPT.format(slides_text=slides_text, photo_samples=photo_samples)
    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash", contents=prompt
    )

    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    try:
        final_picks = json.loads(raw)
    except json.JSONDecodeError:
        final_picks = []

    # 组装结果
    results = []
    for s in slides:
        slide_num = s["num"]
        pick = next((p for p in final_picks if p.get("slide") == slide_num), None)
        cands = all_candidates.get(slide_num, {}).get("candidates", [])

        result = {
            "slide": slide_num,
            "text": s["text"][:200],
            "search_keywords": all_candidates.get(slide_num, {}).get("keywords", []),
            "recommended": None,
            "candidates": cands[:top_k_per_slide],
            "reason": "",
        }

        if pick and pick.get("photo_id"):
            photo_info = index.get(pick["photo_id"])
            if photo_info:
                result["recommended"] = {
                    "id": pick["photo_id"],
                    **photo_info,
                }
                result["reason"] = pick.get("reason", "")
            else:
                result["reason"] = pick.get("reason", "未找到合适照片")
        elif pick:
            result["reason"] = pick.get("reason", "未找到合适照片")

        results.append(result)

    return results


def get_photo_url(photo_id):
    """获取照片的下载/查看链接"""
    creds = get_drive_creds()
    resp = __import__("requests").get(
        f"https://www.googleapis.com/drive/v3/files/{photo_id}",
        headers={"Authorization": f"Bearer {creds.token}"},
        params={"fields": "webViewLink,webContentLink,thumbnailLink"},
    )
    return resp.json()


def format_results(results):
    """格式化匹配结果为可读文本"""
    lines = []
    for r in results:
        lines.append(f"\n===图{r['slide']}===")
        lines.append(f"文案: {r['text']}")
        lines.append(f"搜索关键词: {', '.join(r['search_keywords'])}")

        if r["recommended"]:
            rec = r["recommended"]
            lines.append(f"推荐照片: {rec['name']}")
            lines.append(f"  描述: {rec.get('content', '无')}")
            lines.append(f"  原因: {r['reason']}")
            lines.append(f"  查看: https://drive.google.com/file/d/{rec['id']}/view")
        else:
            lines.append(f"未找到合适照片: {r['reason']}")

        if r["candidates"]:
            lines.append("其他候选:")
            for c in r["candidates"][:2]:
                lines.append(f"  - {c['name']}: {c.get('content', '无描述')}")
                lines.append(f"    https://drive.google.com/file/d/{c['id']}/view")

    return "\n".join(lines)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="为帖子匹配配图")
    parser.add_argument("--text", type=str, help="帖子文案文本")
    parser.add_argument("--file", type=str, help="帖子文案文件路径")
    args = parser.parse_args()

    if args.file:
        with open(args.file) as f:
            text = f.read()
    elif args.text:
        text = args.text
    else:
        print("请提供 --text 或 --file 参数")
        sys.exit(1)

    results = match_images_for_post(text)
    print(format_results(results))
