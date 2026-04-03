"""转换器单元测试。"""

from __future__ import annotations

import base64
import subprocess
import zlib
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

from local_document_search.config import LargeFileIndexMode
from local_document_search.converters import ConverterFactory
from local_document_search.converters.base import (
    BaseConverter,
    ConversionResult,
    ConversionStatus,
    ConversionType,
)
from local_document_search.converters.drawio import DrawioConverter
from local_document_search.converters.markitdown import (
    HtmlConverter,
    LegacyBinaryOfficeConverter,
    MarkItDownConverter,
)
from local_document_search.converters.office_com import (
    LegacyOfficeComConversionError,
    LegacyOfficeComUnavailableError,
    _extract_office_com_detail,
)


def test_converter_factory_returns_expected_converter() -> None:
    """验证工厂能为 Markdown 文件选择文本转换器。"""
    factory = ConverterFactory()
    converter = factory.create_converter(Path("note.md"))
    assert converter.__class__.__name__ == "DirectTextConverter"


def test_converter_factory_supports_flv_and_rejects_markdown_extension() -> None:
    """验证文件类型支持会跟随统一注册表变化。"""

    factory = ConverterFactory()

    assert factory.is_supported(Path("video.flv"))
    assert not factory.is_supported(Path("note.markdown"))


def test_html_converter_extracts_title_and_text(tmp_path: Path) -> None:
    """验证 HTML 转换器会保留标题与正文文本。"""
    html_file = tmp_path / "sample.html"
    html_file.write_text(
        "<html><head><title>测试标题</title></head><body><p>sample body</p></body></html>",
        encoding="utf-8",
    )

    result = HtmlConverter().convert(html_file)

    assert result.status is ConversionStatus.COMPLETED
    assert result.conversion_type is ConversionType.HTML_TO_MD
    assert result.content_markdown is not None
    assert "# 测试标题" in result.content_markdown
    assert "sample body" in result.content_markdown


def test_drawio_converter_extracts_inline_mxgraphmodel_text(tmp_path: Path) -> None:
    """验证 draw.io 转换器可提取内嵌 `mxGraphModel` 的节点文本。"""

    drawio_file = tmp_path / "inline.drawio"
    drawio_file.write_text(
        (
            '<mxfile host="app.diagrams.net" name="inline.drawio">'
            '<diagram id="page-1" name="首页">'
            "<mxGraphModel><root>"
            '<mxCell id="0" />'
            '<mxCell id="1" parent="0" />'
            '<mxCell id="2" value="开始&lt;br&gt;节点" parent="1" vertex="1" />'
            '<mxCell id="3" value="&lt;b&gt;结束&lt;/b&gt;" parent="1" vertex="1" />'
            "</root></mxGraphModel>"
            "</diagram>"
            "</mxfile>"
        ),
        encoding="utf-8",
    )

    result = DrawioConverter().convert(drawio_file)

    assert result.status is ConversionStatus.COMPLETED
    assert result.content_markdown is not None
    assert "## 首页" in result.content_markdown
    assert "- 开始 节点" in result.content_markdown
    assert "- 结束" in result.content_markdown


def test_drawio_converter_extracts_compressed_diagram_text(tmp_path: Path) -> None:
    """验证 draw.io 转换器可解码压缩页面中的文本节点。"""

    compressed_xml = (
        "<mxGraphModel><root>"
        '<mxCell id="0" />'
        '<mxCell id="1" parent="0" />'
        '<mxCell id="2" value="需求入口" parent="1" vertex="1" />'
        '<mxCell id="3" value="&lt;font color=&quot;#333333&quot;&gt;'
        '分省版统计&lt;/font&gt;" parent="1" vertex="1" />'
        "</root></mxGraphModel>"
    )
    compressor = zlib.compressobj(level=9, wbits=-15)
    compressed_bytes = compressor.compress(quote(compressed_xml).encode("utf-8"))
    compressed_bytes += compressor.flush()
    encoded_diagram = base64.b64encode(compressed_bytes).decode("utf-8")

    drawio_file = tmp_path / "compressed.drawio"
    drawio_file.write_text(
        (
            '<mxfile host="Electron" name="compressed.drawio">'
            f'<diagram id="page-1" name="第 1 页">{encoded_diagram}</diagram>'
            "</mxfile>"
        ),
        encoding="utf-8",
    )

    result = DrawioConverter().convert(drawio_file)

    assert result.status is ConversionStatus.COMPLETED
    assert result.content_markdown is not None
    assert "## 第 1 页" in result.content_markdown
    assert "- 需求入口" in result.content_markdown
    assert "- 分省版统计" in result.content_markdown


def test_legacy_binary_office_converter_uses_com_conversion_result(tmp_path: Path) -> None:
    """验证旧版 Office 文件会把 COM 转换后的现代文件交给正文提取链路。"""

    ppt_file = tmp_path / "legacy.ppt"
    ppt_file.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    converted_file = tmp_path / "legacy.pptx"
    converted_file.write_bytes(b"pptx")

    class FakeStructuredConverter(BaseConverter):
        """记录接收到的转换后文件路径，便于断言调用链路。"""

        def __init__(self) -> None:
            self.received_path: Path | None = None

        def convert(self, source_path: Path) -> ConversionResult:
            self.received_path = source_path
            return ConversionResult(
                content_markdown="# converted",
                conversion_type=ConversionType.STRUCTURED_TO_MD,
                status=ConversionStatus.COMPLETED,
                error_message=None,
            )

    @contextmanager
    def fake_office_converter(source_path: Path):
        """模拟 COM 成功输出现代格式临时文件。"""

        assert source_path == ppt_file
        yield converted_file

    structured_converter = FakeStructuredConverter()
    result = LegacyBinaryOfficeConverter(
        structured_converter=structured_converter,
        office_converter=fake_office_converter,
    ).convert(ppt_file)

    assert result.status is ConversionStatus.COMPLETED
    assert result.content_markdown == "# converted"
    assert structured_converter.received_path == converted_file


def test_legacy_binary_office_converter_falls_back_to_metadata(tmp_path: Path) -> None:
    """验证旧版 Office 无法使用 COM 时会回退到元数据索引。"""

    ppt_file = tmp_path / "legacy.ppt"
    ppt_file.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")

    @contextmanager
    def fake_office_converter(source_path: Path):
        """模拟当前环境无法使用 Office / COM 自动化。"""

        del source_path
        raise LegacyOfficeComUnavailableError("当前环境未安装 pywin32。")
        yield  # pragma: no cover

    result = LegacyBinaryOfficeConverter(office_converter=fake_office_converter).convert(ppt_file)

    assert result.status is ConversionStatus.FALLBACK
    assert result.conversion_type is ConversionType.STRUCTURED_TO_MD
    assert result.content_markdown is not None
    assert result.error_message is not None
    assert "旧版二进制 Office 格式" in result.content_markdown
    assert "Office 中手工正常打开" in result.content_markdown


def test_legacy_binary_office_converter_preserves_specific_com_failure_reason(
    tmp_path: Path,
) -> None:
    """验证旧版 Office 回退文案会保留具体的 COM 失败原因。"""

    xls_file = tmp_path / "legacy.xls"
    xls_file.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")

    @contextmanager
    def fake_office_converter(source_path: Path):
        """模拟 Excel COM 拒绝打开文件。"""

        del source_path
        raise LegacyOfficeComConversionError(
            "调用 Excel / COM 转换旧版表格失败。 详细原因："
            "Office 检测到此文件存在一个问题。要帮助保护您的计算机，不能打开此文件。"
        )
        yield  # pragma: no cover

    result = LegacyBinaryOfficeConverter(office_converter=fake_office_converter).convert(xls_file)

    assert result.status is ConversionStatus.FALLBACK
    assert result.content_markdown is not None
    assert result.error_message is not None
    assert "不能打开此文件" in result.content_markdown
    assert "Office 中手工正常打开" in result.content_markdown


def test_extract_office_com_detail_returns_inner_com_message() -> None:
    """验证 COM 异常会优先提取 Office 返回的内部错误信息。"""

    exc = Exception(
        -2147352567,
        "发生意外。",
        (
            0,
            "Microsoft Excel",
            "Office 检测到此文件存在一个问题。要帮助保护您的计算机，不能打开此文件。",
            "xlmain11.chm",
            0,
            -2146827284,
        ),
        None,
    )

    assert _extract_office_com_detail(exc) == (
        "Office 检测到此文件存在一个问题。要帮助保护您的计算机，不能打开此文件。"
    )


def test_markitdown_converter_reads_subprocess_payload(tmp_path: Path, monkeypatch) -> None:
    """验证 MarkItDown 转换器会读取子进程返回的 Markdown。"""

    pdf_file = tmp_path / "sample.pdf"
    pdf_file.write_bytes(b"%PDF-1.4")

    def fake_run(*args, **kwargs) -> subprocess.CompletedProcess[bytes]:
        del args, kwargs
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=b'{"text_content":"# sample","error_message":null}',
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = MarkItDownConverter().convert(pdf_file)

    assert result.status is ConversionStatus.COMPLETED
    assert result.content_markdown == "# sample"


def test_markitdown_converter_expands_timeout_for_large_files(tmp_path: Path, monkeypatch) -> None:
    """验证大体积结构化文档会自动获得更长的转换超时。"""

    pdf_file = tmp_path / "large.pdf"
    pdf_file.write_bytes(b"%PDF-1.4" + b"0" * (18 * 1024 * 1024))
    observed_timeout: int | None = None

    def fake_run(*args, **kwargs) -> subprocess.CompletedProcess[bytes]:
        del args
        nonlocal observed_timeout
        observed_timeout = kwargs.get("timeout")
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=b'{"text_content":"# sample","error_message":null}',
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = MarkItDownConverter().convert(pdf_file)

    assert result.status is ConversionStatus.COMPLETED
    assert observed_timeout == 300


def test_markitdown_converter_uses_metadata_index_for_very_large_files(
    tmp_path: Path, monkeypatch
) -> None:
    """验证超大结构化文档可按策略直接走元数据索引。"""

    pdf_file = tmp_path / "huge.pdf"
    pdf_file.write_bytes(b"%PDF-1.4" + b"0" * (2 * 1024 * 1024))

    def fake_run(*args, **kwargs) -> subprocess.CompletedProcess[bytes]:
        del args, kwargs
        raise AssertionError("命中元数据策略后不应再调用 MarkItDown 子进程。")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = MarkItDownConverter(
        large_file_threshold_mb=1,
        very_large_file_threshold_mb=1,
        large_file_index_mode=LargeFileIndexMode.METADATA,
    ).convert(pdf_file)

    assert result.status is ConversionStatus.FALLBACK
    assert result.content_markdown is not None
    assert result.error_message is not None
    assert "大文件分级索引策略" in result.content_markdown
    assert "LARGE_FILE_INDEX_MODE" in result.content_markdown


def test_markitdown_converter_falls_back_to_metadata_on_timeout(
    tmp_path: Path, monkeypatch
) -> None:
    """验证子进程超时时会记录为失败，便于后续重试。"""

    pdf_file = tmp_path / "slow.pdf"
    pdf_file.write_bytes(b"%PDF-1.4")

    def fake_run(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        raise subprocess.TimeoutExpired(cmd=["python"], timeout=90)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = MarkItDownConverter().convert(pdf_file)

    assert result.status is ConversionStatus.FAILED
    assert result.content_markdown is None
    assert result.error_message is not None
    assert "转换超时" in result.error_message


def test_markitdown_converter_falls_back_to_metadata_on_general_failure(
    tmp_path: Path, monkeypatch
) -> None:
    """验证非超时失败仍会回退为元数据索引。"""

    pdf_file = tmp_path / "broken.pdf"
    pdf_file.write_bytes(b"%PDF-1.4")

    def fake_run(*args, **kwargs) -> subprocess.CompletedProcess[bytes]:
        del args, kwargs
        return subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout=b'{"text_content":null,"error_message":"PdfConverter threw PSEOF"}',
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = MarkItDownConverter().convert(pdf_file)

    assert result.status is ConversionStatus.FALLBACK
    assert result.content_markdown is not None
    assert result.error_message is not None
    assert "回退为元数据索引" in result.content_markdown
    assert "PSEOF" in result.content_markdown
