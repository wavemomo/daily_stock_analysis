import { useCallback, useEffect, useRef, useState } from 'react';
import { LayoutGrid, RefreshCw, Search } from 'lucide-react';
import { AppPage, Card, Drawer, EmptyState, PageHeader } from '../components/common';
import { ReportMarkdownBody } from '../components/report/ReportMarkdownBody';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import {
  reportGalleryApi,
  type ReportEmailStatus,
  type ReportGalleryDetail,
  type ReportGalleryItem,
} from '../api/reportGallery';
import { cn } from '../utils/cn';

const PAGE_SIZE = 20;

function formatTime(iso?: string | null): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const hh = String(date.getHours()).padStart(2, '0');
  const mm = String(date.getMinutes()).padStart(2, '0');
  return `${hh}:${mm}`;
}

export default function ReportGalleryPage() {
  const { t } = useUiLanguage();

  const [items, setItems] = useState<ReportGalleryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [searchInput, setSearchInput] = useState('');
  const [activeSearch, setActiveSearch] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [hasError, setHasError] = useState(false);

  const [detail, setDetail] = useState<ReportGalleryDetail | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState(false);

  const [emailStatus, setEmailStatus] = useState<ReportEmailStatus | null>(null);
  const [emailBusy, setEmailBusy] = useState(false);

  const requestIdRef = useRef(0);

  const loadPage = useCallback(async (nextPage: number, search: string) => {
    const requestId = ++requestIdRef.current;
    setIsLoading(true);
    setHasError(false);
    try {
      const res = await reportGalleryApi.getList({ page: nextPage, limit: PAGE_SIZE, search });
      if (requestId !== requestIdRef.current) return;
      setTotal(res.total);
      setPage(nextPage);
      setItems((prev) => (nextPage === 1 ? res.items : [...prev, ...res.items]));
    } catch {
      if (requestId !== requestIdRef.current) return;
      setHasError(true);
    } finally {
      if (requestId === requestIdRef.current) setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadPage(1, '');
  }, [loadPage]);

  useEffect(() => {
    let active = true;
    void reportGalleryApi
      .getEmailStatus()
      .then((status) => {
        if (active) setEmailStatus(status);
      })
      .catch(() => {
        if (active) setEmailStatus(null);
      });
    return () => {
      active = false;
    };
  }, []);

  const runSearch = () => {
    const next = searchInput.trim();
    setActiveSearch(next);
    void loadPage(1, next);
  };

  const openDetail = async (recordId: number) => {
    setDetailOpen(true);
    setDetail(null);
    setDetailError(false);
    setDetailLoading(true);
    try {
      const res = await reportGalleryApi.getDetail(recordId);
      setDetail(res);
    } catch {
      setDetailError(true);
    } finally {
      setDetailLoading(false);
    }
  };

  const toggleReportEmail = async () => {
    if (!emailStatus || emailBusy) return;
    const nextEnabled = !emailStatus.reportEmailEnabled;
    setEmailBusy(true);
    setEmailStatus({ ...emailStatus, reportEmailEnabled: nextEnabled });
    try {
      const updated = await reportGalleryApi.setReportEmailDelivery(nextEnabled);
      setEmailStatus(updated);
    } catch {
      setEmailStatus((prev) => (prev ? { ...prev, reportEmailEnabled: !nextEnabled } : prev));
    } finally {
      setEmailBusy(false);
    }
  };

  const hasMore = items.length < total;

  return (
    <AppPage>
      <div className="space-y-5">
        <PageHeader
          eyebrow={t('layout.route.reportGallery.eyebrow')}
          title={t('layout.route.reportGallery.title')}
          description={t('layout.route.reportGallery.description')}
          actions={(
            <button
              type="button"
              className="btn-secondary inline-flex items-center gap-2"
              onClick={() => void loadPage(1, activeSearch)}
              disabled={isLoading}
            >
              <RefreshCw className={cn('h-4 w-4', isLoading ? 'animate-spin' : '')} />
              {t('reportGallery.refresh')}
            </button>
          )}
        />

        {emailStatus?.email ? (
          <Card padding="sm" className="rounded-lg">
            <div className="flex items-center justify-between gap-4">
              <div className="min-w-0">
                <h2 className="text-sm font-semibold text-foreground">{t('reportGallery.emailTitle')}</h2>
                <p className="mt-1 text-xs text-secondary-text">
                  {t('reportGallery.emailDesc', { email: emailStatus.email })}
                </p>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={emailStatus.reportEmailEnabled}
                onClick={() => void toggleReportEmail()}
                disabled={emailBusy}
                className={cn(
                  'relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors',
                  emailStatus.reportEmailEnabled ? 'bg-primary' : 'bg-muted',
                  emailBusy ? 'opacity-60' : '',
                )}
              >
                <span
                  className={cn(
                    'inline-block h-5 w-5 transform rounded-full bg-white transition-transform',
                    emailStatus.reportEmailEnabled ? 'translate-x-5' : 'translate-x-0.5',
                  )}
                />
              </button>
            </div>
          </Card>
        ) : null}

        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-text" />
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') runSearch(); }}
              placeholder={t('reportGallery.searchPlaceholder')}
              className="input-surface h-10 w-full rounded-lg border pl-9 pr-3 text-sm"
            />
          </div>
          <button type="button" className="btn-primary" onClick={runSearch} disabled={isLoading}>
            {t('reportGallery.search')}
          </button>
        </div>

        {hasError ? (
          <Card className="border-error/30 bg-error/5">
            <p className="text-sm text-error">{t('reportGallery.loadFailed')}</p>
            <button
              type="button"
              className="mt-3 text-sm font-medium text-primary hover:underline"
              onClick={() => void loadPage(1, activeSearch)}
            >
              {t('common.retry')}
            </button>
          </Card>
        ) : null}

        {!isLoading && !items.length && !hasError ? (
          <EmptyState
            icon={<LayoutGrid className="h-8 w-8" />}
            title={t('reportGallery.empty')}
            description={t('reportGallery.emptyHint')}
          />
        ) : null}

        {items.length ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {items.map((item) => (
              <div
                key={item.id}
                role="button"
                tabIndex={0}
                onClick={() => void openDetail(item.id)}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); void openDetail(item.id); } }}
                className="terminal-card cursor-pointer rounded-2xl p-4 transition-shadow hover:shadow-soft-card-strong"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="truncate text-base font-semibold text-foreground">
                      {item.stockName || item.stockCode || '--'}
                    </h2>
                    <p className="mt-1 text-xs text-secondary-text">{item.stockCode}</p>
                  </div>
                  {typeof item.sentimentScore === 'number' ? (
                    <span className="shrink-0 rounded-md bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                      {item.sentimentScore}
                    </span>
                  ) : null}
                </div>
                {item.analysisSummary ? (
                  <p className="mt-3 line-clamp-2 text-xs leading-relaxed text-secondary-text">
                    {item.analysisSummary}
                  </p>
                ) : null}
                <div className="mt-3 flex items-center justify-between gap-2 border-t border-border/60 pt-2 text-xs text-muted-text">
                  <span className="truncate">
                    {t('reportGallery.by', { name: item.ownerName || t('reportGallery.anonymous') })}
                  </span>
                  <span className="shrink-0">{formatTime(item.createdAt)}</span>
                </div>
              </div>
            ))}
          </div>
        ) : null}

        {hasMore ? (
          <div className="flex justify-center">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => void loadPage(page + 1, activeSearch)}
              disabled={isLoading}
            >
              {isLoading ? t('common.loading') : t('reportGallery.loadMore')}
            </button>
          </div>
        ) : null}
      </div>

      <Drawer
        isOpen={detailOpen}
        onClose={() => setDetailOpen(false)}
        title={detail ? `${detail.stockName || ''} ${detail.stockCode || ''}`.trim() || t('layout.route.reportGallery.title') : t('layout.route.reportGallery.title')}
      >
        {detailLoading ? (
          <div className="space-y-3">
            <div className="h-6 w-1/2 animate-pulse rounded bg-muted" />
            <div className="h-40 animate-pulse rounded bg-muted" />
          </div>
        ) : detailError ? (
          <p className="text-sm text-error">{t('reportGallery.detailFailed')}</p>
        ) : detail?.content ? (
          <div>
            <p className="mb-3 text-xs text-muted-text">
              {t('reportGallery.by', { name: detail.ownerName || t('reportGallery.anonymous') })}
            </p>
            <ReportMarkdownBody content={detail.content} />
          </div>
        ) : (
          <p className="text-sm text-secondary-text">{t('reportGallery.noContent')}</p>
        )}
      </Drawer>
    </AppPage>
  );
}
