"""
false_analogy.py — 类比错位

原理：用词林找与话题跨域但有隐秘相似结构的词，
显式建立「X 就像 Y」的类比，然后用一个细节打破它，
让听众先接受类比再被那个细节击中。

和 context_shift 的区别：
  context_shift：把 X 放进 Y 的语境，让它们碰撞
  false_analogy：先说「X 就像 Y」建立类比，再用「只不过/除了/但」打破

依赖 context：G（图谱，用于过滤词林结果）
知识来源：同义词词林扩展版（cilin）
"""

from ..gemini_client import call_gemini
from ..knowledge.semantic import find_contrast_with_graph, find_similar_cilin

PROMPT = """你是一个中文脱口秀编剧。请用"类比错位"手法写一段笑话。

话题：{topic}
类比候选词（和话题来自不同语义域）：
{analogy_words}

手法说明：
- 先建立一个「{topic} 就像 [某个类比词]」的类比（让听众觉得有道理）
- 然后用「只不过 / 除了 / 但是」加一个细节打破这个类比
- 打破点越具体越好笑（不要说「但现实很残酷」这种废话）
- 例：「结婚就像买房，只不过房子会跟你争遥控器」
- 2-3句，口语化，脱口秀风格
- 直接输出笑话，不要解释

笑话："""


def generate(topic: str, context: dict = None) -> list[dict]:
    G = context.get("G") if context else None

    contrasts = find_contrast_with_graph(topic, G, top_k=5) if G else []
    if not contrasts:
        contrasts = find_similar_cilin(topic, top_k=5)
    if not contrasts:
        return []

    # 取 slot 字段（词林返回的对比词）
    analogy_words = [c["slot"] for c in contrasts if c.get("slot")]
    if not analogy_words:
        return []

    analogy_str = "、".join(analogy_words)
    joke = call_gemini(PROMPT.format(topic=topic, analogy_words=analogy_str))
    if not joke:
        return []

    return [{"method": "false_analogy", "joke": joke, "slot": analogy_words[0], "triples": []}]
