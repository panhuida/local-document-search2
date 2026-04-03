"""Flask 开发服务器入口脚本。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"


def _load_app():
    """补齐 src 路径后构建 Flask 应用。"""
    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))

    from local_document_search import create_app

    return create_app()


app = _load_app()


if __name__ == "__main__":
    app.run(
        host=app.config["FLASK_HOST"],
        port=app.config["FLASK_PORT"],
        debug=app.config["FLASK_DEBUG"],
    )
