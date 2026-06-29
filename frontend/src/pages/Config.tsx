import { ApiOutlined, CheckCircleFilled } from "@ant-design/icons";
import { App as AntApp, Select, Switch, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { listModelConfigs, updateModelConfig } from "../api/configuration";
import { PageHeader, Panel } from "../components/ui";
import { useAuth } from "../stores/auth";
import type { ModelConfig } from "../types";

const PRIMARY_OPTIONS = [
  { value: "deepseek-chat", label: "deepseek-chat" },
  { value: "deepseek-reasoner", label: "deepseek-reasoner" },
];

const FALLBACK_OPTIONS = [
  { value: "deepseek-chat", label: "deepseek-chat" },
  { value: "deepseek-reasoner", label: "deepseek-reasoner" },
];

// 质量门策略 / 外部集成面板：M1 E3/E9（门策略）与 E7/E8（集成）落地前先以静态展示。
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
  const { message } = AntApp.useApp();
  const qc = useQueryClient();
  const isAdmin = useAuth((s) => s.user?.role === "admin");

  const configsQuery = useQuery({ queryKey: ["model-configs"], queryFn: listModelConfigs });

  const updateMutation = useMutation({
    mutationFn: ({
      id,
      patch,
    }: {
      id: number;
      patch: { primary_model?: string; fallback_model?: string | null };
    }) => updateModelConfig(id, patch),
    onSuccess: () => {
      message.success("模型配置已更新");
      qc.invalidateQueries({ queryKey: ["model-configs"] });
    },
    onError: () => message.error("更新失败，请重试"),
  });

  const columns: ColumnsType<ModelConfig> = [
    {
      title: "Agent",
      dataIndex: "agent_code",
      render: (v: string) => (
        <span className="dy-tabular" style={{ fontWeight: 500 }}>
          {v}
        </span>
      ),
    },
    {
      title: "首选模型",
      dataIndex: "primary_model",
      width: 220,
      render: (v: string, r) => (
        <Select
          size="small"
          value={v}
          options={PRIMARY_OPTIONS}
          style={{ width: 200 }}
          disabled={!isAdmin || updateMutation.isPending}
          onChange={(val) => updateMutation.mutate({ id: r.id, patch: { primary_model: val } })}
        />
      ),
    },
    {
      title: "兜底模型",
      dataIndex: "fallback_model",
      width: 220,
      render: (v: string | null, r) => (
        <Select
          size="small"
          value={v ?? undefined}
          placeholder="无兜底"
          allowClear
          options={FALLBACK_OPTIONS}
          style={{ width: 200 }}
          disabled={!isAdmin || updateMutation.isPending}
          onChange={(val) =>
            updateMutation.mutate({ id: r.id, patch: { fallback_model: val ?? null } })
          }
        />
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title="Agent 配置"
        subtitle="每个 Agent 独立绑定模型 · 质量门策略 · 外部集成（仅管理员）"
      />

      <Panel title="模型配置 · 按 Agent 切换首选 / 兜底" style={{ marginBottom: 16 }}>
        <Table
          rowKey="id"
          columns={columns}
          dataSource={configsQuery.data ?? []}
          loading={configsQuery.isLoading}
          pagination={false}
          size="middle"
        />
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
