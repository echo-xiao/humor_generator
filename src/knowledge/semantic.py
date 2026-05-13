"""
semantic.py
语义冲突查找：HowNet 义原张力 + 同义词词林跨域对比。

HowNet 路径（路径D）：义原张力冲突词
词林路径（路径E）：近义可区分词 + 跨域对比词
"""

import os
import io
import json
import logging
import pickle
import contextlib
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

# ==================== HowNet 初始化 ====================

try:
    import OpenHowNet
    HOWNET_AVAILABLE = True
except ImportError:
    HOWNET_AVAILABLE = False

try:
    import jieba
    _JIEBA_AVAILABLE = True
except ImportError:
    _JIEBA_AVAILABLE = False

_hownet = None

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


def get_hownet():
    global _hownet
    if not HOWNET_AVAILABLE:
        return None
    if _hownet is None:
        with contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            logging.getLogger("jieba").setLevel(logging.WARNING)
            _hownet = OpenHowNet.HowNetDict()
    return _hownet


@lru_cache(maxsize=5000)
def get_sememe_set(word):
    """获取词的义原集合（合并所有 sense，过滤过泛义原）"""
    hownet = get_hownet()
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
    """获取话题义原，多级兜底：直接查词 → jieba 分词 → 逐字查"""
    sem = get_sememe_set(topic)
    if sem:
        return sem
    if _JIEBA_AVAILABLE:
        tokens = [t for t in jieba.lcut(topic) if len(t) >= 1]
        merged = set()
        for t in tokens:
            merged |= set(get_sememe_set(t))
        if merged:
            return frozenset(merged)
    merged = set()
    for char in topic:
        merged |= set(get_sememe_set(char))
    return frozenset(merged)


def sememe_similarity(word_a, word_b):
    """Jaccard 相似度（基于义原集合）"""
    sa = get_sememe_set(word_a)
    sb = get_sememe_set(word_b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# ==================== HowNet 义原张力对（自动从图谱提取） ====================

_tension_pairs = None

_GRAPH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "cache", "knowledge_graph.pkl")


def _build_tension_pairs():
    from .graph import HIGH_VALUE_RELATIONS
    if not os.path.exists(_GRAPH_PATH):
        return {}
    with open(_GRAPH_PATH, "rb") as f:
        G = pickle.load(f)

    tension = defaultdict(set)
    for subj, obj, data in G.edges(data=True):
        relations = data.get("relations", [])
        if not any(r in HIGH_VALUE_RELATIONS for r in relations):
            continue
        subj_sems = _get_sememe_set_with_fallback(subj) - GENERIC_SEMEMES
        obj_sems  = _get_sememe_set_with_fallback(obj)  - GENERIC_SEMEMES
        if not subj_sems or not obj_sems:
            continue
        for s in subj_sems:
            tension[s] |= obj_sems
        for o in obj_sems:
            tension[o] |= subj_sems

    return dict(tension)


def get_tension_pairs():
    global _tension_pairs
    if _tension_pairs is None:
        _tension_pairs = _build_tension_pairs()
    return _tension_pairs


# ==================== HowNet 接口 ====================

def find_conflict_by_sememe(topic, top_k=5):
    """路径D：用义原张力对找与话题冲突的词"""
    if not HOWNET_AVAILABLE:
        return []
    hownet = get_hownet()
    if hownet is None:
        return []

    topic_sememes = _get_sememe_set_with_fallback(topic)
    if not topic_sememes:
        return []

    tension_pairs = get_tension_pairs()
    if not tension_pairs:
        return []

    tension_targets = set()
    triggered_pairs = {}
    for sem in topic_sememes:
        if sem in tension_pairs:
            for conflict_sem in tension_pairs[sem]:
                tension_targets.add(conflict_sem)
                triggered_pairs[conflict_sem] = sem

    if not tension_targets:
        return []

    word_conflicts = defaultdict(set)
    for conflict_sem in tension_targets:
        sem_list = hownet.get_sememe(conflict_sem)
        if not sem_list:
            continue
        for s in sem_list[0].get_senses():
            if s.zh_word and 1 <= len(s.zh_word) <= 6:
                word_conflicts[s.zh_word].add(conflict_sem)

    results = []
    for word, conflict_sems in word_conflicts.items():
        if word == topic:
            continue
        w_sememes = get_sememe_set(word)
        shared = (topic_sememes & w_sememes) - GENERIC_SEMEMES
        actual_conflict = conflict_sems & w_sememes
        if not actual_conflict:
            continue

        triggering = {triggered_pairs[c] for c in actual_conflict if c in triggered_pairs}
        score = len(actual_conflict) * 2 + len(shared)
        trigger_zh  = [s.split('|')[-1] for s in sorted(triggering)]
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
        w_sem = get_sememe_set(r["slot"])
        if len(w_sem) > 20:
            continue
        overlap_ratio = len(topic_sememes & w_sem) / max(len(topic_sememes), 1)
        if overlap_ratio >= 0.8:
            continue
        conflict_zh = [s.split('|')[-1] for s in sorted(r["conflict_sememes"])[:4]]
        trigger_zh  = [s.split('|')[-1] for s in sorted(r["topic_sememes"])[:3]]
        r["description"] = f"{topic}[{', '.join(trigger_zh)}] ↔ {r['slot']}[{', '.join(conflict_zh)}]"
        filtered.append(r)

    return filtered[:top_k]


def find_cross_domain_analogs(topic, min_shared=2, top_k=5):
    """找与 topic 有共性但来自不同语义域的词（跨域同构，兜底用）"""
    if not HOWNET_AVAILABLE:
        return []

    topic_sememes = _get_sememe_set_with_fallback(topic)
    if not topic_sememes:
        return []

    hownet = get_hownet()
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
        w_sememes = get_sememe_set(word)
        if not w_sememes:
            continue
        shared = (topic_sememes & w_sememes) - GENERIC_SEMEMES
        if len(shared) < min_shared:
            continue
        contrast = (w_sememes - topic_sememes) & CONTRAST_SEMEMES
        if not contrast:
            continue
        score = len(shared) * len(contrast)
        shared_list   = sorted(shared)
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


# ==================== 词林初始化 ====================

import importlib.util as _ilu
_pkg = _ilu.find_spec("cilin")
_CILIN_JSON = Path(_pkg.origin).parent.parent / "data" / "cilin_tree.json"

_word_to_codes = None
_code_to_words = None


def _load_cilin():
    global _word_to_codes, _code_to_words
    if _word_to_codes is not None:
        return

    with open(_CILIN_JSON, encoding="utf-8") as f:
        data = json.load(f)

    word_to_codes = defaultdict(list)
    code_to_words = defaultdict(list)

    def walk(node, path):
        if isinstance(node, list):
            for item in node:
                if len(item) > 1 or not item[-1] in "=#@":
                    clean = item.rstrip("=#@")
                    if clean:
                        code = "".join(path)
                        word_to_codes[clean].append(code)
                        code_to_words[code].append(clean)
            return
        for key, sub in node.get("sub", {}).items():
            walk(sub, path + [key])

    for top_key, top_val in data.items():
        walk(top_val, [top_key])

    _word_to_codes = dict(word_to_codes)
    _code_to_words = dict(code_to_words)


def _prefix(code, level):
    cuts = [1, 2, 4, 5, 7]
    return code[:cuts[level - 1]] if len(code) >= cuts[level - 1] else code


def _get_codes(word):
    _load_cilin()
    return _word_to_codes.get(word, [])


def _get_words_by_prefix(prefix):
    _load_cilin()
    words = []
    for code, ws in _code_to_words.items():
        if code.startswith(prefix):
            words.extend(ws)
    return words


# ==================== 词林接口 ====================

def find_similar_cilin(topic, top_k=8):
    """同小类（level-3）不同词群（level-4）的近义可区分词 → "说A其实更像B"结构"""
    codes = _get_codes(topic)
    if not codes:
        return []

    results = []
    seen = set()
    for code in codes:
        l3 = _prefix(code, 3)
        l4 = _prefix(code, 4)
        for word in _get_words_by_prefix(l3):
            if word == topic or word in seen:
                continue
            for wc in _get_codes(word):
                if wc.startswith(l3) and not wc.startswith(l4):
                    seen.add(word)
                    results.append({
                        "slot": word,
                        "path": [topic, word],
                        "relation": "词林同小类",
                        "score": 2.0,
                        "topic_code": code,
                        "word_code": wc,
                        "description": f"{topic}[{l3}] ↔ {word}[{_prefix(wc, 3)}] 同域可区分",
                    })
                    break

    return results[:top_k]


def find_contrast_cilin(topic, top_k=5):
    """不同大类（level-1）的跨域对比词（无图谱过滤）"""
    _load_cilin()
    codes = _get_codes(topic)
    if not codes:
        return []

    topic_l1s = {_prefix(c, 1) for c in codes}
    L1_NAMES = {
        "A": "人", "B": "物", "C": "时地", "D": "社会",
        "E": "自然", "F": "事务", "G": "文化", "H": "活动",
        "I": "性状", "J": "程度", "K": "关联", "L": "助词",
    }
    GENERIC = {"人", "士", "物", "事", "地", "时", "类", "者", "家", "员", "方"}

    results = []
    seen = set()
    for l1 in L1_NAMES:
        if l1 in topic_l1s:
            continue
        for word in _get_words_by_prefix(l1):
            if word == topic or word in seen:
                continue
            if len(word) < 2 or len(word) > 5 or word in GENERIC:
                continue
            seen.add(word)
            topic_name = "/".join(L1_NAMES[l] for l in topic_l1s if l in L1_NAMES)
            word_name  = L1_NAMES.get(l1, l1)
            results.append({
                "slot": word,
                "path": [topic, word],
                "relation": "词林跨域",
                "score": 3.0,
                "topic_l1": topic_name,
                "word_l1": word_name,
                "description": f"{topic}[{topic_name}域] ↔ {word}[{word_name}域]",
            })
            if len(results) >= top_k * 10:
                break

    return results[:top_k]


def find_contrast_with_graph(topic, G, top_k=5):
    """跨域对比词，过滤为在知识图谱中出现过的词（精准度更高）"""
    _load_cilin()
    codes = _get_codes(topic)

    # 话题不在词林时，用图谱邻居的词林编码推断话题所在的语义域
    if not codes and topic in G:
        neighbor_codes = []
        for nbr in list(G.successors(topic)) + list(G.predecessors(topic)):
            neighbor_codes.extend(_get_codes(nbr))
        if not neighbor_codes:
            return []
        # 取邻居中出现最多的大类作为话题域
        from collections import Counter
        l1_counts = Counter(_prefix(c, 1) for c in neighbor_codes)
        dominant_l1 = l1_counts.most_common(1)[0][0]
        codes = [dominant_l1]

    if not codes:
        return []

    topic_l1s  = {_prefix(c, 1) for c in codes}
    graph_nodes = set(G.nodes())
    L1_NAMES = {
        "A": "人", "B": "物", "C": "时地", "D": "社会",
        "E": "自然", "F": "事务", "G": "文化", "H": "活动",
        "I": "性状", "J": "程度", "K": "关联", "L": "助词",
    }

    results = []
    seen = set()
    for l1 in L1_NAMES:
        if l1 in topic_l1s:
            continue
        for word in _get_words_by_prefix(l1):
            if word == topic or word in seen or word not in graph_nodes:
                continue
            seen.add(word)
            topic_name = "/".join(L1_NAMES[l] for l in topic_l1s if l in L1_NAMES)
            word_name  = L1_NAMES.get(l1, l1)
            score = 3.0 + G.nodes[word].get("degree_high", 0) * 0.5
            results.append({
                "slot": word,
                "path": [topic, word],
                "relation": "词林+图谱跨域",
                "score": float(score),
                "topic_l1": topic_name,
                "word_l1": word_name,
                "description": f"{topic}[{topic_name}域] ↔ {word}[{word_name}域]",
            })

    results.sort(key=lambda x: -x["score"])
    return results[:top_k]
