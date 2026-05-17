"""
data.py — 数据加载与格式化

所有数据读取逻辑集中在这里，MCP server 只做薄薄的工具定义层。
"""

import json
import os
import re

_PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA_DIR = os.path.join(_PROJECT_ROOT, "data")

_cache = {}


def load_json(filename):
    """读本地 data/ 目录下的 JSON 文件"""
    if filename in _cache:
        return _cache[filename]
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    _cache[filename] = data
    return data


def load_analyzed_posts():
    if "refs" in _cache:
        return _cache["refs"]
    posts = load_json("posts_analyzed.json")
    if posts:
        _cache["refs"] = posts
    return posts or []


def load_raw_posts():
    if "raw" in _cache:
        return _cache["raw"]
    raw = load_json("posts_raw.json")
    if raw:
        _cache["raw"] = raw
    return raw or {}


def format_post(post, raw_posts):
    """格式化一篇范文（原文 + 7层分析）"""
    title = post.get("title", "?")
    lines = [f"# {title}\n"]

    # 原文
    for name, texts in raw_posts.items():
        if title in name or name in title:
            for i, t in enumerate(texts, 1):
                lines.append(f"[图{i}] {t}")
            break

    # 笑点分析
    punchlines = post.get("punchlines", [])
    if punchlines:
        lines.append(f"\n## 笑点分析（{len(punchlines)}个）\n")
        for p in punchlines:
            s = p.get("structure", {})
            e = p.get("emotion", {})
            x = p.get("expression", {})
            c = p.get("craft", {})
            l = p.get("language", {})
            r = p.get("rhythm", {})

            lines.append(f"### [{s.get('mechanism','?')}] {s.get('punchline_text','')}")
            lines.append(f"- setup: {s.get('setup_text','')}")
            lines.append(f"- 冲突对: {s.get('conflict_pair',[])}")

            if e.get("pain_point"):
                lines.append(f"- 痛点: {e['pain_point']}")
            if l.get("key_technique"):
                lines.append(f"- 语言技巧: {l['key_technique']}")
                ovp = l.get("original_vs_plain", [])
                if ovp and len(ovp) == 2:
                    lines.append(f"  原文: {ovp[0]} vs 直白: {ovp[1]}")
                if l.get("why_better"):
                    lines.append(f"  为什么更好: {l['why_better']}")
            if x.get("delivery"):
                lines.append(f"- 表达: {x['delivery']}")
            if x.get("unsaid") and x["unsaid"] != "无":
                lines.append(f"- 潜台词: {x['unsaid']}")
            if r.get("pacing"):
                lines.append(f"- 节奏: {r['pacing']}")

            for wc in c.get("word_choices", []):
                if isinstance(wc, dict) and wc.get("chose"):
                    lines.append(f"- 选词: '{wc['chose']}' 而非 '{wc.get('not','')}' → {wc.get('why','')}")
            for mr in c.get("micro_rules", []):
                if mr:
                    lines.append(f"- 规则: {mr}")
            if c.get("things_not_said"):
                for tns in c["things_not_said"]:
                    if tns and tns != "无":
                        lines.append(f"- 没说的: {tns}")
            lines.append("")

    # 写作风格
    ws = post.get("writing_style", {})
    if ws:
        lines.append(f"## 写作风格")
        lines.append(f"- 模式: {ws.get('pattern','')}")
        lines.append(f"- 节奏: {ws.get('rhythm','')}")
        lines.append(f"- 语气: {ws.get('tone','')}")
        lines.append(f"- 招牌动作: {ws.get('signature_moves',[])}")

    # 叙事结构
    ns = post.get("narrative_structure", {})
    if ns:
        lines.append(f"\n## 叙事结构")
        lines.append(f"- 弧度: {ns.get('arc','')}")
        lines.append(f"- Hook: {ns.get('hook_strategy','')}")
        lines.append(f"- 结尾: {ns.get('ending_strategy','')}")
        lines.append(f"- 笑点密度: {ns.get('punchline_density','')}")
        lines.append(f"- 递进: {ns.get('escalation_pattern','')}")

    return "\n".join(lines)


def search_references(topic, top_k=3):
    """按话题搜索最相关范文"""
    analyzed = load_analyzed_posts()
    raw_posts = load_raw_posts()

    if not analyzed:
        return None

    topic_chars = set(topic)
    scored = []
    for p in analyzed:
        title = p.get("title", "")
        tags = p.get("topic_tags", [])
        all_text = title + " " + " ".join(tags)

        score = 0
        for char in topic_chars:
            if char in all_text:
                score += 1
        for tag in tags:
            if tag in topic or topic in tag:
                score += 3
        scored.append((score, p))

    scored.sort(key=lambda x: -x[0])

    if scored[0][0] == 0:
        scored = [(len(p.get("punchlines", [])), p) for p in analyzed]
        scored.sort(key=lambda x: -x[0])

    results = [format_post(p, raw_posts) for _, p in scored[:top_k]]
    header = f"找到 {len(analyzed)} 篇范文，返回最相关的 {min(top_k, len(scored))} 篇：\n"
    return header + "\n\n---\n\n".join(results)


def get_rulebook():
    """返回格式化的规则手册"""
    rb = load_json("rulebook.json")
    if not rb:
        return None

    lines = []
    for mr in rb.get("meta_rules", []):
        lines.append(f"## 元规则：{mr.get('name','')}")
        lines.append(f"{mr.get('description','')}")
        for ex in mr.get("examples", []):
            lines.append(f"  例: {ex}")
        lines.append("")

    for cat in rb.get("categories", []):
        lines.append(f"\n## {cat.get('name','')}")
        for rule in cat.get("rules", []):
            freq = f" ({rule['frequency']}次)" if rule.get("frequency") else ""
            lines.append(f"- **{rule.get('name','')}**{freq}: {rule.get('description','')}")
            if rule.get("example"):
                lines.append(f"  例: {rule['example']}")
            if rule.get("example_source"):
                lines.append(f"  出处: {rule['example_source']}")

    return "\n".join(lines)


def get_strategies(topic):
    """返回匹配的写作策略"""
    lib = load_json("strategies.json")
    if not lib:
        return None

    strategies = lib.get("strategies", [])

    matched = []
    for s in strategies:
        scene = s.get("scene", "")
        if topic in scene or scene in topic or any(c in scene for c in topic):
            matched.append(s)

    if not matched:
        scenes = [f"- {s['scene']} ({s.get('frequency','?')}次)" for s in strategies]
        return f"未精确匹配到'{topic}'。可用场景：\n" + "\n".join(scenes)

    output = []
    for s in matched:
        output.append(f"# 场景: {s['scene']} ({s.get('frequency','?')}次)\n")

        es = s.get("emotion_strategy", {})
        output.append(f"## 情绪策略")
        output.append(f"- 痛点: {es.get('typical_pain_points', [])}")
        output.append(f"- 共鸣方式: {es.get('resonance_approach', '')}")
        output.append(f"- 目标受众: {es.get('who_relates', '')}")

        xs = s.get("expression_strategy", {})
        output.append(f"\n## 表达策略")
        output.append(f"- 包装: {xs.get('delivery_approach', '')}")
        output.append(f"- 距离感: {xs.get('closeness', '')}")
        output.append(f"- 潜台词模式: {xs.get('unsaid_pattern', '')}")

        ls = s.get("language_strategy", {})
        output.append(f"\n## 语言策略")
        output.append(f"- 技巧: {ls.get('preferred_techniques', [])}")
        output.append(f"- 选词: {ls.get('word_choice_patterns', [])}")
        output.append(f"- 避免: {ls.get('avoid', [])}")

        ss = s.get("structure_strategy", {})
        output.append(f"\n## 结构策略")
        output.append(f"- 机制: {ss.get('preferred_mechanisms', [])}")
        output.append(f"- 冲突对: {ss.get('typical_conflict_pairs', [])}")

        for ex in s.get("best_examples", []):
            output.append(f"\n## 范文: [{ex.get('source','?')}]")
            output.append(f"setup: {ex.get('setup', '')}")
            output.append(f"punchline: {ex.get('punchline', '')}")

    return "\n".join(output)


def get_persona_data():
    """返回人设 JSON"""
    return load_json("persona.json")


def list_posts():
    """返回所有范文标题列表"""
    analyzed = load_analyzed_posts()
    if not analyzed:
        return None

    lines = [f"共 {len(analyzed)} 篇范文：\n"]
    for p in sorted(analyzed, key=lambda x: x.get("title", "")):
        title = p.get("title", "?")
        tags = p.get("topic_tags", [])
        n_punchlines = len(p.get("punchlines", []))
        lines.append(f"- {title} | 标签: {tags} | 笑点: {n_punchlines}个")

    return "\n".join(lines)
