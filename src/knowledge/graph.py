"""
graph.py — 统一知识图谱：构建、标注、查询

一键运行 python graph.py 完成:
  1. 加载 GCS 已有三元组（妈的欧洲账本、脱口秀大咖/集锦、YouTube）
  2. 扫描 GCS raw_data/ 找新的未处理文本 → Gemini 提取三元组 → checkpoint
  3. 加载本地外部数据（ConceptNet/歇后语/成语/谐音）
  4. 合并所有三元组 → 构建 NetworkX DiGraph
  5. 标注节点属性（情感/词林）
  6. 计算 humor_weight
  7. 保存 pickle

查询时: load_graph() → find_humor_slots() / get_subgraph_triples()
"""

import json
import os
import re
import pickle
import time
import logging
from collections import Counter

import networkx as nx
from tqdm import tqdm

# ==================== 路径 ====================

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.join(_THIS_DIR, "..", "..")
DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
GRAPH_PATH = os.path.join(DATA_DIR, "cache", "knowledge_graph.pkl")
EXTERNAL_TRIPLES_DIR = os.path.join(DATA_DIR, "external_triples")
ANNOTATIONS_DIR = os.path.join(DATA_DIR, "annotations")

# ==================== GCS 配置 ====================

BUCKET_NAME = "xhs-humor-data"
PROJECT_ID = "gen-lang-client-0577448366"

# GCS 路径（外部数据 + 标注 + 图谱缓存）
GCS_EXTERNAL_TRIPLES_PREFIX = "data/external_triples/"
GCS_ANNOTATIONS_PREFIX = "data/annotations/"
GCS_GRAPH_PATH = "data/cache/knowledge_graph.pkl"
GCS_TOPIC_POOL_PATH = "data/topic_pool.json"

# 已合并的三元组文件（GCS）
MERGED_FILES = [
    "data/input_data/graphrag_ready_妈的欧洲账本.jsonl",
]
# checkpoint 目录（GCS）
CHECKPOINT_PREFIXES = [
    "data/input_data/checkpoints/脱口秀大咖/",
    "data/input_data/checkpoints/脱口秀集锦/",
]
YOUTUBE_CHECKPOINT_PREFIX = "data/input_data/checkpoints/youtube_脱口秀/"

# 待提取的原始文本（GCS）
RAW_SOURCES = {
    "脱口秀大咖": "data/raw_data/脱口秀大咖/",
    "脱口秀集锦": "data/raw_data/脱口秀集锦/",
}
GROUPED_RAW_SOURCES = {
    "妈的欧洲账本": "data/raw_data/妈的欧洲账本/",
}
YOUTUBE_RAW_PREFIX = "data/raw_data/youtube_脱口秀/"

# ==================== 常量 ====================

# 冲突性 relation（最高幽默价值，+5 分）
CONFLICT_RELATIONS = {
    "对立于", "反讽", "讽刺", "本质是", "实际是", "现实是", "冲突于", "矛盾于",
    "反而", "却是", "讽刺地", "被视为", "被认为是", "等同于", "等于",
}

# 因果/目的 relation（高幽默价值，+3 分）
CAUSAL_RELATIONS_SET = {
    "期待是", "期待是人生", "目的是", "目的", "真实目的", "导致",
    "原因是", "象征", "意味着", "暗示", "感觉像", "失去",
    "引起渴望", "被阻碍于", "不想要", "渴望",
}

# 结构性 relation（歇后语/谐音，+3 分）
STRUCTURAL_RELATIONS = {
    "歇后语", "谐音于",
}

# 低价值 relation（ConceptNet 废话边，降到 0 分）
LOW_VALUE_RELATIONS = {
    "能够", "是一种", "用于", "属于", "方式是", "类似于",
    "前提是", "首先", "最终", "成语含义", "成语出处",
}

# 合并 HIGH_VALUE（向后兼容，用于 is_high_value 判断）
HIGH_VALUE_RELATIONS = CONFLICT_RELATIONS | CAUSAL_RELATIONS_SET | STRUCTURAL_RELATIONS

NOISE_NODES = {"我", "你", "他", "她", "它", "我们", "你们", "他们", "这", "那", "的", "了", "是"}

# 幽默领域来源（边加分用）
# 数据源优先级权重（越高越好）
SOURCE_WEIGHTS = {
    "妈的欧洲账本": 5.0,       # 真人发疯文学，最高价值
    "脱口秀大咖": 4.0,         # 真人脱口秀
    "脱口秀集锦": 4.0,
    "梗库": 3.5,
    "歇后语": 3.0,             # 传统幽默结构
    "conceptnet": 1.0,         # 通用常识，量大但不直接好笑
    "成语": 0.5,               # 文化知识
    "homophone": 0.5,          # 工具性数据
}
# YouTube 脱口秀频道统一权重
YOUTUBE_SOURCE_WEIGHT = 4.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


# ==================== GCS 工具 ====================

_bucket = None


def _get_bucket():
    global _bucket
    if _bucket is None:
        from google.cloud import storage
        _bucket = storage.Client(project=PROJECT_ID).bucket(BUCKET_NAME)
    return _bucket


def _sync_from_gcs(gcs_path, local_path):
    """从 GCS 下载到本地（如果本地不存在）"""
    if os.path.exists(local_path):
        return True
    try:
        bucket = _get_bucket()
        blob = bucket.blob(gcs_path)
        if not blob.exists():
            return False
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        blob.download_to_filename(local_path)
        size = os.path.getsize(local_path) / 1024 / 1024
        print(f"  GCS → 本地: {gcs_path} ({size:.1f}MB)")
        return True
    except Exception as e:
        logging.warning(f"GCS 下载失败 {gcs_path}: {e}")
        return False


def _upload_to_gcs(local_path, gcs_path):
    """上传本地文件到 GCS"""
    try:
        bucket = _get_bucket()
        bucket.blob(gcs_path).upload_from_filename(local_path)
        size = os.path.getsize(local_path) / 1024 / 1024
        print(f"  本地 → GCS: {gcs_path} ({size:.1f}MB)")
    except Exception as e:
        logging.warning(f"GCS 上传失败 {gcs_path}: {e}")


def _load_triples_from_blob(blob):
    triples = []
    content = blob.download_as_text(encoding="utf-8").strip()
    for line in content.split("\n"):
        if not line:
            continue
        try:
            t = json.loads(line)
            if all(k in t for k in ("subject", "relation", "object")):
                triples.append(t)
        except json.JSONDecodeError:
            pass
    return triples


# ==================== 第1步: 加载 GCS 已有三元组 ====================

def load_gcs_triples():
    """从 GCS 加载所有已有三元组（已合并文件 + checkpoint）"""
    bucket = _get_bucket()
    all_triples = []

    print("=" * 50)
    print("第1步: 加载 GCS 已有三元组")

    # 已合并文件
    for path in MERGED_FILES:
        blob = bucket.blob(path)
        if not blob.exists():
            print(f"  跳过（不存在）: {path}")
            continue
        triples = _load_triples_from_blob(blob)
        all_triples.extend(triples)
        print(f"  {path.split('/')[-1]}: {len(triples)} 条")

    # checkpoint 文件
    for prefix in CHECKPOINT_PREFIXES:
        blobs = list(bucket.list_blobs(prefix=prefix))
        count = 0
        for blob in tqdm(blobs, desc=f"  {prefix.split('/')[-2]}", unit="文件", leave=False):
            triples = _load_triples_from_blob(blob)
            all_triples.extend(triples)
            count += len(triples)
        print(f"  {prefix.split('/')[-2]}: {count} 条")

    # YouTube checkpoint
    yt_blobs = list(bucket.list_blobs(prefix=YOUTUBE_CHECKPOINT_PREFIX))
    if yt_blobs:
        count = 0
        for blob in tqdm(yt_blobs, desc="  youtube_脱口秀", unit="文件", leave=False):
            triples = _load_triples_from_blob(blob)
            for t in triples:
                if not t.get("source_type", "").startswith("youtube_"):
                    channel = blob.name.replace(YOUTUBE_CHECKPOINT_PREFIX, "").split("/")[0]
                    t["source_type"] = f"youtube_脱口秀/{channel}"
            all_triples.extend(triples)
            count += len(triples)
        print(f"  youtube_脱口秀: {count} 条")
    else:
        print("  youtube_脱口秀: 暂无数据")

    print(f"  GCS 总计: {len(all_triples)} 条")
    return all_triples


# ==================== 第2步: 扫描新文本 → Gemini 提取 ====================

EXTRACT_PROMPT = """你是一个知识图谱专家，专门从幽默文本中提取逻辑冲突三元组。

请从以下文本中提取三元组，格式严格为 JSON 数组：
[
  {{"subject": "主体", "relation": "关系", "object": "客体"}},
  ...
]

重点提取：
1. 社会角色之间的对立关系（e.g. 程序员 vs CEO）
2. 期待与现实的冲突（e.g. 哲学家 导致 贫穷）
3. 荒诞的因果关系（e.g. 结婚 目的是 去欧洲旅行）
4. 概念的反转（e.g. 自由 等于 贫穷）

只输出 JSON 数组，不要其他文字。

文本：
{text}"""


def _extract_triples_gemini(text, max_retries=3):
    """调用 Gemini 从文本提取三元组"""
    from google import genai
    from google.genai import errors as genai_errors

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logging.warning("GEMINI_API_KEY 未设置，跳过文本提取")
        return []

    client = genai.Client(api_key=api_key)
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-pro",
                contents=EXTRACT_PROMPT.format(text=text[:3000]),
            )
            if not response.text:
                return []
            raw = response.text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw)
        except json.JSONDecodeError:
            return []
        except genai_errors.ClientError as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e) or "503" in str(e) or "UNAVAILABLE" in str(e):
                wait = 10 * (2 ** attempt)
                logging.warning(f"限流/过载，等待 {wait}s...")
                time.sleep(wait)
            else:
                logging.error(f"Gemini 错误: {e}")
                return []
        except Exception as e:
            logging.error(f"提取错误: {e}")
            return []
    return []


def _get_post_name(filename):
    return re.sub(r"_\d+\.jpg\.txt$", "", filename)


def extract_new_triples():
    """扫描 GCS raw_data/ 找新文本，提取三元组，checkpoint 续传"""
    bucket = _get_bucket()
    new_triples = []

    print("\n" + "=" * 50)
    print("第2步: 扫描新文本 → Gemini 提取三元组")

    # 单文件 source（脱口秀大咖/集锦）
    for source_name, source_prefix in RAW_SOURCES.items():
        checkpoint_prefix = f"data/input_data/checkpoints/{source_name}/"
        blobs = [b for b in bucket.list_blobs(prefix=source_prefix) if b.name.endswith(".txt")]
        done_files = set(
            b.name.split("/")[-1].replace(".jsonl", "")
            for b in bucket.list_blobs(prefix=checkpoint_prefix)
        )
        pending = [b for b in blobs if b.name.split("/")[-1] not in done_files]
        if not pending:
            print(f"  {source_name}: 无新文件")
            continue

        print(f"  {source_name}: {len(pending)} 个新文件")
        for blob in tqdm(pending, desc=f"  {source_name}", unit="文件"):
            try:
                text = blob.download_as_text(encoding="utf-8")
                if len(text.strip()) < 50:
                    continue
                triples = _extract_triples_gemini(text)
                for t in triples:
                    t["source_file"] = blob.name
                    t["source_type"] = source_name
                new_triples.extend(triples)

                # checkpoint
                file_key = blob.name.split("/")[-1]
                tmp_path = f"/tmp/{file_key}.jsonl"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    for t in triples:
                        f.write(json.dumps(t, ensure_ascii=False) + "\n")
                bucket.blob(checkpoint_prefix + file_key + ".jsonl").upload_from_filename(tmp_path)
                time.sleep(0.5)
            except Exception as e:
                logging.error(f"处理失败 {blob.name}: {e}")

    # 分组 source（妈的欧洲账本）
    for source_name, source_prefix in GROUPED_RAW_SOURCES.items():
        checkpoint_prefix = f"data/input_data/checkpoints/{source_name}/"
        blobs = [b for b in bucket.list_blobs(prefix=source_prefix) if b.name.endswith(".txt")]
        posts = {}
        for blob in blobs:
            filename = blob.name.split("/")[-1]
            post_name = _get_post_name(filename)
            posts.setdefault(post_name, []).append(blob)
        for post_name in posts:
            posts[post_name].sort(
                key=lambda b: int(re.search(r"_(\d+)\.jpg\.txt$", b.name.split("/")[-1]).group(1))
                if re.search(r"_(\d+)\.jpg\.txt$", b.name.split("/")[-1]) else 0
            )
        done_posts = set(
            b.name.split("/")[-1].replace(".jsonl", "")
            for b in bucket.list_blobs(prefix=checkpoint_prefix)
        )
        pending_posts = {k: v for k, v in posts.items() if k not in done_posts}
        if not pending_posts:
            print(f"  {source_name}: 无新帖子")
            continue

        print(f"  {source_name}: {len(pending_posts)} 篇新帖子")
        for post_name, blobs_list in tqdm(pending_posts.items(), desc=f"  {source_name}", unit="篇"):
            try:
                parts = []
                for blob in blobs_list:
                    text = blob.download_as_text(encoding="utf-8").strip()
                    if text:
                        parts.append(text)
                merged_text = "\n".join(parts)
                if len(merged_text) < 20:
                    bucket.blob(checkpoint_prefix + post_name + ".jsonl").upload_from_string("")
                    continue
                triples = _extract_triples_gemini(merged_text)
                for t in triples:
                    t["source_file"] = blobs_list[0].name
                    t["source_type"] = source_name
                    t["post_name"] = post_name
                new_triples.extend(triples)

                tmp_path = f"/tmp/{post_name}.jsonl"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    for t in triples:
                        f.write(json.dumps(t, ensure_ascii=False) + "\n")
                bucket.blob(checkpoint_prefix + post_name + ".jsonl").upload_from_filename(tmp_path)
                time.sleep(0.5)
            except Exception as e:
                logging.error(f"处理失败 {post_name}: {e}")

    # YouTube 频道（直接列所有txt，按频道名分组）
    all_yt_blobs = [b for b in bucket.list_blobs(prefix=YOUTUBE_RAW_PREFIX) if b.name.endswith(".txt")]
    yt_channels = {}
    for blob in all_yt_blobs:
        # 路径: data/raw_data/youtube_脱口秀/{频道名}/{文件}.txt
        parts = blob.name.replace(YOUTUBE_RAW_PREFIX, "").split("/")
        if len(parts) >= 2:
            channel = parts[0]
            yt_channels.setdefault(channel, []).append(blob)

    if not yt_channels:
        print("  youtube_脱口秀: 无txt文件")

    for channel_name, blobs in yt_channels.items():
        source_name = f"youtube_脱口秀/{channel_name}"
        checkpoint_prefix = f"data/input_data/checkpoints/{source_name}/"
        done_files = set(
            b.name.split("/")[-1].replace(".jsonl", "")
            for b in bucket.list_blobs(prefix=checkpoint_prefix)
        )
        pending = [b for b in blobs if b.name.split("/")[-1] not in done_files]
        if not pending:
            print(f"  {source_name}: 无新文件")
            continue

        print(f"  {source_name}: {len(pending)} 个新文件")
        for blob in tqdm(pending, desc=f"  {channel_name}", unit="文件"):
            try:
                text = blob.download_as_text(encoding="utf-8")
                if len(text.strip()) < 50:
                    continue
                triples = _extract_triples_gemini(text)
                for t in triples:
                    t["source_file"] = blob.name
                    t["source_type"] = source_name
                new_triples.extend(triples)

                file_key = blob.name.split("/")[-1]
                tmp_path = f"/tmp/{file_key}.jsonl"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    for t in triples:
                        f.write(json.dumps(t, ensure_ascii=False) + "\n")
                bucket.blob(checkpoint_prefix + file_key + ".jsonl").upload_from_filename(tmp_path)
                time.sleep(0.5)
            except Exception as e:
                logging.error(f"处理失败 {blob.name}: {e}")

    print(f"  新提取: {len(new_triples)} 条")
    return new_triples


# ==================== 第3步: 加载本地外部数据 ====================

def load_external_triples():
    """从 GCS 或本地 data/external_triples/ 加载所有 JSONL 文件"""
    all_triples = []

    print("\n" + "=" * 50)
    print("第3步: 加载外部数据（GCS → 本地缓存）")

    # 先从 GCS 同步到本地
    os.makedirs(EXTERNAL_TRIPLES_DIR, exist_ok=True)
    try:
        bucket = _get_bucket()
        blobs = list(bucket.list_blobs(prefix=GCS_EXTERNAL_TRIPLES_PREFIX))
        for blob in blobs:
            if blob.name.endswith(".jsonl"):
                filename = blob.name.split("/")[-1]
                local_path = os.path.join(EXTERNAL_TRIPLES_DIR, filename)
                _sync_from_gcs(blob.name, local_path)
    except Exception as e:
        logging.warning(f"GCS 同步外部数据失败: {e}，使用本地缓存")

    if not os.path.exists(EXTERNAL_TRIPLES_DIR):
        print("  无外部数据，跳过。")
        return all_triples

    for filename in sorted(os.listdir(EXTERNAL_TRIPLES_DIR)):
        if not filename.endswith(".jsonl"):
            continue
        filepath = os.path.join(EXTERNAL_TRIPLES_DIR, filename)
        count = 0
        with open(filepath, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    t = json.loads(line)
                    if all(k in t for k in ("subject", "relation", "object")):
                        all_triples.append(t)
                        count += 1
                except json.JSONDecodeError:
                    pass
        print(f"  {filename}: {count} 条")

    print(f"  外部数据总计: {len(all_triples)} 条")
    return all_triples


# ==================== 第4步: 构建图谱 ====================

def build_networkx_graph(all_triples):
    """从三元组列表构建 NetworkX DiGraph"""
    print("\n" + "=" * 50)
    print("第4步: 构建 NetworkX 图谱")

    G = nx.DiGraph()
    for t in all_triples:
        if "subject" not in t or "object" not in t:
            continue
        rel_key = "relation" if "relation" in t else "relations"
        subj = str(t["subject"]).strip()
        rel = str(t.get(rel_key, "related")).strip()
        obj = str(t["object"]).strip()
        source_type = t.get("source_type", "unknown")

        if subj in NOISE_NODES or obj in NOISE_NODES:
            continue
        if not subj or not obj or len(subj) > 30 or len(obj) > 30:
            continue

        is_high_value = rel in HIGH_VALUE_RELATIONS or t.get("high_value", False)

        for node in (subj, obj):
            if not G.has_node(node):
                G.add_node(node, sources=set(), degree_high=0)
            G.nodes[node]["sources"].add(source_type)

        if G.has_edge(subj, obj):
            edge = G[subj][obj]
            edge["relations"].append(rel)
            edge["sources"].add(source_type)
            if is_high_value:
                edge["high_value"] = True
        else:
            G.add_edge(subj, obj,
                       relations=[rel],
                       sources={source_type},
                       high_value=is_high_value,
                       source_type=source_type)

        if is_high_value:
            G.nodes[subj]["degree_high"] = G.nodes[subj].get("degree_high", 0) + 1
            G.nodes[obj]["degree_high"] = G.nodes[obj].get("degree_high", 0) + 1

    print(f"  节点数: {G.number_of_nodes()}")
    print(f"  边数: {G.number_of_edges()}")
    return G


# ==================== 第5步: 标注节点属性 ====================

def annotate_nodes(G):
    """加载情感词典和词林标注，写入节点属性（GCS → 本地缓存）"""
    print("\n" + "=" * 50)
    print("第5步: 标注节点属性")

    # 从 GCS 同步标注文件
    os.makedirs(ANNOTATIONS_DIR, exist_ok=True)
    _sync_from_gcs("data/annotations/sentiment.json", os.path.join(ANNOTATIONS_DIR, "sentiment.json"))
    _sync_from_gcs("data/annotations/cilin.json", os.path.join(ANNOTATIONS_DIR, "cilin.json"))

    # 情感标注
    sentiment_path = os.path.join(ANNOTATIONS_DIR, "sentiment.json")
    if os.path.exists(sentiment_path):
        with open(sentiment_path, encoding="utf-8") as f:
            sentiment_dict = json.load(f)
        matched = 0
        for node in G.nodes():
            if node in sentiment_dict:
                info = sentiment_dict[node]
                G.nodes[node]["sentiment"] = info["sentiment"]
                G.nodes[node]["sentiment_strength"] = info["strength"]
                G.nodes[node]["sentiment_category"] = info["category"]
                matched += 1
        print(f"  情感标注: {matched}/{G.number_of_nodes()} 节点命中")
    else:
        print(f"  情感标注: 未找到 {sentiment_path}，跳过。运行 build_annotations.py。")

    # 词林标注
    cilin_path = os.path.join(ANNOTATIONS_DIR, "cilin.json")
    if os.path.exists(cilin_path):
        with open(cilin_path, encoding="utf-8") as f:
            cilin_dict = json.load(f)
        matched = 0
        for node in G.nodes():
            if node in cilin_dict:
                info = cilin_dict[node]
                G.nodes[node]["cilin_code"] = info["cilin_code"]
                G.nodes[node]["domain"] = info["domain"]
                matched += 1
        print(f"  词林标注: {matched}/{G.number_of_nodes()} 节点命中")
    else:
        print(f"  词林标注: 未找到 {cilin_path}，跳过。运行 build_annotations.py。")


# ==================== 第6步: 计算 humor_weight ====================

def calc_humor_weights(G):
    """为每条边计算综合幽默权重"""
    print("\n" + "=" * 50)
    print("第6步: 计算 humor_weight")

    for u, v, data in G.edges(data=True):
        w = 0.0
        node_a = G.nodes[u]
        node_b = G.nodes[v]

        # 1. relation 类型分（三级：冲突 > 因果 > 结构 > 低价值）
        rels = data.get("relations", [])
        rel_score = 0
        is_low_value = True
        for rel in rels:
            if rel in CONFLICT_RELATIONS:
                rel_score = max(rel_score, 5.0)
                is_low_value = False
            elif rel in CAUSAL_RELATIONS_SET:
                rel_score = max(rel_score, 3.0)
                is_low_value = False
            elif rel in STRUCTURAL_RELATIONS:
                rel_score = max(rel_score, 3.0)
                is_low_value = False
            elif rel in LOW_VALUE_RELATIONS:
                rel_score = max(rel_score, 0.0)  # 废话边：0 分
            else:
                rel_score = max(rel_score, 1.0)  # 未分类：1 分
                is_low_value = False
        w += rel_score

        # 2. 来源加分（分级权重，取最高来源）
        edge_sources = data.get("sources", set())
        if isinstance(edge_sources, set):
            source_bonus = 0
            for src in edge_sources:
                if src in SOURCE_WEIGHTS:
                    source_bonus = max(source_bonus, SOURCE_WEIGHTS[src])
                elif src.startswith("youtube_"):
                    source_bonus = max(source_bonus, YOUTUBE_SOURCE_WEIGHT)
            w += source_bonus

        # 3. 情感对比分（两端极性相反 × 强度差）
        sa = node_a.get("sentiment")
        sb = node_b.get("sentiment")
        if sa is not None and sb is not None:
            polarity_diff = abs(sa - sb)
            w += polarity_diff * 1.5

        # 4. 跨域分（词林大类不同）
        da = node_a.get("domain")
        db = node_b.get("domain")
        if da and db and da != db:
            w += 2.0

        # 5. 度数加成（连接越多的节点越有价值）
        degree_bonus = min((G.degree(u) + G.degree(v)) * 0.05, 1.5)
        w += degree_bonus

        # 低价值 relation 封顶
        if is_low_value:
            w = min(w, 2.0)

        # ConceptNet-only 因果边降权（导致→下班 这种常识废话）
        # 只有当边完全来自 conceptnet/成语/homophone 且没有幽默来源时才降
        edge_sources = data.get("sources", set())
        if isinstance(edge_sources, list):
            edge_sources = set(edge_sources)
        humor_srcs = {s for s, sw in SOURCE_WEIGHTS.items() if sw >= 3.0}
        has_humor = bool(edge_sources & humor_srcs) or any(s.startswith("youtube_") for s in edge_sources)
        if not has_humor and rel_score <= 3.0:
            # 纯 ConceptNet 的非冲突边：封顶 4.0
            w = min(w, 4.0)

        data["humor_weight"] = round(w, 2)

    # 统计
    weights = [d["humor_weight"] for _, _, d in G.edges(data=True)]
    if weights:
        avg = sum(weights) / len(weights)
        top = sorted(weights, reverse=True)[:10]
        print(f"  平均 humor_weight: {avg:.2f}")
        print(f"  Top 10: {top}")


# ==================== 第7步: 保存 ====================

def save_graph(G):
    os.makedirs(os.path.dirname(GRAPH_PATH), exist_ok=True)
    for node in G.nodes():
        if isinstance(G.nodes[node].get("sources"), set):
            G.nodes[node]["sources"] = list(G.nodes[node]["sources"])
    for u, v, data in G.edges(data=True):
        if isinstance(data.get("sources"), set):
            data["sources"] = list(data["sources"])
    with open(GRAPH_PATH, "wb") as f:
        pickle.dump(G, f)
    print(f"\n图谱已保存: {GRAPH_PATH}")
    # 同步到 GCS
    _upload_to_gcs(GRAPH_PATH, GCS_GRAPH_PATH)


# ==================== 统计 ====================

def print_stats(G, all_triples):
    print(f"\n{'=' * 50}")
    print("图谱统计")
    print(f"{'=' * 50}")
    print(f"节点数: {G.number_of_nodes()}")
    print(f"边数:   {G.number_of_edges()}")
    print(f"三元组总数: {len(all_triples)}")

    high_value = sum(1 for _, _, d in G.edges(data=True) if d.get("high_value"))
    print(f"高价值边数: {high_value}")

    # 按来源统计
    source_counter = Counter()
    for t in all_triples:
        source_counter[t.get("source_type", "unknown")] += 1
    print(f"\n按数据源:")
    for source, cnt in source_counter.most_common():
        print(f"  {source}: {cnt}")

    # Top relation
    rel_counter = Counter()
    for t in all_triples:
        rel_counter[t["relation"]] += 1
    print(f"\nTop 15 relation:")
    for rel, cnt in rel_counter.most_common(15):
        marker = "*" if rel in HIGH_VALUE_RELATIONS else " "
        print(f"  {marker} {rel}: {cnt}")

    # Top 10 幽默节点（按最高 humor_weight 边排序，只选幽默来源的节点）
    humor_srcs = {s for s, w in SOURCE_WEIGHTS.items() if w >= 3.0}
    node_scores = []
    for n in G.nodes():
        srcs = G.nodes[n].get("sources", set())
        if isinstance(srcs, list):
            srcs = set(srcs)
        has_humor = bool(srcs & humor_srcs) or any(s.startswith("youtube_") for s in srcs)
        if not has_humor:
            continue
        hw_edges = [d.get("humor_weight", 0) for _, _, d in G.edges(n, data=True)]
        max_hw = max(hw_edges, default=0)
        if max_hw > 0:
            # 找最强的边的详情
            best_rel = ""
            best_target = ""
            for _, obj, d in G.out_edges(n, data=True):
                if d.get("humor_weight", 0) == max_hw:
                    best_rel = d.get("relations", ["?"])[0]
                    best_target = obj
                    break
            if not best_target:
                for subj, _, d in G.in_edges(n, data=True):
                    if d.get("humor_weight", 0) == max_hw:
                        best_rel = d.get("relations", ["?"])[0]
                        best_target = subj
                        break
            node_scores.append((max_hw, n, best_rel, best_target, ",".join(srcs)))
    node_scores.sort(key=lambda x: -x[0])
    print(f"\nTop 10 Humor 节点:")
    for hw, n, rel, target, src in node_scores[:10]:
        print(f"  {n} --{rel}-> {target}  (hw={hw:.1f}, src={src})")


# ==================== 查询函数（供其他模块 import） ====================

def load_graph(path=None):
    if path is None:
        path = GRAPH_PATH
    # 本地没有则从 GCS 下载
    if not os.path.exists(path):
        _sync_from_gcs(GCS_GRAPH_PATH, path)
    with open(path, "rb") as f:
        G = pickle.load(f)
    # 恢复 sources 为 set
    for node in G.nodes():
        src = G.nodes[node].get("sources")
        if isinstance(src, list):
            G.nodes[node]["sources"] = set(src)
    for u, v, data in G.edges(data=True):
        src = data.get("sources")
        if isinstance(src, list):
            data["sources"] = set(src)
    return G


def find_topic_node(G, topic, semantic_fallback=True):
    """精确匹配 → 模糊匹配 → 语义 embedding 匹配"""
    if topic in G:
        return topic
    candidates = [n for n in G.nodes() if topic in n or n in topic]
    if candidates:
        best = max(candidates, key=lambda n: G.degree(n))
        print(f"  模糊匹配: [{topic}] -> [{best}]")
        return best
    if semantic_fallback:
        try:
            from .rag_retriever import find_similar_node
            matches = find_similar_node(topic, G, top_k=1, threshold=0.6)
            if matches:
                score, node = matches[0]
                print(f"  语义匹配: [{topic}] -> [{node}]（相似度={score:.2f}）")
                return node
        except Exception as e:
            print(f"  语义匹配失败: {e}")
    return None


def find_humor_slots(G, topic, top_k=10):
    """找最强的 Humor Slot 列表（一跳 + 二跳），基于 humor_weight"""
    node = find_topic_node(G, topic)
    if node is None:
        return []

    slots = {}

    # 一跳 out
    for _, obj, data in G.out_edges(node, data=True):
        for rel in data.get("relations", []):
            score = _score(G, obj, rel, hop=1, edge_data=data)
            _update(slots, obj, score, path=[node, obj], relation=rel)

    # 一跳 in
    for subj, _, data in G.in_edges(node, data=True):
        for rel in data.get("relations", []):
            score = _score(G, subj, rel, hop=1, edge_data=data)
            _update(slots, subj, score, path=[subj, node], relation=rel)

    # 二跳
    direct = set(G.successors(node)) | set(G.predecessors(node))
    for mid in direct:
        for _, obj, data in G.out_edges(mid, data=True):
            if obj == node or obj in direct:
                continue
            for rel in data.get("relations", []):
                score = _score(G, obj, rel, hop=2, edge_data=data)
                mid_rel = _get_rel(G, node, mid)
                _update(slots, obj, score, path=[node, mid, obj], relation=f"{mid_rel} -> {rel}")

        for subj, _, data in G.in_edges(mid, data=True):
            if subj == node or subj in direct:
                continue
            for rel in data.get("relations", []):
                score = _score(G, subj, rel, hop=2, edge_data=data)
                mid_rel = _get_rel(G, node, mid)
                _update(slots, subj, score, path=[subj, mid, node], relation=f"{rel} -> {mid_rel}")

    result = sorted(slots.values(), key=lambda x: x["score"], reverse=True)
    return result[:top_k]


def get_subgraph_triples(G, topic, humor_slot, max_triples=10):
    """提取 topic 和 humor_slot 相关的三元组，按 humor_weight 排序"""
    triples = []

    def collect(node):
        for u, v, data in G.out_edges(node, data=True):
            for rel in data.get("relations", []):
                triples.append({
                    "subject": u, "relation": rel, "object": v,
                    "high_value": rel in HIGH_VALUE_RELATIONS,
                    "humor_weight": data.get("humor_weight", 0),
                })
        for u, v, data in G.in_edges(node, data=True):
            for rel in data.get("relations", []):
                triples.append({
                    "subject": u, "relation": rel, "object": v,
                    "high_value": rel in HIGH_VALUE_RELATIONS,
                    "humor_weight": data.get("humor_weight", 0),
                })

    collect(topic)
    collect(humor_slot)

    seen, unique = set(), []
    for t in triples:
        key = (t["subject"], t["relation"], t["object"])
        if key not in seen:
            seen.add(key)
            unique.append(t)

    unique.sort(key=lambda x: x["humor_weight"], reverse=True)
    return unique[:max_triples]


def _score(G, node, relation, hop, edge_data=None):
    """基于 humor_weight 的评分"""
    hw = edge_data.get("humor_weight", 0) if edge_data else 0
    if hw == 0:
        hw = 3.0 if relation in HIGH_VALUE_RELATIONS else 0.5
    hop_decay = 1.0 if hop == 1 else 0.6
    return hw * hop_decay


def _update(slots, node, score, path, relation):
    if node not in slots or score > slots[node]["score"]:
        slots[node] = {"slot": node, "path": path, "relation": relation, "score": score}


def _get_rel(G, src, dst):
    if G.has_edge(src, dst):
        return G[src][dst].get("relations", ["?"])[0]
    if G.has_edge(dst, src):
        return G[dst][src].get("relations", ["?"])[0]
    return "?"


# ==================== 主程序 ====================

def main():
    """一键构建完整图谱"""
    # 第1步: GCS 已有三元组
    gcs_triples = load_gcs_triples()

    # 第2步: 提取新文本（断点续传）
    new_triples = extract_new_triples()

    # 第3步: 本地外部数据
    external_triples = load_external_triples()

    # 合并
    all_triples = gcs_triples + new_triples + external_triples
    print(f"\n合并总计: {len(all_triples)} 条三元组")

    # 第4步: 构建图
    G = build_networkx_graph(all_triples)

    # 第5步: 标注节点
    annotate_nodes(G)

    # 第6步: 计算 humor_weight
    calc_humor_weights(G)

    # 统计
    print_stats(G, all_triples)

    # 第7步: 保存
    save_graph(G)


if __name__ == "__main__":
    main()
