import { DownloadOutlined } from "@ant-design/icons";
import { Button, Empty, Segmented, Spin, Tag } from "antd";
import { useQuery } from "@tanstack/react-query";
import ReactECharts from "echarts-for-react";
import { useMemo, useRef, useState } from "react";

import { getReviewOverview } from "../api/metrics";
import { PageHeader, Panel } from "../components/ui";
import {
  FUNNEL,
  HEATMAP,
  HEATMAP_DAYS,
  PLATFORM_RADAR,
  ROI_DAYS,
  ROI_SERIES,
  SENTIMENT,
} from "../mock/metrics";
import { useThemeMode } from "../stores/theme";
import { chartBase } from "../theme/echarts";
import { CHART_COLORS } from "../theme/tokens";

const RANGE_DAYS: Record<string, number> = { day: 7, week: 30, month: 90 };
const COMPLETION_THRESHOLD = 30;

// 标注数据源尚未接入真实回流的图（M2 客服 / M3 投流 / 多平台回流到位后替换）。
function MockTag() {
  return (
    <Tag style={{ marginInlineEnd: 0, fontSize: 11 }} color="default">
      示例数据
    </Tag>
  );
}

export default function ReviewDashboard() {
  const mode = useThemeMode((s) => s.mode);
  const [range, setRange] = useState("week");
  const base = chartBase(mode);
  const trendRef = useRef<ReactECharts>(null);

  const overviewQuery = useQuery({
    queryKey: ["review-overview", range],
    queryFn: () => getReviewOverview(RANGE_DAYS[range]),
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
                { offset: 0, color: "rgba(91,140,255,0.25)" },
                { offset: 1, color: "rgba(91,140,255,0.01)" },
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

  // —— 示例数据图（数据源未接入）——
  const heatmap = {
    ...base,
    grid: { left: 8, right: 8, top: 10, bottom: 24, containLabel: true },
    tooltip: {
      ...base.tooltip,
      formatter: (p: { value: [number, number, number] }) =>
        `${HEATMAP_DAYS[p.value[1]]} ${p.value[0]}:00 · 效果 ${p.value[2]}`,
    },
    xAxis: {
      type: "category",
      data: Array.from({ length: 24 }, (_, i) => i),
      ...base.categoryAxis,
      axisLabel: { ...base.categoryAxis.axisLabel, interval: 3 },
    },
    yAxis: { type: "category", data: HEATMAP_DAYS, ...base.categoryAxis },
    visualMap: {
      min: 0,
      max: 100,
      calculable: true,
      orient: "horizontal",
      left: "center",
      bottom: -4,
      itemHeight: 80,
      textStyle: { color: base.textStyle.color, fontSize: 11 },
      inRange: { color: ["#11151d", "#23407a", "#5b8cff"] },
    },
    series: [
      {
        type: "heatmap",
        data: HEATMAP,
        itemStyle: { borderColor: "rgba(0,0,0,0.15)", borderWidth: 1 },
      },
    ],
  };

  const radar = {
    ...base,
    legend: { ...base.legend, data: PLATFORM_RADAR.series.map((s) => s.name), top: 0 },
    tooltip: { ...base.tooltip },
    radar: {
      indicator: PLATFORM_RADAR.indicators,
      radius: "62%",
      axisName: { color: base.textStyle.color, fontSize: 11 },
      splitLine: {
        lineStyle: { color: mode === "dark" ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)" },
      },
      splitArea: { show: false },
      axisLine: {
        lineStyle: { color: mode === "dark" ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)" },
      },
    },
    series: [
      {
        type: "radar",
        data: PLATFORM_RADAR.series.map((s, i) => ({
          name: s.name,
          value: s.value,
          lineStyle: { color: CHART_COLORS[i] },
          areaStyle: { opacity: 0.12, color: CHART_COLORS[i] },
        })),
      },
    ],
  };

  const roi = {
    ...base,
    tooltip: { ...base.tooltip, trigger: "axis" },
    xAxis: cat(ROI_DAYS, 2),
    yAxis: val(),
    series: [
      {
        type: "line",
        data: ROI_SERIES,
        smooth: true,
        symbol: "none",
        lineStyle: { width: 2.5, color: CHART_COLORS[0] },
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { color: CHART_COLORS[3], type: "dashed" },
          data: [{ yAxis: 1, label: { formatter: "保本线" } }],
        },
      },
    ],
  };

  const sentiment = {
    ...base,
    tooltip: { ...base.tooltip, trigger: "item", formatter: "{b}: {c}% " },
    legend: { ...base.legend, bottom: 0 },
    series: [
      {
        type: "pie",
        radius: ["52%", "72%"],
        center: ["50%", "45%"],
        data: SENTIMENT.map((s, i) => ({
          ...s,
          itemStyle: { color: [CHART_COLORS[1], CHART_COLORS[2], CHART_COLORS[3]][i] },
        })),
        label: { color: base.textStyle.color, formatter: "{b}\n{c}%" },
      },
    ],
  };

  const funnel = {
    ...base,
    tooltip: { ...base.tooltip, trigger: "item", formatter: "{b}: {c}%" },
    series: [
      {
        type: "funnel",
        left: "8%",
        right: "8%",
        top: 8,
        bottom: 8,
        minSize: "24%",
        gap: 3,
        label: { color: "#fff", formatter: "{b} {c}%" },
        data: FUNNEL.map((f, i) => ({ ...f, itemStyle: { color: CHART_COLORS[i] } })),
      },
    ],
  };

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
        subtitle="数据 → 洞察 → 优化 → 执行 闭环 · 内容指标接真实回流，其余示例数据待集成"
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
        <Panel title="发布时段效果 · 24h × 7天" extra={<MockTag />}>
          <ReactECharts option={heatmap} style={{ height: 260 }} notMerge />
        </Panel>
        <Panel title="平台对比 · 核心指标" extra={<MockTag />}>
          <ReactECharts option={radar} style={{ height: 260 }} notMerge />
        </Panel>
        <Panel title="投流 ROI 趋势" extra={<MockTag />}>
          <ReactECharts option={roi} style={{ height: 240 }} notMerge />
        </Panel>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          <Panel title="评论情感" extra={<MockTag />}>
            <ReactECharts option={sentiment} style={{ height: 240 }} notMerge />
          </Panel>
          <Panel title="私域转化漏斗" extra={<MockTag />}>
            <ReactECharts option={funnel} style={{ height: 240 }} notMerge />
          </Panel>
        </div>
      </div>
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
