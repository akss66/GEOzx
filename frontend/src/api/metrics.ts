import { api } from "./client";

export interface TrendPoint {
  date: string;
  play: number;
  exposure: number;
}

export interface EngagementPoint {
  date: string;
  completion_rate: number;
  like_rate: number;
}

export interface RankItem {
  title: string;
  completion_rate: number;
}

export interface ReviewOverview {
  has_data: boolean;
  trend: TrendPoint[];
  engagement: EngagementPoint[];
  rank_top: RankItem[];
  rank_bottom: RankItem[];
  total_play: number;
  avg_completion_rate: number;
  follower_delta: number;
}

export interface PerformanceSnapshot {
  id: number;
  content_item_id: number | null;
  account_id: number | null;
  source: "douyin" | "xiaohongshu" | "shipinhao" | "manual" | "demo";
  stat_date: string;
  title: string | null;
  play: number;
  exposure: number;
  completion_rate: number;
  like_rate: number;
  comment_rate: number;
  share_rate: number;
  follower_delta: number;
  created_at: string;
}

export async function getReviewOverview(days = 30): Promise<ReviewOverview> {
  const { data } = await api.get<ReviewOverview>("/metrics/overview", { params: { days } });
  return data;
}

export async function listPerformanceSnapshots(
  accountId?: number | null,
): Promise<PerformanceSnapshot[]> {
  const { data } = await api.get<PerformanceSnapshot[]>("/metrics/performance-snapshots", {
    params: accountId != null ? { account_id: accountId } : undefined,
  });
  return data;
}
