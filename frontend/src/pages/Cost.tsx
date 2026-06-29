import ReactECharts from "echarts-for-react";

import { PageHeader, Panel } from "../components/ui";
import { AGENT_CONFIGS } from "../mock/data";
import { useThemeMode } from "../stores/theme";
import { chartBase } from "../theme/echarts";
import { CHART_COLORS } from "../theme/tokens";

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        background: "var(--dy-surface)",
        border: "1px solid var(--dy-border-subtle)",
        borderRadius: 12,
        padding: "16px 18px",
        flex: 1,
        minWidth: 160,
      }}
    >
      <div style={{ fontSize: 12.5, color: "var(--dy-muted)", marginBottom: 8 }}>{label}</div>
      <div className="dy-tabular" style={{ fontSize: 24, fontWeight: 650, color: "var(--dy-text)" }}>
        {value}
      </div>
    </div>
  );
}

export default function Cost() {
  const mode = useThemeMode((s) => s.mode);
  const base = chartBase(mode);

  const total = AGENT_CONFIGS.reduce((s, a) => s + a.cost7d, 0);
  const calls = AGENT_CONFIGS.reduce((s, a) => s + a.calls7d, 0);
  const top = [...AGENT_CONFIGS].sort((a, b) => b.cost7d - a.cost7d)[0];

  const sorted = [...AGENT_CONFIGS].sort((a, b) => a.cost7d - b.cost7d);
  const byAgent = {
    ...base,
    grid: { left: 8, right: 24, top: 10, bottom: 8, containLabel: true },
    tooltip: { ...base.tooltip, trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: { type: "value", ...base.valueAxis, axisLabel: { ...base.valueAxis.axisLabel, formatter: "${value}" } },
    yAxis: { type: "category", data: sorted.map((a) => a.name), ...base.categoryAxis },
    series: [
      {
        type: "bar",
        data: sorted.map((a) => a.cost7d),
        barWidth: 14,
        itemStyle: { color: CHART_COLORS[0], borderRadius: 4 },
        label: { show: true, position: "right", formatter: "${c}", color: base.textStyle.color },
      },
    ],
  };

  const reasonerCost = AGENT_CONFIGS.filter((a) => a.primary.includes("reasoner")).reduce((s, a) => s + a.cost7d, 0);
  const byModel = {
    ...base,
    tooltip: { ...base.tooltip, trigger: "item", formatter: "{b}: ${c}" },
    legend: { ...base.legend, bottom: 0 },
    series: [
      {
        type: "pie",
        radius: ["50%", "72%"],
        center: ["50%", "44%"],
        data: [
          { name: "deepseek-chat", value: Number((total - reasonerCost).toFixed(2)), itemStyle: { color: CHART_COLORS[0] } },
          { name: "deepseek-reasoner", value: Number(reasonerCost.toFixed(2)), itemStyle: { color: CHART_COLORS[4] } },
        ],
        label: { color: base.textStyle.color, formatter: "{b}\n${c}" },
      },
    ],
  };

  return (
    <div>
      <PageHeader title="成本" subtitle="每次 LLM 调用记录模型 / Token / 成本 · 近 7 日" />

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
        <Tile label="7 日总成本" value={`$${total.toFixed(2)}`} />
        <Tile label="调用次数" value={calls.toLocaleString()} />
        <Tile label="平均单次" value={`$${(total / calls).toFixed(4)}`} />
        <Tile label="成本最高 Agent" value={top.name} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: 16 }}>
        <Panel title="各 Agent 成本（近 7 日）">
          <ReactECharts option={byAgent} style={{ height: 320 }} notMerge />
        </Panel>
        <Panel title="模型成本占比">
          <ReactECharts option={byModel} style={{ height: 320 }} notMerge />
        </Panel>
      </div>
    </div>
  );
}
