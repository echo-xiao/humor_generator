"""
llm_assoc.py — 多跳因果链

原理：从图谱出发，沿因果关系走 2-4 跳，
找到因果递进链条，终点越负面/荒诞越好。

数据来源：知识图谱因果边 + 情感标注
"""

from ..gemini_client import call_gemini
from ..knowledge.graph import find_topic_node

CAUSAL_RELATIONS = {"导致", "原因是", "造成", "引发"}

PROMPT = """你是一个中文脱口秀编剧。请根据以下因果链写一段递进式笑话。

话题：{topic}
因果链：
{chains}

要求：
- 用「A导致B → B导致C → 最后…」的递进结构
- 每一步因果看起来合理，但整条链越来越荒诞
- 最后一环是笑点（punchline），要出人意料
- 2-3句，口语化，脱口秀风格
- 直接输出笑话，不要解释

笑话："""


def _find_causal_chains(topic, G, max_hops=3, top_k=3):
    """DFS 找因果链，限制搜索宽度避免爆炸"""
    node = find_topic_node(G, topic, semantic_fallback=False)
    if node is None:
        return []

    chains = []

    def _dfs(current, path, rels, depth):
        if depth > max_hops:
            return
        if len(path) >= 3:
            end_node = path[-1]
            end_sent = G.nodes[end_node].get("sentiment", 0) if end_node in G else 0
            score = abs(end_sent) + len(path) * 0.5
            if end_sent < 0:
                score += 2.0
            chains.append({"path": list(path), "score": score})
            if len(chains) >= top_k * 5:
                return

        # 只取前 5 个因果邻居，避免爆炸
        count = 0
        for _, next_node, data in G.out_edges(current, data=True):
            if next_node in path or count >= 5:
                break
            for rel in data.get("relations", []):
                if rel in CAUSAL_RELATIONS:
                    path.append(next_node)
                    rels.append(rel)
                    _dfs(next_node, path, rels, depth + 1)
                    path.pop()
                    rels.pop()
                    count += 1
                    break

    _dfs(node, [node], [], 0)
    chains.sort(key=lambda x: -x["score"])
    return chains[:top_k]


def generate(topic: str, context: dict = None) -> list[dict]:
    G = context.get("G") if context else None
    if G is None:
        return []

    chains = _find_causal_chains(topic, G, max_hops=3, top_k=3)
    if not chains:
        return []

    chains_str = "\n".join(
        f"链{i+1}: {' → '.join(c['path'])}"
        for i, c in enumerate(chains)
    )

    joke = call_gemini(PROMPT.format(topic=topic, chains=chains_str))
    if not joke:
        return []

    return [{"method": "llm_assoc", "joke": joke, "slot": chains[0]["path"][-1], "triples": []}]
