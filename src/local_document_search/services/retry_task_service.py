"""错误记录重试后台任务服务。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock, Thread
from uuid import uuid4

from local_document_search.services.error_record_service import (
    ErrorRecordService,
    RetryItemResult,
    RetryRequest,
    RetryResult,
)
from local_document_search.utils import utc_now

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetryTaskSnapshot:
    """错误重试后台任务的只读快照。"""

    task_id: str
    status: str
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    retry_all_failed: bool
    include_fallback: bool
    dry_run: bool
    current_file_path: str | None
    total_items: int | None
    succeeded: int
    failed: int
    error_message: str | None
    items: tuple[RetryItemResult, ...]

    @property
    def handled_items(self) -> int:
        """返回当前已处理完成的记录数。"""

        return self.succeeded + self.failed

    @property
    def is_active(self) -> bool:
        """返回任务是否仍在执行中。"""

        return self.status in {"queued", "running"}


@dataclass
class _MutableRetryTaskState:
    """后台重试任务的进程内可变状态。"""

    task_id: str
    request: RetryRequest
    created_at: datetime
    status: str = "queued"
    started_at: datetime | None = None
    ended_at: datetime | None = None
    current_file_path: str | None = None
    total_items: int | None = None
    succeeded: int = 0
    failed: int = 0
    error_message: str | None = None
    items: list[RetryItemResult] = field(default_factory=list)

    def to_snapshot(self) -> RetryTaskSnapshot:
        """将内部状态转换为对外可读快照。"""

        return RetryTaskSnapshot(
            task_id=self.task_id,
            status=self.status,
            created_at=self.created_at,
            started_at=self.started_at,
            ended_at=self.ended_at,
            retry_all_failed=self.request.retry_all_failed,
            include_fallback=self.request.include_fallback,
            dry_run=self.request.dry_run,
            current_file_path=self.current_file_path,
            total_items=self.total_items,
            succeeded=self.succeeded,
            failed=self.failed,
            error_message=self.error_message,
            items=tuple(self.items),
        )


class RetryTaskService:
    """以进程内后台线程运行错误重试任务，并提供任务状态查询。"""

    def __init__(
        self, error_record_service: ErrorRecordService, *, max_recent_items: int = 120
    ) -> None:
        self._error_record_service = error_record_service
        self._max_recent_items = max_recent_items
        self._tasks: dict[str, _MutableRetryTaskState] = {}
        self._lock = Lock()

    def start_task(self, request: RetryRequest) -> RetryTaskSnapshot:
        """启动新的后台重试任务，并立即返回任务快照。"""

        task_id = uuid4().hex
        state = _MutableRetryTaskState(
            task_id=task_id,
            request=request,
            created_at=utc_now(),
        )
        with self._lock:
            self._tasks[task_id] = state

        worker = Thread(
            target=self._run_task,
            args=(task_id,),
            name=f"retry-task-{task_id[:8]}",
            daemon=True,
        )
        worker.start()
        return state.to_snapshot()

    def get_task(self, task_id: str) -> RetryTaskSnapshot | None:
        """按任务 ID 查询当前任务快照。"""

        with self._lock:
            state = self._tasks.get(task_id)
            return state.to_snapshot() if state is not None else None

    def get_latest_task(self) -> RetryTaskSnapshot | None:
        """返回当前活跃任务；如果没有活跃任务，则返回最近一次任务。"""

        with self._lock:
            states = list(self._tasks.values())
            if len(states) == 0:
                return None

            active_states = [state for state in states if state.status in {"queued", "running"}]
            if len(active_states) > 0:
                latest_active = max(active_states, key=lambda state: state.created_at)
                return latest_active.to_snapshot()

            latest_state = max(states, key=lambda state: state.created_at)
            return latest_state.to_snapshot()

    def _run_task(self, task_id: str) -> None:
        """在后台线程执行重试任务。"""

        request = self._mark_task_running(task_id)
        if request is None:
            return

        try:
            result = self._error_record_service.retry_errors(
                request,
                on_targets_resolved=lambda total: self._record_total(task_id, total),
                on_progress=lambda item: self._record_progress(task_id, item),
            )
        except Exception as exc:
            logger.exception("后台重试任务失败：%s", task_id)
            self._mark_task_failed(task_id, str(exc))
            return

        self._mark_task_completed(task_id, result)

    def _mark_task_running(self, task_id: str) -> RetryRequest | None:
        """将任务状态切换为运行中，并返回原始重试请求。"""

        with self._lock:
            state = self._tasks.get(task_id)
            if state is None:
                return None
            state.status = "running"
            state.started_at = utc_now()
            return state.request

    def _record_total(self, task_id: str, total: int) -> None:
        """记录本次任务命中的总记录数。"""

        with self._lock:
            state = self._tasks.get(task_id)
            if state is None:
                return
            state.total_items = total

    def _record_progress(self, task_id: str, item: RetryItemResult) -> None:
        """记录单条重试进度，用于前端轮询展示。"""

        with self._lock:
            state = self._tasks.get(task_id)
            if state is None:
                return

            state.current_file_path = item.file_path
            if item.status == "completed":
                state.succeeded += 1
            elif item.status in {"failed", "fallback"}:
                state.failed += 1

            state.items.append(item)
            if len(state.items) > self._max_recent_items:
                del state.items[0 : len(state.items) - self._max_recent_items]

    def _mark_task_completed(self, task_id: str, result: RetryResult) -> None:
        """写回任务完成后的最终结果。"""

        with self._lock:
            state = self._tasks.get(task_id)
            if state is None:
                return

            state.status = "completed"
            state.ended_at = utc_now()
            state.current_file_path = None
            state.total_items = result.total
            state.succeeded = result.succeeded
            state.failed = result.failed
            state.error_message = None
            state.items = list(result.items)

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
