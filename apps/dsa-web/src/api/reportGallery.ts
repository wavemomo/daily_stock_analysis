import apiClient from './index';
import { toCamelCase } from './utils';

export interface ReportGalleryItem {
  id: number;
  stockCode: string;
  stockName?: string | null;
  reportType?: string | null;
  sentimentScore?: number | null;
  operationAdvice?: string | null;
  analysisSummary?: string | null;
  ownerName?: string | null;
  createdAt?: string | null;
}

export interface ReportGalleryListResponse {
  total: number;
  page: number;
  limit: number;
  date: string;
  items: ReportGalleryItem[];
}

export interface ReportGalleryDetail {
  id: number;
  stockCode: string;
  stockName?: string | null;
  reportType?: string | null;
  ownerName?: string | null;
  createdAt?: string | null;
  content: string;
}

export interface ReportEmailStatus {
  email?: string | null;
  emailVerified: boolean;
  hasPassword: boolean;
  reportEmailEnabled: boolean;
}

export interface GetReportGalleryParams {
  search?: string;
  page?: number;
  limit?: number;
}

export interface RequestOptions {
  signal?: AbortSignal;
}

export const reportGalleryApi = {
  async getList(
    params: GetReportGalleryParams = {},
    options: RequestOptions = {},
  ): Promise<ReportGalleryListResponse> {
    const { search, page = 1, limit = 20 } = params;
    const query: Record<string, string | number> = { page, limit };
    if (search && search.trim()) query.search = search.trim();
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/analysis/gallery', {
      params: query,
      signal: options.signal,
    });
    const data = toCamelCase<ReportGalleryListResponse>(response.data);
    return { ...data, items: (data.items || []).map((item) => toCamelCase<ReportGalleryItem>(item)) };
  },

  async getDetail(recordId: number): Promise<ReportGalleryDetail> {
    const response = await apiClient.get<Record<string, unknown>>(`/api/v1/analysis/gallery/${recordId}`);
    return toCamelCase<ReportGalleryDetail>(response.data);
  },

  // Web 登录用户即已绑定邮箱的用户；复用统一的邮箱偏好端点（cookie 会话 + account.self）。
  async getEmailStatus(): Promise<ReportEmailStatus> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/miniapp/auth/email');
    return toCamelCase<ReportEmailStatus>(response.data);
  },

  async setReportEmailDelivery(enabled: boolean): Promise<ReportEmailStatus> {
    const response = await apiClient.patch<Record<string, unknown>>(
      '/api/v1/miniapp/auth/email/report-delivery',
      { enabled },
    );
    return toCamelCase<ReportEmailStatus>(response.data);
  },
};
