# Humor Generator

知识图谱驱动的中文幽默生成系统。用图谱发现逻辑冲突点，15种数据驱动策略生成笑话候选，critic 评分 + 对抗精炼输出最优。

## 快速开始

```bash
pip install -r requirements.txt

# 首次：导入外部数据 + 构建图谱
python pipeline/build/import_external.py
python src/knowledge/graph.py

# 生成笑话
python main.py --topic 打工人
python main.py --random
python main.py                  # 交互模式
```

## 环境变量

```
GEMINI_API_KEY=xxx
GOOGLE_CLOUD_PROJECT=gen-lang-client-0577448366
```

## 项目结构

```
humor_generator/
├── main.py                          # 入口
├── requirements.txt
├── src/                             # 主包
│   ├── gemini_client.py             # Gemini API 客户端
│   ├── critic.py                    # 评分 + 对抗精炼
│   ├── joke_generator.py            # 多路径编排层
│   ├── caption_generator.py         # 妈的欧洲账本风格文案
│   ├── knowledge/                   # 知识层
│   │   ├── graph.py                 # 图谱构建(一键) + 查询
│   │   ├── graph_expander.py        # 图谱动态扩展(新话题)
│   │   ├── semantic.py              # HowNet义原 + 词林跨域
│   │   └── rag_retriever.py         # 梗库向量检索
│   └── methods/                     # 15种生成方法
│       ├── kg_contrast.py           # 图谱冲突三元组
│       ├── llm_assoc.py             # 多跳因果链
│       ├── semantic_dist.py         # HowNet义原冲突
│       ├── context_shift.py         # 词林跨域对比
│       ├── expectation.py           # 预期违背(图谱期待/现实边)
│       ├── name_analysis.py         # 拆字分析
│       ├── homophone.py             # 谐音梗(图谱谐音边+pypinyin)
│       ├── ambiguity.py             # 歧义词(HowNet多sense)
│       ├── ironic_reversal.py       # 讽刺反转(图谱反讽边)
│       ├── false_analogy.py         # 类比错位(词林+图谱)
│       ├── self_contradiction.py    # 自我矛盾(目的vs结果)
│       ├── hyperbolic_deflation.py  # 夸张降格(崇高→平凡)
│       ├── self_deprecation.py      # 自嘲(情感负面节点)
│       ├── xiehouyu_gen.py          # 歇后语(图谱歇后语边)
│       ├── concretize.py            # 具体化(维度矩阵)
│       └── rag_replace.py           # RAG梗库后处理
├── pipeline/                        # 数据管道
│   ├── build/
│   │   ├── import_external.py       # 一键导入外部数据
│   │   ├── to_rag_chime.py          # Chime梗库 → RAG语料
│   │   └── to_rag_meme.py           # 十万个梗库 → RAG语料
│   ├── extract/
│   │   ├── extract_youtube.py       # YouTube字幕/转录采集
│   │   ├── extract_transcripts.py   # 小红书视频转录
│   │   ├── extract_images.py        # 图片OCR
│   │   └── extract_memes.py         # 梗提取
│   └── configs/
│       ├── sources.yaml             # 数据源配置
│       └── youtube_sources.txt      # YouTube频道列表
└── data/
    ├── cache/
    │   └── knowledge_graph.pkl      # 图谱缓存
    ├── external/                    # 下载的外部数据(ConceptNet等)
    ├── external_triples/            # 外部数据转换的三元组
    ├── annotations/                 # 节点标注(情感/词林)
    └── topic_pool.json
```

## 系统架构

```
输入话题
  │
  ├─ graph.py 构建图谱（一键，自动增量更新）
  │   ├── GCS已有三元组（脱口秀/妈的欧洲账本/YouTube）
  │   ├── 扫描新文本 → Gemini提取三元组（断点续传）
  │   ├── 本地外部数据（ConceptNet/歇后语/成语/谐音）
  │   ├── 节点标注（大连理工情感 + 词林领域）
  │   └── humor_weight 多维度打分
  │
  ├─ find_humor_slots() 找冲突节点（基于humor_weight）
  ├─ RAG检索梗库（1975条）
  │
  ├─ 15路并行生成（全部数据驱动）
  │   ├── 图谱驱动：kg_contrast, ironic_reversal, expectation,
  │   │            self_contradiction, hyperbolic_deflation,
  │   │            self_deprecation, xiehouyu_gen, llm_assoc
  │   ├── HowNet驱动：semantic_dist, ambiguity
  │   ├── 词林驱动：context_shift, false_analogy
  │   ├── 谐音/拆字：homophone, name_analysis
  │   └── 维度矩阵：concretize
  │
  ├─ rag_replace 后处理（梗库融合）
  │
  └─ critic 评分 + 对抗精炼 → 输出最优
```

## 图谱规模

| 指标 | 数量 |
|------|------|
| 节点 | ~195,000 |
| 边 | ~314,000 |
| 三元组 | ~358,000 |
| 高价值边 | ~225,000 |
| 情感标注命中 | ~16,700 |
| 词林标注命中 | ~26,000 |

数据来源：ConceptNet中文(272k) + 成语(55k) + 歇后语(14k) + 谐音(14k) + 脱口秀语料(1.7k) + 妈的欧洲账本(1.6k)

## 图谱更新

```bash
# 导入/更新外部数据（首次或有新数据源时）
python pipeline/build/import_external.py

# 重建图谱（自动检测新文本、提取、合并、标注）
python src/knowledge/graph.py

# 只导入特定数据源
python pipeline/build/import_external.py --only conceptnet xiehouyu
```

## 技术栈

- **LLM**: Gemini API (gemini-2.5-pro)
- **知识图谱**: NetworkX (~195k节点)
- **语义词典**: HowNet (OpenHowNet) — 义原冲突、多sense歧义
- **语义分类**: 词林扩展版 (cilin) — 跨域对比
- **情感标注**: 大连理工情感词汇本体库 (27k词)
- **谐音**: pypinyin + jieba词库
- **常识**: ConceptNet 5.7 中文子集 (272k三元组)
- **云存储**: GCS (xhs-humor-data)

## GCS 数据结构

```
xhs-humor-data/data/
├── original_data/          # 原始数据（只读）
├── raw_data/               # 逐字稿/图文txt
│   ├── 脱口秀大咖/
│   ├── 脱口秀集锦/
│   ├── 妈的欧洲账本/
│   └── youtube_脱口秀/
└── input_data/             # 处理后的JSONL + checkpoint
    ├── rag_ready_*.jsonl
    ├── graphrag_ready_*.jsonl
    └── checkpoints/
```

## 理论基础

| 论文 | 贡献 |
|------|------|
| Witscript 3 (Toplyn, 2023) | 多路径候选 + critic选优 |
| Let's be Humorous (Zhang et al., 2020) | 知识图谱驱动，relation类型决定笑点强度 |
| Incongruity-Resolution (Ritchie) | Setup建立期待 → Punchline打破 |
| HUMORCHAIN (2026) | 理论匹配 → 幽默生成链条 |
| Not Human, Funnier (CHI 2026) | AI承认身份时幽默感倍增 |
