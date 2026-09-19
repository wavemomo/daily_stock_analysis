"""用户归属通知必须只走该用户自己的渠道，不得广播到全局静态渠道。

回归点：`email_receivers_override` 过去只隔离邮件收件人，企业微信 / Webhook / ntfy 等
全局静态渠道仍会收到某个用户的私有报告与告警（管理员与其他运维可见）。现在
`owner_scoped=True` 会把投递收敛为仅邮件。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import List

import pytest

from src.notification import (
    NotificationChannel,
    NotificationNoiseDecision,
    NotificationService,
)


@pytest.fixture()
def notifier(monkeypatch) -> NotificationService:
    """构造一个「邮件 + 企业微信 + 自定义 Webhook」均可用的通知服务。"""
    service = NotificationService.__new__(NotificationService)
    channels = [
        NotificationChannel.EMAIL,
        NotificationChannel.WECHAT,
        NotificationChannel.CUSTOM,
    ]
    service._available_channels = list(channels)
    service._stock_email_groups = []
    service._markdown_to_image_channels = set()
    service._markdown_to_image_max_chars = 10000
    service.get_channels_for_route = lambda route_type=None: list(channels)  # type: ignore[assignment]
    service.should_broadcast_static_channels = lambda: True  # type: ignore[assignment]
    service.send_to_context = lambda content: False  # type: ignore[assignment]
    service._config = SimpleNamespace()
    # 噪声控制与本用例无关，固定为"允许发送"，避免依赖全局配置。
    service.evaluate_noise_control = lambda *args, **kwargs: NotificationNoiseDecision(  # type: ignore[assignment]
        should_send=True, message=""
    )
    return service


def _capture_channels(service: NotificationService) -> List[NotificationChannel]:
    """记录本次分发实际触达的渠道。"""
    used: List[NotificationChannel] = []

    def _fake_send_to_channel(channel, content, **kwargs):
        used.append(channel)
        return True

    service._send_to_static_channel = _fake_send_to_channel  # type: ignore[assignment]
    return used


def test_owner_scoped_dispatch_only_uses_email(notifier):
    used = _capture_channels(notifier)

    result = notifier.send_with_results(
        "私有个股报告",
        route_type="report",
        email_receivers_override=["user@example.com"],
        owner_scoped=True,
    )

    assert result.success is True
    # 只走邮件；企业微信与自定义 Webhook 这类全局渠道必须被跳过。
    assert used == [NotificationChannel.EMAIL]
    assert NotificationChannel.WECHAT not in used
    assert NotificationChannel.CUSTOM not in used


def test_global_dispatch_still_broadcasts_all_channels(notifier):
    used = _capture_channels(notifier)

    result = notifier.send_with_results("全局大盘复盘", route_type="report")

    assert result.success is True
    # 全局归属内容（定时任务/大盘复盘/管理员）保持原有全局广播行为。
    assert set(used) == {
        NotificationChannel.EMAIL,
        NotificationChannel.WECHAT,
        NotificationChannel.CUSTOM,
    }


def test_owner_scoped_without_email_channel_skips_dispatch(notifier):
    """用户归属通知在未配置邮件渠道时应跳过，而不是回退到全局渠道。"""
    notifier.get_channels_for_route = lambda route_type=None: [  # type: ignore[assignment]
        NotificationChannel.WECHAT,
        NotificationChannel.CUSTOM,
    ]
    used = _capture_channels(notifier)

    result = notifier.send_with_results(
        "私有个股报告",
        route_type="report",
        email_receivers_override=["user@example.com"],
        owner_scoped=True,
    )

    assert used == []
    assert result.status == "no_channel"
    assert result.success is False


def test_owner_scoped_with_empty_receivers_skips_email_without_global_fallback(notifier):
    """用户未绑定邮箱/关闭开关时：不发邮件，也绝不改走全局渠道。"""
    used: List[NotificationChannel] = []
    real_send_to_static_channel = NotificationService._send_to_static_channel

    def _tracking(channel, content, **kwargs):
        used.append(channel)
        return real_send_to_static_channel(notifier, channel, content, **kwargs)

    notifier._send_to_static_channel = _tracking  # type: ignore[assignment]
    result = notifier.send_with_results(
        "私有个股报告",
        route_type="report",
        email_receivers_override=[],
        owner_scoped=True,
    )

    assert used == [NotificationChannel.EMAIL]
    assert NotificationChannel.WECHAT not in used
    assert NotificationChannel.CUSTOM not in used
    # 空收件人按"无需发送"处理（非失败），且没有任何全局渠道被触达。
    assert result.success is True
