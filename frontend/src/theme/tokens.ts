import type { ThemeConfig } from "antd";
import { theme as antdTheme } from "antd";

export type Mode = "dark" | "light";

const LIGHT = {
  layout: "#f6f7f8",
  container: "#ffffff",
  elevated: "#fbfbfc",
  sider: "#fbfbfc",
  toolbar: "rgba(255,255,255,0.86)",
  border: "#dfe2e6",
  borderSubtle: "#eceef1",
  text: "#111315",
  textSecondary: "#4f5863",
  textTertiary: "#747d88",
  selectedBg: "#f0f1f3",
  hoverBg: "#f3f4f5",
};

const DARK = {
  layout: "#101113",
  container: "#17191c",
  elevated: "#1d2024",
  sider: "#141619",
  toolbar: "rgba(20,22,25,0.92)",
  border: "#2c3036",
  borderSubtle: "#24282e",
  text: "#f2f3f5",
  textSecondary: "#b5bbc3",
  textTertiary: "#858d97",
  selectedBg: "#24272c",
  hoverBg: "#202328",
};

export const ACCENT = "#111315";
export const ACCENT_HOVER = "#2a2e33";
export const ACCENT_ACTIVE = "#050607";
export const FONT_SANS =
  '"OpenAI Sans", "Söhne", "Helvetica Neue", -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", Arial, sans-serif';

export const SEMANTIC = {
  success: "#1f8f4d",
  warning: "#a66a00",
  error: "#c43d4b",
  info: "#59616c",
};

export const CHART_COLORS = [
  "#111315",
  "#59616c",
  "#8d949d",
  "#c9ced6",
  "#1f8f4d",
  "#a66a00",
  "#c43d4b",
  "#3f6f6a",
];

export const PLATFORM_COLORS: Record<string, string> = {
  douyin: "#111315",
  xiaohongshu: "#c43d4b",
  shipinhao: "#1f8f4d",
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
      borderRadius: 14,
      borderRadiusLG: 18,
      borderRadiusSM: 10,
      borderRadiusXS: 8,
      wireframe: false,
      fontFamily: FONT_SANS,
      fontSize: 14,
      controlHeight: 36,
      boxShadow: "none",
      boxShadowSecondary: "none",
    },
    components: {
      Layout: {
        headerBg: c.toolbar,
        siderBg: c.sider,
        bodyBg: c.layout,
        headerHeight: 54,
        headerPadding: "0 20px",
      },
      Menu: {
        itemBg: "transparent",
        itemHoverBg: c.hoverBg,
        itemSelectedBg: c.selectedBg,
        itemSelectedColor: c.text,
        itemColor: c.textSecondary,
        groupTitleColor: c.textTertiary,
        itemHeight: 36,
        itemMarginInline: 10,
        itemBorderRadius: 14,
        iconSize: 16,
      },
      Card: {
        colorBgContainer: c.container,
        borderRadiusLG: 18,
      },
      Table: {
        headerBg: mode === "dark" ? c.elevated : "#f6f7f8",
        headerColor: c.textSecondary,
        rowHoverBg: c.hoverBg,
        borderColor: c.borderSubtle,
        cellPaddingBlock: 11,
      },
      Statistic: { contentFontSize: 26 },
      Tag: { borderRadiusSM: 999 },
      Segmented: {
        trackBg: c.selectedBg,
        itemSelectedBg: c.container,
      },
      Button: {
        primaryShadow: "none",
        defaultShadow: "none",
        dangerShadow: "none",
      },
      Input: {
        activeBorderColor: ACCENT,
        hoverBorderColor: mode === "dark" ? "#444a53" : "#c9ced6",
      },
      Select: {
        optionSelectedBg: c.selectedBg,
      },
    },
  };
}
