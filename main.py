"""
main.py
幽默生成器入口。

用法：
  python main.py --topic 打工人        # 指定话题生成
  python main.py --random              # 从图谱热门话题随机抽取
  python main.py --list                # 查看话题池
  python main.py --refresh             # 从图谱重新生成话题池
  python main.py                       # 交互模式
"""

import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="google.auth")
warnings.filterwarnings("ignore", category=UserWarning, module="jieba")

import argparse
import json
import os
import random

from src.critic import run as generate_and_score

TOPIC_POOL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "topic_pool.json")


# ==================== 话题池（从图谱生成） ====================

def build_topic_pool(top_k=200, min_len=2, max_len=4):
    """从知识图谱中提取话题——只从自己的幽默数据源（妈的欧洲账本/脱口秀/YouTube）"""
    from src.knowledge.graph import load_graph, SOURCE_WEIGHTS, YOUTUBE_SOURCE_WEIGHT
    print("从图谱生成话题池（只取幽默语料来源的节点）...")
    G = load_graph()

    # 只有来自这些来源的节点才有资格做话题
    HUMOR_SOURCES = {s for s, w in SOURCE_WEIGHTS.items() if w >= 3.0}

    candidates = []
    for node in G.nodes():
        if not (min_len <= len(node) <= max_len):
            continue
        node_sources = G.nodes[node].get("sources", set())
        if isinstance(node_sources, list):
            node_sources = set(node_sources)

        # 检查是否来自幽默数据源
        has_humor_source = bool(node_sources & HUMOR_SOURCES)
        if not has_humor_source:
            has_humor_source = any(s.startswith("youtube_") for s in node_sources)
        if not has_humor_source:
            continue

        degree = G.degree(node)
        max_hw = max((d.get("humor_weight", 0) for _, _, d in G.edges(node, data=True)), default=0)

        # 来源权重加分：来自越高优先级的来源，分越高
        source_bonus = 0
        for src in node_sources:
            if src in SOURCE_WEIGHTS:
                source_bonus = max(source_bonus, SOURCE_WEIGHTS[src])
            elif src.startswith("youtube_"):
                source_bonus = max(source_bonus, YOUTUBE_SOURCE_WEIGHT)

        score = max_hw + source_bonus * 2 + min(degree, 50) * 0.01
        candidates.append({"topic": node, "degree": degree, "max_hw": max_hw, "source_bonus": source_bonus, "score": score})

    candidates.sort(key=lambda x: -x["score"])

    # 去掉纯情绪词和太泛的词
    skip = {"高兴", "开心", "难过", "伤心", "痛苦", "快乐", "兴奋", "哭泣",
            "大笑", "害羞", "脸红", "痛哭", "啜泣", "眼泪", "笑容",
            "我们", "他们", "自己", "所有", "非常", "可能", "一样"}
    filtered = [c for c in candidates if c["topic"] not in skip]

    pool = [c["topic"] for c in filtered[:top_k]]

    os.makedirs(os.path.dirname(TOPIC_POOL_PATH), exist_ok=True)
    with open(TOPIC_POOL_PATH, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)

    print(f"话题池已生成: {len(pool)} 个话题 → {TOPIC_POOL_PATH}")
    # 同步到 GCS
    try:
        from src.knowledge.graph import _upload_to_gcs
        _upload_to_gcs(TOPIC_POOL_PATH, "data/topic_pool.json")
    except Exception:
        pass
    return pool


def load_pool():
    if os.path.exists(TOPIC_POOL_PATH):
        with open(TOPIC_POOL_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    # 本地没有，从 GCS 下载
    try:
        from src.knowledge.graph import _sync_from_gcs
        if _sync_from_gcs("data/topic_pool.json", TOPIC_POOL_PATH):
            with open(TOPIC_POOL_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return []


# ==================== 模式 ====================

def run_topic(topic):
    generate_and_score(topic)


def interactive_mode():
    print("=" * 50)
    print("幽默生成器（输入 q 退出）")
    print("=" * 50)
    while True:
        topic = input("\n话题：").strip()
        if topic.lower() in ("q", "quit", "exit", "退出"):
            print("再见！")
            break
        if not topic:
            continue
        run_topic(topic)


def random_mode():
    pool = load_pool()
    if not pool:
        pool = build_topic_pool()
    if not pool:
        print("话题池为空且图谱不可用。")
        return
    topic = random.choice(pool)
    print(f"随机话题：【{topic}】")
    generate_and_score(topic)


def list_mode():
    pool = load_pool()
    if not pool:
        print("话题池为空。用 --refresh 从图谱生成。")
        return
    print(f"话题池（共 {len(pool)} 个）：")
    for i, t in enumerate(pool, 1):
        print(f"  {i:3d}. {t}")


# ==================== 入口 ====================

def main():
    parser = argparse.ArgumentParser(description="幽默生成器")
    parser.add_argument("--topic", type=str, help="指定话题生成笑话")
    parser.add_argument("--random", action="store_true", help="从话题池随机抽取")
    parser.add_argument("--list", action="store_true", help="查看话题池")
    parser.add_argument("--refresh", action="store_true", help="从图谱重新生成话题池")
    args = parser.parse_args()

    if args.refresh:
        build_topic_pool()
    elif args.list:
        list_mode()
    elif args.topic:
        run_topic(args.topic)
    elif args.random:
        random_mode()
    else:
        interactive_mode()


if __name__ == "__main__":
    main()
