"""
to_rag_meme.py — 十万个梗库 txt 转 RAG 格式

流程：
  本地 data/raw_data/十万个梗库/**/*.txt（extract_memes.py 的输出）
    → 正则解析【梗名称】【梗来源】【梗含义】【用法建议】锚点
    → 格式化为 page_content + metadata
    → 写出本地 data/input_data/meme_rag_ready.jsonl（517条）

解析策略（V6）：
  - 删除 Markdown 加粗（**）再匹配
  - 只认【关键词】锚点，忽略数字序号
  - 梗名提取失败时兜底用文件名

运行：python generate_input_data/build/to_rag_meme.py
"""

import json
import re
import os
import logging

# ================= 配置区域 =================
INPUT_DIR = "data/raw_data/十万个梗库/"
OUTPUT_FILENAME = "data/input_data/meme_rag_ready.jsonl"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')


def parse_txt_content_universal(text):
    """
    V6 解析逻辑：抛弃数字序号，只认【关键词】锚点
    """
    data = {}

    # 预处理：删掉 Markdown 加粗符号(**)
    clean_text = text.replace("**", "")

    meme_match = re.search(r"【(?:梗名称|名称)】[:：]?\s*(.*?)\s*(?=【(?:梗来源|来源)|$)", clean_text, re.DOTALL)
    data['meme'] = meme_match.group(1).strip() if meme_match else "暂无"

    origin_match = re.search(r"【(?:梗来源|来源)】[:：]?\s*(.*?)\s*(?=【(?:梗含义|含义)|$)", clean_text, re.DOTALL)
    data['origin'] = origin_match.group(1).strip() if origin_match else "暂无"

    meaning_match = re.search(r"【(?:梗含义|含义)】[:：]?\s*(.*?)\s*(?=【(?:用法建议|用法)|$)", clean_text, re.DOTALL)
    data['meaning'] = meaning_match.group(1).strip() if meaning_match else "暂无"

    usage_match = re.search(r"【(?:用法建议|用法)】[:：]?\s*(.*?)\s*$", clean_text, re.DOTALL)
    data['usage'] = usage_match.group(1).strip() if usage_match else "暂无"

    return data


def main():
    output_dir = os.path.dirname(OUTPUT_FILENAME)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    print(f"开始 V6 强力扫描: {INPUT_DIR}")

    processed_docs = []
    file_count = 0
    success_count = 0

    for root, dirs, files in os.walk(INPUT_DIR):
        for file in files:
            if file.endswith(".txt"):
                file_count += 1
                file_path = os.path.join(root, file)

                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()

                    parsed_data = parse_txt_content_universal(content)

                    if parsed_data['meme'] != "暂无" or parsed_data['meaning'] != "暂无":
                        if parsed_data['meme'] == "暂无":
                            clean_name = file.replace(".txt", "").replace("_1", "").split("#")[0].strip()
                            parsed_data['meme'] = clean_name

                        doc = {
                            "page_content": (
                                f"网络热梗：{parsed_data['meme']}\n"
                                f"含义解析：{parsed_data['meaning']}\n"
                                f"起源/出处：{parsed_data['origin']}\n"
                                f"用法建议/例句：\n{parsed_data['usage']}"
                            ),
                            "metadata": {
                                "meme_name": parsed_data['meme'],
                                "source": "xhs_100k_memes",
                                "original_file": file_path,
                                "is_offensive": False
                            }
                        }
                        processed_docs.append(doc)
                        success_count += 1

                    if file_count % 500 == 0:
                        print(f"已扫描 {file_count} 个，成功 {success_count} 个...", end='\r')

                except Exception:
                    pass

    print("\n" + "=" * 40)
    print(f"最终战报:")
    print(f"   - 扫描总数: {file_count}")
    print(f"   - 成功转换: {success_count}")
    print(f"   - 成功率: {success_count/file_count*100:.1f}%" if file_count else "   - 无文件")

    if success_count > 0:
        with open(OUTPUT_FILENAME, 'w', encoding='utf-8') as f:
            for doc in processed_docs:
                f.write(json.dumps(doc, ensure_ascii=False) + '\n')
        print(f"文件已生成: {OUTPUT_FILENAME}")


if __name__ == "__main__":
    main()
