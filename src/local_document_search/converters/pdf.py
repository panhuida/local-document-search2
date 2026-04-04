"""PDF 专用转换器，负责多引擎正文提取与乱码质量判定。"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from local_document_search.config import LargeFileIndexMode
from local_document_search.converters.base import (
    BaseConverter,
    ConversionResult,
    ConversionStatus,
    ConversionType,
)
from local_document_search.converters.markitdown import (
    MarkItDownConverter,
    _build_large_file_metadata_fallback,
    _build_markitdown_metadata_fallback,
    _get_file_size_mebibytes,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PdfTextQuality:
    """单次 PDF 提取结果的质量评估。"""

    score: float
    is_usable: bool
    issues: tuple[str, ...]


@dataclass(frozen=True)
class _PdfCandidate:
    """单个提取引擎产出的候选正文。"""

    engine_name: str
    content_markdown: str
    quality: PdfTextQuality


class PdfConverter(BaseConverter):
    """针对 PDF 组合 PyMuPDF 与 MarkItDown 的正文提取器。"""

    def __init__(
        self,
        *,
        markitdown_converter: MarkItDownConverter,
        very_large_file_threshold_mb: int,
        large_file_index_mode: LargeFileIndexMode,
    ) -> None:
        self._markitdown_converter = markitdown_converter
        self._very_large_file_threshold_mb = very_large_file_threshold_mb
        self._large_file_index_mode = large_file_index_mode

    def convert(self, source_path: Path) -> ConversionResult:
        """按 PDF 专用顺序提取正文，并在疑似乱码时回退。"""

        large_file_result = self._build_large_file_result(source_path)
        if large_file_result is not None:
            return large_file_result

        candidates: list[_PdfCandidate] = []
        error_messages: list[str] = []

        pymupdf_candidate = self._try_extract_with_pymupdf(source_path)
        if pymupdf_candidate is not None:
            candidates.append(pymupdf_candidate)
        else:
            error_messages.append("PyMuPDF 未提取到可用正文。")

        markitdown_result = self._markitdown_converter.convert(source_path)
        if (
            markitdown_result.status is ConversionStatus.COMPLETED
            and markitdown_result.content_markdown is not None
        ):
            markitdown_quality = _analyze_pdf_text_quality(markitdown_result.content_markdown)
            candidates.append(
                _PdfCandidate(
                    engine_name="MarkItDown",
                    content_markdown=markitdown_result.content_markdown,
                    quality=markitdown_quality,
                )
            )
        elif markitdown_result.error_message is not None:
            error_messages.append(f"MarkItDown：{markitdown_result.error_message}")

        usable_candidates = [candidate for candidate in candidates if candidate.quality.is_usable]
        if len(usable_candidates) > 0:
            best_candidate = max(usable_candidates, key=lambda candidate: candidate.quality.score)
            logger.info(
                "PDF 正文提取成功：%s，采用引擎：%s，质量评分：%.3f",
                source_path,
                best_candidate.engine_name,
                best_candidate.quality.score,
            )
            return ConversionResult(
                content_markdown=best_candidate.content_markdown,
                conversion_type=ConversionType.STRUCTURED_TO_MD,
                status=ConversionStatus.COMPLETED,
                error_message=None,
            )

        if len(candidates) > 0:
            quality_message = _build_pdf_quality_message(candidates)
            logger.warning(
                "PDF 正文提取疑似乱码，已回退元数据索引：%s，原因：%s", source_path, quality_message
            )
            return ConversionResult(
                content_markdown=_build_markitdown_metadata_fallback(source_path, quality_message),
                conversion_type=ConversionType.STRUCTURED_TO_MD,
                status=ConversionStatus.FALLBACK,
                error_message=quality_message,
            )

        if markitdown_result.status is ConversionStatus.FAILED:
            return markitdown_result

        combined_error_message = "；".join(error_messages) or "PDF 正文提取失败。"
        logger.warning(
            "PDF 正文提取失败，已回退元数据索引：%s，原因：%s", source_path, combined_error_message
        )
        return ConversionResult(
            content_markdown=_build_markitdown_metadata_fallback(
                source_path, combined_error_message
            ),
            conversion_type=ConversionType.STRUCTURED_TO_MD,
            status=ConversionStatus.FALLBACK,
            error_message=combined_error_message,
        )

    def _build_large_file_result(self, source_path: Path) -> ConversionResult | None:
        """为命中大文件策略的 PDF 直接生成元数据索引。"""

        file_size_mebibytes = _get_file_size_mebibytes(source_path)
        if (
            file_size_mebibytes is None
            or file_size_mebibytes < self._very_large_file_threshold_mb
            or self._large_file_index_mode is not LargeFileIndexMode.METADATA
        ):
            return None

        logger.info(
            "PDF 命中超大文件策略，改用元数据索引：%s，大小 %.2f MiB",
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
            status=ConversionStatus.FALLBACK,
            error_message=(
                f"当前文件大小已达到 {self._very_large_file_threshold_mb} MiB 以上，"
                "按大文件分级索引策略回退为元数据索引。"
            ),
        )

    def _try_extract_with_pymupdf(self, source_path: Path) -> _PdfCandidate | None:
        """尝试使用 PyMuPDF 提取 PDF 正文。"""

        try:
            content_markdown = _extract_pdf_with_pymupdf(source_path)
        except Exception as exc:
            logger.warning("PyMuPDF 提取 PDF 失败：%s，原因：%s", source_path, exc)
            return None
        quality = _analyze_pdf_text_quality(content_markdown)
        return _PdfCandidate(
            engine_name="PyMuPDF",
            content_markdown=content_markdown,
            quality=quality,
        )


def _extract_pdf_with_pymupdf(source_path: Path) -> str:
    """使用 PyMuPDF 提取 PDF 文本，并保留页面边界。"""

    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyMuPDF 未安装，无法处理 PDF 正文提取。") from exc

    page_sections: list[str] = []
    with pymupdf.open(str(source_path)) as document:
        for page_index in range(document.page_count):
            page = document.load_page(page_index)
            page_text_result = page.get_text("text", sort=True)
            if not isinstance(page_text_result, str):
                logger.warning(
                    "PyMuPDF 返回了非文本类型的结果，已跳过该页：%s，第 %s 页",
                    source_path,
                    page_index + 1,
                )
                continue
            page_text = page_text_result.strip()
            if page_text == "":
                continue
            page_sections.append(f"## 第 {page_index + 1} 页\n\n{page_text}")
    content_markdown = "\n\n".join(page_sections).strip()
    if content_markdown == "":
        raise RuntimeError("PyMuPDF 未提取到可用的 PDF 正文。")
    return content_markdown


def _analyze_pdf_text_quality(content_markdown: str) -> PdfTextQuality:
    """基于字符分布判断 PDF 提取结果是否疑似乱码。"""

    normalized_content = content_markdown.replace("\r\n", "\n")
    non_whitespace_characters = [char for char in normalized_content if not char.isspace()]
    total_characters = len(non_whitespace_characters)
    if total_characters == 0:
        return PdfTextQuality(score=0.0, is_usable=False, issues=("未提取到有效文本。",))

    cid_matches = len(re.findall(r"\(cid:\d+\)", normalized_content))
    replacement_count = normalized_content.count("\ufffd")
    null_count = normalized_content.count("\x00")
    control_count = sum(
        1 for char in normalized_content if ord(char) < 32 and char not in {"\n", "\r", "\t"}
    )
    private_use_count = sum(1 for char in normalized_content if 0xE000 <= ord(char) <= 0xF8FF)
    foreign_script_letter_count = sum(
        1
        for char in non_whitespace_characters
        if char.isalpha() and not _is_latin_character(char) and not _is_cjk_character(char)
    )
    readable_count = sum(
        1 for char in non_whitespace_characters if _is_readable_pdf_character(char)
    )

    readable_ratio = readable_count / total_characters
    suspicious_ratio = (
        replacement_count + null_count + control_count + private_use_count + cid_matches * 4
    ) / total_characters
    score = readable_ratio - suspicious_ratio

    issues: list[str] = []
    if total_characters < 20:
        issues.append("提取文本过短。")
    if cid_matches > 0:
        issues.append("包含 cid 字体映射残留。")
    if replacement_count > 0:
        issues.append("包含替换字符。")
    if private_use_count / total_characters >= 0.03:
        issues.append("包含较多私有区字符。")
    if foreign_script_letter_count / total_characters >= 0.15:
        issues.append("包含较多非拉丁/中日韩文字字符。")
    if readable_ratio < 0.45:
        issues.append("可读字符占比偏低。")
    if suspicious_ratio >= 0.12:
        issues.append("异常字符占比偏高。")

    is_usable = (
        total_characters >= 20
        and cid_matches == 0
        and replacement_count == 0
        and control_count == 0
        and readable_ratio >= 0.45
        and suspicious_ratio < 0.12
        and private_use_count / total_characters < 0.03
        and foreign_script_letter_count / total_characters < 0.15
    )
    return PdfTextQuality(score=score, is_usable=is_usable, issues=tuple(issues))


def _is_readable_pdf_character(character: str) -> bool:
    """判断字符是否属于常见可读文本。"""

    return (
        character.isdigit()
        or _is_latin_character(character)
        or character in "，。！？；：、“”‘’（）()【】[]《》<>-—_+*/=,.!?:;'\"%&@#$^`~|\\"
        or _is_cjk_character(character)
    )


def _is_latin_character(character: str) -> bool:
    """判断字符是否属于拉丁字母体系。"""

    if character.isascii() and character.isalpha():
        return True
    character_name = unicodedata.name(character, "")
    return character_name.startswith("LATIN ")


def _is_cjk_character(character: str) -> bool:
    """判断字符是否属于常见中日韩统一表意文字范围。"""

    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
    )


def _build_pdf_quality_message(candidates: list[_PdfCandidate]) -> str:
    """把多引擎质量判断结果格式化为可读错误信息。"""

    parts: list[str] = ["PDF 正文提取结果疑似乱码，已回退为元数据索引。"]
    for candidate in candidates:
        issue_text = "；".join(candidate.quality.issues) or "可读性不足。"
        parts.append(
            f"{candidate.engine_name} 评分 {candidate.quality.score:.3f}，问题：{issue_text}"
        )
    return " ".join(parts)
