"""
graph_expander.py
知识图谱扩展器：用 3 种方法为新话题动态生成三元组，扩充图谱。

方法一：模式迁移（Pattern Migration）
  - 取现有高价值三元组作为模板
  - 让 Gemini 对新话题生成同样模式的三元组
  - e.g. 结婚[被视为]有空时顺便做的事 → 打工人[被视为]?

方法二：节点关联扩展（Node Association Expansion）
  - 对新话题的语义邻近节点，提取二跳子图内的三元组
  - 在已有图里找与话题语义最近的节点，把它们的边借给新话题

方法三：话题类比（Topic Analogy via HowNet）
  - 用 HowNet 义原相似度找已有图中最相似的话题节点
  - 把那个相似话题的高价值三元组复制并替换主语为新话题

最终：三种方法的三元组合并去重，加入临时图（不修改原图）。
对外暴露：expand_topic(topic, G) → 返回扩充后的图（临时副本）
"""

import os
import sys
import json
import time
import pickle
from collections import defaultdict

from google import genai
from google.genai import errors as genai_errors
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from graph_builder import HIGH_VALUE_RELATIONS, NOISE_NODES

GRAPH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "knowledge_graph.pkl")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = "gemini-2.5-pro"

client = genai.Client(api_key=GEMINI_API_KEY)

# ==================== HowNet（可选，用于方法三） ====================

try:
    import OpenHowNet
    _HOWNET_AVAILABLE = True
except ImportError:
    _HOWNET_AVAILABLE = False

_hownet = None

def _get_hownet():
    global _hownet
    if not _HOWNET_AVAILABLE:
        return None
    if _hownet is None:
        _hownet = OpenHowNet.HowNetDict()
    return _hownet


def _get_sememe_set(word):
    hownet = _get_hownet()
    if hownet is None:
        return frozenset()
    results = hownet.get_sememes_by_word(word)
    sememes = set()
    for r in results:
        for s in r.get("sememes", []):
            sememes.add(str(s))
    return frozenset(sememes)


def _sememe_similarity(word_a, word_b):
    """Jaccard 相似度（基于义原集合）"""
    sa = _get_sememe_set(word_a)
    sb = _get_sememe_set(word_b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# ==================== Gemini 调用 ====================

def call_gemini(prompt, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(model=MODEL, contents=prompt)
            return response.text.strip() if response.text else ""
        except genai_errors.ClientError as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e) or "503" in str(e) or "UNAVAILABLE" in str(e):
                wait = 15 * (2 ** attempt)
                print(f"  限流/过载，等待 {wait}s...")
                time.sleep(wait)
            else:
                print(f"  API 错误: {e}")
                return ""
        except Exception as e:
            print(f"  错误: {e}")
            return ""
    return ""


# ==================== 方法一：模式迁移 ====================

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


def method1_pattern_migration(topic, G, n_templates=6, n_generate=8):
    """
    方法一：从图谱中取高价值三元组作为模板，让 Gemini 生成新话题的相似三元组。
    """
    # 收集高价值三元组（随机采样 n_templates 条，优先选 relation 多样的）
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


def method2_node_expansion(topic, G, n_generate=6):
    """
    方法二：找话题节点的现有三元组（含一跳邻居），让 Gemini 补充扩展。
    """
    # 找话题节点（精确 + 模糊）
    topic_node = _find_topic_node_simple(G, topic)

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
        # 图里没有这个话题，用 pattern migration 的结果补充
        print(f"  [方法二] 图谱中未找到 [{topic}]，尝试模糊关联...")
        # 找字面包含关系的节点
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

    # 截取不超过 15 条
    existing_str = "\n".join(f"- {t}" for t in existing_triples[:15])
    prompt = NODE_EXPAND_PROMPT.format(topic=topic, existing=existing_str, n=n_generate)

    raw = call_gemini(prompt)
    if not raw:
        return []

    triples = _parse_triples_json(raw, source="node_expansion")
    print(f"  [方法二] 节点关联扩展生成 {len(triples)} 条三元组")
    return triples


# ==================== 方法三：话题类比 ====================

def method3_topic_analogy(topic, G, top_k_similar=3):
    """
    方法三：用 HowNet 义原相似度找图谱中最相似的已有话题，
    把它们的高价值三元组复制并替换 subject 为新话题。
    """
    # 如果 HowNet 不可用，用字符串 overlap 代替
    graph_nodes = [n for n in G.nodes() if 1 <= len(n) <= 6 and n not in NOISE_NODES]

    if _HOWNET_AVAILABLE:
        sims = []
        for node in graph_nodes:
            sim = _sememe_similarity(topic, node)
            if sim > 0:
                sims.append((node, sim))
        sims.sort(key=lambda x: -x[1])
        similar_nodes = [n for n, s in sims[:top_k_similar] if s > 0]
    else:
        # 字符重叠作为相似度
        topic_chars = set(topic)
        sims = [(n, len(topic_chars & set(n)) / max(len(topic_chars | set(n)), 1))
                for n in graph_nodes]
        sims.sort(key=lambda x: -x[1])
        similar_nodes = [n for n, s in sims[:top_k_similar] if s > 0]

    if not similar_nodes:
        print(f"  [方法三] 未找到相似话题，跳过")
        return []

    print(f"  [方法三] 相似话题: {similar_nodes}")

    # 收集相似话题的高价值三元组，替换 subject 为新话题
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

    # 去重（避免重复 object）
    seen = set()
    deduped = []
    for t in new_triples:
        key = (t["subject"], t["relation"], t["object"])
        if key not in seen:
            seen.add(key)
            deduped.append(t)

    print(f"  [方法三] 话题类比迁移 {len(deduped)} 条三元组")
    return deduped


# ==================== 工具函数 ====================

def _find_topic_node_simple(G, topic):
    if topic in G:
        return topic
    candidates = [n for n in G.nodes() if topic in n or n in topic]
    if candidates:
        return max(candidates, key=lambda n: G.degree(n))
    return None


def _parse_triples_json(raw, source="unknown"):
    """解析 Gemini 返回的三元组 JSON，容错处理"""
    # 清理 markdown 代码块
    if "```" in raw:
        parts = raw.split("```")
        for p in parts:
            if "[" in p:
                raw = p
                break
        if raw.startswith("json"):
            raw = raw[4:]

    # 找 JSON 数组
    start = raw.find("[")
    end = raw.rfind("]") + 1
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
        rel = item.get("relation", "").strip()
        obj = item.get("object", "").strip()
        if subj and rel and obj:
            triples.append({
                "subject": subj,
                "relation": rel,
                "object": obj,
                "source_type": source,
            })
    return triples


def _add_triples_to_graph(G, triples):
    """把三元组列表加入图（原地修改）"""
    import networkx as nx
    added = 0
    for t in triples:
        subj = t["subject"]
        rel = t["relation"]
        obj = t["object"]
        source_type = t.get("source_type", "expanded")

        if subj in NOISE_NODES or obj in NOISE_NODES:
            continue

        is_high_value = rel in HIGH_VALUE_RELATIONS

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
            G.add_edge(subj, obj,
                       relations=[rel],
                       high_value=is_high_value,
                       source_type=source_type)
            added += 1

        if is_high_value:
            G.nodes[subj]["degree_high"] = G.nodes[subj].get("degree_high", 0) + 1
            G.nodes[obj]["degree_high"] = G.nodes[obj].get("degree_high", 0) + 1

    return added


# ==================== 主接口 ====================

def expand_topic(topic, G=None, methods=(1, 2, 3), verbose=True):
    """
    为 topic 生成扩展三元组，返回扩充后的图谱副本。

    参数：
      topic   - 话题词
      G       - 原始 NetworkX 图（传 None 则自动加载）
      methods - 使用哪些方法，默认全部 (1, 2, 3)
      verbose - 是否打印日志

    返回：
      G_expanded - 包含原图 + 新三元组的临时图（原图不变）
    """
    import copy
    import networkx as nx

    if G is None:
        if not os.path.exists(GRAPH_PATH):
            print("  [graph_expander] 图谱文件不存在，返回空图")
            return nx.DiGraph()
        with open(GRAPH_PATH, "rb") as f:
            G = pickle.load(f)

    G_expanded = copy.deepcopy(G)

    all_triples = []

    if 1 in methods:
        if verbose:
            print(f"\n  [扩展] 方法一：模式迁移...")
        t1 = method1_pattern_migration(topic, G)
        all_triples.extend(t1)

    if 2 in methods:
        if verbose:
            print(f"\n  [扩展] 方法二：节点关联扩展...")
        t2 = method2_node_expansion(topic, G)
        all_triples.extend(t2)

    if 3 in methods:
        if verbose:
            print(f"\n  [扩展] 方法三：话题类比...")
        t3 = method3_topic_analogy(topic, G)
        all_triples.extend(t3)

    # 去重
    seen = set()
    unique_triples = []
    for t in all_triples:
        key = (t["subject"], t["relation"], t["object"])
        if key not in seen:
            seen.add(key)
            unique_triples.append(t)

    added = _add_triples_to_graph(G_expanded, unique_triples)

    if verbose:
        print(f"\n  [扩展] 共生成 {len(unique_triples)} 条三元组（去重后），新增边 {added} 条")
        topic_node = _find_topic_node_simple(G_expanded, topic)
        if topic_node:
            print(f"  [扩展] [{topic}] 节点度数: 原={G.degree(topic_node) if topic_node in G else 0} → 扩展后={G_expanded.degree(topic_node)}")

    return G_expanded, unique_triples


# ==================== 持久化扩展（可选） ====================

def expand_and_save(topic, output_path=None, methods=(1, 2, 3)):
    """
    扩展并保存到新的图谱文件（不覆盖原图）。
    output_path 默认为 data/knowledge_graph_expanded.pkl
    """
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
