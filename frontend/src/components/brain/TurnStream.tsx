import type {
  Artifact,
  ConversationApproval,
  ConversationThread,
  ConversationTurn,
  TurnProjection,
} from "../../types";
import { Button, Input, Tag, Typography } from "antd";
import { AgentAvatar } from "../agents/AgentAvatar";
import type { ArtifactAction } from "./ArtifactCard";
import {
  isActiveConversationTurnStatus,
  turnReactKey,
} from "./conversationTurnProjection";
import { TurnArtifact } from "./TurnArtifact";

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
      {thread.turns.map((turn) => (
        <TurnArticle
          key={turnReactKey({
            threadId: thread.id,
            turnId: turn.id,
            clientMessageId: turn.client_message_id ?? `turn-${turn.id ?? "pending"}`,
          })}
          turn={turn}
          threadId={thread.id}
          threadAccountId={thread.account_id}
          approvingToolCallId={approvingToolCallId}
          approvalComment={approvalComment}
          onApprovalCommentChange={onApprovalCommentChange}
          onApprove={onApprove}
          onArtifactAction={onArtifactAction}
          revisingArtifactId={revisingArtifactId}
          artifactRefreshKey={artifactRefreshKey}
          revisionArtifacts={revisionArtifacts}
          sourceArtifactOverrides={sourceArtifactOverrides}
        />
      ))}
    </div>
  );
}

function TurnArticle({
  turn,
  threadId,
  threadAccountId,
  approvingToolCallId,
  approvalComment,
  onApprovalCommentChange,
  onApprove,
  onArtifactAction,
  revisingArtifactId,
  artifactRefreshKey,
  revisionArtifacts,
  sourceArtifactOverrides,
}: {
  turn: ConversationTurn;
  threadId: number;
  threadAccountId: number;
  approvingToolCallId: number | null;
  approvalComment: string;
  onApprovalCommentChange?: (value: string) => void;
  onApprove?: (approval: ConversationApproval, approved: boolean, comment?: string) => void;
  onArtifactAction?: (action: ArtifactAction) => void;
  revisingArtifactId: number | null;
  artifactRefreshKey: number;
  revisionArtifacts: Record<number, Artifact[]>;
  sourceArtifactOverrides: Record<number, Artifact>;
}) {
  const persistedTurnId = turn.id;
  const projections = persistedTurnId == null
    ? []
    : turn.projections.filter((projection) => belongsToTurn(projection, persistedTurnId));
  const unknownProjection = projections.some((projection) => !isKnownProjection(projection));
  const executionBlocked = projections.some((projection) => projection.type === "execution_blocked");
  const clientIdentity = turn.client_message_id ?? `turn-${turn.id ?? "pending"}`;

  return (
    <article
      className="tz-conversation-turn"
      data-testid={`conversation-turn-${turn.id ?? clientIdentity}`}
      data-turn-id={turn.id ?? undefined}
      data-turn-key={turnReactKey({
        threadId,
        turnId: turn.id,
        clientMessageId: clientIdentity,
      })}
      data-turn-status={turn.status}
    >
      <section
        className="dy-chat-message dy-chat-message-user tz-conversation-turn__user"
        aria-label="User message"
      >
        <div className="dy-chat-bubble">
          <Typography.Paragraph style={{ color: "inherit", margin: 0, whiteSpace: "pre-wrap" }}>
            {turn.user_input}
          </Typography.Paragraph>
        </div>
      </section>
      <section
        className="dy-chat-message dy-chat-message-agent tz-conversation-turn__assistant"
        aria-label="Assistant response"
        aria-busy={isActiveConversationTurnStatus(turn.status)}
      >
        <AgentAvatar code="00-decision" className="dy-chat-avatar" />
        <div className="dy-chat-bubble">
          <div className="dy-chat-title-line">
            <span>运营大脑</span>
            <Tag style={{ marginInlineEnd: 0 }}>
              {executionBlocked ? "需处理" : turnStatusCopy(turn.status)}
            </Tag>
          </div>
          {turn.assistant_response ? (
            <Typography.Paragraph style={{ color: "inherit", margin: 0, whiteSpace: "pre-wrap" }}>
              {turn.assistant_response}
            </Typography.Paragraph>
          ) : (
            <Typography.Text type="secondary">
              {isActiveConversationTurnStatus(turn.status) ? "正在处理…" : "暂无回复"}
            </Typography.Text>
          )}
        </div>
      </section>
      <div className="tz-conversation-turn__projections">
        {projections.filter(isKnownProjection).map((projection) => (
          <Projection
            key={projectionKey(projection, persistedTurnId!)}
            projection={projection}
            turnId={persistedTurnId!}
            threadId={threadId}
            threadAccountId={threadAccountId}
            approving={approvingToolCallId === approvalId(projection)}
            approvalComment={approvalComment}
            onApprovalCommentChange={onApprovalCommentChange}
            onApprove={onApprove}
            onArtifactAction={onArtifactAction}
            revisingArtifactId={revisingArtifactId}
            artifactRefreshKey={artifactRefreshKey}
            revisionArtifacts={revisionArtifacts}
            sourceArtifactOverrides={sourceArtifactOverrides}
          />
        ))}
        {unknownProjection && persistedTurnId != null
          ? <UnknownProjection turnId={persistedTurnId} />
          : null}
      </div>
      <TurnTechnicalDetails turn={turn} />
    </article>
  );
}

function TurnTechnicalDetails({ turn }: { turn: ConversationTurn }) {
  const { intent } = turn;
  const route = readableIntent(intent, "mode");
  const execution = turn.projections.find(
    (projection) => projection.type === "execution_summary",
  );

  return (
    <details className="tz-conversation-turn__technical">
      <summary>技术日志</summary>
      {turn.id != null ? <div>消息编号：{turn.id}</div> : null}
      {route ? <div>路由：{route}</div> : null}
      {turn.intent?.route_source ? <div>路由来源：{turn.intent.route_source}</div> : null}
      <div>状态：{turn.status}</div>
      {turn.route_ms != null ? <div>路由耗时：{turn.route_ms} ms</div> : null}
      {turn.first_token_ms != null ? <div>首字延迟：{turn.first_token_ms} ms</div> : null}
      {turn.completion_ms != null ? <div>完成耗时：{turn.completion_ms} ms</div> : null}
      {turn.total_ms != null ? <div>总耗时：{turn.total_ms} ms</div> : null}
      {turn.model_call_count != null ? <div>模型调用：{turn.model_call_count} 次</div> : null}
      {execution?.type === "execution_summary" ? (
        <>
          {execution.run_id ? <div>Agent Run：{execution.run_id}</div> : null}
          {execution.skill_code ? <div>Skill：{execution.skill_code}</div> : null}
          {execution.skill_version != null ? (
            <div>Skill 版本：v{execution.skill_version}</div>
          ) : null}
          {execution.skill_run_id ? <div>Skill Run：{execution.skill_run_id}</div> : null}
          {execution.quality_score != null ? (
            <div>质量门：{Math.round(execution.quality_score * 100)} 分</div>
          ) : null}
          {execution.experts.map((expert) => (
            <div key={expert.id}>
              Expert #{expert.id} · {expert.agent_code} · {expert.status}
            </div>
          ))}
          {execution.tools.map((tool) => (
            <div key={tool.id}>
              Tool #{tool.id} · {tool.tool_code} · {tool.status}
              {tool.retry_count != null ? ` · 重试 ${tool.retry_count} 次` : ""}
            </div>
          ))}
          {execution.error_code ? <div>错误代码：{execution.error_code}</div> : null}
          {execution.recovery_action ? <div>恢复建议：{execution.recovery_action}</div> : null}
        </>
      ) : null}
    </details>
  );
}

function turnStatusCopy(status: string) {
  if (["claimed", "waiting_predecessor", "queued"].includes(status)) return "等待中";
  if (["running", "retry_wait"].includes(status)) return "执行中";
  if (["completed"].includes(status)) return "完成";
  if (["blocked", "waiting_permission", "waiting_decision", "waiting_user"].includes(status)) {
    return "需处理";
  }
  if (["failed", "dead_letter"].includes(status)) return "执行失败";
  if (["cancelled", "stopped"].includes(status)) return "已停止";
  return "状态更新";
}

function Projection({
  projection,
  turnId,
  threadId,
  threadAccountId,
  approving,
  approvalComment,
  onApprovalCommentChange,
  onApprove,
  onArtifactAction,
  revisingArtifactId,
  artifactRefreshKey,
  revisionArtifacts,
  sourceArtifactOverrides,
}: {
  projection: TurnProjection;
  turnId: number;
  threadId: number;
  threadAccountId: number;
  approving: boolean;
  approvalComment: string;
  onApprovalCommentChange?: (value: string) => void;
  onApprove?: (approval: ConversationApproval, approved: boolean, comment?: string) => void;
  onArtifactAction?: (action: ArtifactAction) => void;
  revisingArtifactId: number | null;
  artifactRefreshKey: number;
  revisionArtifacts: Record<number, Artifact[]>;
  sourceArtifactOverrides: Record<number, Artifact>;
}) {
  const key = projectionKey(projection, turnId);
  const shared = {
    className: "tz-turn-projection",
    "data-testid": `projection-${key}`,
    "data-projection-key": key,
  };

  switch (projection.type) {
    case "answer":
      return <section {...shared}>{projection.message}</section>;
    case "progress":
      return (
        <section {...shared} aria-label="Turn progress">
          {projection.stages.map((stage) => (
            <div key={`${projection.skill_run_id}-${stage.code}`}>
              {stage.name}: {stage.status}
            </div>
          ))}
        </section>
      );
    case "expert":
      return (
        <section {...shared} aria-label="Expert update">
          {projection.invocation.agent_name}: {projection.invocation.status}
        </section>
      );
    case "execution_summary":
      return (
        <section {...shared} aria-label="Execution summary">
          {projection.experts.length > 0 ? (
            <>
              <strong>调用专家</strong>
              <div>{projection.experts.map((expert) => expert.agent_name).join("、")}</div>
            </>
          ) : projection.tools.length > 0 ? (
            <>
              <strong>调用工具</strong>
              <div>{projection.tools.map((tool) => tool.tool_name).join("、")}</div>
            </>
          ) : projection.quality_score != null ? (
            <strong>质量审核</strong>
          ) : (
            <strong>执行记录</strong>
          )}
          {projection.quality_score != null ? (
            <div>质量评分：{Math.round(projection.quality_score * 100)} 分</div>
          ) : null}
        </section>
      );
    case "approval":
      return (
        <section {...shared} aria-label="Approval required">
          Approval required: {projection.approval.tool_name || projection.approval.tool_code}
          {onApprove ? (
            <div>
              {onApprovalCommentChange ? (
                <Input.TextArea
                  aria-label="Approval comment"
                  value={approvalComment}
                  maxLength={500}
                  autoSize={{ minRows: 2, maxRows: 4 }}
                  onChange={(event) => onApprovalCommentChange(event.target.value)}
                />
              ) : null}
              <Button
                loading={approving}
                onClick={() => {
                  const comment = approvalComment.trim();
                  if (comment) onApprove(projection.approval, true, comment);
                  else onApprove(projection.approval, true);
                }}
              >
                Approve
              </Button>
              <Button
                danger
                disabled={approving}
                onClick={() => {
                  const comment = approvalComment.trim();
                  if (comment) onApprove(projection.approval, false, comment);
                  else onApprove(projection.approval, false);
                }}
              >
                Reject
              </Button>
            </div>
          ) : null}
        </section>
      );
    case "artifact":
      return (
        <TurnArtifact
          {...shared}
          artifactId={projection.artifact_id}
          accountId={projection.account_id}
          threadAccountId={threadAccountId}
          threadId={threadId}
          sourceTurnId={turnId}
          onAction={onArtifactAction}
          revisingArtifactId={revisingArtifactId}
          revisionArtifacts={revisionArtifacts[projection.artifact_id]}
          sourceArtifactOverride={sourceArtifactOverrides[projection.artifact_id]}
          refreshKey={artifactRefreshKey}
        />
      );
    case "account_data":
      return <section {...shared}>已读取当前账号的数据，可继续告诉我想分析的指标。</section>;
    case "execution_blocked":
      return (
        <section {...shared} aria-label="Execution blocked">
          本次执行需要处理。{projection.recovery_action ? ` ${projection.recovery_action}` : ""}
        </section>
      );
  }
}

function approvalId(projection: TurnProjection) {
  return projection.type === "approval" ? projection.approval.id : null;
}

function UnknownProjection({ turnId }: { turnId: number }) {
  return (
    <section
      className="tz-turn-projection tz-turn-projection--unknown"
      data-projection-key={`unknown-${turnId}`}
    >
      本轮有一条新进展，请刷新后查看。
    </section>
  );
}

function belongsToTurn(projection: TurnProjection, turnId: number) {
  return projection.turn_id === turnId;
}

function isKnownProjection(projection: TurnProjection): projection is TurnProjection {
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

function projectionKey(projection: TurnProjection, turnId: number) {
  switch (projection.type) {
    case "answer":
      return `answer-${turnId}`;
    case "progress":
      return `progress-${projection.skill_run_id}`;
    case "expert":
      return `expert-${projection.invocation.id}`;
    case "execution_summary":
      return `execution-summary-${projection.skill_run_id ?? turnId}`;
    case "approval":
      return `approval-${projection.approval.id}`;
    case "artifact":
      return `artifact-${projection.artifact_id}`;
    case "account_data":
      return `account-data-${projection.skill_run_id}`;
    case "execution_blocked":
      return `blocked-${projection.skill_run_id}`;
  }
}

function readableIntent(intent: ConversationTurn["intent"], key: "mode") {
  const value = intent?.[key];
  return typeof value === "string" && value.length <= 80 ? value : null;
}
