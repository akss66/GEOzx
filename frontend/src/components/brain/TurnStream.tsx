import type { ConversationThread, ConversationTurn, TurnProjection } from "../../types";
import { Button } from "antd";
import type { AgentToolCall } from "../../types";

export function TurnStream({
  thread,
  approvingToolCallId = null,
  onApprove,
}: {
  thread: ConversationThread;
  approvingToolCallId?: number | null;
  onApprove?: (approval: AgentToolCall, approved: boolean) => void;
}) {
  return (
    <div className="tz-turn-stream" aria-label="Conversation turns">
      {thread.turns.map((turn) => (
        <TurnArticle
          key={turn.id}
          turn={turn}
          approvingToolCallId={approvingToolCallId}
          onApprove={onApprove}
        />
      ))}
    </div>
  );
}

function TurnArticle({
  turn,
  approvingToolCallId,
  onApprove,
}: {
  turn: ConversationTurn;
  approvingToolCallId: number | null;
  onApprove?: (approval: AgentToolCall, approved: boolean) => void;
}) {
  const projections = turn.projections.filter((projection) => belongsToTurn(projection, turn.id));
  const unknownProjection = projections.some((projection) => !isKnownProjection(projection));

  return (
    <article
      className="tz-conversation-turn"
      data-testid={`conversation-turn-${turn.id}`}
      data-turn-id={turn.id}
      data-turn-key={`turn-${turn.id}`}
    >
      <header className="tz-conversation-turn__header">
        <span>Turn {turn.id}</span>
        <TurnRoute intent={turn.intent} />
      </header>
      <section className="tz-conversation-turn__user" aria-label="User message">
        {turn.user_input}
      </section>
      {turn.assistant_response ? (
        <section className="tz-conversation-turn__assistant" aria-label="Assistant response">
          {turn.assistant_response}
        </section>
      ) : null}
      <div className="tz-conversation-turn__projections">
        {projections.filter(isKnownProjection).map((projection) => (
          <Projection
            key={projectionKey(projection, turn.id)}
            projection={projection}
            turnId={turn.id}
            approving={approvingToolCallId === approvalId(projection)}
            onApprove={onApprove}
          />
        ))}
        {unknownProjection ? <UnknownProjection turnId={turn.id} /> : null}
      </div>
    </article>
  );
}

function TurnRoute({ intent }: { intent: Record<string, unknown> | null }) {
  const route = readableIntent(intent, "mode");
  const status = readableIntent(intent, "status");
  if (!route && !status) return null;

  return (
    <span className="tz-conversation-turn__route">
      {route ? `Route: ${route}` : null}
      {route && status ? " · " : null}
      {status ? `Status: ${status}` : null}
    </span>
  );
}

function Projection({
  projection,
  turnId,
  approving,
  onApprove,
}: {
  projection: TurnProjection;
  turnId: number;
  approving: boolean;
  onApprove?: (approval: AgentToolCall, approved: boolean) => void;
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
              <Button loading={approving} onClick={() => onApprove(projection.approval, true)}>
                Approve
              </Button>
              <Button danger disabled={approving} onClick={() => onApprove(projection.approval, false)}>
                Reject
              </Button>
            </div>
          ) : null}
        </section>
      );
    case "artifact":
      return <ArtifactProjection {...shared} artifactType={projection.artifact_type} report={projection.report} />;
    case "account_data":
      return <section {...shared}>Account data is ready.</section>;
    case "execution_blocked":
      return (
        <section {...shared} aria-label="Execution blocked">
          This turn needs attention.{projection.recovery_action ? ` ${projection.recovery_action}` : ""}
        </section>
      );
  }
}

function approvalId(projection: TurnProjection) {
  return projection.type === "approval" ? projection.approval.id : null;
}

function ArtifactProjection({
  artifactType,
  report,
  ...shared
}: {
  artifactType: string;
  report: Record<string, unknown>;
  className: string;
  "data-testid": string;
  "data-projection-key": string;
}) {
  const summary = readableReportText(report, "summary");
  const recommendations = readableReportText(report, "recommendations");

  return (
    <section {...shared} aria-label={`Artifact: ${titleCase(artifactType)}`}>
      <strong>{titleCase(artifactType)}</strong>
      {summary ? <p>{summary}</p> : null}
      {recommendations ? <p>{recommendations}</p> : null}
    </section>
  );
}

function UnknownProjection({ turnId }: { turnId: number }) {
  return (
    <section
      className="tz-turn-projection tz-turn-projection--unknown"
      data-projection-key={`unknown-${turnId}`}
    >
      An update is available for this turn.
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

function readableReportText(report: Record<string, unknown>, key: string) {
  const value = report[key];
  if (typeof value === "string") return value;
  if (Array.isArray(value) && value.every((item) => typeof item === "string")) {
    return value.join(" ");
  }
  return null;
}

function titleCase(value: string) {
  return value.split("_").map((word) => word[0]?.toUpperCase() + word.slice(1)).join(" ");
}
