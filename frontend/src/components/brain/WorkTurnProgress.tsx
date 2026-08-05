import { useId, useState } from "react";

import type { WorkTurnPresentation, WorkTurnStep } from "../../types";

const STEP_STATE_COPY: Record<WorkTurnStep["state"], string> = {
  done: "已完成",
  active: "进行中",
  waiting: "待执行",
  failed: "未完成",
};

export function WorkTurnProgress({
  steps,
  mode,
  isFailed,
}: {
  steps: WorkTurnStep[];
  mode: WorkTurnPresentation["progressMode"];
  isFailed: boolean;
}) {
  const [summaryOpen, setSummaryOpen] = useState(false);
  const contentId = useId();
  if (mode === "hidden" || steps.length === 0) return null;

  if (mode === "summary") {
    const completedCount = steps.filter((step) => step.state === "done").length;
    const label = `已完成 ${completedCount} 项检查`;
    return (
      <section className="tz-work-turn__progress tz-work-turn__progress--summary" aria-label="执行步骤">
        <button
          type="button"
          aria-expanded={summaryOpen}
          aria-controls={contentId}
          onClick={() => setSummaryOpen((open) => !open)}
        >
          {label}
        </button>
        {summaryOpen ? <StepList id={contentId} steps={steps} /> : null}
      </section>
    );
  }

  const completed = isFailed ? steps.filter((step) => step.state === "done") : [];
  const unfinished = isFailed ? steps.filter((step) => step.state !== "done") : steps;

  return (
    <section className="tz-work-turn__progress" aria-label="执行步骤">
      <h3>执行步骤</h3>
      {isFailed && completed.length > 0 ? <StepGroup title="已完成" steps={completed} /> : null}
      {isFailed ? <StepGroup title="未完成" steps={unfinished} unresolved /> : <StepList steps={steps} />}
    </section>
  );
}

function StepGroup({
  title,
  steps,
  unresolved = false,
}: {
  title: string;
  steps: WorkTurnStep[];
  unresolved?: boolean;
}) {
  if (steps.length === 0) return null;
  return (
    <section className="tz-work-turn__progress-group" aria-label={title}>
      <h4>{title}</h4>
      <StepList steps={steps} unresolved={unresolved} />
    </section>
  );
}

function StepList({
  id,
  steps,
  unresolved = false,
}: {
  id?: string;
  steps: WorkTurnStep[];
  unresolved?: boolean;
}) {
  return (
    <ol id={id}>
      {steps.map((step) => (
        <li key={step.code} data-step-state={step.state}>
          <span>{step.label}</span>
          <small>{unresolved ? "未完成" : STEP_STATE_COPY[step.state]}{step.detail ? `：${step.detail}` : ""}</small>
        </li>
      ))}
    </ol>
  );
}
