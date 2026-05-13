"""
kg_contrast.py — 知识图谱对比：正面属性 × 负面事件（原路径A）

原理：从图谱中找到话题的高价值冲突三元组（Humor Slot），
利用三元组中的反差、荒诞因果或概念颠覆制造笑点。

依赖 context：slots, triples, triples_str, slot_name
"""

from ..gemini_client import call_gemini

PROMPT = """你是一个中文脱口秀编剧。请根据以下知识图谱三元组，写一段2-3句的脱口秀笑话。

话题：{topic}
冲突节点（Humor Slot）：{slot}
相关三元组：
{triples}

要求：
- 利用三元组中的反差、荒诞因果或概念颠覆制造笑点
- 语气口语化，像李诞或脱口秀演员的风格
- 直接输出笑话，不要解释

笑话："""


def generate(topic: str, context: dict = None) -> list[dict]:
    if not context or not context.get("slots"):
        return []

    slot_name = context["slot_name"]
    triples_str = context["triples_str"]
    triples = context["triples"]

    joke = call_gemini(PROMPT.format(topic=topic, slot=slot_name, triples=triples_str))
    if not joke:
        return []

    return [{"method": "kg_contrast", "joke": joke, "slot": slot_name, "triples": triples}]
