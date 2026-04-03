"""CLI 入口脚本。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"


def main() -> None:
    """补齐 src 路径后启动 Typer 应用。"""
    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))

    from local_document_search.cli import app, render_plain_help

    if "--help-plain" in sys.argv[1:]:
        plain_argv = tuple(argument for argument in sys.argv[1:] if argument != "--help-plain")
        print(render_plain_help(plain_argv))
        return

    app()


if __name__ == "__main__":
    main()
