import {
  CheckCircleFilled,
  ClockCircleFilled,
  DownOutlined,
  LoadingOutlined,
} from "@ant-design/icons";
import { Collapse, Empty } from "antd";
import { useEffect, useMemo, useState } from "react";

import { AgentAvatar } from "../agents/AgentAvatar";
import {
  previewOrchestrationAdapter,
  type AgentStep,
  type AgentStepStatus,
  type OrchestrationSession,
} from "./orchestrationAdapter";

interface Props {
  goal: string;
}

export function AgentOrchestration({ goal }: Props) {
  const session = useMemo<OrchestrationSession | null>(() => {
    const trimmed = goal.trim();
    return trimmed ? previewOrchestrationAdapter.createPreviewSession(trimmed) : null;
  }, [goal]);
  const [visibleCount, setVisibleCount] = useState(0);

  useEffect(() => {
    setVisibleCount(session ? 1 : 0);
  }, [session]);

  useEffect(() => {
    if (!session || visibleCount >= session.steps.length) return;
    const timer = window.setTimeout(() => {
      setVisibleCount((count) => Math.min(count + 1, session.steps.length));
    }, visibleCount === 1 ? 1100 : 760);
    return () => window.clearTimeout(timer);
  }, [session, visibleCount]);

  if (!session) {
    return (
      <div className="dy-agent-empty">
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="输入运营目标后，这里会显示运营大脑调度专家的过程"
        />
      </div>
    );
  }

  const activeIndex = Math.min(visibleCount - 1, session.steps.length - 1);

  return (
    <section className="dy-agent-stage" aria-label="专家团接力执行">
      <div className="dy-agent-master">
        <AgentAvatar code="00-decision" className="dy-agent-master-avatar" />
        <div>
          <div className="dy-agent-master-label">运营大脑</div>
          <p>{session.intro}</p>
          <div className="dy-agent-goal">目标：{session.goal}</div>
        </div>
      </div>

      <div className="dy-agent-relay">
        {session.steps.slice(0, visibleCount).map((step, index) => {
          const status: AgentStepStatus =
            index < activeIndex ? "done" : index === activeIndex ? "running" : step.status;
          const handoff = session.handoffs.find((item) => item.afterStepIndex === index);
          return (
            <div key={step.id} className="dy-agent-relay-item">
              {handoff && <div className="dy-agent-handoff">{handoff.text}</div>}
              <AgentCard step={{ ...step, status }} index={index} />
            </div>
          );
        })}
      </div>
    </section>
  );
}

function AgentCard({ step, index }: { step: AgentStep; index: number }) {
  const done = step.status === "done";
  const running = step.status === "running";
  const waiting = step.status === "waiting";
  const message = done
    ? step.summary
    : running
      ? "正在分析输入目标、账号约束与下一步交付物。"
      : "等待运营大脑调度。";

  return (
    <article className={`dy-agent-card dy-agent-card-${step.status}`}>
      <div className="dy-agent-card-head">
        <div className="dy-agent-identity">
          <div className="dy-agent-avatar" aria-hidden="true">
            {step.identity}
          </div>
          <div>
            <div className="dy-agent-name-line">
              <span className="dy-tabular">{String(index + 1).padStart(2, "0")}</span>
              <strong>{step.agentName}</strong>
            </div>
            <p>{step.role}</p>
          </div>
        </div>
        <AgentState status={step.status} />
      </div>

      <div className="dy-agent-conclusion">
        {done ? (
          <CheckCircleFilled />
        ) : running ? (
          <LoadingOutlined />
        ) : (
          <ClockCircleFilled />
        )}
        <div>
          <span>{running ? "正在处理" : waiting ? "等待接力" : "核心结论"}</span>
          <p>{message}</p>
        </div>
      </div>

      <div className="dy-agent-output">
        <span>预期交付</span>
        <strong>{step.outputName}</strong>
      </div>

      {done && (
        <Collapse
          ghost
          expandIcon={({ isActive }) => (
            <DownOutlined rotate={isActive ? 180 : 0} className="dy-agent-collapse-icon" />
          )}
          items={[
            {
              key: "detail",
              label: <span className="dy-agent-detail-label">展开分析详情</span>,
              children: (
                <ul className="dy-agent-detail-list">
                  {step.detail.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              ),
            },
          ]}
        />
      )}
    </article>
  );
}

function AgentState({ status }: { status: AgentStepStatus }) {
  const label =
    status === "done" ? "已完成" : status === "running" ? "分析中" : status === "blocked" ? "需确认" : "等待中";

  return (
    <span className={`dy-agent-state dy-agent-state-${status}`}>
      <span />
      {label}
    </span>
  );
}
