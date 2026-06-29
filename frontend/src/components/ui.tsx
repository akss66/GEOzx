import {
  CheckCircleFilled,
  ClockCircleFilled,
  LoadingOutlined,
  StopFilled,
} from "@ant-design/icons";
import { Tag, Typography } from "antd";
import type { ReactNode } from "react";

import { PLATFORM_LABEL, type CardStatus, type Platform } from "../mock/data";

const STATUS_META: Record<
  CardStatus,
  { label: string; color: string; icon: ReactNode }
> = {
  running: { label: "进行中", color: "var(--dy-info)", icon: <LoadingOutlined /> },
  done: { label: "已完成", color: "var(--dy-success)", icon: <CheckCircleFilled /> },
  review: { label: "待审核", color: "var(--dy-warning)", icon: <ClockCircleFilled /> },
  blocked: { label: "已阻塞", color: "var(--dy-error)", icon: <StopFilled /> },
};

/** 状态标记：颜色 + 图标 + 文字三重冗余（色盲安全）。 */
export function StatusBadge({ status }: { status: CardStatus }) {
  const m = STATUS_META[status];
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        color: m.color,
        fontSize: 12,
        fontWeight: 500,
      }}
    >
      <span style={{ fontSize: 11, display: "inline-flex" }}>{m.icon}</span>
      {m.label}
    </span>
  );
}

const PLATFORM_COLOR: Record<Platform, string> = {
  douyin: "blue",
  xiaohongshu: "magenta",
  shipinhao: "green",
};

export function PlatformTag({ platform }: { platform: Platform }) {
  return (
    <Tag color={PLATFORM_COLOR[platform]} style={{ marginInlineEnd: 0 }}>
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
    <div
      style={{
        display: "flex",
        alignItems: "flex-end",
        justifyContent: "space-between",
        gap: 16,
        marginBottom: 20,
      }}
    >
      <div>
        <Typography.Title level={4} style={{ margin: 0, fontWeight: 600 }}>
          {title}
        </Typography.Title>
        {subtitle && (
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            {subtitle}
          </Typography.Text>
        )}
      </div>
      {extra}
    </div>
  );
}

/** 图表容器：统一标题 + 边框 + 内距，承载 ECharts。 */
export function Panel({
  title,
  extra,
  children,
  style,
}: {
  title?: string;
  extra?: ReactNode;
  children: ReactNode;
  style?: React.CSSProperties;
}) {
  return (
    <section
      style={{
        background: "var(--dy-surface)",
        border: "1px solid var(--dy-border-subtle)",
        borderRadius: 12,
        padding: 16,
        ...style,
      }}
    >
      {(title || extra) && (
        <header
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 12,
          }}
        >
          <span style={{ fontSize: 13, fontWeight: 600, color: "var(--dy-text)" }}>
            {title}
          </span>
          {extra}
        </header>
      )}
      {children}
    </section>
  );
}
