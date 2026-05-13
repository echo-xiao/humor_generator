"""
semantic_dist.py — 语义控制距离（原路径D）

原理：用 HowNet 义原找与话题语义相似但关键维度截然相反的词，
利用"似乎相同但本质相反"的荒诞感制造笑点。
（例：打工[职位] ↔ 囚犯[惩罚]：都被安排任务，一个付工资一个受惩罚）

依赖 context：G（可选，用于义原提取）
知识来源：HowNet (OpenHowNet)
"""

from ..gemini_client import call_gemini
from ..knowledge.semantic import find_conflict_by_sememe

PROMPT = """你是一个中文脱口秀编剧。请根据以下语义冲突，写一段笑话。

话题：{topic}
语义冲突对：{conflicts}

说明：这些冲突对来自语言学分析——话题词和冲突词在某些核心属性上相似，
但在关键维度上截然相反（比如"打工[职位]↔囚犯[惩罚]"：都是被安排任务，但一个付工资一个受惩罚）。

要求：
- 利用这种"似乎相同但本质相反"的荒诞感制造笑点
- 2-3句，口语化，脱口秀风格
- 直接输出笑话，不要解释

笑话："""


def generate(topic: str, context: dict = None) -> list[dict]:
    conflicts = find_conflict_by_sememe(topic, top_k=3)
    if not conflicts:
        return []

    conflicts_str = "\n".join(f"- {c['description']}" for c in conflicts)
    joke = call_gemini(PROMPT.format(topic=topic, conflicts=conflicts_str))
    if not joke:
        return []

    return [{"method": "semantic_dist", "joke": joke, "slot": conflicts[0]["slot"], "triples": []}]
