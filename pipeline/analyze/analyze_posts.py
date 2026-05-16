"""
analyze_posts.py — 分析"妈的欧洲账本"全部帖子

目标：每篇帖子的分析结果直接反哺生成系统
  - 冲突对 → 进图谱
  - 写作模式 → 生成模板
  - setup/punchline 结构 → 叙事模板

流程：
  1. 从 GCS 读取帖子（按帖子分组）
  2. Gemini 读完整篇帖子，做理论驱动的结构化标注
  3. 输出到 GCS: data/analyzed_posts/{帖子名}.json

断点续传，已处理的跳过。

运行：
  python pipeline/analyze/analyze_posts.py
  python pipeline/analyze/analyze_posts.py --limit 5
"""

import argparse
import json
import os
import re
import time
import logging

from dotenv import load_dotenv

_PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

from google.cloud import storage
from google import genai
from google.genai import errors as genai_errors
from tqdm import tqdm

# ==================== 配置 ====================

PROJECT_ID = "gen-lang-client-0577448366"
BUCKET_NAME = "xhs-humor-data"
RAW_PREFIX = "data/raw_data/妈的欧洲账本/"
OUTPUT_PREFIX = "data/analyzed_posts/"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

gemini_client = genai.Client(api_key=GEMINI_API_KEY)
storage_client = storage.Client(project=PROJECT_ID)
bucket = storage_client.bucket(BUCKET_NAME)

# ==================== Prompt ====================

ANALYZE_PROMPT = """你是一个研究幽默写作的学者。下面是小红书账号"妈的欧洲账本"的一篇完整帖子。
这个账号以克制、反讽、短句断行的风格著称，每篇帖子由多张图片的文案组成。

请读完整篇帖子，然后从6个层面做结构化标注。你的分析会被直接用于训练一个内容生成系统，所以必须具体、可执行，禁止笼统评价。

帖子标题：{title}
图片数量：{num_slides}

原始文本（每张图用 === 分隔）：
{raw_text}

## 标注要求

### 1. clean_slides
把每张图的纯文案提取出来（删除广告/品牌名/产品描述/OCR噪声/外文路标/重复内容/"小红书"水印）。
每张图标注叙事角色：hook / setup / escalation / punchline / resolution / ad

### 2. punchlines — 每个笑点做5层分析

找出所有笑点。每个笑点必须标注以下5个层面：

#### 层1: structure（结构上为什么好笑——骨架）
- mechanism: 从以下选一个：
  expectation_violation / redefinition / precise_absurdity / understated_irony /
  dual_frame / deflation / self_deprecation / escalation
- setup_text: 铺垫原文（建立了什么预期/语境）
- punchline_text: 笑点原文（怎么打破的）
- conflict_pair: [A, B]（冲突的两个概念，直接变图谱三元组）

#### 层2: emotion（情绪上为什么共鸣——传播力）
- pain_point: 戳中了什么普遍痛点？（要具体，如"独自旅行时被各种小事折腾"）
- resonance_type: 经验共鸣 / 身份共鸣 / 情绪共鸣 / 泛化共鸣
- who_relates: 哪类人最有感？（如"自由行旅客"、"北漂打工人"）

#### 层3: event（事件上为什么是这个场景——选材）
- why_this_scene: 为什么选这个具体场景而不是别的？（如"北欧=发达=守时预期，实际不守时，反差比在东南亚等车更大"）
- scene_tags: 场景标签（如["交通", "等待", "异国"]）

#### 层4: language（语言上为什么这么说——措辞）
- key_technique: 用了什么语言技巧？（拟人化/精确数字/省略/口语化/第二人称"你"/反问/...）
- original_vs_plain: 原文 vs 直白说法（展示语言加工的价值）
  例: 原文"这是北欧第一次对你说谎" vs 直白"站牌不准"
- why_better: 为什么原文比直白说法好笑？（一句话，要具体）

#### 层5: rhythm（节奏上为什么在这个位置——编排）
- position: 在第几张图？
- why_here: 为什么放在这个位置效果好？（如"开头第1张图暴击，定下全程受虐基调"）
- pacing: 这个笑点的节奏特征（如"三短句递进→一句收"、"长铺垫→短反转"）

#### 层6: expression（表达智慧——为什么读完觉得"这个人好会说话"）
- delivery: 怎么包装的？（把苦说甜/自降身段/伪客观/留白让读者悟/先自黑再输出观点）
- reader_feeling: 读者读完这句话的感受（如"被逗乐的同时觉得作者很聪明"、"想截图发给朋友"）
- closeness: 作者和读者的距离感（俯视/平视/仰视——"妈的欧洲账本"几乎永远是平视）
- unsaid: 潜台词——没明说但读者感受到的那层意思（如"表面吐槽旅行糟心事，潜台词是一个人在异国的孤独和倔强"。这往往才是读者真正被打动的原因。如果没有潜台词写"无"。）

#### 层7: craft（手艺层——每个词的选择理由，这是最细节最重要的一层）
每个笑点里，找出作者做的关键微观选择。每个选择回答：选了什么词？没选什么？为什么选这个更好笑？

- word_choices: 关键用词选择，每个包含：
  - chose: 作者实际选的词
  - not: 平庸的替代说法
  - why: 为什么选这个更好（要具体到原因，如"精确到可信的数字，信了才失望"、"拟人化让站牌变成角色"、"一个词完成夸张"）

- running_elements: 贯穿全篇反复出现的元素（如"23公斤行李箱反复出现，每次更惨"）。running gag 每次出现必须升级，否则就是无聊的重复。

- things_not_said: 作者刻意没说出来但读者能感受到的东西（如"全篇没提孤独二字，但每张图都在说孤独"）。潜台词不说出来比说出来强10倍。

- micro_rules: 从这个笑点中能提炼出的可复用规则（如"数字选读者会信的范围，信了才会失望"、"比喻选同类但不可能的组合"、"转折越短越好笑——铺垫用整段，反转用一个'但'"）

示例（供你参考这个标注的深度）：

原文："站牌显示还有8分钟，这是北欧第一次对你说谎，这不是最后一次"
word_choices:
  - chose: "8分钟", not: "很久/2小时", why: "8分钟是一个你会信的数字。信了才会失望。2小时你根本不会等"
  - chose: "说谎", not: "不准", why: "拟人化，站牌从道具变成角色，和你有了人际关系"
  - chose: "第一次", not: 无, why: "暗示后面还有更多次，读者产生'还有多惨'的期待，自动往下翻"
running_elements: 无（此为开篇第一个笑点）
things_not_said: "没说在冰天雪地等车有多冷多惨。'等一艘船'的比喻让读者自己脑补绝望程度"
micro_rules: ["数字选读者会信的范围", "拟人化让物体变角色", "用'第一次'制造后续期待"]

### 3. writing_style（整篇的风格特征）
- pattern: 写作模式（反差清单/叙事递进/重新定义体/双坐标系对比/自嘲独白/其他）
- rhythm: 句子节奏特征
- tone: 语气特征
- signature_moves: 招牌动作

### 4. narrative_structure（整篇的叙事编排）
- arc: 叙事弧度（如"铺垫→递进→高潮→收尾"）
- hook_strategy: 开头怎么抓人？
- ending_strategy: 结尾怎么收？
- punchline_density: 笑点密度（每几张图一个笑点？）
- escalation_pattern: 怎么递进的？（越来越惨/越来越荒诞/越来越精确/...）

### 5. topic_tags
话题标签

### 6. ad_removed
被排除的广告/品牌内容

只输出 JSON：
{{
    "clean_slides": [
        {{"slide": 1, "text": "...", "role": "hook"}}
    ],
    "punchlines": [
        {{
            "structure": {{
                "mechanism": "...",
                "setup_text": "...",
                "punchline_text": "...",
                "conflict_pair": ["A", "B"]
            }},
            "emotion": {{
                "pain_point": "...",
                "resonance_type": "...",
                "who_relates": "..."
            }},
            "event": {{
                "why_this_scene": "...",
                "scene_tags": ["...", "..."]
            }},
            "language": {{
                "key_technique": "...",
                "original_vs_plain": ["原文", "直白说法"],
                "why_better": "..."
            }},
            "rhythm": {{
                "position": 1,
                "why_here": "...",
                "pacing": "..."
            }},
            "expression": {{
                "delivery": "...",
                "reader_feeling": "...",
                "closeness": "平视",
                "unsaid": "..."
            }},
            "craft": {{
                "word_choices": [
                    {{"chose": "...", "not": "...", "why": "..."}}
                ],
                "running_elements": ["..."],
                "things_not_said": ["..."],
                "micro_rules": ["..."]
            }}
        }}
    ],
    "writing_style": {{
        "pattern": "...",
        "rhythm": "...",
        "tone": "...",
        "signature_moves": ["..."]
    }},
    "narrative_structure": {{
        "arc": "...",
        "hook_strategy": "...",
        "ending_strategy": "...",
        "punchline_density": "...",
        "escalation_pattern": "..."
    }},
    "topic_tags": ["..."],
    "ad_removed": "..."
}}"""


# ==================== 加载帖子 ====================

def load_posts():
    blobs = list(bucket.list_blobs(prefix=RAW_PREFIX))
    txt_blobs = [b for b in blobs if b.name.endswith('.txt')]

    posts = {}
    for b in txt_blobs:
        name = b.name.split('/')[-1]
        post_name = re.sub(r'_\d+\.jpg\.txt$', '', name)
        posts.setdefault(post_name, []).append(b)

    for post_name in posts:
        posts[post_name].sort(
            key=lambda b: int(re.search(r'_(\d+)\.jpg', b.name).group(1))
            if re.search(r'_(\d+)\.jpg', b.name) else 0
        )
    return posts


def read_post(blobs_list):
    texts = []
    for b in blobs_list:
        text = b.download_as_text(encoding='utf-8').strip()
        texts.append(text if text else "(空)")
    return texts


# ==================== JSON 修复 ====================

def _parse_json_lenient(raw, title=""):
    """尝试解析 JSON，失败则尝试修复常见问题"""
    # 1. 直接解析
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 2. 修复常见问题
    fixed = raw
    # 移除尾部多余逗号 (,] 或 ,})
    fixed = re.sub(r',\s*([}\]])', r'\1', fixed)
    # 修复未转义的换行符在字符串内
    # 修复中文引号
    fixed = fixed.replace('\u201c', '\\"').replace('\u201d', '\\"')
    fixed = fixed.replace('\u2018', "\\'").replace('\u2019', "\\'")

    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # 3. 用 Gemini 修复
    try:
        logging.info(f"尝试用 Gemini 修复 JSON: {title}")
        fix_response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"以下 JSON 有语法错误，请修复并只输出正确的 JSON，不要任何其他内容：\n\n{raw[:8000]}",
        )
        fix_text = fix_response.text.strip()
        if fix_text.startswith("```"):
            fix_text = fix_text.split("```")[1]
            if fix_text.startswith("json"):
                fix_text = fix_text[4:]
        return json.loads(fix_text)
    except Exception as e:
        logging.warning(f"JSON 修复也失败 {title}: {e}")
        return None


# ==================== 分析 ====================

def analyze_post(title, slide_texts, max_retries=3):
    raw_text = "\n===\n".join(slide_texts)
    if len(raw_text) > 10000:
        raw_text = raw_text[:10000] + "\n...(截断)"

    prompt = ANALYZE_PROMPT.format(
        title=title,
        num_slides=len(slide_texts),
        raw_text=raw_text,
    )

    for attempt in range(max_retries):
        try:
            response = gemini_client.models.generate_content(
                model="gemini-2.5-pro",
                contents=prompt,
            )
            if not response.text:
                return None

            raw = response.text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]

            result = _parse_json_lenient(raw, title)
            if result is None:
                continue  # retry
            result["title"] = title
            result["num_slides_original"] = len(slide_texts)
            return result

        except json.JSONDecodeError as e:
            logging.warning(f"JSON 解析失败 {title}: {e}")
            continue  # retry instead of giving up
        except genai_errors.ClientError as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                wait = 15 * (2 ** attempt)
                logging.warning(f"限流，等待 {wait}s...")
                time.sleep(wait)
            else:
                logging.error(f"Gemini 错误: {e}")
                return None
        except Exception as e:
            logging.error(f"分析失败 {title}: {e}")
            return None

    return None


# ==================== 主流程 ====================

def main():
    parser = argparse.ArgumentParser(description="分析妈的欧洲账本帖子")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 篇（0=全部）")
    args = parser.parse_args()

    print("加载帖子...")
    posts = load_posts()
    print(f"共 {len(posts)} 篇帖子")

    # 断点续传
    done_blobs = list(bucket.list_blobs(prefix=OUTPUT_PREFIX))
    done_names = set(
        b.name.split('/')[-1].replace('.json', '')
        for b in done_blobs if b.name.endswith('.json')
    )
    pending = {k: v for k, v in posts.items() if k not in done_names}
    if args.limit > 0:
        pending = dict(list(pending.items())[:args.limit])

    print(f"已完成: {len(done_names)}, 待处理: {len(pending)}")

    stats = {"mechanisms": {}, "resonance_types": {}, "patterns": {},
             "techniques": {}, "deliveries": {}, "total_punchlines": 0}

    for post_name, blobs_list in tqdm(pending.items(), desc="分析", unit="篇"):
        slide_texts = read_post(blobs_list)
        result = analyze_post(post_name, slide_texts)
        if result is None:
            continue

        # 上传到 GCS
        output_path = f"{OUTPUT_PREFIX}{post_name}.json"
        bucket.blob(output_path).upload_from_string(
            json.dumps(result, ensure_ascii=False, indent=2),
            content_type="application/json; charset=utf-8",
        )

        # 统计 5 层
        punchlines = result.get("punchlines", [])
        stats["total_punchlines"] += len(punchlines)
        for p in punchlines:
            s = p.get("structure", {})
            e = p.get("emotion", {})
            l = p.get("language", {})
            x = p.get("expression", {})
            m = s.get("mechanism", "unknown")
            stats["mechanisms"][m] = stats["mechanisms"].get(m, 0) + 1
            rt = e.get("resonance_type", "unknown")
            stats["resonance_types"][rt] = stats["resonance_types"].get(rt, 0) + 1
            kt = l.get("key_technique", "unknown")
            stats["techniques"][kt] = stats["techniques"].get(kt, 0) + 1
            dv = x.get("delivery", "unknown")
            stats["deliveries"][dv] = stats["deliveries"].get(dv, 0) + 1

        pattern = result.get("writing_style", {}).get("pattern", "unknown")
        stats["patterns"][pattern] = stats["patterns"].get(pattern, 0) + 1

        # 打印摘要
        tqdm.write(f"\n  [{pattern}] {post_name}")
        for p in punchlines[:2]:
            s = p.get("structure", {})
            e = p.get("emotion", {})
            l = p.get("language", {})
            x = p.get("expression", {})
            tqdm.write(f"    笑点: {s.get('punchline_text', '')[:50]}")
            tqdm.write(f"    结构: {s.get('mechanism', '?')} | 冲突: {s.get('conflict_pair', [])}")
            tqdm.write(f"    情绪: {e.get('pain_point', '')[:50]}")
            tqdm.write(f"    语言: {l.get('key_technique', '?')} | {l.get('why_better', '')[:50]}")
            tqdm.write(f"    表达: {x.get('delivery', '?')} | 潜台词: {x.get('unsaid', '')[:50]}")

        time.sleep(1)

    # 汇总
    print(f"\n{'='*50}")
    print(f"分析完成")
    print(f"总笑点数: {stats['total_punchlines']}")
    print(f"\n幽默机制分布 (结构层):")
    for m, c in sorted(stats["mechanisms"].items(), key=lambda x: -x[1]):
        print(f"  {m}: {c}")
    print(f"\n共鸣类型分布 (情绪层):")
    for r, c in sorted(stats["resonance_types"].items(), key=lambda x: -x[1]):
        print(f"  {r}: {c}")
    print(f"\n语言技巧分布 (语言层):")
    for t, c in sorted(stats["techniques"].items(), key=lambda x: -x[1]):
        print(f"  {t}: {c}")
    print(f"\n表达方式分布 (表达层):")
    for d, c in sorted(stats["deliveries"].items(), key=lambda x: -x[1]):
        print(f"  {d}: {c}")
    print(f"\n写作模式分布:")
    for p, c in sorted(stats["patterns"].items(), key=lambda x: -x[1]):
        print(f"  {p}: {c}")


if __name__ == "__main__":
    main()
