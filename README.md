# 本地文档搜索助手

本地文档搜索助手（Local Document Search）是一个本地运行、隐私优先的文档索引与检索工具。

项目支持 Markdown、PDF、Office、XMind、draw.io、HTML、图片、视频等多种文件类型，提供 CLI 与 Web UI 双入口，并遵循 **CLI 优先** 的设计原则：所有核心能力先落到 Service 层和 CLI，再由 Web UI 复用。


## 界面

### 搜索主页

<p align="center">
  <img src="docs\assets\search.png" alt="搜索主页">
</p>


## 快速开始

以下示例以 Windows PowerShell 为主；若使用 bash，可将 `Copy-Item` 替换为 `cp`。

### 1. 安装运行依赖

如果你只是想使用工具，执行：

```powershell
uv sync
```

如果你还要参与开发、运行测试或使用 Playwright 截图脚本，执行：

```powershell
uv sync --extra dev
```

### 2. 创建配置文件

```powershell
Copy-Item .env.example .env
```

最少确认以下配置：

- `DATABASE_BACKEND`
- `POSTGRESQL_DEFAULT_SEARCH_MODE`
- `SQLITE_DB_PATH` 或 `DATABASE_URL`
- `SEARCH_DIRS`

PostgreSQL 建议显式使用 `psycopg` 驱动前缀：

```env
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/local_document_search
```

如果你希望 PostgreSQL 默认使用更适合中文场景的全文检索，请改成：

```env
DATABASE_BACKEND=postgresql
POSTGRESQL_DEFAULT_SEARCH_MODE=fulltext
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/local_document_search
```

说明：

- `fulltext` 对应 PGroonga
- `fuzzy` 对应 pg_trgm
- 当前 PostgreSQL 初始化会同时准备两套搜索对象，CLI 与 Web 可按请求切换，不再需要修改配置后重启应用才能切换匹配方式

### 3. 初始化数据库

```powershell
uv run python doc-cli.py db init
```

如果你要把已有 SQLite 数据迁移到 PostgreSQL，请继续阅读：

- `docs/engineering/SQLite_到_PostgreSQL_迁移指南.md`
- `docs/engineering/PostgreSQL_PGroonga_接入说明.md`

### 4. 索引一个目录

```powershell
uv run python doc-cli.py index "D:\documents\历史"
```

如果你想先预览会处理哪些文件：

```powershell
uv run python doc-cli.py index "D:\documents\历史" --dry-run
```

### 5. 执行一次搜索

```powershell
uv run python doc-cli.py search "历史"
```

### 6. 启动 Web 服务

```powershell
uv run python run.py
```

默认访问地址：`http://127.0.0.1:5000`

到这一步，你应该已经跑通了“初始化 -> 索引 -> 搜索 -> 打开 Web”的完整闭环。

如果你计划在 Ubuntu 24.04 上运行，请继续阅读：

- `docs/engineering/本地文档搜索助手_Ubuntu_24.04_部署指南.md`


## 支持的文件类型

| 类型 | 扩展名 | 当前处理方式 |
| --- | --- | --- |
| Markdown / 文本 | `md` `txt` | 直接读取正文 |
| HTML | `html` `htm` | 轻量抽取标题与正文，转为 Markdown |
| 现代 Office / PDF | `pdf` `docx` `pptx` `xlsx` | 使用 `markitdown` 提取正文 |
| 旧版 Office | `doc` `ppt` `xls` | Windows Office / COM 自动化优先，失败时回退为元数据索引 |
| XMind | `xmind` | 解析内容并转为 Markdown |
| draw.io | `drawio` | 解析 XML 并提取文本 |
| 图片 | `jpg` `jpeg` `png` `gif` `webp` `svg` | 当前仅索引元数据，不做 OCR |
| 视频 | `mp4` `mov` `avi` `mkv` `flv` | 当前仅索引元数据，不做转录 |


## CLI 用法

### 命令矩阵

| 命令 | 作用 | 关键参数 | `dry-run` | 当前状态 |
| --- | --- | --- | --- | --- |
| `db init` | 初始化数据库、建表、建搜索索引对象 | `--format` `--verbose` `--quiet` | 不支持 | 已完成 |
| `db migrate-to-postgres` | 把 SQLite 业务数据迁移到 PostgreSQL | `--sqlite-db-path` `--database-url` `--include-ingest-state` `--truncate-target` `--format` | 不支持 | 已完成 |
| `index` | 索引一个或多个目录 | `--recursive` `--since` `--type` `--force` `--dry-run` `--format` `--verbose` `--quiet` | 支持 | 已完成 |
| `search` | 检索已索引文档 | `--mode` `--limit` `--offset` `--format` | 不需要，命令本身只读 | 已完成 |
| `errors list` | 查看失败记录，必要时可一并包含回退记录 | `--name` `--updated-after` `--updated-before` `--include-fallback` `--format` | 不需要，命令本身只读 | 已完成 |
| `errors retry` | 重试失败记录，必要时可一并处理回退记录 | `--id` `--path` `--all-failed` `--include-fallback` `--dry-run` `--format` | 支持 | 已完成，推荐入口 |
| `retry` | `errors retry` 的顶层兼容别名 | `document_id` `--all-failed` `--include-fallback` `--dry-run` `--format` | 支持 | 已完成，兼容别名 |
| `clean` | 扫描并删除孤儿索引记录 | `--dry-run` `--type` `--path-keyword` `--format` | 支持 | 已完成 |

补充说明：

- 当前 CLI 已支持 SQLite 与 PostgreSQL 两种数据库后端
- PostgreSQL 目前支持 `全文检索（PGroonga）` 与 `模糊匹配（pg_trgm）` 两种模式，中文全文检索建议优先使用 PGroonga
- 如需从 SQLite 切换到 PostgreSQL，可使用 `db migrate-to-postgres`
- `index --dry-run` 只预览扫描结果，不转换文件、不写库、不更新索引状态
- `errors retry --dry-run` / `retry --dry-run` 只预览命中的失败或回退记录，不执行真实重试
- 所有命令路径都支持 `--help-plain`，输出适合 AI Agent 和脚本读取的纯文本帮助
- 文档与帮助文案默认主推 `errors retry`；顶层 `retry` 仅作为兼容和快捷别名保留

### 常用命令示例

```powershell
uv run python doc-cli.py db init
uv run python doc-cli.py db migrate-to-postgres
uv run python doc-cli.py index "D:\docs" --dry-run
uv run python doc-cli.py index "D:\docs" --force
uv run python doc-cli.py search "历史"
uv run python doc-cli.py search "历史" --mode fuzzy
uv run python doc-cli.py errors list
uv run python doc-cli.py errors list --include-fallback
uv run python doc-cli.py errors retry --all-failed --dry-run
uv run python doc-cli.py errors retry --all-failed --include-fallback
uv run python doc-cli.py clean "D:\docs" --dry-run
```


## Windows Terminal 中直接使用 `doc-cli`

项目已提供包装脚本：

- `scripts\doc-cli.cmd`
- `scripts\doc-cli.ps1`

把项目的 `scripts` 目录加入当前用户的 `PATH`。

重开一个 Windows Terminal 标签页后，就可以直接使用：

```powershell
doc-cli --help
doc-cli --help-plain
doc-cli index "D:\documents\历史"
doc-cli search "历史"
```

说明：

- `doc-cli.cmd` 适用于 `cmd`、PowerShell、Windows Terminal，兼容性最好
- `doc-cli.ps1` 提供 PowerShell 原生入口
- 两个脚本都会自动定位仓库根目录并转发到 `uv run python doc-cli.py`，不依赖当前工作目录


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
├── scripts/                    # 辅助脚本（如截图、包装脚本）
├── data/                       # SQLite 数据目录
├── logs/                       # 日志目录
├── doc-cli.py                  # CLI 唯一入口
├── run.py                      # Web 服务入口
├── README.md
└── AGENTS.md
```


## 常见问题

### 为什么 Web 端不能像桌面应用一样直接选择本地文件夹？

因为当前是浏览器 + Flask 的本地 Web 架构，浏览器不能把可直接交给服务端使用的本机目录句柄暴露出来。v1 当前采用“输入本机绝对路径”的方案。

### 旧版 Office 文档为什么有时只能做元数据索引？

`doc` / `xls` / `ppt` 的正文提取依赖 Windows 本机 Microsoft Office 与 `pywin32`。如果环境不满足，系统会自动回退为元数据索引，而不是整次索引失败。

### 回退为元数据索引的文件如何重新转换？

当前版本已经支持把“回退为元数据索引”的文档纳入错误记录的筛选和重试范围。

Web：

- 打开“错误记录”页面
- 勾选“包含回退记录”
- 选择要重试的记录，点击“重试选中”
- 或直接点击“重试全部问题记录”

CLI：

```powershell
uv run python doc-cli.py errors list --include-fallback
uv run python doc-cli.py errors retry --all-failed --include-fallback
uv run python doc-cli.py retry --all-failed --include-fallback
```

说明：

- 真失败记录的 `status` 为 `failed`
- 回退为元数据索引的记录 `status` 为 `fallback`
- 搜索结果仍会包含 `completed` 和 `fallback` 文档；只有 `failed` 文档不会出现在检索结果中
