import type { TurnPhase } from "../../types";

export type ThinkingOrbVisualState =
  | "working"
  | "searching"
  | "solving"
  | "listening"
  | "connecting"
  | "weaving"
  | "composing"
  | "breathing"
  | "shaping";

const ORB_STATE_BY_PHASE: Partial<Record<TurnPhase, ThinkingOrbVisualState>> = {
  understanding: "listening",
  reading_data: "searching",
  consulting_experts: "weaving",
  quality_review: "solving",
  composing_artifact: "composing",
};

export function workTurnOrbState(phase?: TurnPhase): ThinkingOrbVisualState {
  return (phase && ORB_STATE_BY_PHASE[phase]) || "working";
}
