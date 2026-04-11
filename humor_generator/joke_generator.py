"""
joke_generator.py
五路径生成笑话候选，对应 Witscript 3 (Paper 1) 的多路径生成策略。

路径A：图谱冲突三元组 → Gemini 生成
路径B：RAG 检索梗库 → Gemini 填充
路径C：话题 + Humor Slot → Gemini 自由发挥
路径D：HowNet 义原张力冲突 → Gemini 生成
路径E：词林跨域对比（图谱过滤）→ Gemini 生成
"""

import json
import os
import sys
import time
from google.cloud import storage
from google import genai
from google.genai import errors as genai_errors
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from graph_builder import HIGH_VALUE_RELATIONS
from humor_slot_finder import load_graph, find_humor_slots, get_subgraph_triples, find_topic_node
from rag_retriever import load_memes, load_or_build_embeddings, retrieve as vector_retrieve
from cross_domain_finder import find_conflict_by_sememe
from graph_expander import expand_topic
from cilin_finder import find_contrast_with_graph, find_similar_cilin

# ==================== 配置 ====================
PROJECT_ID = "gen-lang-client-0577448366"
BUCKET_NAME = "xhs-humor-data"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = "gemini-2.5-pro"

# RAG 全局缓存（避免每次调用重复加载）
_rag_memes = None
_rag_embeddings = None


def _get_rag():
    global _rag_memes, _rag_embeddings
    if _rag_memes is None:
        _rag_memes = load_memes()
        _rag_embeddings = load_or_build_embeddings(_rag_memes)
    return _rag_memes, _rag_embeddings

# ==================== 初始化 ====================
client = genai.Client(api_key=GEMINI_API_KEY)
storage_client = storage.Client(project=PROJECT_ID)
bucket = storage_client.bucket(BUCKET_NAME)


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


# ==================== 三路径生成 ====================

PROMPT_A = """你是一个中文脱口秀编剧。请根据以下知识图谱三元组，写一段2-3句的脱口秀笑话。

话题：{topic}
冲突节点（Humor Slot）：{slot}
相关三元组：
{triples}

要求：
- 利用三元组中的反差、荒诞因果或概念颠覆制造笑点
- 语气口语化，像李诞或脱口秀演员的风格
- 直接输出笑话，不要解释

笑话："""

PROMPT_B = """你是一个中文脱口秀编剧。请把以下梗融入到关于"{topic}"的笑话中。

话题：{topic}
冲突节点（Humor Slot）：{slot}
相关梗：
{memes}

要求：
- 自然地把梗的核心逻辑用到话题上，不要生硬拼接
- 2-3句，口语化，脱口秀风格
- 直接输出笑话，不要解释

笑话："""

PROMPT_C = """你是一个中文脱口秀编剧。请写一段关于"{topic}"的笑话。

话题：{topic}
冲突切入点：{slot}
这两个概念之间的关系：{relation}

要求：
- 从"冲突切入点"的角度找反差或荒诞感
- 2-3句，口语化，脱口秀风格
- 直接输出笑话，不要解释

笑话："""

PROMPT_E = """你是一个中文脱口秀编剧。请根据以下跨域对比，写一段笑话。

话题：{topic}
跨域对比词：{contrasts}

说明：这些词和话题来自完全不同的语义领域（如"活动域"vs"社会域"），但在现实中有某种隐秘的相似结构，把它们放在一起会产生荒诞感。

要求：
- 找出话题和对比词之间意想不到的共同点或因果关系
- 2-3句，口语化，脱口秀风格
- 直接输出笑话，不要解释

笑话："""

PROMPT_D = """你是一个中文脱口秀编剧。请根据以下语义冲突，写一段笑话。

话题：{topic}
语义冲突对：{conflicts}

说明：这些冲突对来自语言学分析——话题词和冲突词在某些核心属性上相似，但在关键维度上截然相反（比如"打工[职位]↔囚犯[惩罚]"：都是被安排任务，但一个付工资一个受惩罚）。

要求：
- 利用这种"似乎相同但本质相反"的荒诞感制造笑点
- 2-3句，口语化，脱口秀风格
- 直接输出笑话，不要解释

笑话："""


def generate_jokes(topic, verbose=True):
    """
    生成五路径笑话候选。

    返回：[{"path": "A/B/C/D/E", "joke": str, "slot": str, "triples": [...]}]
    """
    if verbose:
        print(f"\n{'='*50}")
        print(f"话题：【{topic}】")

    # 1. 加载图谱，找 Humor Slot（若无结果则触发图谱扩展）
    G = load_graph()
    slots = find_humor_slots(G, topic, top_k=3)
    if not slots:
        if verbose:
            print(f"  图谱中未找到 [{topic}] 的 Humor Slot，触发图谱扩展...")
        G, _ = expand_topic(topic, G, methods=(1, 2, 3), verbose=verbose)
        slots = find_humor_slots(G, topic, top_k=3)
    if not slots:
        print("  扩展后仍未找到 Humor Slot，无法生成路径A/B/C")
        return []

    best_slot = slots[0]
    slot_name = best_slot["slot"]
    slot_relation = best_slot["relation"]

    topic_node = find_topic_node(G, topic) or topic
    triples = get_subgraph_triples(G, topic_node, slot_name, max_triples=6)

    if verbose:
        print(f"Humor Slot：【{slot_name}】  relation={slot_relation}")
        print(f"三元组数：{len(triples)}")

    # 2. 格式化三元组
    triples_str = "\n".join(
        f"{'⭐' if t['high_value'] else '-'} ({t['subject']}, {t['relation']}, {t['object']})"
        for t in triples
    )

    # 3. 向量检索梗库
    memes, embeddings = _get_rag()
    retrieved = vector_retrieve(topic, slot_name, top_k=3, memes=memes, embeddings=embeddings)
    memes_str = "\n---\n".join(retrieved) if retrieved else "（未找到相关梗）"

    # 4. 三路径生成
    candidates = []

    if verbose:
        print("\n生成路径A（三元组驱动）...")
    joke_a = call_gemini(PROMPT_A.format(topic=topic, slot=slot_name, triples=triples_str))
    if joke_a:
        candidates.append({"path": "A", "joke": joke_a, "slot": slot_name, "triples": triples})
        if verbose:
            print(f"  {joke_a}")

    if verbose:
        print("\n生成路径B（RAG梗库）...")
    joke_b = call_gemini(PROMPT_B.format(topic=topic, slot=slot_name, memes=memes_str))
    if joke_b:
        candidates.append({"path": "B", "joke": joke_b, "slot": slot_name, "triples": triples})
        if verbose:
            print(f"  {joke_b}")

    if verbose:
        print("\n生成路径C（自由发挥）...")
    joke_c = call_gemini(PROMPT_C.format(topic=topic, slot=slot_name, relation=slot_relation))
    if joke_c:
        candidates.append({"path": "C", "joke": joke_c, "slot": slot_name, "triples": triples})
        if verbose:
            print(f"  {joke_c}")

    # 5. 路径D：HowNet 义原张力冲突
    if verbose:
        print("\n生成路径D（HowNet义原冲突）...")
    hownet_conflicts = find_conflict_by_sememe(topic, top_k=3)
    if hownet_conflicts:
        conflicts_str = "\n".join(f"- {c['description']}" for c in hownet_conflicts)
        joke_d = call_gemini(PROMPT_D.format(topic=topic, conflicts=conflicts_str))
        if joke_d:
            d_slot = hownet_conflicts[0]["slot"]
            candidates.append({"path": "D", "joke": joke_d, "slot": d_slot, "triples": []})
            if verbose:
                print(f"  {joke_d}")
    else:
        if verbose:
            print("  未找到义原冲突，跳过路径D")

    # 6. 路径E：词林跨域对比（图谱过滤）
    if verbose:
        print("\n生成路径E（词林跨域对比）...")
    cilin_contrasts = find_contrast_with_graph(topic, G, top_k=3)
    if not cilin_contrasts:
        # 兜底：同小类近义词也可以产生"说A其实更像B"的笑点
        cilin_contrasts = find_similar_cilin(topic, top_k=3)
    if cilin_contrasts:
        contrasts_str = "\n".join(f"- {c['description']}" for c in cilin_contrasts)
        joke_e = call_gemini(PROMPT_E.format(topic=topic, contrasts=contrasts_str))
        if joke_e:
            e_slot = cilin_contrasts[0]["slot"]
            candidates.append({"path": "E", "joke": joke_e, "slot": e_slot, "triples": []})
            if verbose:
                print(f"  {joke_e}")
    else:
        if verbose:
            print("  未找到词林对比词，跳过路径E")

    return candidates


# ==================== 主程序（演示） ====================

def main():
    test_topics = ["结婚", "上班", "贫穷"]
    for topic in test_topics:
        candidates = generate_jokes(topic)
        print(f"\n共生成 {len(candidates)} 个候选，等待 critic 评分...")
        print()


if __name__ == "__main__":
    main()
