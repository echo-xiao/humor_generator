# 妈的欧洲账本 — 文案生成系统

MCP Server 驱动的小红书风格文案工具。Claude 写文案，系统提供风格数据 + 图片匹配 + 渲染发布。

## 使用

通过 Claude Code 对话使用，MCP server 自动启动。

```bash
# 环境变量
GEMINI_API_KEY=xxx
GOOGLE_CLOUD_PROJECT=gen-lang-client-0577448366
```

## 工作流

1. `get_persona` → 读人设
2. `get_references` / `get_strategy` / `get_rules` → 获取风格数据
3. Claude 写文案（===图1=== 格式）
4. `match_images` → 从 Google Photos 匹配照片
5. `render_and_preview` → 渲染成图 + 上传 Google Drive

## 结构

```
├── mcp_server.py          # MCP 工具定义
├── pipeline/
│   ├── data.py            # 数据加载 + 格式化
│   ├── match_images.py    # 照片匹配
│   ├── render_post.py     # 图片渲染
│   ├── publish.py         # 预览 + 发布
│   └── photo_index.py     # 照片索引构建
├── scripts/               # 一次性脚本
├── data/                  # 风格数据 + 照片索引
└── output/                # 渲染输出
```
