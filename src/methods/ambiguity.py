"""
ambiguity.py — 歧义词挖掘（数据驱动）

原理：用 HowNet 找话题词的多个 sense（义项），
用信息论量化「歧义度」（两义项激活概率接近）×「差异度」（义原距离大），
选双关张力最强的候选词喂给 Gemini。

数据来源：OpenHowNet 多 sense
"""

from ..gemini_client import call_gemini
from ..knowledge.semantic import get_hownet, HOWNET_AVAILABLE, GENERIC_SEMEMES

PROMPT = """你是一个中文脱口秀编剧。请利用以下多义词的歧义制造笑点。

话题：{topic}
多义词分析：
{ambiguity_info}

要求：
- 利用词的两层含义之间的落差制造笑点
- 第一层含义做铺垫，第二层含义做反转
- 2-3句，口语化，脱口秀风格
- 直接输出笑话，不要解释

笑话："""


def _find_ambiguous_words(topic, top_k=3):
    """找话题相关的高歧义度词"""
    if not HOWNET_AVAILABLE:
        return []
    hownet = get_hownet()
    if hownet is None:
        return []

    # 收集候选词：话题本身 + jieba 分词
    candidates = [topic]
    try:
        import jieba
        candidates.extend([w for w in jieba.lcut(topic) if len(w) >= 2])
    except ImportError:
        pass

    results = []
    for word in candidates:
        senses = hownet.get_sememes_by_word(word)
        if len(senses) < 2:
            continue

        # 提取每个 sense 的义原集合
        sense_sememes = []
        for s in senses:
            sems = set()
            for sem in s.get("sememes", []):
                s_str = str(sem)
                if s_str not in GENERIC_SEMEMES:
                    sems.add(s_str)
            if sems:
                sense_sememes.append(sems)

        if len(sense_sememes) < 2:
            continue

        # 计算最大歧义对的张力
        best_tension = 0
        best_pair = None
        for i in range(len(sense_sememes)):
            for j in range(i + 1, len(sense_sememes)):
                sa, sb = sense_sememes[i], sense_sememes[j]
                union = sa | sb
                intersection = sa & sb
                if not union:
                    continue
                # 差异度 = 1 - Jaccard（越不同越好）
                diff = 1 - len(intersection) / len(union) if union else 0
                # 歧义度 = 两个义项大小越接近越好
                size_ratio = min(len(sa), len(sb)) / max(len(sa), len(sb)) if max(len(sa), len(sb)) > 0 else 0
                tension = diff * size_ratio
                if tension > best_tension:
                    best_tension = tension
                    best_pair = (sa, sb)

        if best_pair and best_tension > 0.3:
            sa_zh = [s.split("|")[-1] for s in sorted(best_pair[0])[:4]]
            sb_zh = [s.split("|")[-1] for s in sorted(best_pair[1])[:4]]
            results.append({
                "word": word,
                "tension": best_tension,
                "sense1": "、".join(sa_zh),
                "sense2": "、".join(sb_zh),
                "description": f"「{word}」义项1[{', '.join(sa_zh)}] vs 义项2[{', '.join(sb_zh)}]，张力={best_tension:.2f}",
            })

    results.sort(key=lambda x: -x["tension"])
    return results[:top_k]


def generate(topic: str, context: dict = None) -> list[dict]:
    ambiguous = _find_ambiguous_words(topic, top_k=3)
    if not ambiguous:
        return []

    info_str = "\n".join(f"- {a['description']}" for a in ambiguous)
    joke = call_gemini(PROMPT.format(topic=topic, ambiguity_info=info_str))
    if not joke:
        return []

    return [{"method": "ambiguity", "joke": joke, "slot": ambiguous[0]["word"], "triples": []}]
