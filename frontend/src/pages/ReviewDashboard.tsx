import { DownloadOutlined, SendOutlined } from "@ant-design/icons";
import { App as AntApp, Button, Empty, Segmented, Spin, Tag } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import ReactECharts from "echarts-for-react";
import { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  listOptimizationSuggestions,
  sendOptimizationSuggestionToBrain,
  updateOptimizationSuggestion,
} from "../api/feedback";
import {
  getReviewOverview,
  listPerformanceSnapshots,
  type PerformanceSnapshot,
} from "../api/metrics";
import { PageHeader, Panel } from "../components/ui";
import { useCurrentWorkspace } from "../stores/currentWorkspace";
import { useThemeMode } from "../stores/theme";
import { chartBase } from "../theme/echarts";
import { silverTagStyle } from "../theme/styles";
import { CHART_COLORS } from "../theme/tokens";
import type {
  ContentStage,
  OptimizationSuggestion,
  OptimizationSuggestionStatus,
} from "../types";

const RANGE_DAYS: Record<string, number> = { day: 7, week: 30, month: 90 };
const COMPLETION_THRESHOLD = 30;

export default function ReviewDashboard() {
  const { message } = AntApp.useApp();
  const mode = useThemeMode((s) => s.mode);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [range, setRange] = useState("week");
  const accountId = useCurrentWorkspace((s) => s.accountId);
  const base = chartBase(mode);
  const trendRef = useRef<ReactECharts>(null);

  const overviewQuery = useQuery({
    queryKey: ["review-overview", range],
    queryFn: () => getReviewOverview(RANGE_DAYS[range]),
  });
  const snapshotsQuery = useQuery({
    queryKey: ["performance-snapshots", accountId],
    queryFn: () => listPerformanceSnapshots(accountId),
  });
  const suggestionsQuery = useQuery({
    queryKey: ["optimization-suggestions"],
    queryFn: listOptimizationSuggestions,
  });
  const suggestionMutation = useMutation({
    mutationFn: ({
      id,
      status,
      note,
    }: {
      id: number;
      status: OptimizationSuggestionStatus;
      note?: string;
    }) => updateOptimizationSuggestion(id, status, note),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["optimization-suggestions"] }),
  });
  const sendToBrainMutation = useMutation({
    mutationFn: sendOptimizationSuggestionToBrain,
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["optimization-suggestions"] }),
        qc.invalidateQueries({ queryKey: ["brain-tasks"] }),
      ]);
      message.success("已生成下一轮任务 Brief");
      navigate("/brain");
    },
  });
  const data = overviewQuery.data;

  const cat = (d: string[], interval = 0) => ({
    type: "category",
    data: d,
    ...base.categoryAxis,
    axisLabel: { ...base.categoryAxis.axisLabel, interval },
  });
  const val = (extra: object = {}) => ({ type: "value", ...base.valueAxis, ...extra });

  // —— 真实数据图 ——
  const trend = useMemo(() => {
    const days = (data?.trend ?? []).map((t) => t.date);
    return {
      ...base,
      legend: { ...base.legend, data: ["播放量", "曝光量"], right: 0, top: 0 },
      tooltip: { ...base.tooltip, trigger: "axis" },
      xAxis: cat(days, Math.max(0, Math.floor(days.length / 6))),
      yAxis: val(),
      series: [
        {
          name: "曝光量",
          type: "line",
          data: (data?.trend ?? []).map((t) => t.exposure),
          smooth: true,
          symbol: "none",
          lineStyle: { width: 1.5, color: CHART_COLORS[5], opacity: 0.7 },
        },
        {
          name: "播放量",
          type: "line",
          data: (data?.trend ?? []).map((t) => t.play),
          smooth: true,
          symbol: "none",
          lineStyle: { width: 2.5, color: CHART_COLORS[0] },
          areaStyle: {
            color: {
              type: "linear",
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: "rgba(196,201,209,0.25)" },
                { offset: 1, color: "rgba(196,201,209,0.01)" },
              ],
            },
          },
        },
      ],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, mode]);

  const engagement = useMemo(() => {
    const days = (data?.engagement ?? []).map((e) => e.date);
    // 比率以小数存，乘 100 显示为百分比
    return {
      ...base,
      legend: { ...base.legend, data: ["完播率", "点赞率"], right: 0, top: 0 },
      tooltip: { ...base.tooltip, trigger: "axis" },
      xAxis: cat(days, Math.max(0, Math.floor(days.length / 6))),
      yAxis: val({ axisLabel: { ...base.valueAxis.axisLabel, formatter: "{value}%" } }),
      series: [
        {
          name: "完播率",
          type: "line",
          data: (data?.engagement ?? []).map((e) => +(e.completion_rate * 100).toFixed(1)),
          smooth: true,
          symbol: "circle",
          symbolSize: 5,
          lineStyle: { width: 2.5, color: CHART_COLORS[1] },
          markLine: {
            silent: true,
            symbol: "none",
            lineStyle: { color: CHART_COLORS[2], type: "dashed" },
            data: [{ yAxis: COMPLETION_THRESHOLD, label: { formatter: "达标线 30%" } }],
          },
        },
        {
          name: "点赞率",
          type: "line",
          data: (data?.engagement ?? []).map((e) => +(e.like_rate * 100).toFixed(1)),
          smooth: true,
          symbol: "circle",
          symbolSize: 5,
          lineStyle: { width: 2, color: CHART_COLORS[4] },
        },
      ],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, mode]);

  const ranking = useMemo(() => {
    const items = [...(data?.rank_bottom ?? []), ...(data?.rank_top ?? [])];
    return {
      ...base,
      grid: { left: 8, right: 40, top: 10, bottom: 8, containLabel: true },
      tooltip: { ...base.tooltip, trigger: "axis", axisPointer: { type: "shadow" } },
      xAxis: val({ axisLabel: { ...base.valueAxis.axisLabel, formatter: "{value}%" } }),
      yAxis: {
        type: "category",
        data: items.map((r) => r.title).reverse(),
        ...base.categoryAxis,
      },
      series: [
        {
          type: "bar",
          data: items
            .map((r, i) => ({
              value: +(r.completion_rate * 100).toFixed(1),
              itemStyle: {
                color: i >= (data?.rank_bottom?.length ?? 0) ? CHART_COLORS[1] : CHART_COLORS[3],
                borderRadius: 4,
              },
            }))
            .reverse(),
          barWidth: 14,
          label: { show: true, position: "right", formatter: "{c}%", color: base.textStyle.color },
        },
      ],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, mode]);

  const exportTrend = () => {
    const inst = trendRef.current?.getEchartsInstance();
    if (!inst) return;
    const url = inst.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: "transparent" });
    const a = document.createElement("a");
    a.href = url;
    a.download = `流量趋势_${range}.png`;
    a.click();
  };

  const hasData = data?.has_data ?? false;

  return (
    <div>
      <PageHeader
        title="复盘看板"
        subtitle="数据 → 洞察 → 优化 → 执行闭环 · 仅展示已接入的真实回流和人工录入数据"
        extra={
          <Segmented
            value={range}
            onChange={setRange}
            options={[
              { label: "近 7 天", value: "day" },
              { label: "近 30 天", value: "week" },
              { label: "近 90 天", value: "month" },
            ]}
          />
        }
      />

      {hasData && (
        <div style={{ display: "flex", gap: 24, marginBottom: 16, flexWrap: "wrap" }}>
          <Stat label="总播放量" value={data!.total_play.toLocaleString()} />
          <Stat label="平均完播率" value={`${(data!.avg_completion_rate * 100).toFixed(1)}%`} />
          <Stat label="净增粉丝" value={data!.follower_delta.toLocaleString()} />
        </div>
      )}

      <Panel
        title="流量趋势 · 播放量与曝光量"
        extra={
          hasData && (
            <Button size="small" icon={<DownloadOutlined />} onClick={exportTrend}>
              导出 PNG
            </Button>
          )
        }
        style={{ marginBottom: 16 }}
      >
        {overviewQuery.isLoading ? (
          <div style={{ display: "grid", placeItems: "center", height: 260 }}>
            <Spin />
          </div>
        ) : hasData ? (
          <ReactECharts ref={trendRef} option={trend} style={{ height: 260 }} notMerge />
        ) : (
          <Empty
            description="暂无指标数据 · 接入抖音回流（E8）或手动录入后显示"
            style={{ padding: "60px 0" }}
          />
        )}
      </Panel>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <Panel title="完播 & 互动趋势">
          {hasData ? (
            <ReactECharts option={engagement} style={{ height: 240 }} notMerge />
          ) : (
            <Empty description="暂无数据" style={{ padding: "50px 0" }} />
          )}
        </Panel>
        <Panel title="内容排名 · TOP / BOTTOM 完播率">
          {hasData && (data!.rank_top.length > 0 || data!.rank_bottom.length > 0) ? (
            <ReactECharts option={ranking} style={{ height: 240 }} notMerge />
          ) : (
            <Empty description="暂无排名数据" style={{ padding: "50px 0" }} />
          )}
        </Panel>
        <Panel title="优化建议追踪">
          <SuggestionTracker
            loading={suggestionsQuery.isLoading}
            suggestions={suggestionsQuery.data ?? []}
            updatingId={
              suggestionMutation.isPending
                ? suggestionMutation.variables?.id
                : undefined
            }
            sendingId={
              sendToBrainMutation.isPending ? sendToBrainMutation.variables : undefined
            }
            onUpdate={(id, status, note) =>
              suggestionMutation.mutate({ id, status, note })
            }
            onSendToBrain={(id) => sendToBrainMutation.mutate(id)}
          />
        </Panel>
        <Panel title="发布闭环快照">
          <PerformanceSnapshotsPanel
            loading={snapshotsQuery.isLoading}
            snapshots={snapshotsQuery.data ?? []}
          />
        </Panel>
      </div>
    </div>
  );
}

const STAGE_LABEL: Record<ContentStage, string> = {
  positioning: "定位",
  content_direction: "编导",
  art_direction: "美术",
  video_creation: "视频",
  editing: "剪辑",
  operation: "运营",
  advertising: "投流",
  customer_service: "客服",
};

const SUGGESTION_STATUS: Record<
  OptimizationSuggestionStatus,
  { label: string; color: string }
> = {
  suggested: { label: "待采纳", color: "var(--dy-warning)" },
  accepted: { label: "已采纳", color: "var(--dy-info)" },
  verified: { label: "已验证", color: "var(--dy-success)" },
};

function PerformanceSnapshotsPanel({
  loading,
  snapshots,
}: {
  loading: boolean;
  snapshots: PerformanceSnapshot[];
}) {
  if (loading) {
    return (
      <div style={{ display: "grid", placeItems: "center", height: 240 }}>
        <Spin />
      </div>
    );
  }
  if (snapshots.length === 0) {
    return (
      <Empty
        description="暂无发布回流数据 · 抖音数据同步或手动录入后显示"
        style={{ padding: "50px 0" }}
      />
    );
  }

  return (
    <div style={{ display: "grid", gap: 10 }}>
      {snapshots.slice(0, 5).map((snapshot) => (
        <div
          key={snapshot.id}
          style={{
            display: "grid",
            gap: 8,
            padding: "10px 0",
            borderBottom: "1px solid var(--dy-border-subtle)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <strong style={{ color: "var(--dy-text)", fontSize: 13 }}>
              {snapshot.title ?? `内容 #${snapshot.content_item_id ?? snapshot.id}`}
            </strong>
            <Tag style={{ marginInlineEnd: 0, ...silverTagStyle }}>
              {metricSourceLabel(snapshot.source)}
            </Tag>
            {snapshot.account_id != null && (
              <Tag style={{ marginInlineEnd: 0 }}>账号 #{snapshot.account_id}</Tag>
            )}
            <span style={{ marginLeft: "auto", color: "var(--dy-faint)", fontSize: 12 }}>
              {snapshot.stat_date}
            </span>
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
              gap: 8,
              fontSize: 12,
            }}
          >
            <SnapshotMetric label="播放" value={snapshot.play.toLocaleString()} />
            <SnapshotMetric label="曝光" value={snapshot.exposure.toLocaleString()} />
            <SnapshotMetric
              label="完播"
              value={`${(snapshot.completion_rate * 100).toFixed(1)}%`}
            />
            <SnapshotMetric
              label="互动"
              value={`${((snapshot.like_rate + snapshot.comment_rate + snapshot.share_rate) * 100).toFixed(1)}%`}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

function SnapshotMetric({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ color: "var(--dy-faint)", marginBottom: 2 }}>{label}</div>
      <div className="dy-tabular" style={{ color: "var(--dy-text)", fontWeight: 700 }}>
        {value}
      </div>
    </div>
  );
}

function metricSourceLabel(source: PerformanceSnapshot["source"]) {
  const labels: Record<PerformanceSnapshot["source"], string> = {
    douyin: "抖音回流",
    xiaohongshu: "小红书回流",
    shipinhao: "视频号回流",
    manual: "手动录入",
    demo: "示例数据",
  };
  return labels[source] ?? source;
}

function SuggestionTracker({
  loading,
  suggestions,
  updatingId,
  sendingId,
  onUpdate,
  onSendToBrain,
}: {
  loading: boolean;
  suggestions: OptimizationSuggestion[];
  updatingId?: number;
  sendingId?: number;
  onUpdate: (id: number, status: OptimizationSuggestionStatus, note?: string) => void;
  onSendToBrain: (id: number) => void;
}) {
  if (loading) {
    return (
      <div style={{ display: "grid", placeItems: "center", height: 240 }}>
        <Spin />
      </div>
    );
  }
  if (suggestions.length === 0) {
    return <Empty description="暂无优化建议 · 运营复盘完成后自动生成" style={{ padding: "50px 0" }} />;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      {suggestions.slice(0, 5).map((item, index) => {
        const status = SUGGESTION_STATUS[item.status];
        const isUpdating = updatingId === item.id;
        const isSending = sendingId === item.id;
        return (
          <div
            key={item.id}
            style={{
              padding: index === 0 ? "0 0 12px" : "12px 0",
              borderTop: index === 0 ? "none" : "1px solid var(--dy-border-subtle)",
              display: "grid",
              gap: 8,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <Tag
                style={{
                  marginInlineEnd: 0,
                  color: status.color,
                  borderColor: "var(--dy-border)",
                  background: "transparent",
                }}
              >
                {status.label}
              </Tag>
              {item.target_stage && (
                <Tag style={{ marginInlineEnd: 0, ...silverTagStyle }}>
                  {STAGE_LABEL[item.target_stage]}
                </Tag>
              )}
              <span style={{ fontSize: 12, color: "var(--dy-faint)" }}>
                {item.content_title}
              </span>
            </div>
            <div style={{ color: "var(--dy-text)", fontSize: 13, lineHeight: 1.55 }}>
              {item.suggestion}
            </div>
            {item.status !== "verified" && (
              <div style={{ display: "flex", gap: 8 }}>
                <Button
                  size="small"
                  type="primary"
                  icon={<SendOutlined />}
                  loading={isSending}
                  onClick={() => onSendToBrain(item.id)}
                >
                  送入运营大脑
                </Button>
                {item.status === "suggested" && (
                  <Button
                    size="small"
                    loading={isUpdating}
                    onClick={() => onUpdate(item.id, "accepted", "下周期采用")}
                  >
                    仅采纳
                  </Button>
                )}
                <Button
                  size="small"
                  loading={isUpdating}
                  onClick={() => onUpdate(item.id, "verified", "已验证")}
                >
                  标记已验证
                </Button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="dy-tabular" style={{ fontSize: 24, fontWeight: 600, color: "var(--dy-text)" }}>
        {value}
      </div>
      <div style={{ fontSize: 12.5, color: "var(--dy-muted)" }}>{label}</div>
    </div>
  );
}
