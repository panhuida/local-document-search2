#!/usr/bin/env bash
set -euo pipefail

# 基于脚本所在目录定位仓库根目录，避免依赖当前工作目录。
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

exec uv run python "$project_root/doc-cli.py" "$@"
