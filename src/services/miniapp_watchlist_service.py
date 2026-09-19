# -*- coding: utf-8 -*-
"""按用户维度的个人自选股业务服务。

与全局 STOCK_LIST（部署级默认清单，管理员维护、驱动每日自动分析）相互独立：
本服务只处理登录用户自己的个人自选，普通成员即可增删查。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from data_provider.base import normalize_stock_code
from src.repositories.miniapp_watchlist_repo import MiniappWatchlistRepository
from src.repositories.user_schedule_pref_repo import UserSchedulePrefRepository
from src.services.stock_code_utils import is_code_like
from src.storage import MiniappWatchlistRecord

# 单用户自选上限，避免异常写入或滥用撑爆个人清单。
MAX_WATCHLIST_SIZE = 200


class WatchlistError(Exception):
    """自选请求不符合业务约束。"""


class WatchlistValidationError(WatchlistError):
    """股票代码非法或数量超限。"""


def _match_key(code: str) -> str:
    """增删/去重使用的等价键；与 stocks 全局自选 _watchlist_match_key 保持一致。"""
    normalized = normalize_stock_code(code.strip())
    if re.fullmatch(r"\d{5}", normalized):
        return f"HK{normalized}"
    return normalized.upper()


class MiniappWatchlistService:
    def __init__(
        self,
        repository: Optional[MiniappWatchlistRepository] = None,
        pref_repository: Optional[UserSchedulePrefRepository] = None,
    ):
        self.repository = repository or MiniappWatchlistRepository()
        self.pref_repository = pref_repository or UserSchedulePrefRepository()

    def list(self, *, user_id: int) -> Dict[str, Any]:
        rows = self.repository.list(user_id=user_id)
        items = [self.serialize(row) for row in rows]
        return {
            "items": items,
            "stock_codes": [item["stock_code"] for item in items],
            "total": len(items),
        }

    def add(self, *, user_id: int, stock_code: str, stock_name: str = "") -> Dict[str, Any]:
        raw = (stock_code or "").strip()
        if not raw:
            raise WatchlistValidationError("股票代码不能为空")
        if not is_code_like(raw):
            raise WatchlistValidationError(f"'{raw}' 不是合法的股票代码格式")
        key = _match_key(raw)
        # 已存在则视为幂等成功，不计入上限校验。
        if self.repository.get_by_key(user_id=user_id, match_key=key) is None:
            if self.repository.count(user_id=user_id) >= MAX_WATCHLIST_SIZE:
                raise WatchlistValidationError(
                    f"自选数量已达上限（{MAX_WATCHLIST_SIZE}），请先删除部分股票"
                )
        row = self.repository.add(
            user_id=user_id,
            stock_code=raw,
            match_key=key,
            stock_name=(stock_name or "").strip(),
        )
        return self.serialize(row)

    def remove(self, *, user_id: int, stock_code: str) -> bool:
        raw = (stock_code or "").strip()
        if not raw:
            raise WatchlistValidationError("股票代码不能为空")
        return self.repository.remove(user_id=user_id, match_key=_match_key(raw))

    def set_scheduled(self, *, user_id: int, stock_code: str, scheduled: bool) -> Dict[str, Any]:
        """设置某只自选是否纳入本人定时分析池。"""
        raw = (stock_code or "").strip()
        if not raw:
            raise WatchlistValidationError("股票代码不能为空")
        row = self.repository.set_scheduled(
            user_id=user_id,
            match_key=_match_key(raw),
            scheduled=bool(scheduled),
        )
        if row is None:
            raise WatchlistValidationError("自选不存在")
        return self.serialize(row)

    def get_schedule_pref(self, *, user_id: int) -> Dict[str, Any]:
        """返回本人定时分析参与状态与当前纳入池的股票数量。"""
        enabled = self.pref_repository.is_enabled(user_id=user_id)
        scheduled_codes = self.repository.list_scheduled_codes(user_id=user_id)
        return {
            "scheduled_analysis_enabled": bool(enabled),
            "scheduled_count": len(scheduled_codes),
        }

    def set_schedule_pref(self, *, user_id: int, enabled: bool) -> Dict[str, Any]:
        """开启/关闭本人定时分析参与开关。"""
        self.pref_repository.set_enabled(user_id=user_id, enabled=bool(enabled))
        return self.get_schedule_pref(user_id=user_id)

    def iter_scheduled_pools(self) -> list:
        """供定时调度使用：返回已开启且有勾选股票的用户及其股票池。

        形如 ``[{"user_id": int, "codes": [{"stock_code": str, "stock_name": str}]}]``；
        空池用户不会出现在结果中，调度侧无需再判空。
        """
        pools = []
        for user_id in self.pref_repository.list_enabled_user_ids():
            rows = self.repository.list_scheduled_rows(user_id=user_id)
            if not rows:
                continue
            pools.append({
                "user_id": int(user_id),
                "codes": [
                    {"stock_code": row.stock_code, "stock_name": row.stock_name or ""}
                    for row in rows
                ],
            })
        return pools

    @staticmethod
    def serialize(row: MiniappWatchlistRecord) -> Dict[str, Any]:
        return {
            "id": int(row.id),
            "stock_code": row.stock_code,
            "stock_name": row.stock_name or "",
            "scheduled": bool(row.scheduled),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
