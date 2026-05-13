"""
xiehouyu_gen.py — 歇后语风格生成（策略11）

原理：从图谱的歇后语边检索与话题相关的歇后语，
或用话题的图谱关联生成新的歇后语结构（前半句具体意象 → 后半句意外结论）。

数据来源：图谱歇后语边
"""

from ..gemini_client import call_gemini
from ..knowledge.graph import find_topic_node

PROMPT = """你是一个中文脱口秀编剧。请参考以下歇后语素材，写一段笑话。

话题：{topic}
相关歇后语：
{xiehouyu}

话题的图谱关联：
{graph_context}

要求：
- 可以直接引用已有歇后语，也可以模仿歇后语结构创造新的
- 歇后语结构：前半句（具体意象/场景）→ 后半句（意外的结论/双关）
- 融入话题，不要生硬堆砌
- 2-3句，口语化，脱口秀风格
- 直接输出笑话，不要解释

笑话："""


def _find_related_xiehouyu(topic, G, top_k=5):
    """在图谱的歇后语边中搜索与话题相关的"""
    results = []

    # 方法1：话题节点直接连的歇后语边
    node = find_topic_node(G, topic, semantic_fallback=False)
    if node:
        for _, obj, data in G.out_edges(node, data=True):
            if "歇后语" in data.get("relations", []):
                results.append((node, obj))
        for subj, _, data in G.in_edges(node, data=True):
            if "歇后语" in data.get("relations", []):
                results.append((subj, node))

    # 方法2：在所有歇后语边中搜索包含话题关键字的
    if len(results) < top_k:
        for u, v, data in G.edges(data=True):
            if "歇后语" not in data.get("relations", []):
                continue
            if topic in u or topic in v or any(c in u for c in topic) or any(c in v for c in topic):
                if (u, v) not in results:
                    results.append((u, v))
            if len(results) >= top_k * 2:
                break

    return results[:top_k]


def _get_graph_context(topic, G, max_triples=5):
    node = find_topic_node(G, topic, semantic_fallback=False)
    if node is None:
        return "（无）"
    triples = []
    for _, obj, data in G.out_edges(node, data=True):
        for rel in data.get("relations", []):
            if rel != "歇后语":
                triples.append(f"({node}, {rel}, {obj})")
    return "\n".join(triples[:max_triples]) if triples else "（无）"


def generate(topic: str, context: dict = None) -> list[dict]:
    G = context.get("G") if context else None
    if G is None:
        return []

    xiehouyu = _find_related_xiehouyu(topic, G)
    graph_ctx = _get_graph_context(topic, G)

    if not xiehouyu and graph_ctx == "（无）":
        return []

    xh_str = "\n".join(f"- {setup} —— {punch}" for setup, punch in xiehouyu) if xiehouyu else "（无直接相关歇后语）"

    joke = call_gemini(PROMPT.format(topic=topic, xiehouyu=xh_str, graph_context=graph_ctx))
    if not joke:
        return []

    slot = xiehouyu[0][1] if xiehouyu else topic
    return [{"method": "xiehouyu_gen", "joke": joke, "slot": slot, "triples": []}]
