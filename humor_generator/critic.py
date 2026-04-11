"""
critic.py
Self-RAG Critics + 对抗精炼：对笑话候选打分，选最优，再精炼一轮。

评分维度（各 1-5 分）：
  - 幽默度：是否真的好笑，有没有反转/意外/荒诞感
  - 相关性：是否切题，跟话题/Humor Slot 是否贴合
  - 自然度：语言是否口语化，像真实脱口秀

流程：
  生成4路径候选 → 评分排序 → 最优候选 → refine精炼 → 输出
"""

import json
import os
import sys
import time
from google import genai
from google.genai import errors as genai_errors
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from joke_generator import generate_jokes

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = "gemini-2.5-pro"

client = genai.Client(api_key=GEMINI_API_KEY)

# ==================== 评分 Prompt ====================

REFINE_PROMPT = """你是一个脱口秀编剧，请根据评委的批评，把这个笑话改得更好笑。

话题：{topic}
原始笑话：{joke}
评委批评：{comment}
当前得分：幽默={humor}/相关={relevance}/自然={naturalness}（满分各5分）

改写要求：
- 针对批评的弱点重点改进
- 保留原笑话的核心冲突点，不要换话题
- 让笑点更集中、铺垫更短、结尾更有力
- 2-3句，口语化，脱口秀风格
- 直接输出改写后的笑话，不要解释

改写后的笑话："""

SCORE_PROMPT = """你是一个脱口秀评委，请对以下笑话打分。

话题：{topic}
笑话：{joke}

请从三个维度各给 1-5 分，然后给出总分（三项之和）：
- 幽默度（1-5）：是否好笑，有没有反转/意外/荒诞感
- 相关性（1-5）：是否切题，跟话题贴合程度
- 自然度（1-5）：语言是否口语化，像真实脱口秀

只输出 JSON，格式如下：
{{"humor": 分数, "relevance": 分数, "naturalness": 分数, "total": 总分, "comment": "一句点评"}}"""


def call_gemini(prompt, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(model=MODEL, contents=prompt)
            return response.text.strip() if response.text else ""
        except genai_errors.ClientError as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e) or "503" in str(e) or "UNAVAILABLE" in str(e):
                wait = 15 * (2 ** attempt)
                print(f"  限流/过载，等待 {wait}s...")
                time.sleep(wait)
            else:
                print(f"  API 错误: {e}")
                return ""
        except Exception as e:
            print(f"  错误: {e}")
            return ""
    return ""


def score_joke(topic, joke):
    """对单个笑话打分，返回 dict"""
    raw = call_gemini(SCORE_PROMPT.format(topic=topic, joke=joke))
    if not raw:
        return {"humor": 0, "relevance": 0, "naturalness": 0, "total": 0, "comment": "评分失败"}

    # 清理 markdown 代码块
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        return {"humor": 0, "relevance": 0, "naturalness": 0, "total": 0, "comment": raw[:50]}


def evaluate(topic, candidates, verbose=True):
    """
    对所有候选打分，返回排序结果。

    candidates: generate_jokes() 的返回值
    返回: 带 scores 字段的候选列表，按 total 降序
    """
    if not candidates:
        return []

    if verbose:
        print(f"\n{'='*50}")
        print(f"评分中（共 {len(candidates)} 个候选）...")

    for c in candidates:
        if verbose:
            print(f"\n  路径{c['path']}：{c['joke'][:40]}...")
        scores = score_joke(topic, c["joke"])
        c["scores"] = scores
        if verbose:
            print(f"  幽默={scores.get('humor')}/相关={scores.get('relevance')}/自然={scores.get('naturalness')} → 总分={scores.get('total')}  {scores.get('comment', '')}")

    # 评分失败的候选排到最后，但不丢弃
    scored = [c for c in candidates if c["scores"].get("total", 0) > 0]
    unscored = [c for c in candidates if c["scores"].get("total", 0) == 0]
    ranked = sorted(scored, key=lambda x: x["scores"].get("total", 0), reverse=True) + unscored
    return ranked


def refine(topic, joke, scores, max_rounds=2):
    """
    对抗精炼：用评委批评驱动生成器重写，最多迭代 max_rounds 轮。

    只有当分数有提升才接受新版本（保守策略，避免越改越差）。
    返回最终笑话 dict: {"joke": str, "scores": dict, "rounds": int}
    """
    current_joke = joke
    current_scores = scores
    rounds = 0

    for _ in range(max_rounds):
        comment = current_scores.get("comment", "")
        total = current_scores.get("total", 0)

        # 总分已经很高（≥13/15），不必再改
        if total >= 13:
            break

        print(f"\n  [精炼第{rounds+1}轮] 当前总分={total}，批评：{comment}")

        refined = call_gemini(REFINE_PROMPT.format(
            topic=topic,
            joke=current_joke,
            comment=comment,
            humor=current_scores.get("humor", 0),
            relevance=current_scores.get("relevance", 0),
            naturalness=current_scores.get("naturalness", 0),
        ))

        if not refined:
            break

        new_scores = score_joke(topic, refined)
        new_total = new_scores.get("total", 0)

        print(f"  精炼后：总分={new_total}  {new_scores.get('comment', '')}")
        print(f"  {refined}")

        # 只有分数提升才接受
        if new_total > total:
            current_joke = refined
            current_scores = new_scores
            rounds += 1
        else:
            print("  分数未提升，保留原版")
            break

    return {"joke": current_joke, "scores": current_scores, "rounds": rounds}


def run(topic):
    """端到端：生成 + 评分 + 精炼 + 输出最优"""
    candidates = generate_jokes(topic)

    if not candidates:
        print("没有生成任何候选，退出。")
        return None

    ranked = evaluate(topic, candidates)

    scored = [c for c in ranked if c["scores"].get("total", 0) > 0]

    print(f"\n{'='*50}")
    if not scored:
        print("评分服务不可用，展示所有生成结果：")
        for c in ranked:
            print(f"\n  [路径{c['path']}] {c['joke']}")
        return ranked[0]

    best = scored[0]
    print(f"初始最优（路径{best['path']}，总分={best['scores'].get('total')}）：")
    print(f"\n  {best['joke']}")
    print(f"\n  点评：{best['scores'].get('comment', '')}")

    # 精炼
    print(f"\n{'='*50}")
    print("对抗精炼中...")
    result = refine(topic, best["joke"], best["scores"])

    print(f"\n{'='*50}")
    if result["rounds"] > 0:
        print(f"精炼后（{result['rounds']}轮，总分={result['scores'].get('total')}）：")
    else:
        print(f"精炼未提升，最终结果（总分={result['scores'].get('total')}）：")
    print(f"\n  {result['joke']}")
    print(f"\n  点评：{result['scores'].get('comment', '')}")

    best["joke"] = result["joke"]
    best["scores"] = result["scores"]
    return best


# ==================== 主程序 ====================

def main():
    topics = ["结婚", "贫穷"]
    for topic in topics:
        run(topic)
        print()


if __name__ == "__main__":
    main()
