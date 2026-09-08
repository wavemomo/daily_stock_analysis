import { RefreshCw, TicketCheck } from 'lucide-react';
import { useFeatureQuotas } from '../contexts/FeatureQuotaContext';
import { AppPage, Card, EmptyState, PageHeader } from '../components/common';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import type { UiLanguage } from '../i18n/uiText';
import { cn } from '../utils/cn';

function featureLabel(featureCode: string, language: UiLanguage): string {
  const labels: Record<string, [string, string]> = {
    stock_analysis: ['个股分析', 'Stock analysis'],
    agent_chat: ['问股', 'Ask'],
    agent_research: ['深度研究', 'Deep research'],
  };
  return labels[featureCode]?.[language === 'zh' ? 0 : 1] ?? featureCode;
}

export default function FeatureQuotasPage() {
  const { t, language } = useUiLanguage();
  const { items, isLoading, hasLoadError, refresh } = useFeatureQuotas();

  return (
    <AppPage>
      <div className="space-y-5">
        <PageHeader
          eyebrow={t('layout.route.featureQuotas.eyebrow')}
          title={t('layout.route.featureQuotas.title')}
          description={t('layout.route.featureQuotas.description')}
          actions={(
            <button
              type="button"
              className="btn-secondary inline-flex items-center gap-2"
              onClick={() => void refresh()}
              disabled={isLoading}
            >
              <RefreshCw className={cn('h-4 w-4', isLoading ? 'animate-spin' : '')} />
              {t('layout.route.featureQuotas.refresh')}
            </button>
          )}
        />

        {hasLoadError ? (
          <Card className="border-error/30 bg-error/5">
            <p className="text-sm text-error">{t('layout.route.featureQuotas.error')}</p>
            <button type="button" className="mt-3 text-sm font-medium text-primary hover:underline" onClick={() => void refresh()}>
              {t('common.retry')}
            </button>
          </Card>
        ) : null}

        {isLoading && !items.length ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3" aria-label={t('layout.route.featureQuotas.loading')}>
            {Array.from({ length: 3 }).map((_, index) => (
              <div key={index} className="h-36 animate-pulse rounded-2xl border border-border/70 bg-card/60" />
            ))}
          </div>
        ) : null}

        {!isLoading && !items.length && !hasLoadError ? (
          <EmptyState
            icon={<TicketCheck className="h-8 w-8" />}
            title={t('layout.route.featureQuotas.emptyTitle')}
            description={t('layout.route.featureQuotas.emptyDescription')}
          />
        ) : null}

        {items.length ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {items.map((item) => {
              const status = item.disabled
                ? t('layout.route.featureQuotas.disabled')
                : item.unlimited
                  ? t('layout.route.featureQuotas.unlimited')
                  : item.remaining === null
                    ? '—'
                    : t('layout.route.featureQuotas.remaining', { count: item.remaining });
              return (
                <Card key={item.feature_code} padding="sm" className="rounded-lg">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h2 className="truncate text-base font-semibold text-foreground">{featureLabel(item.feature_code, language)}</h2>
                      <p className="mt-1 text-xs text-secondary-text">{item.feature_code}</p>
                    </div>
                    <TicketCheck className="h-5 w-5 shrink-0 text-cyan" aria-hidden="true" />
                  </div>
                  <p className={cn('mt-6 text-2xl font-semibold', item.disabled ? 'text-error' : 'text-foreground')}>
                    {status}
                  </p>
                  {item.daily_limit !== null && !item.disabled && !item.unlimited ? (
                    <p className="mt-2 text-xs text-secondary-text">
                      {language === 'zh' ? `每日上限 ${item.daily_limit} 次` : `${item.daily_limit} uses per day`}
                    </p>
                  ) : null}
                </Card>
              );
            })}
          </div>
        ) : null}
      </div>
    </AppPage>
  );
}
