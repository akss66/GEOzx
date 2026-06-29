import { ArrowUpOutlined, RightOutlined, WarningFilled } from "@ant-design/icons";
import { Button, Tag, Typography } from "antd";
import ReactECharts from "echarts-for-react";
import { useNavigate } from "react-router-dom";

import { Panel, PageHeader } from "../components/ui";
import { ACTIVITY, KPI, PENDING_GATES, STAGES, CONTENT_CARDS } from "../mock/data";
import { TREND_DAYS, TREND_PLAY } from "../mock/metrics";
import { chartBase } from "../theme/echarts";
import { useThemeMode } from "../stores/theme";

function Tile({
  label,
  value,
  delta,
  tone = "default",
}: {
  label: string;
  value: string;
  delta?: string;
  tone?: "default" | "warning";
}) {
  return (
    <div
      style={{
        background: "var(--dy-surface)",
        border: "1px solid var(--dy-border-subtle)",
        borderRadius: 12,
        padding: "16px 18px",
        flex: 1,
        minWidth: 150,
      }}
    >
      <div style={{ fontSize: 12.5, color: "var(--dy-muted)", marginBottom: 8 }}>
        {label}
      </div>
      <div
        className="dy-tabular"
        style={{
          fontSize: 26,
          fontWeight: 650,
          color: tone === "warning" ? "var(--dy-warning)" : "var(--dy-text)",
          lineHeight: 1.1,
        }}
      >
        {value}
      </div>
      {delta && (
        <div style={{ fontSize: 12, color: "var(--dy-success)", marginTop: 6 }}>
          <ArrowUpOutlined /> {delta}
        </div>
      )}
    </div>
  );
}

export default function Dashboard() {
  const mode = useThemeMode((s) => s.mode);
  const navigate = useNavigate();

  const stageCounts = STAGES.map((s) => ({
    ...s,
    count: CONTENT_CARDS.filter((c) => c.stage === s.key).length,
  }));

  const trendOption = {
    ...chartBase(mode),
    grid: { left: 4, right: 8, top: 10, bottom: 4, containLabel: true },
    xAxis: {
      type: "category",
      data: TREND_DAYS,
      ...chartBase(mode).categoryAxis,
      axisLabel: { ...chartBase(mode).categoryAxis.axisLabel, interval: 6 },
    },
    yAxis: { type: "value", ...chartBase(mode).valueAxis },
    tooltip: { ...chartBase(mode).tooltip, trigger: "axis" },
    series: [
      {
        type: "line",
        data: TREND_PLAY,
        smooth: true,
        symbol: "none",
        lineStyle: { width: 2, color: "#5b8cff" },
        areaStyle: {
          color: {
            type: "linear",
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: "rgba(91,140,255,0.28)" },
              { offset: 1, color: "rgba(91,140,255,0.01)" },
            ],
          },
        },
      },
    ],
  };

  return (
    <div>
      <PageHeader
        title="指挥台"
        subtitle="全链路态势 · 数据驱动决策"
        extra={
          <Button type="primary" onClick={() => navigate("/pipeline")}>
            进入流水线
          </Button>
        }
      />

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
        <Tile label="进行中内容" value="37" delta="本周 +12" />
        <Tile label="待审质量门" value={String(KPI.pendingGates)} tone="warning" />
        <Tile label="今日已发布" value="18" delta="较昨日 +3" />
        <Tile label="7 日成本" value={`$${KPI.cost7d}`} />
        <Tile label="矩阵账号" value="1,042" />
        <Tile label="平均完播率" value={`${KPI.avgCompletion}%`} delta="+1.7pt" />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.6fr 1fr", gap: 16 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <Panel
            title="流量趋势 · 近 30 天播放量"
            extra={
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                峰值 {Math.max(...TREND_PLAY).toLocaleString()}
              </Typography.Text>
            }
          >
            <ReactECharts option={trendOption} style={{ height: 220 }} notMerge />
          </Panel>

          <Panel title="流水线吞吐 · 各阶段在产">
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {stageCounts.map((s) => (
                <div
                  key={s.key}
                  className="dy-rise"
                  onClick={() => navigate("/pipeline")}
                  style={{
                    flex: 1,
                    minWidth: 96,
                    cursor: "pointer",
                    background: "var(--dy-elevated)",
                    border: "1px solid var(--dy-border-subtle)",
                    borderRadius: 10,
                    padding: "12px 12px",
                  }}
                >
                  <div style={{ fontSize: 11, color: "var(--dy-faint)" }}>
                    {s.index}
                  </div>
                  <div style={{ fontSize: 13, color: "var(--dy-muted)", margin: "2px 0 8px" }}>
                    {s.name}
                  </div>
                  <div
                    className="dy-tabular"
                    style={{ fontSize: 22, fontWeight: 650, color: "var(--dy-text)" }}
                  >
                    {s.count}
                  </div>
                </div>
              ))}
            </div>
          </Panel>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <Panel
            title="等你判断 · 待审质量门"
            style={{ borderColor: "rgba(214,161,38,0.35)" }}
            extra={
              <Button type="link" size="small" onClick={() => navigate("/approvals")}>
                全部 <RightOutlined />
              </Button>
            }
          >
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {PENDING_GATES.slice(0, 4).map((g) => (
                <div
                  key={g.id}
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    gap: 10,
                    padding: "10px 12px",
                    background: "var(--dy-elevated)",
                    border: "1px solid var(--dy-border-subtle)",
                    borderRadius: 10,
                  }}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        fontSize: 13,
                        color: "var(--dy-text)",
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {g.title}
                    </div>
                    <div style={{ fontSize: 11.5, color: "var(--dy-faint)", marginTop: 3 }}>
                      @{g.account} · 等待 {g.waiting}
                    </div>
                  </div>
                  <Tag
                    color={g.forced ? "warning" : "default"}
                    icon={g.forced ? <WarningFilled /> : undefined}
                    style={{ marginInlineEnd: 0, flex: "none" }}
                  >
                    {g.gate}
                  </Tag>
                </div>
              ))}
            </div>
          </Panel>

          <Panel title="实时动态">
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {ACTIVITY.map((a) => (
                <div key={a.id} style={{ display: "flex", gap: 10 }}>
                  <div
                    style={{
                      width: 6,
                      height: 6,
                      borderRadius: "50%",
                      marginTop: 6,
                      flex: "none",
                      background: "var(--dy-accent)",
                    }}
                  />
                  <div>
                    <div style={{ fontSize: 12.5, color: "var(--dy-text)", lineHeight: 1.5 }}>
                      {a.text}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--dy-faint)", marginTop: 2 }}>
                      {a.time}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}
