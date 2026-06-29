import { Segmented } from "antd";
import ReactECharts from "echarts-for-react";
import { useState } from "react";

import { PageHeader, Panel } from "../components/ui";
import {
  COMPLETION_RATE,
  COMPLETION_THRESHOLD,
  FUNNEL,
  HEATMAP,
  HEATMAP_DAYS,
  INTERACTION_RATE,
  PLATFORM_RADAR,
  RANK_BOTTOM,
  RANK_TOP,
  ROI_DAYS,
  ROI_SERIES,
  SENTIMENT,
  TREND_DAYS,
  TREND_EXPOSURE,
  TREND_PLAY,
  WEEK_LABELS,
} from "../mock/metrics";
import { useThemeMode } from "../stores/theme";
import { chartBase } from "../theme/echarts";
import { CHART_COLORS } from "../theme/tokens";

export default function ReviewDashboard() {
  const mode = useThemeMode((s) => s.mode);
  const [range, setRange] = useState("week");
  const base = chartBase(mode);

  const cat = (data: string[], interval = 0) => ({
    type: "category",
    data,
    ...base.categoryAxis,
    axisLabel: { ...base.categoryAxis.axisLabel, interval },
  });
  const val = (extra: object = {}) => ({ type: "value", ...base.valueAxis, ...extra });

  const trend = {
    ...base,
    legend: { ...base.legend, data: ["播放量", "曝光量"], right: 0, top: 0 },
    tooltip: { ...base.tooltip, trigger: "axis" },
    xAxis: cat(TREND_DAYS, 5),
    yAxis: val(),
    series: [
      {
        name: "曝光量",
        type: "line",
        data: TREND_EXPOSURE,
        smooth: true,
        symbol: "none",
        lineStyle: { width: 1.5, color: CHART_COLORS[5], opacity: 0.7 },
      },
      {
        name: "播放量",
        type: "line",
        data: TREND_PLAY,
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

  const engagement = {
    ...base,
    legend: { ...base.legend, data: ["完播率", "互动率"], right: 0, top: 0 },
    tooltip: { ...base.tooltip, trigger: "axis" },
    xAxis: cat(WEEK_LABELS),
    yAxis: val({ axisLabel: { ...base.valueAxis.axisLabel, formatter: "{value}%" } }),
    series: [
      {
        name: "完播率",
        type: "line",
        data: COMPLETION_RATE,
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
        name: "互动率",
        type: "line",
        data: INTERACTION_RATE,
        smooth: true,
        symbol: "circle",
        symbolSize: 5,
        lineStyle: { width: 2, color: CHART_COLORS[4] },
      },
    ],
  };

  const ranking = {
    ...base,
    grid: { left: 8, right: 40, top: 10, bottom: 8, containLabel: true },
    tooltip: { ...base.tooltip, trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: val({ axisLabel: { ...base.valueAxis.axisLabel, formatter: "{value}%" } }),
    yAxis: {
      type: "category",
      data: [...RANK_BOTTOM, ...RANK_TOP].map((r) => r.name).reverse(),
      ...base.categoryAxis,
    },
    series: [
      {
        type: "bar",
        data: [...RANK_BOTTOM, ...RANK_TOP]
          .map((r, i) => ({
            value: r.value,
            itemStyle: { color: i >= 3 ? CHART_COLORS[1] : CHART_COLORS[3], borderRadius: 4 },
          }))
          .reverse(),
        barWidth: 14,
        label: { show: true, position: "right", formatter: "{c}%", color: base.textStyle.color },
      },
    ],
  };

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
      splitLine: { lineStyle: { color: mode === "dark" ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)" } },
      splitArea: { show: false },
      axisLine: { lineStyle: { color: mode === "dark" ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)" } },
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

  return (
    <div>
      <PageHeader
        title="复盘看板"
        subtitle="数据 → 洞察 → 优化 → 执行 闭环 · 指标标注来源与时间，可据此决策"
        extra={
          <Segmented
            value={range}
            onChange={setRange}
            options={[
              { label: "日", value: "day" },
              { label: "周", value: "week" },
              { label: "月", value: "month" },
            ]}
          />
        }
      />

      <Panel title="流量趋势 · 播放量与曝光量" style={{ marginBottom: 16 }}>
        <ReactECharts option={trend} style={{ height: 260 }} notMerge />
      </Panel>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <Panel title="完播 & 互动趋势">
          <ReactECharts option={engagement} style={{ height: 240 }} notMerge />
        </Panel>
        <Panel title="内容排名 · 本周 TOP3 / BOTTOM3 完播率">
          <ReactECharts option={ranking} style={{ height: 240 }} notMerge />
        </Panel>
        <Panel title="发布时段效果 · 24h × 7天">
          <ReactECharts option={heatmap} style={{ height: 260 }} notMerge />
        </Panel>
        <Panel title="平台对比 · 核心指标">
          <ReactECharts option={radar} style={{ height: 260 }} notMerge />
        </Panel>
        <Panel title="投流 ROI 趋势">
          <ReactECharts option={roi} style={{ height: 240 }} notMerge />
        </Panel>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          <Panel title="评论情感">
            <ReactECharts option={sentiment} style={{ height: 240 }} notMerge />
          </Panel>
          <Panel title="私域转化漏斗">
            <ReactECharts option={funnel} style={{ height: 240 }} notMerge />
          </Panel>
        </div>
      </div>
    </div>
  );
}
