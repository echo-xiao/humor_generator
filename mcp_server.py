"""
mcp_server.py — "妈的欧洲账本"风格文案生成 MCP Server

MCP 只做数据检索，Claude 自己做生成/检查/修改。

工具：
  1. get_references  — 输入话题 → 返回最相关的范文原文 + 7层分析
  2. get_rules       — 返回完整规则手册
  3. get_strategy    — 输入话题 → 返回场景写作策略
  4. search_graph    — 输入关键词 → 返回知识图谱冲突对
"""

import json
import os
import re
import sys
import logging

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else os.getcwd()
sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

from mcp.server.fastmcp import FastMCP
from google.cloud import storage

PROJECT_ID = "gen-lang-client-0577448366"
BUCKET_NAME = "xhs-humor-data"

logging.basicConfig(level=logging.WARNING)

_bucket = None
_cache = {}
_graph = None


def get_bucket():
    global _bucket
    if _bucket is None:
        _bucket = storage.Client(project=PROJECT_ID).bucket(BUCKET_NAME)
    return _bucket


def _load_json(path):
    """先读本地 cache，没有再读 GCS"""
    if path in _cache:
        return _cache[path]
    # 本地
    local = os.path.join(_PROJECT_ROOT, "data", "cache", os.path.basename(path))
    if os.path.exists(local):
        with open(local, "r", encoding="utf-8") as f:
            data = json.load(f)
        _cache[path] = data
        return data
    # GCS fallback
    try:
        blob = get_bucket().blob(path)
        if not blob.exists():
            return None
        data = json.loads(blob.download_as_text(encoding="utf-8"))
        _cache[path] = data
        return data
    except Exception:
        return None


LOCAL_CACHE_DIR = os.path.join(_PROJECT_ROOT, "data", "cache")
ANALYZED_CACHE = os.path.join(LOCAL_CACHE_DIR, "analyzed_all.json")
RAW_CACHE = os.path.join(LOCAL_CACHE_DIR, "raw_all.json")


def _load_all_analyzed():
    if "refs" in _cache:
        return _cache["refs"]

    # 先试本地缓存
    if os.path.exists(ANALYZED_CACHE):
        with open(ANALYZED_CACHE, "r", encoding="utf-8") as f:
            posts = json.load(f)
        _cache["refs"] = posts
        return posts

    # 从 GCS 下载（首次慢，之后秒读）
    blobs = list(get_bucket().list_blobs(prefix="data/analyzed_posts/"))
    posts = []
    for b in blobs:
        if b.name.endswith(".json"):
            try:
                posts.append(json.loads(b.download_as_text(encoding="utf-8")))
            except Exception:
                pass

    if posts:
        os.makedirs(LOCAL_CACHE_DIR, exist_ok=True)
        with open(ANALYZED_CACHE, "w", encoding="utf-8") as f:
            json.dump(posts, f, ensure_ascii=False)

    _cache["refs"] = posts
    return posts


def _load_raw_posts():
    if "raw" in _cache:
        return _cache["raw"]

    # 先试本地缓存
    if os.path.exists(RAW_CACHE):
        with open(RAW_CACHE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        _cache["raw"] = raw
        return raw

    # 从 GCS 下载
    blobs = list(get_bucket().list_blobs(prefix="data/raw_data/妈的欧洲账本/"))
    txt_blobs = [b for b in blobs if b.name.endswith(".txt")]
    posts = {}
    for b in txt_blobs:
        name = b.name.split("/")[-1]
        post_name = re.sub(r"_\d+\.jpg\.txt$", "", name)
        posts.setdefault(post_name, []).append(b)
    for k in posts:
        posts[k].sort(key=lambda b: int(m.group(1)) if (m := re.search(r"_(\d+)\.jpg", b.name)) else 0)
    raw = {}
    for name, blob_list in posts.items():
        raw[name] = [b.download_as_text(encoding="utf-8").strip() or "(空)" for b in blob_list]

    if raw:
        os.makedirs(LOCAL_CACHE_DIR, exist_ok=True)
        with open(RAW_CACHE, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False)

    _cache["raw"] = raw
    return raw


def _format_post(post, raw_posts):
    title = post.get("title", "?")
    lines = [f"# {title}\n"]

    # 原文
    for name, texts in raw_posts.items():
        if title in name or name in title:
            for i, t in enumerate(texts, 1):
                lines.append(f"[图{i}] {t}")
            break

    # 7层分析
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

            # 手艺层
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

    # 整篇分析
    ws = post.get("writing_style", {})
    if ws:
        lines.append(f"## 写作风格")
        lines.append(f"- 模式: {ws.get('pattern','')}")
        lines.append(f"- 节奏: {ws.get('rhythm','')}")
        lines.append(f"- 语气: {ws.get('tone','')}")
        lines.append(f"- 招牌动作: {ws.get('signature_moves',[])}")

    ns = post.get("narrative_structure", {})
    if ns:
        lines.append(f"\n## 叙事结构")
        lines.append(f"- 弧度: {ns.get('arc','')}")
        lines.append(f"- Hook: {ns.get('hook_strategy','')}")
        lines.append(f"- 结尾: {ns.get('ending_strategy','')}")
        lines.append(f"- 笑点密度: {ns.get('punchline_density','')}")
        lines.append(f"- 递进: {ns.get('escalation_pattern','')}")

    return "\n".join(lines)


# ==================== MCP ====================

mcp = FastMCP("humor_generator")


@mcp.tool()
def get_references(topic: str, top_k: int = 3) -> str:
    """
    根据话题返回最相关的范文（原文 + 完整7层分析）。

    这些是"妈的欧洲账本"的真实帖子，包含每个笑点的结构/情绪/语言/节奏/表达/手艺层分析。
    用这些范文作为风格锚点来写新帖子。

    Args:
        topic: 话题或槽点，如"租房""堵车""吃饭被坑"
        top_k: 返回几篇（默认3）
    """
    analyzed = _load_all_analyzed()
    raw_posts = _load_raw_posts()

    if not analyzed:
        return "范文库未就绪，请先运行 analyze_posts.py"

    # 简单匹配：按标题关键词
    scored = []
    topic_lower = topic.lower()
    topic_chars = set(topic)
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

    # 如果没有好的匹配，返回最有代表性的几篇
    if scored[0][0] == 0:
        # 按笑点数排序，选分析最丰富的
        scored = [(len(p.get("punchlines", [])), p) for p in analyzed]
        scored.sort(key=lambda x: -x[0])

    results = []
    for _, p in scored[:top_k]:
        results.append(_format_post(p, raw_posts))

    header = f"找到 {len(analyzed)} 篇范文，返回最相关的 {min(top_k, len(scored))} 篇：\n"
    return header + "\n\n---\n\n".join(results)


@mcp.tool()
def get_rules() -> str:
    """
    返回完整的风格规则手册。

    从195篇帖子中提炼的所有写作规则，包括：
    元规则、数字与精确度、比喻与拟人、句式结构、语气与反转、
    自嘲与免疫、潜台词与留白、叙事编排等。

    用这些规则来检查生成的文案质量。
    """
    rb = _load_json("data/rulebook.json")
    if not rb:
        return """规则手册尚未生成（等 aggregate_rules.py 跑完）。先用以下核心规则：

1. 数字精确：所有金额精确到元，数量精确到个位。"¥1200/月"不是"一千多"
2. 禁止情绪词：不能出现"好气""崩溃""太惨""无语""绝了""笑死"
3. 拟人化：至少一个物体被赋予人格/态度
4. 克制：语气永远比内容冷静一个级别
5. 潜台词：全篇必须有一层没说出来的意思
6. 精确比喻：比喻必须具体（"在机场等一艘船"），不能笼统（"像做梦一样"）
7. 反差结构：每个笑点必须有setup+punchline
8. 升维结尾：最后1-2张图跳出具体事件
9. 不解释笑点：写完就走，不加"哈哈""笑死"
10. 笑点密度：每3张图至少一个笑点
11. 排比前两次正面第三次反转（rule of three）
12. 用崇高/哲学语气描述日常琐事
13. Running gag 每次出现必须升级
14. 说出所有人想过但不敢说的话
15. 全篇不说出核心情绪词，用场景让读者自己感受"""

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


@mcp.tool()
def get_strategy(topic: str) -> str:
    """
    返回某个话题/场景的写作策略。

    告诉你这类场景应该用什么情绪策略、表达策略、语言策略，
    以及最佳范文片段。

    Args:
        topic: 场景或话题，如"交通出行""租房""吃饭""职场""旅行"
    """
    lib = _load_json("data/strategy_library.json")
    if not lib:
        return "策略库尚未生成，请先运行 aggregate_rules.py"

    strategies = lib.get("strategies", [])
    topic_lower = topic.lower()

    # 匹配
    matched = []
    for s in strategies:
        scene = s.get("scene", "")
        if topic in scene or scene in topic or any(c in scene for c in topic):
            matched.append(s)

    if not matched:
        # 返回所有场景列表
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


@mcp.tool()
def get_persona() -> str:
    """
    返回当前IP的人设定义。

    包括：我是谁、我的态度、我的视角、和读者的关系、贯穿元素、红线、潜台词。
    每次写帖子前都应该读一下人设，确保内容一致。

    人设文件在 data/persona.json，用户可以随时修改。
    """
    persona_path = os.path.join(_PROJECT_ROOT, "data", "persona.json")
    if not os.path.exists(persona_path):
        return "人设文件不存在。请先编辑 data/persona.json 定义你的IP人设。"

    with open(persona_path, "r", encoding="utf-8") as f:
        persona = json.load(f)

    # 检查是否填写了
    name = persona.get("账号名", "")
    if not name:
        return ("人设文件存在但未填写。请编辑 data/persona.json，填入你的IP信息。\n\n"
                f"文件位置: {persona_path}\n\n"
                f"当前内容:\n{json.dumps(persona, ensure_ascii=False, indent=2)}")

    return json.dumps(persona, ensure_ascii=False, indent=2)


@mcp.tool()
def list_all_posts() -> str:
    """列出所有195篇范文的标题和话题标签。"""
    analyzed = _load_all_analyzed()
    if not analyzed:
        return "范文库未就绪"

    lines = [f"共 {len(analyzed)} 篇范文：\n"]
    for p in sorted(analyzed, key=lambda x: x.get("title", "")):
        title = p.get("title", "?")
        tags = p.get("topic_tags", [])
        n_punchlines = len(p.get("punchlines", []))
        lines.append(f"- {title} | 标签: {tags} | 笑点: {n_punchlines}个")

    return "\n".join(lines)


@mcp.tool()
def save_draft(title: str, post_text: str) -> str:
    """
    保存文案草稿到本地，方便后续修改和渲染。

    每次生成或修改文案后调用，保存到 output/drafts/ 目录。

    Args:
        title: 帖子标题（用作文件名）
        post_text: 完整文案（===图1=== 格式）
    """
    import time
    drafts_dir = os.path.join(_PROJECT_ROOT, "output", "drafts")
    os.makedirs(drafts_dir, exist_ok=True)

    # 文件名：标题 + 时间戳
    safe_title = re.sub(r'[^\w\u4e00-\u9fff]', '_', title)[:30]
    timestamp = time.strftime("%m%d_%H%M")
    filename = f"{safe_title}_{timestamp}.txt"
    filepath = os.path.join(drafts_dir, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n{post_text}")

    # 同时保存一个 latest.txt 方便快速访问
    latest_path = os.path.join(drafts_dir, "latest.txt")
    with open(latest_path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n{post_text}")

    return f"已保存到 {filepath}\n\n可在 output/drafts/ 目录找到并修改，修改后用 render_and_preview 渲染。"


@mcp.tool()
def list_drafts() -> str:
    """列出所有已保存的文案草稿。"""
    drafts_dir = os.path.join(_PROJECT_ROOT, "output", "drafts")
    if not os.path.exists(drafts_dir):
        return "还没有保存的草稿"

    files = sorted([f for f in os.listdir(drafts_dir) if f.endswith('.txt') and f != 'latest.txt'])
    if not files:
        return "还没有保存的草稿"

    lines = [f"共 {len(files)} 篇草稿：\n"]
    for f in files:
        filepath = os.path.join(drafts_dir, f)
        with open(filepath, "r", encoding="utf-8") as fp:
            first_line = fp.readline().strip()
        lines.append(f"- {f}: {first_line}")

    return "\n".join(lines)


@mcp.tool()
def match_images(post_text: str) -> str:
    """
    为确认后的帖子文案匹配 Google Photos 图片。

    输入完整的帖子文案（===图1=== 格式），系统会从你的 Google Photos 里
    为每张图推荐最合适的照片，并返回查看链接。

    Args:
        post_text: 确认后的帖子文案，格式为 ===图1=== ... ===图2=== ...
    """
    try:
        from pipeline.images.match_images import match_images_for_post, format_results
        results = match_images_for_post(post_text)
        if not results:
            return "匹配失败。请确认：\n1. 图片索引已构建（python pipeline/images/photo_index.py）\n2. 文案格式正确（===图1=== ...）"
        return format_results(results)
    except Exception as e:
        return f"图片匹配出错: {e}"


@mcp.tool()
def render_and_preview(post_text: str, title: str = "") -> str:
    """
    渲染帖子所有图片并打开预览文件夹 + 上传到 Google Drive。

    第1张图自动用封面模式（大黑块+白字），其余用正常排版。
    渲染完成后上传到 Google Drive "小红书发布" 文件夹，手机可直接下载发布。

    Args:
        post_text: 确认后的帖子文案，格式为 ===图1=== ... ===图2=== ...
        title: 帖子标题（用于创建子文件夹）
    """
    try:
        from pipeline.publish.publish import preview_post
        rendered = preview_post(post_text)
        if not rendered:
            return "渲染失败，请检查文案格式"

        upload_msg = ""
        try:
            from pipeline.images.photo_index import get_drive_creds
            import requests as _req
            creds = get_drive_creds()

            resp = _req.get(
                "https://www.googleapis.com/drive/v3/files",
                headers={"Authorization": f"Bearer {creds.token}"},
                params={"q": "name='小红书发布' and mimeType='application/vnd.google-apps.folder' and trashed=false",
                        "fields": "files(id)"},
            )
            folders = resp.json().get("files", [])
            if folders:
                folder_id = folders[0]["id"]
            else:
                resp = _req.post(
                    "https://www.googleapis.com/drive/v3/files",
                    headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"},
                    json={"name": "小红书发布", "mimeType": "application/vnd.google-apps.folder"},
                )
                folder_id = resp.json()["id"]

            from datetime import datetime
            sub_name = title if title else datetime.now().strftime("%m%d_%H%M")
            resp = _req.post(
                "https://www.googleapis.com/drive/v3/files",
                headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"},
                json={"name": sub_name, "mimeType": "application/vnd.google-apps.folder", "parents": [folder_id]},
            )
            sub_id = resp.json()["id"]

            for img_path in rendered:
                fname = os.path.basename(img_path)
                metadata = json.dumps({"name": fname, "parents": [sub_id]})
                with open(img_path, "rb") as f:
                    img_data = f.read()
                _req.post(
                    "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
                    headers={"Authorization": f"Bearer {creds.token}"},
                    files={
                        "metadata": ("metadata", metadata, "application/json"),
                        "file": (fname, img_data, "image/jpeg"),
                    },
                )
            upload_msg = f"\n\n已上传到 Google Drive: 小红书发布/{sub_name}\n手机打开 Google Drive app 即可下载发布"
        except Exception as e:
            upload_msg = f"\n\nGoogle Drive 上传失败({e})，请手动从预览文件夹获取图片"

        return f"渲染完成！共 {len(rendered)} 张图片\n预览文件夹已打开{upload_msg}"
    except Exception as e:
        return f"渲染出错: {e}"


if __name__ == "__main__":
    mcp.run()
