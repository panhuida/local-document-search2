"""MarkItDown 子进程执行入口。"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    """执行单文件转换，并将结果以 JSON 输出到标准输出。"""

    if len(sys.argv) < 2:
        print(json.dumps({"text_content": None, "error_message": "缺少待转换文件路径。"}))
        return 1

    source_path = Path(sys.argv[1])
    try:
        from markitdown import MarkItDown
    except ImportError:
        print(
            json.dumps(
                {"text_content": None, "error_message": "markitdown 未安装，无法处理该文件类型。"}
            )
        )
        return 1

    try:
        converted = MarkItDown().convert(str(source_path))
        text_content = getattr(converted, "text_content", None)
        if not isinstance(text_content, str) or text_content.strip() == "":
            raise RuntimeError("markitdown 未返回可用的 Markdown 内容。")
        print(json.dumps({"text_content": text_content, "error_message": None}))
        return 0
    except Exception as exc:
        print(json.dumps({"text_content": None, "error_message": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
