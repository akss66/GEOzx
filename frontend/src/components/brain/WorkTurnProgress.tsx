import type { WorkTurnStep } from "../../types";

const STEP_STATE_COPY: Record<WorkTurnStep["state"], string> = {
  done: "已完成",
  active: "进行中",
  waiting: "待执行",
  failed: "未完成",
};

export function WorkTurnProgress({ steps }: { steps: WorkTurnStep[] }) {
  if (steps.length === 0) return null;

  return (
    <section className="tz-work-turn__progress" aria-label="执行步骤">
      <h3>执行步骤</h3>
      <ol>
        {steps.map((step) => (
          <li key={step.code} data-step-state={step.state}>
            <span>{step.label}</span>
            <small>{STEP_STATE_COPY[step.state]}{step.detail ? `：${step.detail}` : ""}</small>
          </li>
        ))}
      </ol>
    </section>
  );
}
