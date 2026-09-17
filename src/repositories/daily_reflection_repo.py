# -*- coding: utf-8 -*-
"""“渡劫”每日心得的数据访问层。"""

from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional, Tuple

from sqlalchemy import and_, delete, desc, func, select

from src.storage import DailyReflectionRecord, DatabaseManager, local_naive_now


class DailyReflectionRepository:
    """只允许在认证用户自己的数据范围内读写心得。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def upsert(
        self,
        *,
        user_id: int,
        reflection_date: date,
        title: str,
        content: str,
    ) -> DailyReflectionRecord:
        with self.db.get_session() as session:
            row = session.execute(
                select(DailyReflectionRecord)
                .where(
                    DailyReflectionRecord.user_id == user_id,
                    DailyReflectionRecord.reflection_date == reflection_date,
                )
                .limit(1)
            ).scalar_one_or_none()
            now = local_naive_now()
            if row is None:
                row = DailyReflectionRecord(
                    user_id=user_id,
                    reflection_date=reflection_date,
                    title=title,
                    content=content,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.title = title
                row.content = content
                row.updated_at = now
            session.commit()
            session.refresh(row)
            return row

    def get(self, *, user_id: int, reflection_id: int) -> Optional[DailyReflectionRecord]:
        with self.db.get_session() as session:
            return session.execute(
                select(DailyReflectionRecord)
                .where(
                    DailyReflectionRecord.id == reflection_id,
                    DailyReflectionRecord.user_id == user_id,
                )
                .limit(1)
            ).scalar_one_or_none()

    def list_dates(self, *, user_id: int) -> List[date]:
        """返回当前用户全部心得日期（升序），仅取 date 列用于连续打卡与月度统计。"""
        with self.db.get_session() as session:
            rows = session.execute(
                select(DailyReflectionRecord.reflection_date)
                .where(DailyReflectionRecord.user_id == user_id)
                .order_by(DailyReflectionRecord.reflection_date.asc())
            ).scalars().all()
            return [row for row in rows]

    def get_by_date(
        self,
        *,
        user_id: int,
        reflection_date: date,
    ) -> Optional[DailyReflectionRecord]:
        with self.db.get_session() as session:
            return session.execute(
                select(DailyReflectionRecord)
                .where(
                    DailyReflectionRecord.user_id == user_id,
                    DailyReflectionRecord.reflection_date == reflection_date,
                )
                .limit(1)
            ).scalar_one_or_none()

    def list(
        self,
        *,
        user_id: int,
        page: int,
        page_size: int,
    ) -> Tuple[List[DailyReflectionRecord], int]:
        condition = DailyReflectionRecord.user_id == user_id
        offset = (page - 1) * page_size
        with self.db.get_session() as session:
            total = session.execute(
                select(func.count(DailyReflectionRecord.id)).where(condition)
            ).scalar() or 0
            rows = session.execute(
                select(DailyReflectionRecord)
                .where(condition)
                .order_by(
                    desc(DailyReflectionRecord.reflection_date),
                    desc(DailyReflectionRecord.id),
                )
                .offset(offset)
                .limit(page_size)
            ).scalars().all()
            return list(rows), int(total)

    def delete(self, *, user_id: int, reflection_id: int) -> bool:
        with self.db.get_session() as session:
            result = session.execute(
                delete(DailyReflectionRecord).where(
                    and_(
                        DailyReflectionRecord.id == reflection_id,
                        DailyReflectionRecord.user_id == user_id,
                    )
                )
            )
            session.commit()
            return bool(result.rowcount)
