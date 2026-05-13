"""
rag_replace.py — RAG 后处理替换

原理：8路方法生成候选后，检索梗库判断是否有可以自然融入已有笑话的梗。
有合适的梗时，生成增强版本；没有合适的返回空列表。

依赖 context：memes, candidates（已有候选列表）
"""

from ..gemini_client import call_gemini

PROMPT = """你是一个中文脱口秀编剧。下面有几个已生成的笑话，和一批梗库素材。
请判断梗库中是否有可以自然融入某个笑话的梗，如果有，输出融合后的最佳版本；没有合适的就只输出"无"。

话题：{topic}

已有笑话：
{jokes}

梗库素材：
{memes}

要求：
- 只在真正搭配时才替换，不要强行融合
- 2-3句，口语化，脱口秀风格
- 没有合适的只输出"无"，不要解释
- 有合适的直接输出融合后的笑话，不要解释

输出："""


def generate(topic: str, context: dict = None) -> list[dict]:
    if not context:
        return []

    memes = context.get("memes", [])
    candidates = context.get("candidates", [])

    if not memes or not candidates:
        return []

    memes_str = "\n---\n".join(memes)
    jokes_str = "\n".join(
        f"{i+1}. [{c['method']}] {c['joke']}"
        for i, c in enumerate(candidates[:5])
    )

    result = call_gemini(PROMPT.format(topic=topic, jokes=jokes_str, memes=memes_str))
    if not result or result.strip() == "无":
        return []

    slot_name = context.get("slot_name", "")
    return [{"method": "rag_replace", "joke": result.strip(), "slot": slot_name, "triples": context.get("triples", [])}]
