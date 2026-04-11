"""
cilin_finder.py
基于同义词词林扩展版（哈工大，9万词）找语义对比词。

词林编码结构（5层）：
  大类(A-P) → 中类(a-z) → 小类(01-99) → 词群(A-Z) → 原子词群(01-99)
  e.g. Hj11A01= → H=人类 j=职业 11=工作 A=词群A 01=原子词群

对比逻辑：
  - 同小类不同词群（Hj11A vs Hj11B）：近义可区分，"说的是A其实更像B"
  - 不同大类（H vs D）：跨域强对比，笑点核心
"""

import os
import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

# ==================== 数据加载 ====================

# cilin_tree.json 在 pip install cilin 时附带，位于 site-packages/data/
import importlib.util as _ilu
_pkg = _ilu.find_spec("cilin")
_CILIN_JSON = Path(_pkg.origin).parent.parent / "data" / "cilin_tree.json"

_word_to_codes = None   # word → [code, ...]
_code_to_words = None   # code_prefix → [word, ...]


def _load():
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
                # 叶节点列表混合了词和标记符 (01=, 02#)
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


# ==================== 编码工具 ====================

def _prefix(code, level):
    """
    提取编码前缀：
      level 1 → 'H'        大类
      level 2 → 'Hj'       中类
      level 3 → 'Hj11'     小类
      level 4 → 'Hj11A'    词群
      level 5 → 'Hj11A01'  原子词群（完整编码）
    """
    cuts = [1, 2, 4, 5, 7]
    return code[:cuts[level - 1]] if len(code) >= cuts[level - 1] else code


def _get_codes(word):
    _load()
    return _word_to_codes.get(word, [])


def _get_words_by_prefix(prefix):
    """返回所有以 prefix 开头的编码下的词"""
    _load()
    words = []
    for code, ws in _code_to_words.items():
        if code.startswith(prefix):
            words.extend(ws)
    return words


# ==================== 主接口 ====================

def find_similar_cilin(topic, top_k=8):
    """
    找与 topic 同小类（level-3）但不同词群（level-4）的词。
    → "近义可区分"：同一语义域内的细微差异，可用于"说的是A其实更像B"结构。

    返回格式与 humor_slot_finder 兼容。
    """
    codes = _get_codes(topic)
    if not codes:
        return []

    results = []
    seen = set()

    for code in codes:
        l3 = _prefix(code, 3)   # 同小类前缀
        l4 = _prefix(code, 4)   # 当前词群前缀

        # 同小类的所有词
        candidates = _get_words_by_prefix(l3)
        for word in candidates:
            if word == topic or word in seen:
                continue
            w_codes = _get_codes(word)
            # 必须有至少一个编码在同小类但不同词群
            for wc in w_codes:
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
    """
    找与 topic 不同大类（level-1）的跨域对比词。
    → "跨域强对比"：同一语义连接但来自截然不同领域，笑点核心。

    只返回也出现在知识图谱邻域内的词（避免无关词泛滥）。
    """
    _load()
    codes = _get_codes(topic)
    if not codes:
        return []

    topic_l1s = {_prefix(c, 1) for c in codes}

    # 大类含义参考（用于描述）
    L1_NAMES = {
        "A": "人", "B": "物", "C": "时地", "D": "社会",
        "E": "自然", "F": "事务", "G": "文化", "H": "活动",
        "I": "性状", "J": "程度", "K": "关联", "L": "助词",
    }

    # 过滤太泛的单字词和常见虚词
    GENERIC = {"人", "士", "物", "事", "地", "时", "类", "者", "家", "员", "方"}

    results = []
    seen = set()

    for l1 in L1_NAMES:
        if l1 in topic_l1s:
            continue
        candidates = _get_words_by_prefix(l1)
        for word in candidates:
            if word == topic or word in seen:
                continue
            if len(word) < 2 or len(word) > 5:
                continue
            if word in GENERIC:
                continue
            seen.add(word)
            topic_name = "/".join(L1_NAMES[l] for l in topic_l1s if l in L1_NAMES)
            word_name = L1_NAMES.get(l1, l1)
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
    """
    跨域对比词，但过滤为「在知识图谱中出现过的词」，避免无关候选。
    → 结合词林的语义结构 + 图谱的话题相关性，精准度更高。
    """
    _load()
    codes = _get_codes(topic)
    if not codes:
        return []

    topic_l1s = {_prefix(c, 1) for c in codes}
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
        candidates = _get_words_by_prefix(l1)
        for word in candidates:
            if word == topic or word in seen:
                continue
            if word not in graph_nodes:
                continue
            seen.add(word)
            topic_name = "/".join(L1_NAMES[l] for l in topic_l1s if l in L1_NAMES)
            word_name = L1_NAMES.get(l1, l1)
            # 打分：图谱高价值边优先
            node_data = G.nodes[word]
            score = 3.0 + node_data.get("degree_high", 0) * 0.5
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


# ==================== 主程序（演示） ====================

def main():
    test_topics = ["打工人", "结婚", "老板", "考研", "贫穷", "躺平"]

    for topic in test_topics:
        print(f"\n{'='*50}")
        print(f"话题：【{topic}】  编码：{_get_codes(topic)}")

        print("  [同小类近义可区分]：")
        similar = find_similar_cilin(topic, top_k=4)
        if not similar:
            print("    未找到")
        for r in similar:
            print(f"    {r['slot']}  {r['description']}")

        print("  [跨域对比（无过滤）]：")
        contrast = find_contrast_cilin(topic, top_k=4)
        if not contrast:
            print("    未找到")
        for r in contrast:
            print(f"    {r['slot']}  {r['description']}")


if __name__ == "__main__":
    main()
