import type { ThemeConfig } from "antd";

// Kept for chart compatibility while the remaining report pages are migrated.
export type Mode = "light" | "dark";

export const DESIGN_TOKENS = {
  brandRed: "#C9161D",
  brandRedHover: "#AE1218",
  brandRedActive: "#941016",
  brandFrame: "#EEE8DF",
  brandFrameStrong: "#E4DCD0",
  workCanvas: "#F7F7F4",
  surface: "#FFFFFF",
  surfaceSoft: "#F0F0ED",
  ink: "#171614",
  inkSoft: "#4A4640",
  muted: "#666159",
  faint: "#827C73",
  line: "#DDD8CF",
  lineSubtle: "#EBE7DF",
  success: "#2B8152",
  warning: "#9A6300",
  error: "#B92B36",
  info: "#526774",
} as const;

export const FONT_SANS =
  '"Geist Variable", "Noto Sans SC Variable", "PingFang SC", "Microsoft YaHei UI", "Segoe UI", sans-serif';

export const ACCENT = DESIGN_TOKENS.brandRed;
export const ACCENT_HOVER = DESIGN_TOKENS.brandRedHover;
export const ACCENT_ACTIVE = DESIGN_TOKENS.brandRedActive;

export const SEMANTIC = {
  success: DESIGN_TOKENS.success,
  warning: DESIGN_TOKENS.warning,
  error: DESIGN_TOKENS.error,
  info: DESIGN_TOKENS.info,
};

export const CHART_COLORS = [
  DESIGN_TOKENS.ink,
  DESIGN_TOKENS.brandRed,
  DESIGN_TOKENS.info,
  "#7F8D86",
  DESIGN_TOKENS.success,
  DESIGN_TOKENS.warning,
  "#A6A29B",
  "#C9C6BF",
];

export const PLATFORM_COLORS: Record<string, string> = {
  douyin: DESIGN_TOKENS.ink,
  xiaohongshu: DESIGN_TOKENS.brandRed,
  shipinhao: DESIGN_TOKENS.success,
};

export function buildTheme(): ThemeConfig {
  const c = DESIGN_TOKENS;

  return {
    token: {
      colorPrimary: c.brandRed,
      colorLink: c.ink,
      colorInfo: c.info,
      colorSuccess: c.success,
      colorWarning: c.warning,
      colorError: c.error,
      colorBgLayout: c.workCanvas,
      colorBgContainer: c.surface,
      colorBgElevated: c.surface,
      colorBorder: c.line,
      colorBorderSecondary: c.lineSubtle,
      colorText: c.ink,
      colorTextSecondary: c.inkSoft,
      colorTextTertiary: c.muted,
      borderRadius: 8,
      borderRadiusLG: 10,
      borderRadiusSM: 8,
      borderRadiusXS: 6,
      wireframe: false,
      fontFamily: FONT_SANS,
      fontSize: 14,
      controlHeight: 36,
      boxShadow: "none",
      boxShadowSecondary: "0 12px 34px rgba(42, 35, 27, 0.10)",
    },
    components: {
      Layout: {
        headerBg: c.workCanvas,
        siderBg: c.brandFrame,
        bodyBg: c.workCanvas,
        headerHeight: 62,
        headerPadding: "0 22px",
      },
      Menu: {
        itemBg: "transparent",
        itemHoverBg: "rgba(255, 255, 255, 0.42)",
        itemSelectedBg: "rgba(255, 255, 255, 0.62)",
        itemSelectedColor: c.ink,
        itemColor: c.inkSoft,
        groupTitleColor: c.muted,
        itemHeight: 37,
        itemMarginInline: 10,
        itemBorderRadius: 8,
        iconSize: 16,
      },
      Card: { colorBgContainer: c.surface, borderRadiusLG: 10 },
      Table: {
        headerBg: c.workCanvas,
        headerColor: c.inkSoft,
        rowHoverBg: c.surfaceSoft,
        borderColor: c.lineSubtle,
        cellPaddingBlock: 11,
      },
      Statistic: { contentFontSize: 26 },
      Tag: { borderRadiusSM: 999 },
      Segmented: { trackBg: c.surfaceSoft, itemSelectedBg: c.surface },
      Button: {
        colorPrimary: c.ink,
        colorPrimaryHover: c.inkSoft,
        colorPrimaryActive: "#0F0E0D",
        primaryColor: c.surface,
        primaryShadow: "none",
        defaultShadow: "none",
        dangerShadow: "none",
      },
      Input: { activeBorderColor: c.ink, hoverBorderColor: c.faint },
      Select: { optionSelectedBg: c.surfaceSoft },
    },
  };
}
