import type {
  ConversationTurn,
  TurnPhase,
  TurnProjection,
  WorkTurnStatus,
  WorkTurnStep,
  WorkTurnViewModel,
} from "../../types";

export function projectWorkTurn(turn: ConversationTurn): WorkTurnViewModel {
  const projections = projectionsForTurn(turn);
  const steps = projectSteps(projections);

  return {
    key: workTurnKey(turn),
    turnId: turn.id,
    userMessage: turn.user_input,
    status: projectStatus(turn.status, turn.turn_phase),
    currentActivity: projectCurrentActivity(turn.turn_phase, turn.status, steps),
    assistantText: turn.assistant_response ?? latestAnswer(projections),
    steps,
    experts: projectExperts(projections),
    deliverableIds: projectDeliverableIds(projections),
    assistant: {
      identity: "运营大脑",
      steps,
    },
  };
}

export function reduceWorkTurnStreamFrame(
  turn: ConversationTurn,
  payload: Record<string, unknown>,
  eventTurnId: number | null,
  phase: "start" | "delta" | "done" | "error",
  turnPhase: TurnPhase | null,
): ConversationTurn {
  const sequence = nonNegativeInteger(payload.stream_seq);
  if (sequence == null) return turn;

  const messageId = stringValue(payload.message_id) ?? "";
  const previous = turn.stream_state;
  if (previous?.terminal) return turn;
  if (previous && previous.messageId === messageId && sequence <= previous.lastSequence) {
    return turn;
  }

  const content = phase === "delta"
    ? `${turn.assistant_response ?? ""}${stringValue(payload.delta) ?? ""}`
    : phase === "done"
      ? stringValue(payload.content) ?? stringValue(payload.message) ?? ""
      : phase === "error"
        ? stringValue(payload.error) ?? stringValue(payload.message) ?? ""
        : turn.assistant_response;
  const terminal = phase === "done" || phase === "error";

  return {
    ...turn,
    ...(turn.id == null && eventTurnId != null ? { id: eventTurnId } : {}),
    assistant_response: content,
    status: phase === "error"
      ? "failed"
      : phase === "done"
        ? stringValue(payload.status) ?? "completed"
        : "running",
    stream_state: {
      messageId,
      lastSequence: sequence,
      terminal,
    },
    ...(turnPhase ? { turn_phase: turnPhase } : {}),
  };
}

function workTurnKey(turn: ConversationTurn) {
  const message = turn.client_message_id ?? `turn-${turn.id ?? "pending"}`;
  return `org:${turn.org_id}:thread:${turn.thread_id}:message:${message}`;
}

function projectionsForTurn(turn: ConversationTurn) {
  if (turn.id == null) return [];
  return turn.projections.filter((projection) =>
    projection.turn_id == null || projection.turn_id === turn.id
  );
}

function projectStatus(status: string, phase: TurnPhase | undefined): WorkTurnStatus {
  if (phase === "waiting_approval") return "waiting_user";
  if (phase === "failed") return "failed";
  if (["waiting_permission", "waiting_decision", "waiting_user"].includes(status)) {
    return "waiting_user";
  }
  if (["blocked"].includes(status)) return "blocked";
  if (["failed", "dead_letter"].includes(status)) return "failed";
  if (["cancelled", "stopped"].includes(status)) return "cancelled";
  if (["completed"].includes(status) || phase === "completed") return "completed";
  return "working";
}

function projectCurrentActivity(
  phase: TurnPhase | undefined,
  status: string,
  steps: WorkTurnStep[],
) {
  if (
    ["completed", "failed"].includes(phase ?? "")
    || ["completed", "failed", "dead_letter", "cancelled", "stopped"].includes(status)
  ) {
    return null;
  }
  const phaseActivity: Partial<Record<TurnPhase, string>> = {
    understanding: "正在理解需求",
    reading_data: "正在读取账号数据",
    consulting_experts: "正在咨询专家",
    quality_review: "正在质量审核",
    waiting_approval: "等待你的确认",
    composing_artifact: "正在整理回复",
  };
  if (phase && phaseActivity[phase]) return phaseActivity[phase];
  const activeStep = steps.find((step) => step.state === "active" || step.state === "waiting");
  if (activeStep) return activeStep.label;
  if (["queued", "claimed", "waiting_predecessor"].includes(status)) return "等待执行";
  if (status === "retry_wait") return "正在恢复执行";
  return null;
}

function projectSteps(projections: TurnProjection[]): WorkTurnStep[] {
  return projections.flatMap((projection) => {
    if (projection.type === "progress") {
      return projection.stages.map((stage) => ({
        code: stage.code,
        label: stage.name,
        state: projectStepState(stage.status),
      }));
    }
    if (projection.type === "execution_summary" && projection.skill_code) {
      return [{
        code: projection.skill_code,
        label: projection.skill_code,
        state: projectStepState(projection.status),
        ...(projection.error_code ? { detail: projection.error_code } : {}),
      }];
    }
    if (projection.type === "execution_blocked") {
      return [{
        code: projection.code,
        label: projection.recovery_action ?? projection.code,
        state: "failed",
      }];
    }
    return [];
  });
}

function projectStepState(status: string | null | undefined): WorkTurnStep["state"] {
  if (["done", "completed", "success", "skipped"].includes(status ?? "")) return "done";
  if (["failed", "error"].includes(status ?? "")) return "failed";
  if (["running", "active", "in_progress"].includes(status ?? "")) return "active";
  return "waiting";
}

function projectExperts(projections: TurnProjection[]) {
  const experts = projections.flatMap((projection) => {
    if (projection.type === "expert") {
      return [{ name: projection.invocation.agent_name, status: projection.invocation.status }];
    }
    if (projection.type === "execution_summary") {
      return projection.experts.map((expert) => ({
        name: expert.agent_name,
        status: expert.status,
      }));
    }
    return [];
  });
  return experts.filter((expert, index) =>
    experts.findIndex((candidate) => candidate.name === expert.name) === index
  );
}

function projectDeliverableIds(projections: TurnProjection[]) {
  const ids = projections.flatMap((projection) => {
    if (projection.type === "artifact") return [projection.artifact_id];
    if (projection.type === "execution_summary") return projection.artifact_ids ?? [];
    return [];
  });
  return [...new Set(ids)];
}

function latestAnswer(projections: TurnProjection[]) {
  for (let index = projections.length - 1; index >= 0; index -= 1) {
    const projection = projections[index];
    if (projection.type === "answer") return projection.message;
  }
  return null;
}

function stringValue(value: unknown) {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function nonNegativeInteger(value: unknown) {
  const number = Number(value);
  return Number.isInteger(number) && number >= 0 ? number : null;
}
