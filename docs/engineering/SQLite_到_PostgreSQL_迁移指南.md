# SQLite 到 PostgreSQL 迁移指南

本文说明如何把现有 SQLite 数据迁移到 PostgreSQL，并切换本地文档搜索助手到 PostgreSQL 后端。

## 迁移范围

- 迁移 `documents`
- 迁移 `ingest_state`
- 不迁移 SQLite FTS 表 `documents_fts`

说明：

- PostgreSQL 端的搜索能力基于 `pg_trgm + ILIKE`
- `db init` 会自动创建 `pg_trgm` 扩展和 trigram 索引

## 前置条件

1. 已准备好可连接的 PostgreSQL 数据库
2. 目标数据库账号具备以下权限：
   - 建表
   - 建索引
   - 创建扩展，或由管理员预先执行 `CREATE EXTENSION IF NOT EXISTS pg_trgm;`
3. 已安装项目依赖：

```powershell
uv sync
```

## 配置方式

复制配置文件：

```powershell
Copy-Item .env.example .env
```

至少确认以下配置：

```env
DATABASE_BACKEND=postgresql
DATABASE_URL=postgresql://user:password@localhost:5432/local_document_search
SQLITE_DB_PATH=data/search.db
```

说明：

- `DATABASE_URL` 用于目标 PostgreSQL
- `SQLITE_DB_PATH` 用于源 SQLite，迁移命令默认读取它

## 推荐迁移步骤

### 1. 备份 SQLite 数据库

```powershell
Copy-Item data\search.db data\search.db.bak
```

### 2. 初始化 PostgreSQL 结构与搜索对象

```powershell
uv run python doc-cli.py db init
```

该命令会自动执行：

- 创建 ORM 表
- 创建 `pg_trgm` 扩展
- 创建 `documents.file_name` 的 trigram GIN 索引
- 创建 `COALESCE(documents.content_markdown, '')` 的 trigram GIN 索引

### 3. 执行迁移命令

默认读取 `.env` 中的 `SQLITE_DB_PATH` 和 `DATABASE_URL`：

```powershell
uv run python doc-cli.py db migrate-to-postgres
```

如果你想显式指定源库和目标库：

```powershell
uv run python doc-cli.py db migrate-to-postgres `
  --sqlite-db-path "D:\study\code\github\local-document-search\data\search.db" `
  --database-url "postgresql://user:password@localhost:5432/local_document_search"
```

### 4. 如目标库已有旧数据，可显式清空后迁移

```powershell
uv run python doc-cli.py db migrate-to-postgres --truncate-target
```

说明：

- 该选项会清空目标 PostgreSQL 中将要迁移的表
- 默认情况下，如果目标表非空，命令会直接报错，避免误覆盖

### 5. 如不需要迁移增量索引状态，可跳过 `ingest_state`

```powershell
uv run python doc-cli.py db migrate-to-postgres --no-include-ingest-state
```

适用场景：

- 只想迁移文档内容和搜索数据
- 不关心历史增量扫描游标
- 准备在 PostgreSQL 上重新执行一次完整索引

## 迁移后校验

可在 PostgreSQL 中执行：

```sql
SELECT COUNT(*) FROM documents;
SELECT COUNT(*) FROM ingest_state;
SELECT extname FROM pg_extension WHERE extname = 'pg_trgm';
```

如果要检查 trigram 索引：

```sql
SELECT indexname
FROM pg_indexes
WHERE tablename = 'documents';
```

## 切换运行

迁移完成后，保持以下配置即可正式切换到 PostgreSQL：

```env
DATABASE_BACKEND=postgresql
DATABASE_URL=postgresql://user:password@localhost:5432/local_document_search
```

然后像平时一样使用：

```powershell
uv run python doc-cli.py search "历史"
uv run python run.py
```

## 回退方案

如果迁移后需要回退：

1. 把 `.env` 中的 `DATABASE_BACKEND` 改回 `sqlite`
2. 确认 `SQLITE_DB_PATH` 指向原始 SQLite 文件
3. 重新运行 CLI 或 Web

## 常见问题

### 1. 提示无法创建 `pg_trgm`

原因：

- 当前数据库账号没有 `CREATE EXTENSION` 权限

处理：

由数据库管理员先执行：

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

### 2. 提示目标表非空

原因：

- 目标 PostgreSQL 里已经有旧数据

处理：

- 手工清空目标表后再迁移，或
- 使用 `--truncate-target`

### 3. 迁移后搜索结果和 SQLite 略有不同

原因：

- SQLite 使用 `FTS5 + LIKE` 路线
- PostgreSQL 当前版本使用 `pg_trgm + ILIKE`

说明：

- 这是当前版本的预期差异
- 后续如引入 PGroonga，再考虑进一步提升召回能力

### 4. 提示 `PostgreSQL text fields cannot contain NUL (0x00) bytes`

原因：

- 旧 SQLite 数据里包含 `\x00`
- PostgreSQL 不允许在 `text` / `varchar` 字段中存储 NUL 字节

当前版本处理方式：

- `db migrate-to-postgres` 会在迁移时自动移除这些 NUL 字节
- 正常索引、重试、写入目录状态时，也会在写库前自动清洗

建议：

- 更新到包含该修复的版本后，重新执行迁移命令
- 如需审计影响范围，可先备份 SQLite 再迁移
