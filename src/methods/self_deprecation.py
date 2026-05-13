"""
self_deprecation.py — 自嘲（策略13）

原理：从图谱找话题最负面的节点（情感强度最高的负面词），
用「我就是那种…」或「我们这代人…」框架做自嘲。

数据来源：知识图谱 + 大连理工情感标注
"""

from ..gemini_client import call_gemini
from ..knowledge.graph import find_topic_node

PROMPT = """你是一个中文脱口秀编剧。请用"自嘲"手法写一段笑话。

话题：{topic}
负面现实（越惨越好笑）：
{negatives}

要求：
- 用「我就是那种…」或「我们这代人…」的自嘲框架
- 把最惨的处境说得云淡风轻，越轻描淡写越好笑
- 自嘲不是自怜——要有一种"认清现实后的豁达"
- 2-3句，口语化，脱口秀风格
- 直接输出笑话，不要解释

笑话："""


def _find_negative_associations(topic, G, top_k=5):
    node = find_topic_node(G, topic, semantic_fallback=False)
    if node is None:
        return []

    negatives = []
    for _, obj, data in G.out_edges(node, data=True):
        sent = G.nodes[obj].get("sentiment", 0) if obj in G else 0
        strength = G.nodes[obj].get("sentiment_strength", 0) if obj in G else 0
        if sent < 0:
            rels = data.get("relations", [])
            negatives.append({
                "node": obj,
                "relation": rels[0] if rels else "相关",
                "sentiment": sent,
                "strength": strength,
                "score": abs(sent) * strength,
            })

    for subj, _, data in G.in_edges(node, data=True):
        sent = G.nodes[subj].get("sentiment", 0) if subj in G else 0
        strength = G.nodes[subj].get("sentiment_strength", 0) if subj in G else 0
        if sent < 0:
            rels = data.get("relations", [])
            negatives.append({
                "node": subj,
                "relation": rels[0] if rels else "相关",
                "sentiment": sent,
                "strength": strength,
                "score": abs(sent) * strength,
            })

    negatives.sort(key=lambda x: -x["score"])
    return negatives[:top_k]


def generate(topic: str, context: dict = None) -> list[dict]:
    G = context.get("G") if context else None
    if G is None:
        return []

    negatives = _find_negative_associations(topic, G)
    if not negatives:
        return []

    neg_str = "\n".join(
        f"- {n['node']}（{n['relation']}，负面强度={n['strength']}）"
        for n in negatives
    )
    joke = call_gemini(PROMPT.format(topic=topic, negatives=neg_str))
    if not joke:
        return []

    return [{"method": "self_deprecation", "joke": joke, "slot": negatives[0]["node"], "triples": []}]
