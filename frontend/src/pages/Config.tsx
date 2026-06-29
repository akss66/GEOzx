import { ApiOutlined, CheckCircleFilled } from "@ant-design/icons";
import { Select, Switch, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";

import { PageHeader, Panel } from "../components/ui";
import { AGENT_CONFIGS, type AgentConfig } from "../mock/data";

const MODEL_OPTIONS = [
  { value: "deepseek-chat", label: "deepseek-chat" },
  { value: "deepseek-reasoner", label: "deepseek-reasoner" },
  { value: "—", label: "无兜底" },
];

const GATES = [
  { name: "定位审核", forced: false },
  { name: "选题审核", forced: false },
  { name: "脚本合规", forced: true },
  { name: "成片审核", forced: false },
  { name: "发布前审核", forced: true },
  { name: "大额投放（日耗 > ¥2000）", forced: true },
];

const INTEGRATIONS = [
  { name: "DeepSeek", desc: "大模型 · v1 默认", connected: true },
  { name: "Seedance", desc: "AI 视频生成", connected: false },
  { name: "抖音开放平台", desc: "发布 / 数据回流", connected: false },
  { name: "巨量千川", desc: "投流", connected: false },
];

export default function Config() {
  const columns: ColumnsType<AgentConfig> = [
    { title: "Agent", dataIndex: "name", render: (v: string, r) => (
      <span><span style={{ fontWeight: 500 }}>{v}</span>
        <span className="dy-tabular" style={{ color: "var(--dy-faint)", fontSize: 12, marginLeft: 8 }}>{r.code}</span>
      </span>
    ) },
    {
      title: "首选模型",
      dataIndex: "primary",
      width: 200,
      render: (v: string) => (
        <Select size="small" defaultValue={v} options={MODEL_OPTIONS} style={{ width: 180 }} />
      ),
    },
    {
      title: "兜底模型",
      dataIndex: "fallback",
      width: 200,
      render: (v: string) => (
        <Select size="small" defaultValue={v} options={MODEL_OPTIONS} style={{ width: 180 }} />
      ),
    },
    { title: "近 7 日调用", dataIndex: "calls7d", width: 120, align: "right", render: (v: number) => <span className="dy-tabular">{v.toLocaleString()}</span> },
    { title: "成本", dataIndex: "cost7d", width: 100, align: "right", render: (v: number) => <span className="dy-tabular">${v.toFixed(2)}</span> },
  ];

  return (
    <div>
      <PageHeader
        title="Agent 配置"
        subtitle="每个 Agent 独立绑定模型 · 质量门策略 · 外部集成（仅管理员）"
      />

      <Panel title="模型配置 · 按 Agent 切换首选 / 兜底" style={{ marginBottom: 16 }}>
        <Table rowKey="code" columns={columns} dataSource={AGENT_CONFIGS} pagination={false} size="middle" />
      </Panel>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <Panel title="质量门策略 · 强制人工开关">
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {GATES.map((g, i) => (
              <div
                key={g.name}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "11px 4px",
                  borderBottom: i < GATES.length - 1 ? "1px solid var(--dy-border-subtle)" : "none",
                }}
              >
                <span style={{ fontSize: 13.5, color: "var(--dy-text)" }}>{g.name}</span>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span style={{ fontSize: 12, color: "var(--dy-faint)" }}>
                    {g.forced ? "强制人工" : "自动通过"}
                  </span>
                  <Switch defaultChecked={g.forced} size="small" />
                </div>
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="外部集成">
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {INTEGRATIONS.map((it, i) => (
              <div
                key={it.name}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  padding: "12px 4px",
                  borderBottom: i < INTEGRATIONS.length - 1 ? "1px solid var(--dy-border-subtle)" : "none",
                }}
              >
                <ApiOutlined style={{ color: "var(--dy-muted)" }} />
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13.5, color: "var(--dy-text)" }}>{it.name}</div>
                  <div style={{ fontSize: 12, color: "var(--dy-faint)" }}>{it.desc}</div>
                </div>
                {it.connected ? (
                  <Tag color="success" icon={<CheckCircleFilled />} style={{ marginInlineEnd: 0 }}>
                    已连接
                  </Tag>
                ) : (
                  <Tag style={{ marginInlineEnd: 0 }}>待接入</Tag>
                )}
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  );
}
