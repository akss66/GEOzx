import type { Artifact, ConversationThread, ConversationTurn, TurnProjection } from "../../types";
import { Button, Input, Tag, Typography } from "antd";
import type { AgentToolCall } from "../../types";
import { AgentAvatar } from "../agents/AgentAvatar";
import type { ArtifactAction } from "./ArtifactCard";
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
  onApprove?: (approval: AgentToolCall, approved: boolean, comment?: string) => void;
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
          key={turn.id}
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
  onApprove?: (approval: AgentToolCall, approved: boolean, comment?: string) => void;
  onArtifactAction?: (action: ArtifactAction) => void;
  revisingArtifactId: number | null;
  artifactRefreshKey: number;
  revisionArtifacts: Record<number, Artifact[]>;
  sourceArtifactOverrides: Record<number, Artifact>;
}) {
  const projections = turn.projections.filter((projection) => belongsToTurn(projection, turn.id));
  const unknownProjection = projections.some((projection) => !isKnownProjection(projection));
  const executionBlocked = projections.some((projection) => projection.type === "execution_blocked");

  return (
    <article
      className="tz-conversation-turn"
      data-testid={`conversation-turn-${turn.id}`}
      data-turn-id={turn.id}
      data-turn-key={`turn-${turn.id}`}
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
      {turn.assistant_response ? (
        <section
          className="dy-chat-message dy-chat-message-agent tz-conversation-turn__assistant"
          aria-label="Assistant response"
        >
          <AgentAvatar code="00-decision" className="dy-chat-avatar" />
          <div className="dy-chat-bubble">
            <div className="dy-chat-title-line">
              <span>运营大脑</span>
              <Tag style={{ marginInlineEnd: 0 }}>{executionBlocked ? "需处理" : "完成"}</Tag>
            </div>
            <Typography.Paragraph style={{ color: "inherit", margin: 0, whiteSpace: "pre-wrap" }}>
              {turn.assistant_response}
            </Typography.Paragraph>
          </div>
        </section>
      ) : null}
      <div className="tz-conversation-turn__projections">
        {projections.filter(isKnownProjection).map((projection) => (
          <Projection
            key={projectionKey(projection, turn.id)}
            projection={projection}
            turnId={turn.id}
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
        {unknownProjection ? <UnknownProjection turnId={turn.id} /> : null}
      </div>
      <TurnTechnicalDetails turn={turn} />
    </article>
  );
}

function TurnTechnicalDetails({ turn }: { turn: ConversationTurn }) {
  const { intent } = turn;
  const route = readableIntent(intent, "mode");
  const status = readableIntent(intent, "status");

  return (
    <details className="tz-conversation-turn__technical">
      <summary>技术日志</summary>
      <div>消息编号：{turn.id}</div>
      {route ? <div>路由：{route}</div> : null}
      {status ? <div>状态：{status}</div> : null}
    </details>
  );
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
  onApprove?: (approval: AgentToolCall, approved: boolean, comment?: string) => void;
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

function readableIntent(intent: Record<string, unknown> | null, key: string) {
  const value = intent?.[key];
  return typeof value === "string" && value.length <= 80 ? value : null;
}
