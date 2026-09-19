import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { CalendarClock, Languages, LogOut, Mail, User } from 'lucide-react';
import { AppPage, Button, Card, ConfirmDialog, InlineAlert, Input, PageHeader } from '../components/common';
import {
  accountApi,
  AVATAR_ACCEPT_TYPES,
  AVATAR_MAX_BYTES,
  type AccountProfile,
  type EmailBinding,
} from '../api/account';
import { watchlistApi, type WatchlistItem } from '../api/watchlist';
import { getParsedApiError } from '../api/error';
import { API_BASE_URL } from '../utils/constants';

function avatarSrc(url?: string | null): string {
  if (!url) {
    return '';
  }
  return /^https?:\/\//i.test(url) ? url : `${API_BASE_URL}${url}`;
}
import { useAuth } from '../contexts/AuthContext';
import { useUiLanguage } from '../contexts/UiLanguageContext';

const RESEND_SECONDS = 60;

type Feedback = { variant: 'success' | 'danger'; text: string } | null;

export default function PersonalSettingsPage() {
  const { t, language, setLanguage } = useUiLanguage();
  const { logout, refreshStatus } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    document.title = t('personalSettings.pageTitle');
  }, [t]);

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [profile, setProfile] = useState<AccountProfile | null>(null);
  const [binding, setBinding] = useState<EmailBinding | null>(null);

  // 个人信息
  const [nickname, setNickname] = useState('');
  const [profileSaving, setProfileSaving] = useState(false);
  const [profileMsg, setProfileMsg] = useState<Feedback>(null);
  const [avatarUploading, setAvatarUploading] = useState(false);
  const avatarInputRef = useRef<HTMLInputElement>(null);

  // Web 登录邮箱
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [sending, setSending] = useState(false);
  const [binding_, setBindingSubmitting] = useState(false);
  const [reportSaving, setReportSaving] = useState(false);
  const [emailMsg, setEmailMsg] = useState<Feedback>(null);
  const [resend, setResend] = useState(0);
  const resendTimer = useRef<number | undefined>(undefined);

  // 定时分析（分析池 = 个人自选）
  const [scheduleEnabled, setScheduleEnabled] = useState(false);
  const [scheduleItems, setScheduleItems] = useState<WatchlistItem[]>([]);
  const [scheduleSaving, setScheduleSaving] = useState(false);
  const [scheduleMsg, setScheduleMsg] = useState<Feedback>(null);

  // 退出登录
  const [showLogout, setShowLogout] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [profileData, bindingData] = await Promise.all([
        accountApi.getProfile(),
        accountApi.getEmailBinding(),
      ]);
      setProfile(profileData);
      setNickname(profileData.nickname ?? '');
      setBinding(bindingData);
      setEmail(bindingData.email ?? '');
    } catch (err) {
      setLoadError(getParsedApiError(err).message || t('personalSettings.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  const loadSchedule = useCallback(async () => {
    // 独立加载，缺少 watchlist 权限时不影响页面其余部分。
    try {
      const [pref, items] = await Promise.all([
        watchlistApi.getSchedulePref(),
        watchlistApi.listItems(),
      ]);
      setScheduleEnabled(!!pref.scheduled_analysis_enabled);
      setScheduleItems(items);
    } catch {
      // 忽略：无权限或暂不可用时不展示定时分析管理。
    }
  }, []);

  useEffect(() => {
    void loadAll();
    void loadSchedule();
  }, [loadAll, loadSchedule]);

  const handleToggleSchedulePref = async () => {
    if (scheduleSaving) {
      return;
    }
    setScheduleSaving(true);
    setScheduleMsg(null);
    try {
      const next = await watchlistApi.setSchedulePref(!scheduleEnabled);
      setScheduleEnabled(!!next.scheduled_analysis_enabled);
    } catch (err) {
      setScheduleMsg({ variant: 'danger', text: getParsedApiError(err).message || t('personalSettings.scheduleFailed') });
    } finally {
      setScheduleSaving(false);
    }
  };

  const handleToggleStockScheduled = async (item: WatchlistItem) => {
    if (scheduleSaving) {
      return;
    }
    const nextScheduled = !item.scheduled;
    setScheduleSaving(true);
    setScheduleMsg(null);
    try {
      await watchlistApi.setScheduled(item.stock_code, nextScheduled);
      setScheduleItems((rows) =>
        rows.map((row) => (row.stock_code === item.stock_code ? { ...row, scheduled: nextScheduled } : row)),
      );
    } catch (err) {
      setScheduleMsg({ variant: 'danger', text: getParsedApiError(err).message || t('personalSettings.scheduleFailed') });
    } finally {
      setScheduleSaving(false);
    }
  };

  useEffect(() => {
    if (resend <= 0) {
      return undefined;
    }
    resendTimer.current = window.setTimeout(() => setResend((value) => value - 1), 1000);
    return () => window.clearTimeout(resendTimer.current);
  }, [resend]);

  const handleSaveNickname = async () => {
    const trimmed = nickname.trim();
    if (profileSaving) {
      return;
    }
    setProfileSaving(true);
    setProfileMsg(null);
    try {
      const updated = await accountApi.updateNickname(trimmed);
      setProfile(updated);
      setNickname(updated.nickname ?? '');
      setProfileMsg({ variant: 'success', text: t('personalSettings.saved') });
      await refreshStatus();
    } catch (err) {
      setProfileMsg({ variant: 'danger', text: getParsedApiError(err).message || t('personalSettings.saveFailed') });
    } finally {
      setProfileSaving(false);
    }
  };

  const handleAvatarChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file || avatarUploading) {
      return;
    }
    if (!AVATAR_ACCEPT_TYPES.includes(file.type)) {
      setProfileMsg({ variant: 'danger', text: t('personalSettings.avatarTypeError') });
      return;
    }
    if (file.size > AVATAR_MAX_BYTES) {
      setProfileMsg({ variant: 'danger', text: t('personalSettings.avatarTooLarge') });
      return;
    }
    setAvatarUploading(true);
    setProfileMsg(null);
    try {
      const updated = await accountApi.uploadAvatar(file);
      setProfile(updated);
      setProfileMsg({ variant: 'success', text: t('personalSettings.avatarUpdated') });
      await refreshStatus();
    } catch (err) {
      setProfileMsg({ variant: 'danger', text: getParsedApiError(err).message || t('personalSettings.avatarFailed') });
    } finally {
      setAvatarUploading(false);
    }
  };

  const handleToggleReportEmail = async () => {
    if (!binding || reportSaving) {
      return;
    }
    setReportSaving(true);
    setEmailMsg(null);
    try {
      const next = await accountApi.setReportEmailEnabled(!binding.report_email_enabled);
      setBinding(next);
      setEmailMsg({ variant: 'success', text: t('personalSettings.reportEmailUpdated') });
    } catch (err) {
      setEmailMsg({ variant: 'danger', text: getParsedApiError(err).message || t('personalSettings.reportEmailFailed') });
    } finally {
      setReportSaving(false);
    }
  };

  const handleSendCode = async () => {
    if (sending || resend > 0) {
      return;
    }
    if (!email.trim()) {
      setEmailMsg({ variant: 'danger', text: t('personalSettings.emailRequired') });
      return;
    }
    setSending(true);
    setEmailMsg(null);
    try {
      await accountApi.requestEmailCode(email.trim());
      setResend(RESEND_SECONDS);
      setEmailMsg({ variant: 'success', text: t('personalSettings.codeSent') });
    } catch (err) {
      setEmailMsg({ variant: 'danger', text: getParsedApiError(err).message || t('personalSettings.sendFailed') });
    } finally {
      setSending(false);
    }
  };

  const handleBindEmail = async () => {
    if (binding_) {
      return;
    }
    if (!email.trim()) {
      setEmailMsg({ variant: 'danger', text: t('personalSettings.emailRequired') });
      return;
    }
    if (!code.trim()) {
      setEmailMsg({ variant: 'danger', text: t('personalSettings.codeRequired') });
      return;
    }
    if (password.length < 8) {
      setEmailMsg({ variant: 'danger', text: t('personalSettings.passwordShort') });
      return;
    }
    if (password !== confirmPassword) {
      setEmailMsg({ variant: 'danger', text: t('personalSettings.passwordMismatch') });
      return;
    }
    setBindingSubmitting(true);
    setEmailMsg(null);
    try {
      const next = await accountApi.bindEmail(email.trim(), code.trim(), password);
      setBinding(next);
      setCode('');
      setPassword('');
      setConfirmPassword('');
      setEmailMsg({ variant: 'success', text: t('personalSettings.bound') });
    } catch (err) {
      setEmailMsg({ variant: 'danger', text: getParsedApiError(err).message || t('personalSettings.bindFailed') });
    } finally {
      setBindingSubmitting(false);
    }
  };

  const handleLogout = async () => {
    setLoggingOut(true);
    try {
      await logout();
      navigate('/login', { replace: true });
    } catch {
      setLoggingOut(false);
      setShowLogout(false);
    }
  };

  const emailStatusText = (() => {
    if (!binding || !binding.email) {
      return t('personalSettings.emailStatusUnbound');
    }
    return binding.email_verified
      ? t('personalSettings.emailStatusBound', { email: binding.email })
      : t('personalSettings.emailStatusUnverified', { email: binding.email });
  })();

  return (
    <AppPage>
      <div className="mx-auto w-full max-w-2xl space-y-5">
        <PageHeader
          eyebrow={t('layout.route.personalSettings.eyebrow')}
          title={t('layout.route.personalSettings.title')}
          description={t('layout.route.personalSettings.description')}
        />

        {loadError ? (
          <InlineAlert
            variant="danger"
            message={loadError}
            action={(
              <Button variant="secondary" size="sm" onClick={() => void loadAll()}>
                {t('common.retry')}
              </Button>
            )}
          />
        ) : null}

        {loading ? (
          <div className="space-y-4" aria-label={t('common.loading')}>
            {Array.from({ length: 3 }).map((_, index) => (
              <div key={index} className="h-40 animate-pulse rounded-2xl border border-border/70 bg-card/60" />
            ))}
          </div>
        ) : (
          <>
            {/* 个人信息 */}
            <Card className="space-y-4">
              <div className="flex items-center gap-2">
                <User className="h-5 w-5 text-cyan" aria-hidden="true" />
                <h2 className="text-base font-semibold text-foreground">{t('personalSettings.profileSection')}</h2>
              </div>
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => avatarInputRef.current?.click()}
                  disabled={avatarUploading}
                  className="group relative h-20 w-20 shrink-0 overflow-hidden rounded-full border border-border/70 disabled:cursor-not-allowed"
                  aria-label={t('personalSettings.avatarChange')}
                >
                  {profile?.avatar_url ? (
                    <img src={avatarSrc(profile.avatar_url)} alt="" className="h-full w-full object-cover" />
                  ) : (
                    <span className="flex h-full w-full items-center justify-center bg-primary-gradient text-2xl font-semibold text-primary-foreground">
                      {(profile?.nickname ?? '#').slice(0, 1)}
                    </span>
                  )}
                  <span className="absolute inset-x-0 bottom-0 bg-black/45 py-0.5 text-center text-[10px] text-white opacity-0 transition-opacity group-hover:opacity-100">
                    {avatarUploading ? t('common.processing') : t('personalSettings.avatarChange')}
                  </span>
                </button>
                <input
                  ref={avatarInputRef}
                  type="file"
                  accept={AVATAR_ACCEPT_TYPES.join(',')}
                  className="hidden"
                  onChange={(event) => void handleAvatarChange(event)}
                />
                <div className="min-w-0 text-sm text-secondary-text">
                  <p>{t('personalSettings.userId', { id: profile?.id ?? '—' })}</p>
                  {profile?.roles?.length ? <p className="mt-1">{t('personalSettings.roles', { roles: profile.roles.join(', ') })}</p> : null}
                  <p className="mt-1 text-xs text-muted-foreground">{t('personalSettings.avatarHint')}</p>
                </div>
              </div>
              <Input
                label={t('personalSettings.nickname')}
                value={nickname}
                maxLength={64}
                placeholder={t('personalSettings.nicknamePlaceholder')}
                onChange={(event) => setNickname(event.target.value)}
                disabled={profileSaving}
              />
              {profileMsg ? <InlineAlert variant={profileMsg.variant} message={profileMsg.text} /> : null}
              <div className="flex justify-end">
                <Button onClick={() => void handleSaveNickname()} isLoading={profileSaving}>
                  {t('personalSettings.save')}
                </Button>
              </div>
            </Card>

            {/* Web 登录邮箱 */}
            <Card className="space-y-4">
              <div className="flex items-center gap-2">
                <Mail className="h-5 w-5 text-cyan" aria-hidden="true" />
                <h2 className="text-base font-semibold text-foreground">{t('personalSettings.emailSection')}</h2>
              </div>
              <p className="text-sm text-secondary-text">{t('personalSettings.emailDesc')}</p>
              <div className="rounded-xl border border-border/60 bg-muted/20 px-4 py-3 text-sm text-foreground">
                {emailStatusText}
              </div>

              <label className="flex items-center justify-between gap-3 rounded-xl border border-border/60 px-4 py-3">
                <span className="min-w-0">
                  <span className="block text-sm font-medium text-foreground">{t('personalSettings.reportEmailLabel')}</span>
                  <span className="mt-1 block text-xs text-secondary-text">{t('personalSettings.reportEmailDesc')}</span>
                </span>
                <input
                  type="checkbox"
                  className="h-5 w-5 accent-[hsl(var(--primary))]"
                  checked={binding?.report_email_enabled ?? false}
                  disabled={reportSaving || !binding?.email}
                  onChange={() => void handleToggleReportEmail()}
                />
              </label>

              <div className="space-y-3">
                <p className="text-xs text-secondary-text">{t('personalSettings.rebindHint')}</p>
                <Input
                  label={t('personalSettings.email')}
                  type="email"
                  autoComplete="email"
                  value={email}
                  placeholder={t('personalSettings.emailPlaceholder')}
                  onChange={(event) => setEmail(event.target.value)}
                  trailingAction={(
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => void handleSendCode()}
                      isLoading={sending}
                      disabled={resend > 0}
                    >
                      {resend > 0 ? t('personalSettings.resendIn', { n: resend }) : t('personalSettings.sendCode')}
                    </Button>
                  )}
                />
                <Input
                  label={t('personalSettings.code')}
                  value={code}
                  inputMode="numeric"
                  maxLength={16}
                  placeholder={t('personalSettings.codePlaceholder')}
                  onChange={(event) => setCode(event.target.value)}
                />
                <Input
                  label={t('personalSettings.password')}
                  type="password"
                  autoComplete="new-password"
                  allowTogglePassword
                  value={password}
                  placeholder={t('personalSettings.passwordPlaceholder')}
                  onChange={(event) => setPassword(event.target.value)}
                />
                <Input
                  label={t('personalSettings.confirmPassword')}
                  type="password"
                  autoComplete="new-password"
                  allowTogglePassword
                  value={confirmPassword}
                  placeholder={t('personalSettings.confirmPlaceholder')}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                />
                {emailMsg ? <InlineAlert variant={emailMsg.variant} message={emailMsg.text} /> : null}
                <div className="flex justify-end">
                  <Button onClick={() => void handleBindEmail()} isLoading={binding_}>
                    {t('personalSettings.bindSubmit')}
                  </Button>
                </div>
              </div>
            </Card>

            {/* 语言 */}
            <Card className="space-y-3">
              <div className="flex items-center gap-2">
                <Languages className="h-5 w-5 text-cyan" aria-hidden="true" />
                <h2 className="text-base font-semibold text-foreground">{t('personalSettings.languageSection')}</h2>
              </div>
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm text-secondary-text">{t('personalSettings.languageDesc')}</p>
                <Button variant="secondary" size="sm" onClick={() => setLanguage(language === 'zh' ? 'en' : 'zh')}>
                  {language === 'zh' ? 'English' : '中文'}
                </Button>
              </div>
            </Card>

            {/* 定时分析（分析池 = 个人自选） */}
            <Card className="space-y-4">
              <div className="flex items-center gap-2">
                <CalendarClock className="h-5 w-5 text-cyan" aria-hidden="true" />
                <h2 className="text-base font-semibold text-foreground">{t('personalSettings.scheduleSection')}</h2>
              </div>
              <p className="text-sm text-secondary-text">{t('personalSettings.scheduleDesc')}</p>
              <label className="flex items-center justify-between gap-3 rounded-xl border border-border/60 px-4 py-3">
                <span className="min-w-0">
                  <span className="block text-sm font-medium text-foreground">{t('personalSettings.scheduleEnableLabel')}</span>
                  <span className="mt-1 block text-xs text-secondary-text">{t('personalSettings.scheduleEnableDesc')}</span>
                </span>
                <input
                  type="checkbox"
                  className="h-5 w-5 accent-[hsl(var(--primary))]"
                  checked={scheduleEnabled}
                  disabled={scheduleSaving}
                  onChange={() => void handleToggleSchedulePref()}
                />
              </label>
              {scheduleEnabled ? (
                scheduleItems.length ? (
                  <div className="space-y-2">
                    <p className="text-xs text-secondary-text">{t('personalSettings.scheduleStocksHint')}</p>
                    <ul className="divide-y divide-border/50 rounded-xl border border-border/60">
                      {scheduleItems.map((item) => (
                        <li key={item.stock_code} className="flex items-center justify-between gap-3 px-4 py-2.5">
                          <span className="min-w-0 truncate text-sm text-foreground">
                            {item.stock_name ? `${item.stock_name} · ${item.stock_code}` : item.stock_code}
                          </span>
                          <input
                            type="checkbox"
                            className="h-4 w-4 accent-[hsl(var(--primary))]"
                            checked={item.scheduled}
                            disabled={scheduleSaving}
                            onChange={() => void handleToggleStockScheduled(item)}
                            aria-label={item.stock_code}
                          />
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">{t('personalSettings.scheduleNoStocks')}</p>
                )
              ) : null}
              {scheduleMsg ? <InlineAlert variant={scheduleMsg.variant} message={scheduleMsg.text} /> : null}
            </Card>

            {/* 退出登录 */}
            <Card className="space-y-3">
              <div className="flex items-center gap-2">
                <LogOut className="h-5 w-5 text-danger" aria-hidden="true" />
                <h2 className="text-base font-semibold text-foreground">{t('layout.logout')}</h2>
              </div>
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm text-secondary-text">{t('personalSettings.logoutDesc')}</p>
                <Button variant="danger-subtle" size="sm" onClick={() => setShowLogout(true)}>
                  {t('layout.logout')}
                </Button>
              </div>
            </Card>
          </>
        )}
      </div>

      <ConfirmDialog
        isOpen={showLogout}
        title={t('layout.logoutTitle')}
        message={t('layout.webUserLogoutMessage')}
        confirmText={loggingOut ? t('layout.logoutPending') : t('layout.logoutConfirm')}
        cancelText={t('common.cancel')}
        confirmDisabled={loggingOut}
        cancelDisabled={loggingOut}
        isDanger
        onConfirm={() => void handleLogout()}
        onCancel={() => {
          if (!loggingOut) {
            setShowLogout(false);
          }
        }}
      />
    </AppPage>
  );
}
