import React, { useEffect, useState } from 'react';
import { Activity, BarChart3, Bell, BriefcaseBusiness, Gauge, Home, LayoutGrid, LogOut, MessageSquareQuote, Search, Settings2, ShieldCheck, TicketCheck } from 'lucide-react';
import { NavLink, useNavigate } from 'react-router-dom';
import { getParsedApiError } from '../../api/error';
import { SCREENING_CONFIG_CHANGED_EVENT, SYSTEM_CONFIG_CHANGED_EVENT, screeningApi } from '../../api/screening';
import { useAuth } from '../../contexts/AuthContext';
import { useFeatureQuotas } from '../../contexts/FeatureQuotaContext';
import { useAgentChatStore } from '../../stores/agentChatStore';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { UiTextKey } from '../../i18n/uiText';
import { cn } from '../../utils/cn';
import { ConfirmDialog } from '../common/ConfirmDialog';
import { StatusDot } from '../common/StatusDot';
import { UiLanguageToggle } from '../i18n/UiLanguageToggle';
import { ThemeToggle } from '../theme/ThemeToggle';

type SidebarNavProps = {
  collapsed?: boolean;
  onNavigate?: () => void;
  variant?: 'default' | 'rail';
};

type NavItem = {
  key: string;
  labelKey: UiTextKey;
  to: string;
  icon: React.ComponentType<{ className?: string }>;
  exact?: boolean;
  badge?: 'completion';
  requiredPermissions?: string[];
};

const NAV_ITEMS: NavItem[] = [
  { key: 'home', labelKey: 'layout.nav.home', to: '/', icon: Home, exact: true },
  { key: 'chat', labelKey: 'layout.nav.chat', to: '/chat', icon: MessageSquareQuote, badge: 'completion', requiredPermissions: ['agent.read'] },
  { key: 'screening', labelKey: 'layout.nav.screening', to: '/screening', icon: Search, requiredPermissions: ['screening.read'] },
  { key: 'portfolio', labelKey: 'layout.nav.portfolio', to: '/portfolio', icon: BriefcaseBusiness, requiredPermissions: ['portfolio.read'] },
  { key: 'decision-signals', labelKey: 'layout.nav.decisionSignals', to: '/decision-signals', icon: Activity, requiredPermissions: ['decision_signals.read'] },
  { key: 'report-gallery', labelKey: 'layout.nav.reportGallery', to: '/report-gallery', icon: LayoutGrid, requiredPermissions: ['analysis.read'] },
  { key: 'backtest', labelKey: 'layout.nav.backtest', to: '/backtest', icon: BarChart3, requiredPermissions: ['backtest.read'] },
  { key: 'alerts', labelKey: 'layout.nav.alerts', to: '/alerts', icon: Bell, requiredPermissions: ['alerts.read'] },
  { key: 'usage', labelKey: 'layout.nav.usage', to: '/usage', icon: Gauge, requiredPermissions: ['usage.read'] },
  { key: 'feature-quotas', labelKey: 'layout.nav.featureQuotas', to: '/feature-quotas', icon: TicketCheck },
  { key: 'access-control', labelKey: 'layout.nav.accessControl', to: '/access-control', icon: ShieldCheck, requiredPermissions: ['rbac.manage'] },
  { key: 'settings', labelKey: 'layout.nav.settings', to: '/settings', icon: Settings2, requiredPermissions: ['system.manage'] },
];

export const SidebarNav: React.FC<SidebarNavProps> = ({ collapsed = false, onNavigate, variant = 'default' }) => {
  const { actor, hasAnyPermission, logout } = useAuth();
  const navigate = useNavigate();
  const { describe: describeQuotas, hasLoadError, refresh: refreshQuotas } = useFeatureQuotas();
  const { t } = useUiLanguage();
  const completionBadge = useAgentChatStore((state) => state.completionBadge);
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const [showScreeningNav, setShowScreeningNav] = useState(false);

  useEffect(() => {
    let active = true;

    const refreshScreeningStatus = async () => {
      try {
        const status = await screeningApi.getStatus();
        if (active) {
          setShowScreeningNav(status.enabled);
        }
      } catch {
        if (active) {
          setShowScreeningNav(false);
        }
      }
    };

    void refreshScreeningStatus();
    window.addEventListener(SCREENING_CONFIG_CHANGED_EVENT, refreshScreeningStatus);
    window.addEventListener(SYSTEM_CONFIG_CHANGED_EVENT, refreshScreeningStatus);

    return () => {
      active = false;
      window.removeEventListener(SCREENING_CONFIG_CHANGED_EVENT, refreshScreeningStatus);
      window.removeEventListener(SYSTEM_CONFIG_CHANGED_EVENT, refreshScreeningStatus);
    };
  }, []);

  const navItems = (showScreeningNav ? NAV_ITEMS : NAV_ITEMS.filter((item) => item.key !== 'screening'))
    .filter((item) => !item.requiredPermissions || hasAnyPermission(item.requiredPermissions));
  const isRail = variant === 'rail';
  const itemBaseClass = cn(
    'group relative flex h-[var(--nav-item-height)] w-full items-center overflow-hidden rounded-2xl border border-transparent text-sm leading-none text-secondary-text transition-all',
    isRail
      ? 'justify-start gap-2.5 px-3'
      : collapsed
        ? 'justify-center px-0'
        : 'gap-3 px-[var(--nav-item-padding-x)]'
  );
  const itemInteractiveClass = cn(
    itemBaseClass,
    'hover:bg-[var(--nav-hover-bg)] hover:text-foreground'
  );
  const itemActiveClass = 'border-[var(--nav-active-border)] bg-[var(--nav-active-bg)] font-medium text-[hsl(var(--primary))]';
  const itemIconClass = cn(isRail ? 'h-[18px] w-[18px]' : 'h-5 w-5', 'shrink-0');
  const itemLabelClass = cn('min-w-0 flex-1 truncate', isRail ? 'text-left' : '');

  const handleLogoutConfirm = async () => {
    setIsLoggingOut(true);
    setLogoutError(null);
    try {
      await logout();
      setShowLogoutConfirm(false);
      onNavigate?.();
      navigate('/login', { replace: true });
    } catch (error) {
      const parsed = getParsedApiError(error);
      const csrfFailed = parsed.status === 403
        && `${parsed.rawMessage} ${parsed.message}`.toLowerCase().includes('csrf_failed');
      setLogoutError(t(csrfFailed ? 'layout.logoutCsrfFailed' : 'layout.logoutFailed'));
    } finally {
      setIsLoggingOut(false);
    }
  };

  return (
    <div className="flex h-full w-full min-w-0 flex-col">
      <div
        className={cn(
          'flex items-center',
          isRail ? 'mb-5 justify-start gap-2 px-3 pt-1' : 'mb-4 gap-2 px-1',
          collapsed || isRail ? 'justify-center' : ''
        )}
      >
        <div
          className={cn(
            'flex items-center justify-center bg-primary-gradient text-[hsl(var(--primary-foreground))] shadow-[0_12px_28px_var(--nav-brand-shadow)]',
            isRail ? 'h-9 w-9 rounded-[1rem]' : 'h-10 w-10 rounded-2xl'
          )}
        >
          <BarChart3 className={cn(isRail ? 'h-[19px] w-[19px]' : 'h-5 w-5')} />
        </div>
        {!collapsed ? (
          <p className={cn('min-w-0 truncate font-semibold text-foreground', isRail ? 'text-[0.95rem] leading-none' : 'text-sm')}>DSA</p>
        ) : null}
      </div>

      <nav className={cn('flex flex-col gap-1.5', isRail ? '' : 'flex-1')} aria-label={t('layout.mainNav')}>
        {navItems.map(({ key, labelKey, to, icon: Icon, exact, badge }) => {
          const label = t(labelKey);
          return (
          <NavLink
            key={key}
            to={to}
            end={exact}
            onClick={onNavigate}
            aria-label={label}
            className={({ isActive }) =>
              cn(
                itemInteractiveClass,
                isActive ? itemActiveClass : ''
              )
            }
          >
            {({ isActive }) => (
              <>
                <Icon className={cn(itemIconClass, isActive ? 'text-[var(--nav-icon-active)]' : 'text-current')} />
                {!collapsed ? <span className={itemLabelClass}>{label}</span> : null}
                {badge === 'completion' && completionBadge ? (
                  <StatusDot
                    tone="info"
                    data-testid="chat-completion-badge"
                    className={cn(
                      'absolute right-3 border-2 border-background shadow-[0_0_10px_var(--nav-indicator-shadow)]',
                      collapsed ? 'right-2 top-2' : ''
                    )}
                    aria-label={t('layout.newChatMessage')}
                  />
                ) : null}
              </>
            )}
          </NavLink>
        );
        })}

        <ThemeToggle
          variant={isRail ? 'rail' : 'nav'}
          collapsed={collapsed}
          wrapperClassName="w-full"
          triggerClassName={itemInteractiveClass}
          triggerActiveClassName={itemActiveClass}
          iconClassName={itemIconClass}
          labelClassName={itemLabelClass}
        />
        <UiLanguageToggle
          variant={isRail ? 'rail' : 'nav'}
          collapsed={collapsed}
          wrapperClassName="w-full"
          triggerClassName={itemInteractiveClass}
          triggerActiveClassName={itemActiveClass}
          iconClassName={itemIconClass}
          labelClassName={itemLabelClass}
        />
      </nav>

      {actor !== 'anonymous' ? (
        <>
          <div className={cn('mt-auto px-2', collapsed ? 'sr-only' : '')}>
            <p className="text-xs text-secondary-text">{describeQuotas()}</p>
            {hasLoadError ? (
              <button
                type="button"
                onClick={() => void refreshQuotas()}
                className="mt-1 text-left text-xs font-medium text-primary hover:underline"
              >
                {t('layout.featureQuotasReload')}
              </button>
            ) : null}
          </div>
          <button
          type="button"
          onClick={() => {
            setLogoutError(null);
            setShowLogoutConfirm(true);
          }}
          className={cn(
            itemInteractiveClass,
            isRail ? 'mt-1.5' : 'mt-5'
          )}
        >
          <LogOut className={itemIconClass} />
          {!collapsed ? <span className={itemLabelClass}>{t('layout.logout')}</span> : null}
          </button>
        </>
      ) : null}

      <ConfirmDialog
        isOpen={showLogoutConfirm}
        title={t('layout.logoutTitle')}
        message={t('layout.webUserLogoutMessage')}
        confirmText={isLoggingOut ? t('layout.logoutPending') : t('layout.logoutConfirm')}
        cancelText={t('common.cancel')}
        confirmDisabled={isLoggingOut}
        cancelDisabled={isLoggingOut}
        isDanger
        onConfirm={() => {
          void handleLogoutConfirm();
        }}
        onCancel={() => {
          if (!isLoggingOut) {
            setLogoutError(null);
            setShowLogoutConfirm(false);
          }
        }}
      />
      {logoutError ? (
        <div role="alert" className="fixed bottom-5 left-1/2 z-[60] w-[min(90vw,28rem)] -translate-x-1/2 rounded-xl border border-red-400/40 bg-red-950/90 px-4 py-3 text-sm text-red-100 shadow-xl">
          {logoutError}
        </div>
      ) : null}
    </div>
  );
};
