import {
  CheckCircleFilled,
  ClockCircleFilled,
  CloseCircleOutlined,
  InboxOutlined,
  LoadingOutlined,
  SafetyCertificateOutlined,
  StopFilled,
} from "@ant-design/icons";
import { Button, Tag, Typography } from "antd";
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

export type OperationalStateKind = "loading" | "empty" | "error" | "blocked";

const OPERATIONAL_STATE_ICON: Record<OperationalStateKind, ReactNode> = {
  loading: <LoadingOutlined spin />,
  empty: <InboxOutlined />,
  error: <CloseCircleOutlined />,
  blocked: <SafetyCertificateOutlined />,
};

/**
 * 统一的数据状态面板。业务说明始终可见，诊断信息默认折叠，避免技术细节污染主界面。
 */
export function OperationalState({
  kind,
  title,
  description,
  actionLabel,
  onAction,
  actionLoading = false,
  diagnostic,
  compact = false,
}: {
  kind: OperationalStateKind;
  title: string;
  description: string;
  actionLabel?: string;
  onAction?: () => void;
  actionLoading?: boolean;
  diagnostic?: string | null;
  compact?: boolean;
}) {
  return (
    <section
      className={`tz-operational-state is-${kind}${compact ? " is-compact" : ""}`}
      role={kind === "error" ? "alert" : "status"}
      aria-busy={kind === "loading" || undefined}
    >
      <span className="tz-operational-state__icon" aria-hidden="true">
        {OPERATIONAL_STATE_ICON[kind]}
      </span>
      <div className="tz-operational-state__copy">
        <h2>{title}</h2>
        <p>{description}</p>
        {diagnostic ? (
          <details>
            <summary>查看诊断信息</summary>
            <code>{diagnostic}</code>
          </details>
        ) : null}
      </div>
      {actionLabel && onAction ? (
        <Button loading={actionLoading} onClick={onAction}>
          {actionLabel}
        </Button>
      ) : null}
    </section>
  );
}
