import { useQuery } from "@tanstack/react-query";
import { Empty, Segmented, Spin, Tag } from "antd";
import ReactECharts from "echarts-for-react";
import { useMemo, useState } from "react";

import { getCostOverview } from "../api/costs";
import { PageHeader, Panel } from "../components/ui";
import { useThemeMode } from "../stores/theme";
import { chartBase } from "../theme/echarts";
import { CHART_COLORS } from "../theme/tokens";
import type { BrainTask, CostOverview } from "../types";

type CostView = "brain" | "agent" | "task" | "model";

const TASK_TYPE_LABEL: Record<BrainTask["type"], string> = {
  content_creation: "内容生产",
  account_diagnosis: "账号诊断",
  review_optimization: "复盘优化",
  matrix_distribution: "矩阵分发",
};

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        background: "var(--dy-surface)",
        border: "1px solid var(--dy-border-subtle)",
        borderRadius: 10,
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
  const [view, setView] = useState<CostView>("brain");
  const costQuery = useQuery({ queryKey: ["cost-overview"], queryFn: getCostOverview });
  const overview = costQuery.data;

  const activeRows = useMemo(() => buildRows(overview, view), [overview, view]);
  const chartOption = useMemo(() => {
    const sorted = [...activeRows].sort((a, b) => a.cost - b.cost);
    return {
      ...base,
      grid: { left: 8, right: 32, top: 10, bottom: 8, containLabel: true },
      tooltip: { ...base.tooltip, trigger: "axis", axisPointer: { type: "shadow" } },
      xAxis: {
        type: "value",
        ...base.valueAxis,
        axisLabel: { ...base.valueAxis.axisLabel, formatter: "${value}" },
      },
      yAxis: { type: "category", data: sorted.map((row) => row.label), ...base.categoryAxis },
      series: [
        {
          type: "bar",
          data: sorted.map((row) => row.cost),
          barWidth: 14,
          itemStyle: { color: CHART_COLORS[0], borderRadius: 4 },
          label: { show: true, position: "right", formatter: "${c}", color: base.textStyle.color },
        },
      ],
    };
  }, [activeRows, base]);
  const modelOption = useMemo(() => {
    const rows = overview?.by_model ?? [];
    return {
      ...base,
      tooltip: { ...base.tooltip, trigger: "item", formatter: "{b}: ${c}" },
      legend: { ...base.legend, bottom: 0 },
      series: [
        {
          type: "pie",
          radius: ["50%", "72%"],
          center: ["50%", "44%"],
          data: rows.map((row, index) => ({
            name: row.model,
            value: Number(row.cost.toFixed(4)),
            itemStyle: { color: CHART_COLORS[index % CHART_COLORS.length] },
          })),
          label: { color: base.textStyle.color, formatter: "{b}\n${c}" },
        },
      ],
    };
  }, [base, overview?.by_model]);
  const top = activeRows[0];

  return (
    <div>
      <PageHeader
        title="成本"
        subtitle="按运营大脑、子 Agent、任务与模型查看成本 · 数据来自真实调用记录"
      />

      {costQuery.isLoading ? (
        <div style={{ display: "grid", placeItems: "center", height: 320 }}>
          <Spin />
        </div>
      ) : !overview || overview.total_calls === 0 ? (
        <Empty description="暂无成本记录 · 产生 LLM 调用后显示" style={{ padding: "90px 0" }} />
      ) : (
        <>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
            <Tile label="总成本" value={`$${overview.total_cost.toFixed(4)}`} />
            <Tile label="调用次数" value={overview.total_calls.toLocaleString()} />
            <Tile label="Token" value={overview.total_tokens.toLocaleString()} />
            <Tile label="当前视图最高" value={top?.label ?? "-"} />
          </div>

          <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginBottom: 12 }}>
            <Segmented
              value={view}
              onChange={(value) => setView(value as CostView)}
              options={[
                { label: "运营大脑", value: "brain" },
                { label: "子 Agent", value: "agent" },
                { label: "任务", value: "task" },
                { label: "模型", value: "model" },
              ]}
            />
            <Tag style={{ marginInlineEnd: 0 }}>
              平均单次 ${safeAverage(overview.total_cost, overview.total_calls)}
            </Tag>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: 16 }}>
            <Panel title={viewTitle(view)}>
              {activeRows.length === 0 ? (
                <Empty description="当前维度暂无数据" style={{ padding: "80px 0" }} />
              ) : (
                <ReactECharts option={chartOption} style={{ height: 330 }} notMerge />
              )}
            </Panel>
            <Panel title="模型成本占比">
              {overview.by_model.length === 0 ? (
                <Empty description="暂无模型调用" style={{ padding: "80px 0" }} />
              ) : (
                <ReactECharts option={modelOption} style={{ height: 330 }} notMerge />
              )}
            </Panel>
          </div>
        </>
      )}
    </div>
  );
}

function buildRows(overview: CostOverview | undefined, view: CostView) {
  if (!overview) return [];
  if (view === "brain") {
    return overview.by_brain.map((row) => ({
      key: row.type,
      label: TASK_TYPE_LABEL[row.type],
      cost: row.cost,
      calls: row.calls,
      tokens: row.tokens,
    }));
  }
  if (view === "agent") {
    return overview.by_agent.map((row) => ({
      key: row.agent_code,
      label: row.agent_name,
      cost: row.cost,
      calls: row.calls,
      tokens: row.tokens,
    }));
  }
  if (view === "task") {
    return overview.by_task.map((row) => ({
      key: String(row.task_id),
      label: row.title,
      cost: row.cost,
      calls: row.calls,
      tokens: row.tokens,
    }));
  }
  return overview.by_model.map((row) => ({
    key: row.model,
    label: row.model,
    cost: row.cost,
    calls: row.calls,
    tokens: row.tokens,
  }));
}

function viewTitle(view: CostView) {
  if (view === "brain") return "运营大脑类型成本";
  if (view === "agent") return "子 Agent 成本";
  if (view === "task") return "任务成本";
  return "模型成本";
}

function safeAverage(total: number, count: number) {
  return count === 0 ? "0.0000" : (total / count).toFixed(4);
}
