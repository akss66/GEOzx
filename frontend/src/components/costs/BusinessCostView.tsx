import ReactECharts from "echarts-for-react";

import { useThemeMode } from "../../stores/theme";
import { chartBase } from "../../theme/echarts";
import type { CostOverview } from "../../types";
import {
  budgetStatusCopy,
  businessToolName,
  formatCost,
  formatPercent,
  safeTaskTitle,
  taskStatusCopy,
  taskTypeCopy,
} from "./costPresentation";

export function BusinessCostView({ overview }: { overview: CostOverview }) {
  const summary = overview.summary;
  const budgetUsage = Math.min(summary.budget_usage ?? 0, 100);
  const title = summary.budget_usage == null
    ? "本周期成本已按真实任务归集"
    : `本周期已使用 ${formatPercent(summary.budget_usage)} 预算`;

  return (
    <div className="cost-report">
      <section className="cost-budget-hero" data-status={summary.budget_status}>
        <div className="cost-budget-hero__amount">
          <span>{overview.scope.project_name ?? overview.scope.client_name} · 近 {overview.scope.period_days} 天</span>
          <strong>{formatCost(summary.actual_cost)}</strong>
          <h2>{title}</h2>
          <p>{budgetNarrative(overview)}</p>
        </div>
        <div className="cost-budget-hero__budget">
          <div><span>周期预算</span><strong>{summary.budget == null ? "未设置" : formatCost(summary.budget)}</strong></div>
          <div><span>预算余额</span><strong>{summary.remaining_budget == null ? "-" : formatCost(summary.remaining_budget)}</strong></div>
          <div className="cost-budget-progress" aria-label={`预算使用率 ${formatPercent(summary.budget_usage)}`}>
            <i style={{ width: `${budgetUsage}%` }} />
          </div>
          <small>{budgetStatusCopy(summary.budget_status)}</small>
        </div>
      </section>

      <section className="cost-metric-rail" aria-label="运营成本摘要">
        <Metric label="任务" value={summary.task_count.toLocaleString()} note="进入成本账本" />
        <Metric label="专家调用" value={summary.agent_calls.toLocaleString()} note="已完成与处理中" />
        <Metric label="工具调用" value={summary.tool_calls.toLocaleString()} note="含发布准备与素材工具" />
        <Metric label="失败动作" value={summary.failed_operations.toLocaleString()} note="需要复核或重试" alert={summary.failed_operations > 0} />
      </section>

      <div className="cost-report-grid">
        <section className="cost-section cost-section--trend">
          <SectionHeader title="成本趋势" meta="真实任务与工具成本" />
          {overview.daily.length === 0 ? (
            <InlineEmpty>当前周期还没有产生可计量成本。</InlineEmpty>
          ) : (
            <BusinessTrend rows={overview.daily} />
          )}
        </section>
        <section className="cost-section">
          <SectionHeader title="投入结构" meta="按专家与工具归因" />
          <AttributionList overview={overview} />
        </section>
      </div>

      {overview.scope.project_id == null ? <ProjectLedger overview={overview} /> : null}
      <TaskLedger overview={overview} />
    </div>
  );
}

function Metric({ label, value, note, alert = false }: { label: string; value: string; note: string; alert?: boolean }) {
  return <div data-alert={alert || undefined}><span>{label}</span><strong>{value}</strong><small>{note}</small></div>;
}

function SectionHeader({ title, meta }: { title: string; meta: string }) {
  return <header className="cost-section__header"><h3>{title}</h3><span>{meta}</span></header>;
}

function BusinessTrend({ rows }: { rows: CostOverview["daily"] }) {
  const mode = useThemeMode((state) => state.mode);
  const base = chartBase(mode);
  const option = {
    ...base,
    grid: { left: 12, right: 16, top: 22, bottom: 8, containLabel: true },
    tooltip: { ...base.tooltip, trigger: "axis", valueFormatter: (value: number) => formatCost(value) },
    xAxis: { type: "category", data: rows.map((row) => row.date.slice(5)), ...base.categoryAxis },
    yAxis: { type: "value", ...base.valueAxis, axisLabel: { formatter: "${value}" } },
    series: [{
      type: "line",
      data: rows.map((row) => row.cost),
      smooth: 0.28,
      symbolSize: 7,
      lineStyle: { width: 2, color: "#C9161D" },
      itemStyle: { color: "#C9161D" },
      areaStyle: { color: "rgba(201, 22, 29, 0.08)" },
    }],
  };
  return <ReactECharts option={option} style={{ height: 260 }} notMerge />;
}

function AttributionList({ overview }: { overview: CostOverview }) {
  const rows = [
    ...overview.by_agent.map((row) => ({ key: `agent-${row.agent_code}`, label: row.agent_name, type: "专家", cost: row.cost, calls: row.calls })),
    ...overview.by_tool.map((row) => ({ key: `tool-${row.tool_code}`, label: businessToolName(row.tool_code, row.tool_name), type: "工具", cost: row.cost, calls: row.calls })),
  ].sort((a, b) => b.cost - a.cost).slice(0, 6);
  if (rows.length === 0) return <InlineEmpty>暂无专家或工具成本。</InlineEmpty>;
  return <div className="cost-attribution-list">{rows.map((row) => (
    <div key={row.key}>
      <span><i>{row.type}</i><strong>{row.label}</strong><small>{row.calls} 次</small></span>
      <b>{formatCost(row.cost)}</b>
    </div>
  ))}</div>;
}

function TaskLedger({ overview }: { overview: CostOverview }) {
  return (
    <section className="cost-section cost-ledger">
      <SectionHeader title="任务成本账本" meta={`${overview.by_task.length} 项真实任务`} />
      {overview.by_task.length === 0 ? <InlineEmpty>当前周期还没有任务成本记录。</InlineEmpty> : (
        <div className="cost-table-wrap"><table><thead><tr><th>任务</th><th>类型</th><th>状态</th><th>专家</th><th>工具</th><th>成本</th></tr></thead>
          <tbody>{overview.by_task.map((row) => <tr key={row.task_id}>
            <td><strong>{safeTaskTitle(row.title, row.task_id)}</strong><small>#{row.task_id}</small></td>
            <td>{taskTypeCopy(row.type)}</td><td>{taskStatusCopy(row.status)}</td>
            <td>{row.agent_calls}</td><td>{row.tool_calls}</td><td><b>{formatCost(row.cost)}</b></td>
          </tr>)}</tbody></table></div>
      )}
    </section>
  );
}

function ProjectLedger({ overview }: { overview: CostOverview }) {
  return (
    <section className="cost-section cost-ledger">
      <SectionHeader title="项目预算" meta={`${overview.by_project.length} 个可访问项目`} />
      <div className="cost-table-wrap"><table><thead><tr><th>项目</th><th>任务</th><th>实际成本</th><th>预算</th><th>使用率</th><th>状态</th></tr></thead>
        <tbody>{overview.by_project.map((row) => <tr key={row.project_id}>
          <td><strong>{row.project_name}</strong></td><td>{row.task_count}</td><td>{formatCost(row.actual_cost)}</td>
          <td>{row.budget == null ? "未设置" : formatCost(row.budget)}</td><td>{formatPercent(row.budget_usage)}</td><td>{budgetStatusCopy(row.budget_status)}</td>
        </tr>)}</tbody></table></div>
    </section>
  );
}

function InlineEmpty({ children }: { children: string }) {
  return <p className="cost-inline-empty">{children}</p>;
}

function budgetNarrative(overview: CostOverview) {
  const { summary } = overview;
  if (summary.budget_status === "exceeded") return `已超出预算 ${formatCost(Math.abs(summary.remaining_budget ?? 0))}，建议暂停低优先级任务并检查失败重试。`;
  if (summary.budget_status === "warning") return `预算进入预警区，剩余 ${formatCost(summary.remaining_budget ?? 0)}，建议优先保留高价值任务。`;
  if (summary.budget_status === "healthy") return `预算仍在健康区间，当前剩余 ${formatCost(summary.remaining_budget ?? 0)}。`;
  return "尚未设置项目月度预算；成本记录仍会持续按任务和专家归集。";
}
