import json
import logging
import re
import time
from google.cloud import storage
from google import genai
from google.genai import errors as genai_errors
import os
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()

# ==================== 配置 ====================
PROJECT_ID = "gen-lang-client-0577448366"
BUCKET_NAME = "xhs-humor-data"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

SOURCES = {
    "脱口秀大咖": "data/raw_data/脱口秀大咖/",
    "脱口秀集锦": "data/raw_data/脱口秀集锦/",
}

# 图片笔记类 source，需按帖子分组合并
GROUPED_SOURCES = {
    "妈的欧洲账本": "data/raw_data/妈的欧洲账本/",
}

OUTPUT_PREFIX = "data/input_data/"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ==================== 初始化 ====================
client = genai.Client(api_key=GEMINI_API_KEY)
storage_client = storage.Client(project=PROJECT_ID)
bucket = storage_client.bucket(BUCKET_NAME)

# ==================== 提取三元组 ====================
PROMPT_TEMPLATE = """你是一个知识图谱专家，专门从幽默文本中提取逻辑冲突三元组。

请从以下文本中提取三元组，格式严格为 JSON 数组：
[
  {{"subject": "主体", "relation": "关系", "object": "客体"}},
  ...
]

重点提取：
1. 社会角色之间的对立关系（e.g. 程序员 vs CEO）
2. 期待与现实的冲突（e.g. 哲学家 导致 贫穷）
3. 荒诞的因果关系（e.g. 结婚 目的是 去欧洲旅行）
4. 概念的反转（e.g. 自由 等于 贫穷）

只输出 JSON 数组，不要其他文字。

文本：
{text}"""

def extract_triples_with_retry(text, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-pro",
                contents=PROMPT_TEMPLATE.format(text=text[:3000])
            )
            if not response.text:
                return []
            raw = response.text.strip()

            # 清理 markdown 代码块
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]

            triples = json.loads(raw)
            return triples

        except json.JSONDecodeError:
            logging.warning("JSON 解析失败，跳过")
            return []
        except genai_errors.ClientError as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                wait = 10 * (2 ** attempt)
                logging.warning(f"限流，等待 {wait} 秒...")
                time.sleep(wait)
            elif "503" in str(e) or "UNAVAILABLE" in str(e):
                wait = 10 * (2 ** attempt)
                logging.warning(f"服务过载，等待 {wait} 秒...")
                time.sleep(wait)
            else:
                logging.error(f"错误: {e}")
                return []
        except Exception as e:
            logging.error(f"错误: {e}")
            return []
    return []

# ==================== 主流程 ====================
def process_source(source_name, source_prefix):
    output_filename = f"graphrag_ready_{source_name}.jsonl"
    output_gcs_path = OUTPUT_PREFIX + output_filename
    checkpoint_prefix = f"data/input_data/checkpoints/{source_name}/"

    logging.info(f"\n{'='*50}")
    logging.info(f"开始处理: {source_name}")

    blobs = list(bucket.list_blobs(prefix=source_prefix))
    txt_blobs = [b for b in blobs if b.name.endswith(".txt")]
    logging.info(f"找到 {len(txt_blobs)} 个 txt 文件")

    # 断点续传
    done_files = set(
        b.name.split("/")[-1].replace(".jsonl", "")
        for b in bucket.list_blobs(prefix=checkpoint_prefix)
    )
    pending = [b for b in txt_blobs if b.name.split("/")[-1] not in done_files]
    logging.info(f"已完成 {len(done_files)} 个，待处理 {len(pending)} 个")

    all_triples = []

    for blob in tqdm(pending, desc=f"{source_name}", unit="文件"):
        try:
            text = blob.download_as_text(encoding="utf-8")
            if len(text.strip()) < 50:
                continue

            triples = extract_triples_with_retry(text)

            for triple in triples:
                triple["source_file"] = blob.name
                triple["source_type"] = source_name
            all_triples.extend(triples)

            # 打印最新提取的三元组
            if triples:
                print(f"\n  [{blob.name.split('/')[-1]}] 提取 {len(triples)} 条：")
                for t in triples[:3]:
                    print(f"    ({t['subject']}, {t['relation']}, {t['object']})")

            # 立即上传 checkpoint
            file_key = blob.name.split("/")[-1]
            tmp_path = f"/tmp/{file_key}.jsonl"
            with open(tmp_path, "w", encoding="utf-8") as f:
                for t in triples:
                    f.write(json.dumps(t, ensure_ascii=False) + "\n")
            bucket.blob(checkpoint_prefix + file_key + ".jsonl").upload_from_filename(tmp_path)

            time.sleep(0.5)

        except Exception as e:
            logging.error(f"处理失败 {blob.name}: {e}")

    # 合并生成最终文件
    logging.info(f"合并结果，生成 {output_filename}...")
    all_checkpoint_blobs = list(bucket.list_blobs(prefix=checkpoint_prefix))
    merged = []
    for cb in all_checkpoint_blobs:
        content = cb.download_as_text(encoding="utf-8")
        for line in content.strip().split("\n"):
            if line:
                merged.append(line)

    tmp_output = f"/tmp/{output_filename}"
    with open(tmp_output, "w", encoding="utf-8") as f:
        f.write("\n".join(merged))

    bucket.blob(output_gcs_path).upload_from_filename(tmp_output)
    logging.info(f"✅ {source_name} 完成！共 {len(merged)} 条三元组")

import re

def get_post_name(filename):
    """从文件名提取帖子名，去掉 _N.jpg.txt 后缀"""
    return re.sub(r'_\d+\.jpg\.txt$', '', filename)

def process_grouped_source(source_name, source_prefix):
    """按帖子分组合并图片文字，再提取三元组"""
    output_filename = f"graphrag_ready_{source_name}.jsonl"
    output_gcs_path = OUTPUT_PREFIX + output_filename
    checkpoint_prefix = f"data/input_data/checkpoints/{source_name}/"

    logging.info(f"\n{'='*50}")
    logging.info(f"开始处理（分组模式）: {source_name}")

    blobs = list(bucket.list_blobs(prefix=source_prefix))
    txt_blobs = [b for b in blobs if b.name.endswith(".txt")]

    # 按帖子名分组
    posts = {}
    for blob in txt_blobs:
        filename = blob.name.split("/")[-1]
        post_name = get_post_name(filename)
        if post_name not in posts:
            posts[post_name] = []
        posts[post_name].append(blob)

    # 每组按数字排序
    for post_name in posts:
        posts[post_name].sort(
            key=lambda b: int(re.search(r'_(\d+)\.jpg\.txt$', b.name.split("/")[-1]).group(1))
        )

    logging.info(f"找到 {len(posts)} 篇帖子")

    # 断点续传
    done_posts = set(
        b.name.split("/")[-1].replace(".jsonl", "")
        for b in bucket.list_blobs(prefix=checkpoint_prefix)
    )
    pending_posts = {k: v for k, v in posts.items() if k not in done_posts}
    logging.info(f"已完成 {len(done_posts)} 篇，待处理 {len(pending_posts)} 篇")

    for post_name, blobs_list in tqdm(pending_posts.items(), desc=source_name, unit="篇"):
        try:
            # 合并所有图片文字
            parts = []
            for blob in blobs_list:
                text = blob.download_as_text(encoding="utf-8").strip()
                if text:
                    parts.append(text)
            merged_text = "\n".join(parts)

            if len(merged_text) < 20:
                # 内容太少，写空 checkpoint 跳过
                bucket.blob(checkpoint_prefix + post_name + ".jsonl").upload_from_string("")
                continue

            triples = extract_triples_with_retry(merged_text)

            for triple in triples:
                triple["source_file"] = blobs_list[0].name
                triple["source_type"] = source_name
                triple["post_name"] = post_name

            if triples:
                print(f"\n  [{post_name}] 提取 {len(triples)} 条：")
                for t in triples[:3]:
                    print(f"    ({t['subject']}, {t['relation']}, {t['object']})")

            # 立即上传 checkpoint
            tmp_path = f"/tmp/{post_name}.jsonl"
            with open(tmp_path, "w", encoding="utf-8") as f:
                for t in triples:
                    f.write(json.dumps(t, ensure_ascii=False) + "\n")
            bucket.blob(checkpoint_prefix + post_name + ".jsonl").upload_from_filename(tmp_path)

            time.sleep(0.5)

        except Exception as e:
            logging.error(f"处理失败 {post_name}: {e}")

    # 合并生成最终文件
    logging.info(f"合并结果，生成 {output_filename}...")
    all_checkpoint_blobs = list(bucket.list_blobs(prefix=checkpoint_prefix))
    merged = []
    for cb in all_checkpoint_blobs:
        content = cb.download_as_text(encoding="utf-8")
        for line in content.strip().split("\n"):
            if line:
                merged.append(line)

    tmp_output = f"/tmp/{output_filename}"
    with open(tmp_output, "w", encoding="utf-8") as f:
        f.write("\n".join(merged))

    bucket.blob(output_gcs_path).upload_from_filename(tmp_output)
    logging.info(f"✅ {source_name} 完成！共 {len(merged)} 条三元组")

def main():
    # 妈的欧洲账本（图片分组模式）
    # for source_name, source_prefix in GROUPED_SOURCES.items():
    #     process_grouped_source(source_name, source_prefix)

    # 脱口秀大咖 & 脱口秀集锦（单文件模式）
    for source_name, source_prefix in SOURCES.items():
        process_source(source_name, source_prefix)

    print("\n全部完成！生成文件：")
    for source_name in SOURCES:
        print(f"  gs://{BUCKET_NAME}/{OUTPUT_PREFIX}graphrag_ready_{source_name}.jsonl")

if __name__ == "__main__":
    main()
