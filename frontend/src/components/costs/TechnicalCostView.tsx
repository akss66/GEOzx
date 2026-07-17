import type { TechnicalCostOverview } from "../../types";
import { formatCost } from "./costPresentation";

export function TechnicalCostView({ overview }: { overview: TechnicalCostOverview }) {
  const summary = overview.summary;
  return (
    <div className="cost-report cost-technical">
      <section className="cost-technical-hero">
        <div><span>TECHNICAL LEDGER · 近 {overview.period_days} 天</span><h2>模型基础设施运行账本</h2><p>这里仅供系统管理员诊断供应商、模型、Token、延迟和失败兜底。</p></div>
        <strong>{formatCost(summary.total_cost)}</strong>
      </section>
      <section className="cost-metric-rail" aria-label="技术成本摘要">
        <Metric label="模型调用" value={summary.total_calls.toLocaleString()} note="含失败尝试" />
        <Metric label="Token" value={summary.total_tokens.toLocaleString()} note="提示与生成合计" />
        <Metric label="平均延迟" value={`${summary.average_latency_ms} ms`} note="所有调用平均" />
        <Metric label="失败 / 兜底" value={summary.fallback_attempts.toLocaleString()} note={`${summary.failed_calls} 次失败`} alert={summary.failed_calls > 0} />
      </section>
      <div className="cost-report-grid">
        <ProviderLedger overview={overview} />
        <AgentLedger overview={overview} />
      </div>
      <ModelLedger overview={overview} />
    </div>
  );
}

function Metric({ label, value, note, alert = false }: { label: string; value: string; note: string; alert?: boolean }) {
  return <div data-alert={alert || undefined}><span>{label}</span><strong>{value}</strong><small>{note}</small></div>;
}

function Header({ title, meta }: { title: string; meta: string }) {
  return <header className="cost-section__header"><h3>{title}</h3><span>{meta}</span></header>;
}

function ProviderLedger({ overview }: { overview: TechnicalCostOverview }) {
  return <section className="cost-section"><Header title="供应商运行" meta="组织级技术账本" />
    {overview.by_provider.length === 0 ? <p className="cost-inline-empty">暂无供应商调用。</p> : <div className="cost-attribution-list">{overview.by_provider.map((row) => <div key={row.provider}>
      <span><i>供应商</i><strong>{row.provider}</strong><small>{row.calls} 次 · {row.average_latency_ms} ms</small></span>
      <b>{formatCost(row.cost)}</b>
    </div>)}</div>}
  </section>;
}

function AgentLedger({ overview }: { overview: TechnicalCostOverview }) {
  return <section className="cost-section"><Header title="Agent 调用" meta="按内部职责代码" />
    {overview.by_agent.length === 0 ? <p className="cost-inline-empty">暂无 Agent 调用。</p> : <div className="cost-attribution-list">{overview.by_agent.slice(0, 8).map((row) => <div key={row.agent_code}>
      <span><i>Agent</i><strong>{row.agent_code}</strong><small>{row.calls} 次 · {row.tokens.toLocaleString()} Token</small></span>
      <b>{formatCost(row.cost)}</b>
    </div>)}</div>}
  </section>;
}

function ModelLedger({ overview }: { overview: TechnicalCostOverview }) {
  return <section className="cost-section cost-ledger"><Header title="模型调用明细" meta="仅管理员可见" />
    {overview.by_model.length === 0 ? <p className="cost-inline-empty">暂无模型调用记录。</p> : <div className="cost-table-wrap"><table><thead><tr><th>模型</th><th>供应商</th><th>调用</th><th>Token</th><th>平均延迟</th><th>失败</th><th>成本</th></tr></thead>
      <tbody>{overview.by_model.map((row) => <tr key={`${row.provider}-${row.model}`}><td><strong>{row.model}</strong></td><td>{row.provider}</td><td>{row.calls}</td><td>{row.tokens.toLocaleString()}</td><td>{row.average_latency_ms} ms</td><td>{row.failed_calls}</td><td><b>{formatCost(row.cost)}</b></td></tr>)}</tbody></table></div>}
  </section>;
}
