"""
aggregate_rules.py — 从所有已分析帖子中聚合规则 + 场景策略库

纯本地处理，不调用任何 LLM API。
直接读 data/cache/analyzed_all.json，统计聚合，输出结构化数据。

输出（本地）：
  data/cache/rulebook.json        — 规则手册
  data/cache/strategy_library.json — 场景策略库
  data/cache/raw_stats.json       — 原始统计

同时上传到 GCS（如果可用）。

运行：
  python pipeline/analyze/aggregate_rules.py
"""

import json
import os
import logging
from collections import Counter, defaultdict

_PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
CACHE_DIR = os.path.join(_PROJECT_ROOT, "data", "cache")
ANALYZED_CACHE = os.path.join(CACHE_DIR, "analyzed_all.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")


# ==================== 1. 加载 ====================

def load_posts():
    """从本地缓存加载已分析帖子"""
    if os.path.exists(ANALYZED_CACHE):
        with open(ANALYZED_CACHE, "r", encoding="utf-8") as f:
            posts = json.load(f)
        logging.info(f"从本地缓存加载 {len(posts)} 篇")
        return posts

    # fallback: 从 GCS
    logging.info("本地缓存不存在，从 GCS 加载...")
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))
        from google.cloud import storage
        client = storage.Client(project="gen-lang-client-0577448366")
        bucket = client.bucket("xhs-humor-data")
        blobs = list(bucket.list_blobs(prefix="data/analyzed_posts/"))
        posts = []
        for b in blobs:
            if b.name.endswith(".json"):
                posts.append(json.loads(b.download_as_text(encoding="utf-8")))
        # 缓存到本地
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(ANALYZED_CACHE, "w", encoding="utf-8") as f:
            json.dump(posts, f, ensure_ascii=False)
        logging.info(f"从 GCS 加载并缓存 {len(posts)} 篇")
        return posts
    except Exception as e:
        logging.error(f"加载失败: {e}")
        return []


# ==================== 2. 提取原始数据 ====================

def extract_raw_data(posts):
    raw = {
        "micro_rules": [],
        "word_choices": [],
        "running_elements": [],
        "things_not_said": [],
        "mechanisms": Counter(),
        "conflict_pairs": [],
        "pain_points": [],
        "resonance_types": Counter(),
        "techniques": Counter(),
        "original_vs_plain": [],
        "deliveries": Counter(),
        "unsaids": [],
        "pacing_patterns": [],
        "writing_patterns": Counter(),
        "narrative_arcs": [],
        "signature_moves": [],
    }

    for post in posts:
        title = post.get("title", "unknown")

        for p in post.get("punchlines", []):
            s = p.get("structure", {})
            e = p.get("emotion", {})
            l = p.get("language", {})
            r = p.get("rhythm", {})
            x = p.get("expression", {})
            c = p.get("craft", {})

            if s.get("mechanism"):
                raw["mechanisms"][s["mechanism"]] += 1
            cp = s.get("conflict_pair", [])
            if cp and len(cp) == 2:
                raw["conflict_pairs"].append({"pair": cp, "source": title})
            if e.get("pain_point"):
                raw["pain_points"].append({"text": e["pain_point"], "source": title})
            if e.get("resonance_type"):
                raw["resonance_types"][e["resonance_type"]] += 1
            if l.get("key_technique"):
                raw["techniques"][l["key_technique"]] += 1
            ovp = l.get("original_vs_plain", [])
            if ovp and len(ovp) == 2 and l.get("why_better"):
                raw["original_vs_plain"].append({
                    "original": ovp[0], "plain": ovp[1],
                    "why": l["why_better"], "source": title
                })
            if r.get("pacing"):
                raw["pacing_patterns"].append(r["pacing"])
            if x.get("delivery"):
                raw["deliveries"][x["delivery"]] += 1
            if x.get("unsaid") and x["unsaid"] != "无":
                raw["unsaids"].append({"text": x["unsaid"], "source": title})

            for wc in c.get("word_choices", []):
                if isinstance(wc, dict) and wc.get("chose"):
                    raw["word_choices"].append({
                        "chose": wc["chose"], "not": wc.get("not", ""),
                        "why": wc.get("why", ""), "source": title
                    })
            for item in c.get("running_elements", []):
                if item and item != "无":
                    raw["running_elements"].append({"text": item, "source": title})
            for item in c.get("things_not_said", []):
                if item and item != "无":
                    raw["things_not_said"].append({"text": item, "source": title})
            for item in c.get("micro_rules", []):
                if item:
                    raw["micro_rules"].append({"rule": item, "source": title})

        ws = post.get("writing_style", {})
        if ws.get("pattern"):
            raw["writing_patterns"][ws["pattern"]] += 1
        for sm in ws.get("signature_moves", []):
            if sm:
                raw["signature_moves"].append({"move": sm, "source": title})

        ns = post.get("narrative_structure", {})
        if ns:
            raw["narrative_arcs"].append({
                "arc": ns.get("arc", ""),
                "hook": ns.get("hook_strategy", ""),
                "ending": ns.get("ending_strategy", ""),
                "escalation": ns.get("escalation_pattern", ""),
                "source": title
            })

    return raw


# ==================== 3. 生成规则手册（纯统计，不调LLM）====================

def build_rulebook(raw):
    """从统计数据中直接构建规则手册"""

    # 按频次去重 micro_rules
    rule_counter = Counter()
    rule_examples = {}
    for item in raw["micro_rules"]:
        rule = item["rule"]
        rule_counter[rule] += 1
        if rule not in rule_examples:
            rule_examples[rule] = item["source"]

    # 按类别分组（基于关键词匹配）
    categories = {
        "数字与精确度": ["数字", "精确", "金额", "价格", "可信"],
        "比喻与拟人": ["比喻", "拟人", "拟人化", "角色", "人格", "性格"],
        "句式结构": ["句式", "排比", "三拍", "重复", "断句", "对仗", "rule of three"],
        "语气与反转": ["语气", "反转", "克制", "伪", "庄重", "崇高", "降格", "正面反话"],
        "自嘲与免疫": ["自嘲", "自降", "自黑", "暴露", "承认", "免疫", "戳穿"],
        "Running gag": ["running", "重复", "贯穿", "反复", "升级"],
        "收尾与升维": ["结尾", "收尾", "升维", "升华", "跳出", "最后"],
        "潜台词与留白": ["潜台词", "没说", "留白", "不说", "暗示", "感受"],
        "选词与措辞": ["选词", "措辞", "用词", "替代", "换成"],
        "叙事编排": ["叙事", "编排", "节奏", "位置", "铺垫", "hook", "开头"],
    }

    categorized = {name: [] for name in categories}
    categorized["其他"] = []

    for rule, count in rule_counter.most_common():
        placed = False
        rule_lower = rule.lower()
        for cat_name, keywords in categories.items():
            if any(kw in rule_lower for kw in keywords):
                categorized[cat_name].append({
                    "name": rule[:30],
                    "description": rule,
                    "example_source": rule_examples.get(rule, ""),
                    "frequency": count
                })
                placed = True
                break
        if not placed:
            categorized["其他"].append({
                "name": rule[:30],
                "description": rule,
                "example_source": rule_examples.get(rule, ""),
                "frequency": count
            })

    # 构建 JSON
    rulebook = {
        "meta_rules": [
            {
                "name": "克制大于表达",
                "description": "语气永远比内容冷静一个级别。内容很惨，语气要平静。不解释笑点，写完就走。",
                "examples": ["站牌显示还有8分钟，这是北欧第一次对你说谎"]
            },
            {
                "name": "精确大于模糊",
                "description": "所有数字精确到个位，所有地点精确到具体名称。精确产生画面感，模糊产生废话。",
                "examples": ["23公斤行李箱", "¥4500/四晚", "一公里外的711"]
            },
            {
                "name": "潜台词大于明说",
                "description": "全篇不说出核心情绪词。用场景让读者自己感受。读者自己悟到的比你说出来的强10倍。",
                "examples": ["全篇没提孤独二字，但每张图都在说孤独"]
            },
            {
                "name": "具体大于抽象",
                "description": "比喻要具体（在机场等一艘船），拟人要有性格（行李箱陷入沉默），场景要有细节。",
                "examples": ["在机场等一艘船", "热水器每次工作15分钟后就要休息"]
            },
            {
                "name": "自嘲大于抱怨",
                "description": "永远对自己开刀，不对别人开刀。把苦说甜，把惨说成有趣。先自黑，读者就不反感。",
                "examples": ["可以比较心安理得地当个乞丐", "气得我把它买了下来"]
            },
        ],
        "categories": [],
        "stats": {
            "total_rules": len(rule_counter),
            "total_word_choices": len(raw["word_choices"]),
            "total_conflict_pairs": len(raw["conflict_pairs"]),
            "mechanisms": dict(raw["mechanisms"].most_common()),
            "resonance_types": dict(raw["resonance_types"].most_common()),
            "techniques": dict(raw["techniques"].most_common(20)),
            "deliveries": dict(raw["deliveries"].most_common()),
            "writing_patterns": dict(raw["writing_patterns"].most_common()),
        }
    }

    for cat_name, rules in categorized.items():
        if not rules:
            continue
        rules.sort(key=lambda x: -x["frequency"])
        rulebook["categories"].append({
            "name": cat_name,
            "rules": rules
        })

    # 加入 word_choices 作为独立 section
    rulebook["word_choices_examples"] = raw["word_choices"][:100]
    rulebook["original_vs_plain_examples"] = raw["original_vs_plain"][:50]
    rulebook["unsaid_examples"] = raw["unsaids"][:50]

    return rulebook


# ==================== 4. 生成场景策略库（纯统计）====================

def build_strategy_library(posts):
    """按 scene_tags 聚类，纯统计生成策略库"""

    clusters = defaultdict(list)
    for post in posts:
        title = post.get("title", "unknown")
        topic_tags = post.get("topic_tags", [])

        for p in post.get("punchlines", []):
            ev = p.get("event", {})
            scene_tags = ev.get("scene_tags", [])
            if not scene_tags:
                scene_tags = topic_tags[:2] if topic_tags else ["未分类"]

            entry = {
                "post_title": title,
                "setup_text": p.get("structure", {}).get("setup_text", ""),
                "punchline_text": p.get("structure", {}).get("punchline_text", ""),
                "mechanism": p.get("structure", {}).get("mechanism", ""),
                "pain_point": p.get("emotion", {}).get("pain_point", ""),
                "resonance_type": p.get("emotion", {}).get("resonance_type", ""),
                "delivery": p.get("expression", {}).get("delivery", ""),
                "unsaid": p.get("expression", {}).get("unsaid", ""),
                "closeness": p.get("expression", {}).get("closeness", ""),
                "key_technique": p.get("language", {}).get("key_technique", ""),
            }
            for tag in scene_tags:
                tag = tag.strip()
                if tag:
                    clusters[tag].append(entry)

    # 每个场景生成策略卡
    strategies = []
    for tag, entries in sorted(clusters.items(), key=lambda x: -len(x[1])):
        if len(entries) < 2:
            continue

        mechs = Counter(e["mechanism"] for e in entries if e["mechanism"])
        techs = Counter(e["key_technique"] for e in entries if e["key_technique"])
        delivs = Counter(e["delivery"] for e in entries if e["delivery"])
        pain_points = list(set(e["pain_point"] for e in entries if e["pain_point"]))
        unsaids = list(set(e["unsaid"] for e in entries if e["unsaid"] and e["unsaid"] != "无"))

        # 选最佳范文
        examples = []
        for e in entries:
            if e["setup_text"] and e["punchline_text"]:
                examples.append({
                    "setup": e["setup_text"],
                    "punchline": e["punchline_text"],
                    "source": e["post_title"]
                })
        examples = examples[:3]

        strategies.append({
            "scene": tag,
            "frequency": len(entries),
            "emotion_strategy": {
                "typical_pain_points": pain_points[:5],
                "resonance_types": dict(Counter(e["resonance_type"] for e in entries if e["resonance_type"]).most_common(3)),
            },
            "expression_strategy": {
                "delivery_approaches": dict(delivs.most_common(3)),
                "closeness": Counter(e["closeness"] for e in entries if e["closeness"]).most_common(1)[0][0] if any(e["closeness"] for e in entries) else "平视",
                "unsaid_patterns": unsaids[:3],
            },
            "language_strategy": {
                "preferred_techniques": dict(techs.most_common(5)),
            },
            "structure_strategy": {
                "preferred_mechanisms": dict(mechs.most_common(5)),
            },
            "best_examples": examples,
        })

    return {"strategies": strategies}


# ==================== 5. 保存 ====================

def save_local(data, filename):
    path = os.path.join(CACHE_DIR, filename)
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    size = os.path.getsize(path) / 1024
    logging.info(f"已保存: {path} ({size:.0f}KB)")
    return path


def try_upload_gcs(local_path, gcs_path):
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))
        from google.cloud import storage
        client = storage.Client(project="gen-lang-client-0577448366")
        bucket = client.bucket("xhs-humor-data")
        bucket.blob(gcs_path).upload_from_filename(local_path)
        logging.info(f"已上传 GCS: {gcs_path}")
    except Exception as e:
        logging.warning(f"GCS 上传跳过: {e}")


# ==================== 主流程 ====================

def main():
    # 1. 加载
    posts = load_posts()
    if not posts:
        print("没有已分析的帖子。请先运行 analyze_posts.py 或确保 data/cache/analyzed_all.json 存在。")
        return

    # 2. 提取
    raw = extract_raw_data(posts)
    print(f"\n从 {len(posts)} 篇帖子中提取:")
    print(f"  micro_rules: {len(raw['micro_rules'])} 条")
    print(f"  word_choices: {len(raw['word_choices'])} 条")
    print(f"  conflict_pairs: {len(raw['conflict_pairs'])} 对")
    print(f"  unsaids: {len(raw['unsaids'])} 条")
    print(f"  mechanisms: {dict(raw['mechanisms'].most_common())}")
    print(f"  techniques: {dict(raw['techniques'].most_common(10))}")

    # 3. 规则手册
    print("\n生成规则手册...")
    rulebook = build_rulebook(raw)
    path = save_local(rulebook, "rulebook.json")
    try_upload_gcs(path, "data/rulebook.json")

    num_rules = sum(len(cat["rules"]) for cat in rulebook["categories"])
    print(f"  {len(rulebook['meta_rules'])} 条元规则, {len(rulebook['categories'])} 个类别, {num_rules} 条规则")

    # 4. 策略库
    print("\n生成场景策略库...")
    strategy_lib = build_strategy_library(posts)
    path = save_local(strategy_lib, "strategy_library.json")
    try_upload_gcs(path, "data/strategy_library.json")

    strategies = strategy_lib["strategies"]
    print(f"  {len(strategies)} 个场景")
    for s in strategies[:10]:
        print(f"    {s['scene']}: {s['frequency']}次")

    # 5. 原始统计
    save_local(raw["micro_rules"], "raw_stats_rules.json")
    save_local(raw["word_choices"], "raw_stats_word_choices.json")

    print(f"\n完成！文件在 {CACHE_DIR}/")
    print(f"  rulebook.json          — MCP get_rules 会读这个")
    print(f"  strategy_library.json  — MCP get_strategy 会读这个")


if __name__ == "__main__":
    main()
