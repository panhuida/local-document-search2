"""基于 MarkItDown 与本地回退逻辑的通用转换器集合。"""

from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from local_document_search.config import LargeFileIndexMode
from local_document_search.converters.base import (
    BaseConverter,
    ConversionResult,
    ConversionStatus,
    ConversionType,
)
from local_document_search.converters.office_com import (
    LegacyOfficeComConversionError,
    LegacyOfficeComUnavailableError,
    convert_legacy_office_to_modern_file,
)

logger = logging.getLogger(__name__)
MARKITDOWN_TIMEOUT_SECONDS = 90


class MarkItDownTimeoutError(RuntimeError):
    """表示 MarkItDown 单文件转换超时。"""


class DirectTextConverter(BaseConverter):
    """直接读取 Markdown / 文本文件。"""

    def convert(self, source_path: Path) -> ConversionResult:
        try:
            content = _read_text_with_fallback(source_path)
            return ConversionResult(
                content_markdown=content,
                conversion_type=ConversionType.DIRECT,
                status=ConversionStatus.COMPLETED,
                error_message=None,
            )
        except Exception as exc:
            logger.error("读取文本文件失败：%s，原因：%s", source_path, exc)
            return ConversionResult(
                content_markdown=None,
                conversion_type=ConversionType.DIRECT,
                status=ConversionStatus.FAILED,
                error_message=str(exc),
            )


class HtmlConverter(BaseConverter):
    """把简单 HTML 抽取为 Markdown 文本。"""

    def convert(self, source_path: Path) -> ConversionResult:
        try:
            raw_content = _read_text_with_fallback(source_path)
            parser = _HtmlMarkdownExtractor()
            parser.feed(raw_content)
            markdown_content = parser.render_markdown()
            return ConversionResult(
                content_markdown=markdown_content,
                conversion_type=ConversionType.HTML_TO_MD,
                status=ConversionStatus.COMPLETED,
                error_message=None,
            )
        except Exception as exc:
            logger.error("HTML 转 Markdown 失败：%s，原因：%s", source_path, exc)
            return ConversionResult(
                content_markdown=None,
                conversion_type=ConversionType.HTML_TO_MD,
                status=ConversionStatus.FAILED,
                error_message=str(exc),
            )


class MediaMetadataConverter(BaseConverter):
    """为图片和视频生成元数据型 Markdown。"""

    def __init__(self, *, is_video: bool) -> None:
        self._is_video = is_video

    def convert(self, source_path: Path) -> ConversionResult:
        try:
            stat = source_path.stat()
            conversion_type = (
                ConversionType.VIDEO_METADATA_TO_MD
                if self._is_video
                else ConversionType.IMAGE_METADATA_TO_MD
            )
            markdown_content = "\n".join(
                [
                    f"# {source_path.name}",
                    "",
                    f"- 文件类型：{source_path.suffix.lower().lstrip('.')}",
                    f"- 文件大小：{stat.st_size} 字节",
                    f"- 创建时间：{_format_timestamp(stat.st_ctime)}",
                    f"- 修改时间：{_format_timestamp(stat.st_mtime)}",
                    "",
                    "当前版本仅索引图片/视频元数据，内容 OCR 与转录留待后续版本。",
                ]
            )
            return ConversionResult(
                content_markdown=markdown_content,
                conversion_type=conversion_type,
                status=ConversionStatus.COMPLETED,
                error_message=None,
            )
        except Exception as exc:
            logger.error("提取媒体元数据失败：%s，原因：%s", source_path, exc)
            conversion_type = (
                ConversionType.VIDEO_METADATA_TO_MD
                if self._is_video
                else ConversionType.IMAGE_METADATA_TO_MD
            )
            return ConversionResult(
                content_markdown=None,
                conversion_type=conversion_type,
                status=ConversionStatus.FAILED,
                error_message=str(exc),
            )


class LegacyBinaryOfficeConverter(BaseConverter):
    """优先通过 Windows Office / COM 自动化提取旧版 Office 正文，失败时回退元数据。"""

    def __init__(
        self,
        *,
        structured_converter: BaseConverter | None = None,
        office_converter: Callable[[Path], AbstractContextManager[Path]] | None = None,
    ) -> None:
        self._structured_converter = structured_converter or MarkItDownConverter()
        self._office_converter = office_converter or convert_legacy_office_to_modern_file

    def convert(self, source_path: Path) -> ConversionResult:
        try:
            with self._office_converter(source_path) as converted_path:
                return self._structured_converter.convert(Path(converted_path))
        except (LegacyOfficeComUnavailableError, LegacyOfficeComConversionError) as exc:
            logger.warning(
                "旧版 Office COM 转换不可用，已回退元数据索引：%s，原因：%s", source_path, exc
            )
            return ConversionResult(
                content_markdown=_build_legacy_office_metadata_fallback(source_path, str(exc)),
                conversion_type=ConversionType.STRUCTURED_TO_MD,
                status=ConversionStatus.COMPLETED,
                error_message=None,
            )
        except Exception as exc:
            logger.error("旧版 Office 处理失败：%s，原因：%s", source_path, exc)
            return ConversionResult(
                content_markdown=None,
                conversion_type=ConversionType.STRUCTURED_TO_MD,
                status=ConversionStatus.FAILED,
                error_message=str(exc),
            )


class MarkItDownConverter(BaseConverter):
    """调用 MarkItDown 处理 PDF / DOCX / PPTX / XLSX。"""

    def __init__(
        self,
        *,
        timeout_seconds: int = MARKITDOWN_TIMEOUT_SECONDS,
        large_file_threshold_mb: int = 15,
        very_large_file_threshold_mb: int = 50,
        large_file_index_mode: LargeFileIndexMode = LargeFileIndexMode.METADATA,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._large_file_threshold_mb = large_file_threshold_mb
        self._very_large_file_threshold_mb = very_large_file_threshold_mb
        self._large_file_index_mode = large_file_index_mode

    def convert(self, source_path: Path) -> ConversionResult:
        try:
            file_size_mebibytes = _get_file_size_mebibytes(source_path)
            if (
                file_size_mebibytes is not None
                and file_size_mebibytes >= self._very_large_file_threshold_mb
                and self._large_file_index_mode is LargeFileIndexMode.METADATA
            ):
                logger.info(
                    "结构化文档命中超大文件策略，改用元数据索引：%s，大小 %.2f MiB",
                    source_path,
                    file_size_mebibytes,
                )
                return ConversionResult(
                    content_markdown=_build_large_file_metadata_fallback(
                        source_path,
                        file_size_mebibytes=file_size_mebibytes,
                        threshold_mb=self._very_large_file_threshold_mb,
                    ),
                    conversion_type=ConversionType.STRUCTURED_TO_MD,
                    status=ConversionStatus.COMPLETED,
                    error_message=None,
                )
            timeout_seconds = _resolve_markitdown_timeout_seconds(
                source_path,
                base_timeout_seconds=self._timeout_seconds,
                large_file_threshold_mb=self._large_file_threshold_mb,
            )
            text_content = _run_markitdown_subprocess(source_path, timeout_seconds)

            return ConversionResult(
                content_markdown=text_content,
                conversion_type=ConversionType.STRUCTURED_TO_MD,
                status=ConversionStatus.COMPLETED,
                error_message=None,
            )
        except MarkItDownTimeoutError as exc:
            error_message = _normalize_markitdown_error(exc)
            logger.error("MarkItDown 转换超时：%s，原因：%s", source_path, error_message)
            return ConversionResult(
                content_markdown=None,
                conversion_type=ConversionType.STRUCTURED_TO_MD,
                status=ConversionStatus.FAILED,
                error_message=error_message,
            )
        except Exception as exc:
            error_message = _normalize_markitdown_error(exc)
            logger.warning(
                "MarkItDown 转换失败，已回退元数据索引：%s，原因：%s", source_path, error_message
            )
            return ConversionResult(
                content_markdown=_build_markitdown_metadata_fallback(source_path, error_message),
                conversion_type=ConversionType.STRUCTURED_TO_MD,
                status=ConversionStatus.COMPLETED,
                error_message=None,
            )


def _read_text_with_fallback(source_path: Path) -> str:
    """按常见编码顺序尝试读取文本文件。"""

    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return source_path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return source_path.read_text(encoding="utf-8", errors="ignore")


def _run_markitdown_subprocess(source_path: Path, timeout_seconds: int) -> str:
    """在独立子进程中调用 MarkItDown，避免单个大文件长期阻塞主索引进程。"""

    command = [
        sys.executable,
        "-X",
        "utf8",
        "-m",
        "local_document_search.converters.markitdown_worker",
        str(source_path),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=False,
            timeout=timeout_seconds,
            check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired as exc:
        raise MarkItDownTimeoutError(
            f"文件转换超时，{timeout_seconds} 秒内未完成，已中止该文件索引。"
        ) from exc

    payload = _parse_markitdown_payload(completed.stdout, completed.stderr)
    error_message = payload.get("error_message")
    if completed.returncode != 0:
        raise RuntimeError(
            error_message
            if isinstance(error_message, str) and error_message.strip()
            else "文件转换失败。"
        )

    text_content = payload.get("text_content")
    if not isinstance(text_content, str) or text_content.strip() == "":
        raise RuntimeError("markitdown 未返回可用的 Markdown 内容。")
    return text_content


def _resolve_markitdown_timeout_seconds(
    source_path: Path,
    *,
    base_timeout_seconds: int,
    large_file_threshold_mb: int,
) -> int:
    """按文件体积动态放宽 MarkItDown 超时，降低大文件被误判超时的概率。"""

    file_size_mebibytes = _get_file_size_mebibytes(source_path)
    if file_size_mebibytes is None or file_size_mebibytes < large_file_threshold_mb:
        return base_timeout_seconds

    max_timeout_seconds = max(base_timeout_seconds, 300)
    estimated_timeout_seconds = 90 + math.ceil(file_size_mebibytes) * 12
    return min(max_timeout_seconds, max(base_timeout_seconds, estimated_timeout_seconds))


def _get_file_size_mebibytes(source_path: Path) -> float | None:
    """读取文件大小并换算为 MiB，失败时返回 None。"""

    try:
        return source_path.stat().st_size / (1024 * 1024)
    except OSError:
        return None


def _parse_markitdown_payload(stdout: bytes, stderr: bytes) -> dict[str, str | None]:
    """解析子进程返回的 JSON 结果。"""

    normalized_stdout = stdout.decode("utf-8", errors="replace").strip()
    normalized_stderr = stderr.decode("utf-8", errors="replace").strip()
    if normalized_stdout == "":
        fallback_error = normalized_stderr or "markitdown 未返回任何输出。"
        return {"text_content": None, "error_message": fallback_error}

    try:
        payload = json.loads(normalized_stdout)
    except json.JSONDecodeError as exc:
        raw_error = normalized_stderr or normalized_stdout
        raise RuntimeError(f"markitdown 子进程输出无法解析：{raw_error}") from exc

    text_content = payload.get("text_content")
    error_message = payload.get("error_message")
    return {
        "text_content": text_content if isinstance(text_content, str) else None,
        "error_message": error_message if isinstance(error_message, str) else None,
    }


def _format_timestamp(timestamp: float) -> str:
    """把文件时间戳格式化为可读字符串。"""

    return datetime.fromtimestamp(timestamp).isoformat(sep=" ", timespec="seconds")


def _build_markitdown_metadata_fallback(source_path: Path, error_message: str) -> str:
    """为正文提取失败的结构化文档生成元数据回退索引。"""

    stat = source_path.stat()
    return "\n".join(
        [
            f"# {source_path.name}",
            "",
            f"- 文件类型：{source_path.suffix.lower().lstrip('.')}",
            f"- 文件大小：{stat.st_size} 字节",
            f"- 创建时间：{_format_timestamp(stat.st_ctime)}",
            f"- 修改时间：{_format_timestamp(stat.st_mtime)}",
            "",
            "当前文件正文提取失败，已回退为元数据索引。",
            f"失败原因：{error_message}",
        ]
    )


def _build_legacy_office_metadata_fallback(source_path: Path, detail_message: str) -> str:
    """为旧版 Office 文档生成带原因说明的元数据索引。"""

    stat = source_path.stat()
    return "\n".join(
        [
            f"# {source_path.name}",
            "",
            f"- 文件类型：{source_path.suffix.lower().lstrip('.')}",
            f"- 文件大小：{stat.st_size} 字节",
            f"- 创建时间：{_format_timestamp(stat.st_ctime)}",
            f"- 修改时间：{_format_timestamp(stat.st_mtime)}",
            "",
            "当前文件属于旧版二进制 Office 格式，本次回退为元数据索引。",
            f"回退原因：{detail_message}",
            "如需正文提取，请确认本机已安装 Microsoft Office 与 pywin32。",
        ]
    )


def _build_large_file_metadata_fallback(
    source_path: Path,
    *,
    file_size_mebibytes: float,
    threshold_mb: int,
) -> str:
    """为命中大文件策略的结构化文档生成元数据索引。"""

    stat = source_path.stat()
    return "\n".join(
        [
            f"# {source_path.name}",
            "",
            f"- 文件类型：{source_path.suffix.lower().lstrip('.')}",
            f"- 文件大小：{stat.st_size} 字节（约 {file_size_mebibytes:.2f} MiB）",
            f"- 创建时间：{_format_timestamp(stat.st_ctime)}",
            f"- 修改时间：{_format_timestamp(stat.st_mtime)}",
            "",
            f"当前文件大小已达到 {threshold_mb} MiB 以上。",
            "为提升大目录索引速度，当前按大文件分级索引策略回退为元数据索引。",
            "如需正文全文检索，可把 LARGE_FILE_INDEX_MODE 调整为 full 后重新索引。",
        ]
    )


def _normalize_markitdown_error(exc: Exception) -> str:
    """把底层库的英文异常归一化为面向用户的中文错误说明。"""

    raw_message = str(exc)
    lowered = raw_message.lower()

    if "markitdown 未安装" in raw_message:
        return "markitdown 未安装，无法处理该文件类型。"
    if "pdfconverter" in lowered and "missingdependencyexception" in lowered:
        return "缺少 PDF 转换依赖，请执行 `uv sync --extra dev` 安装 `markitdown[pdf,pptx]`。"
    if "pptxconverter" in lowered and "missingdependencyexception" in lowered:
        return "缺少 PPTX 转换依赖，请执行 `uv sync --extra dev` 安装 `markitdown[pdf,pptx]`。"
    if "encrypted" in lowered:
        return "文件可能已加密，当前无法提取正文，已跳过。"
    if "unsupportedformatexception" in lowered:
        return "当前环境暂不支持该文件格式的正文提取。"
    if raw_message.strip() == "":
        return "文件转换失败。"
    return raw_message


class _HtmlMarkdownExtractor(HTMLParser):
    """轻量 HTML 文本抽取器，仅保留标题与正文文本。"""

    def __init__(self) -> None:
        super().__init__()
        self._title: str | None = None
        self._in_title = False
        self._lines: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag == "title":
            self._in_title = True
        if tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4"}:
            self._lines.append("")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4"}:
            self._lines.append("")

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if stripped == "":
            return
        if self._in_title and self._title is None:
            self._title = stripped
            return
        self._lines.append(stripped)

    def render_markdown(self) -> str:
        lines = [line for line in self._lines if line.strip()]
        if self._title:
            return f"# {self._title}\n\n" + "\n\n".join(lines)
        return "\n\n".join(lines)
