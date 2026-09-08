import type React from 'react';
import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { webAuthApi, type WebUser } from '../api/webAuth';
import { setCsrfToken } from '../api';
import { useStockPoolStore } from '../stores';

export type WebActor = 'anonymous' | 'web_user';

type AuthContextValue = {
  actor: WebActor;
  user: WebUser | null;
  loggedIn: boolean;
  isLoading: boolean;
  loadError: ParsedApiError | null;
  hasPermission: (permission: string) => boolean;
  hasAnyPermission: (permissions: string[]) => boolean;
  logout: () => Promise<void>;
  refreshStatus: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [actor, setActor] = useState<WebActor>('anonymous');
  const [user, setUser] = useState<WebUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<ParsedApiError | null>(null);

  const resetUserState = useCallback(() => {
    setActor('anonymous');
    setUser(null);
    setCsrfToken();
    useStockPoolStore.getState().resetDashboardState();
  }, []);

  const fetchStatus = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const session = await webAuthApi.me();
      setActor('web_user');
      setUser(session.user);
      setCsrfToken(session.csrf_token);
    } catch (err) {
      const parsed = getParsedApiError(err);
      resetUserState();
      if (parsed.status !== 401) {
        setLoadError(parsed);
      }
    } finally {
      setIsLoading(false);
    }
  }, [resetUserState]);

  useEffect(() => {
    void fetchStatus();
  }, [fetchStatus]);

  const hasPermission = useCallback(
    (permission: string) => user?.permissions?.includes(permission) ?? false,
    [user],
  );

  const hasAnyPermission = useCallback(
    (permissions: string[]) => permissions.some((permission) => user?.permissions?.includes(permission)),
    [user],
  );

  const logout = useCallback(async () => {
    let logoutError: unknown = null;
    try {
      await webAuthApi.logout();
    } catch (err) {
      logoutError = err;
    } finally {
      resetUserState();
    }
    if (logoutError && getParsedApiError(logoutError).status !== 401) throw logoutError;
  }, [resetUserState]);

  return (
    <AuthContext.Provider value={{
      actor,
      user,
      loggedIn: actor === 'web_user',
      isLoading,
      loadError,
      hasPermission,
      hasAnyPermission,
      logout,
      refreshStatus: fetchStatus,
    }}>
      {children}
    </AuthContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components -- useAuth is a hook, co-located for context access
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider');
  return ctx;
}
