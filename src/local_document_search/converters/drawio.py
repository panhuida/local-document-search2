"""draw.io 文件到 Markdown 的转换器。"""

from __future__ import annotations

import logging
from pathlib import Path
from xml.etree import ElementTree

from local_document_search.converters.base import (
    BaseConverter,
    ConversionResult,
    ConversionStatus,
    ConversionType,
)

logger = logging.getLogger(__name__)


class DrawioConverter(BaseConverter):
    """从 draw.io XML 中提取页面与节点文本。"""

    def convert(self, source_path: Path) -> ConversionResult:
        """执行 draw.io 转换，并在失败时返回结构化错误。"""

        try:
            root = ElementTree.fromstring(source_path.read_text(encoding="utf-8"))
            markdown_content = self._render_drawio(root)
            return ConversionResult(
                content_markdown=markdown_content,
                conversion_type=ConversionType.DRAWIO_TO_MD,
                status=ConversionStatus.COMPLETED,
                error_message=None,
            )
        except Exception as exc:
            logger.exception("draw.io 转换失败：%s", source_path)
            return ConversionResult(
                content_markdown=None,
                conversion_type=ConversionType.DRAWIO_TO_MD,
                status=ConversionStatus.FAILED,
                error_message=str(exc),
            )

    def _render_drawio(self, root: ElementTree.Element) -> str:
        """把 draw.io 页面结构转换为 Markdown 列表。"""

        lines: list[str] = []
        for diagram in root.findall(".//diagram"):
            name = diagram.attrib.get("name", "未命名页面")
            lines.append(f"# {name}")
            values = self._extract_values(diagram)
            if len(values) == 0:
                lines.append("- 页面中没有可提取文本")
            else:
                for value in values:
                    lines.append(f"- {value}")
            lines.append("")
        if len(lines) == 0:
            raise RuntimeError("draw.io 文件中未找到 diagram 节点。")
        return "\n".join(lines).strip()

    def _extract_values(self, diagram: ElementTree.Element) -> list[str]:
        """提取 diagram 中所有可见文本节点。"""

        values: list[str] = []
        for element in diagram.iter():
            value = element.attrib.get("value")
            if value and value.strip():
                cleaned = value.replace("&lt;", "<").replace("&gt;", ">").replace("<br>", " ")
                values.append(cleaned.strip())
        return values
