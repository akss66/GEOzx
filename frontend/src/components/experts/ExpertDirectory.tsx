import type { AgentCode, AgentProfile } from "../../types";
import { AgentAvatar } from "../agents/AgentAvatar";

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
            <AgentAvatar
              code={expert.code}
              className="expert-directory__monogram"
              label={expert.name}
            />
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
