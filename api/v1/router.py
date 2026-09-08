# -*- coding: utf-8 -*-
"""API v1 路由聚合。"""

from fastapi import APIRouter

from api.v1.endpoints import (
    agent,
    alerts,
    screening,
    analysis,
    backtest,
    daily_reflections,
    data,
    decision_signals,
    feature_quotas,
    health,
    history,
    intelligence,
    miniapp_auth,
    miniapp_rbac,
    miniapp_system_config,
    miniapp_watchlist,
    portfolio,
    rbac,
    stocks,
    system_config,
    usage,
    web_auth,
)

router = APIRouter()

router.include_router(web_auth.router, prefix="/web-auth", tags=["WebAuth"])
router.include_router(miniapp_auth.router, prefix="/miniapp/auth", tags=["MiniappAuth"])
router.include_router(miniapp_rbac.router, prefix="/miniapp/rbac", tags=["MiniappRbac"])
router.include_router(
    miniapp_system_config.router,
    prefix="/miniapp/system",
    tags=["MiniappSystemConfig"],
)
router.include_router(
    feature_quotas.miniapp_rbac_router,
    prefix="/miniapp/rbac/feature-quotas",
    tags=["MiniappRbac"],
)
router.include_router(rbac.router, prefix="/rbac", tags=["Rbac"])
router.include_router(
    feature_quotas.miniapp_rbac_router,
    prefix="/rbac/feature-quotas",
    tags=["Rbac"],
)
router.include_router(feature_quotas.router, prefix="/feature-quotas", tags=["FeatureQuotas"])
router.include_router(
    daily_reflections.router,
    prefix="/miniapp/daily-reflections",
    tags=["DailyReflections"],
)
router.include_router(
    miniapp_watchlist.router,
    prefix="/miniapp/watchlist",
    tags=["MiniappWatchlist"],
)
router.include_router(agent.router, prefix="/agent", tags=["Agent"])
router.include_router(analysis.router, prefix="/analysis", tags=["Analysis"])
router.include_router(history.router, prefix="/history", tags=["History"])
router.include_router(stocks.router, prefix="/stocks", tags=["Stocks"])
router.include_router(backtest.router, prefix="/backtest", tags=["Backtest"])
router.include_router(system_config.router, prefix="/system", tags=["SystemConfig"])
router.include_router(usage.router, prefix="/usage", tags=["Usage"])
router.include_router(portfolio.router, prefix="/portfolio", tags=["Portfolio"])
router.include_router(alerts.router, prefix="/alerts", tags=["Alerts"])
router.include_router(
    decision_signals.router,
    prefix="/decision-signals",
    tags=["DecisionSignals"],
)
router.include_router(screening.router, prefix="/screening", tags=["Screening"])
router.include_router(data.router, prefix="/data", tags=["Data"])
router.include_router(
    intelligence.router,
    prefix="/intelligence",
    tags=["Intelligence"],
)
router.include_router(health.router, tags=["Health"])
