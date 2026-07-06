import {
  ApiOutlined,
  CheckOutlined,
  CloseOutlined,
  FileDoneOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  WarningFilled,
} from "@ant-design/icons";
import { App as AntApp, Button, Empty, Spin, Tag } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { approveToolCall, listPendingToolCallApprovals } from "../api/brain";
import { approveGate, listPendingGates } from "../api/orchestrator";
import { PageHeader } from "../components/ui";
import { useEventStream } from "../hooks/useEventStream";
import type { AgentToolCall, ComplianceCheck, ComplianceRisk, GateType } from "../types";

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
  warn: { color: "var(--dy-warning)", bg: "rgba(214,161,38,0.1)", label: "疑似风险" },
  block: { color: "var(--dy-error)", bg: "rgba(220,80,80,0.1)", label: "高危违规" },
};

const FORCED_GATES = new Set<GateType>([
  "script_compliance",
  "pre_publish_review",
  "large_ad_spend",
]);

export default function Approvals() {
  const { message } = AntApp.useApp();
  const qc = useQueryClient();

  const gatesQuery = useQuery({ queryKey: ["gates"], queryFn: listPendingGates });
  const toolApprovalsQuery = useQuery({
    queryKey: ["tool-call-approvals"],
    queryFn: listPendingToolCallApprovals,
  });

  useEventStream(() => {
    qc.invalidateQueries({ queryKey: ["gates"] });
    qc.invalidateQueries({ queryKey: ["tool-call-approvals"] });
  });

  const decideGateMutation = useMutation({
    mutationFn: ({ id, approved }: { id: number; approved: boolean }) =>
      approveGate(id, approved),
    onSuccess: (_data, { approved }) => {
      message.success(approved ? "质量门已通过" : "已打回，对应 Agent 会收到阻塞信号");
      qc.invalidateQueries({ queryKey: ["gates"] });
      qc.invalidateQueries({ queryKey: ["content-items"] });
    },
    onError: () => message.error("质量门处理失败，请重试"),
  });

  const decideToolMutation = useMutation({
    mutationFn: ({ id, approved }: { id: number; approved: boolean }) =>
      approveToolCall({
        toolCallId: id,
        approved,
        comment: approved ? "人工确认通过" : "人工确认打回",
      }),
    onSuccess: (_data, { approved }) => {
      message.success(approved ? "Agent 工具调用已确认" : "Agent 工具调用已打回");
      qc.invalidateQueries({ queryKey: ["tool-call-approvals"] });
      qc.invalidateQueries({ queryKey: ["brain-tasks"] });
    },
    onError: () => message.error("Agent 工具确认失败，请重试"),
  });

  const gates = gatesQuery.data ?? [];
  const toolApprovals = toolApprovalsQuery.data ?? [];
  const loading = gatesQuery.isLoading || toolApprovalsQuery.isLoading;
  const pendingCount = gates.length + toolApprovals.length;

  return (
    <div>
      <PageHeader
        title="人工审批"
        subtitle="人在关键处把关：Agent 工具确认、质量门、发布前风险控制统一进入这里。"
        extra={
          <Tag color="warning" style={{ marginInlineEnd: 0 }}>
            待处理 {pendingCount}
          </Tag>
        }
      />

      {loading ? (
        <div style={{ display: "grid", placeItems: "center", marginTop: 80 }}>
          <Spin />
        </div>
      ) : pendingCount === 0 ? (
        <Empty description="全部处理完毕，链路畅通" style={{ marginTop: 80 }} />
      ) : (
        <div style={{ display: "grid", gap: 18, maxWidth: 980 }}>
          {toolApprovals.length > 0 && (
            <section>
              <SectionTitle icon={<RobotOutlined />} title="Agent 工具确认" count={toolApprovals.length} />
              <div style={{ display: "grid", gap: 12 }}>
                {toolApprovals.map((toolCall) => (
                  <ToolApprovalCard
                    key={toolCall.id}
                    toolCall={toolCall}
                    loading={
                      decideToolMutation.isPending &&
                      decideToolMutation.variables?.id === toolCall.id
                    }
                    onApprove={() => decideToolMutation.mutate({ id: toolCall.id, approved: true })}
                    onReject={() => decideToolMutation.mutate({ id: toolCall.id, approved: false })}
                  />
                ))}
              </div>
            </section>
          )}

          {gates.length > 0 && (
            <section>
              <SectionTitle icon={<ApiOutlined />} title="质量门审批" count={gates.length} />
              <div style={{ display: "grid", gap: 12 }}>
                {gates.map((gate) => {
                  const forced = FORCED_GATES.has(gate.gate);
                  const loadingGate =
                    decideGateMutation.isPending && decideGateMutation.variables?.id === gate.id;
                  return (
                    <div
                      key={gate.id}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 16,
                        padding: "16px 18px",
                        background: "var(--dy-surface)",
                        border: "1px solid",
                        borderColor: forced ? "rgba(214,161,38,0.3)" : "var(--dy-border-subtle)",
                        borderRadius: 18,
                      }}
                    >
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 8,
                            marginBottom: 6,
                          }}
                        >
                          <Tag
                            color={forced ? "warning" : "default"}
                            icon={forced ? <WarningFilled /> : undefined}
                            style={{ marginInlineEnd: 0 }}
                          >
                            {GATE_LABEL[gate.gate]}
                          </Tag>
                          {forced && (
                            <span style={{ fontSize: 12, color: "var(--dy-warning)" }}>
                              强制人工
                            </span>
                          )}
                          <span
                            style={{ fontSize: 12, color: "var(--dy-faint)", marginLeft: "auto" }}
                          >
                            等待 {relativeTime(gate.created_at)}
                          </span>
                        </div>
                        <div style={{ fontSize: 14, fontWeight: 600, color: "var(--dy-text)" }}>
                          {gate.content_title}
                        </div>
                        <div style={{ fontSize: 12.5, color: "var(--dy-muted)", marginTop: 4 }}>
                          内容 #{gate.content_item_id}
                        </div>
                        {gate.compliance && <ComplianceBanner check={gate.compliance} />}
                      </div>
                      <ApprovalActions
                        loading={loadingGate}
                        onApprove={() =>
                          decideGateMutation.mutate({ id: gate.id, approved: true })
                        }
                        onReject={() =>
                          decideGateMutation.mutate({ id: gate.id, approved: false })
                        }
                      />
                    </div>
                  );
                })}
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  );
}

function ToolApprovalCard({
  toolCall,
  loading,
  onApprove,
  onReject,
}: {
  toolCall: AgentToolCall;
  loading: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  if (toolCall.tool_code === "publish_package_prepare" || toolCall.tool_code === "publish_readiness_check") {
    return (
      <PublishReadinessApprovalCard
        toolCall={toolCall}
        loading={loading}
        onApprove={onApprove}
        onReject={onReject}
      />
    );
  }

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 16,
        padding: "16px 18px",
        background: "var(--dy-surface)",
        border: "1px solid rgba(214,161,38,0.3)",
        borderRadius: 18,
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
          <Tag color="warning" icon={<RobotOutlined />} style={{ marginInlineEnd: 0 }}>
            {toolCall.tool_name}
          </Tag>
          <Tag style={{ marginInlineEnd: 0 }}>{toolCall.permission_mode}</Tag>
          <span style={{ fontSize: 12, color: "var(--dy-faint)", marginLeft: "auto" }}>
            任务 #{toolCall.task_id}
          </span>
        </div>
        <div style={{ fontSize: 14, fontWeight: 600, color: "var(--dy-text)" }}>
          {toolCall.meta?.agent_name ? String(toolCall.meta.agent_name) : toolCall.agent_code}
        </div>
        <div style={{ display: "grid", gap: 6, marginTop: 8, fontSize: 12.5 }}>
          <ApprovalLine label="输入" value={toolCall.input_summary || "暂无输入摘要"} />
          <ApprovalLine label="输出" value={toolCall.output_summary || "暂无输出摘要"} />
        </div>
      </div>
      <ApprovalActions loading={loading} onApprove={onApprove} onReject={onReject} />
    </div>
  );
}

function PublishReadinessApprovalCard({
  toolCall,
  loading,
  onApprove,
  onReject,
}: {
  toolCall: AgentToolCall;
  loading: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  const meta = readPublishReadinessMeta(toolCall);
  const riskColor =
    meta.risk === "block" ? "error" : meta.risk === "warn" ? "warning" : "success";

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "minmax(0, 1fr) auto",
        gap: 18,
        padding: "18px 20px",
        background: "var(--dy-surface)",
        border: "1px solid rgba(214,161,38,0.34)",
        borderRadius: 20,
      }}
    >
      <div style={{ minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <Tag color="warning" icon={<SafetyCertificateOutlined />} style={{ marginInlineEnd: 0 }}>
            发布包人工确认
          </Tag>
          <Tag color={riskColor} style={{ marginInlineEnd: 0 }}>
            {meta.risk}
          </Tag>
          <Tag style={{ marginInlineEnd: 0 }}>{contentTypeLabel(meta.contentType)}</Tag>
          {meta.accountId != null && (
            <Tag style={{ marginInlineEnd: 0 }}>账号 #{meta.accountId}</Tag>
          )}
          {meta.matrixPlanId != null && (
            <Tag color="processing" style={{ marginInlineEnd: 0 }}>
              矩阵计划 #{meta.matrixPlanId}
            </Tag>
          )}
          {meta.matrixItemId != null && (
            <Tag color="processing" style={{ marginInlineEnd: 0 }}>
              子任务 #{meta.matrixItemId}
            </Tag>
          )}
          <Tag icon={<RobotOutlined />} style={{ marginInlineEnd: 0 }}>
            {toolCall.agent_code ?? "06-operator"}
          </Tag>
          <span style={{ fontSize: 12, color: "var(--dy-faint)", marginLeft: "auto" }}>
            等待 {relativeTime(toolCall.created_at)}
          </span>
        </div>

        <div style={{ marginTop: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <FileDoneOutlined style={{ color: "var(--dy-accent)" }} />
            <strong style={{ color: "var(--dy-text)", fontSize: 15 }}>
              {meta.contentTitle}
            </strong>
          </div>
          <div
            style={{
              marginTop: 6,
              display: "flex",
              gap: 8,
              flexWrap: "wrap",
              color: "var(--dy-muted)",
              fontSize: 12.5,
            }}
          >
            <span>平台：{meta.platform}</span>
            <span>内容 #{meta.contentItemId}</span>
            <span>任务 #{toolCall.task_id}</span>
          </div>
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
            gap: 10,
            marginTop: 14,
          }}
        >
          <ApprovalMetric label="发布标题" value={meta.publishTitle} />
          <ApprovalMetric label="执行模式" value={executionModeLabel(meta.executionMode)} />
          <ApprovalMetric label="定时发布" value={meta.scheduledAt ?? "立即发布"} />
          <ApprovalMetric label="话题" value={meta.topics.length ? meta.topics.join(" / ") : "未设置"} />
          <ApprovalMetric label="可见范围" value={visibilityLabel(meta.visibility)} />
          <ApprovalMetric label="评论" value={meta.allowComment ? "允许评论" : "关闭评论"} />
          <ApprovalMetric label="封面" value={meta.coverMaterialId ? `#${meta.coverMaterialId}` : "未指定"} />
          <ApprovalMetric
            label="素材"
            value={meta.materialIds.length ? meta.materialIds.map((id) => `#${id}`).join(" / ") : "未选择"}
          />
        </div>

        <div style={{ marginTop: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--dy-text)", marginBottom: 8 }}>
            人工发布清单
          </div>
          <div style={{ display: "grid", gap: 6 }}>
            {meta.manualSteps.map((step, index) => (
              <div
                key={`${step}-${index}`}
                style={{
                  display: "grid",
                  gridTemplateColumns: "24px minmax(0, 1fr)",
                  gap: 8,
                  alignItems: "start",
                  padding: "8px 10px",
                  border: "1px solid var(--dy-border-subtle)",
                  borderRadius: 12,
                  background: "var(--dy-elevated)",
                }}
              >
                <span
                  className="dy-tabular"
                  style={{ fontSize: 12, fontWeight: 700, color: "var(--dy-accent)" }}
                >
                  {index + 1}
                </span>
                <span style={{ color: "var(--dy-text)", fontSize: 12.5, lineHeight: 1.5 }}>
                  {step}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div style={{ display: "grid", gap: 8, marginTop: 14 }}>
          {meta.findings.map((finding) => (
            <div
              key={`${finding.code}-${finding.message}`}
              style={{
                display: "grid",
                gridTemplateColumns: "70px minmax(0, 1fr)",
                gap: 10,
                alignItems: "start",
                padding: "8px 10px",
                border: "1px solid var(--dy-border-subtle)",
                borderRadius: 12,
                background: "var(--dy-elevated)",
              }}
            >
              <Tag
                color={
                  finding.level === "block"
                    ? "error"
                    : finding.level === "warn"
                      ? "warning"
                      : "success"
                }
                style={{ marginInlineEnd: 0, textAlign: "center" }}
              >
                {finding.level}
              </Tag>
              <div>
                <div style={{ fontWeight: 600, color: "var(--dy-text)", fontSize: 12.5 }}>
                  {finding.code}
                </div>
                <div style={{ color: "var(--dy-muted)", fontSize: 12.5, lineHeight: 1.5 }}>
                  {finding.message}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <ApprovalActions loading={loading} onApprove={onApprove} onReject={onReject} />
    </div>
  );
}

function ApprovalMetric({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        padding: "10px 12px",
        borderRadius: 14,
        background: "var(--dy-elevated)",
        border: "1px solid var(--dy-border-subtle)",
        minWidth: 0,
      }}
    >
      <div style={{ fontSize: 12, color: "var(--dy-faint)", marginBottom: 4 }}>{label}</div>
      <div
        style={{
          color: "var(--dy-text)",
          fontSize: 13,
          fontWeight: 600,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
        title={value}
      >
        {value}
      </div>
    </div>
  );
}

function ApprovalActions({
  loading,
  onApprove,
  onReject,
}: {
  loading: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  return (
    <div style={{ display: "flex", gap: 8, flex: "none" }}>
      <Button danger icon={<CloseOutlined />} loading={loading} onClick={onReject}>
        打回
      </Button>
      <Button type="primary" icon={<CheckOutlined />} loading={loading} onClick={onApprove}>
        通过
      </Button>
    </div>
  );
}

function SectionTitle({
  icon,
  title,
  count,
}: {
  icon: ReactNode;
  title: string;
  count: number;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "0 0 10px" }}>
      <span style={{ color: "var(--dy-text)" }}>{icon}</span>
      <strong style={{ color: "var(--dy-text)", fontSize: 15 }}>{title}</strong>
      <Tag style={{ marginInlineEnd: 0 }}>{count}</Tag>
    </div>
  );
}

function ApprovalLine({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "40px minmax(0, 1fr)", gap: 8 }}>
      <span style={{ color: "var(--dy-faint)" }}>{label}</span>
      <span style={{ color: "var(--dy-text)", lineHeight: 1.45 }}>{value}</span>
    </div>
  );
}

function ComplianceBanner({ check }: { check: ComplianceCheck }) {
  const meta = RISK_META[check.risk];
  return (
    <div
      style={{
        marginTop: 10,
        padding: "8px 10px",
        borderRadius: 14,
        background: meta.bg,
        border: `1px solid ${meta.color}33`,
      }}
    >
      <div style={{ fontSize: 12.5, fontWeight: 600, color: meta.color }}>
        {meta.label} · {check.summary}
      </div>
      {check.findings && check.findings.length > 0 && (
        <div style={{ marginTop: 6, display: "flex", gap: 6, flexWrap: "wrap" }}>
          {check.findings.map((finding, index) => (
            <Tag
              key={`${finding.word}-${index}`}
              color={finding.level === "block" ? "error" : "warning"}
              style={{ marginInlineEnd: 0, fontSize: 11 }}
            >
              {finding.word}（{finding.category}）
            </Tag>
          ))}
        </div>
      )}
    </div>
  );
}

type PublishFinding = {
  level: "pass" | "warn" | "block";
  code: string;
  message: string;
};

function readPublishReadinessMeta(toolCall: AgentToolCall): {
  contentItemId: string;
  contentTitle: string;
  platform: string;
  publishTitle: string;
  scheduledAt: string | null;
  topics: string[];
  materialIds: number[];
  coverMaterialId: number | null;
  visibility: "public" | "friends" | "private";
  allowComment: boolean;
  risk: "pass" | "warn" | "block";
  findings: PublishFinding[];
  accountId: number | null;
  matrixPlanId: number | null;
  matrixItemId: number | null;
  contentType: "video" | "image_text";
  executionMode: "official_api" | "manual_checklist" | "browser_runner_disabled";
  manualSteps: string[];
} {
  const meta = toolCall.meta ?? {};
  const rawPackage =
    meta.publish_package && typeof meta.publish_package === "object"
      ? (meta.publish_package as Record<string, unknown>)
      : {};
  const rawRisk = typeof meta.risk === "string" ? meta.risk : "warn";
  const risk = rawRisk === "pass" || rawRisk === "block" ? rawRisk : "warn";
  const rawTopics = Array.isArray(rawPackage.topics) ? rawPackage.topics : meta.topics;
  const topics = Array.isArray(rawTopics)
    ? rawTopics.filter((topic): topic is string => typeof topic === "string")
    : [];
  const rawMaterialIds = Array.isArray(rawPackage.material_ids)
    ? rawPackage.material_ids
    : meta.material_ids;
  const materialIds = Array.isArray(rawMaterialIds)
    ? rawMaterialIds.filter((id): id is number => typeof id === "number")
    : [];
  const rawVisibility =
    typeof rawPackage.visibility === "string"
      ? rawPackage.visibility
      : typeof meta.visibility === "string"
        ? meta.visibility
        : "public";
  const visibility =
    rawVisibility === "friends" || rawVisibility === "private" ? rawVisibility : "public";
  const coverMaterialId =
    typeof rawPackage.cover_material_id === "number"
      ? rawPackage.cover_material_id
      : typeof meta.cover_material_id === "number"
        ? meta.cover_material_id
        : null;
  const allowComment =
    typeof rawPackage.allow_comment === "boolean"
      ? rawPackage.allow_comment
      : typeof meta.allow_comment === "boolean"
        ? meta.allow_comment
        : true;
  const accountId =
    typeof rawPackage.account_id === "number"
      ? rawPackage.account_id
      : typeof meta.account_id === "number"
        ? meta.account_id
        : null;
  const matrixPlanId = typeof meta.matrix_plan_id === "number" ? meta.matrix_plan_id : null;
  const matrixItemId = typeof meta.matrix_item_id === "number" ? meta.matrix_item_id : null;
  const rawContentType =
    typeof rawPackage.content_type === "string" ? rawPackage.content_type : "video";
  const contentType = rawContentType === "image_text" ? rawContentType : "video";
  const rawExecutionMode =
    typeof rawPackage.execution_mode === "string"
      ? rawPackage.execution_mode
      : "manual_checklist";
  const executionMode =
    rawExecutionMode === "official_api" || rawExecutionMode === "browser_runner_disabled"
      ? rawExecutionMode
      : "manual_checklist";
  const manualSteps = Array.isArray(rawPackage.manual_steps)
    ? rawPackage.manual_steps.filter((step): step is string => typeof step === "string")
    : [];
  const findings = Array.isArray(meta.findings)
    ? meta.findings
        .map((item): PublishFinding | null => {
          if (!item || typeof item !== "object") return null;
          const record = item as Record<string, unknown>;
          const level = record.level;
          if (level !== "pass" && level !== "warn" && level !== "block") return null;
          return {
            level,
            code: typeof record.code === "string" ? record.code : "unknown",
            message: typeof record.message === "string" ? record.message : "",
          };
        })
        .filter((item): item is PublishFinding => item != null)
    : [];

  return {
    contentItemId:
      typeof meta.content_item_id === "number" ? String(meta.content_item_id) : "-",
    contentTitle:
      typeof meta.content_title === "string" ? meta.content_title : toolCall.tool_name,
    platform: typeof meta.platform === "string" ? meta.platform : "douyin",
    publishTitle:
      typeof rawPackage.title === "string"
        ? rawPackage.title
        : typeof meta.publish_title === "string"
          ? meta.publish_title
          : toolCall.input_summary,
    scheduledAt: typeof meta.scheduled_at === "string" ? meta.scheduled_at : null,
    topics,
    materialIds,
    coverMaterialId,
    visibility,
    allowComment,
    risk,
    findings,
    accountId,
    matrixPlanId,
    matrixItemId,
    contentType,
    executionMode,
    manualSteps,
  };
}

function contentTypeLabel(value: "video" | "image_text"): string {
  return {
    video: "视频",
    image_text: "图文",
  }[value];
}

function executionModeLabel(
  value: "official_api" | "manual_checklist" | "browser_runner_disabled",
): string {
  return {
    official_api: "官方接口",
    manual_checklist: "人工发布",
    browser_runner_disabled: "浏览器自动化已关闭",
  }[value];
}

function visibilityLabel(value: "public" | "friends" | "private"): string {
  return {
    public: "公开",
    friends: "朋友可见",
    private: "私密",
  }[value];
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时`;
  return `${Math.floor(hours / 24)} 天`;
}
