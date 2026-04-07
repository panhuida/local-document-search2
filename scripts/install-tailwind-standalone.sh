#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tool_directory="$project_root/tools/tailwind"
tailwind_version="v4.1.18"

architecture="$(uname -m)"
case "$architecture" in
  x86_64|amd64)
    binary_name="tailwindcss-linux-x64"
    ;;
  aarch64|arm64)
    binary_name="tailwindcss-linux-arm64"
    ;;
  *)
    echo "当前 Linux 架构不受支持：$architecture" >&2
    exit 1
    ;;
esac

download_url="https://github.com/tailwindlabs/tailwindcss/releases/download/$tailwind_version/$binary_name"
target_path="$tool_directory/$binary_name"

mkdir -p "$tool_directory"

# 使用固定版本，避免多人协作时构建结果随上游最新版本漂移。
if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$download_url" -o "$target_path"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "$target_path" "$download_url"
else
  echo "未找到 curl 或 wget，无法下载 Tailwind standalone CLI。" >&2
  exit 1
fi

chmod +x "$target_path"
echo "已下载 Tailwind standalone CLI 到 $target_path"
