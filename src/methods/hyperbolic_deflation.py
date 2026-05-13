"""
hyperbolic_deflation.py — 夸张降格（策略10）

原理：从图谱找话题的崇高/宏大节点（象征/理想是）
和平凡/琐碎节点（导致/实际是），
用崇高语气开场 → 平凡结局收场。

数据来源：知识图谱 + 情感标注 + 词林跨域
"""

from ..gemini_client import call_gemini
from ..knowledge.graph import find_topic_node

GRAND_RELS = {"象征", "被视为", "被认为是", "期待是", "期待是人生", "目的是"}
MUNDANE_RELS = {"导致", "实际是", "现实是", "伴随着", "却是"}

PROMPT = """你是一个中文脱口秀编剧。请用"夸张降格"手法写一段笑话。

话题：{topic}
崇高 vs 平凡：
{pairs}

要求：
- 用宏大、崇高的语气铺垫，然后突然降到极其平凡琐碎的结局
- 反差越大越好笑
- 例："历经十年奋斗，终于在格子间实现了每天准时点外卖的人生价值"
- 2-3句，口语化，脱口秀风格
- 直接输出笑话，不要解释

笑话："""


def _find_grand_mundane_pairs(topic, G, top_k=3):
    node = find_topic_node(G, topic, semantic_fallback=False)
    if node is None:
        return []

    grands = []
    mundanes = []
    for _, obj, data in G.out_edges(node, data=True):
        for rel in data.get("relations", []):
            sent = G.nodes[obj].get("sentiment", 0) if obj in G else 0
            if rel in GRAND_RELS:
                grands.append((obj, rel, sent))
            elif rel in MUNDANE_RELS:
                mundanes.append((obj, rel, sent))

    for subj, _, data in G.in_edges(node, data=True):
        for rel in data.get("relations", []):
            sent = G.nodes[subj].get("sentiment", 0) if subj in G else 0
            if rel in GRAND_RELS:
                grands.append((subj, rel, sent))
            elif rel in MUNDANE_RELS:
                mundanes.append((subj, rel, sent))

    if not grands and not mundanes:
        return []

    if grands and mundanes:
        pairs = []
        for g_node, g_rel, g_sent in grands:
            for m_node, m_rel, m_sent in mundanes:
                score = abs(g_sent - m_sent) + 1
                pairs.append({"grand": g_node, "grand_rel": g_rel, "mundane": m_node, "mundane_rel": m_rel, "score": score})
        pairs.sort(key=lambda x: -x["score"])
        return pairs[:top_k]

    # 只有一边也能用
    if grands:
        return [{"grand": g[0], "grand_rel": g[1], "mundane": "（日常琐事）", "mundane_rel": "", "score": 1} for g in grands[:top_k]]
    return [{"grand": "（宏大理想）", "grand_rel": "", "mundane": m[0], "mundane_rel": m[1], "score": 1} for m in mundanes[:top_k]]


def generate(topic: str, context: dict = None) -> list[dict]:
    G = context.get("G") if context else None
    if G is None:
        return []

    pairs = _find_grand_mundane_pairs(topic, G)
    if not pairs:
        return []

    pairs_str = "\n".join(
        f"- 崇高「{p['grand']}」({p['grand_rel']}) → 平凡「{p['mundane']}」({p['mundane_rel']})"
        for p in pairs
    )
    joke = call_gemini(PROMPT.format(topic=topic, pairs=pairs_str))
    if not joke:
        return []

    return [{"method": "hyperbolic_deflation", "joke": joke, "slot": pairs[0].get("mundane", topic), "triples": []}]
