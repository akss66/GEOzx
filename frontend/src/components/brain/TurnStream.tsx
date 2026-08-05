import { Button, Input } from "antd";
import { useEffect, useId, useState } from "react";

import type {
  Artifact,
  ConversationApproval,
  ConversationThread,
  ConversationTurn,
  TurnInterrupt,
  TurnProjection,
} from "../../types";
import type { ArtifactAction } from "./ArtifactCard";
import { turnReactKey } from "./conversationTurnProjection";
import { TurnArtifact } from "./TurnArtifact";
import { WorkTurnCard } from "./WorkTurnCard";
import { projectWorkTurn } from "./workTurnProjection";

export function TurnStream({
  thread,
  approvingToolCallId = null,
  approvalComment = "",
  onApprovalCommentChange,
  onApprove,
  resolvingInterruptId = null,
  onResolveInterrupt,
  onArtifactAction,
  revisingArtifactId = null,
  actionPendingArtifactId = null,
  artifactRefreshKey = 0,
  revisionArtifacts = {},
  sourceArtifactOverrides = {},
  onRestartTurn,
}: {
  thread: ConversationThread;
  approvingToolCallId?: number | null;
  approvalComment?: string;
  onApprovalCommentChange?: (value: string) => void;
  onApprove?: (approval: ConversationApproval, approved: boolean, comment?: string) => void;
  resolvingInterruptId?: number | null;
  onResolveInterrupt?: (interrupt: TurnInterrupt, resolution: Record<string, unknown>) => void;
  onArtifactAction?: (action: ArtifactAction) => void;
  revisingArtifactId?: number | null;
  actionPendingArtifactId?: number | null;
  artifactRefreshKey?: number;
  revisionArtifacts?: Record<number, Artifact[]>;
  sourceArtifactOverrides?: Record<number, Artifact>;
  onRestartTurn?: (turn: ConversationTurn) => void;
}) {
  return (
    <div className="tz-turn-stream" aria-label="Conversation turns">
      {thread.turns.map((turn) => {
        const view = projectWorkTurn(turn);
        return (
          <WorkTurnCard
            key={turnReactKey({
              threadId: thread.id,
              turnId: turn.id,
              clientMessageId: turn.client_message_id ?? `turn-${turn.id ?? "pending"}`,
            })}
            view={view}
            sourceStatus={turn.status}
            evidenceSummary={businessEvidence(turn)}
            technicalLog={technicalLog(turn)}
            deliverables={renderDeliverables({
              turn,
              thread,
              onArtifactAction,
              revisingArtifactId,
              actionPendingArtifactId,
              artifactRefreshKey,
              revisionArtifacts,
              sourceArtifactOverrides,
            })}
            businessActions={renderBusinessActions({
              turn,
              recoveryStatus: view.status,
              approvingToolCallId,
              approvalComment,
              onApprovalCommentChange,
              onApprove,
              resolvingInterruptId,
              onResolveInterrupt,
              onRestartTurn,
            })}
          />
        );
      })}
    </div>
  );
}

function renderDeliverables({
  turn,
  thread,
  onArtifactAction,
  revisingArtifactId,
  actionPendingArtifactId,
  artifactRefreshKey,
  revisionArtifacts,
  sourceArtifactOverrides,
}: {
  turn: ConversationTurn;
  thread: ConversationThread;
  onArtifactAction?: (action: ArtifactAction) => void;
  revisingArtifactId: number | null;
  actionPendingArtifactId: number | null;
  artifactRefreshKey: number;
  revisionArtifacts: Record<number, Artifact[]>;
  sourceArtifactOverrides: Record<number, Artifact>;
}) {
  const sourceTurnId = turn.id;
  if (sourceTurnId == null) return null;
  return projectionsForTurn(turn).flatMap((projection) => {
    if (projection.type !== "artifact") return [];
    const key = `artifact-${projection.artifact_id}`;
    return [
      <TurnArtifact
        key={key}
        className="tz-turn-projection"
        data-testid={`projection-${key}`}
        data-projection-key={key}
        artifactId={projection.artifact_id}
        accountId={projection.account_id}
        threadAccountId={thread.account_id}
        threadId={thread.id}
        sourceTurnId={sourceTurnId}
        onAction={onArtifactAction}
        revisingArtifactId={revisingArtifactId}
        actionPendingArtifactId={actionPendingArtifactId}
        revisionArtifacts={revisionArtifacts[projection.artifact_id]}
        sourceArtifactOverride={sourceArtifactOverrides[projection.artifact_id]}
        refreshKey={artifactRefreshKey}
      />,
    ];
  });
}

function renderBusinessActions({
  turn,
  recoveryStatus,
  approvingToolCallId,
  approvalComment,
  onApprovalCommentChange,
  onApprove,
  resolvingInterruptId,
  onResolveInterrupt,
  onRestartTurn,
}: {
  turn: ConversationTurn;
  recoveryStatus: string;
  approvingToolCallId: number | null;
  approvalComment: string;
  onApprovalCommentChange?: (value: string) => void;
  onApprove?: (approval: ConversationApproval, approved: boolean, comment?: string) => void;
  resolvingInterruptId: number | null;
  onResolveInterrupt?: (interrupt: TurnInterrupt, resolution: Record<string, unknown>) => void;
  onRestartTurn?: (turn: ConversationTurn) => void;
}) {
  const interrupt = turn.pending_interrupt?.status === "pending"
    ? turn.pending_interrupt
    : null;
  const projections = projectionsForTurn(turn);
  const approval = projections.find((projection) => projection.type === "approval");
  const blocked = projections.find((projection) => projection.type === "execution_blocked");
  const recoveryLabel = recoveryActionLabel(recoveryStatus);

  return (
    <>
      {interrupt ? (
        <InterruptAction
          interrupt={interrupt}
          resolving={resolvingInterruptId === interrupt.id}
          onResolve={onResolveInterrupt}
        />
      ) : approval?.type === "approval" ? (
        <ApprovalAction
          approval={approval.approval}
          approving={approvingToolCallId === approval.approval.id}
          approvalComment={approvalComment}
          onApprovalCommentChange={onApprovalCommentChange}
          onApprove={onApprove}
        />
      ) : null}
      {blocked?.type === "execution_blocked" ? (
        <BlockedRecoveryAction recoveryAction={blocked.recovery_action} />
      ) : null}
      {recoveryLabel && onRestartTurn ? (
        <section className="tz-work-turn__recovery" aria-label="恢复操作">
          <Button type="text" onClick={() => onRestartTurn(turn)}>{recoveryLabel}</Button>
        </section>
      ) : null}
    </>
  );
}

function recoveryActionLabel(status: string) {
  if (status === "failed") return "重新开始本轮";
  if (status === "cancelled") return "重新开始本轮";
  return null;
}

function BlockedRecoveryAction({ recoveryAction }: { recoveryAction?: string }) {
  const [expanded, setExpanded] = useState(false);
  const guidanceId = useId();
  const guidance = recoveryAction?.trim() || "请检查当前账号状态、权限和所需数据后再继续。";

  return (
    <section className="tz-work-turn__recovery" aria-label="恢复指引">
      <Button
        type="text"
        aria-controls={guidanceId}
        aria-expanded={expanded}
        onClick={() => setExpanded((current) => !current)}
      >
        查看如何继续
      </Button>
      {expanded ? <p id={guidanceId}>{guidance}</p> : null}
    </section>
  );
}

function InterruptAction({
  interrupt,
  resolving,
  onResolve,
}: {
  interrupt: TurnInterrupt;
  resolving: boolean;
  onResolve?: (interrupt: TurnInterrupt, resolution: Record<string, unknown>) => void;
}) {
  const [answer, setAnswer] = useState("");
  useEffect(() => setAnswer(""), [interrupt.id]);
  const actionLabel = interrupt.action_label
    ?? (interrupt.kind === "approval" ? "允许" : "继续");

  if (!onResolve) return <p>{interrupt.public_message}</p>;
  if (interrupt.kind === "clarification") {
    return (
      <section aria-label="Input required">
        <p>{interrupt.public_message}</p>
        <Input.TextArea
          aria-label="Your answer"
          value={answer}
          maxLength={2000}
          autoSize={{ minRows: 2, maxRows: 6 }}
          onChange={(event) => setAnswer(event.target.value)}
        />
        <Button
          type="primary"
          loading={resolving}
          disabled={!answer.trim()}
          onClick={() => onResolve(interrupt, { answer: answer.trim() })}
        >
          {actionLabel}
        </Button>
      </section>
    );
  }
  if (interrupt.kind === "approval") {
    return (
      <section aria-label="Approval required">
        <p>{interrupt.public_message}</p>
        <Button
          type="primary"
          loading={resolving}
          onClick={() => onResolve(interrupt, { approved: true })}
        >
          {actionLabel}
        </Button>
        <Button
          danger
          disabled={resolving}
          onClick={() => onResolve(interrupt, { approved: false })}
        >
          拒绝
        </Button>
      </section>
    );
  }
  return (
    <section aria-label="Paused task">
      <p>{interrupt.public_message}</p>
      <Button
        type="primary"
        loading={resolving}
        onClick={() => onResolve(interrupt, { continue: true })}
      >
        {actionLabel}
      </Button>
    </section>
  );
}

function ApprovalAction({
  approval,
  approving,
  approvalComment,
  onApprovalCommentChange,
  onApprove,
}: {
  approval: ConversationApproval;
  approving: boolean;
  approvalComment: string;
  onApprovalCommentChange?: (value: string) => void;
  onApprove?: (approval: ConversationApproval, approved: boolean, comment?: string) => void;
}) {
  if (!onApprove) return <p>需要确认：{approval.tool_name || approval.tool_code}</p>;
  const submit = (approved: boolean) => {
    const comment = approvalComment.trim();
    if (comment) onApprove(approval, approved, comment);
    else onApprove(approval, approved);
  };
  return (
    <section aria-label="Approval required">
      <p>需要确认：{approval.tool_name || approval.tool_code}</p>
      {onApprovalCommentChange ? (
        <Input.TextArea
          aria-label="Approval comment"
          value={approvalComment}
          maxLength={500}
          autoSize={{ minRows: 2, maxRows: 4 }}
          onChange={(event) => onApprovalCommentChange(event.target.value)}
        />
      ) : null}
      <Button loading={approving} onClick={() => submit(true)}>允许</Button>
      <Button danger disabled={approving} onClick={() => submit(false)}>拒绝</Button>
    </section>
  );
}

function businessEvidence(turn: ConversationTurn) {
  return projectionsForTurn(turn).flatMap((projection) => {
    if (projection.type === "execution_summary") {
      const evidence = [
        ...projection.tools.map((tool) => `数据来源：${tool.tool_name}`),
      ];
      if (projection.quality_score != null) evidence.push(`质量评分：${Math.round(projection.quality_score * 100)} 分`);
      if (projection.evidence_ids?.length) {
        const label = projection.skill_code === "account_data_analysis" ? "分析依据" : "业务依据";
        evidence.push(`${label}：${projection.evidence_ids.length} 项`);
      }
      return evidence;
    }
    if (projection.type === "account_data") return accountDataSummary(projection.data);
    return [];
  });
}

function technicalLog(turn: ConversationTurn) {
  const lines: string[] = [];
  if (turn.id != null) lines.push(`消息编号：${turn.id}`);
  if (turn.intent?.mode) lines.push(`路由：${turn.intent.mode}`);
  if (turn.intent?.route_source) lines.push(`路由来源：${turn.intent.route_source}`);
  lines.push(`状态：${turn.status}`);
  if (turn.route_ms != null) lines.push(`路由耗时：${turn.route_ms} ms`);
  if (turn.first_token_ms != null) lines.push(`首字延迟：${turn.first_token_ms} ms`);
  if (turn.completion_ms != null) lines.push(`完成耗时：${turn.completion_ms} ms`);
  if (turn.total_ms != null) lines.push(`总耗时：${turn.total_ms} ms`);
  if (turn.model_call_count != null) lines.push(`模型调用：${turn.model_call_count} 次`);
  for (const projection of projectionsForTurn(turn)) {
    if (!isKnownProjection(projection)) {
      lines.push(`未识别事件：${safeProjectionType(projection.type)}`);
      continue;
    }
    if (projection.type !== "execution_summary") continue;
    if (projection.run_id) lines.push(`Agent Run：${projection.run_id}`);
    if (projection.skill_code) lines.push(`Skill：${projection.skill_code}`);
    if (projection.skill_version != null) lines.push(`Skill 版本：v${projection.skill_version}`);
    if (projection.skill_run_id) lines.push(`Skill Run：${projection.skill_run_id}`);
    if (projection.quality_score != null) lines.push(`质量门：${Math.round(projection.quality_score * 100)} 分`);
    for (const expert of projection.experts) lines.push(`Expert #${expert.id} · ${expert.agent_code} · ${expert.status}`);
    for (const tool of projection.tools) {
      lines.push(`Tool #${tool.id} · ${tool.tool_code} · ${tool.status}${tool.retry_count != null ? ` · 重试 ${tool.retry_count} 次` : ""}`);
    }
    if (projection.error_code) lines.push(`错误代码：${projection.error_code}`);
    if (projection.recovery_action) lines.push(`恢复建议：${projection.recovery_action}`);
  }
  return lines;
}

function accountDataSummary(data?: Extract<TurnProjection, { type: "account_data" }>["data"]) {
  const completeness = data?.data_status === "pending_import"
    ? "存在待确认导入"
    : data?.data_status === "empty"
      ? "暂无可分析数据"
      : data?.data_status === "available"
        ? "可用于分析"
        : "状态未知";
  const lines = [`数据完整性：${completeness}`];
  for (const pendingImport of data?.pending_imports ?? []) {
    lines.push(`数据来源：${pendingImport.template_code}（${pendingImport.row_count} 行）`);
    if (pendingImport.period_start || pendingImport.period_end) {
      lines.push(`数据周期：${pendingImport.period_start ?? "未知"} 至 ${pendingImport.period_end ?? "未知"}`);
    }
  }
  return lines;
}

function projectionsForTurn(turn: ConversationTurn) {
  return turn.id == null ? [] : turn.projections.filter((projection) => projection.turn_id === turn.id);
}

function isKnownProjection(projection: TurnProjection) {
  return [
    "answer",
    "progress",
    "expert",
    "execution_summary",
    "approval",
    "artifact",
    "account_data",
    "execution_blocked",
  ].includes(projection.type);
}

function safeProjectionType(value: unknown) {
  if (typeof value !== "string") return "unknown";
  return value.replace(/[^A-Za-z0-9_.:-]/g, "").slice(0, 64) || "unknown";
}
