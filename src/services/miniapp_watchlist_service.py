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
    def __init__(self, repository: Optional[MiniappWatchlistRepository] = None):
        self.repository = repository or MiniappWatchlistRepository()

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

    @staticmethod
    def serialize(row: MiniappWatchlistRecord) -> Dict[str, Any]:
        return {
            "id": int(row.id),
            "stock_code": row.stock_code,
            "stock_name": row.stock_name or "",
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
