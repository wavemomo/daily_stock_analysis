import type React from 'react';
import { lazy, useEffect } from 'react';
import { BrowserRouter as Router, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { ApiErrorAlert, Shell } from './components/common';
import {
  PageLoadingFallback,
  RouteOutletBoundary,
  StandaloneRouteBoundary,
} from './components/layout/RouteBoundary';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { FeatureQuotaProvider } from './contexts/FeatureQuotaContext';
import { UiLanguageProvider, useUiLanguage } from './contexts/UiLanguageContext';
import { useAgentChatStore } from './stores/agentChatStore';
import './App.css';

const HomePage = lazy(() => import('./pages/HomePage'));
const BacktestPage = lazy(() => import('./pages/BacktestPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const LoginPage = lazy(() => import('./pages/LoginPage'));
const NotFoundPage = lazy(() => import('./pages/NotFoundPage'));
const ChatPage = lazy(() => import('./pages/ChatPage'));
const PortfolioPage = lazy(() => import('./pages/PortfolioPage'));
const DecisionSignalsPage = lazy(() => import('./pages/DecisionSignalsPage'));
const AlertsPage = lazy(() => import('./pages/AlertsPage'));
const TokenUsagePage = lazy(() => import('./pages/TokenUsagePage'));
const FeatureQuotasPage = lazy(() => import('./pages/FeatureQuotasPage'));
const StockScreeningPage = lazy(() => import('./pages/StockScreeningPage'));
const AccessControlPage = lazy(() => import('./pages/AccessControlPage'));
const ReportGalleryPage = lazy(() => import('./pages/ReportGalleryPage'));
const PersonalSettingsPage = lazy(() => import('./pages/PersonalSettingsPage'));
const TribulationPage = lazy(() => import('./pages/TribulationPage'));

const AppContent: React.FC = () => {
  const location = useLocation();
  const { actor, hasPermission, isLoading, loadError, refreshStatus } = useAuth();
  const { t } = useUiLanguage();

  useEffect(() => {
    useAgentChatStore.getState().setCurrentRoute(location.pathname);
  }, [location.pathname]);

  if (isLoading) {
    return <PageLoadingFallback />;
  }

  if (loadError) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-base px-4">
        <div className="w-full max-w-lg">
          <ApiErrorAlert error={loadError} />
        </div>
        <button
          type="button"
          className="btn-primary"
          onClick={() => void refreshStatus()}
        >
          {t('common.retry')}
        </button>
      </div>
    );
  }

  if (actor === 'anonymous') {
    if (location.pathname === '/login') {
      return <StandaloneRouteBoundary><LoginPage /></StandaloneRouteBoundary>;
    }
    const redirect = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?redirect=${redirect}`} replace />;
  }

  if (location.pathname === '/login') {
    return <Navigate to="/" replace />;
  }

  const canManageSystem = hasPermission('system.manage');
  const canManageRbac = hasPermission('rbac.manage');

  return (
    <Routes>
      <Route
        element={(
          <Shell>
            <RouteOutletBoundary />
          </Shell>
        )}
      >
        <Route path="/" element={<HomePage />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/portfolio" element={<PortfolioPage />} />
        <Route path="/decision-signals" element={<DecisionSignalsPage />} />
        <Route path="/screening" element={<StockScreeningPage />} />
        <Route path="/backtest" element={<BacktestPage />} />
        <Route path="/alerts" element={<AlertsPage />} />
        <Route path="/usage" element={<TokenUsagePage />} />
        <Route path="/feature-quotas" element={<FeatureQuotasPage />} />
        <Route path="/report-gallery" element={<ReportGalleryPage />} />
        <Route path="/tribulation" element={<TribulationPage />} />
        <Route path="/personal-settings" element={<PersonalSettingsPage />} />
        <Route path="/settings" element={canManageSystem ? <SettingsPage /> : <Navigate to="/" replace />} />
        <Route path="/access-control" element={canManageRbac ? <AccessControlPage /> : <Navigate to="/" replace />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
};

const App: React.FC = () => {
  return (
    <UiLanguageProvider>
      <Router>
        <AuthProvider>
          <FeatureQuotaProvider>
            <AppContent />
          </FeatureQuotaProvider>
        </AuthProvider>
      </Router>
    </UiLanguageProvider>
  );
};

export default App;
