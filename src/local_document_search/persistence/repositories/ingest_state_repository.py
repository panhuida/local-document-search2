"""索引状态表仓储。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from local_document_search.models import IngestState
from local_document_search.utils import utc_now


@dataclass(frozen=True)
class IngestStateSummary:
    """一次索引任务结束后的汇总信息。"""

    source: str
    scope_key: str
    started_at: datetime
    ended_at: datetime | None
    last_status: str
    last_error_message: str | None
    cursor_updated_at: datetime | None
    total_files: int
    processed: int
    skipped: int
    errors: int


@dataclass(frozen=True)
class IngestStateRunSnapshot:
    """记录本轮任务启动时可用的游标信息。"""

    cursor_updated_at: datetime | None


class IngestStateRepository:
    """封装 ingest_state 表的查询与更新操作。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_source_and_scope(self, source: str, scope_key: str) -> IngestState | None:
        """按来源与范围键查询索引状态。"""
        statement = select(IngestState).where(
            IngestState.source == source,
            IngestState.scope_key == scope_key,
        )
        return self._session.scalars(statement).first()

    def get_id_by_source_and_scope(self, source: str, scope_key: str) -> int | None:
        """按来源与范围键查询索引状态主键，避免加载整行历史数据。"""
        statement = select(IngestState.id).where(
            IngestState.source == source,
            IngestState.scope_key == scope_key,
        )
        return self._session.execute(statement).scalar_one_or_none()

    def peek_run_snapshot(self, source: str, scope_key: str) -> IngestStateRunSnapshot:
        """只读取当前目录可用的增量游标，不修改运行状态。"""

        state = self.get_by_source_and_scope(source, scope_key)
        if state is None:
            return IngestStateRunSnapshot(cursor_updated_at=None)
        return IngestStateRunSnapshot(cursor_updated_at=state.cursor_updated_at)

    def start_run(
        self,
        source: str,
        scope_key: str,
        started_at: datetime,
        *,
        allow_legacy_replace: bool = False,
    ) -> IngestStateRunSnapshot:
        """标记一次新的索引任务开始。"""
        if allow_legacy_replace:
            return self._start_run_without_loading(source, scope_key, started_at)

        state = self.get_by_source_and_scope(source, scope_key)
        if state is None:
            state = IngestState(source=source, scope_key=scope_key)
            self._session.add(state)

        state.last_started_at = started_at
        state.last_ended_at = None
        state.last_error_message = None
        state.last_status = "running"
        state.total_files = 0
        state.processed = 0
        state.skipped = 0
        state.errors = 0
        self._session.flush()
        return IngestStateRunSnapshot(cursor_updated_at=state.cursor_updated_at)

    def finish_run(
        self,
        summary: IngestStateSummary,
        *,
        allow_legacy_replace: bool = False,
    ) -> IngestState:
        """写回一次索引任务的最终统计结果。"""
        if allow_legacy_replace:
            return self._finish_run_without_loading(summary)

        state = self.get_by_source_and_scope(summary.source, summary.scope_key)
        if state is None:
            state = IngestState(source=summary.source, scope_key=summary.scope_key)
            self._session.add(state)

        state.last_started_at = summary.started_at
        state.last_ended_at = summary.ended_at
        state.last_status = summary.last_status
        state.last_error_message = summary.last_error_message
        state.cursor_updated_at = summary.cursor_updated_at
        state.total_files = summary.total_files
        state.processed = summary.processed
        state.skipped = summary.skipped
        state.errors = summary.errors
        self._session.flush()
        return state

    def _start_run_without_loading(
        self,
        source: str,
        scope_key: str,
        started_at: datetime,
    ) -> IngestStateRunSnapshot:
        """在强制重建时直接覆盖旧状态行，避免读取历史脏时间字段。"""

        state_id = self.get_id_by_source_and_scope(source, scope_key)
        current_time = utc_now()
        values = {
            "last_started_at": started_at,
            "last_ended_at": None,
            "last_error_message": None,
            "cursor_updated_at": None,
            "total_files": 0,
            "processed": 0,
            "skipped": 0,
            "errors": 0,
            "last_status": "running",
            # 强制重建路径用于修复旧格式时间数据，直接刷新记录时间。
            "created_at": current_time,
            "updated_at": current_time,
        }

        if state_id is None:
            state = IngestState(source=source, scope_key=scope_key, **values)
            self._session.add(state)
        else:
            self._session.execute(
                update(IngestState).where(IngestState.id == state_id).values(**values)
            )
        self._session.flush()
        return IngestStateRunSnapshot(cursor_updated_at=None)

    def _finish_run_without_loading(self, summary: IngestStateSummary) -> IngestState:
        """在强制重建时直接写回目录状态，避免重新加载旧行。"""

        state_id = self.get_id_by_source_and_scope(summary.source, summary.scope_key)
        current_time = utc_now()
        values = {
            "source": summary.source,
            "scope_key": summary.scope_key,
            "last_started_at": summary.started_at,
            "last_ended_at": summary.ended_at,
            "last_status": summary.last_status,
            "last_error_message": summary.last_error_message,
            "cursor_updated_at": summary.cursor_updated_at,
            "total_files": summary.total_files,
            "processed": summary.processed,
            "skipped": summary.skipped,
            "errors": summary.errors,
            "created_at": current_time,
            "updated_at": current_time,
        }

        if state_id is None:
            state = IngestState(**values)
            self._session.add(state)
            self._session.flush()
            return state

        self._session.execute(
            update(IngestState).where(IngestState.id == state_id).values(**values)
        )
        self._session.flush()
        repaired_state = self.get_by_source_and_scope(summary.source, summary.scope_key)
        if repaired_state is None:
            raise RuntimeError(f"写回索引状态后无法重新读取：{summary.scope_key}")
        return repaired_state
