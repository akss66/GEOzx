import { CheckOutlined, CloseOutlined, WarningFilled } from "@ant-design/icons";
import { App as AntApp, Button, Empty, Spin, Tag } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { approveGate, listPendingGates } from "../api/orchestrator";
import { PageHeader } from "../components/ui";
import { useEventStream } from "../hooks/useEventStream";
import type { ComplianceCheck, ComplianceRisk, GateType } from "../types";

const GATE_LABEL: Record<GateType, string> = {
  positioning_review: "定位审核",
  topic_review: "选题审核",
  script_compliance: "脚本合规",
  final_video_review: "成片审核",
  pre_publish_review: "发布前审核",
  large_ad_spend: "大额投放",
};

const RISK_META: Record<ComplianceRisk, { color: string; bg: string; label: string }> = {
  pass: { color: "var(--dy-success)", bg: "rgba(48,164,108,0.1)", label: "合规预检通过" },
  warn: { color: "var(--dy-warning)", bg: "rgba(214,161,38,0.1)", label: "合规预检：疑似风险" },
  block: { color: "var(--dy-error)", bg: "rgba(220,80,80,0.1)", label: "合规预检：高危违禁" },
};

/** 合规预检横幅：自动检测结果，供人工审批参考（不替代人工决策）。 */
function ComplianceBanner({ check }: { check: ComplianceCheck }) {
  const meta = RISK_META[check.risk];
  return (
    <div
      style={{
        marginTop: 10,
        padding: "8px 10px",
        borderRadius: 8,
        background: meta.bg,
        border: `1px solid ${meta.color}33`,
      }}
    >
      <div style={{ fontSize: 12.5, fontWeight: 500, color: meta.color }}>
        {meta.label} · {check.summary}
      </div>
      {check.findings && check.findings.length > 0 && (
        <div style={{ marginTop: 6, display: "flex", gap: 6, flexWrap: "wrap" }}>
          {check.findings.map((f, i) => (
            <Tag
              key={`${f.word}-${i}`}
              color={f.level === "block" ? "error" : "warning"}
              style={{ marginInlineEnd: 0, fontSize: 11 }}
            >
              {f.word}（{f.category}）
            </Tag>
          ))}
        </div>
      )}
    </div>
  );
}

// 强制人工的质量门（SPEC 5.5：脚本合规 / 发布前 / 大额投放）。
const FORCED_GATES = new Set<GateType>([
  "script_compliance",
  "pre_publish_review",
  "large_ad_spend",
]);

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const min = Math.floor(diff / 60000);
  if (min < 1) return "刚刚";
  if (min < 60) return `${min} 分钟`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时`;
  return `${Math.floor(hr / 24)} 天`;
}

export default function Approvals() {
  const { message } = AntApp.useApp();
  const qc = useQueryClient();

  const gatesQuery = useQuery({ queryKey: ["gates"], queryFn: listPendingGates });

  // 编排事件（门待审/审批结果）到达即刷新。
  useEventStream(() => qc.invalidateQueries({ queryKey: ["gates"] }));

  const decideMutation = useMutation({
    mutationFn: ({ id, approved }: { id: number; approved: boolean }) =>
      approveGate(id, approved),
    onSuccess: (_data, { approved }) => {
      message.success(approved ? "已通过，链路继续流转" : "已打回，已通知对应 Agent");
      qc.invalidateQueries({ queryKey: ["gates"] });
      qc.invalidateQueries({ queryKey: ["content-items"] });
    },
    onError: () => message.error("操作失败，请重试"),
  });

  const gates = gatesQuery.data ?? [];

  return (
    <div>
      <PageHeader
        title="质量门审批"
        subtitle="人在关键处把关 · 脚本合规 / 发布前 / 大额投放 强制人工"
        extra={
          <Tag color="warning" style={{ marginInlineEnd: 0 }}>
            待处理 {gates.length}
          </Tag>
        }
      />

      {gatesQuery.isLoading ? (
        <div style={{ display: "grid", placeItems: "center", marginTop: 80 }}>
          <Spin />
        </div>
      ) : gates.length === 0 ? (
        <Empty description="全部处理完毕 · 链路畅通" style={{ marginTop: 80 }} />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 880 }}>
          {gates.map((g) => {
            const forced = FORCED_GATES.has(g.gate);
            const pending =
              decideMutation.isPending && decideMutation.variables?.id === g.id;
            return (
              <div
                key={g.id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 16,
                  padding: "16px 18px",
                  background: "var(--dy-surface)",
                  border: "1px solid",
                  borderColor: forced ? "rgba(214,161,38,0.3)" : "var(--dy-border-subtle)",
                  borderRadius: 12,
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                    <Tag
                      color={forced ? "warning" : "default"}
                      icon={forced ? <WarningFilled /> : undefined}
                      style={{ marginInlineEnd: 0 }}
                    >
                      {GATE_LABEL[g.gate]}
                    </Tag>
                    {forced && (
                      <span style={{ fontSize: 12, color: "var(--dy-warning)" }}>强制人工</span>
                    )}
                    <span style={{ fontSize: 12, color: "var(--dy-faint)", marginLeft: "auto" }}>
                      等待 {relativeTime(g.created_at)}
                    </span>
                  </div>
                  <div style={{ fontSize: 14, fontWeight: 500, color: "var(--dy-text)" }}>
                    {g.content_title}
                  </div>
                  <div style={{ fontSize: 12.5, color: "var(--dy-muted)", marginTop: 4 }}>
                    内容 #{g.content_item_id}
                  </div>
                  {g.compliance && <ComplianceBanner check={g.compliance} />}
                </div>
                <div style={{ display: "flex", gap: 8, flex: "none" }}>
                  <Button
                    danger
                    icon={<CloseOutlined />}
                    loading={pending}
                    onClick={() => decideMutation.mutate({ id: g.id, approved: false })}
                  >
                    打回
                  </Button>
                  <Button
                    type="primary"
                    icon={<CheckOutlined />}
                    loading={pending}
                    onClick={() => decideMutation.mutate({ id: g.id, approved: true })}
                  >
                    通过
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
