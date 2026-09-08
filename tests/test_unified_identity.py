# -*- coding: utf-8 -*-
"""统一 users/auth_identities 身份模型的确定性回归测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, inspect, select, text

from src.config import Config
from src.repositories.auth_identity_repo import (
    AuthIdentityConflictError,
    AuthIdentityRepository,
)
from src.repositories.miniapp_user_repo import MiniappUserRepository
from src.services.identity_service import IdentityService
from src.services.wechat_miniapp_auth_service import (
    MiniappAuthError,
    WechatMiniappAuthService,
)
from src.storage import AuthIdentityRecord, DatabaseManager, UserRecord


class UnifiedIdentityTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "identity.db"
        Config.reset_instance()
        DatabaseManager.reset_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        self.temp_dir.cleanup()

    def _database(self) -> DatabaseManager:
        return DatabaseManager(f"sqlite:///{self.db_path}")

    def test_legacy_miniapp_users_rename_preserves_ids_and_child_foreign_keys(self) -> None:
        engine = create_engine(f"sqlite:///{self.db_path}")
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE miniapp_users ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "openid VARCHAR(128) NOT NULL UNIQUE, "
                "unionid VARCHAR(128) UNIQUE, "
                "nickname VARCHAR(64), avatar_url VARCHAR(2048), "
                "profile_updated_at DATETIME, is_active BOOLEAN NOT NULL DEFAULT 1, "
                "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, "
                "last_login_at DATETIME NOT NULL"
                ")"
            ))
            connection.execute(text(
                "CREATE TABLE legacy_identity_child ("
                "id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
                "FOREIGN KEY(user_id) REFERENCES miniapp_users(id)"
                ")"
            ))
            connection.execute(text(
                "INSERT INTO miniapp_users "
                "(id, openid, is_active, created_at, updated_at, last_login_at) "
                "VALUES (41, 'legacy-openid', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ))
            connection.execute(text("INSERT INTO legacy_identity_child (id, user_id) VALUES (1, 41)"))
        engine.dispose()

        database = self._database()
        table_names = set(inspect(database._engine).get_table_names())
        self.assertIn("users", table_names)
        self.assertNotIn("miniapp_users", table_names)
        with database.get_session() as session:
            migrated = session.execute(
                select(UserRecord).where(UserRecord.id == 41)
            ).scalar_one()
        self.assertEqual(migrated.openid, "legacy-openid")

        with database._engine.connect() as connection:
            child_foreign_keys = connection.exec_driver_sql(
                "PRAGMA foreign_key_list(legacy_identity_child)"
            ).fetchall()
            violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            foreign_keys_enabled = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
        self.assertEqual(child_foreign_keys[0][2], "users")
        self.assertEqual(violations, [])
        self.assertEqual(foreign_keys_enabled, 1)
        openid = next(
            column for column in inspect(database._engine).get_columns("users")
            if column["name"] == "openid"
        )
        self.assertTrue(openid["nullable"])

    def test_existing_users_and_nonempty_legacy_table_fails_closed(self) -> None:
        engine = create_engine(f"sqlite:///{self.db_path}")
        with engine.begin() as connection:
            for table_name in ("users", "miniapp_users"):
                connection.execute(text(
                    f"CREATE TABLE {table_name} ("
                    "id INTEGER PRIMARY KEY, openid VARCHAR(128) NOT NULL, "
                    "unionid VARCHAR(128), nickname VARCHAR(64), avatar_url VARCHAR(2048), "
                    "profile_updated_at DATETIME, is_active BOOLEAN NOT NULL DEFAULT 1, "
                    "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, "
                    "last_login_at DATETIME NOT NULL)"
                ))
            connection.execute(text(
                "INSERT INTO miniapp_users "
                "(id, openid, is_active, created_at, updated_at, last_login_at) "
                "VALUES (1, 'legacy-openid', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ))
        engine.dispose()

        with self.assertRaisesRegex(RuntimeError, "拒绝自动合并身份数据"):
            self._database()

    def test_issuer_scoped_openids_remain_separate_users(self) -> None:
        database = self._database()
        service = IdentityService(AuthIdentityRepository(database))

        first, first_created = service.resolve_miniapp_user(
            app_id="wx-miniapp-a", openid="same-openid", unionid=None
        )
        second, second_created = service.resolve_miniapp_user(
            app_id="wx-miniapp-b", openid="same-openid", unionid=None
        )

        self.assertTrue(first_created)
        self.assertTrue(second_created)
        self.assertNotEqual(first.id, second.id)
        with database.get_session() as session:
            identities = session.execute(select(AuthIdentityRecord)).scalars().all()
        self.assertEqual(
            {(row.provider, row.issuer, row.subject) for row in identities},
            {
                ("wechat_miniapp", "wx-miniapp-a", "same-openid"),
                ("wechat_miniapp", "wx-miniapp-b", "same-openid"),
            },
        )

    def test_unionid_connects_web_and_miniapp_to_one_user(self) -> None:
        database = self._database()
        service = IdentityService(AuthIdentityRepository(database))

        miniapp_user, created = service.resolve_miniapp_user(
            app_id="wx-miniapp", openid="miniapp-openid", unionid="trusted-unionid"
        )
        web_user, web_created = service.resolve_open_web_user(
            app_id="wx-open-web", openid="web-openid", unionid="trusted-unionid"
        )

        self.assertTrue(created)
        self.assertFalse(web_created)
        self.assertEqual(web_user.id, miniapp_user.id)
        with database.get_session() as session:
            identities = session.execute(
                select(AuthIdentityRecord).where(AuthIdentityRecord.user_id == miniapp_user.id)
            ).scalars().all()
        self.assertEqual(
            {(row.provider, row.issuer, row.subject) for row in identities},
            {
                ("wechat_miniapp", "wx-miniapp", "miniapp-openid"),
                ("wechat_open_web", "wx-open-web", "web-openid"),
                ("wechat_unionid", "wechat", "trusted-unionid"),
            },
        )

    def test_conflicting_direct_and_union_identity_fails_closed(self) -> None:
        database = self._database()
        service = IdentityService(AuthIdentityRepository(database))
        service.resolve_miniapp_user(
            app_id="wx-miniapp", openid="direct-openid", unionid=None
        )
        service.resolve_open_web_user(
            app_id="wx-open-web", openid="web-openid", unionid="union-owned-elsewhere"
        )

        with self.assertRaisesRegex(AuthIdentityConflictError, "拒绝自动关联"):
            service.resolve_miniapp_user(
                app_id="wx-miniapp",
                openid="direct-openid",
                unionid="union-owned-elsewhere",
            )

    def test_miniapp_login_uses_issuer_and_hides_identity_conflicts(self) -> None:
        repository = MagicMock()
        user = SimpleNamespace(
            id=7,
            nickname=None,
            avatar_url=None,
            profile_updated_at=None,
            created_at=None,
            last_login_at=None,
        )
        repository.upsert_user_with_status.return_value = (user, True)
        config = SimpleNamespace(
            wechat_miniapp_app_id="wx-miniapp",
            wechat_miniapp_session_ttl_hours=2,
        )
        service = WechatMiniappAuthService(repository=repository, config=config)
        with patch.object(service, "_exchange_code", return_value={"openid": "openid"}), patch(
            "src.services.wechat_miniapp_auth_service.RbacService"
        ) as rbac_service:
            rbac_service.return_value.ensure_user_access.return_value = {
                "roles": [], "permissions": []
            }
            service.login("temporary-code")
        repository.upsert_user_with_status.assert_called_once_with(
            openid="openid",
            unionid=None,
            issuer="wx-miniapp",
        )

        repository.upsert_user_with_status.side_effect = AuthIdentityConflictError("internal")
        with patch.object(service, "_exchange_code", return_value={"openid": "openid"}):
            with self.assertRaisesRegex(MiniappAuthError, "当前微信身份暂时无法登录"):
                service.login("temporary-code")


if __name__ == "__main__":
    unittest.main()
