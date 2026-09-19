# -*- coding: utf-8 -*-
"""按用户维度的个人自选股数据访问层。

只在认证用户自己的数据范围内读写；跨用户读写在 SQL 条件层 fail closed。
"""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import and_, delete, desc, func, select

from src.storage import DatabaseManager, MiniappWatchlistRecord, local_naive_now


class MiniappWatchlistRepository:
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def count(self, *, user_id: int) -> int:
        with self.db.get_session() as session:
            return int(
                session.execute(
                    select(func.count(MiniappWatchlistRecord.id)).where(
                        MiniappWatchlistRecord.user_id == user_id
                    )
                ).scalar()
                or 0
            )

    def list(self, *, user_id: int) -> List[MiniappWatchlistRecord]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(MiniappWatchlistRecord)
                .where(MiniappWatchlistRecord.user_id == user_id)
                .order_by(
                    desc(MiniappWatchlistRecord.created_at),
                    desc(MiniappWatchlistRecord.id),
                )
            ).scalars().all()
            return list(rows)

    def get_by_key(self, *, user_id: int, match_key: str) -> Optional[MiniappWatchlistRecord]:
        with self.db.get_session() as session:
            return session.execute(
                select(MiniappWatchlistRecord)
                .where(
                    MiniappWatchlistRecord.user_id == user_id,
                    MiniappWatchlistRecord.match_key == match_key,
                )
                .limit(1)
            ).scalar_one_or_none()

    def add(
        self,
        *,
        user_id: int,
        stock_code: str,
        match_key: str,
        stock_name: str,
    ) -> MiniappWatchlistRecord:
        """新增一条自选；若已存在相同 match_key 则原样返回，不重复插入。"""
        with self.db.get_session() as session:
            existing = session.execute(
                select(MiniappWatchlistRecord)
                .where(
                    MiniappWatchlistRecord.user_id == user_id,
                    MiniappWatchlistRecord.match_key == match_key,
                )
                .limit(1)
            ).scalar_one_or_none()
            if existing is not None:
                # 已存在则仅在提供了名称且原名称为空时补齐展示名，保持幂等。
                if stock_name and not existing.stock_name:
                    existing.stock_name = stock_name
                    existing.updated_at = local_naive_now()
                    session.commit()
                    session.refresh(existing)
                return existing
            now = local_naive_now()
            row = MiniappWatchlistRecord(
                user_id=user_id,
                stock_code=stock_code,
                match_key=match_key,
                stock_name=stock_name,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def set_scheduled(
        self,
        *,
        user_id: int,
        match_key: str,
        scheduled: bool,
    ) -> Optional[MiniappWatchlistRecord]:
        """Toggle whether a watchlist item joins the user's scheduled pool."""
        with self.db.get_session() as session:
            row = session.execute(
                select(MiniappWatchlistRecord)
                .where(
                    MiniappWatchlistRecord.user_id == user_id,
                    MiniappWatchlistRecord.match_key == match_key,
                )
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            row.scheduled = bool(scheduled)
            row.updated_at = local_naive_now()
            session.commit()
            session.refresh(row)
            return row

    def list_scheduled_rows(self, *, user_id: int) -> List[MiniappWatchlistRecord]:
        """Watchlist rows flagged for scheduled analysis, oldest first (stable order)."""
        with self.db.get_session() as session:
            rows = session.execute(
                select(MiniappWatchlistRecord)
                .where(
                    MiniappWatchlistRecord.user_id == user_id,
                    MiniappWatchlistRecord.scheduled.is_(True),
                )
                .order_by(
                    MiniappWatchlistRecord.created_at,
                    MiniappWatchlistRecord.id,
                )
            ).scalars().all()
            return list(rows)

    def list_scheduled_codes(self, *, user_id: int) -> List[str]:
        return [row.stock_code for row in self.list_scheduled_rows(user_id=user_id)]

    def remove(self, *, user_id: int, match_key: str) -> bool:
        with self.db.get_session() as session:
            result = session.execute(
                delete(MiniappWatchlistRecord).where(
                    and_(
                        MiniappWatchlistRecord.user_id == user_id,
                        MiniappWatchlistRecord.match_key == match_key,
                    )
                )
            )
            session.commit()
            return bool(result.rowcount)
