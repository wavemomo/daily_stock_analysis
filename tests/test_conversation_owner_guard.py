"""对话表的数据层归属兜底。

对话隔离的主依据仍是 API 层的 session_id 前缀校验（``miniapp:{owner_id}:``）。
这里验证新增的 DB 兜底语义：
- 另一个用户即使猜到 session_id，也读不到他人的消息与摘要；
- 语义只"拦截"不"隐藏"：本人以及 legacy/global（后台、CLI、机器人）写入的行仍可读，
  不会因为加了归属列而让任何人看不到自己的历史。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from src.analysis_ownership import AnalysisOwner
from src.analysis_owner_context import (
    bind_current_analysis_owner,
    reset_current_analysis_owner,
)
from src.config import Config
from src.repositories.miniapp_user_repo import MiniappUserRepository
from src.storage import DatabaseManager


@pytest.fixture()
def conversation_db():
    temp_dir = tempfile.TemporaryDirectory()
    db_path = Path(temp_dir.name) / "conversation_owner.db"
    previous = {key: os.environ.get(key) for key in ("DATABASE_PATH", "STOCK_LIST", "GEMINI_API_KEY")}
    os.environ.update(
        DATABASE_PATH=str(db_path),
        STOCK_LIST="600519",
        GEMINI_API_KEY="test",
    )
    Config.reset_instance()
    DatabaseManager.reset_instance()
    database = DatabaseManager.get_instance()
    try:
        yield database
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        temp_dir.cleanup()


def _as_owner(owner, action):
    token = bind_current_analysis_owner(owner)
    try:
        return action()
    finally:
        reset_current_analysis_owner(token)


def _make_user(database, openid: str) -> int:
    return MiniappUserRepository(database).upsert_user(openid=openid, issuer="conv-test").id


def test_other_user_cannot_read_conversation_even_with_known_session_id(conversation_db):
    database = conversation_db
    user_a = _make_user(database, "conv-owner-a")
    user_b = _make_user(database, "conv-owner-b")
    owner_a = AnalysisOwner.user(user_a)
    owner_b = AnalysisOwner.user(user_b)
    session_id = f"miniapp:{user_a}:known-session"

    _as_owner(owner_a, lambda: database.save_conversation_message(session_id, "user", "A 的私密提问"))
    _as_owner(owner_a, lambda: database.upsert_conversation_summary(session_id, "A 的摘要", 1, 1, 10))

    # 本人可读
    own_messages = _as_owner(owner_a, lambda: database.get_visible_conversation_messages(session_id))
    assert [item["content"] for item in own_messages] == ["A 的私密提问"]
    own_summary = _as_owner(owner_a, lambda: database.get_conversation_summary(session_id))
    assert own_summary is not None and own_summary["summary"] == "A 的摘要"

    # 另一个用户即使拿到 session_id 也读不到（DB 兜底）
    assert _as_owner(owner_b, lambda: database.get_visible_conversation_messages(session_id)) == []
    assert _as_owner(owner_b, lambda: database.get_conversation_summary(session_id)) is None
    assert _as_owner(owner_b, lambda: database.get_conversation_history(session_id)) == []


def test_legacy_and_background_rows_stay_readable(conversation_db):
    """未绑定归属（后台/CLI/机器人）写入的行不被隐藏，避免反向回归。"""
    database = conversation_db
    user_a = _make_user(database, "conv-legacy-a")
    session_id = "legacy-session"

    # 未绑定 owner 上下文 → 记为 global
    database.save_conversation_message(session_id, "user", "后台写入的历史")

    visible_for_user = _as_owner(
        AnalysisOwner.user(user_a),
        lambda: database.get_visible_conversation_messages(session_id),
    )
    visible_for_background = database.get_visible_conversation_messages(session_id)

    assert [item["content"] for item in visible_for_user] == ["后台写入的历史"]
    assert [item["content"] for item in visible_for_background] == ["后台写入的历史"]


def test_conversation_rows_record_owner_on_write(conversation_db):
    database = conversation_db
    user_a = _make_user(database, "conv-stamp-a")
    session_id = f"miniapp:{user_a}:stamped"

    _as_owner(
        AnalysisOwner.user(user_a),
        lambda: database.save_conversation_user_turn(session_id, "带归属的提问", ["skill-a"]),
    )

    from src.storage import ConversationMessage, ConversationSessionState

    with database.get_session() as session:
        message = session.query(ConversationMessage).filter(
            ConversationMessage.session_id == session_id
        ).one()
        state = session.get(ConversationSessionState, session_id)

    assert (message.owner_scope, message.owner_user_id) == ("user", user_a)
    assert (state.owner_scope, state.owner_user_id) == ("user", user_a)
