"""
context_shift.py — 语境错位（原路径E）

原理：用词林找与话题跨大类（不同语义域）的词，
把两个来自完全不同领域的概念并置，产生荒诞感。
（例：话题"婚姻"[社会域] ↔ "合同"[法律域]）

依赖 context：G（图谱，用于过滤只保留图谱中出现过的词）
知识来源：同义词词林扩展版（cilin）
"""

from ..gemini_client import call_gemini
from ..knowledge.semantic import find_contrast_with_graph, find_similar_cilin

PROMPT = """你是一个中文脱口秀编剧。请根据以下跨域对比，写一段笑话。

话题：{topic}
跨域对比词：{contrasts}

说明：这些词和话题来自完全不同的语义领域（如"活动域"vs"社会域"），
但在现实中有某种隐秘的相似结构，把它们放在一起会产生荒诞感。

要求：
- 找出话题和对比词之间意想不到的共同点或因果关系
- 2-3句，口语化，脱口秀风格
- 直接输出笑话，不要解释

笑话："""


def generate(topic: str, context: dict = None) -> list[dict]:
    G = context.get("G") if context else None

    contrasts = find_contrast_with_graph(topic, G, top_k=3) if G else []
    if not contrasts:
        contrasts = find_similar_cilin(topic, top_k=3)
    if not contrasts:
        return []

    contrasts_str = "\n".join(f"- {c['description']}" for c in contrasts)
    joke = call_gemini(PROMPT.format(topic=topic, contrasts=contrasts_str))
    if not joke:
        return []

    return [{"method": "context_shift", "joke": joke, "slot": contrasts[0]["slot"], "triples": []}]
