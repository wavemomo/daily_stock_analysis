# -*- coding: utf-8 -*-
"""Object-level ownership regressions for Agent Chat endpoints."""

import asyncio
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from starlette.requests import Request

from api.v1.endpoints import agent as agent_endpoint
from src.portfolio_ownership import PortfolioScope
from src.services.agent_chat_session_service import AgentChatSessionService


def _http_request(owner_id: str | None) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/agent/chat",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "root_path": "",
        }
    )
    if owner_id is None:
        # No canonical principal: the unified-identity middleware only produces
        # authenticated ``miniapp`` / ``web_user`` principals, so a principal-less
        # request is unauthenticated and must fail closed (401).
        request.state.auth_kind = "admin"
    else:
        request.state.auth_kind = "miniapp"
        request.state.miniapp_principal = SimpleNamespace(
            user=SimpleNamespace(id=int(owner_id))
        )
    return request


def _config(backend: str = "litellm") -> SimpleNamespace:
    return SimpleNamespace(
        agent_backend=backend,
        report_language="zh",
        is_agent_available=lambda: True,
    )


def _skill_selection() -> SimpleNamespace:
    return SimpleNamespace(
        effective_skill_ids=[],
        selected_skill_ids_update=None,
    )


def _executor() -> MagicMock:
    executor = MagicMock()
    executor.prepare_turn.return_value = object()
    executor.execute_turn.return_value = SimpleNamespace(
        success=True,
        content="ok",
        error=None,
        total_steps=1,
        backend="codex_app_server",
        error_code=None,
    )
    return executor


class AgentChatOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        with agent_endpoint._ACTIVE_CODEX_STREAMS_LOCK:
            agent_endpoint._ACTIVE_CODEX_STREAMS.clear()

    def tearDown(self) -> None:
        with agent_endpoint._ACTIVE_CODEX_STREAMS_LOCK:
            agent_endpoint._ACTIVE_CODEX_STREAMS.clear()

    def assert_not_found(self, exc: HTTPException) -> None:
        self.assertEqual(exc.status_code, 404)

    def test_session_id_scope_supports_miniapp_and_legacy_admin(self) -> None:
        generated = agent_endpoint._resolve_session_id(
            requested_session_id=None,
            owner_id="17",
        )
        self.assertTrue(generated.startswith("miniapp:17:"))
        self.assertEqual(
            agent_endpoint._resolve_session_id(
                requested_session_id="miniapp:17:existing",
                owner_id="17",
            ),
            "miniapp:17:existing",
        )
        self.assertEqual(
            agent_endpoint._resolve_session_id(
                requested_session_id="legacy-admin-session",
                owner_id=None,
            ),
            "legacy-admin-session",
        )

        with self.assertRaises(HTTPException) as caught:
            agent_endpoint._resolve_session_id(
                requested_session_id="miniapp:18:foreign",
                owner_id="17",
            )
        self.assert_not_found(caught.exception)

    def test_sync_and_stream_reject_foreign_session_before_model_work(self) -> None:
        session_service = MagicMock(spec=AgentChatSessionService)

        async def exercise() -> None:
            with patch("api.v1.endpoints.agent.get_config", return_value=_config()), \
                 patch(
                     "api.v1.endpoints.agent._select_agent_chat_backend",
                     return_value="litellm",
                 ), \
                 patch("api.v1.endpoints.agent._build_executor") as build_executor:
                with self.assertRaises(HTTPException) as sync_caught:
                    await agent_endpoint.agent_chat(
                        agent_endpoint.ChatRequest(
                            message="sync",
                            session_id="miniapp:2:foreign",
                        ),
                        _http_request("1"),
                        session_service,
                    )
                self.assert_not_found(sync_caught.exception)

                with self.assertRaises(HTTPException) as stream_caught:
                    await agent_endpoint.agent_chat_stream(
                        agent_endpoint.ChatRequest(
                            message="stream",
                            session_id="miniapp:2:foreign",
                        ),
                        _http_request("1"),
                        session_service,
                    )
                self.assert_not_found(stream_caught.exception)
                build_executor.assert_not_called()
                session_service.resolve_skill_selection.assert_not_called()

        asyncio.run(exercise())

    def test_sync_chat_uses_authenticated_owner_and_discards_client_owner(self) -> None:
        session_service = MagicMock(spec=AgentChatSessionService)
        session_service.resolve_skill_selection.return_value = _skill_selection()
        executor = _executor()
        executor.chat.return_value = SimpleNamespace(
            success=True,
            content="ok",
            error=None,
        )

        async def exercise() -> None:
            with patch(
                "api.v1.endpoints.agent.get_config",
                return_value=_config(),
            ), patch(
                "api.v1.endpoints.agent._select_agent_chat_backend",
                return_value="litellm",
            ), patch(
                "api.v1.endpoints.agent._build_executor",
                return_value=executor,
            ), patch(
                "api.v1.endpoints.agent.FeatureQuotaService.reserve_for_request",
                return_value=None,
            ):
                response = await agent_endpoint.agent_chat(
                    agent_endpoint.ChatRequest(
                        message="sync",
                        session_id="miniapp:17:session",
                        context={
                            "stock_code": "600519",
                            "resource_owner_id": "attacker",
                        },
                    ),
                    _http_request("17"),
                    session_service,
                )

            self.assertTrue(response.success)
            kwargs = executor.chat.call_args.kwargs
            self.assertEqual(kwargs["resource_owner_id"], "17")
            self.assertEqual(kwargs["portfolio_scope"], PortfolioScope.user("17"))
            self.assertNotIn("resource_owner_id", kwargs["context"])
            self.assertEqual(kwargs["context"]["stock_code"], "600519")

        asyncio.run(exercise())

    def test_principal_less_request_fails_closed_for_sync_and_stream(self) -> None:
        # Unified identity no longer exposes a principal-less "admin" domain:
        # every authenticated request carries a canonical principal, so a
        # principal-less request must fail closed (401) instead of receiving a
        # legacy global portfolio scope.
        session_service = MagicMock(spec=AgentChatSessionService)
        session_service.resolve_skill_selection.return_value = _skill_selection()
        executor = _executor()

        async def exercise() -> None:
            with patch("api.v1.endpoints.agent.get_config", return_value=_config()), \
                 patch(
                     "api.v1.endpoints.agent._select_agent_chat_backend",
                     return_value="litellm",
                 ), \
                 patch(
                     "api.v1.endpoints.agent._build_executor",
                     return_value=executor,
                 ):
                with self.assertRaises(HTTPException) as sync_caught:
                    await agent_endpoint.agent_chat(
                        agent_endpoint.ChatRequest(message="legacy sync"),
                        _http_request(None),
                        session_service,
                    )
                self.assertEqual(sync_caught.exception.status_code, 401)

                with self.assertRaises(HTTPException) as stream_caught:
                    await agent_endpoint.agent_chat_stream(
                        agent_endpoint.ChatRequest(
                            message="legacy stream",
                            request_id="legacy-scope-stream",
                        ),
                        _http_request(None),
                        session_service,
                    )
                self.assertEqual(stream_caught.exception.status_code, 401)

        asyncio.run(exercise())
        executor.chat.assert_not_called()
        executor.prepare_turn.assert_not_called()

    def test_session_endpoints_apply_owner_scope_and_hide_foreign_ids(self) -> None:
        session_service = MagicMock(spec=AgentChatSessionService)
        session_service.list_sessions.return_value = []
        session_service.get_session_detail.return_value = SimpleNamespace(
            messages=[],
            selected_skill_ids=None,
        )
        session_service.delete_session.return_value = 1

        async def exercise() -> None:
            await agent_endpoint.list_chat_sessions(
                _http_request("41"),
                20,
                session_service,
            )
            session_service.list_sessions.assert_called_once_with(20, "miniapp:41:")

            # A principal-less request is unauthenticated and must fail closed
            # rather than receive a global (owner=None) session listing.
            session_service.list_sessions.reset_mock()
            with self.assertRaises(HTTPException) as anon_caught:
                await agent_endpoint.list_chat_sessions(
                    _http_request(None),
                    20,
                    session_service,
                )
            self.assertEqual(anon_caught.exception.status_code, 401)
            session_service.list_sessions.assert_not_called()

            # A client-supplied user_id cannot widen an authenticated owner's scope.
            session_service.list_sessions.reset_mock()
            await agent_endpoint.list_chat_sessions(
                _http_request("41"),
                20,
                session_service,
                user_id="miniapp:42:",
            )
            session_service.list_sessions.assert_called_once_with(20, "miniapp:41:")

            detail = await agent_endpoint.get_chat_session_messages(
                "miniapp:41:owned",
                _http_request("41"),
                30,
                session_service,
            )
            self.assertEqual(detail.session_id, "miniapp:41:owned")
            session_service.get_session_detail.assert_called_once_with(
                "miniapp:41:owned",
                30,
            )

            deleted = await agent_endpoint.delete_chat_session(
                "miniapp:41:owned",
                _http_request("41"),
                session_service,
            )
            self.assertEqual(deleted, {"deleted": 1})

            session_service.get_session_detail.reset_mock()
            with self.assertRaises(HTTPException) as detail_caught:
                await agent_endpoint.get_chat_session_messages(
                    "miniapp:42:foreign",
                    _http_request("41"),
                    30,
                    session_service,
                )
            self.assert_not_found(detail_caught.exception)
            session_service.get_session_detail.assert_not_called()

            session_service.delete_session.reset_mock()
            with self.assertRaises(HTTPException) as delete_caught:
                await agent_endpoint.delete_chat_session(
                    "miniapp:42:foreign",
                    _http_request("41"),
                    session_service,
                )
            self.assert_not_found(delete_caught.exception)
            session_service.delete_session.assert_not_called()

        asyncio.run(exercise())

    def test_cancel_requires_same_owner_and_rejects_principal_less(self) -> None:
        owner_event = threading.Event()
        admin_event = threading.Event()
        with agent_endpoint._ACTIVE_CODEX_STREAMS_LOCK:
            agent_endpoint._ACTIVE_CODEX_STREAMS.update(
                {
                    "owner-request": agent_endpoint._ActiveCodexStream(
                        owner_id="7",
                        cancel_event=owner_event,
                    ),
                    "admin-request": agent_endpoint._ActiveCodexStream(
                        owner_id=None,
                        cancel_event=admin_event,
                    ),
                }
            )

        async def exercise() -> None:
            response = await agent_endpoint.cancel_agent_chat_stream(
                "owner-request",
                _http_request("7"),
            )
            self.assertEqual(
                response,
                {"accepted": True, "request_id": "owner-request"},
            )
            self.assertTrue(owner_event.is_set())

            admin_event.clear()
            with self.assertRaises(HTTPException) as admin_owned_caught:
                await agent_endpoint.cancel_agent_chat_stream(
                    "admin-request",
                    _http_request("7"),
                )
            self.assert_not_found(admin_owned_caught.exception)
            self.assertFalse(admin_event.is_set())

            owner_event.clear()
            with self.assertRaises(HTTPException) as foreign_caught:
                await agent_endpoint.cancel_agent_chat_stream(
                    "owner-request",
                    _http_request("8"),
                )
            self.assert_not_found(foreign_caught.exception)
            self.assertFalse(owner_event.is_set())

            # A principal-less request cannot cancel any stream; it fails closed
            # (401) instead of acting as a global administrator.
            owner_event.clear()
            admin_event.clear()
            with self.assertRaises(HTTPException) as anon_caught:
                await agent_endpoint.cancel_agent_chat_stream(
                    "owner-request",
                    _http_request(None),
                )
            self.assertEqual(anon_caught.exception.status_code, 401)
            self.assertFalse(owner_event.is_set())
            self.assertFalse(admin_event.is_set())

        asyncio.run(exercise())

    def test_cancel_owner_check_and_signal_share_one_lock_point(self) -> None:
        class BlockingEvent(threading.Event):
            def __init__(self) -> None:
                super().__init__()
                self.entered = threading.Event()
                self.release = threading.Event()

            def set(self) -> None:
                self.entered.set()
                if not self.release.wait(timeout=2):
                    raise AssertionError("test did not release cancellation")
                super().set()

        request_id = "linearized-cancel"
        original_event = BlockingEvent()
        replacement = agent_endpoint._ActiveCodexStream(
            owner_id="7",
            cancel_event=threading.Event(),
        )
        with agent_endpoint._ACTIVE_CODEX_STREAMS_LOCK:
            agent_endpoint._ACTIVE_CODEX_STREAMS[request_id] = (
                agent_endpoint._ActiveCodexStream(
                    owner_id="7",
                    cancel_event=original_event,
                )
            )

        cancel_result = {}

        def cancel() -> None:
            cancel_result["value"] = asyncio.run(
                agent_endpoint.cancel_agent_chat_stream(
                    request_id,
                    _http_request("7"),
                )
            )

        replaced = threading.Event()

        def replace() -> None:
            with agent_endpoint._ACTIVE_CODEX_STREAMS_LOCK:
                agent_endpoint._ACTIVE_CODEX_STREAMS[request_id] = replacement
            replaced.set()

        cancel_thread = threading.Thread(target=cancel)
        replace_thread = threading.Thread(target=replace)
        cancel_thread.start()
        self.assertTrue(original_event.entered.wait(timeout=1))
        replace_thread.start()
        self.assertFalse(replaced.wait(timeout=0.05))

        original_event.release.set()
        cancel_thread.join(timeout=1)
        replace_thread.join(timeout=1)

        self.assertFalse(cancel_thread.is_alive())
        self.assertFalse(replace_thread.is_alive())
        self.assertTrue(original_event.is_set())
        self.assertTrue(replaced.is_set())
        self.assertEqual(
            cancel_result["value"],
            {"accepted": True, "request_id": request_id},
        )
        with agent_endpoint._ACTIVE_CODEX_STREAMS_LOCK:
            self.assertIs(
                agent_endpoint._ACTIVE_CODEX_STREAMS[request_id],
                replacement,
            )

    def test_codex_stream_records_owner_rejects_conflict_and_cleans_up(self) -> None:
        session_service = MagicMock(spec=AgentChatSessionService)
        session_service.resolve_skill_selection.return_value = _skill_selection()
        executor = _executor()

        async def exercise() -> None:
            with patch(
                "api.v1.endpoints.agent.get_config",
                return_value=_config("codex_app_server"),
            ), patch(
                "api.v1.endpoints.agent._select_agent_chat_backend",
                return_value="codex_app_server",
            ), patch(
                "api.v1.endpoints.agent._build_executor",
                return_value=executor,
            ), patch(
                "api.v1.endpoints.agent.FeatureQuotaService.reserve_for_request",
                return_value=None,
            ):
                response = await agent_endpoint.agent_chat_stream(
                    agent_endpoint.ChatRequest(
                        message="first",
                        session_id="miniapp:9:session",
                        request_id="shared-request-id",
                    ),
                    _http_request("9"),
                    session_service,
                )
                iterator = response.body_iterator
                first_event = json.loads(
                    (await anext(iterator)).removeprefix("data: ").strip()
                )
                self.assertEqual(first_event["type"], "accepted")
                with agent_endpoint._ACTIVE_CODEX_STREAMS_LOCK:
                    active = agent_endpoint._ACTIVE_CODEX_STREAMS[
                        "shared-request-id"
                    ]
                    self.assertEqual(active.owner_id, "9")
                self.assertEqual(
                    executor.prepare_turn.call_args.kwargs["resource_owner_id"],
                    "9",
                )
                self.assertEqual(
                    executor.prepare_turn.call_args.kwargs["portfolio_scope"],
                    PortfolioScope.user("9"),
                )

                with self.assertRaises(HTTPException) as conflict_caught:
                    await agent_endpoint.agent_chat_stream(
                        agent_endpoint.ChatRequest(
                            message="duplicate",
                            session_id="miniapp:9:session",
                            request_id="shared-request-id",
                        ),
                        _http_request("9"),
                        session_service,
                    )
                self.assertEqual(conflict_caught.exception.status_code, 409)

                await iterator.aclose()
                with agent_endpoint._ACTIVE_CODEX_STREAMS_LOCK:
                    self.assertNotIn(
                        "shared-request-id",
                        agent_endpoint._ACTIVE_CODEX_STREAMS,
                    )

        asyncio.run(exercise())
        executor.execute_turn.assert_not_called()

    def test_generator_cleanup_does_not_remove_replacement_record(self) -> None:
        request_id = "reused-after-cleanup"
        replacement = agent_endpoint._ActiveCodexStream(
            owner_id="12",
            cancel_event=threading.Event(),
        )
        session_service = MagicMock(spec=AgentChatSessionService)
        session_service.resolve_skill_selection.return_value = _skill_selection()
        executor = _executor()

        def replace_then_fail(**_kwargs):
            with agent_endpoint._ACTIVE_CODEX_STREAMS_LOCK:
                agent_endpoint._ACTIVE_CODEX_STREAMS[request_id] = replacement
            raise RuntimeError("prepare failed")

        executor.prepare_turn.side_effect = replace_then_fail

        async def exercise() -> None:
            with patch(
                "api.v1.endpoints.agent.get_config",
                return_value=_config("codex_app_server"),
            ), patch(
                "api.v1.endpoints.agent._select_agent_chat_backend",
                return_value="codex_app_server",
            ), patch(
                "api.v1.endpoints.agent._build_executor",
                return_value=executor,
            ), patch.object(agent_endpoint.logger, "error"):
                response = await agent_endpoint.agent_chat_stream(
                    agent_endpoint.ChatRequest(
                        message="question",
                        session_id="miniapp:12:session",
                        request_id=request_id,
                    ),
                    _http_request("12"),
                    session_service,
                )
                events = [
                    json.loads(chunk.removeprefix("data: ").strip())
                    async for chunk in response.body_iterator
                ]
                self.assertEqual(
                    [event["error_code"] for event in events],
                    ["request_not_accepted"],
                )

            with agent_endpoint._ACTIVE_CODEX_STREAMS_LOCK:
                self.assertIs(
                    agent_endpoint._ACTIVE_CODEX_STREAMS[request_id],
                    replacement,
                )

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
