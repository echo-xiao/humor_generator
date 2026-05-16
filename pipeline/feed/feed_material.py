"""
feed_material.py — 素材 feed 入口

让你随时把小红书看到的好 topic、脱口秀段子、生活灵感灌进系统。
素材会存入本地 JSON 并可选择性地导入知识图谱和 RAG 梗库。

用法：
  python pipeline/feed/feed_material.py --interactive   # 交互式输入
  python pipeline/feed/feed_material.py --list           # 查看所有素材
  python pipeline/feed/feed_material.py --stats          # 统计

MCP 工具 feed_material 也可直接调用。
"""

import json
import os
import sys
import time
import logging
from datetime import datetime

from dotenv import load_dotenv

_PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, _PROJECT_ROOT)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

FEED_PATH = os.path.join(_PROJECT_ROOT, "data", "feed_materials.json")

# 素材类型
MATERIAL_TYPES = {
    "topic": "小红书话题/选题灵感",
    "joke": "脱口秀段子/搞笑内容",
    "life": "生活经历/槽点素材",
    "reference": "参考帖子/风格参考",
    "phrase": "好句子/金句",
    "other": "其他",
}


def load_feed():
    if os.path.exists(FEED_PATH):
        with open(FEED_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_feed(feed):
    os.makedirs(os.path.dirname(FEED_PATH), exist_ok=True)
    with open(FEED_PATH, "w", encoding="utf-8") as f:
        json.dump(feed, f, ensure_ascii=False, indent=2)


def add_material(content, material_type="other", source="", tags=None):
    """
    添加一条素材

    Args:
        content: 素材内容
        material_type: 类型 (topic/joke/life/reference/phrase/other)
        source: 来源（如"小红书""脱口秀大会S6"）
        tags: 标签列表

    Returns:
        添加的素材条目
    """
    feed = load_feed()

    entry = {
        "id": len(feed) + 1,
        "content": content.strip(),
        "type": material_type,
        "source": source,
        "tags": tags or [],
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "used": False,
        "used_in": None,
    }

    feed.append(entry)
    save_feed(feed)
    return entry


def list_materials(material_type=None, unused_only=False):
    """列出素材"""
    feed = load_feed()
    if material_type:
        feed = [m for m in feed if m["type"] == material_type]
    if unused_only:
        feed = [m for m in feed if not m.get("used")]
    return feed


def mark_used(material_id, used_in=""):
    """标记素材为已使用"""
    feed = load_feed()
    for m in feed:
        if m["id"] == material_id:
            m["used"] = True
            m["used_in"] = used_in
            save_feed(feed)
            return True
    return False


def get_stats():
    """统计"""
    feed = load_feed()
    stats = {"total": len(feed), "by_type": {}, "unused": 0}
    for m in feed:
        t = m.get("type", "other")
        stats["by_type"][t] = stats["by_type"].get(t, 0) + 1
        if not m.get("used"):
            stats["unused"] += 1
    return stats


def format_material(m):
    """格式化一条素材"""
    used_mark = "[已用]" if m.get("used") else ""
    type_name = MATERIAL_TYPES.get(m["type"], m["type"])
    tags = f" #{' #'.join(m['tags'])}" if m.get("tags") else ""
    source = f" (来源: {m['source']})" if m.get("source") else ""
    return f"[{m['id']}] {used_mark}[{type_name}] {m['content'][:100]}{source}{tags} — {m['created']}"


def feed_to_topic_pool():
    """将 feed 中的 topic 类素材导入 topic_pool.json"""
    feed = load_feed()
    topics = [m["content"] for m in feed if m["type"] == "topic" and not m.get("used")]

    pool_path = os.path.join(_PROJECT_ROOT, "data", "topic_pool.json")
    if os.path.exists(pool_path):
        with open(pool_path, "r", encoding="utf-8") as f:
            pool = json.load(f)
    else:
        pool = []

    added = 0
    for t in topics:
        if t not in pool:
            pool.append(t)
            added += 1

    with open(pool_path, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)

    return added, len(pool)


def interactive():
    """交互式输入素材"""
    print("=" * 50)
    print("素材 Feed 入口")
    print("输入素材，系统会自动分类存储")
    print("输入 q 退出, list 查看, stats 统计")
    print("=" * 50)

    while True:
        print(f"\n素材类型: {', '.join(f'{k}={v}' for k, v in MATERIAL_TYPES.items())}")
        type_input = input("类型 (直接回车=auto): ").strip().lower()

        if type_input == "q":
            break
        if type_input == "list":
            for m in list_materials():
                print(format_material(m))
            continue
        if type_input == "stats":
            s = get_stats()
            print(f"总计: {s['total']}条, 未使用: {s['unused']}条")
            for t, c in s["by_type"].items():
                print(f"  {MATERIAL_TYPES.get(t, t)}: {c}条")
            continue

        if type_input not in MATERIAL_TYPES:
            type_input = "other"

        print("输入素材内容（多行输入，空行结束）：")
        lines = []
        while True:
            line = input()
            if line == "":
                break
            lines.append(line)

        if not lines:
            continue

        content = "\n".join(lines)
        source = input("来源（如'小红书''脱口秀'，直接回车跳过）：").strip()
        tags_input = input("标签（逗号分隔，直接回车跳过）：").strip()
        tags = [t.strip() for t in tags_input.split(",") if t.strip()] if tags_input else []

        entry = add_material(content, type_input, source, tags)
        print(f"\n已添加: {format_material(entry)}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="素材 Feed 入口")
    parser.add_argument("--interactive", "-i", action="store_true", help="交互模式")
    parser.add_argument("--list", "-l", action="store_true", help="列出所有素材")
    parser.add_argument("--stats", "-s", action="store_true", help="统计")
    parser.add_argument("--sync-topics", action="store_true", help="将topic素材同步到topic_pool")
    args = parser.parse_args()

    if args.list:
        for m in list_materials():
            print(format_material(m))
    elif args.stats:
        s = get_stats()
        print(f"总计: {s['total']}条, 未使用: {s['unused']}条")
        for t, c in s["by_type"].items():
            print(f"  {MATERIAL_TYPES.get(t, t)}: {c}条")
    elif args.sync_topics:
        added, total = feed_to_topic_pool()
        print(f"同步了 {added} 个新话题到 topic_pool（现共 {total} 个）")
    elif args.interactive:
        interactive()
    else:
        interactive()
