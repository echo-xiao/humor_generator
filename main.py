"""
main.py
幽默生成器入口。

用法：
  python main.py --topic 打工人        # 生成笑话，并把话题存入话题池
  python main.py --random              # 从话题池随机抽一个生成
  python main.py --list                # 查看话题池
  python main.py                       # 交互模式
"""

import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="google.auth")
warnings.filterwarnings("ignore", category=UserWarning, module="jieba")

import argparse
import sys
import os
import json
import random

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "humor_generator"))

from critic import run as generate_and_score

TOPIC_POOL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "topic_pool.json")


# ==================== 话题池 ====================

def load_pool():
    if os.path.exists(TOPIC_POOL_PATH):
        with open(TOPIC_POOL_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_topic(topic):
    pool = load_pool()
    if topic not in pool:
        pool.append(topic)
        os.makedirs(os.path.dirname(TOPIC_POOL_PATH), exist_ok=True)
        with open(TOPIC_POOL_PATH, "w", encoding="utf-8") as f:
            json.dump(pool, f, ensure_ascii=False, indent=2)
        print(f"  已加入话题池（共 {len(pool)} 个话题）")


def run_topic(topic):
    save_topic(topic)
    generate_and_score(topic)


# ==================== 模式 ====================

def interactive_mode():
    print("=" * 50)
    print("幽默生成器（输入 q 退出，输入的话题自动存入话题池）")
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
        print("话题池为空，请先用 --topic 添加话题。")
        return
    topic = random.choice(pool)
    print(f"随机话题：【{topic}】")
    generate_and_score(topic)


def list_mode():
    pool = load_pool()
    if not pool:
        print("话题池为空。")
        return
    print(f"话题池（共 {len(pool)} 个）：")
    for i, t in enumerate(pool, 1):
        print(f"  {i}. {t}")


# ==================== 入口 ====================

def main():
    parser = argparse.ArgumentParser(description="幽默生成器")
    parser.add_argument("--topic", type=str, help="输入话题，自动存入话题池")
    parser.add_argument("--random", action="store_true", help="从话题池随机抽取话题生成")
    parser.add_argument("--list", action="store_true", help="查看话题池")
    args = parser.parse_args()

    if args.list:
        list_mode()
    elif args.topic:
        run_topic(args.topic)
    elif args.random or load_pool():
        random_mode()
    else:
        interactive_mode()


if __name__ == "__main__":
    main()
