# 本地文档搜索助手

本地文档搜索助手（Local Document Search）是一个本地运行、隐私优先的文档索引与检索工具。项目支持 Markdown、HTML、PDF、Office、XMind、draw.io、图片、视频等多种文件类型，提供 CLI 与 Web UI 双入口，并遵循 **CLI 优先** 的设计原则：所有核心能力先落到 Service 层和 CLI，再由 Web UI 复用。

AI Coding Agent 专用说明请见 `AGENTS.md`；本文档面向人类开发者，提供完整的项目说明、运行方式与开发流程。

## 当前实现范围

- `db init`：初始化 SQLite 数据库与 FTS5 虚拟表
- `index`：扫描目录，支持递归、增量跳过、按修改时间过滤、按文件类型过滤
- `search`：基于 SQLite FTS5 + LIKE 回退的全文检索
- `errors list` / `errors retry` / `retry`：查看失败记录并重试
- `clean`：扫描并删除孤儿索引记录
- Web 页面：索引、检索、错误记录、清理索引、文档预览、打开原文件 / 所在目录

## 架构原则

- **CLI 优先**：核心功能先实现为 CLI 命令，再由 Web UI 复用
- **Service 层复用**：CLI 与 Web UI 共享同一套 Service 层
- **路由薄封装**：`routes/` 负责参数解析、调用 Service、返回页面或响应，不实现业务逻辑
- **转换器统一返回结构化结果**：Converter 失败时返回带 `error_message` 的结果对象，而不是向外抛异常

## 支持的文件类型

| 类型 | 扩展名 | 当前处理方式 |
| --- | --- | --- |
| Markdown / 文本 | `md` `markdown` `txt` | 直接读取正文 |
| HTML | `html` `htm` | 轻量抽取标题与正文，转为 Markdown |
| 现代 Office / PDF | `pdf` `docx` `pptx` `xlsx` | 使用 `markitdown` 提取正文 |
| 旧版 Office | `doc` `ppt` `xls` | Windows Office / COM 自动化优先，失败时回退为元数据索引 |
| XMind | `xmind` | 解析内容并转为 Markdown |
| draw.io | `drawio` | 解析 XML 并提取文本 |
| 图片 | `jpg` `jpeg` `png` `gif` `webp` `svg` | 当前仅索引元数据，不做 OCR |
| 视频 | `mp4` `mov` `avi` `mkv` | 当前仅索引元数据，不做转录 |

> 说明：当前推荐使用 SQLite。PostgreSQL 接口已预留，但检索后端仍处于待完善状态。

## 技术栈

- Python 3.12
- Flask
- SQLAlchemy
- Typer
- SQLite FTS5
- `markitdown`
- `python-dotenv`
- `uv`
- `ruff`
- `pyright`
- `pytest`
- Playwright（仅开发期页面验证 / 截图）

## 项目结构

```text
local-document-search/
├── src/
│   └── local_document_search/
│       ├── routes/             # Web 路由层
│       ├── services/           # 核心业务逻辑
│       ├── converters/         # 文件格式转换器
│       ├── persistence/        # 数据访问层、Repository、搜索后端
│       ├── templates/          # Jinja 模板
│       ├── static/             # 静态资源
│       ├── utils/              # 辅助工具
│       ├── cli.py              # Typer 命令定义
│       ├── config.py           # 配置加载
│       ├── exceptions.py       # 自定义异常
│       └── models.py           # ORM 模型
├── tests/
│   ├── unit/                   # 单元测试
│   ├── integration/            # 集成测试
│   ├── e2e/                    # CLI 端到端测试
│   └── fixtures/               # 测试样例文档
├── scripts/                    # 辅助脚本（如页面截图）
├── data/                       # SQLite 数据目录
├── logs/                       # 日志目录
├── doc-cli.py                  # CLI 唯一入口
├── run.py                      # Web 服务入口
├── README.md
└── AGENTS.md
```

## 快速开始

以下示例以 Windows PowerShell 为主；若使用 bash，可将 `Copy-Item` 替换为 `cp`。

### 1. 安装依赖

```powershell
uv sync --extra dev
```

### 2. 创建配置文件

```powershell
Copy-Item .env.example .env
```

至少确认以下配置：

- `DATABASE_BACKEND`
- `SQLITE_DB_PATH`
- `SEARCH_DIRS`

### 3. 初始化数据库

```powershell
uv run python doc-cli.py db init
```

### 4. 启动 Web 服务

```powershell
uv run python run.py
```

默认访问地址：`http://127.0.0.1:5000`

### 5. 在 Windows Terminal 中直接使用 `doc-cli`

项目已提供包装脚本：

- `scripts\doc-cli.cmd`
- `scripts\doc-cli.ps1`

推荐做法是把项目的 `scripts` 目录加入当前用户的 `PATH`。在 PowerShell 中执行一次：

```powershell
$scriptsDir = "D:\study\code\github\local-document-search\scripts"
[Environment]::SetEnvironmentVariable(
    "Path",
    $env:Path + ";" + $scriptsDir,
    "User"
)
```

重开一个 Windows Terminal 标签页后，就可以直接使用：

```powershell
doc-cli --help
doc-cli --help-plain
doc-cli search "历史"
doc-cli index "D:\documents\历史"
```

说明：

- `doc-cli.cmd` 适用于 `cmd`、PowerShell、Windows Terminal，兼容性最好。
- `doc-cli.ps1` 提供 PowerShell 原生入口。
- 两个脚本都会自动定位仓库根目录并转发到 `uv run python doc-cli.py`，不依赖当前工作目录。

## 配置说明

`.env.example` 当前包含以下配置项：

```dotenv
# 搜索后端选择：sqlite（默认）或 postgresql
DATABASE_BACKEND=sqlite

# SQLite 数据库文件路径（DATABASE_BACKEND=sqlite 时生效）
SQLITE_DB_PATH=data/search.db

# PostgreSQL 连接串（DATABASE_BACKEND=postgresql 时生效）
# DATABASE_URL=postgresql://user:password@localhost:5432/dbname

# 默认索引目录（逗号分隔，doc-cli.py index 不传参数时使用）
SEARCH_DIRS=

# 索引时排除的文件夹名称关键词（逗号分隔）；默认排除 Markdown 图片目录
EXCLUDED_DIR_KEYWORDS=.assets

# Flask 运行配置
FLASK_HOST=127.0.0.1
FLASK_PORT=5000
FLASK_DEBUG=false

# 日志级别：DEBUG / INFO / WARNING / ERROR
LOG_LEVEL=INFO

# MarkItDown 基础转换超时时间（秒）
# 大体积 PDF / Office 文件会在此基础上自动放宽，默认最多放宽到 300 秒
MARKITDOWN_TIMEOUT_SECONDS=90

# 大文件阈值（MiB）：达到该大小后，结构化文档会自动放宽转换超时
LARGE_FILE_THRESHOLD_MB=15

# 超大文件阈值（MiB）：达到该大小后，按 LARGE_FILE_INDEX_MODE 执行
VERY_LARGE_FILE_THRESHOLD_MB=50

# 超大文件索引策略：full=继续全文提取，metadata=直接回退为元数据索引
LARGE_FILE_INDEX_MODE=metadata
```

配置建议：

- 当前优先使用 `DATABASE_BACKEND=sqlite`
- 如果 `index` 命令不传路径，则会回退使用 `SEARCH_DIRS`
- `EXCLUDED_DIR_KEYWORDS` 默认是 `.assets`，用于跳过 Markdown 笔记图片目录；显式设为空字符串可关闭默认排除
- 旧版 Office 正文提取依赖 Windows 本机 Microsoft Office 与 `pywin32`
- `.env` 包含本地路径等敏感信息，已加入 `.gitignore`，不要提交

## CLI 用法

### 命令矩阵

| 命令 | 作用 | 关键参数 | `dry-run` | 当前状态 |
| --- | --- | --- | --- | --- |
| `db init` | 初始化数据库、建表、建搜索对象、重建搜索索引 | `--format` `--verbose` `--quiet` | 不支持 | 已完成 |
| `index` | 索引一个或多个目录 | `--recursive` `--since` `--type` `--force` `--dry-run` `--format` `--verbose` `--quiet` | 支持 | 已完成 |
| `search` | 检索已索引文档 | `--limit` `--offset` `--format` | 不需要，命令本身只读 | 已完成 |
| `errors list` | 查看失败记录 | `--name` `--updated-after` `--updated-before` `--format` | 不需要，命令本身只读 | 已完成 |
| `errors retry` | 重试失败记录 | `--id` `--path` `--all-failed` `--dry-run` `--format` | 支持 | 已完成，推荐入口 |
| `retry` | `errors retry` 的顶层兼容别名 | `document_id` `--all-failed` `--dry-run` `--format` | 支持 | 已完成，兼容别名 |
| `clean` | 扫描并删除孤儿索引记录 | `--dry-run` `--type` `--path-keyword` `--format` | 支持 | 已完成 |

说明：

- 当前 CLI 主路径以 SQLite 为准，已具备完整可用性。
- `index --dry-run` 只预览扫描结果，不转换文件、不写库、不更新索引状态。
- `errors retry --dry-run` / `retry --dry-run` 只预览命中的失败记录，不执行真实重试。
- 文档与帮助文案默认主推 `errors retry`；顶层 `retry` 仅作为兼容和快捷别名保留。
- PostgreSQL 检索后端仍为预留能力，当前不在“已完成”范围内。

### 数据库初始化

```powershell
uv run python doc-cli.py db init
```

### 索引文档

```powershell
uv run python doc-cli.py index D:\docs
uv run python doc-cli.py index D:\docs D:\archive
uv run python doc-cli.py index D:\docs --force
uv run python doc-cli.py index D:\docs --dry-run
uv run python doc-cli.py index D:\docs --since 2026-03-01T00:00:00
uv run python doc-cli.py index D:\docs --type md --type html
uv run python doc-cli.py index D:\docs --format json
```

### 搜索文档

```powershell
uv run python doc-cli.py search "sample keyword"
uv run python doc-cli.py search "sample keyword" --limit 20
uv run python doc-cli.py search "sample keyword" --offset 20 --format json
```

### 查看与重试失败记录

```powershell
uv run python doc-cli.py errors list
uv run python doc-cli.py errors list --name broken
uv run python doc-cli.py errors retry --all-failed
uv run python doc-cli.py errors retry --all-failed --dry-run
uv run python doc-cli.py errors retry --path D:\docs\broken.drawio
uv run python doc-cli.py retry 12
```

说明：

- 推荐优先使用 `uv run python doc-cli.py errors retry ...`
- `uv run python doc-cli.py retry ...` 仅是顶层兼容别名，适合短命令或脚本场景

### 清理孤儿记录

```powershell
uv run python doc-cli.py clean
uv run python doc-cli.py clean D:\docs --dry-run
uv run python doc-cli.py clean D:\docs --type md --path-keyword archive
```

通用说明：

- 所有命令都支持 `--help`
- 所有命令路径都支持 `--help-plain`，输出适合 AI Agent 和脚本读取的纯文本帮助
- 大部分命令支持 `--format table|json`
- 部分命令支持 `--verbose` 与 `--quiet`

## Web UI

Web UI 与 CLI 复用同一套 Service 层。当前页面包括：

- 文档索引
- 文档搜索
- 错误记录
- 清理索引
- 文档预览
- 打开文件 / 打开所在目录

说明：

- 文档预览基于 Markdown 渲染
- “打开文件 / 打开所在目录”调用系统默认行为
- 浏览器无法安全地把本机文件夹句柄直接交给 Flask 服务端，因此索引页面当前使用“输入目录绝对路径”的方式代替原生文件夹选择器

## 开发与测试

### 提交前质量检查

```powershell
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest
```

### 测试分层

- `tests/unit/`：单个函数 / 类的单元测试，使用 mock 隔离依赖
- `tests/integration/`：Service + 数据库的集成测试，使用临时数据库
- `tests/e2e/`：通过 CLI 驱动的端到端测试
- `tests/fixtures/`：最小化测试样本文档

当前重点覆盖：

- 单元测试：转换器基础行为
- 集成测试：索引、检索、预览、失败重试、孤儿清理
- E2E 测试：`db init -> index -> search -> errors retry -> clean`

开发约束：

- 所有测试必须使用隔离的临时数据库，禁止读写 `data/search.db`
- 新增或修改功能时，应补充对应测试

## 页面改动验证流程

如果你正在调整 Web 页面样式或交互，推荐按下面的顺序验证。

1. 修改页面文件，例如搜索结果页主要调整 `src/local_document_search/templates/search.html`
2. 运行质量检查：

```powershell
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest
```

3. 为搜索页准备测试数据，例如索引 `D:\documents\历史`：

```powershell
uv run python doc-cli.py index "D:\documents\历史" --force --quiet
```

4. 在终端 A 启动 Web 服务：

```powershell
uv run python run.py
```

5. 在终端 B 打开搜索结果页查看效果：

```powershell
Start-Process "http://127.0.0.1:5000/search?q=%E5%8E%86%E5%8F%B2"
```

6. 如果需要保存截图，可以使用浏览器无头模式：

```powershell
New-Item -ItemType Directory -Force artifacts | Out-Null

$edge = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
if (-not (Test-Path $edge)) {
    $edge = "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe"
}

& $edge `
  --headless=new `
  --disable-gpu `
  --window-size=1440,1800 `
  --virtual-time-budget=3000 `
  --screenshot="artifacts\search-history.png" `
  "http://127.0.0.1:5000/search?q=%E5%8E%86%E5%8F%B2"
```

7. 验证完成后，在运行 `run.py` 的终端中按 `Ctrl + C` 停止服务。

## Playwright 页面验证方案

如果你需要更稳定的页面验证流程，例如自动输入关键词、等待页面稳定后截图、后续扩展成 Web E2E，可使用 `scripts/capture_search_page.py`。

### 安装浏览器

```powershell
uv run playwright install chromium
```

### 运行截图脚本

先在终端 A 启动 Web 服务：

```powershell
uv run python run.py
```

再在终端 B 执行：

```powershell
uv run python scripts/capture_search_page.py --query 历史
```

默认行为：

- 打开 `http://127.0.0.1:5000/search`
- 在首页输入关键词并执行搜索
- 等待结果页稳定
- 将整页截图保存到 `artifacts/search-history-playwright.png`

常见用法：

```powershell
uv run python scripts/capture_search_page.py --query 历史 --output artifacts/search-history.png
uv run python scripts/capture_search_page.py --query 历史 --headed
uv run python scripts/capture_search_page.py --base-url http://127.0.0.1:5001 --query sample
```

适用建议：

- 临时看效果、零额外脚本依赖：使用浏览器无头截图方案
- 需要稳定重现页面、验证交互、后续扩展 Web E2E：优先使用 Playwright 方案


