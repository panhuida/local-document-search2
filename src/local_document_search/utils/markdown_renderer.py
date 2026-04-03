"""Markdown 渲染工具。"""

from __future__ import annotations

from markdown_it import MarkdownIt


def _build_markdown_renderer() -> MarkdownIt:
    """创建统一的 Markdown 渲染器。"""

    renderer = MarkdownIt(
        "commonmark",
        {
            # 预览页不接受原生 HTML，避免 Markdown 中的任意标签直接注入页面。
            "html": False,
        },
    )
    renderer.enable("table")
    renderer.enable("strikethrough")
    return renderer


_MARKDOWN_RENDERER = _build_markdown_renderer()


def render_markdown_to_html(content_markdown: str) -> str:
    """将 Markdown 文本渲染为 HTML。"""

    return _MARKDOWN_RENDERER.render(content_markdown)
