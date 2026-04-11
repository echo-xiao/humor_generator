"""
humor_slot_finder.py
从知识图谱中找 Humor Slot。

理论依据：
  - Paper 2 (Let's be Humorous)：Humor Slot = 两个语义脚本的 incongruity（不一致）点
  - 核心逻辑：高价值 relation（导致/等同于/对立于/现实是...）= 天然的笑点连接

由于图谱较稀疏，不使用 Louvain 社区划分，
直接通过高价值 relation 找跨语义领域的冲突节点。
"""

import pickle
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from graph_builder import HIGH_VALUE_RELATIONS

GRAPH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "knowledge_graph.pkl")


def load_graph(path=None):
    if path is None:
        path = GRAPH_PATH
    with open(path, "rb") as f:
        return pickle.load(f)


def find_topic_node(G, topic):
    """精确匹配或模糊匹配话题节点"""
    if topic in G:
        return topic
    candidates = [n for n in G.nodes() if topic in n or n in topic]
    if candidates:
        best = max(candidates, key=lambda n: G.degree(n))
        print(f"  模糊匹配: [{topic}] → [{best}]")
        return best
    return None


def find_humor_slots(G, topic, top_k=10):
    """
    给定话题，找最强的 Humor Slot 列表。

    策略：
      1. 一跳：topic 直连的高价值 relation 边
      2. 二跳：topic → 中间节点 → 目标节点（至少一条高价值边）

    返回列表，每项：{
        "slot": 节点名,
        "path": 路径,
        "relation": 关键 relation,
        "score": 综合评分,
    }
    """
    node = find_topic_node(G, topic)
    if node is None:
        return []

    slots = {}

    # ---- 一跳：topic 出边 ----
    for _, obj, data in G.out_edges(node, data=True):
        for rel in data.get("relations", []):
            score = _score(G, obj, rel, hop=1)
            _update(slots, obj, score, path=[node, obj], relation=rel)

    # ---- 一跳：topic 入边 ----
    for subj, _, data in G.in_edges(node, data=True):
        for rel in data.get("relations", []):
            score = _score(G, subj, rel, hop=1)
            _update(slots, subj, score, path=[subj, node], relation=rel)

    # ---- 二跳：经过中间节点 ----
    direct = set(G.successors(node)) | set(G.predecessors(node))
    for mid in direct:
        for _, obj, data in G.out_edges(mid, data=True):
            if obj == node or obj in direct:
                continue
            for rel in data.get("relations", []):
                score = _score(G, obj, rel, hop=2)
                mid_rel = _get_rel(G, node, mid)
                _update(slots, obj, score, path=[node, mid, obj], relation=f"{mid_rel} → {rel}")

        for subj, _, data in G.in_edges(mid, data=True):
            if subj == node or subj in direct:
                continue
            for rel in data.get("relations", []):
                score = _score(G, subj, rel, hop=2)
                mid_rel = _get_rel(G, node, mid)
                _update(slots, subj, score, path=[subj, mid, node], relation=f"{rel} → {mid_rel}")

    # 排序返回
    result = sorted(slots.values(), key=lambda x: x["score"], reverse=True)
    return result[:top_k]


def _score(G, node, relation, hop):
    """综合评分：高价值relation + 跳数衰减 + 节点度数"""
    base = 3.0 if relation in HIGH_VALUE_RELATIONS else 0.5
    hop_decay = 1.0 if hop == 1 else 0.6
    degree_bonus = min(G.degree(node) * 0.15, 1.5)
    return base * hop_decay + degree_bonus


def _update(slots, node, score, path, relation):
    if node not in slots or score > slots[node]["score"]:
        slots[node] = {"slot": node, "path": path, "relation": relation, "score": score}


def _get_rel(G, src, dst):
    if G.has_edge(src, dst):
        return G[src][dst].get("relations", ["?"])[0]
    if G.has_edge(dst, src):
        return G[dst][src].get("relations", ["?"])[0]
    return "?"


# ==================== 提取三元组子图 ====================

def get_subgraph_triples(G, topic, humor_slot, max_triples=10):
    """
    提取 topic 和 humor_slot 相关的三元组，用于喂给 Gemini。
    高价值 relation 优先。
    """
    triples = []

    def collect(node):
        for u, v, data in G.out_edges(node, data=True):
            for rel in data.get("relations", []):
                triples.append({"subject": u, "relation": rel, "object": v,
                                 "high_value": rel in HIGH_VALUE_RELATIONS})
        for u, v, data in G.in_edges(node, data=True):
            for rel in data.get("relations", []):
                triples.append({"subject": u, "relation": rel, "object": v,
                                 "high_value": rel in HIGH_VALUE_RELATIONS})

    collect(topic)
    collect(humor_slot)

    # 去重 + 高价值优先
    seen, unique = set(), []
    for t in triples:
        key = (t["subject"], t["relation"], t["object"])
        if key not in seen:
            seen.add(key)
            unique.append(t)

    unique.sort(key=lambda x: x["high_value"], reverse=True)
    return unique[:max_triples]


# ==================== 主程序（演示） ====================

def main():
    print("加载知识图谱...")
    G = load_graph()
    print(f"节点: {G.number_of_nodes()}, 边: {G.number_of_edges()}")

    test_topics = ["结婚", "上班", "贫穷", "脱口秀演员", "30岁"]

    for topic in test_topics:
        print(f"\n{'='*50}")
        print(f"话题: 【{topic}】")
        slots = find_humor_slots(G, topic, top_k=5)

        if not slots:
            print("  未找到 Humor Slot")
            continue

        for i, s in enumerate(slots):
            path_str = " → ".join(s["path"])
            print(f"  #{i+1} [{s['slot']}]  score={s['score']:.1f}  relation={s['relation']}")
            print(f"        路径: {path_str}")

        # 展示最佳 slot 的三元组
        best = slots[0]["slot"]
        node = find_topic_node(G, topic)
        print(f"\n  最佳 Slot [{best}] 相关三元组：")
        triples = get_subgraph_triples(G, node, best, max_triples=6)
        for t in triples:
            marker = "⭐" if t["high_value"] else "  "
            print(f"  {marker} ({t['subject']}, {t['relation']}, {t['object']})")


if __name__ == "__main__":
    main()
