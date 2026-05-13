"""
self_contradiction.py — 自我矛盾（策略9）

原理：从图谱找话题的「目的是/为了」边（正面目的）
和「导致/实际是」边（负面结果），
当目的和结果方向相反时 = 话题自己打脸。

数据来源：知识图谱 + 情感标注
"""

from ..gemini_client import call_gemini
from ..knowledge.graph import find_topic_node

PURPOSE_RELS = {"目的是", "目的", "真实目的", "渴望", "期待是"}
RESULT_RELS = {"导致", "实际是", "现实是", "却是", "反而", "伴随着"}

PROMPT = """你是一个中文脱口秀编剧。请根据以下"自我矛盾"素材写一段笑话。

话题：{topic}
矛盾点：
{contradictions}

要求：
- 话题的目的和实际结果自相矛盾，"自己打脸"
- 例："养生的人每天熬夜研究如何早睡，最后研究出了黑眼圈"
- 2-3句，口语化，脱口秀风格
- 直接输出笑话，不要解释

笑话："""


def _find_contradictions(topic, G, top_k=3):
    node = find_topic_node(G, topic, semantic_fallback=False)
    if node is None:
        return []

    purposes = []
    results = []
    for _, obj, data in G.out_edges(node, data=True):
        for rel in data.get("relations", []):
            if rel in PURPOSE_RELS:
                purposes.append((obj, rel))
            elif rel in RESULT_RELS:
                results.append((obj, rel))

    if not purposes or not results:
        return []

    pairs = []
    for p_node, p_rel in purposes:
        for r_node, r_rel in results:
            p_sent = G.nodes[p_node].get("sentiment", 0) if p_node in G else 0
            r_sent = G.nodes[r_node].get("sentiment", 0) if r_node in G else 0
            # 方向相反 = 矛盾
            if (p_sent >= 0 and r_sent <= 0) or (p_sent <= 0 and r_sent >= 0):
                score = abs(p_sent - r_sent) + 1
                pairs.append({
                    "purpose": p_node, "purpose_rel": p_rel,
                    "result": r_node, "result_rel": r_rel,
                    "score": score,
                })

    pairs.sort(key=lambda x: -x["score"])
    return pairs[:top_k]


def generate(topic: str, context: dict = None) -> list[dict]:
    G = context.get("G") if context else None
    if G is None:
        return []

    contradictions = _find_contradictions(topic, G)
    if not contradictions:
        return []

    cont_str = "\n".join(
        f"- 目的「{c['purpose']}」({c['purpose_rel']}) vs 结果「{c['result']}」({c['result_rel']})"
        for c in contradictions
    )
    joke = call_gemini(PROMPT.format(topic=topic, contradictions=cont_str))
    if not joke:
        return []

    return [{"method": "self_contradiction", "joke": joke, "slot": contradictions[0]["result"], "triples": []}]
