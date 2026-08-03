import { useId, useState } from "react";

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
  const processContentId = useId();
  const technicalContentId = useId();

  return (
    <section className="tz-work-turn__process" aria-label="执行过程">
      <button
        type="button"
        aria-expanded={processOpen}
        aria-controls={processContentId}
        onClick={() => setProcessOpen((open) => !open)}
      >
        查看过程
      </button>
      {processOpen ? (
        <div id={processContentId}>
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
            <section className="tz-work-turn__technical-log">
              <button
                type="button"
                aria-expanded={technicalOpen}
                aria-controls={technicalContentId}
                onClick={() => setTechnicalOpen((open) => !open)}
              >
                技术日志
              </button>
              {technicalOpen ? <ul id={technicalContentId}>{technicalLog.map((item) => <li key={item}>{item}</li>)}</ul> : null}
            </section>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
