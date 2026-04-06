"""Typer CLI 命令定义与终端输出渲染。"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

import click
import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from typer.main import get_command

from local_document_search import bootstrap_runtime, build_service_container
from local_document_search.config import AppConfig, DatabaseBackend, SearchMode, load_app_config
from local_document_search.persistence.database import initialize_database
from local_document_search.services import (
    CleanRequest,
    CleanResult,
    DatabaseMigrationRequest,
    DatabaseMigrationResult,
    DatabaseMigrationService,
    ErrorListRequest,
    ErrorListResult,
    IndexProgressEvent,
    IndexRequest,
    IndexResult,
    RetryRequest,
    RetryResult,
    SearchRequest,
    SearchResult,
)
from local_document_search.utils import parse_datetime_string_to_aware_utc

console = Console()
app = typer.Typer(help="本地文档搜索助手 CLI", no_args_is_help=True)
db_app = typer.Typer(help="数据库相关命令", no_args_is_help=True)
errors_app = typer.Typer(help="失败/回退记录相关命令", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(errors_app, name="errors")


class OutputFormat(StrEnum):
    """CLI 支持的输出格式。"""

    TABLE = "table"
    JSON = "json"


@app.callback()
def main_callback(
    ctx: typer.Context,
    help_plain: Annotated[
        bool,
        typer.Option(
            "--help-plain",
            help="输出适合 AI Agent 和脚本读取的纯文本帮助。",
            is_eager=True,
        ),
    ] = False,
) -> None:
    """注册 CLI 根级公共选项。"""

    if help_plain:
        typer.echo(render_plain_help(tuple()))
        raise typer.Exit()
    del ctx


def render_plain_help(command_path: tuple[str, ...] = tuple()) -> str:
    """生成适合 AI Agent 与脚本读取的纯文本帮助信息。"""

    root_command = get_command(app)
    resolved_path, target_command = _resolve_plain_help_command(root_command, command_path)
    lines: list[str] = []

    usage = _build_plain_usage(target_command, resolved_path)
    lines.append(f"Usage: {usage}")

    help_text = target_command.help or target_command.short_help
    if help_text:
        lines.append("")
        lines.append(help_text)

    option_lines = _build_plain_option_lines(target_command)
    if option_lines:
        lines.append("")
        lines.append("Options:")
        lines.extend(option_lines)

    if isinstance(target_command, click.Group):
        command_lines = _build_plain_command_lines(target_command)
        if command_lines:
            lines.append("")
            lines.append("Commands:")
            lines.extend(command_lines)

    return "\n".join(lines)


def _resolve_plain_help_command(
    root_command: click.Command,
    command_path: tuple[str, ...],
) -> tuple[tuple[str, ...], click.Command]:
    """按命令路径解析目标 click 命令。"""

    current_command = root_command
    resolved_segments: list[str] = []

    for segment in command_path:
        if not isinstance(current_command, click.Group):
            break
        next_command = current_command.commands.get(segment)
        if next_command is None:
            break
        resolved_segments.append(segment)
        current_command = next_command

    return tuple(resolved_segments), current_command


def _build_plain_usage(command: click.Command, command_path: tuple[str, ...]) -> str:
    """构造稳定的纯文本 usage。"""

    usage_parts = ["doc-cli.py", *command_path]
    option_exists = any(isinstance(parameter, click.Option) for parameter in command.params)
    if option_exists:
        usage_parts.append("[OPTIONS]")

    if isinstance(command, click.Group):
        usage_parts.extend(["COMMAND", "[ARGS]..."])
        return " ".join(usage_parts)

    argument_parts = _build_plain_argument_parts(command)
    usage_parts.extend(argument_parts)
    return " ".join(usage_parts)


def _build_plain_argument_parts(command: click.Command) -> list[str]:
    """提取命令参数中的位置参数占位。"""

    context = click.Context(
        command,
        info_name=command.name,
        terminal_width=120,
        max_content_width=120,
    )
    argument_parts: list[str] = []
    for parameter in command.params:
        if not isinstance(parameter, click.Argument):
            continue
        metavar = parameter.make_metavar(context)
        if parameter.nargs == -1:
            argument_parts.append(f"{metavar}...")
        else:
            argument_parts.append(metavar)
    return argument_parts


def _build_plain_option_lines(command: click.Command) -> list[str]:
    """格式化命令选项列表。"""

    context = click.Context(
        command,
        info_name=command.name,
        terminal_width=120,
        max_content_width=120,
    )
    lines: list[str] = []

    for parameter in command.params:
        if not isinstance(parameter, click.Option):
            continue

        option_tokens = [*parameter.opts, *parameter.secondary_opts]
        metavar = parameter.make_metavar(context)
        if metavar and not parameter.is_flag and metavar != "BOOLEAN":
            option_tokens = [f"{token} {metavar}" for token in option_tokens]
        option_text = ", ".join(option_tokens)
        help_text = parameter.help or ""
        lines.append(f"  {option_text:<32} {help_text}".rstrip())

    return lines


def _build_plain_command_lines(command: click.Group) -> list[str]:
    """格式化子命令列表。"""

    lines: list[str] = []
    for name, subcommand in command.commands.items():
        help_text = subcommand.short_help or subcommand.help or ""
        lines.append(f"  {name:<32} {help_text}".rstrip())
    return lines


def _parse_datetime_option(raw_value: str | None) -> datetime | None:
    """把命令行日期字符串解析为 datetime。"""
    return parse_datetime_string_to_aware_utc(raw_value)


def _serialize_value(value: object) -> object:
    """递归序列化 dataclass、Path 与 datetime，便于输出 JSON。"""

    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _serialize_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple | list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    return value


def _emit_json(payload: object) -> None:
    """以机器可读 JSON 形式输出结果。"""

    typer.echo(json.dumps(_serialize_value(payload), ensure_ascii=False, indent=2))


def _render_index_table(result: IndexResult) -> None:
    """渲染索引结果表格。"""

    table = Table(title="索引预览" if result.dry_run else "索引结果")
    table.add_column("路径")
    table.add_column("状态")
    table.add_column("消息")
    table.add_column("文档 ID", justify="right")
    for item in result.files:
        table.add_row(item.file_path, item.status, item.message, str(item.document_id or "-"))
    console.print(table)
    if result.dry_run:
        console.print(
            f"总计 {result.total_files} 个文件，计划索引 {result.planned}，"
            f"跳过 {result.skipped}，失败 {result.errors}。"
        )
        return
    console.print(
        f"总计 {result.total_files} 个文件，成功 {result.processed}，"
        f"跳过 {result.skipped}，失败 {result.errors}。"
    )


def _render_search_table(result: SearchResult) -> None:
    """渲染检索结果表格。"""

    mode_label = "全文检索" if result.mode is SearchMode.FULLTEXT else "模糊匹配"
    table = Table(title=f"检索结果（{mode_label}，共 {result.total} 条）")
    table.add_column("ID", justify="right")
    table.add_column("文件名")
    table.add_column("类型")
    table.add_column("路径")
    table.add_column("摘要")
    for item in result.hits:
        table.add_row(
            str(item.document_id),
            item.file_name,
            item.file_type or "-",
            item.file_path,
            item.snippet or "-",
        )
    console.print(table)


def _format_database_target(config: AppConfig) -> str:
    """按后端生成适合展示的数据库目标描述。"""

    if config.database_backend is DatabaseBackend.SQLITE:
        return str(config.sqlite_db_path)
    database_url = config.database_url or ""
    parts = urlsplit(database_url)
    if parts.password is None:
        return database_url

    hostname = parts.hostname or ""
    if parts.port is not None:
        hostname = f"{hostname}:{parts.port}"

    username = parts.username or ""
    netloc = f"{username}:***@{hostname}" if username != "" else hostname
    if parts.username is None and parts.hostname is None:
        return database_url
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _render_error_table(result: ErrorListResult) -> None:
    """渲染失败或回退记录表格。"""

    title_prefix = "问题记录" if result.include_fallback else "失败记录"
    table = Table(title=f"{title_prefix}（共 {result.total} 条）")
    table.add_column("ID", justify="right")
    table.add_column("文件名")
    table.add_column("状态")
    table.add_column("路径")
    table.add_column("原因说明" if result.include_fallback else "错误信息")
    for item in result.items:
        table.add_row(
            str(item.document_id),
            item.file_name,
            item.status,
            item.file_path,
            item.error_message or "-",
        )
    console.print(table)


def _render_database_migration_result(result: DatabaseMigrationResult) -> None:
    """渲染 SQLite 到 PostgreSQL 的迁移结果。"""

    console.print(
        f"[green]已迁移 SQLite 到 PostgreSQL[/green] "
        f"{result.source_sqlite_path} -> {result.target_database_target}"
    )
    console.print(
        f"documents：源库 {result.source_documents} 条，迁移 {result.migrated_documents} 条，"
        f"目标现有 {result.target_documents} 条。"
    )
    if result.include_ingest_state:
        console.print(
            f"ingest_state：源库 {result.source_ingest_states} 条，"
            f"迁移 {result.migrated_ingest_states} 条，目标现有 {result.target_ingest_states} 条。"
        )
    else:
        console.print("已按参数跳过 ingest_state 迁移。")
    if result.truncated_target:
        console.print("迁移前已清空目标 PostgreSQL 表。")
    if result.sanitized_document_fields > 0 or result.sanitized_ingest_state_fields > 0:
        console.print(
            "迁移中已自动移除 PostgreSQL 不接受的 NUL 字节："
            f"documents {result.sanitized_document_records} 条记录 / "
            f"{result.sanitized_document_fields} 个字段，"
            f"ingest_state {result.sanitized_ingest_state_records} 条记录 / "
            f"{result.sanitized_ingest_state_fields} 个字段。"
        )


def _render_retry_table(result: RetryResult) -> None:
    """渲染重试结果表格。"""

    table = Table(title=f"{'重试预览' if result.dry_run else '重试结果'}（共 {result.total} 条）")
    table.add_column("ID", justify="right")
    table.add_column("路径")
    table.add_column("状态")
    table.add_column("消息")
    for item in result.items:
        table.add_row(str(item.document_id or "-"), item.file_path, item.status, item.message)
    console.print(table)
    if result.dry_run:
        console.print(f"计划重试 {result.planned} 条，dry-run 未执行实际重试。")
        return
    console.print(f"成功 {result.succeeded}，失败 {result.failed}。")


def _render_clean_table(result: CleanResult) -> None:
    """渲染孤儿记录清理结果表格。"""

    table = Table(title=f"孤儿记录（命中 {result.matched} 条）")
    table.add_column("ID", justify="right")
    table.add_column("文件名")
    table.add_column("路径")
    table.add_column("类型")
    for item in result.items:
        table.add_row(str(item.document_id), item.file_name, item.file_path, item.file_type or "-")
    console.print(table)
    console.print(
        f"扫描 {result.scanned} 条记录，命中 {result.matched} 条，删除 {result.deleted} 条。"
    )


def _handle_exception(exc: Exception) -> None:
    """统一把异常转成 CLI 友好的错误输出。"""

    console.print(f"[red]错误[/red] {exc}")
    raise typer.Exit(code=1) from exc


@contextmanager
def _suppress_console_logs(enabled: bool) -> Iterator[None]:
    """在 quiet 模式下临时压制控制台日志，文件日志仍然保留。"""

    if not enabled:
        yield
        return

    root_logger = logging.getLogger()
    stream_handlers = [
        handler
        for handler in root_logger.handlers
        if isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
    ]
    previous_handler_levels = [handler.level for handler in stream_handlers]

    # 第三方 PDF 解析库的 warning 会淹没 quiet 输出，这里只抑制控制台。
    noisy_loggers = [
        logging.getLogger("pdfminer"),
        logging.getLogger("pdfminer.pdffont"),
    ]
    previous_logger_levels = [logger.level for logger in noisy_loggers]

    try:
        for handler in stream_handlers:
            handler.setLevel(logging.CRITICAL)
        for noisy_logger in noisy_loggers:
            noisy_logger.setLevel(logging.CRITICAL)
        yield
    finally:
        for handler, level in zip(stream_handlers, previous_handler_levels, strict=False):
            handler.setLevel(level)
        for noisy_logger, level in zip(noisy_loggers, previous_logger_levels, strict=False):
            noisy_logger.setLevel(level)


@db_app.command("init")
def db_init(
    output_format: Annotated[OutputFormat, typer.Option("--format")] = OutputFormat.TABLE,
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
    quiet: Annotated[bool, typer.Option("--quiet")] = False,
) -> None:
    """初始化数据库结构与搜索索引对象。"""

    del verbose
    try:
        config = bootstrap_runtime(force_reload=True)
        initialize_database(rebuild_index=True)
        database_target = _format_database_target(config)
        result = {
            "database_backend": config.database_backend.value,
            "postgresql_default_search_mode": (
                config.postgresql_default_search_mode.value
                if config.database_backend is DatabaseBackend.POSTGRESQL
                else None
            ),
            "database_target": database_target,
            "status": "initialized",
        }
        if output_format is OutputFormat.JSON:
            _emit_json(result)
            return
        if not quiet:
            console.print(f"[green]已初始化数据库[/green] {database_target}")
    except Exception as exc:
        _handle_exception(exc)


@db_app.command("migrate-to-postgres")
def db_migrate_to_postgres(
    sqlite_db_path: Annotated[
        Path | None,
        typer.Option("--sqlite-db-path", help="SQLite 源数据库路径，默认读取 SQLITE_DB_PATH。"),
    ] = None,
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", help="PostgreSQL 目标连接串，默认读取 DATABASE_URL。"),
    ] = None,
    include_ingest_state: Annotated[
        bool,
        typer.Option(
            "--include-ingest-state/--no-include-ingest-state",
            help="是否一并迁移 ingest_state 表。",
        ),
    ] = True,
    truncate_target: Annotated[
        bool,
        typer.Option("--truncate-target", help="迁移前清空目标 PostgreSQL 表。"),
    ] = False,
    output_format: Annotated[OutputFormat, typer.Option("--format")] = OutputFormat.TABLE,
) -> None:
    """将 SQLite 中的业务数据迁移到 PostgreSQL。"""

    try:
        config = load_app_config(force_reload=True)
        source_sqlite_path = sqlite_db_path or config.sqlite_db_path
        target_database_url = database_url or config.database_url or ""
        service = DatabaseMigrationService()
        result = service.migrate_sqlite_to_postgresql(
            DatabaseMigrationRequest(
                source_sqlite_path=source_sqlite_path,
                target_database_url=target_database_url,
                include_ingest_state=include_ingest_state,
                truncate_target=truncate_target,
            )
        )
        if output_format is OutputFormat.JSON:
            _emit_json(result)
            return
        _render_database_migration_result(result)
    except Exception as exc:
        _handle_exception(exc)


@app.command("index")
def index_documents(
    paths: Annotated[list[Path] | None, typer.Argument(help="待索引的目录，可传多个")] = None,
    recursive: Annotated[bool, typer.Option("--recursive/--no-recursive")] = True,
    since: Annotated[str | None, typer.Option("--since")] = None,
    file_types: Annotated[list[str] | None, typer.Option("--type")] = None,
    force: Annotated[bool, typer.Option("--force")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    output_format: Annotated[OutputFormat, typer.Option("--format")] = OutputFormat.TABLE,
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
    quiet: Annotated[bool, typer.Option("--quiet")] = False,
) -> None:
    """索引一个或多个目录。"""

    try:
        with _suppress_console_logs(quiet):
            container = build_service_container(force_reload=True)
            progress_messages: list[str] = []

            if quiet:
                result = container.index_service.index_documents(
                    IndexRequest(
                        paths=tuple(paths or []),
                        recursive=recursive,
                        modified_since=_parse_datetime_option(since),
                        file_types=tuple(file_types or []),
                        force=force,
                        dry_run=dry_run,
                    )
                )
            else:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("{task.description}"),
                    transient=True,
                    console=console,
                ) as progress:
                    task_id = progress.add_task(
                        "正在预览索引范围..." if dry_run else "正在索引文档...",
                        total=None,
                    )

                    def on_progress(event: IndexProgressEvent) -> None:
                        """把 Service 的进度事件转换为终端提示。"""

                        progress.update(task_id, description=f"正在处理：{event.file_path}")
                        progress.advance(task_id)
                        if verbose:
                            progress_messages.append(
                                f"[{event.status}] {event.file_path} - {event.message}"
                            )

                    result = container.index_service.index_documents(
                        IndexRequest(
                            paths=tuple(paths or []),
                            recursive=recursive,
                            modified_since=_parse_datetime_option(since),
                            file_types=tuple(file_types or []),
                            force=force,
                            dry_run=dry_run,
                        ),
                        on_progress=on_progress,
                    )

                if verbose and len(progress_messages) > 0:
                    for message in progress_messages:
                        console.print(message)

        if output_format is OutputFormat.JSON:
            _emit_json(result)
            return
        _render_index_table(result)
    except Exception as exc:
        _handle_exception(exc)


@app.command("search")
def search_documents(
    query: Annotated[
        str,
        typer.Argument(help="检索关键词，多个词默认按 AND 匹配，双引号可用于短语查询"),
    ],
    mode: Annotated[
        SearchMode | None,
        typer.Option(
            "--mode",
            help="匹配方式：fulltext=全文检索，fuzzy=模糊匹配；不传时使用当前默认模式。",
        ),
    ] = None,
    limit: Annotated[int, typer.Option("--limit")] = 20,
    offset: Annotated[int, typer.Option("--offset")] = 0,
    output_format: Annotated[OutputFormat, typer.Option("--format")] = OutputFormat.TABLE,
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
    quiet: Annotated[bool, typer.Option("--quiet")] = False,
) -> None:
    """执行本地检索。"""

    del verbose, quiet
    try:
        container = build_service_container(force_reload=True)
        result = container.search_service.search(
            SearchRequest(query=query, limit=limit, offset=offset, mode=mode)
        )
        if output_format is OutputFormat.JSON:
            _emit_json(result)
            return
        _render_search_table(result)
    except Exception as exc:
        _handle_exception(exc)


@errors_app.command("list")
def list_errors(
    name: Annotated[str | None, typer.Option("--name")] = None,
    updated_after: Annotated[str | None, typer.Option("--updated-after")] = None,
    updated_before: Annotated[str | None, typer.Option("--updated-before")] = None,
    include_fallback: Annotated[bool, typer.Option("--include-fallback")] = False,
    output_format: Annotated[OutputFormat, typer.Option("--format")] = OutputFormat.TABLE,
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
    quiet: Annotated[bool, typer.Option("--quiet")] = False,
) -> None:
    """列出失败记录，必要时可一并包含回退记录。"""

    del verbose, quiet
    try:
        container = build_service_container(force_reload=True)
        result = container.error_record_service.list_errors(
            ErrorListRequest(
                file_name_keyword=name,
                updated_after=_parse_datetime_option(updated_after),
                updated_before=_parse_datetime_option(updated_before),
                include_fallback=include_fallback,
            )
        )
        if output_format is OutputFormat.JSON:
            _emit_json(result)
            return
        _render_error_table(result)
    except Exception as exc:
        _handle_exception(exc)


def _run_retry_command(
    document_id: int | None,
    file_path: str | None,
    retry_all_failed: bool,
    include_fallback: bool,
    dry_run: bool,
    output_format: OutputFormat,
) -> None:
    """执行失败记录重试的共享逻辑。"""

    container = build_service_container(force_reload=True)
    result = container.error_record_service.retry_errors(
        RetryRequest(
            document_id=document_id,
            file_path=file_path,
            retry_all_failed=retry_all_failed,
            include_fallback=include_fallback,
            dry_run=dry_run,
        )
    )
    if output_format is OutputFormat.JSON:
        _emit_json(result)
        return
    _render_retry_table(result)


@errors_app.command("retry", help="重试失败记录，必要时可一并处理回退记录，推荐使用这个入口。")
def retry_errors(
    document_id: Annotated[int | None, typer.Option("--id")] = None,
    file_path: Annotated[str | None, typer.Option("--path")] = None,
    all_failed: Annotated[bool, typer.Option("--all-failed")] = False,
    include_fallback: Annotated[bool, typer.Option("--include-fallback")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    output_format: Annotated[OutputFormat, typer.Option("--format")] = OutputFormat.TABLE,
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
    quiet: Annotated[bool, typer.Option("--quiet")] = False,
) -> None:
    """通过 errors 子命令重试失败或回退记录。"""

    del verbose, quiet
    try:
        _run_retry_command(
            document_id,
            file_path,
            all_failed,
            include_fallback,
            dry_run,
            output_format,
        )
    except Exception as exc:
        _handle_exception(exc)


@app.command(
    "retry",
    help="失败/回退记录重试的顶层兼容别名，建议优先使用 `errors retry`。",
)
def retry_alias(
    document_id: Annotated[int | None, typer.Argument(help="失败记录 ID")] = None,
    all_failed: Annotated[bool, typer.Option("--all-failed")] = False,
    include_fallback: Annotated[bool, typer.Option("--include-fallback")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    output_format: Annotated[OutputFormat, typer.Option("--format")] = OutputFormat.TABLE,
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
    quiet: Annotated[bool, typer.Option("--quiet")] = False,
) -> None:
    """提供顶层 retry 兼容别名，建议优先使用 `errors retry`。"""

    del verbose, quiet
    try:
        _run_retry_command(
            document_id,
            None,
            all_failed,
            include_fallback,
            dry_run,
            output_format,
        )
    except Exception as exc:
        _handle_exception(exc)


@app.command("clean")
def clean_documents(
    scope_path: Annotated[Path | None, typer.Argument(help="待扫描的目录，可选")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    file_types: Annotated[list[str] | None, typer.Option("--type")] = None,
    path_keyword: Annotated[str | None, typer.Option("--path-keyword")] = None,
    output_format: Annotated[OutputFormat, typer.Option("--format")] = OutputFormat.TABLE,
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
    quiet: Annotated[bool, typer.Option("--quiet")] = False,
) -> None:
    """扫描并清理孤儿索引记录。"""

    del verbose, quiet
    try:
        container = build_service_container(force_reload=True)
        result = container.clean_service.clean_orphans(
            CleanRequest(
                scope_path=scope_path,
                dry_run=dry_run,
                file_types=tuple(file_types or []),
                path_keyword=path_keyword,
            )
        )
        if output_format is OutputFormat.JSON:
            _emit_json(result)
            return
        _render_clean_table(result)
    except Exception as exc:
        _handle_exception(exc)
