"""通过 Windows 本机 Office / COM 自动化转换旧版 Office 文档。"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


class LegacyOfficeComUnavailableError(RuntimeError):
    """表示当前环境无法使用 Windows Office / COM 自动化。"""


class LegacyOfficeComConversionError(RuntimeError):
    """表示旧版 Office 文档转换过程中发生错误。"""


@dataclass(frozen=True)
class _LegacyOfficeComDefinition:
    """描述单种旧版 Office 格式的 COM 转换参数。"""

    application: str
    target_extension: str
    save_format: int


_LEGACY_OFFICE_COM_DEFINITIONS: dict[str, _LegacyOfficeComDefinition] = {
    "doc": _LegacyOfficeComDefinition(
        application="word",
        target_extension=".docx",
        save_format=12,
    ),
    "xls": _LegacyOfficeComDefinition(
        application="excel",
        target_extension=".xlsx",
        save_format=51,
    ),
    "ppt": _LegacyOfficeComDefinition(
        application="powerpoint",
        target_extension=".pptx",
        save_format=24,
    ),
}

# COM 动态分发对象来自第三方库，静态类型信息不完整，这里仅在包装层使用 Any。
type _ComObject = Any


@contextmanager
def convert_legacy_office_to_modern_file(source_path: Path) -> Iterator[Path]:
    """把旧版 Office 文档转换为现代格式临时文件，并在退出时自动清理。"""

    definition = _LEGACY_OFFICE_COM_DEFINITIONS.get(source_path.suffix.lower().lstrip("."))
    if definition is None:
        raise LegacyOfficeComConversionError(f"不支持的旧版 Office 类型：{source_path.suffix}")

    if sys.platform != "win32":
        raise LegacyOfficeComUnavailableError("当前仅支持在 Windows 上使用 Office / COM 自动化。")

    try:
        import pythoncom
        from win32com import client as win32_client
    except ModuleNotFoundError as exc:
        raise LegacyOfficeComUnavailableError(
            "当前环境未安装 pywin32，无法使用 Windows 本机 Office / COM 自动化。"
        ) from exc

    temporary_directory = TemporaryDirectory(prefix="legacy-office-")
    try:
        target_path = (
            Path(temporary_directory.name) / f"{source_path.stem}{definition.target_extension}"
        )
        _convert_with_office_com(
            source_path=source_path.resolve(),
            target_path=target_path,
            definition=definition,
            pythoncom_module=pythoncom,
            win32_client_module=win32_client,
        )
        yield target_path
    finally:
        temporary_directory.cleanup()


def _convert_with_office_com(
    *,
    source_path: Path,
    target_path: Path,
    definition: _LegacyOfficeComDefinition,
    pythoncom_module: _ComObject,
    win32_client_module: _ComObject,
) -> None:
    """按文件类型选择对应的 Office 程序完成转换。"""

    pythoncom = pythoncom_module
    try:
        pythoncom.CoInitialize()
    except Exception as exc:
        raise LegacyOfficeComUnavailableError("初始化 COM 环境失败。") from exc

    try:
        if definition.application == "word":
            _convert_with_word(source_path, target_path, definition, win32_client_module)
        elif definition.application == "excel":
            _convert_with_excel(source_path, target_path, definition, win32_client_module)
        elif definition.application == "powerpoint":
            _convert_with_powerpoint(source_path, target_path, definition, win32_client_module)
        else:
            raise LegacyOfficeComConversionError(
                f"未实现的 Office COM 转换类型：{definition.application}"
            )
    finally:
        pythoncom.CoUninitialize()

    if not target_path.exists():
        raise LegacyOfficeComConversionError("Office 已执行转换，但未生成目标文件。")


def _convert_with_word(
    source_path: Path,
    target_path: Path,
    definition: _LegacyOfficeComDefinition,
    win32_client_module: _ComObject,
) -> None:
    """使用 Word COM 把 `.doc` 转换为 `.docx`。"""

    word_application: _ComObject | None = None
    word_document: _ComObject | None = None
    try:
        dispatch_ex = win32_client_module.DispatchEx
        word_application_instance = dispatch_ex("Word.Application")
        word_application = word_application_instance
        word_application_instance.Visible = False
        word_application_instance.DisplayAlerts = 0
        word_document_instance = word_application_instance.Documents.Open(
            str(source_path),
            ConfirmConversions=False,
            ReadOnly=True,
            AddToRecentFiles=False,
        )
        word_document = word_document_instance
        word_document_instance.SaveAs2(str(target_path), FileFormat=definition.save_format)
    except Exception as exc:
        raise LegacyOfficeComConversionError(
            "调用 Word / COM 转换旧版文档失败，请确认本机已安装可用的 Microsoft Word。"
        ) from exc
    finally:
        if word_document is not None:
            try:
                word_document.Close(False)
            except Exception:
                pass
        if word_application is not None:
            try:
                word_application.Quit()
            except Exception:
                pass


def _convert_with_excel(
    source_path: Path,
    target_path: Path,
    definition: _LegacyOfficeComDefinition,
    win32_client_module: _ComObject,
) -> None:
    """使用 Excel COM 把 `.xls` 转换为 `.xlsx`。"""

    excel_application: _ComObject | None = None
    workbook: _ComObject | None = None
    try:
        dispatch_ex = win32_client_module.DispatchEx
        excel_application_instance = dispatch_ex("Excel.Application")
        excel_application = excel_application_instance
        excel_application_instance.Visible = False
        excel_application_instance.DisplayAlerts = False
        workbook_instance = excel_application_instance.Workbooks.Open(
            str(source_path), ReadOnly=True
        )
        workbook = workbook_instance
        workbook_instance.SaveAs(str(target_path), FileFormat=definition.save_format)
    except Exception as exc:
        raise LegacyOfficeComConversionError(
            "调用 Excel / COM 转换旧版表格失败，请确认本机已安装可用的 Microsoft Excel。"
        ) from exc
    finally:
        if workbook is not None:
            try:
                workbook.Close(False)
            except Exception:
                pass
        if excel_application is not None:
            try:
                excel_application.Quit()
            except Exception:
                pass


def _convert_with_powerpoint(
    source_path: Path,
    target_path: Path,
    definition: _LegacyOfficeComDefinition,
    win32_client_module: _ComObject,
) -> None:
    """使用 PowerPoint COM 把 `.ppt` 转换为 `.pptx`。"""

    powerpoint_application: _ComObject | None = None
    presentation: _ComObject | None = None
    try:
        dispatch_ex = win32_client_module.DispatchEx
        powerpoint_application_instance = dispatch_ex("PowerPoint.Application")
        powerpoint_application = powerpoint_application_instance
        presentation_instance = powerpoint_application_instance.Presentations.Open(
            str(source_path),
            ReadOnly=True,
            Untitled=False,
            WithWindow=False,
        )
        presentation = presentation_instance
        presentation_instance.SaveAs(str(target_path), definition.save_format)
    except Exception as exc:
        raise LegacyOfficeComConversionError(
            "调用 PowerPoint / COM 转换旧版演示文稿失败，"
            "请确认本机已安装可用的 Microsoft PowerPoint。"
        ) from exc
    finally:
        if presentation is not None:
            try:
                presentation.Close()
            except Exception:
                pass
        if powerpoint_application is not None:
            try:
                powerpoint_application.Quit()
            except Exception:
                pass
