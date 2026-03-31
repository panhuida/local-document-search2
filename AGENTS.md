# AGENTS.md

本文件面向 AI Coding Agent，提供项目引导、开发与测试的约束。**在开始任何编码任务之前，必须完整阅读本文件。**



## 1. 项目概述

- 本项目是**本地运行、隐私优先**的文档索引与搜索工具。
- **CLI 优先**：所有核心能力先落到 CLI / Service，再由 Web UI 复用。





## 2. 技术栈

- Python 3.12
- Flask + SQLAlchemy + Typer
- SQLite（默认）/ PostgreSQL（预留）
- `uv` 管理依赖
- `ruff`、`pyright`、`pytest`





## 3. 项目结构

- `src/local_document_search/services/`：业务逻辑
- `src/local_document_search/routes/`：参数解析、调用 Service、返回页面或响应
- `src/local_document_search/converters/`：文件格式转换
- `src/local_document_search/persistence/`：数据库、Repository、搜索后端
- `tests/unit/`：单元测试
- `tests/integration/`：Service + 数据库集成测试
- `tests/e2e/`：端到端测试
- CLI 唯一入口：`doc-cli.py`
- Web 唯一入口：`run.py`





## 4. 编码约束

- 命名要清晰，前后要保持一致。
- 注释、日志、文档字符串使用中文，变量名、函数名、类名统一使用英文，遵循 PEP 8 命名规范。
- 所有函数必须写参数和返回值类型注解。
- Service 层公开输入/输出必须使用 `dataclass` 或 `TypedDict`，禁止裸 `dict`。
- 禁止使用 `Any`，仅允许在第三方库封装层中例外，并写注释说明原因。
- 统一使用 `logger = logging.getLogger(__name__)`，不要用 `print()`。
- 重构时清理无用旧代码，不要留下死分支。





## 5. 架构约束

- 新业务优先放在 `services/`，`routes/` 只做薄封装。
- 新文件格式支持：先加 `converters/`，再更新 `ConverterFactory`，最后补测试。
- 新搜索后端放在 `persistence/search/`，以确保 Service 层对后端无感知，便于切换 PostgreSQL。
- Converter 的 `convert()` 必须捕获所有异常，失败时返回结构化结果，不向外抛异常。
- Service 层抛出的业务异常必须由 CLI / Routes 转成可读输出，避免直接 500。





## 6. 测试约束

- 所有测试必须使用临时数据库或内存 SQLite，禁止读写 `data/search.db`。
- 关键 CLI 流程必须覆盖：`db init -> index -> search -> errors retry -> clean`。
- 新增或修改功能时，默认补对应测试；如果没补，最终说明里必须写明原因和风险。





## 7. 提交前检查

```shell
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest
```

以上命令全部通过，才算完成。
若同一命令连续失败 3 次仍无法修复，停止执行，输出失败原因和已尝试的方案，等待人工介入。