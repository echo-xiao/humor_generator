"""
ironic_reversal.py — 讽刺反转

原理：从图谱中找到话题的"反讽/讽刺"关系边，
利用「表面说 A，实际意思是 non-A」的结构制造笑点。
说好听的 → 其实是在骂人 / 说坏的 → 其实是现实。

依赖 context：G（图谱，遍历反讽/讽刺 relation 边）
"""

from ..gemini_client import call_gemini
from ..knowledge.graph import HIGH_VALUE_RELATIONS

IRONIC_RELATIONS = {"反讽", "讽刺", "讽刺地", "被视为", "被认为是"}

PROMPT = """你是一个中文脱口秀编剧。请根据以下讽刺关系，用"反话/讽刺反转"手法写一段笑话。

话题：{topic}
讽刺关系：
{ironic_triples}

手法说明：
- 表面上说一个正面/中性的事，其实暗示的是完全相反的现实
- 或者：把一个负面的事情用"官方正能量"语气说出来
- 听众需要自己反应过来才觉得好笑
- 2-3句，口语化，脱口秀风格
- 直接输出笑话，不要解释

笑话："""


def generate(topic: str, context: dict = None) -> list[dict]:
    G = context.get("G") if context else None
    if G is None:
        return []

    # 从图谱里找话题相关的反讽/讽刺边（一跳）
    ironic_triples = []
    topic_node = None
    for n in G.nodes():
        if topic in n or n in topic:
            topic_node = n
            break

    if topic_node is None:
        return []

    for _, obj, data in G.out_edges(topic_node, data=True):
        for rel in data.get("relations", []):
            if rel in IRONIC_RELATIONS:
                ironic_triples.append((topic_node, rel, obj))

    for subj, _, data in G.in_edges(topic_node, data=True):
        for rel in data.get("relations", []):
            if rel in IRONIC_RELATIONS:
                ironic_triples.append((subj, rel, topic_node))

    if not ironic_triples:
        return []

    triples_str = "\n".join(f"- ({s}, {r}, {o})" for s, r, o in ironic_triples[:5])
    joke = call_gemini(PROMPT.format(topic=topic, ironic_triples=triples_str))
    if not joke:
        return []

    used_triples = [{"subject": s, "relation": r, "object": o} for s, r, o in ironic_triples[:5]]
    return [{"method": "ironic_reversal", "joke": joke, "slot": topic_node, "triples": used_triples}]
