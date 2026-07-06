import {
  CheckCircleFilled,
  ClockCircleFilled,
  LoadingOutlined,
  StopFilled,
} from "@ant-design/icons";
import { Tag, Typography } from "antd";
import type { ReactNode } from "react";

import type { Platform } from "../types";

export type StatusBadgeStatus = "running" | "done" | "review" | "blocked";

const PLATFORM_LABEL: Record<Platform, string> = {
  douyin: "抖音",
  xiaohongshu: "小红书",
  shipinhao: "视频号",
};

const STATUS_META: Record<
  StatusBadgeStatus,
  { label: string; color: string; icon: ReactNode }
> = {
  running: { label: "进行中", color: "var(--dy-info)", icon: <LoadingOutlined /> },
  done: { label: "已完成", color: "var(--dy-success)", icon: <CheckCircleFilled /> },
  review: { label: "待审核", color: "var(--dy-warning)", icon: <ClockCircleFilled /> },
  blocked: { label: "已阻塞", color: "var(--dy-error)", icon: <StopFilled /> },
};

/** 状态标记：颜色 + 图标 + 文字三重冗余（色盲安全）。 */
export function StatusBadge({ status }: { status: StatusBadgeStatus }) {
  const m = STATUS_META[status];
  return (
    <span
      className="dy-status-badge"
      style={{ color: m.color }}
    >
      <span className="dy-status-badge-icon">{m.icon}</span>
      {m.label}
    </span>
  );
}

const PLATFORM_COLOR: Record<Platform, string> = {
  douyin: "default",
  xiaohongshu: "error",
  shipinhao: "success",
};

export function PlatformTag({ platform }: { platform: Platform }) {
  return (
    <Tag color={PLATFORM_COLOR[platform]} className="dy-platform-tag">
      {PLATFORM_LABEL[platform]}
    </Tag>
  );
}

/** 页面标题区：标题 + 副标题 + 右侧操作。 */
export function PageHeader({
  title,
  subtitle,
  extra,
}: {
  title: string;
  subtitle?: string;
  extra?: ReactNode;
}) {
  return (
    <div className="dy-page-header">
      <div className="dy-page-header-copy">
        <Typography.Title level={3} className="dy-page-title">
          {title}
        </Typography.Title>
        {subtitle && (
          <Typography.Text className="dy-page-subtitle">
            {subtitle}
          </Typography.Text>
        )}
      </div>
      {extra && <div className="dy-page-actions">{extra}</div>}
    </div>
  );
}

/** 图表容器：统一标题 + 边框 + 内距，承载 ECharts。 */
export function Panel({
  title,
  extra,
  children,
  style,
  className,
}: {
  title?: string;
  extra?: ReactNode;
  children: ReactNode;
  style?: React.CSSProperties;
  className?: string;
}) {
  return (
    <section
      className={["dy-panel", className].filter(Boolean).join(" ")}
      style={style}
    >
      {(title || extra) && (
        <header className="dy-panel-header">
          <span className="dy-panel-title">
            {title}
          </span>
          {extra}
        </header>
      )}
      {children}
    </section>
  );
}
