"""索引后台任务服务。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock, Thread
from uuid import uuid4

from local_document_search.exceptions import IndexCancelledError
from local_document_search.services.index_service import (
    IndexFileResult,
    IndexProgressEvent,
    IndexRequest,
    IndexResult,
    IndexService,
)
from local_document_search.utils import utc_now

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IndexTaskSnapshot:
    """索引后台任务的只读快照。"""

    task_id: str
    status: str
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    requested_paths: tuple[str, ...]
    recursive: bool
    modified_since: datetime | None
    file_types: tuple[str, ...]
    force: bool
    current_file_path: str | None
    total_files: int | None
    processed: int
    skipped: int
    errors: int
    error_message: str | None
    files: tuple[IndexFileResult, ...]

    @property
    def handled_files(self) -> int:
        """返回当前已处理完成的文件数。"""

        return self.processed + self.skipped + self.errors

    @property
    def is_active(self) -> bool:
        """返回任务是否仍在进行中。"""

        return self.status in {"queued", "running", "cancelling"}


@dataclass
class _MutableIndexTaskState:
    """后台任务的进程内可变状态。"""

    task_id: str
    request: IndexRequest
    created_at: datetime
    status: str = "queued"
    started_at: datetime | None = None
    ended_at: datetime | None = None
    current_file_path: str | None = None
    total_files: int | None = None
    processed: int = 0
    skipped: int = 0
    errors: int = 0
    error_message: str | None = None
    cancel_requested: bool = False
    files: list[IndexFileResult] = field(default_factory=list)

    def to_snapshot(self) -> IndexTaskSnapshot:
        """将内部状态转换为对外可读快照。"""

        return IndexTaskSnapshot(
            task_id=self.task_id,
            status=self.status,
            created_at=self.created_at,
            started_at=self.started_at,
            ended_at=self.ended_at,
            requested_paths=tuple(str(path) for path in self.request.paths),
            recursive=self.request.recursive,
            modified_since=self.request.modified_since,
            file_types=self.request.file_types,
            force=self.request.force,
            current_file_path=self.current_file_path,
            total_files=self.total_files,
            processed=self.processed,
            skipped=self.skipped,
            errors=self.errors,
            error_message=self.error_message,
            files=tuple(self.files),
        )


class IndexTaskService:
    """以进程内后台线程运行索引任务，并提供任务状态查询。"""

    def __init__(self, index_service: IndexService, *, max_recent_items: int = 120) -> None:
        self._index_service = index_service
        self._max_recent_items = max_recent_items
        self._tasks: dict[str, _MutableIndexTaskState] = {}
        self._lock = Lock()

    def start_task(self, request: IndexRequest) -> IndexTaskSnapshot:
        """启动新的索引后台任务，并立即返回任务快照。"""

        task_id = uuid4().hex
        state = _MutableIndexTaskState(
            task_id=task_id,
            request=request,
            created_at=utc_now(),
        )
        with self._lock:
            self._tasks[task_id] = state

        worker = Thread(
            target=self._run_task,
            args=(task_id,),
            name=f"index-task-{task_id[:8]}",
            daemon=True,
        )
        worker.start()
        return state.to_snapshot()

    def get_task(self, task_id: str) -> IndexTaskSnapshot | None:
        """按任务 ID 查询当前任务快照。"""

        with self._lock:
            state = self._tasks.get(task_id)
            return state.to_snapshot() if state is not None else None

    def cancel_task(self, task_id: str) -> IndexTaskSnapshot | None:
        """请求取消指定任务。"""

        with self._lock:
            state = self._tasks.get(task_id)
            if state is None:
                return None
            if state.status in {"completed", "failed", "cancelled"}:
                return state.to_snapshot()

            state.cancel_requested = True
            state.status = "cancelling"
            state.error_message = "已收到取消请求，当前文件处理完成后将停止索引。"
            return state.to_snapshot()

    def get_latest_task(self) -> IndexTaskSnapshot | None:
        """返回当前活跃任务；如果没有活跃任务，则返回最近一次任务。"""

        with self._lock:
            states = list(self._tasks.values())
            if len(states) == 0:
                return None

            active_states = [
                state for state in states if state.status in {"queued", "running", "cancelling"}
            ]
            if len(active_states) > 0:
                latest_active = max(active_states, key=lambda state: state.created_at)
                return latest_active.to_snapshot()

            latest_state = max(states, key=lambda state: state.created_at)
            return latest_state.to_snapshot()

    def _run_task(self, task_id: str) -> None:
        """在后台线程执行索引任务。"""

        request = self._mark_task_running(task_id)
        if request is None:
            return

        try:
            result = self._index_service.index_documents(
                request,
                on_progress=lambda event: self._record_progress(task_id, event),
                should_cancel=lambda: self._should_cancel(task_id),
            )
        except IndexCancelledError as exc:
            logger.info("后台索引任务已取消：%s", task_id)
            self._mark_task_cancelled(task_id, str(exc))
            return
        except Exception as exc:
            logger.exception("后台索引任务失败：%s", task_id)
            self._mark_task_failed(task_id, str(exc))
            return

        self._mark_task_completed(task_id, result)

    def _mark_task_running(self, task_id: str) -> IndexRequest | None:
        """将任务状态切换为运行中，并返回原始索引请求。"""

        with self._lock:
            state = self._tasks.get(task_id)
            if state is None:
                return None
            if state.cancel_requested:
                state.status = "cancelling"
            else:
                state.status = "running"
            state.started_at = utc_now()
            return state.request

    def _record_progress(self, task_id: str, event: IndexProgressEvent) -> None:
        """记录单文件进度，用于前端轮询展示。"""

        with self._lock:
            state = self._tasks.get(task_id)
            if state is None:
                return

            state.current_file_path = event.file_path
            if event.status == "completed":
                state.processed += 1
            elif event.status == "skipped":
                state.skipped += 1
            elif event.status == "failed":
                state.errors += 1

            state.files.append(
                IndexFileResult(
                    file_path=event.file_path,
                    status=event.status,
                    message=event.message,
                    document_id=None,
                )
            )
            if len(state.files) > self._max_recent_items:
                del state.files[0 : len(state.files) - self._max_recent_items]

    def _should_cancel(self, task_id: str) -> bool:
        """查询任务是否已收到取消请求。"""

        with self._lock:
            state = self._tasks.get(task_id)
            return state.cancel_requested if state is not None else False

    def _mark_task_completed(self, task_id: str, result: IndexResult) -> None:
        """写回任务完成后的最终结果。"""

        with self._lock:
            state = self._tasks.get(task_id)
            if state is None:
                return

            state.status = "completed"
            state.ended_at = utc_now()
            state.current_file_path = None
            state.total_files = result.total_files
            state.processed = result.processed
            state.skipped = result.skipped
            state.errors = result.errors
            state.error_message = None
            state.files = list(result.files)

    def _mark_task_cancelled(self, task_id: str, error_message: str) -> None:
        """写回任务已取消状态。"""

        with self._lock:
            state = self._tasks.get(task_id)
            if state is None:
                return

            state.status = "cancelled"
            state.ended_at = utc_now()
            state.current_file_path = None
            state.error_message = error_message

    def _mark_task_failed(self, task_id: str, error_message: str) -> None:
        """写回任务异常结束状态。"""

        with self._lock:
            state = self._tasks.get(task_id)
            if state is None:
                return

            state.status = "failed"
            state.ended_at = utc_now()
            state.current_file_path = None
            state.error_message = error_message
