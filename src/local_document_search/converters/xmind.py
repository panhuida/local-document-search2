"""XMind 文件到 Markdown 的转换器。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

from local_document_search.converters.base import (
    BaseConverter,
    ConversionResult,
    ConversionStatus,
    ConversionType,
)

logger = logging.getLogger(__name__)


class XMindConverter(BaseConverter):
    """支持解析 XMind 的 JSON 与 XML 两种内容格式。"""

    def convert(self, source_path: Path) -> ConversionResult:
        """执行 XMind 转换，并统一返回结构化结果。"""

        try:
            with ZipFile(source_path) as archive:
                if "content.json" in archive.namelist():
                    content = archive.read("content.json").decode("utf-8")
                    markdown_content = self._convert_json_content(content)
                elif "content.xml" in archive.namelist():
                    content = archive.read("content.xml").decode("utf-8")
                    markdown_content = self._convert_xml_content(content)
                else:
                    raise RuntimeError("未找到 XMind 内容文件。")

            return ConversionResult(
                content_markdown=markdown_content,
                conversion_type=ConversionType.XMIND_TO_MD,
                status=ConversionStatus.COMPLETED,
                error_message=None,
            )
        except Exception as exc:
            logger.exception("XMind 转换失败：%s", source_path)
            return ConversionResult(
                content_markdown=None,
                conversion_type=ConversionType.XMIND_TO_MD,
                status=ConversionStatus.FAILED,
                error_message=str(exc),
            )

    def _convert_json_content(self, content: str) -> str:
        """解析新版 XMind 的 content.json。"""

        loaded = json.loads(content)
        sheets = loaded if isinstance(loaded, list) else []
        lines: list[str] = []
        for sheet in sheets:
            title = sheet.get("title", "未命名画布")
            lines.append(f"# {title}")
            root_topic = sheet.get("rootTopic")
            if isinstance(root_topic, dict):
                lines.extend(self._render_json_topic(root_topic, 2))
            lines.append("")
        return "\n".join(lines).strip()

    def _render_json_topic(self, topic: dict[str, object], level: int) -> list[str]:
        """递归渲染 JSON 主题树。"""

        title = str(topic.get("title", "未命名主题"))
        lines = [f"{'#' * min(level, 6)} {title}"]
        children = topic.get("children")
        if not isinstance(children, dict):
            return lines
        attached = children.get("attached")
        if not isinstance(attached, list):
            return lines
        for child in attached:
            if isinstance(child, dict):
                lines.extend(self._render_json_topic(child, level + 1))
        return lines

    def _convert_xml_content(self, content: str) -> str:
        """解析旧版 XMind 的 content.xml。"""

        root = ElementTree.fromstring(content)
        lines: list[str] = []
        namespace = "{urn:xmind:xmap:xmlns:content:2.0}"
        for sheet in root.findall(f"{namespace}sheet"):
            title_node = sheet.find(f"{namespace}title")
            title = title_node.text if title_node is not None and title_node.text else "未命名画布"
            lines.append(f"# {title}")
            topic_node = sheet.find(f"{namespace}topic")
            if topic_node is not None:
                lines.extend(self._render_xml_topic(topic_node, namespace, 2))
            lines.append("")
        return "\n".join(lines).strip()

    def _render_xml_topic(
        self,
        topic_node: ElementTree.Element,
        namespace: str,
        level: int,
    ) -> list[str]:
        """递归渲染 XML 主题树。"""

        title_node = topic_node.find(f"{namespace}title")
        title = title_node.text if title_node is not None and title_node.text else "未命名主题"
        lines = [f"{'#' * min(level, 6)} {title}"]
        children_node = topic_node.find(f"{namespace}children")
        if children_node is None:
            return lines
        topics_node = children_node.find(f"{namespace}topics")
        if topics_node is None:
            return lines
        for child in topics_node.findall(f"{namespace}topic"):
            lines.extend(self._render_xml_topic(child, namespace, level + 1))
        return lines
