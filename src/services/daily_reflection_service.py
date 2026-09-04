# -*- coding: utf-8 -*-
"""“渡劫”每日心得业务服务。"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional

from src.repositories.daily_reflection_repo import DailyReflectionRepository
from src.storage import DailyReflectionRecord


class DailyReflectionError(Exception):
    """心得请求不符合业务约束。"""


class DailyReflectionNotFoundError(DailyReflectionError):
    """心得不存在或不属于当前用户。"""


class DailyReflectionService:
    """每日一条心得的写入、查询与删除服务。"""

    def __init__(self, repository: Optional[DailyReflectionRepository] = None):
        self.repository = repository or DailyReflectionRepository()

    def upsert(
        self,
        *,
        user_id: int,
        reflection_date: date,
        title: str,
        content: str,
    ) -> Dict[str, Any]:
        normalized_content = (content or "").strip()
        if not normalized_content:
            raise DailyReflectionError("心得内容不能为空")
        row = self.repository.upsert(
            user_id=user_id,
            reflection_date=reflection_date,
            title=(title or "").strip(),
            content=normalized_content,
        )
        return self.serialize(row)

    def get(self, *, user_id: int, reflection_id: int) -> Dict[str, Any]:
        row = self.repository.get(user_id=user_id, reflection_id=reflection_id)
        if row is None:
            raise DailyReflectionNotFoundError("心得不存在")
        return self.serialize(row)

    def get_by_date(self, *, user_id: int, reflection_date: date) -> Optional[Dict[str, Any]]:
        row = self.repository.get_by_date(
            user_id=user_id,
            reflection_date=reflection_date,
        )
        return self.serialize(row) if row is not None else None

    def list(self, *, user_id: int, page: int, page_size: int) -> Dict[str, Any]:
        rows, total = self.repository.list(
            user_id=user_id,
            page=page,
            page_size=page_size,
        )
        return {
            "items": [self.serialize(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def delete(self, *, user_id: int, reflection_id: int) -> None:
        if not self.repository.delete(user_id=user_id, reflection_id=reflection_id):
            raise DailyReflectionNotFoundError("心得不存在")

    @staticmethod
    def serialize(row: DailyReflectionRecord) -> Dict[str, Any]:
        return {
            "id": int(row.id),
            "reflection_date": row.reflection_date.isoformat(),
            "title": row.title or "",
            "content": row.content,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
