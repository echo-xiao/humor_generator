"""
expectation.py — 预期违背（数据驱动）

原理：从图谱找话题的「期待是/象征/目的是」边（正面预期）
和「实际是/导致/现实是」边（负面现实），
选情感极性差最大的预期×现实对喂给 Gemini。

数据来源：知识图谱（期待/现实边）+ 情感标注
"""

from ..gemini_client import call_gemini
from ..knowledge.graph import find_topic_node

EXPECT_RELATIONS = {"期待是", "期待是人生", "象征", "目的是", "目的", "真实目的", "渴望", "引起渴望"}
REALITY_RELATIONS = {"实际是", "现实是", "导致", "本质是", "等同于", "等于", "却是", "反而"}

PROMPT = """你是一个中文脱口秀编剧。请用"预期违背"结构写一段笑话。

话题：{topic}
预期 vs 现实：
{pairs}

要求：
- 先建立听众认为合理的预期（setup），再用意外的现实打破它（punchline）
- 预期和现实的反差越大越好
- 典型句式："以为...结果..." / "本来想...没想到..."
- 2-3句，口语化，脱口秀风格
- 直接输出笑话，不要解释

笑话："""


def _find_expect_reality_pairs(topic, G, top_k=3):
    """从图谱找预期×现实对，按情感极性差排序"""
    node = find_topic_node(G, topic, semantic_fallback=False)
    if node is None:
        return []

    expects = []   # (node, relation)
    realities = [] # (node, relation)

    for _, obj, data in G.out_edges(node, data=True):
        for rel in data.get("relations", []):
            if rel in EXPECT_RELATIONS:
                expects.append((obj, rel))
            elif rel in REALITY_RELATIONS:
                realities.append((obj, rel))

    for subj, _, data in G.in_edges(node, data=True):
        for rel in data.get("relations", []):
            if rel in EXPECT_RELATIONS:
                expects.append((subj, rel))
            elif rel in REALITY_RELATIONS:
                realities.append((subj, rel))

    if not expects and not realities:
        return []

    # 如果只有一边，用它构造"以为X，结果相反"
    if expects and not realities:
        return [{"expect": e[0], "expect_rel": e[1], "reality": "（图谱未记录）", "reality_rel": "",
                 "score": 1.0} for e in expects[:top_k]]
    if realities and not expects:
        return [{"expect": "（常规期待）", "expect_rel": "", "reality": r[0], "reality_rel": r[1],
                 "score": 1.0} for r in realities[:top_k]]

    # 两边都有，配对并按情感对比打分
    pairs = []
    for e_node, e_rel in expects:
        for r_node, r_rel in realities:
            # 情感对比分
            e_sent = G.nodes[e_node].get("sentiment", 0) if e_node in G else 0
            r_sent = G.nodes[r_node].get("sentiment", 0) if r_node in G else 0
            polarity_diff = abs(e_sent - r_sent)
            # 不同节点加分
            score = polarity_diff + (1.0 if e_node != r_node else 0)
            pairs.append({
                "expect": e_node, "expect_rel": e_rel,
                "reality": r_node, "reality_rel": r_rel,
                "score": score,
            })

    pairs.sort(key=lambda x: -x["score"])
    return pairs[:top_k]


def generate(topic: str, context: dict = None) -> list[dict]:
    G = context.get("G") if context else None
    if G is None:
        return []

    pairs = _find_expect_reality_pairs(topic, G, top_k=3)
    if not pairs:
        return []

    pairs_str = "\n".join(
        f"- 预期「{p['expect']}」({p['expect_rel']}) → 现实「{p['reality']}」({p['reality_rel']})"
        for p in pairs
    )
    joke = call_gemini(PROMPT.format(topic=topic, pairs=pairs_str))
    if not joke:
        return []

    slot = pairs[0]["reality"] if pairs[0]["reality"] != "（常规期待）" else pairs[0]["expect"]
    return [{"method": "expectation", "joke": joke, "slot": slot, "triples": []}]
