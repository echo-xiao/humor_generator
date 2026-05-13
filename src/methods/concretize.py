"""
concretize.py — 具体化（策略8）

原理：把抽象话题强制放进具体维度（时间/金额/动作/场景/次数），
用极度精确的数字/细节制造荒诞感。

数据来源：知识图谱具体节点 + 维度矩阵
"""

from ..gemini_client import call_gemini
from ..knowledge.graph import find_topic_node

DIMENSIONS = ["时间", "金额", "动作", "场景", "次数", "对话"]

PROMPT = """你是一个中文脱口秀编剧。请用"具体化"手法写一段笑话。

话题：{topic}
图谱关联：
{graph_context}

手法说明：
- 把抽象的"{topic}"放进极其具体的场景
- 加入精确的数字、时间、金额、次数（越精确越荒诞）
- 例："差0.3元，放回去了" / "凌晨三点算了三遍，还是买不起"
- 可参考的维度：{dimensions}
- 2-3句，口语化，脱口秀风格
- 直接输出笑话，不要解释

笑话："""


def _get_graph_context(topic, G, max_triples=8):
    node = find_topic_node(G, topic, semantic_fallback=False)
    if node is None:
        return "（无）"

    triples = []
    for _, obj, data in G.out_edges(node, data=True):
        hw = data.get("humor_weight", 0)
        for rel in data.get("relations", []):
            triples.append((hw, f"({node}, {rel}, {obj})"))
    for subj, _, data in G.in_edges(node, data=True):
        hw = data.get("humor_weight", 0)
        for rel in data.get("relations", []):
            triples.append((hw, f"({subj}, {rel}, {node})"))

    triples.sort(key=lambda x: -x[0])
    return "\n".join(t[1] for t in triples[:max_triples]) if triples else "（无）"


def generate(topic: str, context: dict = None) -> list[dict]:
    G = context.get("G") if context else None
    graph_ctx = _get_graph_context(topic, G) if G else "（无）"

    joke = call_gemini(PROMPT.format(
        topic=topic,
        graph_context=graph_ctx,
        dimensions="、".join(DIMENSIONS),
    ))
    if not joke:
        return []

    return [{"method": "concretize", "joke": joke, "slot": topic, "triples": []}]
