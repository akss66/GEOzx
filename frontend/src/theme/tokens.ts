import type { ThemeConfig } from "antd";
import { theme as antdTheme } from "antd";

export type Mode = "dark" | "light";

/**
 * 指挥中心配色（克制 · 精确）。深色为长时间盯盘的主场景，浅色为可选。
 * 冷中性 + 单一强调色（蓝）+ 色盲安全状态色。所有正文对比度 ≥ AA。
 */
const DARK = {
  layout: "#0b0e14",
  container: "#11151d",
  elevated: "#161b24",
  sider: "#0c0f16",
  border: "#232a36",
  borderSubtle: "#1a202a",
  text: "#e7eaf0",
  textSecondary: "#9aa4b4",
  textTertiary: "#6a7384",
};

const LIGHT = {
  layout: "#f5f6f8",
  container: "#ffffff",
  elevated: "#ffffff",
  sider: "#ffffff",
  border: "#e4e7ec",
  borderSubtle: "#eef0f3",
  text: "#1a1f29",
  textSecondary: "#586273",
  textTertiary: "#8a94a6",
};

export const ACCENT = "#5b8cff";
export const SEMANTIC = {
  success: "#3fb950",
  warning: "#d6a126",
  error: "#f0566b",
  info: "#58a6ff",
};

/** 色盲安全的定性色板（用于图表分类）；配合形状/标签冗余使用。 */
export const CHART_COLORS = [
  "#5b8cff",
  "#3fb950",
  "#d6a126",
  "#f0566b",
  "#a371f7",
  "#39c5cf",
  "#e3759b",
  "#d2a8ff",
];

/** 平台品牌色（始终叠加文字/图标标签，不仅靠颜色区分）。 */
export const PLATFORM_COLORS: Record<string, string> = {
  douyin: "#5b8cff",
  xiaohongshu: "#f0566b",
  shipinhao: "#3fb950",
};

export function buildTheme(mode: Mode): ThemeConfig {
  const c = mode === "dark" ? DARK : LIGHT;
  return {
    algorithm: mode === "dark" ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
    token: {
      colorPrimary: ACCENT,
      colorInfo: SEMANTIC.info,
      colorSuccess: SEMANTIC.success,
      colorWarning: SEMANTIC.warning,
      colorError: SEMANTIC.error,
      colorBgLayout: c.layout,
      colorBgContainer: c.container,
      colorBgElevated: c.elevated,
      colorBorder: c.border,
      colorBorderSecondary: c.borderSubtle,
      colorText: c.text,
      colorTextSecondary: c.textSecondary,
      colorTextTertiary: c.textTertiary,
      borderRadius: 8,
      borderRadiusLG: 12,
      wireframe: false,
      fontFamily:
        'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", Roboto, sans-serif',
      fontSize: 14,
      controlHeight: 34,
    },
    components: {
      Layout: {
        headerBg: mode === "dark" ? c.sider : c.container,
        siderBg: c.sider,
        bodyBg: c.layout,
        headerHeight: 56,
        headerPadding: "0 20px",
      },
      Menu: {
        itemBg: "transparent",
        itemSelectedBg: mode === "dark" ? "rgba(91,140,255,0.14)" : "rgba(91,140,255,0.10)",
        itemSelectedColor: ACCENT,
        itemHeight: 38,
        itemMarginInline: 8,
        itemBorderRadius: 8,
        iconSize: 16,
      },
      Card: {
        colorBgContainer: c.container,
        borderRadiusLG: 12,
      },
      Table: {
        headerBg: mode === "dark" ? c.elevated : "#fafbfc",
        headerColor: c.textSecondary,
        rowHoverBg: mode === "dark" ? "rgba(255,255,255,0.025)" : "#fafbfc",
        borderColor: c.borderSubtle,
        cellPaddingBlock: 12,
      },
      Statistic: { contentFontSize: 26 },
      Tag: { borderRadiusSM: 6 },
      Segmented: {
        trackBg: mode === "dark" ? c.elevated : "#f0f1f3",
      },
    },
  };
}
