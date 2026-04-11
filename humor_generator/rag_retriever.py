"""
rag_retriever.py
基于 Gemini Embedding 的梗库向量检索，替换 joke_generator 中的关键词匹配。

流程：
  1. 首次运行：从 GCS 加载梗库，计算所有 embedding，缓存到本地
  2. 查询时：计算 query embedding，cosine similarity 找 top-k
"""

import json
import os
import sys
import time
import math
from google.cloud import storage
from google import genai
from google.genai import errors as genai_errors
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ==================== 配置 ====================
PROJECT_ID = "gen-lang-client-0577448366"
BUCKET_NAME = "xhs-humor-data"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
EMBED_MODEL = "gemini-embedding-001"

RAG_FILES = [
    "data/input_data/rag_ready_chime.jsonl",
    "data/input_data/rag_ready_十万个梗库.jsonl",
]

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
MEMES_CACHE_PATH = os.path.join(DATA_DIR, "rag_memes.json")
EMBEDDINGS_CACHE_PATH = os.path.join(DATA_DIR, "rag_embeddings.json")

# ==================== 初始化 ====================
client = genai.Client(api_key=GEMINI_API_KEY)
storage_client = storage.Client(project=PROJECT_ID)
bucket = storage_client.bucket(BUCKET_NAME)


# ==================== 梗库加载 ====================

def load_memes():
    """从 GCS 加载所有梗，返回文本列表"""
    if os.path.exists(MEMES_CACHE_PATH):
        with open(MEMES_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    print("从 GCS 加载梗库...")
    memes = []
    for path in RAG_FILES:
        blob = bucket.blob(path)
        if not blob.exists():
            print(f"  跳过（不存在）: {path}")
            continue
        for line in blob.download_as_text(encoding="utf-8").strip().split("\n"):
            if not line:
                continue
            try:
                item = json.loads(line)
                text = item.get("page_content", "").strip()
                if text:
                    memes.append(text)
            except json.JSONDecodeError:
                pass
    print(f"梗库加载完成：{len(memes)} 条")

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(MEMES_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(memes, f, ensure_ascii=False)
    return memes


# ==================== Embedding ====================

def embed_texts(texts, batch_size=20):
    """批量计算 embedding，带限流重试"""
    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        for attempt in range(4):
            try:
                result = client.models.embed_content(
                    model=EMBED_MODEL,
                    contents=batch,
                )
                all_embeddings.extend([e.values for e in result.embeddings])
                time.sleep(0.3)  # 避免限流
                break
            except genai_errors.ClientError as e:
                if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                    wait = 10 * (2 ** attempt)
                    print(f"  限流，等待 {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"  Embedding 错误: {e}")
                    all_embeddings.extend([[0.0] * 3072] * len(batch))
                    break
    return all_embeddings


def load_or_build_embeddings(memes):
    """加载缓存 embedding，若不存在则重新计算"""
    if os.path.exists(EMBEDDINGS_CACHE_PATH):
        with open(EMBEDDINGS_CACHE_PATH, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if len(cached) == len(memes):
            return cached
        print(f"缓存数量不匹配（{len(cached)} vs {len(memes)}），重新计算...")

    print(f"计算 {len(memes)} 条梗的 embedding（首次约需几分钟）...")
    # 只用前200字节做 embedding，节省 token
    truncated = [m[:500] for m in memes]
    embeddings = embed_texts(truncated, batch_size=20)
    print(f"计算完成，共 {len(embeddings)} 条")

    with open(EMBEDDINGS_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(embeddings, f)
    return embeddings


# ==================== 检索 ====================

def cosine_similarity(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def retrieve(topic, humor_slot, top_k=3, memes=None, embeddings=None):
    """
    给定话题和 Humor Slot，检索最相关的梗。

    参数：
      memes, embeddings 可预先传入避免重复加载（批量调用时推荐）
    返回：
      top_k 条梗文本列表
    """
    if memes is None:
        memes = load_memes()
    if embeddings is None:
        embeddings = load_or_build_embeddings(memes)

    query = f"{topic} {humor_slot}"
    query_emb_result = client.models.embed_content(model=EMBED_MODEL, contents=query)
    query_emb = query_emb_result.embeddings[0].values

    scored = [
        (cosine_similarity(query_emb, emb), meme)
        for emb, meme in zip(embeddings, memes)
    ]
    scored.sort(reverse=True)
    return [meme for _, meme in scored[:top_k]]


# ==================== 主程序（演示） ====================

def main():
    print("加载梗库...")
    memes = load_memes()
    print("构建/加载 embedding 索引...")
    embeddings = load_or_build_embeddings(memes)

    test_queries = [
        ("结婚", "有空时顺便做的事"),
        ("贫穷", "思考哲学"),
        ("上班", "自由职业"),
    ]

    for topic, slot in test_queries:
        print(f"\n{'='*50}")
        print(f"Query: 话题={topic}  Slot={slot}")
        results = retrieve(topic, slot, top_k=2, memes=memes, embeddings=embeddings)
        for i, r in enumerate(results):
            print(f"\n  #{i+1}: {r[:120]}...")


if __name__ == "__main__":
    main()
