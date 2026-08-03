import { Button, Input } from "antd";

import type {
  Artifact,
  ConversationApproval,
  ConversationThread,
  ConversationTurn,
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
  onArtifactAction,
  revisingArtifactId = null,
  artifactRefreshKey = 0,
  revisionArtifacts = {},
  sourceArtifactOverrides = {},
}: {
  thread: ConversationThread;
  approvingToolCallId?: number | null;
  approvalComment?: string;
  onApprovalCommentChange?: (value: string) => void;
  onApprove?: (approval: ConversationApproval, approved: boolean, comment?: string) => void;
  onArtifactAction?: (action: ArtifactAction) => void;
  revisingArtifactId?: number | null;
  artifactRefreshKey?: number;
  revisionArtifacts?: Record<number, Artifact[]>;
  sourceArtifactOverrides?: Record<number, Artifact>;
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
              artifactRefreshKey,
              revisionArtifacts,
              sourceArtifactOverrides,
            })}
            businessActions={renderBusinessActions({
              turn,
              approvingToolCallId,
              approvalComment,
              onApprovalCommentChange,
              onApprove,
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
  artifactRefreshKey,
  revisionArtifacts,
  sourceArtifactOverrides,
}: {
  turn: ConversationTurn;
  thread: ConversationThread;
  onArtifactAction?: (action: ArtifactAction) => void;
  revisingArtifactId: number | null;
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
        revisionArtifacts={revisionArtifacts[projection.artifact_id]}
        sourceArtifactOverride={sourceArtifactOverrides[projection.artifact_id]}
        refreshKey={artifactRefreshKey}
      />,
    ];
  });
}

function renderBusinessActions({
  turn,
  approvingToolCallId,
  approvalComment,
  onApprovalCommentChange,
  onApprove,
}: {
  turn: ConversationTurn;
  approvingToolCallId: number | null;
  approvalComment: string;
  onApprovalCommentChange?: (value: string) => void;
  onApprove?: (approval: ConversationApproval, approved: boolean, comment?: string) => void;
}) {
  const projections = projectionsForTurn(turn);
  const approval = projections.find((projection) => projection.type === "approval");
  const blocked = projections.find((projection) => projection.type === "execution_blocked");
  const unknown = projections.some((projection) => !isKnownProjection(projection));

  return (
    <>
      {approval?.type === "approval" ? (
        <ApprovalAction
          approval={approval.approval}
          approving={approvingToolCallId === approval.approval.id}
          approvalComment={approvalComment}
          onApprovalCommentChange={onApprovalCommentChange}
          onApprove={onApprove}
        />
      ) : null}
      {blocked?.type === "execution_blocked" ? (
        <p>本次执行需要处理。{blocked.recovery_action ? ` ${blocked.recovery_action}` : ""}</p>
      ) : null}
      {unknown ? <p>本轮有一条新进展，请刷新后查看。</p> : null}
    </>
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
        ...projection.experts.map((expert) => `已调用 ${expert.agent_name}`),
        ...projection.tools.map((tool) => `已使用 ${tool.tool_name}`),
      ];
      if (projection.quality_score != null) evidence.push(`质量评分：${Math.round(projection.quality_score * 100)} 分`);
      return evidence;
    }
    if (projection.type === "account_data") return [accountDataMessage(projection.data?.data_status)];
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

function accountDataMessage(status?: "available" | "pending_import" | "empty") {
  if (status === "pending_import") return "当前账号暂无已正式写入的数据；存在待确认导入，请先完成正式写入。";
  if (status === "empty") return "当前账号暂无可分析数据，请先同步或导入账号数据。";
  return "已读取当前账号的数据，可继续告诉我想分析的指标。";
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
