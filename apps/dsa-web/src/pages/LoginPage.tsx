import type React from 'react';
import { useEffect } from 'react';
import { QrCode, ShieldCheck } from 'lucide-react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Button } from '../components/common';
import { useAuth } from '../hooks';
import { WECHAT_OAUTH_START_PATH } from '../api/webAuth';

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
  const { actor } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const redirect = safeRedirect(searchParams.get('redirect'));

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
            <p className="text-sm font-medium text-primary">微信开放平台登录</p>
            <h1 className="mt-1 text-3xl font-bold tracking-tight">使用微信扫码登录</h1>
            <p className="mt-2 text-sm leading-6 text-secondary-text">
              扫码确认后将以同一统一账户进入 Web；系统不提供独立管理员密码登录。
            </p>
          </div>
        </div>
        <Button type="button" variant="primary" size="lg" className="w-full" onClick={beginWechatLogin}>
          <QrCode className="mr-2 h-4 w-4" aria-hidden="true" />
          前往微信扫码登录
        </Button>
        <p className="mt-5 text-center text-xs leading-5 text-muted-foreground">
          登录后，系统会根据你的权限展示可访问的功能与管理入口。
        </p>
      </section>
    </main>
  );
};

export default LoginPage;
