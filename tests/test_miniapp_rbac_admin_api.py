# -*- coding: utf-8 -*-
"""真实 SQLite 上的小程序权限管理 API 集成测试。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules['litellm'] = MagicMock()

from api.app import create_app
from src.config import Config
from src.repositories.miniapp_user_repo import MiniappUserRepository
from src.services.rbac_service import RbacService
from src.storage import DatabaseManager


class MiniappRbacAdminApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.db_path = self.data_dir / 'rbac_admin_api.db'
        self.env_path = self.data_dir / '.env'
        self.env_path.write_text(
            '\n'.join((
                'STOCK_LIST=600519',
                'GEMINI_API_KEY=test',
                'ADMIN_AUTH_ENABLED=false',
                f'DATABASE_PATH={self.db_path}',
            )) + '\n',
            encoding='utf-8',
        )
        os.environ['ENV_FILE'] = str(self.env_path)
        os.environ['DATABASE_PATH'] = str(self.db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()

        self.db = DatabaseManager.get_instance()
        self.user_repo = MiniappUserRepository(self.db)
        self.rbac = RbacService()
        self.admin = self.user_repo.upsert_user(openid='admin-openid', issuer='miniapp-rbac-api-test')
        self.member = self.user_repo.upsert_user(openid='member-openid', issuer='miniapp-rbac-api-test')
        self.target = self.user_repo.upsert_user(openid='target-openid', issuer='miniapp-rbac-api-test')
        self.rbac.repository.ensure_role(self.admin.id, 'admin')
        self.rbac.repository.ensure_role(self.member.id, 'member')
        self.rbac.repository.ensure_role(self.target.id, 'member')
        self.principals = {
            'admin-token': SimpleNamespace(
                user=self.admin,
                roles=('admin',),
                permissions=('rbac.manage',),
            ),
            'member-token': SimpleNamespace(
                user=self.member,
                roles=('member',),
                permissions=('account.self',),
            ),
        }
        self.auth_patch = patch(
            'src.services.wechat_miniapp_auth_service.WechatMiniappAuthService.authenticate_token',
            side_effect=lambda token: self.principals.get(token),
        )
        self.auth_patch.start()
        self.client = TestClient(create_app(static_dir=self.data_dir / 'empty-static'))

    def tearDown(self) -> None:
        self.auth_patch.stop()
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop('ENV_FILE', None)
        os.environ.pop('DATABASE_PATH', None)
        self.temp_dir.cleanup()

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {'Authorization': f'Bearer {token}'}

    def test_directory_requires_admin_and_never_leaks_identity_or_session_secrets(self) -> None:
        unauthenticated = self.client.get('/api/v1/miniapp/rbac/users')
        forbidden = self.client.get('/api/v1/miniapp/rbac/users', headers=self._headers('member-token'))
        self.assertEqual(unauthenticated.status_code, 401, unauthenticated.text)
        self.assertEqual(forbidden.status_code, 403, forbidden.text)

        self.user_repo.update_profile(user_id=self.target.id, nickname='目标用户', avatar_url=None)
        response = self.client.get(
            '/api/v1/miniapp/rbac/users',
            headers=self._headers('admin-token'),
            params={'query': '目标', 'page': 1, 'page_size': 1},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload['total'], 1)
        self.assertEqual(payload['page'], 1)
        self.assertEqual(payload['page_size'], 1)
        item = payload['items'][0]
        self.assertEqual(item['id'], self.target.id)
        self.assertEqual(item['roles'], ['member'])
        self.assertTrue(item['is_active'])
        serialized = response.text.lower()
        for sensitive_key in ('openid', 'unionid', 'token_hash', 'access_token'):
            self.assertNotIn(sensitive_key, serialized)

    def test_custom_role_assignment_status_and_audit_lifecycle(self) -> None:
        created = self.client.post(
            '/api/v1/miniapp/rbac/roles',
            headers=self._headers('admin-token'),
            json={
                'code': 'research_reader',
                'name': '研报阅读员',
                'description': '只读研究能力',
                'permissions': ['analysis.read', 'stocks.read'],
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()['permissions'], ['analysis.read', 'stocks.read'])

        updated = self.client.put(
            '/api/v1/miniapp/rbac/roles/research_reader',
            headers=self._headers('admin-token'),
            json={
                'name': '研究员',
                'description': '可读可选股',
                'permissions': ['analysis.read', 'screening.read', 'stocks.read'],
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()['name'], '研究员')
        self.assertIn('screening.read', updated.json()['permissions'])

        role_update = self.client.put(
            f'/api/v1/miniapp/rbac/users/{self.target.id}/roles',
            headers=self._headers('admin-token'),
            json={'roles': ['research_reader']},
        )
        self.assertEqual(role_update.status_code, 200, role_update.text)
        self.assertEqual(role_update.json()['roles'], ['research_reader'])
        self.assertEqual(role_update.json()['permissions'], ['analysis.read', 'screening.read', 'stocks.read'])

        disabled = self.client.patch(
            f'/api/v1/miniapp/rbac/users/{self.target.id}/active',
            headers=self._headers('admin-token'),
            json={'is_active': False},
        )
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.assertFalse(disabled.json()['is_active'])

        delete_assigned = self.client.delete(
            '/api/v1/miniapp/rbac/roles/research_reader',
            headers=self._headers('admin-token'),
        )
        self.assertEqual(delete_assigned.status_code, 400, delete_assigned.text)

        audit = self.client.get('/api/v1/miniapp/rbac/audit', headers=self._headers('admin-token'))
        self.assertEqual(audit.status_code, 200, audit.text)
        actions = [item['action'] for item in audit.json()['items']]
        self.assertIn('role.created', actions)
        self.assertIn('role.updated', actions)
        self.assertIn('user.roles_replaced', actions)
        self.assertIn('user.status_changed', actions)
        self.assertNotIn('openid', audit.text.lower())

    def test_invalid_roles_missing_user_and_last_admin_protections(self) -> None:
        empty = self.client.put(
            f'/api/v1/miniapp/rbac/users/{self.target.id}/roles',
            headers=self._headers('admin-token'),
            json={'roles': []},
        )
        unknown = self.client.put(
            f'/api/v1/miniapp/rbac/users/{self.target.id}/roles',
            headers=self._headers('admin-token'),
            json={'roles': ['missing_role']},
        )
        missing = self.client.put(
            '/api/v1/miniapp/rbac/users/999999/roles',
            headers=self._headers('admin-token'),
            json={'roles': ['member']},
        )
        self.assertEqual(empty.status_code, 400, empty.text)
        self.assertEqual(unknown.status_code, 400, unknown.text)
        self.assertEqual(missing.status_code, 404, missing.text)

        cannot_remove_self = self.client.put(
            f'/api/v1/miniapp/rbac/users/{self.admin.id}/roles',
            headers=self._headers('admin-token'),
            json={'roles': ['member']},
        )
        cannot_disable_self = self.client.patch(
            f'/api/v1/miniapp/rbac/users/{self.admin.id}/active',
            headers=self._headers('admin-token'),
            json={'is_active': False},
        )
        self.assertEqual(cannot_remove_self.status_code, 400, cannot_remove_self.text)
        self.assertEqual(cannot_disable_self.status_code, 400, cannot_disable_self.text)

        second_admin = self.user_repo.upsert_user(
            openid='second-admin-openid', issuer='miniapp-rbac-api-test'
        )
        self.rbac.repository.ensure_role(second_admin.id, 'admin')
        demoted = self.client.put(
            f'/api/v1/miniapp/rbac/users/{second_admin.id}/roles',
            headers=self._headers('admin-token'),
            json={'roles': ['member']},
        )
        self.assertEqual(demoted.status_code, 200, demoted.text)
        self.assertEqual(demoted.json()['roles'], ['member'])
    def test_custom_management_role_preserves_last_active_manager_invariant(self) -> None:
        created = self.client.post(
            '/api/v1/miniapp/rbac/roles',
            headers=self._headers('admin-token'),
            json={
                'code': 'rbac_manager',
                'name': '权限经理',
                'description': '可维护权限，但不是系统 admin 角色',
                'permissions': ['rbac.manage'],
            },
        )
        self.assertEqual(created.status_code, 201, created.text)

        assigned = self.client.put(
            f'/api/v1/miniapp/rbac/users/{self.member.id}/roles',
            headers=self._headers('admin-token'),
            json={'roles': ['rbac_manager']},
        )
        self.assertEqual(assigned.status_code, 200, assigned.text)
        self.principals['member-token'] = SimpleNamespace(
            user=self.member,
            roles=('rbac_manager',),
            permissions=('rbac.manage',),
        )

        demoted_admin = self.client.put(
            f'/api/v1/miniapp/rbac/users/{self.admin.id}/roles',
            headers=self._headers('member-token'),
            json={'roles': ['member']},
        )
        self.assertEqual(demoted_admin.status_code, 200, demoted_admin.text)

        cannot_self_remove_management = self.client.put(
            f'/api/v1/miniapp/rbac/users/{self.member.id}/roles',
            headers=self._headers('member-token'),
            json={'roles': ['member']},
        )
        self.assertEqual(cannot_self_remove_management.status_code, 400, cannot_self_remove_management.text)
        self.assertIn('rbac.manage', cannot_self_remove_management.text)

        cannot_remove_last_management_role = self.client.put(
            '/api/v1/miniapp/rbac/roles/rbac_manager',
            headers=self._headers('member-token'),
            json={
                'name': '权限经理',
                'description': '不再拥有管理权限',
                'permissions': [],
            },
        )
        self.assertEqual(cannot_remove_last_management_role.status_code, 400, cannot_remove_last_management_role.text)
        self.assertIn('rbac.manage', cannot_remove_last_management_role.text)

    def test_openid_never_auto_grants_admin_role(self) -> None:
        user = self.user_repo.upsert_user(
            openid='former-bootstrap-openid', issuer='miniapp-rbac-api-test'
        )

        access = self.rbac.ensure_user_access(user, assign_default=True)

        self.assertNotIn('admin', access['roles'])
        self.assertIn('member', access['roles'])
        audit = self.rbac.repository.list_audit_events(page=1, page_size=100)
        self.assertEqual(
            [event for event in audit['items'] if event['action'] == 'user.bootstrap_admin_granted'],
            [],
        )



if __name__ == '__main__':
    unittest.main()
