"""
generate_post.py — 生成"妈的欧洲账本"风格的完整小红书帖子

输入：用户提供一组槽点（真实经历的流水账）
输出：一篇完整的小红书帖子（逐张图的文案）

流程：
  1. 匹配：槽点 → 查策略库 + 查知识图谱 + 查梗库 + 查范文
  2. 骨架：冲突对 + 幽默机制 + 排 setup/punchline 位置
  3. 生成：素材 + 策略 + 图谱冲突 + 梗库灵感 + 规则 + 范文 → 初稿
  4. 检查：Critic 逐条规则检查 + 和范文对比
  5. 修改：不过就打回带具体意见重写（最多3轮）
  6. 输出：终稿

运行：
  python pipeline/generate/generate_post.py
  python pipeline/generate/generate_post.py --interactive
"""

import argparse
import json
import os
import re
import sys
import math
import time
import logging

from dotenv import load_dotenv

_PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, _PROJECT_ROOT)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

from google.cloud import storage
from google import genai

# ==================== 配置 ====================

PROJECT_ID = "gen-lang-client-0577448366"
BUCKET_NAME = "xhs-humor-data"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

gemini_client = genai.Client(api_key=GEMINI_API_KEY)
storage_client = storage.Client(project=PROJECT_ID)
bucket = storage_client.bucket(BUCKET_NAME)


# ==================== 1. 加载资源 ====================

_cache = {}


def load_resource(path):
    """从 GCS 加载 JSON，带内存缓存"""
    if path in _cache:
        return _cache[path]
    blob = bucket.blob(path)
    if not blob.exists():
        logging.warning(f"资源不存在: {path}")
        return None
    data = json.loads(blob.download_as_text(encoding="utf-8"))
    _cache[path] = data
    return data


def load_strategy_library():
    return load_resource("data/strategy_library.json")


def load_rulebook():
    return load_resource("data/rulebook.json")


def load_reference_posts():
    """加载所有已分析帖子作为范文库"""
    if "reference_posts" in _cache:
        return _cache["reference_posts"]

    blobs = list(bucket.list_blobs(prefix="data/analyzed_posts/"))
    posts = []
    for blob in blobs:
        if not blob.name.endswith(".json"):
            continue
        try:
            data = json.loads(blob.download_as_text(encoding="utf-8"))
            posts.append(data)
        except Exception:
            pass
    _cache["reference_posts"] = posts
    logging.info(f"加载了 {len(posts)} 篇范文")
    return posts


def load_raw_posts():
    """加载原始帖子文本（用于给 Generator 看范文原文）"""
    if "raw_posts" in _cache:
        return _cache["raw_posts"]

    blobs = list(bucket.list_blobs(prefix="data/raw_data/妈的欧洲账本/"))
    txt_blobs = [b for b in blobs if b.name.endswith(".txt")]

    posts = {}
    for b in txt_blobs:
        name = b.name.split("/")[-1]
        post_name = re.sub(r"_\d+\.jpg\.txt$", "", name)
        posts.setdefault(post_name, []).append(b)

    for post_name in posts:
        posts[post_name].sort(
            key=lambda b: int(re.search(r"_(\d+)\.jpg", b.name).group(1))
            if re.search(r"_(\d+)\.jpg", b.name) else 0
        )

    # 读取文本
    raw = {}
    for post_name, blob_list in posts.items():
        texts = []
        for b in blob_list:
            t = b.download_as_text(encoding="utf-8").strip()
            texts.append(t if t else "(空)")
        raw[post_name] = texts

    _cache["raw_posts"] = raw
    logging.info(f"加载了 {len(raw)} 篇原始帖子")
    return raw


# ==================== 1.5 知识图谱 + 梗库（联想引擎）====================

_graph = None
_rag_memes = None
_rag_embeddings = None


def load_knowledge_graph():
    """加载知识图谱（用于联想冲突对和相关概念）"""
    global _graph
    if _graph is not None:
        return _graph
    try:
        from src.knowledge.graph import load_graph
        _graph = load_graph()
        logging.info(f"知识图谱已加载: {_graph.number_of_nodes()} 节点, {_graph.number_of_edges()} 边")
        return _graph
    except Exception as e:
        logging.warning(f"知识图谱加载失败: {e}")
        return None


def query_graph(槽点_keywords):
    """从知识图谱中找冲突对和联想素材

    输入一组关键词（从槽点中提取），返回：
    - conflict_pairs: 高幽默价值的冲突关系
    - associations: 联想到的相关概念和事件
    """
    G = load_knowledge_graph()
    if G is None:
        return {"conflict_pairs": [], "associations": []}

    from src.knowledge.graph import find_humor_slots, get_subgraph_triples, find_topic_node

    all_slots = []
    all_triples = []

    for keyword in 槽点_keywords:
        node = find_topic_node(G, keyword)
        if node is None:
            continue

        slots = find_humor_slots(G, keyword, top_k=3)
        for s in slots:
            all_slots.append({
                "keyword": keyword,
                "slot": s["slot"],
                "relation": s["relation"],
                "score": s["score"],
                "path": s.get("path", []),
            })

        if slots:
            triples = get_subgraph_triples(G, node, slots[0]["slot"], max_triples=5)
            all_triples.extend(triples)

    # 去重排序
    all_slots.sort(key=lambda x: -x["score"])
    seen = set()
    unique_slots = []
    for s in all_slots:
        key = s["slot"]
        if key not in seen:
            seen.add(key)
            unique_slots.append(s)

    return {
        "conflict_pairs": unique_slots[:8],
        "associations": all_triples[:12],
    }


def query_rag(槽点_text, top_k=5):
    """从梗库中检索相关的梗/段子作为灵感"""
    global _rag_memes, _rag_embeddings
    try:
        from src.knowledge.rag_retriever import load_memes, load_or_build_embeddings, retrieve
        if _rag_memes is None:
            _rag_memes = load_memes()
            _rag_embeddings = load_or_build_embeddings(_rag_memes)
        results = retrieve(槽点_text, 槽点_text, top_k=top_k,
                          memes=_rag_memes, embeddings=_rag_embeddings)
        return results
    except Exception as e:
        logging.warning(f"梗库检索失败: {e}")
        return []


def extract_keywords(槽点_text):
    """从槽点中提取关键词用于图谱查询"""
    prompt = f"""从以下文本中提取5-8个关键词（名词/场景词），用于知识图谱查询。
只输出关键词，逗号分隔：

{槽点_text}"""
    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash", contents=prompt
    )
    keywords = [k.strip() for k in response.text.strip().split(",") if k.strip()]
    return keywords


# ==================== 2. 匹配：找策略 + 找范文 ====================

def match_strategy(槽点_text, strategy_library):
    """用 Gemini 匹配最相关的场景策略"""
    strategies = strategy_library.get("strategies", [])
    if not strategies:
        return None

    scene_list = "\n".join(
        f"- {s['scene']} ({s.get('frequency', '?')}次): "
        f"痛点={s.get('emotion_strategy', {}).get('typical_pain_points', [])}"
        for s in strategies
    )

    prompt = f"""以下是从195篇"妈的欧洲账本"帖子中提取的场景策略列表：

{scene_list}

用户要写的槽点：
{槽点_text}

请选出最相关的1-3个场景（按相关度排序），只输出场景名，逗号分隔："""

    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash", contents=prompt
    )
    matched_names = [n.strip() for n in response.text.strip().split(",")]

    matched = []
    for s in strategies:
        if s["scene"] in matched_names:
            matched.append(s)
    return matched if matched else strategies[:2]


def find_reference_posts(槽点_text, reference_posts, top_k=3):
    """找最相关的范文帖子"""
    prompt = f"""以下是帖子标题列表：

{chr(10).join(f"- {p.get('title', '未知')}" for p in reference_posts)}

用户要写的内容：
{槽点_text}

请选出最适合作为参考的{top_k}篇帖子（风格/主题最接近的），只输出标题，每行一个："""

    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash", contents=prompt
    )
    matched_titles = [t.strip().strip("-").strip() for t in response.text.strip().split("\n") if t.strip()]

    matched = []
    for p in reference_posts:
        title = p.get("title", "")
        for mt in matched_titles:
            if mt in title or title in mt:
                matched.append(p)
                break
    return matched[:top_k]


def format_reference_post(post, raw_posts):
    """格式化一篇范文供 Generator 参考"""
    title = post.get("title", "未知")
    lines = [f"### 范文：{title}"]

    # 找原始文本
    raw_text = None
    for name, texts in raw_posts.items():
        if title in name or name in title:
            raw_text = "\n---\n".join(texts)
            break

    if raw_text:
        lines.append(f"\n原文（每张图用---分隔）：\n{raw_text[:3000]}")

    # 加分析亮点
    punchlines = post.get("punchlines", [])
    if punchlines:
        lines.append("\n笑点分析：")
        for p in punchlines[:4]:
            s = p.get("structure", {})
            c = p.get("craft", {})
            x = p.get("expression", {})
            lines.append(f"- [{s.get('mechanism', '?')}] {s.get('punchline_text', '')[:60]}")
            if x.get("unsaid") and x["unsaid"] != "无":
                lines.append(f"  潜台词: {x['unsaid'][:60]}")
            for wc in c.get("word_choices", [])[:2]:
                if isinstance(wc, dict):
                    lines.append(f"  选词: '{wc.get('chose','')}' 而非 '{wc.get('not','')}' — {wc.get('why','')[:50]}")

    ns = post.get("narrative_structure", {})
    if ns:
        lines.append(f"\n叙事: arc={ns.get('arc','')} | hook={ns.get('hook_strategy','')} | ending={ns.get('ending_strategy','')}")

    return "\n".join(lines)


# ==================== 3. Generator ====================

GENERATE_PROMPT = """你是"妈的欧洲账本"的文案写手。你要根据用户提供的真实槽点，写一篇完整的小红书帖子。

## 用户的槽点（真实经历，这是你的全部素材）
{槽点}

## 知识图谱联想（从图谱中找到的冲突对和相关概念，用于启发创作）
{图谱联想}

## 梗库灵感（从梗库中检索到的相关段子，可以借鉴结构但不要照抄）
{梗库灵感}

## 场景策略（从195篇分析中提取，告诉你这类场景怎么写）
{策略}

## 必须遵守的规则
{规则}

## 参考范文（模仿这个水平和风格）
{范文}

## 输出要求
写一篇{num_slides}张图的小红书帖子。

格式：
===图1===
（文案，2-4行，短句断行）

===图2===
...

要求：
- 每张图的文案独立成立，但整篇有叙事弧度
- 第1张图必须是hook——一句话让人想翻下去
- 每3张图至少一个笑点
- 笑点必须用具体的幽默机制（预期违背/重新定义/精确荒诞/克制反讽/降格/自嘲/递进）
- 所有数字必须精确到个位（¥1200不能写"一千多"，6楼不能写"很高"）
- 禁止直接表达情绪（不能写"好气啊""崩溃了""太惨了"）
- 用拟人化、精确数字、克制语气代替情绪词
- 必须有潜台词——全篇不说出来但读者能感受到的一层意思
- 结尾要跳出具体事件，升维到更大的主题
- 语气：克制、冷静、伪客观。像在冷静记录一个荒诞事实
- 和读者平视，不俯视不仰视

直接输出帖子，不要解释。"""


RULES_FOR_GENERATOR = """核心规则（违反任何一条都会被打回）：

1. 数字精确：所有金额精确到元，所有数量精确到个位。"¥1200/月"不是"一千多"。
2. 禁止情绪词：不能出现"好气""崩溃""太惨""无语""绝了"等直接情绪表达。
3. 拟人化：至少有一个无生命物体被赋予人格/态度/关系。
4. 克制：语气永远比内容冷静一个级别。内容很惨，语气要平静。
5. 潜台词：全篇必须有一层没说出来的意思。如果能一句话概括全篇主题，说明写得太满了。
6. 精确比喻：比喻必须具体（"在机场等一艘船"），不能笼统（"像做梦一样"）。
7. 反差结构：每个笑点必须有setup（建立预期）和punchline（打破预期），不能只有punchline。
8. 升维结尾：最后1-2张图必须跳出具体事件，说一个更大的道理，但不能说教。
9. 不解释笑点：写完就走，不要加"哈哈""笑死"等自我评价。
10. 每3张图一个笑点：不能连续3张图都是平铺直叙。"""


def generate_draft(槽点, strategies, rulebook, reference_texts, graph_data=None, rag_memes=None, num_slides=10):
    """生成初稿"""

    # 格式化图谱联想
    graph_text = ""
    if graph_data:
        cps = graph_data.get("conflict_pairs", [])
        assocs = graph_data.get("associations", [])
        if cps:
            graph_text += "冲突对（高幽默价值的概念关系，可用来构建笑点）：\n"
            for cp in cps[:6]:
                graph_text += f"  - {cp['keyword']} → [{cp['relation']}] → {cp['slot']} (分数={cp['score']:.1f})\n"
        if assocs:
            graph_text += "\n相关三元组（可用来联想新角度）：\n"
            for t in assocs[:8]:
                marker = "★" if t.get("high_value") else "-"
                graph_text += f"  {marker} ({t['subject']}, {t['relation']}, {t['object']})\n"

    # 格式化梗库灵感
    memes_text = ""
    if rag_memes:
        memes_text = "（借鉴结构和角度，不要照抄）\n"
        for i, m in enumerate(rag_memes[:5], 1):
            memes_text += f"{i}. {m[:200]}\n\n"

    # 格式化策略
    strategy_text = ""
    if strategies:
        for s in strategies[:2]:
            strategy_text += f"\n场景: {s['scene']}\n"
            es = s.get("emotion_strategy", {})
            strategy_text += f"  情绪策略: 痛点={es.get('typical_pain_points', [])}, 共鸣={es.get('resonance_approach', '')}\n"
            xs = s.get("expression_strategy", {})
            strategy_text += f"  表达策略: {xs.get('delivery_approach', '')}，潜台词={xs.get('unsaid_pattern', '')}\n"
            ls = s.get("language_strategy", {})
            strategy_text += f"  语言策略: 技巧={ls.get('preferred_techniques', [])}，避免={ls.get('avoid', [])}\n"
            ss = s.get("structure_strategy", {})
            strategy_text += f"  结构策略: 机制={ss.get('preferred_mechanisms', [])}，冲突对={ss.get('typical_conflict_pairs', [])}\n"
            for ex in s.get("best_examples", [])[:2]:
                strategy_text += f"  范例: {ex.get('setup', '')} → {ex.get('punchline', '')}\n"

    prompt = GENERATE_PROMPT.format(
        槽点=槽点,
        图谱联想=graph_text if graph_text else "（图谱未就绪）",
        梗库灵感=memes_text if memes_text else "（梗库未就绪）",
        策略=strategy_text if strategy_text else "（策略库未就绪，请根据范文风格写作）",
        规则=RULES_FOR_GENERATOR,
        范文="\n\n".join(reference_texts[:2]) if reference_texts else "（范文库未就绪）",
        num_slides=num_slides,
    )

    response = gemini_client.models.generate_content(
        model="gemini-2.5-pro", contents=prompt
    )
    return response.text.strip()


# ==================== 4. Critic ====================

CRITIC_PROMPT = """你是"妈的欧洲账本"的主编。你的工作是逐条检查以下帖子是否达到发布标准。

## 帖子初稿
{draft}

## 检查清单（逐条过，不过的标 ❌ 并写出具体修改意见）

### A. 硬性规则（任何一条不过就打回）
1. 数字精确：有没有模糊数字（"很多""好几个""一千多"）？→ 必须精确到个位
2. 禁止情绪词：有没有"好气""崩溃""太惨""无语""绝了""笑死"？→ 删掉或换成克制表达
3. 有没有拟人化：至少一个物体被赋予人格？
4. 克制语气：有没有语气比内容还夸张的地方？→ 降一级
5. 反差结构：每个笑点有setup+punchline？还是只有punchline？
6. 不解释笑点：有没有"哈哈""笑死""真的很好笑"等自我评价？

### B. 质量规则（不影响发布但影响质量）
7. 潜台词：全篇有没有一层没说出来的意思？如果能一句话概括全篇，说明写得太满
8. 升维结尾：最后1-2张图有没有跳出具体事件？
9. 笑点密度：有没有连续3张图以上没有笑点？
10. 选词质量：有没有可以换成更精确/更有画面感的词？举例说明
11. 节奏：有没有铺垫太长或反转太慢的地方？

### C. 和范文对比
{范文对比}
12. 整体水平和范文比，差距在哪？具体到哪句话可以更好？

## 输出格式（JSON）
{{
    "pass": true/false,
    "score": 1-10,
    "hard_fails": ["具体哪条硬性规则没过，引用原文"],
    "suggestions": [
        {{
            "location": "图X",
            "original": "原文",
            "issue": "问题",
            "fix": "建议改成"
        }}
    ],
    "overall": "一句话总评",
    "subtext_check": "这篇的潜台词是什么？如果说不出来就是没有",
    "best_line": "全篇最好的一句话是哪句，为什么好"
}}"""


def critic_check(draft, reference_texts):
    """Critic 检查初稿"""
    ref_compare = ""
    if reference_texts:
        ref_compare = f"参考范文（对标这个水平）：\n{reference_texts[0][:2000]}"

    prompt = CRITIC_PROMPT.format(
        draft=draft,
        范文对比=ref_compare if ref_compare else "（范文不可用，仅根据规则检查）",
    )

    response = gemini_client.models.generate_content(
        model="gemini-2.5-pro", contents=prompt
    )

    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # 尝试提取 JSON
        import re as _re
        match = _re.search(r'\{.*\}', raw, _re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return {"pass": False, "score": 0, "hard_fails": [], "suggestions": [],
                "overall": raw[:200], "subtext_check": "", "best_line": ""}


# ==================== 5. Refine ====================

REFINE_PROMPT = """你是"妈的欧洲账本"的文案写手。你的初稿被主编打回了，请根据修改意见重写。

## 初稿
{draft}

## 主编意见
总评：{overall}
评分：{score}/10
潜台词检查：{subtext_check}

### 硬性问题（必须修）
{hard_fails}

### 具体修改建议
{suggestions}

### 最好的一句话（保留）
{best_line}

## 要求
- 针对每个问题逐一修改
- 保留"最好的一句话"不要动
- 保留整篇的叙事结构，只改有问题的地方
- 如果潜台词检查说"没有潜台词"，重写时要埋一层没说出来的意思
- 直接输出修改后的完整帖子，不要解释改了什么"""


def refine_draft(draft, critic_result):
    """根据 Critic 意见修改"""
    hard_fails_text = "\n".join(f"- {f}" for f in critic_result.get("hard_fails", []))
    suggestions_text = ""
    for s in critic_result.get("suggestions", []):
        if isinstance(s, dict):
            suggestions_text += f"- {s.get('location', '?')}: {s.get('issue', '')}\n"
            suggestions_text += f"  原文: {s.get('original', '')}\n"
            suggestions_text += f"  改成: {s.get('fix', '')}\n"
        else:
            suggestions_text += f"- {s}\n"

    prompt = REFINE_PROMPT.format(
        draft=draft,
        overall=critic_result.get("overall", ""),
        score=critic_result.get("score", 0),
        subtext_check=critic_result.get("subtext_check", ""),
        hard_fails=hard_fails_text if hard_fails_text else "无",
        suggestions=suggestions_text if suggestions_text else "无",
        best_line=critic_result.get("best_line", ""),
    )

    response = gemini_client.models.generate_content(
        model="gemini-2.5-pro", contents=prompt
    )
    return response.text.strip()


# ==================== 6. 主流程 ====================

def generate_post(槽点, num_slides=10, max_rounds=3, verbose=True):
    """
    完整生成流程。

    Args:
        槽点: 用户的真实经历/吐槽，流水账即可
        num_slides: 生成几张图
        max_rounds: 最多修改几轮
        verbose: 是否打印过程

    Returns:
        dict: {draft, critic, rounds, final}
    """
    if verbose:
        print(f"\n{'='*60}")
        print(f"开始生成")
        print(f"{'='*60}")

    # ---- 加载资源 ----
    if verbose:
        print("\n[1/6] 加载资源...")
    strategy_lib = load_strategy_library()
    reference_posts = load_reference_posts()
    raw_posts = load_raw_posts()

    # ---- 知识图谱联想 ----
    if verbose:
        print("\n[2/6] 知识图谱联想...")
    keywords = extract_keywords(槽点)
    if verbose:
        print(f"  关键词: {keywords}")

    graph_data = query_graph(keywords)
    if verbose:
        cps = graph_data.get("conflict_pairs", [])
        if cps:
            print(f"  找到 {len(cps)} 个冲突对:")
            for cp in cps[:4]:
                print(f"    {cp['keyword']} → [{cp['relation']}] → {cp['slot']}")
        else:
            print("  图谱中未找到直接冲突对")

    # ---- 梗库检索 ----
    if verbose:
        print("\n[3/6] 梗库检索灵感...")
    rag_memes = query_rag(槽点, top_k=5)
    if verbose:
        print(f"  找到 {len(rag_memes)} 条相关梗")
        for m in rag_memes[:2]:
            print(f"    {m[:80]}...")

    # ---- 匹配策略 + 范文 ----
    if verbose:
        print("\n[4/6] 匹配场景策略和范文...")

    strategies = []
    if strategy_lib:
        strategies = match_strategy(槽点, strategy_lib)
        if verbose and strategies:
            print(f"  匹配到场景: {[s['scene'] for s in strategies]}")

    ref_posts = []
    ref_texts = []
    if reference_posts:
        ref_posts = find_reference_posts(槽点, reference_posts)
        ref_texts = [format_reference_post(p, raw_posts) for p in ref_posts]
        if verbose and ref_posts:
            print(f"  参考范文: {[p.get('title', '?') for p in ref_posts]}")

    # ---- 生成初稿 ----
    if verbose:
        print(f"\n[5/6] 生成初稿...")
    draft = generate_draft(槽点, strategies, None, ref_texts, graph_data, rag_memes, num_slides)
    if verbose:
        print(f"\n--- 初稿 ---\n{draft}\n")

    # ---- Critic 循环 ----
    final_draft = draft
    all_critics = []

    for round_num in range(max_rounds):
        if verbose:
            print(f"\n[6/6] Critic 检查 (第{round_num + 1}轮)...")
        critic_result = critic_check(final_draft, ref_texts)
        all_critics.append(critic_result)

        score = critic_result.get("score", 0)
        passed = critic_result.get("pass", False)
        hard_fails = critic_result.get("hard_fails", [])

        if verbose:
            print(f"  评分: {score}/10")
            print(f"  通过: {passed}")
            print(f"  总评: {critic_result.get('overall', '')}")
            print(f"  潜台词: {critic_result.get('subtext_check', '')}")
            print(f"  最佳句: {critic_result.get('best_line', '')}")
            if hard_fails:
                print(f"  硬性问题:")
                for f in hard_fails:
                    print(f"    - {f}")
            for s in critic_result.get("suggestions", [])[:3]:
                if isinstance(s, dict):
                    print(f"  修改建议: {s.get('location', '?')}: {s.get('issue', '')}")

        if passed and score >= 7 and not hard_fails:
            if verbose:
                print(f"\n  通过！最终评分: {score}/10")
            break

        if round_num < max_rounds - 1:
            if verbose:
                print(f"\n[5/5] 根据意见修改 (第{round_num + 1}轮)...")
            final_draft = refine_draft(final_draft, critic_result)
            if verbose:
                print(f"\n--- 修改后 ---\n{final_draft}\n")
        else:
            if verbose:
                print(f"\n  达到最大修改轮数 ({max_rounds})，输出当前版本")

    return {
        "input": 槽点,
        "final": final_draft,
        "rounds": len(all_critics),
        "final_score": all_critics[-1].get("score", 0) if all_critics else 0,
        "critics": all_critics,
    }


# ==================== 交互模式 ====================

def interactive():
    print("=" * 60)
    print("妈的欧洲账本风格 · 帖子生成器")
    print("输入你的槽点（真实经历），系统帮你写成帖子")
    print("输入 q 退出")
    print("=" * 60)

    while True:
        print("\n请输入你的槽点/真实经历（越具体越好，有数字有细节）：")
        print("（多行输入，输入空行结束）")

        lines = []
        while True:
            line = input()
            if line == "":
                break
            if line.lower() == "q":
                return
            lines.append(line)

        if not lines:
            continue

        槽点 = "\n".join(lines)

        n = input("生成几张图（默认10）：").strip()
        num_slides = int(n) if n.isdigit() else 10

        result = generate_post(槽点, num_slides=num_slides, verbose=True)

        print(f"\n{'='*60}")
        print(f"最终帖子（评分: {result['final_score']}/10，修改{result['rounds']}轮）")
        print(f"{'='*60}")
        print(result["final"])


# ==================== Demo ====================

def demo():
    test_input = """在巴黎租了个阁楼，月租1200欧
楼梯窄到行李箱要竖着搬
没有电梯，6楼
房东是个80岁老太太，英语一句不会，我法语也不会
热水器每次只能用15分钟，之后要等40分钟才能再用
窗外能看到埃菲尔铁塔的一个角，大概占视野的3%
邻居每天早上7点拉小提琴，拉的是"梁祝"
马桶冲水声整栋楼都能听见
第一周洗了3次冷水澡因为不知道热水器要手动开
房租不含电费，第一个月电费87欧"""

    result = generate_post(test_input, num_slides=10, verbose=True)

    print(f"\n{'='*60}")
    print(f"最终帖子（评分: {result['final_score']}/10，修改{result['rounds']}轮）")
    print(f"{'='*60}")
    print(result["final"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="生成妈的欧洲账本风格帖子")
    parser.add_argument("--interactive", action="store_true", help="交互模式")
    parser.add_argument("--demo", action="store_true", help="演示模式")
    args = parser.parse_args()

    if args.interactive:
        interactive()
    elif args.demo:
        demo()
    else:
        demo()
