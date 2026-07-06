import {
  ApiOutlined,
  AuditOutlined,
  CloudSyncOutlined,
  KeyOutlined,
  WarningFilled,
} from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Empty, Segmented, Spin, Tag } from "antd";
import { useMemo, useState } from "react";

import { listRiskQueue } from "../api/risks";
import { PageHeader } from "../components/ui";
import type { RiskCategory, RiskQueueItem, RiskSeverity } from "../types";

type RiskFilter = "all" | RiskCategory;

const CATEGORY_META: Record<
  RiskCategory,
  { label: string; icon: React.ReactNode; color: string }
> = {
  quality_gate: { label: "质量门", icon: <AuditOutlined />, color: "gold" },
  account_auth: { label: "授权", icon: <KeyOutlined />, color: "red" },
  model_failure: { label: "模型", icon: <ApiOutlined />, color: "volcano" },
  data_sync: { label: "回流", icon: <CloudSyncOutlined />, color: "orange" },
};

const SEVERITY_META: Record<RiskSeverity, { label: string; color: string }> = {
  high: { label: "高", color: "red" },
  medium: { label: "中", color: "orange" },
  low: { label: "低", color: "blue" },
};

export default function Risks() {
  const [filter, setFilter] = useState<RiskFilter>("all");
  const riskQuery = useQuery({ queryKey: ["risk-queue"], queryFn: listRiskQueue });
  const risks = useMemo(() => riskQuery.data ?? [], [riskQuery.data]);
  const visibleRisks = useMemo(
    () => (filter === "all" ? risks : risks.filter((risk) => risk.category === filter)),
    [filter, risks],
  );
  const highCount = risks.filter((risk) => risk.severity === "high").length;

  return (
    <div>
      <PageHeader
        title="风险队列"
        subtitle="质量门、账号授权、模型失败、平台回流失败的统一处理入口"
        extra={
          <Tag color={highCount > 0 ? "red" : "green"} style={{ marginInlineEnd: 0 }}>
            高风险 {highCount}
          </Tag>
        }
      />

      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginBottom: 14 }}>
        <Segmented
          value={filter}
          onChange={(value) => setFilter(value as RiskFilter)}
          options={[
            { label: "全部", value: "all" },
            { label: "质量门", value: "quality_gate" },
            { label: "授权", value: "account_auth" },
            { label: "模型", value: "model_failure" },
            { label: "回流", value: "data_sync" },
          ]}
        />
        <Tag style={{ marginInlineEnd: 0 }}>共 {visibleRisks.length} 条</Tag>
      </div>

      {riskQuery.isLoading ? (
        <div style={{ display: "grid", placeItems: "center", marginTop: 80 }}>
          <Spin />
        </div>
      ) : visibleRisks.length === 0 ? (
        <Empty description="当前筛选下暂无风险" style={{ marginTop: 80 }} />
      ) : (
        <div style={{ display: "grid", gap: 10, maxWidth: 960 }}>
          {visibleRisks.map((risk) => (
            <RiskRow key={risk.id} risk={risk} />
          ))}
        </div>
      )}
    </div>
  );
}

function RiskRow({ risk }: { risk: RiskQueueItem }) {
  const category = CATEGORY_META[risk.category];
  const severity = SEVERITY_META[risk.severity];
  return (
    <article
      style={{
        display: "grid",
        gridTemplateColumns: "minmax(0,1fr) 120px",
        gap: 14,
        alignItems: "center",
        padding: "14px 16px",
        borderRadius: 10,
        border: "1px solid var(--dy-border-subtle)",
        background: "var(--dy-surface)",
      }}
    >
      <div style={{ minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <Tag color={category.color} icon={category.icon} style={{ marginInlineEnd: 0 }}>
            {category.label}
          </Tag>
          <Tag color={severity.color} icon={<WarningFilled />} style={{ marginInlineEnd: 0 }}>
            {severity.label}风险
          </Tag>
          <span style={{ fontSize: 12, color: "var(--dy-faint)" }}>{relativeTime(risk.created_at)}</span>
        </div>
        <div style={{ fontSize: 14, fontWeight: 600, color: "var(--dy-text)", marginTop: 8 }}>
          {risk.title}
        </div>
        <div style={{ fontSize: 12.5, color: "var(--dy-muted)", marginTop: 4, lineHeight: 1.5 }}>
          {risk.description}
        </div>
      </div>
      <div style={{ minWidth: 0, textAlign: "right" }}>
        <div style={{ fontSize: 12, color: "var(--dy-faint)" }}>来源</div>
        <div
          className="dy-tabular"
          style={{
            fontSize: 12.5,
            color: "var(--dy-text)",
            marginTop: 4,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={risk.source}
        >
          {risk.source}
        </div>
        <div style={{ fontSize: 12, color: "var(--dy-faint)", marginTop: 8 }}>
          {risk.status}
        </div>
      </div>
    </article>
  );
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const min = Math.floor(diff / 60000);
  if (min < 1) return "刚刚";
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  return `${Math.floor(hr / 24)} 天前`;
}
