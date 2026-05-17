# Humor Generator — 小红书风格文案生成系统

MCP Server 驱动的小红书吐槽文案工具。Claude 写文案，系统提供风格数据 + 图片匹配 + 渲染 + 质检。

从195篇"妈的欧洲账本"帖子中提炼写作规则，生成同风格文案。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量 (.env)
GEMINI_API_KEY=xxx

# 3. 通过 Claude Code 对话使用，MCP server 自动启动
```

## 工作流

```
话题 → 人设/规则/策略/范文 → Claude 写文案 → 匹配照片 → 渲染 → 质检 → 发布
```

1. `get_persona` — 读人设（年龄/城市/性格/红线）
2. `get_rules` — 195篇帖子提炼的写作规则（数字精确度、比喻、反转、自嘲等）
3. `get_strategy(topic)` — 特定话题的情绪/表达/语言策略
4. `get_references(topic)` — 最相关的范文 + 7层笑点分析
5. Claude 写文案（===图1=== 格式）
6. `save_draft` — 保存草稿
7. `match_images` — 从 Google Photos 匹配照片（强制匹配，无完美选次相关）
8. `render_and_preview` — 渲染成图 + 自动质检
9. 质检不通过 → 返回修复指令 → Claude 自动修复 → 重新渲染

## MCP Tools

| Tool | 功能 |
|---|---|
| `get_persona` | 返回人设定义 |
| `get_rules` | 返回完整风格规则手册 |
| `get_strategy` | 返回特定话题的写作策略 |
| `get_references` | 返回最相关范文 + 笑点分析 |
| `list_all_posts` | 列出所有范文标题 |
| `save_draft` | 保存文案草稿 |
| `list_drafts` | 列出所有草稿 |
| `match_images` | 为文案匹配 Google Photos 照片 |
| `render_and_preview` | 渲染图片 + 质检 + 打开预览 |

## 项目结构

```
├── mcp_server.py          # MCP 工具定义（入口）
├── pipeline/
│   ├── data.py            # 数据加载（范文检索、规则、策略）
│   ├── match_images.py    # 照片匹配（Gemini 选图 + fallback）
│   ├── render_post.py     # 图片渲染（Gemini 排版 + 样式模板）
│   ├── publish.py         # 预览 + 发布（下载照片 + 渲染 + 打开Finder）
│   ├── critic.py          # 质检（纯色背景检测、文字可读性、图文相关性）
│   └── photo_index.py     # Google Photos 索引构建
├── scripts/
│   ├── analyze_styles.py  # 分析博主照片排版风格
│   └── auth_google_photos.py  # Google OAuth 认证
├── data/
│   ├── persona.json       # 人设
│   ├── rulebook.json      # 写作规则（195篇提炼）
│   ├── strategies.json    # 话题策略
│   ├── posts_analyzed.json # 范文 + 笑点分析
│   ├── posts_raw.json     # 原始帖子
│   ├── styles.json        # 排版样式模板
│   ├── design_guide.json  # 设计指南
│   └── photo_index.json   # 照片索引（3900+张）
└── output/                # 渲染输出（gitignored）
```

## 核心规则（摘要）

- 精确大于模糊：`¥4500/四晚`，不是"几千块"
- 克制大于表达：语气永远比内容冷静一个级别
- 自嘲大于抱怨：先对自己开刀
- 潜台词大于明说：全篇不说出核心情绪词
- 具体大于抽象：`在机场等一艘船`，不是"很荒谬"

## 质检机制

`render_and_preview` 渲染后自动运行 `critic.py`：
- 检测纯色背景（缺少照片）
- 检测文字截断/溢出
- 检测文字可读性（对比度）
- Gemini 视觉检查图文相关性
- 不通过时返回修复指令给 Claude
