import type { AgentCode, AgentProfile } from "../../types";

const MONOGRAM: Record<AgentCode, string> = {
  "00-decision": "MA",
  "01-positioning": "PX",
  "02-content-director": "CD",
  "03-art-director": "AD",
  "04-video-creator": "VC",
  "05-editor": "ED",
  "06-operator": "OP",
  "07-advertiser": "GR",
  "08-customer-service": "CS",
};

export function ExpertDirectory({
  experts,
  selectedCode,
  onSelect,
}: {
  experts: AgentProfile[];
  selectedCode: AgentCode | null;
  onSelect: (code: AgentCode) => void;
}) {
  return (
    <aside className="expert-directory" aria-label="专家目录">
      <header>
        <span>专家编排</span>
        <h1>专家团</h1>
        <p>选择一位专家，直接开始一项独立工作。</p>
      </header>
      <nav>
        {experts.map((expert, index) => (
          <button
            key={expert.code}
            type="button"
            className={expert.code === selectedCode ? "is-active" : ""}
            aria-current={expert.code === selectedCode ? "page" : undefined}
            onClick={() => onSelect(expert.code)}
          >
            <span className="expert-directory__index">{String(index + 1).padStart(2, "0")}</span>
            <span className="expert-directory__monogram">{MONOGRAM[expert.code]}</span>
            <span className="expert-directory__copy">
              <strong>{expert.name}</strong>
              <small>{expert.one_liner}</small>
            </span>
            <i aria-hidden="true" />
          </button>
        ))}
      </nav>
    </aside>
  );
}

export function expertMonogram(code: AgentCode) {
  return MONOGRAM[code];
}
