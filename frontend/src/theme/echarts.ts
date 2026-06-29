import { CHART_COLORS, type Mode } from "./tokens";

/** ECharts 通用基底：统一字体、网格、坐标轴、tooltip 风格（克制、深浅自适应）。 */
export function chartBase(mode: Mode) {
  const dark = mode === "dark";
  const axisLine = dark ? "rgba(255,255,255,0.12)" : "rgba(0,0,0,0.12)";
  const splitLine = dark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.06)";
  const label = dark ? "#9aa4b4" : "#586273";
  return {
    color: CHART_COLORS,
    textStyle: {
      fontFamily:
        'Inter, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif',
      color: label,
    },
    grid: { left: 8, right: 16, top: 28, bottom: 8, containLabel: true },
    tooltip: {
      backgroundColor: dark ? "#161b24" : "#ffffff",
      borderColor: dark ? "#232a36" : "#e4e7ec",
      borderWidth: 1,
      textStyle: { color: dark ? "#e7eaf0" : "#1a1f29", fontSize: 12 },
      padding: [8, 12],
    },
    legend: {
      textStyle: { color: label },
      icon: "roundRect",
      itemWidth: 10,
      itemHeight: 10,
    },
    categoryAxis: {
      axisLine: { lineStyle: { color: axisLine } },
      axisTick: { show: false },
      axisLabel: { color: label },
      splitLine: { show: false },
    },
    valueAxis: {
      axisLine: { show: false },
      axisLabel: { color: label },
      splitLine: { lineStyle: { color: splitLine } },
    },
  };
}
