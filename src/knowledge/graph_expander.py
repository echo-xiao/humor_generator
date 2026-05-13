"""
graph_expander.py
知识图谱扩展器：用 3 种方法为新话题动态生成三元组，扩充图谱。

方法一：模式迁移（Pattern Migration）
  - 取现有高价值三元组作为模板，让 Gemini 对新话题生成同样模式的三元组

方法二：节点关联扩展（Node Association Expansion）
  - 对新话题的语义邻近节点，提取二跳子图内的三元组，让 Gemini 补充

方法三：话题类比（Topic Analogy via HowNet）
  - 用 HowNet 义原相似度找已有图中最相似的话题节点，复制其高价值三元组

最终：三种方法的三元组合并去重，加入临时图（不修改原图）。
对外暴露：expand_topic(topic, G) → 返回扩充后的图（临时副本）
"""

import os
import json
import pickle
import urllib.request
import urllib.parse

from ..gemini_client import call_gemini
from .graph import HIGH_VALUE_RELATIONS, NOISE_NODES, GRAPH_PATH, find_topic_node
from .semantic import HOWNET_AVAILABLE, get_hownet, get_sememe_set, sememe_similarity

# ==================== Prompt 模板 ====================

PATTERN_MIGRATION_PROMPT = """\
你是一个了解中国社会的脱口秀编剧。

下面是一些真实的"高价值三元组"，它们揭示了某个话题令人意外、讽刺或荒诞的一面：

{examples}

请参考上面三元组的"揭示逻辑"，为新话题"{topic}"生成 {n} 条类似的高价值三元组。

规则：
- relation 类型必须是：对立于/反讽/讽刺/本质是/实际是/现实是/导致/象征/被视为/等同于/感觉像/意味着/暗示
- subject 必须是"{topic}"或其直接属性/状态
- 三元组要有强烈的反差感或令人会心一笑的荒诞感
- 只输出 JSON 数组，格式：[{{"subject":"...","relation":"...","object":"..."}}]
- 不要输出解释，直接输出 JSON

JSON："""

NODE_EXPAND_PROMPT = """\
你是一个了解中国社会的脱口秀编剧。

知识图谱中已有关于"{topic}"的以下三元组：

{existing}

请根据这些信息，再为"{topic}"**补充** {n} 条新的、有趣的、揭示反差的三元组。
新三元组的 subject 可以是"{topic}"也可以是上面 object 中的词（做二跳扩展）。

规则：
- relation 类型必须是：对立于/反讽/讽刺/本质是/实际是/现实是/导致/象征/被视为/等同于/感觉像/意味着/暗示
- 要揭示反差、荒诞或意外的关系
- 只输出 JSON 数组，格式：[{{"subject":"...","relation":"...","object":"..."}}]
- 不要输出解释，直接输出 JSON

JSON："""

OWNTHINK_PROMPT = """\
你是一个了解中国社会的脱口秀编剧。

以下是来自知识图谱的关于"{topic}"的百科信息：

{knowledge}

请根据这些事实信息，为"{topic}"生成 {n} 条高价值幽默三元组。
挖掘这些事实背后的反差、讽刺和荒诞感（比如：名字高大上但本质很俗；官方定义和现实体验截然相反等）。

规则：
- relation 类型必须是：对立于/反讽/讽刺/本质是/实际是/现实是/导致/象征/被视为/等同于/感觉像/意味着/暗示
- subject 必须是"{topic}"或其直接属性
- 要有强烈反差感或令人会心一笑的荒诞感
- 只输出 JSON 数组，格式：[{{"subject":"...","relation":"...","object":"..."}}]
- 不要输出解释，直接输出 JSON

JSON："""


# ==================== 方法一：模式迁移 ====================

def method1_pattern_migration(topic, G, n_templates=6, n_generate=8):
    high_value_triples = []
    seen_relations = set()
    for subj, obj, data in G.edges(data=True):
        relations = data.get("relations", [])
        hv_rels = [r for r in relations if r in HIGH_VALUE_RELATIONS]
        if hv_rels:
            rel = hv_rels[0]
            if rel not in seen_relations or len(high_value_triples) < 3:
                high_value_triples.append((subj, rel, obj))
                seen_relations.add(rel)
            if len(high_value_triples) >= n_templates:
                break

    if not high_value_triples:
        print("  [方法一] 图谱中无高价值三元组，跳过")
        return []

    examples = "\n".join(f"- ({s}, {r}, {o})" for s, r, o in high_value_triples)
    prompt = PATTERN_MIGRATION_PROMPT.format(examples=examples, topic=topic, n=n_generate)
    raw = call_gemini(prompt)
    if not raw:
        return []

    triples = _parse_triples_json(raw, source="pattern_migration")
    print(f"  [方法一] 模式迁移生成 {len(triples)} 条三元组")
    return triples


# ==================== 方法二：节点关联扩展 ====================

def method2_node_expansion(topic, G, n_generate=6):
    topic_node = find_topic_node(G, topic)

    existing_triples = []
    if topic_node and topic_node in G:
        for nbr in G.successors(topic_node):
            data = G[topic_node][nbr]
            for rel in data.get("relations", []):
                existing_triples.append(f"({topic_node}, {rel}, {nbr})")
        for nbr in G.predecessors(topic_node):
            data = G[nbr][topic_node]
            for rel in data.get("relations", []):
                existing_triples.append(f"({nbr}, {rel}, {topic_node})")

    if not existing_triples:
        print(f"  [方法二] 图谱中未找到 [{topic}]，尝试模糊关联...")
        partial = [n for n in G.nodes() if topic[:2] in n or n[:2] in topic][:5]
        for node in partial:
            for nbr in G.successors(node):
                data = G[node][nbr]
                for rel in data.get("relations", []):
                    existing_triples.append(f"({node}, {rel}, {nbr})")
            if len(existing_triples) >= 10:
                break

    if not existing_triples:
        print(f"  [方法二] 无相关三元组，跳过")
        return []

    existing_str = "\n".join(f"- {t}" for t in existing_triples[:15])
    prompt = NODE_EXPAND_PROMPT.format(topic=topic, existing=existing_str, n=n_generate)
    raw = call_gemini(prompt)
    if not raw:
        return []

    triples = _parse_triples_json(raw, source="node_expansion")
    print(f"  [方法二] 节点关联扩展生成 {len(triples)} 条三元组")
    return triples


# ==================== 方法四：OwnThink 百科知识 ====================

def method4_ownthink_enrichment(topic, G, n_generate=6):
    url = f"https://api.ownthink.com/kg/knowledge?entity={urllib.parse.quote(topic)}"
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  [方法四] OwnThink 请求失败: {e}")
        return []

    if data.get("message") != "success" or not data.get("data"):
        print(f"  [方法四] OwnThink 无数据，跳过")
        return []

    entity_data = data["data"]
    desc = entity_data.get("desc", "")
    avp_list = entity_data.get("avp", [])

    knowledge_lines = []
    if desc:
        knowledge_lines.append(f"简介：{desc[:200]}")
    for avp in avp_list[:15]:
        if isinstance(avp, list) and len(avp) == 2:
            knowledge_lines.append(f"- {avp[0]}：{avp[1]}")

    if not knowledge_lines:
        print(f"  [方法四] OwnThink 信息为空，跳过")
        return []

    knowledge_str = "\n".join(knowledge_lines)
    prompt = OWNTHINK_PROMPT.format(topic=topic, knowledge=knowledge_str, n=n_generate)
    raw = call_gemini(prompt)
    if not raw:
        return []

    triples = _parse_triples_json(raw, source="ownthink")
    print(f"  [方法四] OwnThink 百科生成 {len(triples)} 条三元组")
    return triples


# ==================== 方法三：话题类比 ====================

def method3_topic_analogy(topic, G, top_k_similar=3):
    graph_nodes = [n for n in G.nodes() if 1 <= len(n) <= 6 and n not in NOISE_NODES]

    if HOWNET_AVAILABLE:
        sims = [(node, sememe_similarity(topic, node)) for node in graph_nodes]
        sims = [(n, s) for n, s in sims if s > 0]
        sims.sort(key=lambda x: -x[1])
        similar_nodes = [n for n, s in sims[:top_k_similar]]
    else:
        topic_chars = set(topic)
        sims = [(n, len(topic_chars & set(n)) / max(len(topic_chars | set(n)), 1))
                for n in graph_nodes]
        sims.sort(key=lambda x: -x[1])
        similar_nodes = [n for n, s in sims[:top_k_similar] if s > 0]

    if not similar_nodes:
        print(f"  [方法三] 未找到相似话题，跳过")
        return []

    print(f"  [方法三] 相似话题: {similar_nodes}")

    new_triples = []
    for similar in similar_nodes:
        if similar not in G:
            continue
        for nbr in G.successors(similar):
            data = G[similar][nbr]
            hv_rels = [r for r in data.get("relations", []) if r in HIGH_VALUE_RELATIONS]
            if hv_rels:
                new_triples.append({
                    "subject": topic,
                    "relation": hv_rels[0],
                    "object": nbr,
                    "source_type": f"analogy:{similar}",
                })

    seen, deduped = set(), []
    for t in new_triples:
        key = (t["subject"], t["relation"], t["object"])
        if key not in seen:
            seen.add(key)
            deduped.append(t)

    print(f"  [方法三] 话题类比迁移 {len(deduped)} 条三元组")
    return deduped


# ==================== 工具函数 ====================

def _parse_triples_json(raw, source="unknown"):
    if "```" in raw:
        parts = raw.split("```")
        for p in parts:
            if "[" in p:
                raw = p
                break
        if raw.startswith("json"):
            raw = raw[4:]

    start = raw.find("[")
    end   = raw.rfind("]") + 1
    if start == -1 or end == 0:
        return []

    try:
        items = json.loads(raw[start:end])
    except json.JSONDecodeError:
        return []

    triples = []
    for item in items:
        if not isinstance(item, dict):
            continue
        subj = item.get("subject", "").strip()
        rel  = item.get("relation", "").strip()
        obj  = item.get("object", "").strip()
        if subj and rel and obj:
            triples.append({"subject": subj, "relation": rel, "object": obj, "source_type": source})
    return triples


def _add_triples_to_graph(G, triples):
    added = 0
    for t in triples:
        subj = t["subject"]
        rel  = t["relation"]
        obj  = t["object"]
        source_type   = t.get("source_type", "expanded")
        is_high_value = rel in HIGH_VALUE_RELATIONS

        if subj in NOISE_NODES or obj in NOISE_NODES:
            continue

        for node in (subj, obj):
            if not G.has_node(node):
                G.add_node(node, sources=set(), degree_high=0)
            G.nodes[node]["sources"].add(source_type)

        if G.has_edge(subj, obj):
            if rel not in G[subj][obj]["relations"]:
                G[subj][obj]["relations"].append(rel)
            if is_high_value:
                G[subj][obj]["high_value"] = True
        else:
            G.add_edge(subj, obj, relations=[rel], high_value=is_high_value, source_type=source_type)
            added += 1

        if is_high_value:
            G.nodes[subj]["degree_high"] = G.nodes[subj].get("degree_high", 0) + 1
            G.nodes[obj]["degree_high"]  = G.nodes[obj].get("degree_high", 0) + 1

    return added


# ==================== 主接口 ====================

def expand_topic(topic, G=None, methods=(1, 2, 3, 4), verbose=True):
    """
    为 topic 生成扩展三元组，返回扩充后的图谱副本（原图不变）。
    """
    import copy

    if G is None:
        if not os.path.exists(GRAPH_PATH):
            import networkx as nx
            print("  [graph_expander] 图谱文件不存在，返回空图")
            return nx.DiGraph()
        with open(GRAPH_PATH, "rb") as f:
            G = pickle.load(f)

    G_expanded = copy.deepcopy(G)
    all_triples = []

    if 1 in methods:
        if verbose:
            print(f"\n  [扩展] 方法一：模式迁移...")
        all_triples.extend(method1_pattern_migration(topic, G))

    if 2 in methods:
        if verbose:
            print(f"\n  [扩展] 方法二：节点关联扩展...")
        all_triples.extend(method2_node_expansion(topic, G))

    if 3 in methods:
        if verbose:
            print(f"\n  [扩展] 方法三：话题类比...")
        all_triples.extend(method3_topic_analogy(topic, G))

    if 4 in methods:
        if verbose:
            print(f"\n  [扩展] 方法四：OwnThink 百科...")
        all_triples.extend(method4_ownthink_enrichment(topic, G))

    seen, unique_triples = set(), []
    for t in all_triples:
        key = (t["subject"], t["relation"], t["object"])
        if key not in seen:
            seen.add(key)
            unique_triples.append(t)

    added = _add_triples_to_graph(G_expanded, unique_triples)

    if verbose:
        print(f"\n  [扩展] 共生成 {len(unique_triples)} 条三元组（去重后），新增边 {added} 条")
        topic_node = find_topic_node(G_expanded, topic)
        if topic_node:
            print(f"  [扩展] [{topic}] 节点度数: 原={G.degree(topic_node) if topic_node in G else 0} → 扩展后={G_expanded.degree(topic_node)}")

    return G_expanded, unique_triples


def expand_and_save(topic, output_path=None, methods=(1, 2, 3)):
    """扩展并保存到新的图谱文件（不覆盖原图）"""
    if output_path is None:
        output_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..",
            "data", "knowledge_graph_expanded.pkl"
        )
    with open(GRAPH_PATH, "rb") as f:
        G = pickle.load(f)

    G_expanded, triples = expand_topic(topic, G, methods=methods)

    with open(output_path, "wb") as f:
        pickle.dump(G_expanded, f)

    print(f"\n  扩展图谱已保存: {output_path}")
    return G_expanded, triples


# ==================== 主程序（演示） ====================

def main():
    test_topics = ["躺平", "考研", "相亲"]
    with open(GRAPH_PATH, "rb") as f:
        G = pickle.load(f)

    for topic in test_topics:
        print(f"\n{'='*50}")
        print(f"扩展话题：【{topic}】")
        G_exp, triples = expand_topic(topic, G, methods=(1, 2, 3))
        print(f"\n  新生成的三元组预览（前10条）：")
        for t in triples[:10]:
            star = "⭐" if t["relation"] in HIGH_VALUE_RELATIONS else "-"
            print(f"    {star} ({t['subject']}, {t['relation']}, {t['object']})  [{t['source_type']}]")


if __name__ == "__main__":
    main()
