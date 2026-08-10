import { Component, type ReactNode } from "react";
import { ThinkingOrb } from "thinking-orbs";

import type { TurnPhase } from "../../types";
import { AgentAvatar } from "../agents/AgentAvatar";
import { workTurnOrbState } from "./workTurnOrbState";

type Props = {
  showThinkingOrb: boolean;
  phase?: TurnPhase;
  identity: string;
  activityLabel?: string | null;
  className?: string;
};

type BoundaryProps = { fallback: ReactNode; children: ReactNode };

class OrbRenderBoundary extends Component<BoundaryProps, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

export function MainAgentStatusAvatar({
  showThinkingOrb,
  phase,
  identity,
  activityLabel,
  className = "",
}: Props) {
  const fallback = <AgentAvatar code="00-decision" className={className} label={identity} />;
  if (!showThinkingOrb) return fallback;

  return (
    <OrbRenderBoundary fallback={fallback}>
      <span className={["tz-main-agent-status-avatar", className].filter(Boolean).join(" ")} data-thinking-orb="true">
        <ThinkingOrb
          state={workTurnOrbState(phase)}
          size={64}
          theme="light"
          aria-label={activityLabel ?? undefined}
        />
      </span>
    </OrbRenderBoundary>
  );
}
