import type {
  ConversationTurn,
  TurnPhase,
  TurnProjection,
  WorkTurnStatus,
  WorkTurnStep,
  WorkTurnViewModel,
} from "../../types";
import { presentWorkTurn } from "./workTurnPresentation";

const TERMINAL_WORK_TURN_STATUSES: Record<string, WorkTurnStatus> = {
  blocked: "blocked",
  cancelled: "cancelled",
  stopped: "cancelled",
  failed: "failed",
  dead_letter: "failed",
  completed: "completed",
};

const PAUSED_WORK_TURN_STATUSES = new Set([
  "waiting_permission",
  "waiting_decision",
  "waiting_user",
]);

const FAILED_STEP_STATUSES = new Set([
  "failed",
  "error",
  "blocked",
  "cancelled",
  "stopped",
  "dead_letter",
]);

export function projectWorkTurn(turn: ConversationTurn): WorkTurnViewModel {
  const projections = projectionsForTurn(turn);
  const steps = overlayRuntimeSteps(projectSteps(projections), turn);
  const status = projectStatus(turn.status, turn.turn_phase, turn.pending_interrupt);
  const assistantText = turn.assistant_response ?? latestAnswer(projections);
  const presentation = presentWorkTurn({
    status,
    phase: turn.turn_phase,
    persistedStatus: turn.status,
    hasFinal: status === "completed" && assistantText != null,
    steps,
  });

  return {
    key: workTurnKey(turn),
    turnId: turn.id,
    userMessage: turn.user_input,
    status,
    currentActivity: turn.pending_interrupt?.status === "pending"
      ? turn.pending_interrupt.public_message
      : presentation.activityLabel,
    assistantText,
    presentation,
    steeringNotice: projectSteeringNotice(turn),
    steps,
    experts: projectExperts(projections),
    deliverableIds: [...new Set([
      ...projectDeliverableIds(projections),
      ...(turn.runtime_overlay?.deliverableIds ?? []),
    ])],
    assistant: {
      identity: "运营大脑",
      steps,
    },
  };
}

const STEERING_NOTICE_COPY = {
  supplement: "已补充要求",
  stop: "已请求停止",
  replace_goal: "已换目标",
} as const;

function projectSteeringNotice(turn: ConversationTurn): WorkTurnViewModel["steeringNotice"] {
  const notice = turn.runtime_overlay?.steering_notice;
  if (!notice) return null;
  return {
    label: notice.label,
    copy: STEERING_NOTICE_COPY[notice.label],
    ...(notice.message != null ? { message: notice.message } : {}),
    ...(notice.reason != null ? { reason: notice.reason } : {}),
  };
}

const RUNTIME_STEP_LABELS: Record<string, string> = {
  read_data: "读取账号数据",
  check_completeness: "核对数据完整性",
  specialist_work: "专家分析",
  quality_review: "质量审核",
  prepare_recommendation: "整理运营建议",
};

function overlayRuntimeSteps(
  persisted: WorkTurnStep[],
  turn: ConversationTurn,
): WorkTurnStep[] {
  const overlay = turn.runtime_overlay;
  if (!overlay) return persisted;
  const byCode = new Map(persisted.map((step) => [step.code, step]));
  for (const [code, runtime] of Object.entries(overlay.steps)) {
    const previous = byCode.get(code);
    byCode.set(code, {
      ...(previous ?? { code, label: RUNTIME_STEP_LABELS[code] ?? "执行任务" }),
      state: runtime.state,
      ...(runtime.detail ? { detail: runtime.detail } : {}),
    });
  }
  return [...byCode.values()];
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

function projectStatus(
  status: string,
  phase: TurnPhase | undefined,
  interrupt: ConversationTurn["pending_interrupt"],
): WorkTurnStatus {
  const terminalStatus = TERMINAL_WORK_TURN_STATUSES[status];
  if (terminalStatus) return terminalStatus;
  if (interrupt?.status === "pending") {
    if (interrupt.kind === "clarification") return "needs_input";
    if (interrupt.kind === "approval") return "needs_approval";
    return "paused";
  }
  if (PAUSED_WORK_TURN_STATUSES.has(status)) return "waiting_user";
  if (phase === "waiting_approval") return "waiting_user";
  if (phase === "failed") return "failed";
  if (phase === "completed") return "completed";
  return "working";
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
  if (FAILED_STEP_STATUSES.has(status ?? "")) return "failed";
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
