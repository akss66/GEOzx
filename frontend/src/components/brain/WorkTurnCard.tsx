import type { ReactNode } from "react";

import type { WorkTurnViewModel } from "../../types";
import { AgentAvatar } from "../agents/AgentAvatar";
import { ProcessDisclosure } from "./ProcessDisclosure";
import { WorkTurnProgress } from "./WorkTurnProgress";

export function WorkTurnCard({
  view,
  evidenceSummary = [],
  technicalLog = [],
  deliverables,
  businessActions,
  sourceStatus,
}: {
  view: WorkTurnViewModel;
  evidenceSummary?: string[];
  technicalLog?: string[];
  deliverables?: ReactNode;
  businessActions?: ReactNode;
  sourceStatus?: string;
}) {
  const steeringDetail = view.steeringNotice?.message ?? view.steeringNotice?.reason;
  return (
    <article
      className="tz-work-turn"
      data-testid="work-turn"
      data-turn-id={view.turnId ?? undefined}
      data-turn-key={view.key}
      data-turn-status={sourceStatus ?? view.status}
    >
      <section className="tz-work-turn__user" aria-label="用户消息">
        <p>{view.userMessage}</p>
      </section>

      <section
        className="tz-work-turn__operator"
        aria-label="运营大脑工作回合"
        aria-busy={view.presentation.isActive}
        data-thinking={view.presentation.isActive || undefined}
      >
        <header className="tz-work-turn__identity">
          <AgentAvatar code="00-decision" className="dy-chat-avatar" label={view.assistant.identity} />
          <span>{view.assistant.identity}</span>
          {view.presentation.statusLabel ? <small>{view.presentation.statusLabel}</small> : null}
        </header>

        {view.steeringNotice ? (
          <div
            className="tz-work-turn__steering-notice"
            role="note"
            aria-label="任务调整"
            data-steering-label={view.steeringNotice.label}
          >
            <strong>{view.steeringNotice.copy}</strong>
            {steeringDetail ? <span>{steeringDetail}</span> : null}
          </div>
        ) : null}
        {view.presentation.showActivity && view.presentation.activityLabel ? (
          <p className="tz-work-turn__activity" role="status" aria-live="polite">{view.presentation.activityLabel}</p>
        ) : null}
        {view.presentation.showFinal && view.assistantText ? (
          <p className="tz-work-turn__response">{view.assistantText}</p>
        ) : null}
        <WorkTurnProgress steps={view.steps} mode={view.presentation.progressMode} />
        <ProcessDisclosure
          label={view.presentation.processLabel}
          experts={view.experts}
          evidenceSummary={evidenceSummary}
          technicalLog={technicalLog}
        />
        {deliverables ? <section className="tz-work-turn__deliverables" aria-label="运营内容">{deliverables}</section> : null}
        {businessActions ? <section className="tz-work-turn__actions" aria-label="业务动作">{businessActions}</section> : null}
      </section>
    </article>
  );
}
