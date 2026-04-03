"""Web 路由入口。"""

from __future__ import annotations

from datetime import datetime
from math import ceil
from pathlib import Path
from time import perf_counter

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue

from local_document_search import ServiceContainer
from local_document_search.file_types import SUPPORTED_FILE_EXTENSIONS, build_file_type_groups
from local_document_search.persistence.database import initialize_database
from local_document_search.services import (
    CleanRequest,
    DeleteSelectedOrphansRequest,
    DocumentPreviewRequest,
    ErrorListRequest,
    FileOpenRequest,
    IndexRequest,
    IndexTaskSnapshot,
    RetryRequest,
    SearchRequest,
)
from local_document_search.utils import parse_datetime_string_to_aware_utc

web_blueprint = Blueprint("web", __name__)

_ALL_SUPPORTED_FILE_TYPES = SUPPORTED_FILE_EXTENSIONS


def _services() -> ServiceContainer:
    """从 Flask 扩展中取出服务容器。"""
    services = current_app.extensions.get("services")
    if not isinstance(services, ServiceContainer):
        raise RuntimeError("服务容器未初始化。")
    return services


def _parse_datetime(raw_value: str | None) -> datetime | None:
    """解析表单或查询参数中的 ISO 时间字符串。"""
    return parse_datetime_string_to_aware_utc(raw_value)


def _build_pagination_entries(current_page: int, total_pages: int) -> tuple[int | None, ...]:
    """构造带省略号的分页序列，None 表示省略占位。"""
    if total_pages <= 7:
        return tuple(range(1, total_pages + 1))

    pages = {1, total_pages, current_page - 1, current_page, current_page + 1}
    normalized_pages = sorted(
        page_number for page_number in pages if 1 <= page_number <= total_pages
    )

    entries: list[int | None] = []
    previous_page: int | None = None
    for page_number in normalized_pages:
        if previous_page is not None and page_number - previous_page > 1:
            entries.append(None)
        entries.append(page_number)
        previous_page = page_number
    return tuple(entries)


def _serialize_index_task(task: IndexTaskSnapshot) -> dict[str, object]:
    """把索引任务快照转换为前端轮询使用的 JSON 结构。"""

    return {
        "task_id": task.task_id,
        "status": task.status,
        "created_at": task.created_at.isoformat(),
        "started_at": task.started_at.isoformat() if task.started_at is not None else None,
        "ended_at": task.ended_at.isoformat() if task.ended_at is not None else None,
        "requested_paths": list(task.requested_paths),
        "recursive": task.recursive,
        "modified_since": (
            task.modified_since.isoformat() if task.modified_since is not None else None
        ),
        "file_types": list(task.file_types),
        "force": task.force,
        "current_file_path": task.current_file_path,
        "total_files": task.total_files,
        "processed": task.processed,
        "skipped": task.skipped,
        "errors": task.errors,
        "handled_files": task.handled_files,
        "error_message": task.error_message,
        "is_active": task.is_active,
        "files": [
            {
                "file_path": item.file_path,
                "status": item.status,
                "message": item.message,
                "document_id": item.document_id,
            }
            for item in task.files
        ],
    }


@web_blueprint.route("/")
def home() -> ResponseReturnValue:
    """首页直接跳转到搜索页。"""
    return redirect(url_for("web.search_page"))


@web_blueprint.route("/db/init", methods=["POST"])
def init_database_page() -> ResponseReturnValue:
    """提供手动初始化数据库的 Web 入口。"""
    initialize_database()
    return redirect(url_for("web.index_page", initialized="1"))


@web_blueprint.route("/index", methods=["GET", "POST"])
def index_page() -> ResponseReturnValue:
    """索引页面，提交后启动后台任务并展示进度。"""
    services = _services()
    task: IndexTaskSnapshot | None = None
    error_message = None
    selected_file_types = _ALL_SUPPORTED_FILE_TYPES
    if request.method == "POST":
        try:
            raw_path = request.form.get("path", "").strip()
            raw_file_types = request.form.getlist("file_types")
            selected_file_types = tuple(
                file_type for file_type in raw_file_types if file_type in _ALL_SUPPORTED_FILE_TYPES
            )
            if len(selected_file_types) == 0:
                raise ValueError("请至少选择一种文件类型。")
            task = services.index_task_service.start_task(
                IndexRequest(
                    paths=(Path(raw_path),) if raw_path else tuple(),
                    recursive=request.form.get("recursive") == "on",
                    modified_since=_parse_datetime(request.form.get("modified_since")),
                    file_types=selected_file_types,
                    force=request.form.get("force") == "on",
                )
            )
            return redirect(url_for("web.index_page", task_id=task.task_id))
        except Exception as exc:
            error_message = str(exc)

    task_id = request.args.get("task_id", "").strip()
    if task_id:
        task = services.index_task_service.get_task(task_id)
        if task is None:
            error_message = "索引任务不存在或已过期，请重新提交。"
    else:
        task = services.index_task_service.get_latest_task()

    if request.method != "POST":
        if task is not None and len(task.file_types) > 0:
            selected_file_types = task.file_types
        else:
            selected_file_types = _ALL_SUPPORTED_FILE_TYPES

    return render_template(
        "index.html",
        active_nav="index",
        task=task,
        task_payload=_serialize_index_task(task) if task is not None else None,
        error_message=error_message,
        supported_file_types=_ALL_SUPPORTED_FILE_TYPES,
        file_type_groups=build_file_type_groups(_ALL_SUPPORTED_FILE_TYPES),
        selected_file_types=selected_file_types,
        initialized=request.args.get("initialized") == "1",
    )


@web_blueprint.route("/index/tasks/<task_id>")
def index_task_status(task_id: str) -> ResponseReturnValue:
    """返回索引后台任务的当前状态，供前端轮询。"""

    task = _services().index_task_service.get_task(task_id)
    if task is None:
        return jsonify({"message": "索引任务不存在或已过期。"}), 404
    return jsonify(_serialize_index_task(task))


@web_blueprint.route("/index/tasks/<task_id>/cancel", methods=["POST"])
def cancel_index_task(task_id: str) -> ResponseReturnValue:
    """请求取消后台索引任务。"""

    task = _services().index_task_service.cancel_task(task_id)
    if task is None:
        return jsonify({"message": "索引任务不存在或已过期。"}), 404
    return jsonify(_serialize_index_task(task))


@web_blueprint.route("/search")
def search_page() -> str:
    """搜索页面，展示分页检索结果。"""
    query = request.args.get("q", "").strip()
    page = max(int(request.args.get("page", "1")), 1)
    limit = 10
    offset = (page - 1) * limit
    result = None
    total_pages = 0
    elapsed_ms: int | None = None
    query_terms = tuple(term for term in query.split() if term)

    if query:
        started_at = perf_counter()
        result = _services().search_service.search(
            SearchRequest(query=query, limit=limit, offset=offset)
        )
        elapsed_ms = max(1, round((perf_counter() - started_at) * 1000))
        total_pages = ceil(result.total / limit) if result.total else 0
    pagination_entries = _build_pagination_entries(page, total_pages)

    return render_template(
        "search.html",
        active_nav="search",
        query=query,
        query_terms=query_terms,
        current_page=page,
        total_pages=total_pages,
        pagination_entries=pagination_entries,
        elapsed_ms=elapsed_ms,
        result=result,
    )


@web_blueprint.route("/errors", methods=["GET", "POST"])
def errors_page() -> str:
    """错误记录页面，支持查看和重试失败文档。"""
    services = _services()
    action_result = None
    error_message: str | None = None
    filter_name = request.values.get("name", "").strip()
    updated_after_raw = request.values.get("updated_after", "").strip()
    updated_before_raw = request.values.get("updated_before", "").strip()
    if request.method == "POST":
        action = request.form.get("action", "retry_all")
        if action == "retry_selected":
            selected_document_ids = tuple(
                int(raw_document_id)
                for raw_document_id in request.form.getlist("selected_document_ids")
                if raw_document_id.strip().isdigit()
            )
            if len(selected_document_ids) == 0:
                error_message = "请至少选择一条失败记录。"
            else:
                action_result = services.error_record_service.retry_errors(
                    RetryRequest(document_ids=selected_document_ids)
                )
        else:
            retry_all = request.form.get("retry_all") == "1" or action == "retry_all"
            document_id = request.form.get("document_id")
            file_path = request.form.get("file_path")
            action_result = services.error_record_service.retry_errors(
                RetryRequest(
                    document_id=int(document_id) if document_id else None,
                    file_path=file_path or None,
                    retry_all_failed=retry_all,
                )
            )

    error_list_result = services.error_record_service.list_errors(
        ErrorListRequest(
            file_name_keyword=filter_name or None,
            updated_after=_parse_datetime(updated_after_raw),
            updated_before=_parse_datetime(updated_before_raw),
        )
    )
    return render_template(
        "errors.html",
        active_nav="errors",
        result=error_list_result,
        action_result=action_result,
        error_message=error_message,
        filter_name=filter_name,
        updated_after_value=updated_after_raw,
        updated_before_value=updated_before_raw,
    )


@web_blueprint.route("/clean", methods=["GET", "POST"])
def clean_page() -> str:
    """孤儿记录清理页面。"""
    result = None
    action_message: str | None = None
    error_message: str | None = None
    compare_path = ""
    path_keyword = ""
    selected_file_types: tuple[str, ...] = tuple()
    if request.method == "POST":
        compare_path = request.form.get("path", "").strip()
        path_keyword = request.form.get("path_keyword", "").strip()
        selected_file_types = tuple(
            item for item in request.form.getlist("file_types") if item in _ALL_SUPPORTED_FILE_TYPES
        )
        compare_request = CleanRequest(
            scope_path=Path(compare_path) if compare_path else None,
            dry_run=True,
            file_types=selected_file_types,
            path_keyword=path_keyword or None,
        )
        action = request.form.get("action", "compare")
        services = _services()

        if action == "delete":
            result = services.clean_service.compare_orphans(compare_request)
            selected_document_ids = tuple(
                int(raw_document_id)
                for raw_document_id in request.form.getlist("selected_document_ids")
                if raw_document_id.strip().isdigit()
            )
            if len(selected_document_ids) == 0:
                error_message = "请至少选择一条对比结果。"
            else:
                delete_result = services.clean_service.delete_selected_orphans(
                    DeleteSelectedOrphansRequest(
                        scope_path=compare_request.scope_path,
                        document_ids=selected_document_ids,
                        file_types=compare_request.file_types,
                        path_keyword=compare_request.path_keyword,
                    )
                )
                action_message = f"已删除 {delete_result.deleted} 条记录。"
                result = services.clean_service.compare_orphans(compare_request)
        elif action == "delete_all":
            delete_result = services.clean_service.clean_orphans(
                CleanRequest(
                    scope_path=compare_request.scope_path,
                    dry_run=False,
                    file_types=compare_request.file_types,
                    path_keyword=compare_request.path_keyword,
                )
            )
            action_message = f"已删除 {delete_result.deleted} 条记录。"
            result = services.clean_service.compare_orphans(compare_request)
        else:
            result = services.clean_service.compare_orphans(compare_request)

    return render_template(
        "clean.html",
        active_nav="clean",
        result=result,
        action_message=action_message,
        error_message=error_message,
        compare_path=compare_path,
        path_keyword=path_keyword,
        supported_file_types=_ALL_SUPPORTED_FILE_TYPES,
        file_type_groups=build_file_type_groups(_ALL_SUPPORTED_FILE_TYPES),
        selected_file_types=selected_file_types,
    )


@web_blueprint.route("/documents/<int:document_id>/preview")
def preview_page(document_id: int) -> str:
    """文档预览页面。"""
    result = _services().document_service.preview_document(
        DocumentPreviewRequest(document_id=document_id)
    )
    return render_template("preview.html", active_nav="search", result=result)


@web_blueprint.route("/documents/<int:document_id>/open", methods=["GET", "POST"])
def open_document(document_id: int) -> ResponseReturnValue:
    """触发系统打开文件或父目录；异步请求不跳页，同步请求回到来源页。"""
    open_parent = request.args.get("parent") == "1"
    _services().file_opener_service.open_file(
        FileOpenRequest(document_id=document_id, open_parent_directory=open_parent)
    )
    if request.method == "POST":
        return ("", 204)
    fallback_location = url_for("web.preview_page", document_id=document_id)
    fallback_location = request.referrer or fallback_location
    return redirect(fallback_location)
