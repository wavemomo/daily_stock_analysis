# -*- coding: utf-8 -*-
"""按用户的定时分析参与偏好数据访问层。

只在认证用户自己的数据范围内读写；跨用户读写在 SQL 条件层 fail closed。
调度时间等运行参数由管理员维护，不在本表内。
"""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select

from src.storage import DatabaseManager, UserSchedulePrefRecord, local_naive_now


class UserSchedulePrefRepository:
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def is_enabled(self, *, user_id: int) -> bool:
        with self.db.get_session() as session:
            row = session.execute(
                select(UserSchedulePrefRecord)
                .where(UserSchedulePrefRecord.user_id == user_id)
                .limit(1)
            ).scalar_one_or_none()
            return bool(row.scheduled_analysis_enabled) if row is not None else False

    def set_enabled(self, *, user_id: int, enabled: bool) -> bool:
        with self.db.get_session() as session:
            row = session.execute(
                select(UserSchedulePrefRecord)
                .where(UserSchedulePrefRecord.user_id == user_id)
                .limit(1)
            ).scalar_one_or_none()
            now = local_naive_now()
            if row is None:
                row = UserSchedulePrefRecord(
                    user_id=user_id,
                    scheduled_analysis_enabled=bool(enabled),
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.scheduled_analysis_enabled = bool(enabled)
                row.updated_at = now
            session.commit()
            return bool(enabled)

    def list_enabled_user_ids(self) -> List[int]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(UserSchedulePrefRecord.user_id)
                .where(UserSchedulePrefRecord.scheduled_analysis_enabled.is_(True))
                .order_by(UserSchedulePrefRecord.user_id)
            ).scalars().all()
            return [int(uid) for uid in rows]
