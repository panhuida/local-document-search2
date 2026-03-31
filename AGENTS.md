# AGENTS.md

本文件面向 AI Coding Agent（如 Claude Code、Codex）编写，提供项目引导、开发约束和验证规范。**在开始任何编码任务之前，必须完整阅读本文件。**



## 1. 项目概述

本地文档搜索助手（Local Document Search）是一个**本地运行、隐私安全**的文档检索工具，支持 Markdown、PDF、Word、PowerPoint、Excel、XMind、draw.io、HTML 等格式的索引与检索，提供 Web UI 和 CLI 双入口。

**核心设计原则：CLI 优先。** 所有核心功能必须先有可用的 CLI 命令，Web UI 通过调用相同的 Service 层复用逻辑，不得在路由层重新实现业务逻辑。





## 2. 技术栈

- 后端：Python 3.12 + Flask
- 前端：HTML5 + Vanilla JS + Tailwind CSS（CDN 引入）
- 数据库：SQLite（FTS5 + Native Trigram）、PostgreSQL（PGroonga + pg_trgm）
- ORM：SQLAlchemy
- CLI：Typer
- 文件转换：markitdown
- 配置管理：python-dotenv
- 依赖管理：uv
- 代码格式和风格：Ruff
- 类型检查：Pyright
- 测试框架：pytest





## 3. 项目结构

```text
local-document-search/
├── src/
│   └── local_document_search/  # 源代码根目录
│       ├── routes/             # 路由蓝图（视图函数）
│       ├── services/           # 核心业务逻辑
│       │   ├── index_service.py        # 索引（扫描、写库、增量跳过）
│       │   ├── search_service.py       # 检索
│       │   ├── error_record_service.py # 错误记录查询、重试
│       │   ├── clean_service.py        # 孤儿记录扫描、删除
│       │   ├── document_service.py     # 文档预览
│       │   └── file_opener_service.py  # 打开原始文件
│       ├── converters/         # 文件格式转换层
│       │   ├── __init__.py     # ConverterFactory（含扩展名映射与选择逻辑）
│       │   ├── base.py         # BaseConverter 抽象类
│       │   ├── markitdown.py   # MarkItDownConverter
│       │   ├── xmind.py        # XMindConverter
│       │   └── drawio.py       # DrawioConverter
│       ├── persistence/        # 数据访问层
│       │   ├── database.py     # engine、SessionLocal、Base、FTS5 初始化
│       │   ├── repositories/   # ORM 操作封装（每张表一个 repository）
│       │   │   ├── document_repository.py     # documents 表的增删改查
│       │   │   └── ingest_state_repository.py # ingest_state 表的增删改查
│       │   └── search/         # 搜索后端抽象
│       │       ├── base.py         # SearchBackend 抽象基类
│       │       ├── sqlite_backend.py     # SQLiteSearchBackend（FTS5 实现，默认后端）
│       │       └── postgresql_backend.py # PostgreSQLSearchBackend（PGroonga + pg_trgm，按需实现）
│       ├── static/             # 静态文件
│       ├── templates/          # HTML 模板
│       ├── utils/              # 辅助工具函数
│       ├── __init__.py         # 应用工厂函数
│       ├── cli.py              # Typer 命令定义模块
│       ├── models.py           # ORM 类定义（import Base from persistence.database）
│       ├── config.py           # 配置文件
│       └── exceptions.py       # 自定义异常类（所有业务异常统一定义于此）
├── data/                       # 数据目录（SQLite 文件，含 .gitkeep）
├── docs/                       # 项目文档
├── logs/                       # 运行日志（含 .gitkeep）
├── scripts/                    # 辅助脚本
├── tests/                      # 测试
│   ├── unit/                   # 单元测试
│   ├── integration/            # 集成测试
│   ├── e2e/                    # 端到端测试
│   ├── fixtures/               # 小样本文档
│   └── conftest.py             # pytest 配置文件
├── .env                        # 环境变量（需从 .env.example 复制创建）
├── .env.example                # 环境变量模板
├── pyproject.toml              # 项目配置与依赖管理，不配置 scripts 入口
├── AGENTS.md                   # 面向 AI Coding Agent 的引导、约束说明文档
├── README.md                   # README
├── doc-cli.py                  # CLI 入口（唯一的 CLI 入口，内部导入 cli 模块）
└── run.py                      # 应用启动入口
```





## 4. 代码质量要求

### 4.1 基本要求

- 命名清晰，并保持一致
- 说明、注释、日志要使用中文
- 编写或修改代码时，必须补充必要的中文注释与文档字符串，重点说明不直观的业务约束、边界条件和关键实现意图，避免无信息量的逐行注释
- 重构代码时，要清理遗留的历史代码



### 4.2 类型注解

- 所有函数必须有完整的参数和返回值类型注解（Python 3.12+ 语法）。
- Service 层的公开方法必须使用 dataclass 或 TypedDict 作为输入/输出类型，不得使用裸 `dict`。
- 禁止使用 `Any` 类型，除非是第三方库返回值的包装层（需加注释说明原因）。



### 4.3 日志规范

```python
import logging
logger = logging.getLogger(__name__)
```

- 日志格式：`"%(asctime)s %(levelname)-8s %(name)s:%(lineno)d %(message)s"`
- 同时写入文件（`logs/app-YYYY-MM-DD.log`，按日期轮转）和控制台。
- 日志级别：INFO 用于正常操作记录，WARNING 用于可恢复异常，ERROR 用于转换失败，DEBUG 用于详细调试信息。
- 禁止在 Service 层使用 `print()`，统一使用 `logger`。



### 4.4 异常处理

- Service 层不得让异常穿透到入口层（CLI / Routes），必须捕获并返回结构化的错误结果或抛出自定义异常。
- 自定义异常定义在 `src/local_document_search/exceptions.py`。
- 每个 Converter 的 `convert()` 方法必须捕获所有异常，失败时返回带 `error_message` 的结果对象，而不是抛出异常。



### 4.5 提交前必须通过的检查

```shell
# 格式化
uv run ruff format .

# Lint
uv run ruff check .

# 类型检查
uv run pyright

# 测试
uv run pytest
```

以上命令全部绿色才可视为完成。





## 5. 测试要求

### 5.1 测试覆盖率目标

- Service 层核心逻辑：≥ 60%（pytest-cov 衡量）
- 关键 CLI 流程：必须有 E2E 测试



### 5.2 测试分类

```
tests/
├── unit/          # 单元测试：单个函数/类，使用 mock 隔离依赖
├── integration/   # 集成测试：Service + 数据库，使用内存 SQLite
├── e2e/           # E2E 测试：通过 CLI 命令驱动，使用真实测试文件
└── fixtures/      # 测试文件：每种支持格式至少一个最小化样本
```



### 5.3 测试约束

- 所有测试必须使用**隔离的临时数据库**（`tmp_path` fixture 或内存 SQLite），不得读写 `data/search.db`。
- E2E 测试必须覆盖：`db init` → `index` → `search` → `errors retry`（失败场景）→ `clean` 。
- 运行全量测试：`uv run pytest`；运行指定测试：`uv run pytest tests/unit/`。





## 6. 运行命令

### 6.1 首次初始化（按顺序执行）

```shell
# 第一步：安装依赖
uv sync

# 第二步：创建环境变量文件
cp .env.example .env
# 根据实际情况编辑 .env，至少确认 SEARCH_DIRS 和 DATABASE_BACKEND 的值

# 第三步：初始化数据库（创建表结构和 FTS5 虚拟表）
uv run python doc-cli.py db init

# 第四步：启动 Web 服务
uv run python run.py
```



### 6.2 CLI 命令完整示例

```shell
# 索引指定目录（增量，已索引文件自动跳过）
uv run python doc-cli.py index /path/to/docs

# 索引多个目录
uv run python doc-cli.py index /path/to/docs1 /path/to/docs2

# 强制重新索引（忽略增量状态）
uv run python doc-cli.py index /path/to/docs --force

# 全文检索
uv run python doc-cli.py search "关键词"

# 限制返回结果数量
uv run python doc-cli.py search "关键词" --limit 20

# 查看索引错误记录
uv run python doc-cli.py errors list

# 重试所有失败文件
uv run python doc-cli.py errors retry

# 重试指定文件
uv run python doc-cli.py errors retry --path /path/to/file.pdf

# 扫描并删除孤儿记录（数据库中有记录但文件已不存在）
uv run python doc-cli.py clean

# 查看孤儿记录但不删除（dry-run）
uv run python doc-cli.py clean --dry-run
```



### 6.3 开发期常用命令

```shell
# 格式化 + Lint + 类型检查 + 测试（提交前必须全部通过）
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest

# 仅运行测试并输出覆盖率报告
uv run pytest --cov=src --cov-report=term-missing

# 仅运行单元测试
uv run pytest tests/unit/

# 仅运行集成测试
uv run pytest tests/integration/

# 仅运行 E2E 测试
uv run pytest tests/e2e/
```



### 6.4 `.env` 关键配置项说明

`.env.example` 包含以下配置项，`.env` 必须从其复制并按实际情况修改：

```dotenv
# 搜索后端选择：sqlite（默认）或 postgresql
DATABASE_BACKEND=sqlite

# SQLite 数据库文件路径（DATABASE_BACKEND=sqlite 时生效）
SQLITE_DB_PATH=data/search.db

# PostgreSQL 连接串（DATABASE_BACKEND=postgresql 时生效）
# DATABASE_URL=postgresql://user:password@localhost:5432/dbname

# 默认索引目录（逗号分隔，doc-cli.py index 不传参数时使用）
SEARCH_DIRS=

# Flask 运行配置
FLASK_HOST=127.0.0.1
FLASK_PORT=5000
FLASK_DEBUG=false

# 日志级别：DEBUG / INFO / WARNING / ERROR
LOG_LEVEL=INFO
```

> **注意：** `.env` 文件包含本地路径等敏感信息，已加入 `.gitignore`，禁止提交。
