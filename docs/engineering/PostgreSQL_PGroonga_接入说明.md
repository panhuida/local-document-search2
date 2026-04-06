# PostgreSQL PGroonga 接入说明

本文说明如何在当前项目中启用 PostgreSQL 的 PGroonga 全文检索模式，以改善中文全文检索性能与召回。


## 1. 适用范围

- 数据库后端为 PostgreSQL
- 搜索实现希望从 `pg_trgm` 切换到 `pgroonga`
- 主要关注中文或多语言全文检索


## 2. 安装 PGroonga

先在 PostgreSQL 所在环境安装 PGroonga 扩展。

官方文档：

- https://pgroonga.github.io/install/
- https://pgroonga.github.io/install/windows.html

安装完成后，确保目标数据库能够执行：

```sql
CREATE EXTENSION IF NOT EXISTS pgroonga;
```

如果应用账号没有创建扩展的权限，请先由管理员执行一次。


## 3. 配置项目

在 `.env` 中至少设置以下参数：

```env
DATABASE_BACKEND=postgresql
POSTGRESQL_DEFAULT_SEARCH_MODE=fulltext
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/local_document_search
```

说明：

- `POSTGRESQL_DEFAULT_SEARCH_MODE=fulltext` 表示 PostgreSQL 默认使用 PGroonga
- `POSTGRESQL_DEFAULT_SEARCH_MODE=fuzzy` 表示 PostgreSQL 默认使用 pg_trgm
- 当前实现会同时创建 PGroonga 与 pg_trgm 两套搜索对象，Web 和 CLI 都可以按请求切换，不需要通过修改配置重启应用来切换匹配方式


## 4. 初始化搜索对象

执行：

```powershell
uv run python doc-cli.py db init
```

当前实现会自动创建：

- `pg_trgm` 扩展
- `pgroonga` 扩展
- `idx_documents_file_name_trgm`、`idx_documents_content_markdown_trgm` 模糊匹配索引
- `idx_documents_search_pgroonga` 全文索引

索引目标是：

- `file_name`
- `content_markdown`

并只覆盖 `status in ('completed', 'fallback')` 的记录。


## 5. 验证是否已切换到 PGroonga

可先确认扩展已安装：

```sql
SELECT extname FROM pg_extension WHERE extname = 'pgroonga';
```

再确认索引已创建：

```sql
SELECT indexname
FROM pg_indexes
WHERE schemaname = current_schema()
  AND indexname = 'idx_documents_search_pgroonga';
```

如果以上两条都能返回结果，说明 PGroonga 初始化已完成。


## 6. 与当前实现的关系

当前项目中：

- SQLite 仍然使用 `FTS5 + LIKE` 回退
- PostgreSQL 的 `模糊匹配` 模式仍然保留，方便兼容或对比
- PostgreSQL 的 `全文检索` 模式使用真正的全文索引，不再以 `ILIKE` 为主路径

PGroonga 模式下，搜索逻辑会对：

- `file_name`
- `content_markdown`

做联合全文检索，并提高 `file_name` 的权重。


## 7. 从 pg_trgm 回滚

如果你需要把默认模式改回模糊匹配，只要把 `.env` 改成：

```env
POSTGRESQL_DEFAULT_SEARCH_MODE=fuzzy
```

然后重新执行：

```powershell
uv run python doc-cli.py db init
```

说明：

- 当前实现不会自动删除已有的 PGroonga 索引
- 改回 `fuzzy` 后，应用默认走 pg_trgm 查询路径
- 如果后续确认不再需要 PGroonga，可再手动清理相关扩展与索引


## 8. 已知限制

- 这一版只把 PostgreSQL 主检索切到 PGroonga，搜索摘要仍复用应用侧 `make_snippet()`
- 当前用户查询语义仍保持项目自己的“空格分词 + AND”，没有直接开放 PGroonga 原生高级 DSL
- 如果要进一步提升结果展示质量，可后续再接 `pgroonga_highlight_html` 或 `pgroonga_snippet_html`
