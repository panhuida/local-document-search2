# 本地文档搜索助手：Ubuntu 24.04 部署指南

## 快速导航

适合对象：

- 计划在 Ubuntu 24.04 桌面环境中本地运行本项目的使用者
- 计划在 Ubuntu 24.04 服务器或内网机器上长期运行 Web 服务的开发者
- 需要明确 Linux 能力边界的维护者或 AI Agent

建议阅读顺序：

- 只想快速跑通：优先看 `2` 到 `5`
- 需要长期驻留：继续看 `6`
- 需要确认功能边界：看 `1.2` 与 `7`

关联文档：

- 项目总体说明与快速开始：`README.md`
- 日常命令与排障：`docs/engineering/本地文档搜索助手_运维与排障手册.md`
- 架构原则与实现沉淀：`docs/engineering/本地文档搜索助手_实施沉淀与最佳实践.md`

## 1. 适用范围

### 1.1 结论

当前项目可以在 Ubuntu 24.04 上运行，但需要分两层理解：

- 核心链路可用：CLI、SQLite、索引、搜索、Markdown 预览、Web 页面可运行
- 与 Windows 不完全等价：旧版 Office 正文提取和系统文件打开体验会降级

### 1.2 Ubuntu 24.04 下的能力边界

Ubuntu 24.04 上当前支持情况如下：

- 支持：`md`、`txt`、`html`、`htm`、`pdf`、`docx`、`xlsx`、`pptx`、`xmind`、`drawio`
- 支持但仅索引元数据：图片、视频
- 可索引但旧版 Office 正文提取会降级：`doc`、`xls`、`ppt`

需要特别注意：

- 旧版 Office 正文提取当前依赖 Windows 本机 Office / COM 自动化；Ubuntu 24.04 下会自动回退为元数据索引
- “打开文件”“打开所在目录”依赖 `xdg-open` 和桌面环境；在无桌面的服务器环境中，这类操作通常不可用
- `scripts/doc-cli.cmd`、`scripts/doc-cli.ps1` 是 Windows 包装脚本，Ubuntu 24.04 下不适用

## 2. 环境准备

### 2.1 系统依赖

建议先安装以下基础工具：

```bash
sudo apt update
sudo apt install -y curl git xdg-utils
```

说明：

- `xdg-utils` 用于 Web 页面中的“打开文件 / 打开所在目录”
- Ubuntu 24.04 默认自带 Python 3.12；可先执行 `python3 --version` 确认

### 2.2 安装 uv

如果系统尚未安装 `uv`，可执行：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

安装完成后，重新打开终端，确认：

```bash
uv --version
```

## 3. 获取代码并安装依赖

```bash
git clone <your-repo-url> local-document-search
cd local-document-search
uv sync
```

如果你还要运行测试、Playwright 或参与开发：

```bash
uv sync --extra dev
```

## 4. 配置 .env

先复制示例配置：

```bash
cp .env.example .env
```

一个适合 Ubuntu 24.04 的最小配置示例：

```dotenv
DATABASE_BACKEND=sqlite
SQLITE_DB_PATH=/opt/local-document-search/data/search.db
SEARCH_DIRS=/data/documents,/data/notes
FLASK_HOST=127.0.0.1
FLASK_PORT=5000
FLASK_DEBUG=false
LOG_LEVEL=INFO
EXCLUDED_DIR_KEYWORDS=.assets
MARKITDOWN_TIMEOUT_SECONDS=90
LARGE_FILE_THRESHOLD_MB=15
VERY_LARGE_FILE_THRESHOLD_MB=50
LARGE_FILE_INDEX_MODE=metadata
```

说明：

- `SEARCH_DIRS` 使用英文逗号分隔多个目录
- 目录建议写绝对路径
- 如果希望局域网内其他机器访问 Web，可把 `FLASK_HOST` 改成 `0.0.0.0`

## 5. 首次初始化与 smoke test

### 5.1 初始化数据库

```bash
uv run python doc-cli.py db init
```

### 5.2 先做一次 dry-run

```bash
uv run python doc-cli.py index "/data/documents" --dry-run
```

### 5.3 执行一次真实索引

```bash
uv run python doc-cli.py index "/data/documents"
```

### 5.4 验证搜索

```bash
uv run python doc-cli.py search "测试"
```

如果这四步能跑通，说明 Ubuntu 24.04 上的核心链路已经正常。

## 6. 启动方式

### 6.1 快速验证方式

适合本机临时运行或联调：

```bash
uv run python run.py
```

默认访问地址：

- `http://127.0.0.1:5000`

这条方式直接使用 Flask 开发服务器，适合：

- 本机验证
- 内网单用户或低并发使用
- 开发调试

### 6.2 长期驻留方式

如果你希望 Ubuntu 24.04 开机自动运行，可先用 `systemd` 托管当前启动命令。

示例服务文件：

```ini
[Unit]
Description=Local Document Search
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/local-document-search
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/ubuntu/.local/bin/uv run python run.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

保存为：

```bash
sudo tee /etc/systemd/system/local-document-search.service >/dev/null <<'EOF'
[Unit]
Description=Local Document Search
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/local-document-search
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/ubuntu/.local/bin/uv run python run.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
```

启用并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now local-document-search
sudo systemctl status local-document-search
```

说明：

- 上面的 `User`、`WorkingDirectory`、`ExecStart` 必须按你的实际部署路径调整
- 这仍然是 Flask 开发服务器托管方式，更适合内网、单实例、低并发场景
- 如果后续要做更正式的服务化部署，可再引入 Gunicorn 或其他 WSGI Server

## 7. Ubuntu 24.04 下的已知差异

### 7.1 旧版 Office 会回退为元数据索引

当前项目中：

- `doc`
- `xls`
- `ppt`

在 Ubuntu 24.04 下不会做正文提取，而是回退为元数据索引。这是预期行为，不是部署失败。

### 7.2 打开文件依赖桌面环境

Web 页面中的：

- 打开文件
- 打开所在目录

会走 `xdg-open`。因此：

- Ubuntu 桌面版：通常可以使用
- Ubuntu Server 无桌面：通常不可用或无实际效果

### 7.3 Windows 包装脚本不可用

以下脚本只适用于 Windows：

- `scripts/doc-cli.cmd`
- `scripts/doc-cli.ps1`

Ubuntu 24.04 下请直接使用：

```bash
uv run python doc-cli.py --help
uv run python doc-cli.py --help-plain
```

## 8. 常见问题

### 8.1 Web 能打开，但“打开文件”没反应

优先检查：

- 当前是否有桌面环境
- 是否安装了 `xdg-utils`
- 当前进程是否有图形会话权限

### 8.2 旧版 Excel / Word / PowerPoint 没有正文

这是当前实现边界，不是 Ubuntu 24.04 上的异常。旧版 Office 正文提取目前只在 Windows + Microsoft Office / COM 自动化链路下可用。

### 8.3 想让局域网内其他机器访问

可以把 `.env` 中的：

```dotenv
FLASK_HOST=0.0.0.0
```

然后按需要放通防火墙端口，例如 `5000`。

### 8.4 数据目录放在哪里更合适

建议分开：

- 代码目录：`/opt/local-document-search`
- SQLite 数据：`/opt/local-document-search/data/search.db`
- 被索引文档：`/data/documents`、`/data/notes`

这样代码、数据库和业务文档的边界更清楚，后续备份与迁移也更方便。
