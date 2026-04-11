"""
graph_builder.py
从 GCS 加载三元组 JSONL，构建 NetworkX 知识图谱，保存到本地。

数据来源：
  - gs://xhs-humor-data/data/input_data/graphrag_ready_*.jsonl   （已合并的）
  - gs://xhs-humor-data/data/input_data/checkpoints/脱口秀大咖/  （还没合并的 checkpoint）
"""

import json
import pickle
import os
import networkx as nx
from google.cloud import storage
from tqdm import tqdm
from collections import Counter

# ==================== 配置 ====================
BUCKET_NAME = "xhs-humor-data"
MERGED_FILES = [
    "data/input_data/graphrag_ready_妈的欧洲账本.jsonl",
]
CHECKPOINT_PREFIXES = [
    "data/input_data/checkpoints/脱口秀大咖/",
    "data/input_data/checkpoints/脱口秀集锦/",
]
OUTPUT_PATH = "data/knowledge_graph.pkl"

# 高价值 relation（Paper 2：relation 类型决定笑点强度）
HIGH_VALUE_RELATIONS = {
    "对立于", "反讽", "讽刺", "本质是", "实际是", "现实是", "期待是", "期待是人生",
    "原因是", "目的是", "目的", "真实目的", "导致", "象征", "被视为", "被认为是",
    "等同于", "等于", "感觉像", "伴随着", "实现条件是", "现代处理方式", "失去",
    "成为", "具有特质", "和老师一样", "的特征是", "冲突于", "矛盾于", "反而",
    "却是", "意味着", "暗示", "讽刺地",
}

# 噪声节点（过滤掉）
NOISE_NODES = {"我", "你", "他", "她", "它", "我们", "你们", "他们", "这", "那", "的"}

# ==================== 初始化 ====================
PROJECT_ID = "gen-lang-client-0577448366"
storage_client = storage.Client(project=PROJECT_ID)
bucket = storage_client.bucket(BUCKET_NAME)


def load_triples_from_blob(blob):
    """从单个 blob 读取三元组列表"""
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


def build_graph():
    G = nx.DiGraph()
    all_triples = []

    # 1. 加载已合并的 JSONL
    print("加载已合并文件...")
    for path in MERGED_FILES:
        blob = bucket.blob(path)
        if not blob.exists():
            print(f"  跳过（不存在）: {path}")
            continue
        triples = load_triples_from_blob(blob)
        all_triples.extend(triples)
        print(f"  {path.split('/')[-1]}: {len(triples)} 条")

    # 2. 加载 checkpoint 目录（未合并的）
    print("加载 checkpoint 文件...")
    for prefix in CHECKPOINT_PREFIXES:
        blobs = list(bucket.list_blobs(prefix=prefix))
        for blob in tqdm(blobs, desc=prefix.split("/")[-2], unit="文件"):
            triples = load_triples_from_blob(blob)
            all_triples.extend(triples)

    print(f"\n共加载 {len(all_triples)} 条三元组")

    # 3. 建图
    print("构建知识图谱...")
    for t in all_triples:
        subj = t["subject"].strip()
        rel = t["relation"].strip()
        obj = t["object"].strip()
        source_type = t.get("source_type", "")

        # 过滤噪声节点
        if subj in NOISE_NODES or obj in NOISE_NODES:
            continue

        is_high_value = rel in HIGH_VALUE_RELATIONS

        # 节点：存 source_type 集合
        for node in (subj, obj):
            if not G.has_node(node):
                G.add_node(node, sources=set(), degree_high=0)
            G.nodes[node]["sources"].add(source_type)

        # 边：可以有多条同样的 (subj, obj)，用 key 区分
        if G.has_edge(subj, obj):
            # 更新已有边
            G[subj][obj]["relations"].append(rel)
            if is_high_value:
                G[subj][obj]["high_value"] = True
        else:
            G.add_edge(subj, obj,
                       relations=[rel],
                       high_value=is_high_value,
                       source_type=source_type)

        if is_high_value:
            G.nodes[subj]["degree_high"] = G.nodes[subj].get("degree_high", 0) + 1
            G.nodes[obj]["degree_high"] = G.nodes[obj].get("degree_high", 0) + 1

    return G, all_triples


def print_stats(G, all_triples):
    print(f"\n{'='*50}")
    print(f"图谱统计")
    print(f"{'='*50}")
    print(f"节点数: {G.number_of_nodes()}")
    print(f"边数:   {G.number_of_edges()}")
    print(f"三元组总数: {len(all_triples)}")

    high_value = [(u, v, d) for u, v, d in G.edges(data=True) if d.get("high_value")]
    print(f"高价值边数: {len(high_value)}")

    # relation 分布
    rel_counter = Counter()
    for t in all_triples:
        rel_counter[t["relation"]] += 1
    print(f"\nTop 15 relation 类型:")
    for rel, cnt in rel_counter.most_common(15):
        marker = "⭐" if rel in HIGH_VALUE_RELATIONS else "  "
        print(f"  {marker} {rel}: {cnt}")

    # 度数最高的节点
    top_nodes = sorted(G.nodes(), key=lambda n: G.degree(n), reverse=True)[:10]
    print(f"\nTop 10 高度数节点:")
    for n in top_nodes:
        print(f"  {n}: degree={G.degree(n)}, high_value_degree={G.nodes[n].get('degree_high', 0)}")


def main():
    os.makedirs("data", exist_ok=True)

    G, all_triples = build_graph()
    print_stats(G, all_triples)

    # 保存
    with open(OUTPUT_PATH, "wb") as f:
        pickle.dump(G, f)
    print(f"\n图谱已保存: {OUTPUT_PATH}")
    print(f"加载方式: G = pickle.load(open('{OUTPUT_PATH}', 'rb'))")


if __name__ == "__main__":
    main()
