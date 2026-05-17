"""
photo_index.py — 构建 Google Photos 图片索引

特性：
  - 断点续传：每批处理完自动保存，中断后接着跑
  - 增量更新：只处理新照片，已索引的跳过
  - 进度条：tqdm 显示进度和预估时间
  - 自动重试：遇到 rate limit 自动等待重试

运行：
  python pipeline/images/photo_index.py          # 构建/更新索引
  python pipeline/images/photo_index.py --status  # 查看索引状态
"""

import json
import os
import sys
import time
import base64
import logging
import argparse

from dotenv import load_dotenv

_PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _PROJECT_ROOT)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

import requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google import genai
from tqdm import tqdm

logging.basicConfig(level=logging.WARNING, format="%(asctime)s - %(message)s")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TOKEN_PATH = os.path.join(_PROJECT_ROOT, "data", "google_photos_token.json")
INDEX_PATH = os.path.join(_PROJECT_ROOT, "data", "photo_index.json")

gemini_client = genai.Client(api_key=GEMINI_API_KEY)


def get_drive_creds():
    with open(TOKEN_PATH) as f:
        token_data = json.load(f)
    creds = Credentials(
        token=token_data["token"],
        refresh_token=token_data["refresh_token"],
        token_uri=token_data["token_uri"],
        client_id=token_data["client_id"],
        client_secret=token_data["client_secret"],
        scopes=token_data["scopes"],
    )
    creds.refresh(Request())
    return creds


def fetch_all_photos(creds):
    """从 Google Drive 拉取所有图片元数据"""
    photos = []
    page_token = None
    pbar = tqdm(desc="拉取照片列表", unit="张")
    while True:
        params = {
            "pageSize": 1000,
            "q": "mimeType contains 'image/' and trashed = false",
            "fields": "files(id,name,mimeType,createdTime,imageMediaMetadata,thumbnailLink,parents),nextPageToken",
            "orderBy": "createdTime desc",
        }
        if page_token:
            params["pageToken"] = page_token
        resp = requests.get(
            "https://www.googleapis.com/drive/v3/files",
            headers={"Authorization": f"Bearer {creds.token}"},
            params=params,
        )
        data = resp.json()
        batch = data.get("files", [])
        for f in batch:
            meta = f.get("imageMediaMetadata", {})
            loc = meta.get("location", {})
            # 只索引有GPS定位的真实照片（过滤截图/表情包等）
            if not loc.get("latitude"):
                continue
            photos.append({
                "id": f["id"],
                "name": f["name"],
                "mime": f["mimeType"],
                "created": f.get("createdTime", ""),
                "width": meta.get("width"),
                "height": meta.get("height"),
                "lat": loc.get("latitude"),
                "lon": loc.get("longitude"),
                "thumb": f.get("thumbnailLink", ""),
                "parents": f.get("parents", []),
            })
        pbar.update(len(batch))
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    pbar.close()
    return photos


def download_thumbnail(thumb_url, creds, size=256):
    """下载缩略图并返回 base64"""
    if not thumb_url:
        return None
    url = thumb_url.replace("=s220", f"=s{size}")
    resp = requests.get(url, headers={"Authorization": f"Bearer {creds.token}"})
    if resp.status_code == 200:
        return base64.b64encode(resp.content).decode()
    return None


DESCRIBE_PROMPT = """看这些照片，为每张生成简短的中文描述标签，用于后续搜索匹配。

要求：
- 每张照片一行，格式：序号|场景类型|主要内容|氛围/情绪|关键词(逗号分隔)
- 场景类型：食物/街景/建筑/自然风光/室内/人物/交通/购物/其他
- 关键词要具体（"法式甜点橱窗" 而不是 "商店"）
- 如果看不清就写 "模糊|无法识别"

示例：
1|食物|咖啡馆里的牛角面包和拿铁|悠闲|咖啡,面包,早餐,法式,咖啡馆
2|街景|雨天的巴黎小巷|冷清|巴黎,雨天,街道,欧洲"""


def describe_batch(photo_batch, creds, max_retries=5):
    """用 Gemini Flash Vision 描述一批照片，带自动重试"""
    contents = [DESCRIBE_PROMPT]
    valid_indices = []

    for i, photo in enumerate(photo_batch):
        thumb_b64 = download_thumbnail(photo["thumb"], creds)
        if thumb_b64:
            contents.append({
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": thumb_b64,
                }
            })
            valid_indices.append(i)

    if not valid_indices:
        return {}

    for attempt in range(max_retries):
        try:
            response = gemini_client.models.generate_content(
                model="gemini-2.5-pro",
                contents=contents,
                config={"max_output_tokens": 4096},
            )
            text = response.text
            if not text:
                if attempt < max_retries - 1:
                    time.sleep(3)
                    continue
                return {}
            text = text.strip()
            results = {}
            for line in text.split("\n"):
                line = line.strip()
                if not line or "|" not in line:
                    continue
                parts = line.split("|")
                if len(parts) < 4:
                    continue
                try:
                    idx = int(parts[0].strip()) - 1
                    if 0 <= idx < len(valid_indices):
                        real_idx = valid_indices[idx]
                        results[real_idx] = {
                            "scene_type": parts[1].strip(),
                            "content": parts[2].strip(),
                            "mood": parts[3].strip(),
                            "keywords": parts[4].strip() if len(parts) > 4 else "",
                        }
                except (ValueError, IndexError):
                    continue
            return results

        except Exception as e:
            err = str(e).lower()
            if "429" in str(e) or "rate" in err or "limit" in err or "quota" in err:
                wait = min(30 * (2 ** attempt), 300)
                tqdm.write(f"⏳ Rate limit，等待 {wait}s 后重试 ({attempt+1}/{max_retries})...")
                time.sleep(wait)
            elif "400" in str(e):
                tqdm.write(f"⚠️ 请求无效，跳过这批: {str(e)[:80]}")
                return {}
            else:
                tqdm.write(f"❌ Gemini 错误: {e}")
                if attempt < max_retries - 1:
                    time.sleep(5)
                else:
                    return {}

    return {}


def build_index(batch_size=20):
    """构建/更新图片索引"""
    creds = get_drive_creds()

    # 加载已有索引（断点续传）+ 自动清理失败的
    index = {}
    if os.path.exists(INDEX_PATH):
        with open(INDEX_PATH) as f:
            raw_index = json.load(f)
        failed_count = 0
        for k, v in raw_index.items():
            if v.get("scene_type") == "未识别":
                failed_count += 1
            else:
                index[k] = v
        if failed_count:
            print(f"🧹 自动清理了 {failed_count} 个失败条目，将重新处理")

    # 拉元数据
    all_photos = fetch_all_photos(creds)

    # 过滤已索引的（增量更新）
    to_process = [p for p in all_photos if p["id"] not in index]

    if not to_process:
        print(f"✅ 索引已是最新！共 {len(index)} 张照片，无新照片需要处理")
        return index

    print(f"📷 共 {len(all_photos)} 张照片，已索引 {len(index)} 张，新增 {len(to_process)} 张待处理")

    # 分批描述
    failed = 0
    pbar = tqdm(range(0, len(to_process), batch_size), desc="索引照片", unit="批")
    for i in pbar:
        batch = to_process[i:i + batch_size]
        pbar.set_postfix({"已索引": len(index), "失败": failed})

        descriptions = describe_batch(batch, creds)

        for j, photo in enumerate(batch):
            entry = {
                "name": photo["name"],
                "created": photo["created"],
                "lat": photo["lat"],
                "lon": photo["lon"],
                "width": photo["width"],
                "height": photo["height"],
            }
            if j in descriptions:
                entry.update(descriptions[j])
                index[photo["id"]] = entry
            else:
                # 不写入索引，下次运行自动重试
                failed += 1

        # 每批保存（断点续传）
        os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
        with open(INDEX_PATH, "w") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

        # Gemini Flash 付费 tier 限制宽松，0.5s 间隔即可
        if i + batch_size < len(to_process):
            time.sleep(0.5)

    print(f"\n✅ 索引完成！共 {len(index)} 张照片，本次新增 {len(to_process)} 张，{failed} 张未识别")
    return index


def load_index():
    """加载已有索引"""
    if not os.path.exists(INDEX_PATH):
        return {}
    with open(INDEX_PATH) as f:
        return json.load(f)


def search_photos(query, index=None, top_k=5):
    """根据关键词搜索照片"""
    if index is None:
        index = load_index()
    if not index:
        return []

    query_lower = query.lower()
    query_keywords = set(query_lower.replace(",", " ").replace("，", " ").split())

    results = []
    for photo_id, info in index.items():
        score = 0
        searchable = f"{info.get('content', '')} {info.get('keywords', '')} {info.get('scene_type', '')} {info.get('mood', '')}".lower()

        for kw in query_keywords:
            if kw in searchable:
                score += 1

        if score > 0:
            results.append({
                "id": photo_id,
                "score": score,
                **info,
            })

    results.sort(key=lambda x: -x["score"])
    return results[:top_k]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="构建 Google Photos 图片索引")
    parser.add_argument("--status", action="store_true", help="查看索引状态")
    parser.add_argument("--batch-size", type=int, default=20, help="每批处理几张")
    args = parser.parse_args()

    if args.status:
        idx = load_index()
        print(f"索引中有 {len(idx)} 张照片")
        if idx:
            types = {}
            for info in idx.values():
                t = info.get("scene_type", "未知")
                types[t] = types.get(t, 0) + 1
            print("\n场景类型分布:")
            for t, c in sorted(types.items(), key=lambda x: -x[1]):
                print(f"  {t}: {c}")
            recent = sorted(idx.items(), key=lambda x: x[1].get("created", ""), reverse=True)[:5]
            print("\n最近索引的照片:")
            for pid, info in recent:
                print(f"  {info['name']:40s} {info.get('content', ''):30s} {info.get('created', '')[:10]}")
    else:
        build_index(batch_size=args.batch_size)
