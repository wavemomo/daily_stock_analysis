import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../contexts/UiLanguageContext';
import { UI_LANGUAGE_STORAGE_KEY } from '../../utils/uiLanguage';
import AccessControlPage from '../AccessControlPage';

const {
  getCatalog,
  listUsers,
  listAudit,
  listFeatureQuotaPolicies,
  listFeatureQuotaPlans,
  getUserFeatureQuotaProfile,
  replaceUserRoles,
  setUserActive,
  createRole,
  updateRole,
  deleteRole,
  updateFeatureQuotaPolicy,
  createFeatureQuotaPlan,
  updateFeatureQuotaPlan,
  assignUserFeatureQuotaPlan,
  setUserFeatureQuotaOverride,
  revokeUserFeatureQuotaOverride,
  setUserFeatureQuotaWhitelist,
  revokeUserFeatureQuotaWhitelist,
} = vi.hoisted(() => ({
  getCatalog: vi.fn(),
  listUsers: vi.fn(),
  listAudit: vi.fn(),
  listFeatureQuotaPolicies: vi.fn(),
  listFeatureQuotaPlans: vi.fn(),
  getUserFeatureQuotaProfile: vi.fn(),
  replaceUserRoles: vi.fn(),
  setUserActive: vi.fn(),
  createRole: vi.fn(),
  updateRole: vi.fn(),
  deleteRole: vi.fn(),
  updateFeatureQuotaPolicy: vi.fn(),
  createFeatureQuotaPlan: vi.fn(),
  updateFeatureQuotaPlan: vi.fn(),
  assignUserFeatureQuotaPlan: vi.fn(),
  setUserFeatureQuotaOverride: vi.fn(),
  revokeUserFeatureQuotaOverride: vi.fn(),
  setUserFeatureQuotaWhitelist: vi.fn(),
  revokeUserFeatureQuotaWhitelist: vi.fn(),
}));

vi.mock('../../api/rbacAdmin', () => ({
  rbacAdminApi: {
    getCatalog,
    listUsers,
    listAudit,
    listFeatureQuotaPolicies,
    listFeatureQuotaPlans,
    getUserFeatureQuotaProfile,
    replaceUserRoles,
    setUserActive,
    createRole,
    updateRole,
    deleteRole,
    updateFeatureQuotaPolicy,
    createFeatureQuotaPlan,
    updateFeatureQuotaPlan,
    assignUserFeatureQuotaPlan,
    setUserFeatureQuotaOverride,
    revokeUserFeatureQuotaOverride,
    setUserFeatureQuotaWhitelist,
    revokeUserFeatureQuotaWhitelist,
  },
}));

const catalog = {
  permissions: [
    { code: 'users.read', groupCode: 'users', description: '查看用户' },
  ],
  roles: [
    {
      code: 'operator',
      name: '运营角色',
      description: '管理用户访问',
      isSystem: false,
      permissions: ['users.read'],
    },
  ],
};

const users = {
  items: [
    {
      id: 42,
      nickname: '测试用户',
      avatarUrl: null,
      createdAt: '2026-03-20T08:00:00Z',
      lastLoginAt: '2026-03-20T09:00:00Z',
      isActive: true,
      roles: ['operator'],
      permissions: ['users.read'],
    },
  ],
  total: 1,
  page: 1,
  pageSize: 20,
};

const quotaPolicies = [
  {
    featureCode: 'stock_analysis',
    name: '个股分析',
    description: '消耗 LLM 的个股分析任务。',
    dailyLimit: 5,
    updatedAt: '2026-03-20T10:00:00Z',
    updatedByUserId: null,
  },
];

const quotaPlans = [
  {
    code: 'pro',
    name: '专业套餐',
    description: '专业用户每日额度。',
    isActive: true,
    limits: [{ featureCode: 'stock_analysis', dailyLimit: 20 }],
  },
];

const quotaProfile = {
  userId: 42,
  planAssignment: null,
  overrides: [],
  whitelistEntries: [],
  entitlements: [
    {
      featureCode: 'stock_analysis',
      name: '个股分析',
      description: '消耗 LLM 的个股分析任务。',
      dailyLimit: 5,
      usedCount: 2,
      remaining: 3,
      unlimited: false,
      disabled: false,
      resetAt: '2026-03-21T00:00:00Z',
      limitSource: 'global_policy',
      planCode: null,
      effectiveUntil: null,
    },
  ],
};

const audit = {
  items: [
    {
      id: 7,
      action: 'user.deactivated',
      targetType: 'user',
      targetId: '42',
      actorUserId: null,
      metadata: { actorKind: 'web_admin' },
      createdAt: '2026-03-20T10:00:00Z',
    },
  ],
  total: 1,
  page: 1,
  pageSize: 20,
};

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

function renderPage() {
  return render(
    <UiLanguageProvider>
      <AccessControlPage />
    </UiLanguageProvider>
  );
}

describe('AccessControlPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'zh');
    getCatalog.mockResolvedValue(catalog);
    listUsers.mockResolvedValue(users);
    listAudit.mockResolvedValue(audit);
    listFeatureQuotaPolicies.mockResolvedValue(quotaPolicies);
    listFeatureQuotaPlans.mockResolvedValue(quotaPlans);
    getUserFeatureQuotaProfile.mockResolvedValue(quotaProfile);
    replaceUserRoles.mockResolvedValue({ roles: ['operator'], permissions: ['users.read'] });
    setUserActive.mockResolvedValue({ id: 42, isActive: false });
    createRole.mockResolvedValue(catalog.roles[0]);
    updateRole.mockResolvedValue(catalog.roles[0]);
    deleteRole.mockResolvedValue(undefined);
    updateFeatureQuotaPolicy.mockResolvedValue({ ...quotaPolicies[0], dailyLimit: 8 });
    createFeatureQuotaPlan.mockResolvedValue(quotaPlans[0]);
    updateFeatureQuotaPlan.mockResolvedValue(quotaPlans[0]);
    assignUserFeatureQuotaPlan.mockResolvedValue({
      ...quotaProfile,
      planAssignment: {
        planCode: 'pro',
        planName: '专业套餐',
        effectiveFrom: '2026-03-20',
        effectiveUntil: null,
      },
    });
    setUserFeatureQuotaOverride.mockResolvedValue(quotaProfile);
    revokeUserFeatureQuotaOverride.mockResolvedValue(quotaProfile);
    setUserFeatureQuotaWhitelist.mockResolvedValue(quotaProfile);
    revokeUserFeatureQuotaWhitelist.mockResolvedValue(quotaProfile);
  });

  it('loads users, catalog, quotas, and audit in parallel and displays each tab content', async () => {
    const catalogRequest = deferred<typeof catalog>();
    const usersRequest = deferred<typeof users>();
    const quotaRequest = deferred<typeof quotaPolicies>();
    const auditRequest = deferred<typeof audit>();
    getCatalog.mockImplementationOnce(() => catalogRequest.promise);
    listUsers.mockImplementationOnce(() => usersRequest.promise);
    listFeatureQuotaPolicies.mockImplementationOnce(() => quotaRequest.promise);
    listAudit.mockImplementationOnce(() => auditRequest.promise);

    renderPage();

    await waitFor(() => {
      expect(getCatalog).toHaveBeenCalledWith();
      expect(listUsers).toHaveBeenCalledWith('', 1, 20);
      expect(listFeatureQuotaPolicies).toHaveBeenCalledWith();
      expect(listAudit).toHaveBeenCalledWith(1, 20);
    });
    catalogRequest.resolve(catalog);
    usersRequest.resolve(users);
    quotaRequest.resolve(quotaPolicies);
    auditRequest.resolve(audit);

    expect(await screen.findByText('测试用户')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: '角色' }));
    expect(await screen.findByText('运营角色')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: '配额' }));
    expect(await screen.findByText('个股分析')).toBeInTheDocument();
    await waitFor(() => expect(listFeatureQuotaPlans).toHaveBeenCalledWith());

    fireEvent.click(screen.getByRole('tab', { name: '审计' }));
    expect(await screen.findByText('user.deactivated')).toBeInTheDocument();
  });

  it('updates a feature quota from the quota tab', async () => {
    renderPage();

    await screen.findByText('测试用户');
    fireEvent.click(screen.getByRole('tab', { name: '配额' }));
    await screen.findByText('个股分析');

    fireEvent.change(screen.getByLabelText('每日次数'), { target: { value: '8' } });
    fireEvent.click(screen.getByRole('button', { name: '保存配额' }));

    await waitFor(() => expect(updateFeatureQuotaPolicy).toHaveBeenCalledWith('stock_analysis', 8));
  });

  it('creates a quota plan with configured feature limits', async () => {
    renderPage();

    await screen.findByText('测试用户');
    fireEvent.click(screen.getByRole('tab', { name: '配额' }));
    await screen.findByText('创建套餐');

    fireEvent.change(screen.getByLabelText('套餐编码'), { target: { value: 'starter' } });
    fireEvent.change(screen.getByLabelText('套餐名称'), { target: { value: '入门套餐' } });
    fireEvent.change(screen.getByLabelText('个股分析 (留空则继承全局配额)'), { target: { value: '8' } });
    fireEvent.click(screen.getByRole('button', { name: '保存套餐' }));

    await waitFor(() => expect(createFeatureQuotaPlan).toHaveBeenCalledWith({
      code: 'starter',
      name: '入门套餐',
      description: '',
      isActive: true,
      limits: [{ featureCode: 'stock_analysis', dailyLimit: 8 }],
    }));
  });

  it('loads a user quota profile on demand and assigns a plan', async () => {
    renderPage();

    await screen.findByText('测试用户');
    fireEvent.click(screen.getByRole('button', { name: '管理配额' }));

    expect(await screen.findByLabelText('用户套餐')).toBeInTheDocument();
    await waitFor(() => {
      expect(getUserFeatureQuotaProfile).toHaveBeenCalledWith(42);
      expect(listFeatureQuotaPlans).toHaveBeenCalledWith();
    });
    expect(screen.getByText('3 / 5')).toBeInTheDocument();
    expect(screen.getByText('额度来源: global_policy')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('用户套餐'), { target: { value: 'pro' } });
    fireEvent.click(screen.getByRole('button', { name: '保存用户套餐' }));

    await waitFor(() => expect(assignUserFeatureQuotaPlan).toHaveBeenCalledWith(42, 'pro'));
  });

  it('deactivates an active user and refreshes users and audit after confirmation', async () => {
    renderPage();

    await screen.findByText('测试用户');
    vi.clearAllMocks();

    fireEvent.click(screen.getByRole('button', { name: '停用用户' }));
    expect(await screen.findByText('确认更新“测试用户”的启用状态吗？')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '未启用' }));

    await waitFor(() => expect(setUserActive).toHaveBeenCalledWith(42, false));
    await waitFor(() => expect(listUsers).toHaveBeenCalledWith('', 1, 20));
    await waitFor(() => expect(listAudit).toHaveBeenCalledWith(1, 20));
  });
});
