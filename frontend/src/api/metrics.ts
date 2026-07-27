import { api } from "./client";
import type { OptimizationSuggestion } from "../types";

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
  completion_rate_5s: number | null;
  bounce_rate_2s: number | null;
  profile_visit_count: number | null;
  created_at: string;
}

export type ReviewPeriodDays = 7 | 30 | 90;

export interface ReviewGoalInput {
  period_days: ReviewPeriodDays;
  target_play?: number;
  target_completion_rate?: number;
  target_follower_delta?: number;
}

export interface ReviewGoal {
  id: number | null;
  period_days: number;
  target_play?: number | null;
  target_completion_rate?: number | null;
  target_follower_delta?: number | null;
  status: "not_configured" | "insufficient_data" | "behind" | "on_track" | "achieved";
  achievement_percent: number | null;
  components: Array<{
    metric: "play" | "completion_rate" | "follower_delta";
    label: string;
    current: number;
    target: number;
    achievement_percent: number;
  }>;
  summary: string;
}

export interface ReviewWorkspace {
  account: {
    id: number;
    nickname: string;
    platform: string;
    auth_status: string;
    data_sync_status: string;
  };
  period: {
    days: number;
    current_start: string;
    current_end: string;
    previous_start: string;
    previous_end: string;
  };
  data_status: {
    has_data: boolean;
    sources: Array<PerformanceSnapshot["source"] | "platform_export" | "screenshot_verified" | "manual_entry" | "official_api">;
    latest_stat_date: string | null;
    latest_synced_at: string | null;
    latest_confirmed_at: string | null;
    days_since_observed: number | null;
    days_since_confirmed: number | null;
    coverage: Record<string, "available" | "missing" | "ambiguous">;
    conflict_count: number;
    source_summary: Array<{
      batch_id: number | null;
      source_kind: string;
      data_domains: string[];
      confirmed_at: string | null;
      period_start: string | null;
      period_end: string | null;
    }>;
    missing_reasons: string[];
  };
  goal: ReviewGoal;
  conclusion: string;
  totals: {
    play: number;
    exposure: number;
    avg_completion_rate: number;
    avg_engagement_rate: number;
    follower_delta: number;
  };
  changes: Array<{
    metric: "play" | "completion_rate" | "follower_delta";
    label: string;
    current: number;
    previous: number | null;
    delta_percent: number | null;
    direction: "up" | "down" | "flat" | "baseline";
    summary: string;
  }>;
  trend: TrendPoint[];
  engagement: EngagementPoint[];
  attributions: Array<{
    content_item_id: number | null;
    title: string;
    play: number;
    completion_rate: number;
    engagement_rate: number;
    role: "driver" | "opportunity";
    reason: string;
  }>;
  evidence: PerformanceSnapshot[];
  suggestions: OptimizationSuggestion[];
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

export async function getReviewWorkspace(
  accountId: number,
  days: ReviewPeriodDays,
): Promise<ReviewWorkspace> {
  const { data } = await api.get<ReviewWorkspace>("/metrics/review-workspace", {
    params: { account_id: accountId, days },
  });
  return data;
}

export async function upsertReviewGoal(
  accountId: number,
  input: ReviewGoalInput,
): Promise<ReviewGoal> {
  const { data } = await api.put<ReviewGoal>(`/metrics/review-goals/${accountId}`, input);
  return data;
}
