#!/usr/bin/env bash
set -euo pipefail

command_name="${1:-build}"
if [[ $# -gt 0 ]]; then
  shift
fi

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
input_path="$project_root/src/local_document_search/static_src/tailwind.css"
output_path="$project_root/src/local_document_search/static/css/tailwind.css"

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

binary_path="$project_root/tools/tailwind/$binary_name"

if [[ ! -f "$binary_path" ]]; then
  echo "未找到 Tailwind standalone CLI：$binary_path。请先运行 scripts/install-tailwind-standalone.sh" >&2
  exit 1
fi

mkdir -p "$(dirname "$output_path")"

case "$command_name" in
  build)
    cli_arguments=(-i "$input_path" -o "$output_path" --minify "$@")
    ;;
  watch)
    cli_arguments=(-i "$input_path" -o "$output_path" --watch=always "$@")
    ;;
  *)
    cli_arguments=("$command_name" "$@")
    ;;
esac

cd "$project_root"
"$binary_path" "${cli_arguments[@]}"
