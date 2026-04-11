# Humor Generator — 项目契约

## 项目简介
基于 GraphRAG + RAG + HowNet 多路径的自动化幽默生成器。
用知识图谱发现逻辑冲突点（humor slot），用 RAG 检索梗库填充，HowNet 义原找语义张力，Gemini 四路径生成笑话，critic 评分 + 对抗精炼输出最优。

## 技术栈
- **语言**: Python 3.10+
- **LLM**: Gemini API (google-generativeai)，模型：gemini-2.5-pro
- **云存储**: Google Cloud Storage (Bucket: xhs-humor-data)
- **项目 ID**: gen-lang-client-0577448366
- **知识图谱**: NetworkX（本地图库）
- **语义词典**: HowNet (OpenHowNet)，本地数据 `~/.openhownet/`
- **语义分类**: 同义词词林扩展版（哈工大，9万词，`pip install cilin`，数据在 site-packages/data/cilin_tree.json）
- **中文分词**: jieba（HowNet 查词兜底）

## GCS 数据结构
```
xhs-humor-data/
├── data/
│   ├── raw_data/                    # 解压后的原始数据
│   │   ├── 脱口秀大咖/              # 脱口秀逐字稿 txt → GraphRAG 建图
│   │   ├── 脱口秀集锦/              # 脱口秀逐字稿 txt → GraphRAG 建图
│   │   ├── 妈的欧洲账本/            # 图文内容 txt → GraphRAG 建图
│   │   └── 十万个梗库/              # 梗知识 txt → RAG 检索
│   ├── input_data/                  # 所有处理完待用的数据
│   │   ├── rag_ready_chime.jsonl              # Chime 梗库 1458条 → RAG
│   │   ├── rag_ready_十万个梗库.jsonl          # 十万个梗库 517条 → RAG
│   │   ├── graphrag_ready_脱口秀大咖.jsonl     # 三元组 → GraphRAG（checkpoint）
│   │   ├── graphrag_ready_脱口秀集锦.jsonl     # 三元组 → GraphRAG（checkpoint）
│   │   └── graphrag_ready_妈的欧洲账本.jsonl   # 三元组 → GraphRAG（已合并）
│   └── original_data/               # 最原始数据（zip包）
│       ├── xhs_data/                # 小红书原始 zip
│       └── chime_full.json          # Chime 原始数据
```

## 本地项目结构
```
humor_generator/
├── CLAUDE.md
├── data/
│   ├── knowledge_graph.pkl          # 本地图谱缓存（graph_builder 生成，~4700节点/2500边）
│   └── knowledge_graph_expanded.pkl # 扩展后的临时图谱（graph_expander 可选保存）
├── humor_generator/                 # 核心生成模块
│   ├── graph_builder.py             # ✅ 三元组 JSONL → NetworkX 图
│   ├── humor_slot_finder.py         # ✅ 找 Humor Slot（高价值 relation 一跳/二跳搜索）
│   ├── rag_retriever.py             # ✅ 梗库向量检索（Gemini embedding-001，1975条）
│   ├── cross_domain_finder.py       # ✅ HowNet 义原张力冲突 + 跨域同构词
│   ├── cilin_finder.py              # ✅ 词林跨域对比 + 同小类近义可区分词
│   ├── graph_expander.py            # ✅ 知识图谱动态扩展（三种方法）
│   ├── joke_generator.py            # ✅ Gemini 五路径生成
│   └── critic.py                    # ✅ 评分选优 + 对抗精炼
├── main.py                          # ✅ 入口：python main.py --topic 打工人
└── generate_input_data/
    ├── generate_graphrag/
    │   └── extract_triples.py       # 三元组提取脚本
    ├── generate_rag/                # RAG 数据处理脚本
    └── unzip_original_data/         # 解压脚本
```

## 系统架构
```
输入话题
  ↓
[graph_expander] 图谱扩展（话题不在图谱时自动触发）
  → 方法一：模式迁移（高价值三元组模板 → Gemini 生成新三元组）
  → 方法二：节点关联扩展（现有邻居 → Gemini 二跳补充）
  → 方法三：话题类比（HowNet义原相似度 → 复用相似话题的三元组）
  ↓
[humor_slot_finder] 找 Humor Slot（高价值 relation 冲突节点）
  ↓
[rag_retriever] 向量检索梗库（Chime + 十万个梗库）
  ↓
[cross_domain_finder] HowNet 义原张力冲突词
  ↓
[joke_generator] 五路径生成候选
  → 路径A：冲突三元组 → Gemini 生成
  → 路径B：RAG 检索梗库 → Gemini 填充
  → 路径C：话题 + Humor Slot → Gemini 自由发挥
  → 路径D：HowNet 义原张力对 → Gemini 生成
  → 路径E：词林跨域对比（图谱过滤）→ Gemini 生成
  ↓
[critic] 评分（幽默/相关/自然各1-5分）→ 选最优
  ↓
[critic.refine] 对抗精炼（评委批评 → 生成器重写 → 保守接受）
  ↓
输出最优笑话
```

## 理论基础（三篇核心论文）

| 论文 | arXiv | 贡献 |
|------|-------|------|
| Witscript 3 (Toplyn, 2023) | 2301.02695 | 生成流程骨架：多路径候选 + 选最优 |
| Let's be Humorous (Zhang et al., 2020) | 2004.13317 | 知识图谱驱动生成：relation 类型决定笑点强度 |
| Agentic Graph Reasoning (Buehler, 2025) | 2502.13025 | 图谱分析算法：社区划分 + Bridge Node 检测思路 |

## 各模块详解

### graph_builder.py
- 从 GCS 加载三元组 JSONL，构建 NetworkX DiGraph
- 高价值 relation（HIGH_VALUE_RELATIONS）：对立于/反讽/讽刺/本质是/实际是/导致/象征/被视为/等同于/感觉像/意味着等
- 图谱规模：~4700节点 / ~2500边

### humor_slot_finder.py
- 给定话题，遍历一跳+二跳的高价值 relation 边找冲突节点
- 支持精确匹配 + 模糊匹配（topic in node or node in topic）
- 图谱较稀疏，不使用 Louvain，直接用 relation 类型判断冲突强度

### cross_domain_finder.py
- **find_conflict_by_sememe(topic)**：路径D用，义原张力对找冲突词
  - 义原张力对从图谱高价值边自动提取（非硬编码），覆盖1405个义原
  - 过滤：义原数>20的极度多义字（开/上/打）、与话题义原重叠率>=0.8的近义词
  - 查词兜底：直接查 → jieba 分词 → 逐字查
  - OpenHowNet/jieba 日志在 `_get_hownet()` 内部用 redirect_stdout/stderr 静默
- **find_cross_domain_analogs(topic)**：找共享功能义原但有强反差的跨域词（兜底用）

### cilin_finder.py
- 基于同义词词林扩展版（哈工大，9万词，5层树状编码 大类→中类→小类→词群→原子词群）
- **find_similar_cilin(topic)**：同小类（level-3）不同词群（level-4）的近义可区分词
  - 用途：产生"说的是A其实更像B"的笑点结构（如 结婚↔恋爱，贫穷↔富裕）
- **find_contrast_with_graph(topic, G)**：路径E主用，跨大类（level-1）的跨域词，用图谱节点过滤
  - 只返回出现在知识图谱中的词，避免无关候选；按节点高价值边数量打分
- **find_contrast_cilin(topic)**：无图谱过滤版跨域词（调试用，实际用 with_graph 版）
- 数据路径：通过 `importlib.util.find_spec("cilin")` 自动定位，无需手动配置

### graph_expander.py
- **expand_topic(topic, G, methods=(1,2,3))**：返回扩充后的临时图副本，不修改原图
- 方法一（模式迁移）：取现有高价值三元组为模板，Gemini 生成新话题的同模式三元组
- 方法二（节点关联扩展）：把话题现有邻居展示给 Gemini，让其补充二跳新三元组
- 方法三（话题类比）：HowNet Jaccard 相似度找最近已有节点，克隆其高价值三元组
- 在 joke_generator 中，话题找不到 Humor Slot 时自动触发

### joke_generator.py
- 五路径生成，路径A/B/C 依赖 Humor Slot，路径D 依赖 HowNet，路径E 依赖词林
- 路径D：`find_conflict_by_sememe(topic, top_k=3)` → 义原冲突描述 → PROMPT_D → Gemini
- 路径E：`find_contrast_with_graph(topic, G, top_k=3)` → 词林跨域描述 → PROMPT_E → Gemini
  - 兜底：图谱无结果时用 `find_similar_cilin` 的同小类近义词
- 找不到 Humor Slot 时自动调 graph_expander.expand_topic()

### critic.py
- **evaluate(topic, candidates)**：对所有候选打分，按 total 降序
- **refine(topic, joke, scores, max_rounds=2)**：对抗精炼
  - 评委批评 → 生成器按批评重写 → 重新评分 → 只有分数提升才接受（保守策略）
  - 总分 ≥ 13/15 时提前停止
- **run(topic)**：端到端入口，生成 + 评分 + 精炼 + 输出最优

## 核心概念
- **Humor Slot**: 话题在知识图谱中通过高价值 relation 连接的冲突节点（荒诞感来源）
- **义原 (Sememe)**: HowNet 中词语的语义原子，如 `Occupation|职位`、`die|死`
- **义原张力对**: 在脱口秀笑点结构中互为冲突的义原对，自动从高价值图谱边提取
- **词林编码**: 5层树状语义分类（大类A-P → 中类a-z → 小类01-99 → 词群A-Z → 原子词群01-99）
- **跨域对比**: 词林中不同大类的词放在一起产生荒诞感；同小类词产生"说A其实像B"结构
- **对抗精炼**: 评委打分 → 批评驱动生成器重写 → 保守接受（Multi-Agent Adversarial Generation）
- **三元组**: (subject, relation, object)，知识图谱的基本单位

## 环境变量
```
GEMINI_API_KEY=xxx        # Gemini API Key（免费 tier，有限流）
GOOGLE_CLOUD_PROJECT=gen-lang-client-0577448366
```

## 代码规范
每个批量处理脚本必须包含：
1. **断点续传**：处理前检查云端已完成列表，跳过已处理文件
2. **进度条**：使用 `tqdm` 展示实时进度
3. **进度持久化**：每处理完一个文件立即上传结果到 GCS，不要攒着批量上传

## NEVER
- 不要把 API key 写死在代码里，统一用 os.getenv()
- 不要把原始数据下载到本地，统一通过 GCS SDK 读取
- 不要修改 original_data/ 里的任何文件
- 不要在没有断点续传的情况下跑大批量处理任务
- 不要在 cross_domain_finder 里硬编码义原张力对映射（TENSION_PAIRS），必须从图谱自动提取
- 不要主动修改 GENERATOR_VERSION，除非用户明确要求

## 当前进度
- [x] GCS 数据整理完成
- [x] chime_rag_ready.jsonl 生成完成
- [x] meme_rag_ready.jsonl 生成完成
- [ ] 三元组提取（extract_triples.py 已写好，待运行完整语料）
- [x] graph_builder.py（NetworkX 建图，~4700节点/~2500边）
- [x] humor_slot_finder.py（高价值 relation 找冲突节点，支持一跳/二跳）
- [x] rag_retriever.py（Gemini embedding-001 向量检索，1975条，本地缓存）
- [x] cross_domain_finder.py（HowNet 义原张力 + 跨域同构，张力对从图谱自动提取）
- [x] cilin_finder.py（词林跨域对比 + 同小类近义词，图谱过滤版）
- [x] graph_expander.py（三种方法动态扩展图谱）
- [x] joke_generator.py（Gemini 五路径生成，路径D=HowNet义原，路径E=词林跨域，扩展兜底）
- [x] critic.py（评分选优 + 对抗精炼，多代理人对抗生成）
- [x] main.py（入口，--topic 参数 + 交互模式）
- [ ] 部署
