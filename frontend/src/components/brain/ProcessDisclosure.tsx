import { useState } from "react";

export function ProcessDisclosure({
  experts,
  evidenceSummary,
  technicalLog,
}: {
  experts: Array<{ name: string; status: string }>;
  evidenceSummary: string[];
  technicalLog: string[];
}) {
  const [processOpen, setProcessOpen] = useState(false);
  const [technicalOpen, setTechnicalOpen] = useState(false);

  return (
    <details
      className="tz-work-turn__process"
      open={processOpen}
      onToggle={(event) => setProcessOpen(event.currentTarget.open)}
    >
      <summary>查看过程</summary>
      {processOpen ? (
        <div>
          {experts.length > 0 ? (
            <section aria-label="调用专家摘要">
              <h3>调用专家</h3>
              <ul>
                {experts.map((expert) => <li key={expert.name}>{expert.name}：{expert.status}</li>)}
              </ul>
            </section>
          ) : null}
          {evidenceSummary.length > 0 ? (
            <section aria-label="业务依据摘要">
              <h3>业务依据</h3>
              <ul>{evidenceSummary.map((item) => <li key={item}>{item}</li>)}</ul>
            </section>
          ) : null}
          {experts.length === 0 && evidenceSummary.length === 0 ? (
            <p>暂无额外专家或业务依据。</p>
          ) : null}
          {technicalLog.length > 0 ? (
            <details
              open={technicalOpen}
              onToggle={(event) => setTechnicalOpen(event.currentTarget.open)}
            >
              <summary>技术日志</summary>
              {technicalOpen ? <ul>{technicalLog.map((item) => <li key={item}>{item}</li>)}</ul> : null}
            </details>
          ) : null}
        </div>
      ) : null}
    </details>
  );
}
