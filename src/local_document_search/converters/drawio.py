"""draw.io 文件到 Markdown 的转换器。"""

from __future__ import annotations

import base64
import html
import logging
import re
import zlib
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree

from local_document_search.converters.base import (
    BaseConverter,
    ConversionResult,
    ConversionStatus,
    ConversionType,
)

logger = logging.getLogger(__name__)
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


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

        diagrams = root.findall(".//diagram")
        if len(diagrams) == 0:
            raise RuntimeError("draw.io 文件中未找到 diagram 节点。")

        lines = [
            f"# {root.attrib.get('name', 'draw.io 文档')}",
            "",
            f"总共 {len(diagrams)} 个页面",
            "---",
            "",
        ]
        for diagram in diagrams:
            name = diagram.attrib.get("name", "未命名页面")
            lines.append(f"## {name}")
            values = self._extract_values(diagram)
            if len(values) == 0:
                lines.append("*此页面没有找到文本内容*")
            else:
                lines.extend(f"- {value}" for value in values)
            lines.append("")
        return "\n".join(lines).strip()

    def _extract_values(self, diagram: ElementTree.Element) -> list[str]:
        """提取 diagram 中所有可见文本节点。"""

        xml_root = self._resolve_diagram_root(diagram)
        if xml_root is None:
            return []

        root_element = xml_root.find("root")
        if root_element is None:
            return []

        values: list[str] = []
        for element in root_element:
            element_id = element.attrib.get("id")
            if element_id in {"0", "1"}:
                continue
            value = element.attrib.get("value", "")
            cleaned = self._clean_text_value(value)
            if cleaned:
                values.append(cleaned)
        return values

    def _resolve_diagram_root(self, diagram: ElementTree.Element) -> ElementTree.Element | None:
        """获取页面对应的 `mxGraphModel` 根节点。"""

        embedded_root = diagram.find("mxGraphModel")
        if embedded_root is not None:
            return embedded_root

        diagram_text = (diagram.text or "").strip()
        if not diagram_text:
            return None

        decoded_xml = self._decode_diagram_text(diagram_text)
        if not decoded_xml:
            return None

        try:
            parsed_root = ElementTree.fromstring(decoded_xml)
        except ElementTree.ParseError:
            return None

        if parsed_root.tag == "mxGraphModel":
            return parsed_root
        return parsed_root.find(".//mxGraphModel")

    def _decode_diagram_text(self, data_text: str) -> str:
        """尝试解码 draw.io 页面中压缩存储的 XML 内容。"""

        decode_attempts = (
            self._decode_raw_deflate_base64,
            self._decode_zlib_base64,
            self._decode_base64_text,
            self._decode_raw_text,
        )
        for decode_attempt in decode_attempts:
            decoded_text = decode_attempt(data_text)
            if decoded_text is not None:
                return decoded_text
        return data_text

    def _decode_raw_deflate_base64(self, data_text: str) -> str | None:
        """按 draw.io 常见的 raw deflate + base64 方案解码。"""

        try:
            compressed = base64.b64decode(data_text)
            return unquote(zlib.decompress(compressed, wbits=-15).decode("utf-8"))
        except Exception:
            return None

    def _decode_zlib_base64(self, data_text: str) -> str | None:
        """按标准 zlib + base64 方案解码。"""

        try:
            compressed = base64.b64decode(data_text)
            return unquote(zlib.decompress(compressed).decode("utf-8"))
        except Exception:
            return None

    def _decode_base64_text(self, data_text: str) -> str | None:
        """按普通 base64 文本方案解码。"""

        try:
            return unquote(base64.b64decode(data_text).decode("utf-8"))
        except Exception:
            return None

    def _decode_raw_text(self, data_text: str) -> str | None:
        """按未压缩文本直接 URL 解码。"""

        try:
            return unquote(data_text)
        except Exception:
            return None

    def _clean_text_value(self, text: str) -> str:
        """清洗 draw.io 节点中的 HTML 文本。"""

        if not text.strip():
            return ""
        normalized = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
        normalized = html.unescape(normalized)
        normalized = _HTML_TAG_PATTERN.sub("", normalized)
        return " ".join(normalized.split()).strip()
