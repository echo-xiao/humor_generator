"""
fetch_xhs.py — 抓取小红书帖子内容并分析

输入小红书链接 → 抓取文案 → 分析笑点 → 存入素材库
"""

import json
import os
import re
import sys
import logging

from dotenv import load_dotenv

_PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, _PROJECT_ROOT)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

from google import genai

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=GEMINI_API_KEY)


def fetch_xhs_content(url):
    """
    抓取小红书帖子内容。
    小红书页面是 JS 渲染的，简单 requests 拿不到。
    用 Playwright 无头浏览器抓取。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "请先安装 playwright: pip install playwright && playwright install chromium"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_extra_http_headers({
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15"
            })

            page.goto(url, timeout=15000)
            page.wait_for_timeout(3000)

            # 提取标题
            title = ""
            try:
                title_el = page.query_selector("#detail-title") or page.query_selector(".title")
                if title_el:
                    title = title_el.inner_text().strip()
            except Exception:
                pass

            # 提取正文
            content = ""
            try:
                # 小红书正文在 #detail-desc 或 .desc
                desc_el = page.query_selector("#detail-desc") or page.query_selector(".desc") or page.query_selector(".note-text")
                if desc_el:
                    content = desc_el.inner_text().strip()
            except Exception:
                pass

            # 如果上面没拿到，尝试拿整个页面文本
            if not content:
                try:
                    content = page.inner_text("body")[:3000]
                except Exception:
                    pass

            browser.close()

            if title or content:
                return {"title": title, "content": content, "url": url}, None
            else:
                return None, "页面内容为空，可能需要登录或链接无效"

    except Exception as e:
        return None, f"抓取失败: {e}"


def analyze_xhs_content(title, content):
    """用 Gemini 分析小红书帖子的笑点和亮点"""
    prompt = f"""分析这篇小红书帖子的笑点和写作技巧。

标题：{title}
内容：{content}

请输出JSON：
{{
    "summary": "一句话总结这篇帖子",
    "punchlines": [
        {{
            "text": "笑点原文",
            "mechanism": "笑点机制（预期违背/降格/自嘲/精确荒诞/克制反讽/...）",
            "why_funny": "为什么好笑",
            "transferable": "这个技巧怎么用在我的帖子里"
        }}
    ],
    "good_phrases": ["值得借鉴的好句子"],
    "topic_inspiration": "这篇帖子给了什么选题灵感",
    "tags": ["话题标签"]
}}

只输出JSON。"""

    try:
        resp = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config={"max_output_tokens": 2048, "thinking_config": {"thinking_budget": 512}},
        )
        raw = resp.text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        if raw.endswith("```"):
            raw = raw[:-3]
        return json.loads(raw)
    except Exception as e:
        return {"summary": f"分析失败: {e}", "punchlines": [], "good_phrases": [], "tags": []}


def fetch_and_analyze(url):
    """完整流程：抓取 + 分析 + 存入素材库"""
    from pipeline.feed.feed_material import add_material

    # 1. 抓取
    data, err = fetch_xhs_content(url)
    if err:
        return f"抓取失败: {err}\n\n你可以手动复制帖子内容，用 feed_material 工具直接添加。"

    title = data["title"]
    content = data["content"]

    # 2. 分析
    analysis = analyze_xhs_content(title, content)

    # 3. 存入素材库
    material_content = f"[{title}]\n{content[:500]}"
    entry = add_material(
        content=material_content,
        material_type="reference",
        source=f"小红书: {url}",
        tags=analysis.get("tags", []),
    )

    # 存笑点
    for p in analysis.get("punchlines", []):
        add_material(
            content=f"{p['text']}\n机制: {p['mechanism']}\n为什么好笑: {p['why_funny']}\n怎么用: {p['transferable']}",
            material_type="joke",
            source=f"小红书: {title}",
            tags=analysis.get("tags", []),
        )

    # 存金句
    for phrase in analysis.get("good_phrases", []):
        add_material(content=phrase, material_type="phrase", source=f"小红书: {title}")

    # 存选题灵感
    if analysis.get("topic_inspiration"):
        add_material(
            content=analysis["topic_inspiration"],
            material_type="topic",
            source=f"小红书: {title}",
        )

    # 格式化输出
    lines = [
        f"# {title}\n",
        f"摘要: {analysis.get('summary', '')}\n",
    ]

    punchlines = analysis.get("punchlines", [])
    if punchlines:
        lines.append(f"## 笑点分析（{len(punchlines)}个）\n")
        for p in punchlines:
            lines.append(f"- [{p['mechanism']}] {p['text'][:60]}")
            lines.append(f"  为什么好笑: {p['why_funny'][:60]}")
            lines.append(f"  怎么用: {p['transferable'][:60]}")

    phrases = analysis.get("good_phrases", [])
    if phrases:
        lines.append(f"\n## 好句子")
        for ph in phrases:
            lines.append(f"- {ph}")

    if analysis.get("topic_inspiration"):
        lines.append(f"\n## 选题灵感\n{analysis['topic_inspiration']}")

    lines.append(f"\n已存入素材库（{1 + len(punchlines) + len(phrases)}条）")

    return "\n".join(lines)
