"""
cross_domain_finder.py
基于 HowNet 义原，找话题的冲突概念。

义原张力对（TENSION_PAIRS）从知识图谱高价值 relation 边自动提取：
  - 高价值边 (subject, relation, object) 说明两者在语义上存在冲突
  - 提取 subject 义原集合 S 和 object 义原集合 O
  - S 中的义原 和 O 中的义原 互为张力对
  - 覆盖你的脱口秀语料里真实出现的笑点结构

提供两个接口：
1. find_conflict_by_sememe(topic)  → 路径D：义原张力冲突词
2. find_cross_domain_analogs(topic) → 跨域同构词（兜底）
"""

import os
import sys
import io
import logging
import pickle
import contextlib
from collections import defaultdict
from functools import lru_cache

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import OpenHowNet
    _HOWNET_AVAILABLE = True
except ImportError:
    _HOWNET_AVAILABLE = False

try:
    import jieba
    _JIEBA_AVAILABLE = True
except ImportError:
    _JIEBA_AVAILABLE = False

# ==================== 配置 ====================

GRAPH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "knowledge_graph.pkl")

# 过滤太泛的义原
GENERIC_SEMEMES = {
    "entity|实体", "thing|物", "abstract|抽象物",
    "act|行为", "event|事件", "fact|事情",
    "attribute|属性", "quantity|数量",
}

# 强反差义原：用于 find_cross_domain_analogs
CONTRAST_SEMEMES = {
    "punish|惩罚", "restrict|限制", "confine|禁闭", "forbid|禁止",
    "royal|皇", "official|官", "HeadOfState|元首", "God|神",
    "control|控制", "dominate|统治",
    "animal|动物", "plant|植物", "nature|自然物", "insect|昆虫",
    "military|军", "army|军队", "weapon|武器",
    "commerce|商业", "sell|卖", "buy|买", "pay|付",
    "Freedom|自由度", "free|自由",
    "die|死", "destroy|破坏",
}

# 功能义原：义原反查候选词时用
FUNCTIONAL_SEMEMES = {
    "Occupation|职位", "earn|赚", "alive|活着", "engage|从事",
    "affairs|事务", "duty|责任", "manage|管理", "produce|制造",
    "study|学习", "spouse|配偶", "family|家庭", "GetMarried|结婚",
    "fund|资金", "finance|金融", "wealth|钱财", "teach|教",
}

# ==================== HowNet 初始化 ====================

_hownet = None

def _get_hownet():
    global _hownet
    if not _HOWNET_AVAILABLE:
        return None
    if _hownet is None:
        with contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            logging.getLogger("jieba").setLevel(logging.WARNING)
            _hownet = OpenHowNet.HowNetDict()
    return _hownet


@lru_cache(maxsize=5000)
def _get_sememe_set(word):
    """获取词的义原集合（合并所有 sense，过滤过泛义原）"""
    hownet = _get_hownet()
    if hownet is None:
        return frozenset()
    results = hownet.get_sememes_by_word(word)
    sememes = set()
    for r in results:
        for s in r.get("sememes", []):
            s_str = str(s)
            if s_str not in GENERIC_SEMEMES:
                sememes.add(s_str)
    return frozenset(sememes)


def _get_sememe_set_with_fallback(topic):
    """
    获取话题义原，多级兜底：
    1. 直接查词
    2. jieba 分词后合并
    3. 逐字查
    """
    sem = _get_sememe_set(topic)
    if sem:
        return sem

    if _JIEBA_AVAILABLE:
        tokens = [t for t in jieba.lcut(topic) if len(t) >= 1]
        merged = set()
        for t in tokens:
            merged |= set(_get_sememe_set(t))
        if merged:
            return frozenset(merged)

    merged = set()
    for char in topic:
        merged |= set(_get_sememe_set(char))
    return frozenset(merged)


# ==================== 义原张力对（自动从图谱提取） ====================

_tension_pairs = None  # 懒加载缓存


def _build_tension_pairs():
    """
    从知识图谱高价值 relation 边自动提取义原张力对。

    逻辑：
      高价值 relation 边 (subject, relation, object) 表示两者存在语义冲突。
      subject 的义原集合 S 和 object 的义原集合 O 互为张力。
      → 遍历所有高价值边，合并得到 tension_pairs[义原A] = {义原B, ...}
    """
    from graph_builder import HIGH_VALUE_RELATIONS

    if not os.path.exists(GRAPH_PATH):
        return {}

    with open(GRAPH_PATH, "rb") as f:
        G = pickle.load(f)

    tension = defaultdict(set)
    edge_count = 0

    for subj, obj, data in G.edges(data=True):
        relations = data.get("relations", [])
        if not any(r in HIGH_VALUE_RELATIONS for r in relations):
            continue

        subj_sems = _get_sememe_set_with_fallback(subj) - GENERIC_SEMEMES
        obj_sems = _get_sememe_set_with_fallback(obj) - GENERIC_SEMEMES

        if not subj_sems or not obj_sems:
            continue

        # 双向：subj义原 ↔ obj义原
        for s in subj_sems:
            tension[s] |= obj_sems
        for o in obj_sems:
            tension[o] |= subj_sems

        edge_count += 1

    return dict(tension)


def get_tension_pairs():
    """懒加载并缓存义原张力对"""
    global _tension_pairs
    if _tension_pairs is None:
        _tension_pairs = _build_tension_pairs()
    return _tension_pairs


# ==================== 核心接口 ====================

def find_conflict_by_sememe(topic, top_k=5):
    """
    路径D：用义原张力对找与话题冲突的词。

    返回格式与 humor_slot_finder 兼容：
    [{"slot", "path", "relation", "score", "topic_sememes", "conflict_sememes", "description"}]
    """
    if not _HOWNET_AVAILABLE:
        return []

    hownet = _get_hownet()
    if hownet is None:
        return []

    topic_sememes = _get_sememe_set_with_fallback(topic)
    if not topic_sememes:
        return []

    tension_pairs = get_tension_pairs()
    if not tension_pairs:
        return []

    # 找所有张力对立义原
    tension_targets = set()
    triggered_pairs = {}  # conflict_sem -> 触发它的 topic 义原
    for sem in topic_sememes:
        if sem in tension_pairs:
            for conflict_sem in tension_pairs[sem]:
                tension_targets.add(conflict_sem)
                triggered_pairs[conflict_sem] = sem

    if not tension_targets:
        return []

    # 义原反查：找包含对立义原的词
    word_conflicts = defaultdict(set)
    for conflict_sem in tension_targets:
        sem_list = hownet.get_sememe(conflict_sem)
        if not sem_list:
            continue
        for s in sem_list[0].get_senses():
            if s.zh_word and 1 <= len(s.zh_word) <= 6:
                word_conflicts[s.zh_word].add(conflict_sem)

    # 打分
    results = []
    for word, conflict_sems in word_conflicts.items():
        if word == topic:
            continue

        w_sememes = _get_sememe_set(word)
        shared = (topic_sememes & w_sememes) - GENERIC_SEMEMES
        actual_conflict = conflict_sems & w_sememes

        if not actual_conflict:
            continue

        triggering = {triggered_pairs[c] for c in actual_conflict if c in triggered_pairs}
        score = len(actual_conflict) * 2 + len(shared)

        trigger_zh = [s.split('|')[-1] for s in sorted(triggering)]
        conflict_zh = [s.split('|')[-1] for s in sorted(actual_conflict)]
        description = f"{topic}[{', '.join(trigger_zh)}] ↔ {word}[{', '.join(conflict_zh)}]"

        results.append({
            "slot": word,
            "path": [topic, word],
            "relation": "HowNet义原张力",
            "score": float(score),
            "topic_sememes": sorted(triggering),
            "conflict_sememes": sorted(actual_conflict),
            "description": description,
        })

    results.sort(key=lambda x: -x["score"])

    filtered = []
    for r in results:
        w_sem = _get_sememe_set(r["slot"])
        # 过滤：过于多义的字（义原数>20，通常是"开/上/打"这类极度多义字）
        if len(w_sem) > 20:
            continue
        # 过滤：与话题义原高度重叠（近义词）
        overlap_ratio = len(topic_sememes & w_sem) / max(len(topic_sememes), 1)
        if overlap_ratio >= 0.8:
            continue
        # 截断 description 中的义原列表
        conflict_zh = [s.split('|')[-1] for s in sorted(r["conflict_sememes"])[:4]]
        trigger_zh = [s.split('|')[-1] for s in sorted(r["topic_sememes"])[:3]]
        r["description"] = f"{topic}[{', '.join(trigger_zh)}] ↔ {r['slot']}[{', '.join(conflict_zh)}]"
        filtered.append(r)

    return filtered[:top_k]


def find_cross_domain_analogs(topic, min_shared=2, top_k=5):
    """
    找与 topic 有共性但来自不同语义域的词（跨域同构）。
    """
    if not _HOWNET_AVAILABLE:
        return []

    topic_sememes = _get_sememe_set_with_fallback(topic)
    if not topic_sememes:
        return []

    # 候选词：与 topic 共享至少一个功能义原
    hownet = _get_hownet()
    func_overlap = topic_sememes & FUNCTIONAL_SEMEMES or topic_sememes
    candidates = set()
    for sem_str in func_overlap:
        sem_list = hownet.get_sememe(sem_str)
        if not sem_list:
            continue
        for s in sem_list[0].get_senses():
            if s.zh_word and 1 <= len(s.zh_word) <= 6:
                candidates.add(s.zh_word)

    results = []
    for word in candidates:
        if word == topic:
            continue
        w_sememes = _get_sememe_set(word)
        if not w_sememes:
            continue

        shared = (topic_sememes & w_sememes) - GENERIC_SEMEMES
        if len(shared) < min_shared:
            continue

        contrast = (w_sememes - topic_sememes) & CONTRAST_SEMEMES
        if not contrast:
            continue

        score = len(shared) * len(contrast)
        shared_list = sorted(shared)
        contrast_list = sorted(contrast)
        description = (
            f"{topic} 和 {word} 共享[{'、'.join(s.split('|')[-1] for s in shared_list[:3])}]，"
            f"但 {word} 有[{'、'.join(s.split('|')[-1] for s in contrast_list[:3])}]"
        )
        results.append({
            "slot": word,
            "path": [topic, word],
            "relation": "HowNet跨域同构",
            "score": float(score),
            "shared_sememes": shared_list,
            "contrast_sememes": contrast_list,
            "description": description,
        })

    results.sort(key=lambda x: -x["score"])
    return results[:top_k]


def describe_slot(slot_dict):
    return (
        f"[{slot_dict['slot']}]  score={slot_dict['score']:.0f}  "
        f"{slot_dict.get('description', '')}"
    )


# ==================== 主程序（演示） ====================

def main():
    print("构建义原张力对...")
    pairs = get_tension_pairs()
    print(f"义原张力对覆盖 {len(pairs)} 个义原\n")

    test_topics = ["打工人", "结婚", "老板", "考研", "贫穷", "躺平"]

    for topic in test_topics:
        print(f"\n{'='*50}")
        print(f"话题: 【{topic}】")

        print("  [路径D] 义原张力冲突：")
        conflicts = find_conflict_by_sememe(topic, top_k=3)
        if not conflicts:
            print("    未找到")
        for i, s in enumerate(conflicts):
            print(f"    #{i+1} score={s['score']:.0f}  {s['description']}")

        print("  [跨域同构]：")
        analogs = find_cross_domain_analogs(topic, top_k=3)
        if not analogs:
            print("    未找到")
        for i, s in enumerate(analogs):
            print(f"    #{i+1} {describe_slot(s)}")


if __name__ == "__main__":
    main()
