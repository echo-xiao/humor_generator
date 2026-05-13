"""
caption_generator.py
生成"妈的欧洲账本"风格的发疯文学文案。

风格特征：
- 克制语气 + 荒诞反差
- 短、狠、留白，不解释
- 期待 vs 现实 / 表面 vs 本质 / 荒诞因果
- 1-3句，像冷静陈述一个荒诞事实
"""

import os
import sys
import random

from .gemini_client import call_gemini

# ==================== Few-shot 示例（从真实数据提取）====================

FEW_SHOT_EXAMPLES = """以下是真实的"妈的欧洲账本"风格文案，请仔细体会句式和语气：

【话题：布达佩斯旅行】
想死，但还想去布达佩斯。

【话题：欧洲地铁】
欧洲最美的地铁站，伴随着欧洲最严格的查票系统。

【话题：30岁留学】
30岁留学，期待是大女主剧快乐结局，现实是高中生小团体政治。

【话题：白人饭】
白人饭的宗旨是展现食材本身的风味，实际上是展现食材本身的难吃。

【话题：法国博物馆】
去法国博物馆，导致回到26岁以下。
和妈妈通电话，导致回到18岁以下。
抢棉花糖，导致回到8岁。

【话题：出国生活】
出国的目的是不想出嫁。
生活给的柠檬，现代处理方式是在小红书摆摊变现。

【话题：懒惰】
懒惰拖延不想负责，被认为是松弛感。

【话题：压岁钱】
妈妈帮忙存着，等于压岁钱归零。

【话题：护肤品】
花钱买护肤品，因公司报销，等于增加收入1330元。

【话题：留学课堂】
留学课堂等同于小学课堂。
学生和老师一样，都不想上班。"""


# ==================== Prompt ====================

CAPTION_PROMPT = """{few_shot}

---

现在请你模仿以上风格，为以下话题/场景生成 {n} 条文案。

话题/场景：{topic}
补充信息（可选）：{context}

要求：
- 每条1-3句，克制、冷静，不用感叹号堆砌情绪
- 用反差、荒诞因果、意外等价制造张力
- 像在冷静陈述一个荒诞事实
- 不要解释，不要加标题，不要加序号
- 直接输出文案，每条之间用空行隔开
- 可以用"期待是X，现实是Y" / "A，导致B" / "A等同于B" / "A，实际上是B" 等句式

文案："""


# ==================== 生成 ====================

def generate_captions(topic, context="", n=5):
    """
    生成妈的欧洲账本风格文案。

    Args:
        topic: 话题/场景，如"在巴黎迷路"、"租房押金"
        context: 补充信息，如具体细节，可为空
        n: 生成条数

    Returns:
        list[str] 文案列表
    """
    prompt = CAPTION_PROMPT.format(
        few_shot=FEW_SHOT_EXAMPLES,
        topic=topic,
        context=context if context else "无",
        n=n,
    )

    raw = call_gemini(prompt)
    if not raw:
        return []

    # 按空行分割
    captions = [c.strip() for c in raw.strip().split("\n\n") if c.strip()]
    return captions


def interactive():
    """交互模式：持续输入话题，生成文案"""
    print("=" * 50)
    print("妈的欧洲账本风格文案生成器")
    print("输入话题，按回车生成；输入 q 退出")
    print("=" * 50)

    while True:
        topic = input("\n话题/场景：").strip()
        if topic.lower() == "q":
            break
        if not topic:
            continue

        context = input("补充信息（可选，直接回车跳过）：").strip()
        n = input("生成几条（默认5）：").strip()
        n = int(n) if n.isdigit() else 5

        print("\n生成中...\n")
        captions = generate_captions(topic, context, n)

        print("=" * 50)
        for i, caption in enumerate(captions, 1):
            print(f"[{i}]")
            print(caption)
            print()


# ==================== 主程序 ====================

def main():
    # 演示几个话题
    test_cases = [
        ("在巴黎迷路", "地图导航带我绕了三圈，最后发现目的地就在出发点旁边"),
        ("租房押金", ""),
        ("坐红眼航班", "为了省钱订了凌晨三点的航班"),
        ("在国外超市买菜", ""),
    ]

    for topic, context in test_cases:
        print(f"\n{'='*50}")
        print(f"话题：{topic}")
        if context:
            print(f"背景：{context}")
        print()

        captions = generate_captions(topic, context, n=3)
        for caption in captions:
            print(caption)
            print()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--interactive":
        interactive()
    else:
        main()
