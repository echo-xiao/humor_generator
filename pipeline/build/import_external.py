"""
import_external.py - 一键导入所有外部数据

用法:
  python generate_input_data/build/import_external.py          # 全部导入
  python generate_input_data/build/import_external.py --only conceptnet xiehouyu  # 只跑指定的

数据源:
  conceptnet   - ConceptNet 5.7 中文高价值关系（首次下载 ~3GB）
  xiehouyu     - 歇后语词典（GitHub）
  chengyu      - 成语词典（GitHub）
  homophone    - CC-CEDICT + pypinyin 谐音边
  sentiment    - 大连理工情感词汇本体库 → 节点情感标注
  cilin        - 词林 → 节点领域标注

输出:
  data/external_triples/*.jsonl   - 三元组（graph.py 加载）
  data/annotations/*.json         - 节点标注（graph.py 加载）
"""

import argparse
import gzip
import json
import os
import re
import sys
import urllib.request
from collections import defaultdict

from tqdm import tqdm

# ==================== 路径 ====================

BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data")
EXTERNAL_DIR = os.path.join(BASE_DIR, "external")
TRIPLES_DIR = os.path.join(BASE_DIR, "external_triples")
ANNOTATIONS_DIR = os.path.join(BASE_DIR, "annotations")

for d in (EXTERNAL_DIR, TRIPLES_DIR, ANNOTATIONS_DIR):
    os.makedirs(d, exist_ok=True)


# GCS 配置
BUCKET_NAME = "xhs-humor-data"
PROJECT_ID = "gen-lang-client-0577448366"


# ==================== 工具 ====================

def _download_from_gcs(gcs_path, local_path):
    """优先从 GCS 下载（项目内备份）"""
    try:
        from google.cloud import storage
        client = storage.Client(project=PROJECT_ID)
        bucket = client.bucket(BUCKET_NAME)
        blob = bucket.blob(gcs_path)
        if blob.exists():
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            blob.download_to_filename(local_path)
            size = os.path.getsize(local_path) / 1024 / 1024
            print(f"  GCS 下载: {gcs_path} ({size:.1f}MB)")
            return True
    except Exception as e:
        print(f"  GCS 下载失败: {e}")
    return False


def _download(url, path, desc="下载中", gcs_path=None):
    if os.path.exists(path):
        size = os.path.getsize(path)
        print(f"  已存在: {os.path.basename(path)}（{size/1024/1024:.1f}MB）")
        return

    # 先尝试 GCS
    if gcs_path and _download_from_gcs(gcs_path, path):
        return

    # 再从外部 URL 下载
    os.makedirs(os.path.dirname(path), exist_ok=True)

    def _hook(block_num, block_size, total_size):
        downloaded = block_num * block_size
        if total_size > 0:
            pct = downloaded * 100 / total_size
            sys.stdout.write(f"\r  {desc}: {downloaded/1024/1024:.0f}MB / {total_size/1024/1024:.0f}MB ({pct:.1f}%)")
            sys.stdout.flush()

    urllib.request.urlretrieve(url, path, reporthook=_hook)
    print()


def _skip_if_exists(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            count = sum(1 for _ in f)
        print(f"  已存在: {os.path.basename(path)}（{count} 条），跳过。删除可重新生成。")
        return True
    return False


# ==================== 1. ConceptNet ====================

CONCEPTNET_URL = "https://s3.amazonaws.com/conceptnet/downloads/2019/edges/conceptnet-assertions-5.7.0.csv.gz"
CONCEPTNET_RAW = os.path.join(EXTERNAL_DIR, "conceptnet-assertions-5.7.0.csv.gz")
CONCEPTNET_OUT = os.path.join(TRIPLES_DIR, "conceptnet_zh.jsonl")

CONCEPTNET_RELATION_MAP = {
    "/r/Antonym": "对立于",
    "/r/Causes": "导致",
    "/r/CausesDesire": "引起渴望",
    "/r/MotivatedByGoal": "目的是",
    "/r/ObstructedBy": "被阻碍于",
    "/r/NotDesires": "不想要",
    "/r/HasProperty": "特征是",
    "/r/SymbolOf": "象征",
    "/r/DefinedAs": "等同于",
    "/r/IsA": "是一种",
    "/r/PartOf": "属于",
    "/r/UsedFor": "用于",
    "/r/CapableOf": "能够",
    "/r/MannerOf": "方式是",
    "/r/SimilarTo": "类似于",
    "/r/Desires": "渴望",
    "/r/HasPrerequisite": "前提是",
    "/r/HasFirstSubevent": "首先",
    "/r/HasLastSubevent": "最终",
}

CONCEPTNET_HIGH_VALUE = {
    "/r/Antonym", "/r/Causes", "/r/CausesDesire", "/r/MotivatedByGoal",
    "/r/ObstructedBy", "/r/NotDesires", "/r/SymbolOf", "/r/DefinedAs",
}


def _extract_zh_word(uri, t2s=None):
    parts = uri.split("/")
    if len(parts) >= 4 and parts[2] == "zh":
        word = parts[3]
        if word and len(word) <= 10:
            return t2s.convert(word) if t2s else word
    return None


def import_conceptnet():
    print("\n[ConceptNet]")
    _download(CONCEPTNET_URL, CONCEPTNET_RAW, "下载 ConceptNet（约475MB）",
             gcs_path="data/external/conceptnet-assertions-5.7.0.csv.gz")
    if _skip_if_exists(CONCEPTNET_OUT):
        return

    # 繁→简转换
    try:
        from opencc import OpenCC
        t2s = OpenCC("t2s")
        print("  繁→简转换: 已启用")
    except ImportError:
        t2s = None
        print("  警告: opencc 未安装，繁体字不会转换。pip install opencc-python-reimplemented")

    print("  处理中（流式读取）...")
    count = 0
    high = 0
    with gzip.open(CONCEPTNET_RAW, "rt", encoding="utf-8") as fin, \
         open(CONCEPTNET_OUT, "w", encoding="utf-8") as fout:
        for line in tqdm(fin, desc="  扫描", unit="行", unit_scale=True):
            parts = line.strip().split("\t")
            if len(parts) < 5:
                continue
            rel_uri, start, end = parts[1], parts[2], parts[3]
            if rel_uri not in CONCEPTNET_RELATION_MAP:
                continue
            subj = _extract_zh_word(start, t2s)
            obj = _extract_zh_word(end, t2s)
            if not subj or not obj or subj == obj:
                continue
            try:
                meta = json.loads(parts[4])
                if meta.get("weight", 0) < 1.0:
                    continue
            except (json.JSONDecodeError, KeyError):
                continue

            is_high = rel_uri in CONCEPTNET_HIGH_VALUE
            fout.write(json.dumps({
                "subject": subj, "relation": CONCEPTNET_RELATION_MAP[rel_uri],
                "object": obj, "source_type": "conceptnet", "high_value": is_high,
            }, ensure_ascii=False) + "\n")
            count += 1
            if is_high:
                high += 1

    print(f"  完成: {count} 条（高价值 {high} 条）")


# ==================== 2. 歇后语 ====================

XIEHOUYU_URL = "https://raw.githubusercontent.com/pwxcoo/chinese-xinhua/master/data/xiehouyu.json"
XIEHOUYU_RAW = os.path.join(EXTERNAL_DIR, "xiehouyu.json")
XIEHOUYU_OUT = os.path.join(TRIPLES_DIR, "xiehouyu.jsonl")


def import_xiehouyu():
    print("\n[歇后语]")
    _download(XIEHOUYU_URL, XIEHOUYU_RAW, "下载歇后语词典",
             gcs_path="data/external/xiehouyu.json")
    if _skip_if_exists(XIEHOUYU_OUT):
        return

    with open(XIEHOUYU_RAW, encoding="utf-8") as f:
        data = json.load(f)

    count = 0
    with open(XIEHOUYU_OUT, "w", encoding="utf-8") as fout:
        for item in data:
            riddle = item.get("riddle", "").strip()
            answer = item.get("answer", "").strip()
            if not riddle or not answer:
                continue
            fout.write(json.dumps({
                "subject": riddle, "relation": "歇后语",
                "object": answer, "source_type": "歇后语", "high_value": True,
            }, ensure_ascii=False) + "\n")
            count += 1

    print(f"  完成: {count} 条")


# ==================== 3. 成语 ====================

CHENGYU_URL = "https://raw.githubusercontent.com/pwxcoo/chinese-xinhua/master/data/idiom.json"
CHENGYU_RAW = os.path.join(EXTERNAL_DIR, "idiom.json")
CHENGYU_OUT = os.path.join(TRIPLES_DIR, "chengyu.jsonl")


def import_chengyu():
    print("\n[成语]")
    _download(CHENGYU_URL, CHENGYU_RAW, "下载成语词典",
             gcs_path="data/external/idiom.json")
    if _skip_if_exists(CHENGYU_OUT):
        return

    with open(CHENGYU_RAW, encoding="utf-8") as f:
        data = json.load(f)

    count = 0
    with open(CHENGYU_OUT, "w", encoding="utf-8") as fout:
        for item in data:
            word = item.get("word", "").strip()
            explanation = item.get("explanation", "").strip()
            derivation = item.get("derivation", "").strip()
            if not word or not explanation:
                continue

            short_exp = explanation[:50].rstrip("。，；") if len(explanation) > 50 else explanation
            fout.write(json.dumps({
                "subject": word, "relation": "成语含义",
                "object": short_exp, "source_type": "成语", "high_value": False,
            }, ensure_ascii=False) + "\n")
            count += 1

            if derivation and derivation != "无":
                short_deriv = derivation[:50].rstrip("。，；") if len(derivation) > 50 else derivation
                fout.write(json.dumps({
                    "subject": word, "relation": "成语出处",
                    "object": short_deriv, "source_type": "成语", "high_value": False,
                }, ensure_ascii=False) + "\n")
                count += 1

    print(f"  完成: {count} 条")


# ==================== 4. 谐音 ====================

HOMOPHONE_OUT = os.path.join(TRIPLES_DIR, "homophone.jsonl")


def import_homophone():
    """用 jieba 词库 + pypinyin 构建谐音边（无需外部下载）"""
    print("\n[谐音]")
    try:
        from pypinyin import pinyin, Style
    except ImportError:
        print("  需要安装: pip install pypinyin")
        return

    if _skip_if_exists(HOMOPHONE_OUT):
        return

    import jieba
    jieba.initialize()

    # 从 jieba 词库提取 2-4 字中文词（自带词频）
    print("  从 jieba 词库提取词条...")
    entries = {}
    zh_pattern = re.compile(r"^[\u4e00-\u9fff]+$")
    for word, freq in jieba.dt.FREQ.items():
        if 2 <= len(word) <= 4 and freq >= 50 and zh_pattern.match(word):
            py = "".join(p[0] for p in pinyin(word, style=Style.NORMAL))
            entries[word] = py
    print(f"  词条数: {len(entries)}")

    # 建拼音→词列表索引
    index = defaultdict(list)
    for word, py in entries.items():
        index[py].append(word)
    multi = {py: words for py, words in index.items() if 2 <= len(words) <= 8}
    print(f"  同音词组数: {len(multi)}")

    count = 0
    seen = set()
    with open(HOMOPHONE_OUT, "w", encoding="utf-8") as fout:
        for py, words in multi.items():
            for i, w1 in enumerate(words):
                for w2 in words[i + 1:]:
                    pair = tuple(sorted([w1, w2]))
                    if pair in seen:
                        continue
                    seen.add(pair)
                    fout.write(json.dumps({
                        "subject": w1, "relation": "谐音于",
                        "object": w2, "source_type": "homophone",
                        "high_value": True, "pinyin": py,
                    }, ensure_ascii=False) + "\n")
                    count += 1

    print(f"  完成: {count} 条")


# ==================== 5. 情感标注 ====================

SENTIMENT_RAW_TXT = os.path.join(EXTERNAL_DIR, "sentiment_dict.txt")
SENTIMENT_RAW_CSV = os.path.join(EXTERNAL_DIR, "sentiment_dict.csv")
SENTIMENT_URL = "https://raw.githubusercontent.com/ZaneMuir/DLUT-Emotionontology/master/%E6%83%85%E6%84%9F%E8%AF%8D%E6%B1%87/%E6%83%85%E6%84%9F%E8%AF%8D%E6%B1%87.csv"
SENTIMENT_OUT = os.path.join(ANNOTATIONS_DIR, "sentiment.json")

CATEGORY_POLARITY = {
    "PA": 1.0, "PE": 1.0, "PD": 1.0, "PH": 0.5, "PG": 1.0,
    "PB": 0.5, "PK": 1.0,
    "NA": -1.0, "NB": -1.0, "NJ": -1.0, "NH": -1.0, "NI": -0.5,
    "NC": -0.5, "NG": -1.0, "NK": -1.0, "NN": 0.0,
}


def build_sentiment():
    """解析大连理工情感词典（自动下载，支持 CSV 和 TSV）"""
    print("\n[情感标注]")
    if os.path.exists(SENTIMENT_OUT):
        with open(SENTIMENT_OUT, encoding="utf-8") as f:
            data = json.load(f)
        print(f"  已存在（{len(data)} 词），跳过。")
        return

    # 自动下载
    if not os.path.exists(SENTIMENT_RAW_CSV) and not os.path.exists(SENTIMENT_RAW_TXT):
        try:
            _download(SENTIMENT_URL, SENTIMENT_RAW_CSV, "下载大连理工情感词典",
                     gcs_path="data/external/sentiment_dict.csv")
        except Exception as e:
            print(f"  下载失败: {e}")
            print(f"  请手动下载放到: {SENTIMENT_RAW_CSV}")
            return

    # 选择存在的文件，检测分隔符
    raw_path = SENTIMENT_RAW_CSV if os.path.exists(SENTIMENT_RAW_CSV) else SENTIMENT_RAW_TXT
    with open(raw_path, encoding="utf-8") as f:
        first_line = f.readline()
    sep = "," if "," in first_line else "\t"

    result = {}
    with open(raw_path, encoding="utf-8") as f:
        for line in f:
            parts = [p.strip() for p in line.strip().split(sep)]
            if len(parts) < 7:
                continue
            word = parts[0]
            if not word or word == "词语":
                continue
            category = parts[4]
            try:
                strength = int(float(parts[5]))
            except (ValueError, IndexError):
                strength = 5
            try:
                polarity_code = int(float(parts[6]))
            except (ValueError, IndexError):
                polarity_code = 0

            sentiment = CATEGORY_POLARITY.get(category, 0.0)
            if sentiment == 0.0:
                if polarity_code == 1:
                    sentiment = 0.5
                elif polarity_code == 2:
                    sentiment = -0.5

            if word in result and result[word]["strength"] >= strength:
                continue
            result[word] = {"sentiment": sentiment, "strength": strength, "category": category}

    with open(SENTIMENT_OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    print(f"  完成: {len(result)} 词")


# ==================== 6. 词林标注 ====================

CILIN_OUT = os.path.join(ANNOTATIONS_DIR, "cilin.json")


def build_cilin():
    print("\n[词林标注]")
    if os.path.exists(CILIN_OUT):
        with open(CILIN_OUT, encoding="utf-8") as f:
            data = json.load(f)
        print(f"  已存在（{len(data)} 词），跳过。")
        return

    # cilin 包数据在 site-packages/data/cilin_tree.json
    try:
        import importlib.util
        spec = importlib.util.find_spec("cilin")
        if spec is None or spec.origin is None:
            print("  cilin 未安装: pip install cilin")
            return
        cilin_dir = os.path.dirname(spec.origin)
        cilin_json = os.path.join(cilin_dir, "..", "data", "cilin_tree.json")
        if not os.path.exists(cilin_json):
            print(f"  词林数据未找到: {cilin_json}")
            return
    except ImportError:
        print("  cilin 未安装: pip install cilin")
        return

    with open(cilin_json, encoding="utf-8") as f:
        tree = json.load(f)

    # cilin_tree.json 是嵌套结构: {大类: {中类: {小类: {词群: [词, ...]}}}}
    result = {}

    def _walk(node, code_prefix=""):
        if isinstance(node, list):
            # 叶子节点：词列表
            for word in node:
                if word and word not in result:
                    domain = code_prefix[0] if code_prefix else ""
                    result[word] = {"cilin_code": code_prefix, "domain": domain}
        elif isinstance(node, dict):
            for key, child in node.items():
                _walk(child, code_prefix + key)

    _walk(tree)

    with open(CILIN_OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    print(f"  完成: {len(result)} 词")


# ==================== 主程序 ====================

ALL_TASKS = {
    "conceptnet": import_conceptnet,
    "xiehouyu": import_xiehouyu,
    "chengyu": import_chengyu,
    "homophone": import_homophone,
    "sentiment": build_sentiment,
    "cilin": build_cilin,
}


def main():
    parser = argparse.ArgumentParser(description="导入外部数据")
    parser.add_argument("--only", nargs="+", choices=list(ALL_TASKS.keys()),
                        help="只运行指定的导入任务")
    args = parser.parse_args()

    tasks = args.only if args.only else list(ALL_TASKS.keys())

    print("=" * 50)
    print(f"外部数据导入（{len(tasks)} 个任务）")
    print("=" * 50)

    for name in tasks:
        ALL_TASKS[name]()

    print("\n" + "=" * 50)
    print("全部完成。运行 python humor_generator/knowledge/graph.py 重建图谱。")


if __name__ == "__main__":
    main()
