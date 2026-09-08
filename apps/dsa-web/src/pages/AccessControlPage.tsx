import { RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import {
  rbacAdminApi,
  type FeatureQuotaPlan,
  type FeatureQuotaPlanInput,
  type FeatureQuotaPolicy,
  type FeatureQuotaUserProfile,
  type Paginated,
  type RbacAuditEvent,
  type RbacCatalog,
  type RbacRole,
  type RbacRoleInput,
  type RbacUser,
} from '../api/rbacAdmin';
import {
  ApiErrorAlert,
  AppPage,
  Checkbox,
  ConfirmDialog,
  EmptyState,
  Input,
  PageHeader,
  Pagination,
} from '../components/common';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import { cn } from '../utils/cn';

type TabKey = 'users' | 'roles' | 'quotas' | 'audit';
type PendingAction =
  | { kind: 'roles'; user: RbacUser; roles: string[] }
  | { kind: 'active'; user: RbacUser; isActive: boolean }
  | { kind: 'delete-role'; role: RbacRole }
  | null;
type QuotaPlanForm = {
  code: string;
  name: string;
  description: string;
  isActive: boolean;
  limitDrafts: Record<string, string>;
};

const PAGE_SIZE = 20;
const blankRole: RbacRoleInput = { code: '', name: '', description: '', permissions: [] };
const blankQuotaPlan: QuotaPlanForm = { code: '', name: '', description: '', isActive: true, limitDrafts: {} };

function formatTimestamp(value: string | null | undefined, language: 'zh' | 'en'): string {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString(language === 'zh' ? 'zh-CN' : 'en-US');
}

function quotaPlanForm(plan: FeatureQuotaPlan): QuotaPlanForm {
  return {
    code: plan.code,
    name: plan.name,
    description: plan.description,
    isActive: plan.isActive,
    limitDrafts: Object.fromEntries(plan.limits.map((limit) => [limit.featureCode, String(limit.dailyLimit)])),
  };
}

export default function AccessControlPage() {
  const { t, language } = useUiLanguage();
  const [tab, setTab] = useState<TabKey>('users');
  const [catalog, setCatalog] = useState<RbacCatalog | null>(null);
  const [users, setUsers] = useState<Paginated<RbacUser> | null>(null);
  const [audit, setAudit] = useState<Paginated<RbacAuditEvent> | null>(null);
  const [quotaPolicies, setQuotaPolicies] = useState<FeatureQuotaPolicy[]>([]);
  const [quotaDrafts, setQuotaDrafts] = useState<Record<string, string>>({});
  const [quotaPlans, setQuotaPlans] = useState<FeatureQuotaPlan[]>([]);
  const [plansLoaded, setPlansLoaded] = useState(false);
  const [quotaPlanFormState, setQuotaPlanFormState] = useState<QuotaPlanForm>(blankQuotaPlan);
  const [editingPlanCode, setEditingPlanCode] = useState<string | null>(null);
  const [selectedQuotaUserId, setSelectedQuotaUserId] = useState<number | null>(null);
  const [quotaProfile, setQuotaProfile] = useState<FeatureQuotaUserProfile | null>(null);
  const [selectedPlanCode, setSelectedPlanCode] = useState('');
  const [overrideFeatureCode, setOverrideFeatureCode] = useState('');
  const [overrideDailyLimit, setOverrideDailyLimit] = useState('');
  const [overrideReason, setOverrideReason] = useState('');
  const [whitelistFeatureCode, setWhitelistFeatureCode] = useState('*');
  const [whitelistReason, setWhitelistReason] = useState('');
  const [query, setQuery] = useState('');
  const [userPage, setUserPage] = useState(1);
  const [auditPage, setAuditPage] = useState(1);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [pendingAction, setPendingAction] = useState<PendingAction>(null);
  const [roleForm, setRoleForm] = useState<RbacRoleInput>(blankRole);
  const [editingRoleCode, setEditingRoleCode] = useState<string | null>(null);

  const loadCatalog = useCallback(async () => {
    setCatalog(await rbacAdminApi.getCatalog());
  }, []);

  const loadUsers = useCallback(async (nextQuery = query, nextPage = userPage) => {
    setUsers(await rbacAdminApi.listUsers(nextQuery, nextPage, PAGE_SIZE));
  }, [query, userPage]);

  const loadAudit = useCallback(async (nextPage = auditPage) => {
    setAudit(await rbacAdminApi.listAudit(nextPage, PAGE_SIZE));
  }, [auditPage]);

  const loadQuotaPolicies = useCallback(async () => {
    const result = await rbacAdminApi.listFeatureQuotaPolicies();
    setQuotaPolicies(result);
    setQuotaDrafts(Object.fromEntries(result.map((policy) => [policy.featureCode, String(policy.dailyLimit)])));
  }, []);

  const loadQuotaPlans = useCallback(async () => {
    setQuotaPlans(await rbacAdminApi.listFeatureQuotaPlans());
    setPlansLoaded(true);
  }, []);

  const reload = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      await Promise.all([loadCatalog(), loadUsers(), loadQuotaPolicies(), loadAudit()]);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setIsLoading(false);
    }
  }, [loadAudit, loadCatalog, loadQuotaPolicies, loadUsers]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const groupedPermissions = useMemo(() => {
    const grouped = new Map<string, RbacCatalog['permissions']>();
    for (const permission of catalog?.permissions ?? []) {
      const current = grouped.get(permission.groupCode) ?? [];
      current.push(permission);
      grouped.set(permission.groupCode, current);
    }
    return [...grouped.entries()];
  }, [catalog]);

  const totalUserPages = Math.ceil((users?.total ?? 0) / PAGE_SIZE);
  const totalAuditPages = Math.ceil((audit?.total ?? 0) / PAGE_SIZE);
  const quotaOptions = quotaPolicies.map((policy) => ({ code: policy.featureCode, name: policy.name }));

  const updateUserPage = async (nextPage: number) => {
    setUserPage(nextPage);
    setError(null);
    try {
      await loadUsers(query, nextPage);
    } catch (err) {
      setError(getParsedApiError(err));
    }
  };

  const runSearch = async () => {
    setUserPage(1);
    setError(null);
    try {
      await loadUsers(query, 1);
    } catch (err) {
      setError(getParsedApiError(err));
    }
  };

  const updateAuditPage = async (nextPage: number) => {
    setAuditPage(nextPage);
    setError(null);
    try {
      await loadAudit(nextPage);
    } catch (err) {
      setError(getParsedApiError(err));
    }
  };

  const toggleRole = (roleCode: string, checked: boolean) => {
    setRoleForm((current) => ({
      ...current,
      permissions: checked
        ? [...new Set([...current.permissions, roleCode])]
        : current.permissions.filter((code) => code !== roleCode),
    }));
  };

  const refreshAudit = async () => {
    await loadAudit(1);
    setAuditPage(1);
  };

  const saveRole = async () => {
    setIsSaving(true);
    setError(null);
    try {
      if (editingRoleCode) {
        await rbacAdminApi.updateRole(editingRoleCode, {
          name: roleForm.name,
          description: roleForm.description,
          permissions: roleForm.permissions,
        });
      } else {
        await rbacAdminApi.createRole(roleForm);
      }
      setRoleForm(blankRole);
      setEditingRoleCode(null);
      await Promise.all([loadCatalog(), refreshAudit()]);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setIsSaving(false);
    }
  };

  const saveQuotaPolicy = async (policy: FeatureQuotaPolicy) => {
    const dailyLimit = Number(quotaDrafts[policy.featureCode] ?? String(policy.dailyLimit));
    if (!Number.isInteger(dailyLimit) || dailyLimit < 0 || dailyLimit > 10000) {
      setError(getParsedApiError(new Error(t('accessControl.quotaInvalidLimit'))));
      return;
    }
    setIsSaving(true);
    setError(null);
    try {
      await rbacAdminApi.updateFeatureQuotaPolicy(policy.featureCode, dailyLimit);
      await Promise.all([loadQuotaPolicies(), refreshAudit()]);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setIsSaving(false);
    }
  };

  const buildPlanInput = (): FeatureQuotaPlanInput | null => {
    const limits = [];
    for (const policy of quotaPolicies) {
      const raw = quotaPlanFormState.limitDrafts[policy.featureCode]?.trim();
      if (!raw) continue;
      const dailyLimit = Number(raw);
      if (!Number.isInteger(dailyLimit) || dailyLimit < 0 || dailyLimit > 10000) {
        setError(getParsedApiError(new Error(t('accessControl.quotaInvalidLimit'))));
        return null;
      }
      limits.push({ featureCode: policy.featureCode, dailyLimit });
    }
    return {
      code: quotaPlanFormState.code,
      name: quotaPlanFormState.name,
      description: quotaPlanFormState.description,
      isActive: quotaPlanFormState.isActive,
      limits,
    };
  };

  const saveQuotaPlan = async () => {
    const input = buildPlanInput();
    if (!input) return;
    setIsSaving(true);
    setError(null);
    try {
      if (editingPlanCode) {
        await rbacAdminApi.updateFeatureQuotaPlan(editingPlanCode, {
          name: input.name,
          description: input.description,
          isActive: input.isActive,
          limits: input.limits,
        });
      } else {
        await rbacAdminApi.createFeatureQuotaPlan(input);
      }
      setQuotaPlanFormState(blankQuotaPlan);
      setEditingPlanCode(null);
      await Promise.all([loadQuotaPlans(), refreshAudit()]);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setIsSaving(false);
    }
  };

  const openQuotaProfile = async (userId: number) => {
    if (selectedQuotaUserId === userId) {
      setSelectedQuotaUserId(null);
      setQuotaProfile(null);
      return;
    }
    setIsSaving(true);
    setError(null);
    try {
      const [profile] = await Promise.all([
        rbacAdminApi.getUserFeatureQuotaProfile(userId),
        plansLoaded ? Promise.resolve() : loadQuotaPlans(),
      ]);
      setSelectedQuotaUserId(userId);
      setQuotaProfile(profile);
      setSelectedPlanCode(profile.planAssignment?.planCode ?? '');
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setIsSaving(false);
    }
  };

  const updateQuotaProfile = async (operation: () => Promise<FeatureQuotaUserProfile>) => {
    setIsSaving(true);
    setError(null);
    try {
      const profile = await operation();
      setQuotaProfile(profile);
      setSelectedPlanCode(profile.planAssignment?.planCode ?? '');
      await refreshAudit();
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setIsSaving(false);
    }
  };

  const confirmAction = async () => {
    if (!pendingAction) return;
    setIsSaving(true);
    setError(null);
    try {
      if (pendingAction.kind === 'roles') {
        await rbacAdminApi.replaceUserRoles(pendingAction.user.id, pendingAction.roles);
        await Promise.all([loadUsers(), refreshAudit()]);
      } else if (pendingAction.kind === 'active') {
        await rbacAdminApi.setUserActive(pendingAction.user.id, pendingAction.isActive);
        await Promise.all([loadUsers(), refreshAudit()]);
      } else {
        await rbacAdminApi.deleteRole(pendingAction.role.code);
        if (editingRoleCode === pendingAction.role.code) {
          setEditingRoleCode(null);
          setRoleForm(blankRole);
        }
        await Promise.all([loadCatalog(), refreshAudit()]);
      }
      setPendingAction(null);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setIsSaving(false);
    }
  };

  const pendingDialog = useMemo(() => {
    if (!pendingAction) return null;
    if (pendingAction.kind === 'roles') {
      return {
        title: t('accessControl.confirmRolesTitle'),
        message: t('accessControl.confirmRolesMessage', { name: pendingAction.user.nickname || `#${pendingAction.user.id}` }),
        confirmText: t('accessControl.saveRoles'),
        isDanger: false,
      };
    }
    if (pendingAction.kind === 'active') {
      return {
        title: pendingAction.isActive ? t('accessControl.enableUser') : t('accessControl.disableUser'),
        message: t('accessControl.confirmStatusMessage', { name: pendingAction.user.nickname || `#${pendingAction.user.id}` }),
        confirmText: pendingAction.isActive ? t('common.enabled') : t('common.disabled'),
        isDanger: !pendingAction.isActive,
      };
    }
    return {
      title: t('accessControl.deleteRole'),
      message: t('accessControl.confirmDeleteRoleMessage', { name: pendingAction.role.name }),
      confirmText: t('common.delete'),
      isDanger: true,
    };
  }, [pendingAction, t]);

  const showTab = async (nextTab: TabKey) => {
    setTab(nextTab);
    if (nextTab !== 'quotas' || plansLoaded) return;
    setError(null);
    try {
      await loadQuotaPlans();
    } catch (err) {
      setError(getParsedApiError(err));
    }
  };

  return (
    <AppPage className="space-y-5">
      <PageHeader
        eyebrow="RBAC"
        title={t('layout.route.accessControl.title')}
        description={t('layout.route.accessControl.description')}
        actions={(
          <button type="button" className="btn-secondary inline-flex items-center gap-2" onClick={() => void reload()} disabled={isLoading || isSaving}>
            <RefreshCw className={cn('h-4 w-4', isLoading ? 'animate-spin' : '')} aria-hidden="true" />
            {t('accessControl.refresh')}
          </button>
        )}
      />

      {error ? <ApiErrorAlert error={error} /> : null}

      <div className="flex flex-wrap gap-2 border-b border-border/60 pb-3" role="tablist" aria-label={t('accessControl.tabsLabel')}>
        {(['users', 'roles', 'quotas', 'audit'] as TabKey[]).map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={cn('rounded-xl px-4 py-2 text-sm font-medium transition-colors', tab === key ? 'bg-cyan text-slate-950' : 'text-secondary-text hover:bg-hover hover:text-foreground')}
            onClick={() => void showTab(key)}
          >
            {t(`accessControl.tab.${key}`)}
          </button>
        ))}
      </div>

      {tab === 'users' ? (
        <section className="space-y-4" aria-label={t('accessControl.usersTitle')}>
          <div className="glass-panel flex flex-col gap-3 p-4 md:flex-row md:items-end">
            <div className="flex-1">
              <Input
                label={t('accessControl.searchUsers')}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => { if (event.key === 'Enter') void runSearch(); }}
                placeholder={t('accessControl.searchUsersPlaceholder')}
              />
            </div>
            <button type="button" className="btn-primary h-11" onClick={() => void runSearch()}>{t('accessControl.search')}</button>
          </div>
          {isLoading ? <p className="text-secondary-text">{t('common.loading')}</p> : null}
          {!isLoading && users?.items.length === 0 ? <EmptyState title={t('accessControl.emptyUsersTitle')} description={t('accessControl.emptyUsersDescription')} /> : null}
          {users?.items.map((user) => (
            <article key={user.id} className="glass-panel space-y-4 p-4">
              <div className="flex flex-col justify-between gap-3 md:flex-row md:items-start">
                <div>
                  <h2 className="text-base font-semibold text-foreground">{user.nickname || t('accessControl.unnamedUser', { id: user.id })}</h2>
                  <p className="mt-1 text-xs text-secondary-text">ID #{user.id} · {t('accessControl.lastLogin')}: {formatTimestamp(user.lastLoginAt, language)}</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button type="button" className="btn-secondary" onClick={() => void openQuotaProfile(user.id)} disabled={isSaving}>
                    {selectedQuotaUserId === user.id ? t('accessControl.hideQuotaProfile') : t('accessControl.manageQuota')}
                  </button>
                  <button
                    type="button"
                    className={cn('rounded-lg px-3 py-2 text-sm font-medium', user.isActive ? 'bg-danger/15 text-danger hover:bg-danger/25' : 'bg-cyan/15 text-cyan hover:bg-cyan/25')}
                    onClick={() => setPendingAction({ kind: 'active', user, isActive: !user.isActive })}
                  >
                    {user.isActive ? t('accessControl.disableUser') : t('accessControl.enableUser')}
                  </button>
                </div>
              </div>
              <fieldset className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                <legend className="mb-2 text-sm font-medium text-foreground">{t('accessControl.roles')}</legend>
                {(catalog?.roles ?? []).map((role) => {
                  const checked = user.roles.includes(role.code);
                  return (
                    <Checkbox
                      key={role.code}
                      label={`${role.name} (${role.code})`}
                      checked={checked}
                      onChange={(event) => {
                        const roles = event.target.checked ? [...user.roles, role.code] : user.roles.filter((code) => code !== role.code);
                        setPendingAction({ kind: 'roles', user, roles });
                      }}
                    />
                  );
                })}
              </fieldset>
              {selectedQuotaUserId === user.id && quotaProfile ? (
                <div className="space-y-4 border-t border-border/60 pt-4">
                  <div className="grid gap-3 lg:grid-cols-3">
                    <label className="block text-sm font-medium text-foreground">
                      {t('accessControl.userPlan')}
                      <select className="mt-1 h-11 w-full rounded-lg border border-border bg-surface px-3 text-foreground" value={selectedPlanCode} onChange={(event) => setSelectedPlanCode(event.target.value)}>
                        <option value="">{t('accessControl.noPlan')}</option>
                        {quotaPlans.filter((plan) => plan.isActive || plan.code === quotaProfile.planAssignment?.planCode).map((plan) => <option key={plan.code} value={plan.code}>{plan.name} ({plan.code})</option>)}
                      </select>
                    </label>
                    <div className="flex items-end">
                      <button type="button" className="btn-primary h-11" disabled={isSaving} onClick={() => void updateQuotaProfile(() => rbacAdminApi.assignUserFeatureQuotaPlan(user.id, selectedPlanCode || null))}>{t('accessControl.saveUserPlan')}</button>
                    </div>
                    <p className="self-end pb-2 text-xs text-secondary-text">{quotaProfile.planAssignment ? `${quotaProfile.planAssignment.planName} · ${quotaProfile.planAssignment.effectiveFrom}` : t('accessControl.planFallbackHint')}</p>
                  </div>

                  <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_160px_minmax(0,1fr)_auto] lg:items-end">
                    <label className="block text-sm font-medium text-foreground">{t('accessControl.overrideFeature')}
                      <select className="mt-1 h-11 w-full rounded-lg border border-border bg-surface px-3 text-foreground" value={overrideFeatureCode} onChange={(event) => setOverrideFeatureCode(event.target.value)}>
                        <option value="">{t('common.selectPlaceholder')}</option>
                        {quotaOptions.map((option) => <option key={option.code} value={option.code}>{option.name}</option>)}
                      </select>
                    </label>
                    <Input label={t('accessControl.dailyLimit')} type="number" min="0" max="10000" value={overrideDailyLimit} onChange={(event) => setOverrideDailyLimit(event.target.value)} />
                    <Input label={t('accessControl.quotaReason')} value={overrideReason} onChange={(event) => setOverrideReason(event.target.value)} />
                    <button type="button" className="btn-secondary h-11" disabled={isSaving || !overrideFeatureCode} onClick={() => {
                      const dailyLimit = Number(overrideDailyLimit);
                      if (!Number.isInteger(dailyLimit) || dailyLimit < 0 || dailyLimit > 10000) { setError(getParsedApiError(new Error(t('accessControl.quotaInvalidLimit')))); return; }
                      void updateQuotaProfile(async () => {
                        const profile = await rbacAdminApi.setUserFeatureQuotaOverride(user.id, overrideFeatureCode, dailyLimit, overrideReason);
                        setOverrideDailyLimit(''); setOverrideReason(''); return profile;
                      });
                    }}>{t('accessControl.saveOverride')}</button>
                  </div>
                  {quotaProfile.overrides.length > 0 ? <div className="flex flex-wrap gap-2">{quotaProfile.overrides.map((entry) => <span key={entry.featureCode} className="rounded-lg bg-muted px-3 py-2 text-sm text-secondary-text">{entry.featureCode}: {entry.dailyLimit} {entry.reason ? `· ${entry.reason}` : ''}<button type="button" className="ml-2 underline" disabled={isSaving} onClick={() => void updateQuotaProfile(() => rbacAdminApi.revokeUserFeatureQuotaOverride(user.id, entry.featureCode))}>{t('common.delete')}</button></span>)}</div> : null}

                  <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] lg:items-end">
                    <label className="block text-sm font-medium text-foreground">{t('accessControl.whitelistScope')}
                      <select className="mt-1 h-11 w-full rounded-lg border border-border bg-surface px-3 text-foreground" value={whitelistFeatureCode} onChange={(event) => setWhitelistFeatureCode(event.target.value)}>
                        <option value="*">{t('accessControl.allFeatures')}</option>
                        {quotaOptions.map((option) => <option key={option.code} value={option.code}>{option.name}</option>)}
                      </select>
                    </label>
                    <Input label={t('accessControl.quotaReason')} value={whitelistReason} onChange={(event) => setWhitelistReason(event.target.value)} />
                    <button type="button" className="btn-secondary h-11" disabled={isSaving} onClick={() => void updateQuotaProfile(async () => {
                      const profile = await rbacAdminApi.setUserFeatureQuotaWhitelist(user.id, whitelistFeatureCode === '*' ? null : whitelistFeatureCode, whitelistReason);
                      setWhitelistReason(''); return profile;
                    })}>{t('accessControl.grantWhitelist')}</button>
                  </div>
                  {quotaProfile.whitelistEntries.length > 0 ? <div className="flex flex-wrap gap-2">{quotaProfile.whitelistEntries.map((entry) => <span key={entry.featureCode ?? '*'} className="rounded-lg bg-cyan/10 px-3 py-2 text-sm text-cyan">{entry.featureCode ?? t('accessControl.allFeatures')}{entry.reason ? ` · ${entry.reason}` : ''}<button type="button" className="ml-2 underline" disabled={isSaving} onClick={() => void updateQuotaProfile(() => rbacAdminApi.revokeUserFeatureQuotaWhitelist(user.id, entry.featureCode ?? null))}>{t('common.delete')}</button></span>)}</div> : null}

                  <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                    {quotaProfile.entitlements.map((item) => <div key={item.featureCode} className="rounded-xl border border-border/60 p-3"><p className="font-medium text-foreground">{item.name}</p><p className="mt-1 text-sm text-secondary-text">{item.unlimited ? t('accessControl.unlimited') : `${item.remaining ?? 0} / ${item.dailyLimit ?? 0}`}</p><p className="mt-1 text-xs text-muted-text">{t('accessControl.quotaSource')}: {item.limitSource}</p></div>)}
                  </div>
                </div>
              ) : null}
            </article>
          ))}
          <Pagination currentPage={userPage} totalPages={totalUserPages} onPageChange={(nextPage) => void updateUserPage(nextPage)} />
        </section>
      ) : null}

      {tab === 'roles' ? (
        <section className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(360px,0.9fr)]" aria-label={t('accessControl.rolesTitle')}>
          <div className="space-y-3">
            {(catalog?.roles ?? []).map((role) => (
              <article key={role.code} className="glass-panel flex flex-col gap-3 p-4 md:flex-row md:items-start md:justify-between">
                <div><div className="flex flex-wrap items-center gap-2"><h2 className="font-semibold text-foreground">{role.name}</h2>{role.isSystem ? <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-secondary-text">{t('common.readOnly')}</span> : null}</div><p className="mt-1 text-sm text-secondary-text">{role.description || '—'}</p><p className="mt-2 text-xs text-muted-text">{role.code} · {role.permissions.length} {t('accessControl.permissions')}</p></div>
                {!role.isSystem ? <div className="flex gap-2"><button type="button" className="btn-secondary" onClick={() => { setEditingRoleCode(role.code); setRoleForm({ code: role.code, name: role.name, description: role.description, permissions: role.permissions }); }}>{t('accessControl.editRole')}</button><button type="button" className="btn-danger" onClick={() => setPendingAction({ kind: 'delete-role', role })}>{t('accessControl.deleteRole')}</button></div> : null}
              </article>
            ))}
          </div>
          <form className="glass-panel space-y-4 p-5" onSubmit={(event) => { event.preventDefault(); void saveRole(); }}>
            <div className="flex items-center justify-between gap-3"><h2 className="text-lg font-semibold text-foreground">{editingRoleCode ? t('accessControl.editRole') : t('accessControl.createRole')}</h2>{editingRoleCode ? <button type="button" className="text-sm text-secondary-text underline" onClick={() => { setEditingRoleCode(null); setRoleForm(blankRole); }}>{t('common.cancel')}</button> : null}</div>
            <Input label={t('accessControl.roleCode')} value={roleForm.code} disabled={Boolean(editingRoleCode)} required onChange={(event) => setRoleForm((current) => ({ ...current, code: event.target.value }))} />
            <Input label={t('accessControl.roleName')} value={roleForm.name} required onChange={(event) => setRoleForm((current) => ({ ...current, name: event.target.value }))} />
            <Input label={t('accessControl.roleDescription')} value={roleForm.description} onChange={(event) => setRoleForm((current) => ({ ...current, description: event.target.value }))} />
            <fieldset className="max-h-72 space-y-4 overflow-y-auto rounded-xl border border-border/60 p-3"><legend className="px-1 text-sm font-medium text-foreground">{t('accessControl.permissions')}</legend>{groupedPermissions.map(([groupCode, permissions]) => <div key={groupCode} className="space-y-2"><p className="text-xs font-semibold uppercase tracking-wide text-muted-text">{groupCode}</p>{permissions.map((permission) => <Checkbox key={permission.code} label={`${permission.code} · ${permission.description}`} checked={roleForm.permissions.includes(permission.code)} onChange={(event) => toggleRole(permission.code, event.target.checked)} />)}</div>)}</fieldset>
            <button type="submit" className="btn-primary w-full" disabled={isSaving}>{isSaving ? t('common.processing') : t('accessControl.saveRole')}</button>
          </form>
        </section>
      ) : null}

      {tab === 'quotas' ? (
        <section className="space-y-4" aria-label={t('accessControl.quotasTitle')}>
          <div className="glass-panel space-y-1 p-4"><h2 className="text-base font-semibold text-foreground">{t('accessControl.quotasTitle')}</h2><p className="text-sm text-secondary-text">{t('accessControl.quotasDescription')}</p></div>
          {quotaPolicies.map((policy) => <article key={policy.featureCode} className="glass-panel flex flex-col gap-4 p-4 md:flex-row md:items-end md:justify-between"><div className="min-w-0 flex-1"><h3 className="font-semibold text-foreground">{policy.name}</h3><p className="mt-1 text-sm text-secondary-text">{policy.description}</p><p className="mt-2 text-xs text-muted-text">{t('accessControl.quotaUpdatedAt')}: {formatTimestamp(policy.updatedAt, language)}</p></div><div className="flex flex-col gap-2 sm:flex-row sm:items-end"><Input label={t('accessControl.dailyLimit')} type="number" min="0" max="10000" step="1" value={quotaDrafts[policy.featureCode] ?? String(policy.dailyLimit)} onChange={(event) => setQuotaDrafts((current) => ({ ...current, [policy.featureCode]: event.target.value }))} /><button type="button" className="btn-primary h-11 whitespace-nowrap" disabled={isSaving} onClick={() => void saveQuotaPolicy(policy)}>{isSaving ? t('common.processing') : t('accessControl.saveQuota')}</button></div></article>)}

          <div className="glass-panel space-y-4 p-5">
            <div className="flex items-center justify-between gap-3"><div><h2 className="text-lg font-semibold text-foreground">{editingPlanCode ? t('accessControl.editPlan') : t('accessControl.createPlan')}</h2><p className="text-sm text-secondary-text">{t('accessControl.planDescription')}</p></div>{editingPlanCode ? <button type="button" className="text-sm underline" onClick={() => { setEditingPlanCode(null); setQuotaPlanFormState(blankQuotaPlan); }}>{t('common.cancel')}</button> : null}</div>
            <div className="grid gap-3 md:grid-cols-2"><Input label={t('accessControl.planCode')} value={quotaPlanFormState.code} disabled={Boolean(editingPlanCode)} required onChange={(event) => setQuotaPlanFormState((current) => ({ ...current, code: event.target.value }))} /><Input label={t('accessControl.planName')} value={quotaPlanFormState.name} required onChange={(event) => setQuotaPlanFormState((current) => ({ ...current, name: event.target.value }))} /><Input label={t('accessControl.planDescriptionLabel')} value={quotaPlanFormState.description} onChange={(event) => setQuotaPlanFormState((current) => ({ ...current, description: event.target.value }))} /><Checkbox label={t('accessControl.planActive')} checked={quotaPlanFormState.isActive} onChange={(event) => setQuotaPlanFormState((current) => ({ ...current, isActive: event.target.checked }))} /></div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{quotaPolicies.map((policy) => <Input key={policy.featureCode} label={`${policy.name} (${t('accessControl.planFallback')})`} type="number" min="0" max="10000" placeholder={t('accessControl.inheritGlobal')} value={quotaPlanFormState.limitDrafts[policy.featureCode] ?? ''} onChange={(event) => setQuotaPlanFormState((current) => ({ ...current, limitDrafts: { ...current.limitDrafts, [policy.featureCode]: event.target.value } }))} />)}</div>
            <button type="button" className="btn-primary" disabled={isSaving || !quotaPlanFormState.code || !quotaPlanFormState.name} onClick={() => void saveQuotaPlan()}>{isSaving ? t('common.processing') : t('accessControl.savePlan')}</button>
          </div>
          {plansLoaded && quotaPlans.length === 0 ? <EmptyState title={t('accessControl.emptyPlansTitle')} description={t('accessControl.emptyPlansDescription')} /> : null}
          {quotaPlans.map((plan) => <article key={plan.code} className="glass-panel flex flex-col gap-3 p-4 md:flex-row md:items-start md:justify-between"><div><div className="flex items-center gap-2"><h3 className="font-semibold text-foreground">{plan.name}</h3><span className="rounded-full bg-muted px-2 py-0.5 text-xs text-secondary-text">{plan.isActive ? t('common.enabled') : t('common.disabled')}</span></div><p className="mt-1 text-sm text-secondary-text">{plan.description || '—'}</p><p className="mt-2 text-xs text-muted-text">{plan.code} · {plan.limits.length} {t('accessControl.planLimitCount')}</p></div><button type="button" className="btn-secondary" onClick={() => { setEditingPlanCode(plan.code); setQuotaPlanFormState(quotaPlanForm(plan)); }}>{t('accessControl.editPlan')}</button></article>)}
        </section>
      ) : null}

      {tab === 'audit' ? (
        <section className="space-y-3" aria-label={t('accessControl.auditTitle')}>
          {!isLoading && audit?.items.length === 0 ? <EmptyState title={t('accessControl.emptyAuditTitle')} description={t('accessControl.emptyAuditDescription')} /> : null}
          {audit?.items.map((event) => <article key={event.id} className="glass-panel flex flex-col gap-2 p-4 md:flex-row md:items-start md:justify-between"><div><h2 className="font-medium text-foreground">{event.action}</h2><p className="mt-1 text-sm text-secondary-text">{event.targetType}: {event.targetId}</p><p className="mt-2 text-xs text-muted-text">{t('accessControl.actor')}: {event.metadata.actorKind === 'web_admin' ? t('accessControl.webAdministrator') : event.actorUserId ?? '—'}</p></div><time className="text-xs text-muted-text">{formatTimestamp(event.createdAt, language)}</time></article>)}
          <Pagination currentPage={auditPage} totalPages={totalAuditPages} onPageChange={(nextPage) => void updateAuditPage(nextPage)} />
        </section>
      ) : null}

      <ConfirmDialog isOpen={pendingAction !== null} title={pendingDialog?.title ?? ''} message={pendingDialog?.message ?? ''} confirmText={pendingDialog?.confirmText} cancelText={t('common.cancel')} confirmDisabled={isSaving} cancelDisabled={isSaving} isDanger={pendingDialog?.isDanger} onConfirm={() => void confirmAction()} onCancel={() => setPendingAction(null)} />
    </AppPage>
  );
}
