"""
homophone.py — 谐音梗（数据驱动）

原理：从图谱的「谐音于」边找同音词对，
或用 pypinyin 实时找话题的谐音候选词，
选语义距离最远的谐音对喂给 Gemini。

数据来源：图谱谐音边 + pypinyin
"""

from ..gemini_client import call_gemini
from ..knowledge.graph import find_topic_node

PROMPT = """你是一个中文脱口秀编剧。请用以下谐音素材写一段谐音梗笑话。

话题：{topic}
谐音素材：
{homophones}

要求：
- 利用谐音词的另一层含义制造意外感
- 谐音词最好在句末暴露，惊喜感最强
- 2-3句，口语化，脱口秀风格
- 直接输出笑话，不要解释

笑话："""


def _find_homophones_from_graph(topic, G, top_k=5):
    """从图谱的「谐音于」边找话题相关的谐音词对"""
    results = []

    # 直接查话题节点的谐音边
    node = find_topic_node(G, topic, semantic_fallback=False)
    if node:
        for _, obj, data in G.out_edges(node, data=True):
            if "谐音于" in data.get("relations", []):
                results.append((node, obj, data.get("pinyin", "")))
        for subj, _, data in G.in_edges(node, data=True):
            if "谐音于" in data.get("relations", []):
                results.append((subj, node, data.get("pinyin", "")))

    # 如果直接查没结果，用 pypinyin 找话题的拼音，再在图谱谐音边里匹配
    if not results:
        try:
            from pypinyin import pinyin, Style
            topic_py = "".join(p[0] for p in pinyin(topic, style=Style.NORMAL))
            for u, v, data in G.edges(data=True):
                if "谐音于" not in data.get("relations", []):
                    continue
                edge_py = data.get("pinyin", "")
                if edge_py and (topic_py in edge_py or edge_py in topic_py):
                    results.append((u, v, edge_py))
                if len(results) >= top_k * 2:
                    break
        except ImportError:
            pass

    # 去重，按 humor_weight 排序
    seen = set()
    unique = []
    for w1, w2, py in results:
        pair = tuple(sorted([w1, w2]))
        if pair not in seen:
            seen.add(pair)
            hw = 0
            if G.has_edge(w1, w2):
                hw = G[w1][w2].get("humor_weight", 0)
            unique.append({"word1": w1, "word2": w2, "pinyin": py, "humor_weight": hw})
    unique.sort(key=lambda x: -x["humor_weight"])
    return unique[:top_k]


def generate(topic: str, context: dict = None) -> list[dict]:
    G = context.get("G") if context else None
    if G is None:
        return []

    homophones = _find_homophones_from_graph(topic, G, top_k=5)
    if not homophones:
        return []

    homo_str = "\n".join(
        f"- {h['word1']} ↔ {h['word2']}（拼音：{h['pinyin']}）"
        for h in homophones
    )
    joke = call_gemini(PROMPT.format(topic=topic, homophones=homo_str))
    if not joke:
        return []

    return [{"method": "homophone", "joke": joke, "slot": homophones[0]["word2"], "triples": []}]
