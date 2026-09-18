import apiClient from './index';

// “渡劫”每日心得。后端为 /api/v1/daily-reflections（中性前缀，Web 与小程序复用同一处理逻辑）。
export type DailyReflectionItem = {
  id: number;
  reflection_date: string;
  title: string;
  content: string;
  created_at?: string | null;
  updated_at?: string | null;
};

export type DailyReflectionList = {
  items: DailyReflectionItem[];
  total: number;
  page: number;
  page_size: number;
};

export type DailyReflectionStats = {
  total: number;
  current_streak: number;
  longest_streak: number;
  today_done: boolean;
  month: string;
  month_count: number;
  month_days: number[];
};

export const dailyReflectionsApi = {
  async list(page = 1, pageSize = 20): Promise<DailyReflectionList> {
    const { data } = await apiClient.get<DailyReflectionList>('/api/v1/daily-reflections', {
      params: { page, page_size: pageSize },
    });
    return data;
  },

  async getByDate(reflectionDate: string): Promise<DailyReflectionItem | null> {
    const { data } = await apiClient.get<DailyReflectionItem | null>(
      `/api/v1/daily-reflections/by-date/${reflectionDate}`,
    );
    return data;
  },

  async upsert(reflectionDate: string, content: string, title = ''): Promise<DailyReflectionItem> {
    const { data } = await apiClient.put<DailyReflectionItem>('/api/v1/daily-reflections', {
      reflection_date: reflectionDate,
      title,
      content,
    });
    return data;
  },

  async remove(id: number): Promise<{ deleted: number }> {
    const { data } = await apiClient.delete<{ deleted: number }>(`/api/v1/daily-reflections/${id}`);
    return data;
  },

  async stats(referenceDate?: string, month?: string): Promise<DailyReflectionStats> {
    const { data } = await apiClient.get<DailyReflectionStats>('/api/v1/daily-reflections/stats', {
      params: { reference_date: referenceDate, month },
    });
    return data;
  },
};
