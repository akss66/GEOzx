import type {
  TurnPhase,
  WorkTurnPresentation,
  WorkTurnStatus,
  WorkTurnStep,
} from "../../types";

type WorkTurnPresentationInput = {
  status: WorkTurnStatus;
  phase?: TurnPhase;
  persistedStatus: string;
  hasFinal: boolean;
  steps: WorkTurnStep[];
};

type WorkTurnActivityInput = Pick<WorkTurnPresentationInput, "phase" | "steps"> & {
  status: WorkTurnStatus | string;
  persistedStatus?: string;
};

const STATUS_LABELS: Record<Exclude<WorkTurnStatus, "working">, string> = {
  needs_input: "等待你补充信息",
  needs_approval: "等待你的确认",
  paused: "已暂停",
  waiting_user: "等待你的确认",
  completed: "已完成",
  blocked: "需要处理",
  failed: "本次分析未完成",
  cancelled: "已停止",
};

const ACTIVITY_BY_PHASE: Partial<Record<TurnPhase, string>> = {
  understanding: "正在理解你的需求",
  reading_data: "正在核对已导入的数据范围",
  consulting_experts: "正在分析账号的主要问题",
  quality_review: "正在核验结论与数据依据",
  composing_artifact: "正在整理优先运营建议",
};

const TERMINAL_STATUSES = new Set<WorkTurnStatus>([
  "completed",
  "blocked",
  "failed",
  "cancelled",
]);

export function presentWorkTurn(turn: WorkTurnPresentationInput): WorkTurnPresentation {
  const isActive = turn.status === "working";
  const progress = presentWorkTurnProgress(turn);
  const activityLabel = !isActive || turn.hasFinal || TERMINAL_STATUSES.has(turn.status)
    ? null
    : presentWorkTurnActivity(turn);

  return {
    isActive,
    statusLabel: turn.status === "working" ? null : STATUS_LABELS[turn.status],
    activityLabel,
    showActivity: isActive && activityLabel != null,
    showFinal: turn.hasFinal,
    progressMode: progress.mode,
    processLabel: progress.mode === "summary" ? "查看已完成过程" : "查看分析过程",
  };
}

export function presentWorkTurnActivity(turn: WorkTurnActivityInput): string | null {
  if (isTerminalStatus(turn.status)) return null;
  const phaseActivity = turn.phase ? ACTIVITY_BY_PHASE[turn.phase] : null;
  if (phaseActivity) return phaseActivity;

  const activeStep = turn.steps.find((step) => step.state === "active" || step.state === "waiting");
  if (activeStep) return activeStep.label;
  if (["queued", "claimed", "waiting_predecessor"].includes(turn.persistedStatus ?? "")) {
    return "等待执行";
  }
  if (turn.persistedStatus === "retry_wait") return "正在恢复执行";
  return "正在分析账号情况";
}

export function presentWorkTurnProgress(turn: Pick<WorkTurnPresentationInput, "status" | "steps">) {
  if (turn.steps.length === 0) return { mode: "hidden" as const };
  if (turn.status === "completed") return { mode: "summary" as const };
  return { mode: "expanded" as const };
}

function isTerminalStatus(status: string): status is WorkTurnStatus {
  return TERMINAL_STATUSES.has(status as WorkTurnStatus);
}
