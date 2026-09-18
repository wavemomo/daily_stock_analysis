import { useCallback, useEffect, useState } from 'react';
import { CalendarDays, Flame, NotebookPen } from 'lucide-react';
import { AppPage, Button, Card, ConfirmDialog, EmptyState, InlineAlert, PageHeader } from '../components/common';
import {
  dailyReflectionsApi,
  type DailyReflectionItem,
  type DailyReflectionStats,
} from '../api/dailyReflections';
import { getParsedApiError } from '../api/error';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import { cn } from '../utils/cn';

const PAGE_SIZE = 10;

function localToday(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${now.getFullYear()}-${month}-${day}`;
}

type Feedback = { variant: 'success' | 'danger'; text: string } | null;

export default function TribulationPage() {
  const { t, language } = useUiLanguage();
  const today = localToday();

  useEffect(() => {
    document.title = t('tribulation.pageTitle');
  }, [t]);

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [stats, setStats] = useState<DailyReflectionStats | null>(null);

  const [content, setContent] = useState('');
  const [title, setTitle] = useState('');
  const [saving, setSaving] = useState(false);
  const [editorMsg, setEditorMsg] = useState<Feedback>(null);

  const [items, setItems] = useState<DailyReflectionItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<DailyReflectionItem | null>(null);
  const [deleting, setDeleting] = useState(false);

  const loadHistory = useCallback(async (nextPage: number) => {
    setHistoryLoading(true);
    try {
      const data = await dailyReflectionsApi.list(nextPage, PAGE_SIZE);
      setItems((prev) => (nextPage === 1 ? data.items : [...prev, ...data.items]));
      setTotal(data.total);
      setPage(nextPage);
    } catch (err) {
      setLoadError(getParsedApiError(err).message || t('tribulation.loadFailed'));
    } finally {
      setHistoryLoading(false);
    }
  }, [t]);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [statsData, todayItem] = await Promise.all([
        dailyReflectionsApi.stats(today),
        dailyReflectionsApi.getByDate(today),
      ]);
      setStats(statsData);
      setContent(todayItem?.content ?? '');
      setTitle(todayItem?.title ?? '');
      await loadHistory(1);
    } catch (err) {
      setLoadError(getParsedApiError(err).message || t('tribulation.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [today, loadHistory, t]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  const handleSave = async () => {
    if (saving) {
      return;
    }
    if (!content.trim()) {
      setEditorMsg({ variant: 'danger', text: t('tribulation.contentRequired') });
      return;
    }
    setSaving(true);
    setEditorMsg(null);
    try {
      await dailyReflectionsApi.upsert(today, content.trim(), title.trim());
      setEditorMsg({ variant: 'success', text: t('tribulation.saved') });
      const [statsData] = await Promise.all([dailyReflectionsApi.stats(today), loadHistory(1)]);
      setStats(statsData);
    } catch (err) {
      setEditorMsg({ variant: 'danger', text: getParsedApiError(err).message || t('tribulation.saveFailed') });
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget || deleting) {
      return;
    }
    setDeleting(true);
    try {
      await dailyReflectionsApi.remove(deleteTarget.id);
      setDeleteTarget(null);
      const [statsData] = await Promise.all([dailyReflectionsApi.stats(today), loadHistory(1)]);
      setStats(statsData);
      if (deleteTarget.reflection_date === today) {
        setContent('');
        setTitle('');
      }
    } catch (err) {
      setLoadError(getParsedApiError(err).message || t('tribulation.loadFailed'));
    } finally {
      setDeleting(false);
    }
  };

  const monthDays = new Set(stats?.month_days ?? []);
  const calendarCells = (() => {
    if (!stats?.month) {
      return [];
    }
    const [year, month] = stats.month.split('-').map((value) => Number(value));
    if (!year || !month) {
      return [];
    }
    const firstWeekday = new Date(year, month - 1, 1).getDay();
    const daysInMonth = new Date(year, month, 0).getDate();
    const cells: (number | null)[] = [];
    for (let index = 0; index < firstWeekday; index += 1) {
      cells.push(null);
    }
    for (let day = 1; day <= daysInMonth; day += 1) {
      cells.push(day);
    }
    return cells;
  })();
  const weekdayLabels = language === 'zh'
    ? ['日', '一', '二', '三', '四', '五', '六']
    : ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];

  return (
    <AppPage>
      <div className="mx-auto w-full max-w-3xl space-y-5">
        <PageHeader
          eyebrow={t('layout.route.tribulation.eyebrow')}
          title={t('layout.route.tribulation.title')}
          description={t('layout.route.tribulation.description')}
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
            {/* 连续打卡统计 */}
            <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
              <Card padding="sm" className="rounded-xl">
                <div className="flex items-center gap-2 text-secondary-text">
                  <Flame className="h-4 w-4 text-orange-500" aria-hidden="true" />
                  <span className="text-xs">{t('tribulation.currentStreak')}</span>
                </div>
                <p className="mt-2 text-2xl font-semibold text-foreground">{stats?.current_streak ?? 0}</p>
              </Card>
              <Card padding="sm" className="rounded-xl">
                <span className="text-xs text-secondary-text">{t('tribulation.longestStreak')}</span>
                <p className="mt-2 text-2xl font-semibold text-foreground">{stats?.longest_streak ?? 0}</p>
              </Card>
              <Card padding="sm" className="rounded-xl">
                <span className="text-xs text-secondary-text">{t('tribulation.total')}</span>
                <p className="mt-2 text-2xl font-semibold text-foreground">{stats?.total ?? 0}</p>
              </Card>
              <Card padding="sm" className="rounded-xl">
                <span className="text-xs text-secondary-text">{t('tribulation.todayStatus')}</span>
                <p className={cn('mt-2 text-2xl font-semibold', stats?.today_done ? 'text-success' : 'text-secondary-text')}>
                  {stats?.today_done ? t('tribulation.todayDone') : t('tribulation.todayNotDone')}
                </p>
              </Card>
            </div>

            {/* 今日心得编辑 */}
            <Card className="space-y-4">
              <div className="flex items-center gap-2">
                <NotebookPen className="h-5 w-5 text-cyan" aria-hidden="true" />
                <h2 className="text-base font-semibold text-foreground">{t('tribulation.editorTitle')}</h2>
                <span className="text-xs text-secondary-text">{today}</span>
              </div>
              <input
                className="input-surface h-11 w-full rounded-xl border bg-transparent px-4 text-sm"
                value={title}
                maxLength={80}
                placeholder={t('tribulation.titlePlaceholder')}
                onChange={(event) => setTitle(event.target.value)}
                disabled={saving}
              />
              <textarea
                className="input-surface min-h-[160px] w-full rounded-xl border bg-transparent px-4 py-3 text-sm"
                value={content}
                maxLength={10000}
                placeholder={t('tribulation.contentPlaceholder')}
                onChange={(event) => setContent(event.target.value)}
                disabled={saving}
              />
              {editorMsg ? <InlineAlert variant={editorMsg.variant} message={editorMsg.text} /> : null}
              <div className="flex justify-end">
                <Button onClick={() => void handleSave()} isLoading={saving}>
                  {t('tribulation.save')}
                </Button>
              </div>
            </Card>

            {/* 月历 */}
            <Card className="space-y-3">
              <div className="flex items-center gap-2">
                <CalendarDays className="h-5 w-5 text-cyan" aria-hidden="true" />
                <h2 className="text-base font-semibold text-foreground">{t('tribulation.calendarTitle')}</h2>
                <span className="text-xs text-secondary-text">{stats?.month} · {t('tribulation.monthCount', { count: stats?.month_count ?? 0 })}</span>
              </div>
              <div className="grid grid-cols-7 gap-1 text-center text-xs text-secondary-text">
                {weekdayLabels.map((label) => (
                  <div key={label} className="py-1">{label}</div>
                ))}
                {calendarCells.map((day, index) => (
                  <div
                    key={index}
                    className={cn(
                      'flex h-9 items-center justify-center rounded-lg text-sm',
                      day === null
                        ? ''
                        : monthDays.has(day)
                          ? 'bg-primary-gradient font-semibold text-primary-foreground'
                          : 'border border-border/50 text-secondary-text',
                    )}
                  >
                    {day ?? ''}
                  </div>
                ))}
              </div>
            </Card>

            {/* 历史 */}
            <Card className="space-y-3">
              <h2 className="text-base font-semibold text-foreground">{t('tribulation.historyTitle')}</h2>
              {items.length === 0 ? (
                <EmptyState
                  icon={<NotebookPen className="h-8 w-8" />}
                  title={t('tribulation.empty')}
                  description={t('tribulation.emptyHint')}
                />
              ) : (
                <ul className="space-y-3">
                  {items.map((item) => (
                    <li key={item.id} className="rounded-xl border border-border/60 px-4 py-3">
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-sm font-medium text-foreground">{item.title || item.reflection_date}</span>
                        <div className="flex items-center gap-3">
                          <span className="text-xs text-secondary-text">{item.reflection_date}</span>
                          <button
                            type="button"
                            className="text-xs font-medium text-danger hover:underline"
                            onClick={() => setDeleteTarget(item)}
                          >
                            {t('common.delete')}
                          </button>
                        </div>
                      </div>
                      <p className="mt-2 whitespace-pre-wrap break-words text-sm text-secondary-text">{item.content}</p>
                    </li>
                  ))}
                </ul>
              )}
              {items.length < total ? (
                <div className="flex justify-center">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => void loadHistory(page + 1)}
                    isLoading={historyLoading}
                  >
                    {t('tribulation.loadMore')}
                  </Button>
                </div>
              ) : null}
            </Card>
          </>
        )}
      </div>

      <ConfirmDialog
        isOpen={deleteTarget !== null}
        title={t('tribulation.deleteConfirmTitle')}
        message={t('tribulation.deleteConfirmMessage')}
        confirmText={deleting ? t('common.deleting') : t('common.delete')}
        cancelText={t('common.cancel')}
        confirmDisabled={deleting}
        cancelDisabled={deleting}
        isDanger
        onConfirm={() => void handleDelete()}
        onCancel={() => {
          if (!deleting) {
            setDeleteTarget(null);
          }
        }}
      />
    </AppPage>
  );
}
