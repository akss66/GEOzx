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

export async function getReviewOverview(days = 30): Promise<ReviewOverview> {
  const { data } = await api.get<ReviewOverview>("/metrics/overview", { params: { days } });
  return data;
}
