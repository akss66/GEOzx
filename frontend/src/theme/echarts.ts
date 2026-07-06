import { CHART_COLORS, FONT_SANS, type Mode } from "./tokens";

/** ECharts 通用基底：统一字体、网格、坐标轴、tooltip 风格（克制、深浅自适应）。 */
export function chartBase(mode: Mode) {
  const dark = mode === "dark";
  const axisLine = dark ? "rgba(255,255,255,0.12)" : "rgba(0,0,0,0.12)";
  const splitLine = dark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.06)";
  const label = dark ? "#9ca0a8" : "#5c6169";
  return {
    color: CHART_COLORS,
    textStyle: {
      fontFamily: FONT_SANS,
      color: label,
    },
    grid: { left: 8, right: 16, top: 28, bottom: 8, containLabel: true },
    tooltip: {
      backgroundColor: dark ? "#191b1f" : "#ffffff",
      borderColor: dark ? "#1f2227" : "#e3e5e8",
      borderWidth: 1,
      textStyle: { color: dark ? "#e8eaef" : "#1a1e23", fontSize: 12 },
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
