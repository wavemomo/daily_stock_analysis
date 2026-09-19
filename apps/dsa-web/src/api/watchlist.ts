import apiClient from './index';

// 按用户的个人自选（= 分析池），与小程序共用同一后端存储。
// 走中性前缀 /api/v1/watchlist*，由 Cookie 会话解析当前用户。
export type WatchlistItem = {
  id: number;
  stock_code: string;
  stock_name: string;
  // 是否纳入本人定时分析池（默认纳入）。
  scheduled: boolean;
};

export type SchedulePref = {
  scheduled_analysis_enabled: boolean;
  scheduled_count: number;
};

export const watchlistApi = {
  async listItems(): Promise<WatchlistItem[]> {
    const { data } = await apiClient.get<{ items: WatchlistItem[] }>('/api/v1/watchlist');
    return data.items ?? [];
  },

  async addItem(stockCode: string, stockName = ''): Promise<WatchlistItem> {
    const { data } = await apiClient.post<WatchlistItem>('/api/v1/watchlist/add', {
      stock_code: stockCode,
      stock_name: stockName,
    });
    return data;
  },

  async setScheduled(stockCode: string, scheduled: boolean): Promise<WatchlistItem> {
    const { data } = await apiClient.post<WatchlistItem>('/api/v1/watchlist/scheduled', {
      stock_code: stockCode,
      scheduled,
    });
    return data;
  },

  async getSchedulePref(): Promise<SchedulePref> {
    const { data } = await apiClient.get<SchedulePref>('/api/v1/watchlist/schedule-pref');
    return data;
  },

  async setSchedulePref(enabled: boolean): Promise<SchedulePref> {
    const { data } = await apiClient.put<SchedulePref>('/api/v1/watchlist/schedule-pref', { enabled });
    return data;
  },
};
