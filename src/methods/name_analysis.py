"""
name_analysis.py — 名称/拆字分析（数据驱动）

原理：拆解话题词的汉字结构（部首/偏旁），
找出字面构成与实际含义之间的反差。
用 unicodedata 获取部首信息 + 图谱补充语义。

数据来源：Unicode CJK部首 + 知识图谱
"""

import unicodedata
from ..gemini_client import call_gemini
from ..knowledge.graph import find_topic_node

PROMPT = """你是一个中文脱口秀编剧。请根据以下拆字/名称分析素材写一段笑话。

话题：{topic}
拆字分析：
{analysis}

图谱补充（话题的真实关联）：
{graph_context}

要求：
- 利用字面拆解与实际含义的反差制造笑点
- 例："婚 = 女 + 昏 → 结婚就是女人昏了头"
- 例："忙 = 心 + 亡 → 忙就是心死了"
- 2-3句，口语化，脱口秀风格
- 直接输出笑话，不要解释

笑话："""


# 常见汉字拆字映射（高幽默价值的）
CHAR_DECOMPOSE = {
    "婚": ("女", "昏", "女人昏了头"),
    "忙": ("心", "亡", "心死了"),
    "赢": ("亡", "口", "月", "贝", "凡"),
    "穷": ("穴", "力", "在洞里使劲"),
    "债": ("人", "责", "人的责任"),
    "愁": ("秋", "心", "秋天的心"),
    "舒": ("舍", "予", "舍得给予"),
    "劣": ("少", "力", "力气少"),
    "值": ("人", "直", "人要正直"),
    "悟": ("心", "吾", "我的心"),
    "怕": ("心", "白", "心里一片空白"),
    "忍": ("刃", "心", "心上一把刀"),
    "懒": ("心", "赖", "心在耍赖"),
    "闷": ("门", "心", "心被关在门里"),
}


def _analyze_chars(topic):
    """拆解话题中每个字的结构"""
    results = []
    for char in topic:
        if char in CHAR_DECOMPOSE:
            parts = CHAR_DECOMPOSE[char]
            if len(parts) == 3:
                results.append(f"「{char}」= {parts[0]} + {parts[1]} → {parts[2]}")
            else:
                results.append(f"「{char}」= {' + '.join(parts)}")
            continue

        # 用 unicodedata 获取字符描述
        try:
            name = unicodedata.name(char, "")
            if "CJK" in name:
                # 获取部首（kangxi radical）
                cp = ord(char)
                # 简单的部首推断：检查是否有已知偏旁
                results.append(f"「{char}」（Unicode: U+{cp:04X}）")
        except (ValueError, TypeError):
            pass

    return results


def _get_graph_context(topic, G, max_triples=5):
    """从图谱获取话题的关键关联"""
    node = find_topic_node(G, topic, semantic_fallback=False)
    if node is None:
        return ""

    triples = []
    for _, obj, data in G.out_edges(node, data=True):
        for rel in data.get("relations", []):
            triples.append(f"({node}, {rel}, {obj})")
    for subj, _, data in G.in_edges(node, data=True):
        for rel in data.get("relations", []):
            triples.append(f"({subj}, {rel}, {node})")

    # 按 humor_weight 排序
    scored = []
    for t_str in triples:
        scored.append(t_str)
    return "\n".join(scored[:max_triples]) if scored else "（图谱中无直接关联）"


def generate(topic: str, context: dict = None) -> list[dict]:
    G = context.get("G") if context else None

    analysis = _analyze_chars(topic)
    graph_ctx = _get_graph_context(topic, G) if G else "（无图谱）"

    # 至少要有拆字结果或图谱上下文
    if not analysis and graph_ctx == "（图谱中无直接关联）":
        return []

    analysis_str = "\n".join(analysis) if analysis else "（无法拆字，请从话题名称的字面含义出发）"
    joke = call_gemini(PROMPT.format(topic=topic, analysis=analysis_str, graph_context=graph_ctx))
    if not joke:
        return []

    return [{"method": "name_analysis", "joke": joke, "slot": topic, "triples": []}]
