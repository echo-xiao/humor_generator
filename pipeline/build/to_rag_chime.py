"""
to_rag_chime.py — Chime 梗库转 RAG 格式

流程：
  GCS original_data/chime_full.json（1458条）
    → 格式化为 page_content（百科词条文本）+ metadata
    → 写出本地 chime_rag_ready.jsonl

输出字段：
  page_content: "网络热梗：{meme}\n含义解析：{meaning}\n起源/出处：{origin}\n使用例句：..."
  metadata:     {source, meme_type, is_offensive}

过滤：跳过无 meaning 字段的条目
运行：python generate_input_data/build/to_rag_chime.py
"""

import json
import logging
from typing import Dict, Any
from google.cloud import storage

# ================= 配置区域 =================
BUCKET_NAME = "xhs-humor-data"
SOURCE_BLOB_NAME = "chime_full.json"
OUTPUT_FILENAME = "chime_rag_ready.jsonl"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def format_chime_item(item: Dict[str, Any]) -> Dict[str, Any]:
    meme = item.get('meme', '未知梗')
    meaning = item.get('meaning', '暂无含义解释')
    origin = item.get('origin', '未知来源')

    examples = item.get('example', [])
    if isinstance(examples, list):
        examples_str = "\n".join([f"- {ex}" for ex in examples])
    elif isinstance(examples, str):
        examples_str = f"- {examples}"
    else:
        examples_str = "暂无例句"

    text_content = (
        f"网络热梗：{meme}\n"
        f"含义解析：{meaning}\n"
        f"起源/出处：{origin}\n"
        f"使用例句：\n{examples_str}"
    )

    metadata = {
        "source": "chime_dataset",
        "meme_type": item.get('type_cn', '其他'),
        "is_offensive": item.get('offense', False)
    }

    return {
        "page_content": text_content,
        "metadata": metadata
    }


def load_from_gcs(bucket_name, blob_name):
    logging.info(f"正在连接 GCS Bucket: {bucket_name} ...")
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    logging.info(f"正在下载文件: {blob_name} ...")
    content = blob.download_as_text(encoding='utf-8')
    return json.loads(content)


def main():
    raw_data = load_from_gcs(BUCKET_NAME, SOURCE_BLOB_NAME)
    logging.info(f"下载成功，共获取 {len(raw_data)} 条原始数据")

    logging.info("开始进行数据清洗与格式化...")
    processed_docs = []
    for item in raw_data:
        if not item.get('meaning'):
            continue
        processed_docs.append(format_chime_item(item))

    logging.info(f"处理完成，正在写入输出文件: {OUTPUT_FILENAME}")
    with open(OUTPUT_FILENAME, 'w', encoding='utf-8') as f:
        for doc in processed_docs:
            f.write(json.dumps(doc, ensure_ascii=False) + '\n')

    logging.info("转换成功！文件已准备好用于 RAG 向量化。")

    print("\n--- 转换结果预览 (第一条) ---")
    print(json.dumps(processed_docs[0], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
