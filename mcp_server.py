"""
MCP Server — "妈的欧洲账本"风格文案生成

MCP 只做数据检索，Claude 自己做生成/检查/修改。
"""

import json
import os
import re
import sys
import time

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else os.getcwd()
sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

from mcp.server.fastmcp import FastMCP
from pipeline.data import (
    search_references, get_rulebook, get_strategies,
    get_persona_data, list_posts, DATA_DIR,
)

mcp = FastMCP("humor_generator")

FALLBACK_RULES = """核心规则：
1. 数字精确："¥1200/月"不是"一千多"
2. 禁止情绪词：不能出现"好气""崩溃""太惨""无语""绝了""笑死"
3. 拟人化：至少一个物体被赋予人格/态度
4. 克制：语气永远比内容冷静一个级别
5. 潜台词：全篇必须有一层没说出来的意思
6. 精确比喻："在机场等一艘船"，不是"像做梦一样"
7. 反差结构：每个笑点必须有 setup + punchline
8. 升维结尾：最后1-2张图跳出具体事件
9. 不解释笑点：写完就走，不加"哈哈""笑死"
10. 笑点密度：每3张图至少一个笑点"""


@mcp.tool()
def get_references(topic: str, top_k: int = 3) -> str:
    """
    根据话题返回最相关的范文(原文 + 完整7层分析)。

    这些是"妈的欧洲账本"的真实帖子,包含每个笑点的结构/情绪/语言/节奏/表达/手艺层分析。
    用这些范文作为风格锚点来写新帖子。

    Args:
        topic: 话题或槽点,如"租房""堵车""吃饭被坑"
        top_k: 返回几篇(默认3)
    """
    result = search_references(topic, top_k)
    return result or "范文库未就绪"


@mcp.tool()
def get_rules() -> str:
    """
    返回完整的风格规则手册。

    从195篇帖子中提炼的所有写作规则,包括:
    元规则、数字与精确度、比喻与拟人、句式结构、语气与反转、
    自嘲与免疫、潜台词与留白、叙事编排等。

    用这些规则来检查生成的文案质量。
    """
    return get_rulebook() or FALLBACK_RULES


@mcp.tool()
def get_strategy(topic: str) -> str:
    """
    返回某个话题/场景的写作策略。

    告诉你这类场景应该用什么情绪策略、表达策略、语言策略,
    以及最佳范文片段。

    Args:
        topic: 场景或话题,如"交通出行""租房""吃饭""职场""旅行"
    """
    return get_strategies(topic) or "策略库尚未生成"


@mcp.tool()
def get_persona() -> str:
    """
    返回当前IP的人设定义。

    包括:我是谁、我的态度、我的视角、和读者的关系、贯穿元素、红线、潜台词。
    每次写帖子前都应该读一下人设,确保内容一致。

    人设文件在 data/persona.json,用户可以随时修改。
    """
    persona = get_persona_data()
    if not persona:
        return "人设文件不存在。请编辑 data/persona.json"
    if not persona.get("账号名"):
        return f"人设文件未填写。请编辑 data/persona.json:\n{json.dumps(persona, ensure_ascii=False, indent=2)}"
    return json.dumps(persona, ensure_ascii=False, indent=2)


@mcp.tool()
def list_all_posts() -> str:
    """列出所有195篇范文的标题和话题标签。"""
    return list_posts() or "范文库未就绪"


@mcp.tool()
def save_draft(title: str, post_text: str) -> str:
    """
    保存文案草稿到 output/{title}/post.txt。

    Args:
        title: 帖子标题(用作文件夹名)
        post_text: 完整文案(===图1=== 格式)
    """
    safe_title = re.sub(r'[^\w\u4e00-\u9fff]', '_', title)[:30]
    post_dir = os.path.join(_PROJECT_ROOT, "output", safe_title)
    os.makedirs(post_dir, exist_ok=True)

    filepath = os.path.join(post_dir, "post.txt")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(post_text)

    return f"已保存到 {filepath}"


@mcp.tool()
def list_drafts() -> str:
    """列出所有已保存的帖子(output下的文件夹)。"""
    output_dir = os.path.join(_PROJECT_ROOT, "output")
    if not os.path.exists(output_dir):
        return "还没有保存的帖子"

    folders = []
    for name in sorted(os.listdir(output_dir)):
        folder = os.path.join(output_dir, name)
        if os.path.isdir(folder) and name != "photo_cache":
            post_file = os.path.join(folder, "post.txt")
            has_text = os.path.exists(post_file)
            images = [f for f in os.listdir(folder) if f.endswith('.jpg')]
            folders.append(f"- {name} | 文案:{'有' if has_text else '无'} | 图片:{len(images)}张")

    if not folders:
        return "还没有保存的帖子"

    return f"共 {len(folders)} 篇：\n" + "\n".join(folders)


@mcp.tool()
def match_images(post_text: str) -> str:
    """
    为确认后的帖子文案匹配 Google Photos 图片。

    输入完整的帖子文案(===图1=== 格式),系统会从你的 Google Photos 里
    为每张图推荐最合适的照片,并返回查看链接。

    Args:
        post_text: 确认后的帖子文案,格式为 ===图1=== ... ===图2=== ...
    """
    try:
        from pipeline.match_images import match_images_for_post, format_results
        results = match_images_for_post(post_text)
        if not results:
            return "匹配失败。请确认文案格式正确（===图1=== ...）"
        return format_results(results)
    except Exception as e:
        return f"图片匹配出错: {e}"


@mcp.tool()
def render_and_preview(post_text: str, title: str = "", text_only: bool = False) -> str:
    """
    渲染帖子所有图片并打开预览文件夹 + 自动质检。

    第1张图自动用封面模式(大黑块+白字),其余用正常排版。
    渲染完成后自动运行质检,如果有问题会返回具体的修复指令。
    你需要根据修复指令自行处理(精简文案/换图/重新渲染等)。

    Args:
        post_text: 确认后的帖子文案,格式为 ===图1=== ... ===图2=== ...
        title: 帖子标题(用于创建子文件夹)
    """
    try:
        from pipeline.publish import preview_post
        photo_paths = {} if text_only else None
        rendered = preview_post(post_text, photo_paths=photo_paths, title=title)
        if not rendered:
            return "渲染失败，请检查文案格式"
        folder = os.path.dirname(rendered[0])

        # 自动质检
        from pipeline.critic import critique_post
        critic_result = critique_post(folder)
        report = critic_result.get("report", "")
        passed = critic_result.get("pass", False)
        fixes = critic_result.get("fixes", [])

        result_lines = [
            f"渲染完成！共 {len(rendered)} 张图片",
            f"预览文件夹: {folder}",
            "",
            "=" * 40,
            "自动质检结果:",
            report,
        ]

        if not passed and fixes:
            result_lines.append("")
            result_lines.append("=" * 40)
            result_lines.append("请根据以下修复指令处理:")
            for fix in fixes:
                if fix["type"] == "need_photo":
                    result_lines.append(
                        f"  图{fix['slide']}: 缺少背景照片。"
                        f"请为文案「{fix['text'][:30]}」找一张相关照片，"
                        f"或改用 text_only=true 渲染为纯文字卡片。"
                    )
                elif fix["type"] == "text_overflow":
                    result_lines.append(
                        f"  图{fix['slide']}: 文字溢出。"
                        f"请精简文案或拆分成两张图后重新渲染。"
                    )
                elif fix["type"] == "text_unreadable":
                    result_lines.append(
                        f"  图{fix['slide']}: 文字不清晰。"
                        f"请换一张背景更简洁的照片后重新渲染。"
                    )
                elif fix["type"] == "irrelevant_photo":
                    result_lines.append(
                        f"  图{fix['slide']}: 图文不匹配。"
                        f"请为文案「{fix.get('text', '')[:30]}」换一张更相关的照片。"
                    )
        elif passed:
            result_lines.append("")
            result_lines.append("质检通过！可以发布。")

        return "\n".join(result_lines)
    except Exception as e:
        import traceback
        return f"渲染出错: {e}\n{traceback.format_exc()}"


if __name__ == "__main__":
    mcp.run()
