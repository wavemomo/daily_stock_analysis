import type React from 'react';
import { useEffect, useState } from 'react';
import { LogIn, QrCode, ShieldCheck } from 'lucide-react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Button, Input } from '../components/common';
import { useAuth } from '../hooks';
import { WECHAT_OAUTH_START_PATH, webAuthApi } from '../api/webAuth';
import { getParsedApiError } from '../api/error';

const OAUTH_RETURN_PATH_STORAGE_KEY = 'dsa.webAuth.returnPath';

function safeRedirect(value: string | null): string {
  if (!value || !value.startsWith('/') || value.startsWith('//') || value.includes('\\')) return '/';
  try {
    const target = new URL(value, window.location.origin);
    return target.origin === window.location.origin
      ? `${target.pathname}${target.search}${target.hash}`
      : '/';
  } catch {
    return '/';
  }
}

const LoginPage: React.FC = () => {
  const { actor, refreshStatus } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const redirect = safeRedirect(searchParams.get('redirect'));

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (actor !== 'web_user') return;
    let target = redirect;
    try {
      const storedTarget = window.sessionStorage.getItem(OAUTH_RETURN_PATH_STORAGE_KEY);
      if (storedTarget !== null) {
        target = safeRedirect(storedTarget);
      }
      window.sessionStorage.removeItem(OAUTH_RETURN_PATH_STORAGE_KEY);
    } catch {
      // Session storage is best-effort; the validated query fallback remains safe.
    }
    navigate(target, { replace: true });
  }, [actor, navigate, redirect]);

  const handlePasswordLogin = async (event: React.FormEvent) => {
    event.preventDefault();
    if (submitting) return;
    const normalizedEmail = email.trim();
    if (!normalizedEmail || !password) {
      setError('请输入邮箱和密码');
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      window.sessionStorage.setItem(OAUTH_RETURN_PATH_STORAGE_KEY, redirect);
    } catch {
      // Deep-link restore is best-effort.
    }
    try {
      await webAuthApi.passwordLogin(normalizedEmail, password);
      // 登录成功后 Cookie 已下发，刷新会话状态触发跳转。
      await refreshStatus();
    } catch (err) {
      const parsed = getParsedApiError(err);
      setError(parsed.status === 401 ? '邮箱或密码错误' : parsed.message || '登录失败，请稍后重试');
      setSubmitting(false);
    }
  };

  const beginWechatLogin = () => {
    try {
      window.sessionStorage.setItem(OAUTH_RETURN_PATH_STORAGE_KEY, redirect);
    } catch {
      // OAuth succeeds without restoring deep links when storage is unavailable.
    }
    window.location.assign(WECHAT_OAUTH_START_PATH);
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-base px-4 py-10 text-foreground">
      <section className="w-full max-w-xl rounded-3xl border border-border bg-card p-7 shadow-soft-card sm:p-10">
        <div className="mb-7 flex items-start gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-primary-gradient text-primary-foreground">
            <ShieldCheck className="h-6 w-6" aria-hidden="true" />
          </div>
          <div>
            <p className="text-sm font-medium text-primary">邮箱密码登录</p>
            <h1 className="mt-1 text-3xl font-bold tracking-tight">登录主升浪 Web 端</h1>
            <p className="mt-2 text-sm leading-6 text-secondary-text">
              使用在小程序「个人设置 → Web 登录邮箱」绑定的邮箱和密码登录，与小程序为同一账户。
            </p>
          </div>
        </div>

        <form className="space-y-4" onSubmit={handlePasswordLogin}>
          <Input
            label="邮箱"
            type="email"
            name="email"
            autoComplete="username"
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={submitting}
            iconType="key"
          />
          <Input
            label="密码"
            type="password"
            name="password"
            autoComplete="current-password"
            placeholder="请输入密码"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={submitting}
            iconType="password"
            allowTogglePassword
            error={error || undefined}
          />
          <Button type="submit" variant="primary" size="lg" className="w-full" disabled={submitting}>
            <LogIn className="mr-2 h-4 w-4" aria-hidden="true" />
            {submitting ? '登录中…' : '登录'}
          </Button>
        </form>

        <div className="mt-6 border-t border-border pt-5">
          <p className="mb-3 text-center text-xs leading-5 text-muted-foreground">
            已配置微信开放平台网站应用？也可使用微信扫码登录。
          </p>
          <Button type="button" variant="secondary" size="lg" className="w-full" onClick={beginWechatLogin}>
            <QrCode className="mr-2 h-4 w-4" aria-hidden="true" />
            前往微信扫码登录
          </Button>
        </div>

        <p className="mt-5 text-center text-xs leading-5 text-muted-foreground">
          还没有绑定邮箱？请先在小程序端登录并完成「个人设置 → Web 登录邮箱」绑定。
        </p>
      </section>
    </main>
  );
};

export default LoginPage;
