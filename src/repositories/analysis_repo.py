# -*- coding: utf-8 -*-
"""Owner-bound persistence facade for analysis histories and snapshots."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.analysis_ownership import AnalysisOwner
from src.storage import AnalysisHistory, DatabaseManager

logger = logging.getLogger(__name__)


class AnalysisRepository:
    """Expose analysis persistence through an optional trusted owner boundary.

    An unbound repository is retained solely for established CLI/direct-call
    compatibility. User-facing application paths bind a validated
    :class:`AnalysisOwner` at composition time, so callers cannot accidentally
    mix a read owner with a different write owner.
    """

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        *,
        owner: Optional[AnalysisOwner] = None,
    ) -> None:
        self.db = db_manager or DatabaseManager.get_instance()
        self.owner = owner

    def _owner_kwargs(self) -> Dict[str, Any]:
        return dict(self.owner.storage_kwargs) if self.owner is not None else {}

    def get_by_query_id(self, query_id: str) -> Optional[AnalysisHistory]:
        try:
            records = self.db.get_analysis_history(
                query_id=query_id,
                limit=1,
                **self._owner_kwargs(),
            )
            return records[0] if records else None
        except Exception as exc:
            logger.error("查询分析记录失败: %s", exc)
            return None

    def get_latest_by_query_id(
        self,
        query_id: str,
        *,
        code: Optional[str] = None,
        report_type: Optional[str] = None,
    ) -> Optional[AnalysisHistory]:
        try:
            return self.db.get_latest_analysis_by_query_id(
                query_id,
                code=code,
                report_type=report_type,
                **self._owner_kwargs(),
            )
        except Exception as exc:
            logger.error("查询最新分析记录失败: %s", exc)
            return None

    def get_by_id(self, record_id: int) -> Optional[AnalysisHistory]:
        try:
            return self.db.get_analysis_history_by_id(
                record_id,
                **self._owner_kwargs(),
            )
        except Exception as exc:
            logger.error("按 ID 查询分析记录失败: %s", exc)
            return None

    def get_list(
        self,
        code: Optional[str] = None,
        days: int = 30,
        limit: int = 50,
    ) -> List[AnalysisHistory]:
        try:
            return self.db.get_analysis_history(
                code=code,
                days=days,
                limit=limit,
                **self._owner_kwargs(),
            )
        except Exception as exc:
            logger.error("获取分析列表失败: %s", exc)
            return []

    def save(
        self,
        result: Any,
        query_id: str,
        report_type: str,
        news_content: Optional[str] = None,
        context_snapshot: Optional[Dict[str, Any]] = None,
        *,
        save_snapshot: bool = True,
    ) -> int:
        try:
            return self.db.save_analysis_history(
                result=result,
                query_id=query_id,
                report_type=report_type,
                news_content=news_content,
                context_snapshot=context_snapshot,
                save_snapshot=save_snapshot,
                **self._owner_kwargs(),
            )
        except Exception as exc:
            logger.error("保存分析结果失败: %s", exc)
            return 0

    def update_diagnostics(
        self,
        *,
        query_id: str,
        code: Optional[str] = None,
        diagnostics: Optional[Dict[str, Any]] = None,
        notification_runs: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        return self.db.update_analysis_history_diagnostics(
            query_id=query_id,
            code=code,
            diagnostics=diagnostics,
            notification_runs=notification_runs,
            **self._owner_kwargs(),
        )

    def delete_records(self, record_ids: List[int]) -> int:
        return self.db.delete_analysis_history_records(
            record_ids,
            **self._owner_kwargs(),
        )

    def save_fundamental_snapshot(
        self,
        *,
        query_id: str,
        code: str,
        payload: Dict[str, Any],
        source_chain: Optional[Any] = None,
        coverage: Optional[Any] = None,
    ) -> int:
        return self.db.save_fundamental_snapshot(
            query_id=query_id,
            code=code,
            payload=payload,
            source_chain=source_chain,
            coverage=coverage,
            **self._owner_kwargs(),
        )

    def get_latest_fundamental_snapshot(
        self,
        *,
        query_id: str,
        code: str,
    ) -> Optional[Dict[str, Any]]:
        return self.db.get_latest_fundamental_snapshot(
            query_id=query_id,
            code=code,
            **self._owner_kwargs(),
        )

    def count_by_code(self, code: str, days: int = 30) -> int:
        return len(self.get_list(code=code, days=days, limit=1000))
