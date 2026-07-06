import { ApiOutlined, SendOutlined, SettingOutlined } from "@ant-design/icons";
import { useMutation, useQuery } from "@tanstack/react-query";
import { App as AntApp, Button, Empty, Input, Progress, Space, Tabs, Tag } from "antd";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { invokeAgent, listAgents } from "../api/agents";
import { Panel, PageHeader } from "../components/ui";
import { useAuth } from "../stores/auth";
import { silverTagStyle } from "../theme/styles";
import type { AgentCode, AgentGroup, AgentProfile, AgentToolCallSummaryItem } from "../types";

const GROUPS: AgentGroup[] = ["control", "strategy", "creative", "operation", "growth", "feedback"];

const AGENT_GROUP_LABEL: Record<AgentGroup, string> = {
  control: "主控",
  strategy: "策略",
  creative: "创作",
  operation: "运营",
  growth: "增长",
  feedback: "反馈",
};

export default function ExpertTeam() {
  const { message } = AntApp.useApp();
  const user = useAuth((state) => state.user);
  const isAdmin = user?.role === "admin";
  const agentsQuery = useQuery({ queryKey: ["agents"], queryFn: listAgents });
  const agents = useMemo(() => agentsQuery.data ?? [], [agentsQuery.data]);
  const [selectedCode, setSelectedCode] = useState<AgentCode>("00-decision");
  const [directGoal, setDirectGoal] = useState("");
  const [localAgent, setLocalAgent] = useState<AgentProfile | null>(null);

  const invokeMutation = useMutation({
    mutationFn: ({ code, goal }: { code: AgentCode; goal: string }) => invokeAgent(code, goal),
    onSuccess: (agent) => {
      setLocalAgent(agent);
      setDirectGoal("");
      message.success("已创建子 Agent 调用，结果将回流运营大脑");
    },
  });

  const selected = useMemo(() => {
    if (localAgent?.code === selectedCode) return localAgent;
    return agents.find((agent) => agent.code === selectedCode) ?? agents[0] ?? null;
  }, [agents, localAgent, selectedCode]);

  const runDirectCall = () => {
    const goal = directGoal.trim();
    if (!selected || !goal) {
      message.warning("请输入希望该专家处理的目标");
      return;
    }
    invokeMutation.mutate({ code: selected.code, goal });
  };

  return (
    <div>
      <PageHeader
        title="专家团"
        subtitle="运营大脑与 8 个专业子 Agent 的状态、能力、当前任务和配置"
        extra={<Tag style={{ marginInlineEnd: 0, ...silverTagStyle }}>列表 + 详情面板</Tag>}
      />
      <div style={{ display: "grid", gridTemplateColumns: "360px minmax(0, 1fr)", gap: 16 }}>
        <Panel title="专家列表">
          {agents.length === 0 && !agentsQuery.isLoading ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无专家配置" />
          ) : (
            <div style={{ display: "grid", gap: 14 }}>
              {GROUPS.map((group) => {
                const groupAgents = agents.filter((agent) => agent.group === group);
                if (groupAgents.length === 0) return null;
                return (
                  <section key={group} style={{ display: "grid", gap: 8 }}>
                    <div style={{ fontSize: 12, color: "var(--dy-faint)" }}>
                      {AGENT_GROUP_LABEL[group]}
                    </div>
                    {groupAgents.map((agent) => (
                      <AgentListItem
                        key={agent.code}
                        agent={agent}
                        selected={agent.code === selected?.code}
                        onSelect={() => setSelectedCode(agent.code)}
                      />
                    ))}
                  </section>
                );
              })}
            </div>
          )}
        </Panel>
        {selected ? (
          <AgentDetail
            agent={selected}
            isAdmin={isAdmin}
            directGoal={directGoal}
            invoking={invokeMutation.isPending}
            onGoalChange={setDirectGoal}
            onInvoke={runDirectCall}
          />
        ) : (
          <Panel>
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="请选择一个专家" />
          </Panel>
        )}
      </div>
    </div>
  );
}

function AgentListItem({
  agent,
  selected,
  onSelect,
}: {
  agent: AgentProfile;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className="dy-rise"
      onClick={onSelect}
      style={{
        width: "100%",
        border: `1px solid ${selected ? "var(--dy-accent)" : "var(--dy-border-subtle)"}`,
        background: selected ? "var(--dy-accent-wash)" : "var(--dy-elevated)",
        borderRadius: 10,
        padding: "11px 12px",
        textAlign: "left",
        cursor: "pointer",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--dy-text)" }}>
            {agent.name}
          </div>
          <div style={{ fontSize: 12, color: "var(--dy-muted)", marginTop: 4, lineHeight: 1.45 }}>
            {agent.one_liner}
          </div>
        </div>
        <Tag style={{ marginInlineEnd: 0, flex: "none" }}>{agent.automation_level}</Tag>
      </div>
    </button>
  );
}

function AgentDetail({
  agent,
  isAdmin,
  directGoal,
  invoking,
  onGoalChange,
  onInvoke,
}: {
  agent: AgentProfile;
  isAdmin: boolean;
  directGoal: string;
  invoking: boolean;
  onGoalChange: (value: string) => void;
  onInvoke: () => void;
}) {
  return (
    <div style={{ display: "grid", gap: 16, minWidth: 0 }}>
      <Panel
        title={agent.name}
        extra={
          <Space size={8}>
            <Tag style={{ marginInlineEnd: 0, ...silverTagStyle }}>{AGENT_GROUP_LABEL[agent.group]}</Tag>
            <Tag style={{ marginInlineEnd: 0 }}>{agent.model}</Tag>
          </Space>
        }
      >
        {agent.current_task ? (
          <div style={{ display: "grid", gap: 12 }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 14 }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 15, fontWeight: 600, color: "var(--dy-text)" }}>
                  {agent.current_task.title}
                </div>
                <div style={{ fontSize: 12, color: "var(--dy-muted)", marginTop: 4 }}>
                  {agent.current_task.project_name} · {agent.current_task.account_group_name} ·{" "}
                  {agent.current_task.platforms.join(" / ")}
                </div>
              </div>
              <RiskTag risk={agent.current_task.risk_level} />
            </div>
            <Progress
              percent={agent.current_task.progress}
              strokeColor="var(--dy-accent)"
              trailColor="var(--dy-border)"
            />
            <div style={{ fontSize: 13, color: "var(--dy-text)", lineHeight: 1.6 }}>
              {agent.current_task.output_summary}
            </div>
            {agent.current_task.blockers.length > 0 && (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {agent.current_task.blockers.map((blocker) => (
                  <Tag key={blocker} color="warning" style={{ marginInlineEnd: 0 }}>
                    {blocker}
                  </Tag>
                ))}
              </div>
            )}
            <div style={{ fontSize: 12.5, color: "var(--dy-muted)" }}>
              下一步：{agent.current_task.next_action}
            </div>
          </div>
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有运营大脑分配的任务" />
        )}
      </Panel>

      <ToolSummaryPanel agent={agent} />

      <Panel>
        <Tabs
          items={[
            {
              key: "capability",
              label: "能力",
              children: <CapabilityTab agent={agent} />,
            },
            {
              key: "history",
              label: "历史产出",
              children: <HistoryTab agent={agent} />,
            },
            {
              key: "config",
              label: "配置",
              children: <ConfigTab agent={agent} isAdmin={isAdmin} />,
            },
            {
              key: "invoke",
              label: "直接调用",
              children: (
                <DirectInvokeTab
                  agent={agent}
                  directGoal={directGoal}
                  invoking={invoking}
                  onGoalChange={onGoalChange}
                  onInvoke={onInvoke}
                />
              ),
            },
          ]}
        />
      </Panel>
    </div>
  );
}

function ToolSummaryPanel({ agent }: { agent: AgentProfile }) {
  const navigate = useNavigate();
  const summary = agent.tool_summary;
  const recentCalls = useMemo(
    () => [...summary.recent_calls].sort(compareToolCalls),
    [summary.recent_calls],
  );
  const hasPendingApproval = summary.pending_approvals > 0;

  return (
    <Panel
      title="工具账本"
      extra={
        <Space size={8}>
          <Tag style={{ marginInlineEnd: 0 }}>调用 {summary.total_calls}</Tag>
          <Tag color={summary.pending_approvals > 0 ? "warning" : "default"} style={{ marginInlineEnd: 0 }}>
            待审批 {summary.pending_approvals}
          </Tag>
          <Tag color={summary.failed_calls > 0 ? "error" : "default"} style={{ marginInlineEnd: 0 }}>
            异常 {summary.failed_calls}
          </Tag>
        </Space>
      }
    >
      {summary.recent_calls.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无工具调用记录" />
      ) : (
        <div style={{ display: "grid", gap: 12 }}>
          {hasPendingApproval && (
            <div
              style={{
                display: "flex",
                gap: 12,
                alignItems: "center",
                justifyContent: "space-between",
                padding: "12px 14px",
                borderRadius: 16,
                border: "1px solid rgba(166,106,0,0.28)",
                background: "rgba(166,106,0,0.07)",
              }}
            >
              <div style={{ minWidth: 0 }}>
                <strong style={{ display: "block", color: "var(--dy-text)", fontSize: 13 }}>
                  有工具调用等待人工审批
                </strong>
                <span style={{ color: "var(--dy-muted)", fontSize: 12.5 }}>
                  专家已准备好执行结果，高风险动作需要先进入人工审批。
                </span>
              </div>
              <Button size="small" onClick={() => navigate("/approvals")}>
                去审批
              </Button>
            </div>
          )}

          {recentCalls.map((toolCall) => {
            const status = toolStatusMeta(toolCall.status);
            return (
              <div
                key={toolCall.id}
                style={{
                  display: "grid",
                  gap: 8,
                  padding: "12px 14px",
                  borderRadius: 18,
                  border: `1px solid ${status.border}`,
                  background: "var(--dy-elevated)",
                }}
              >
                <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <strong style={{ color: "var(--dy-text)", fontSize: 13.5 }}>{toolCall.tool_name}</strong>
                  <Tag color={status.color} style={{ marginInlineEnd: 0 }}>
                    {status.label}
                  </Tag>
                  <Tag style={{ marginInlineEnd: 0 }}>{permissionLabel(toolCall.permission_mode)}</Tag>
                  {toolCall.requires_human_confirmation && (
                    <Tag color="warning" style={{ marginInlineEnd: 0 }}>
                      人工门
                    </Tag>
                  )}
                  <span style={{ marginLeft: "auto", color: "var(--dy-faint)", fontSize: 12 }}>
                    任务 #{toolCall.task_id} · {relativeTime(toolCall.created_at)}
                  </span>
                </div>
                <ToolLine label="输入" value={toolCall.input_summary || "暂无输入摘要"} />
                <ToolLine label="输出" value={toolCall.output_summary || "暂无输出摘要"} />
                {toolCall.error && <ToolLine label="异常" value={toolCall.error} />}
              </div>
            );
          })}
        </div>
      )}
    </Panel>
  );
}

function ToolLine({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "38px minmax(0, 1fr)", gap: 8 }}>
      <span style={{ color: "var(--dy-faint)", fontSize: 12 }}>{label}</span>
      <span style={{ color: "var(--dy-text)", fontSize: 12.5, lineHeight: 1.45 }}>{value}</span>
    </div>
  );
}

function compareToolCalls(a: AgentToolCallSummaryItem, b: AgentToolCallSummaryItem) {
  const byPriority = toolStatusPriority(a.status) - toolStatusPriority(b.status);
  if (byPriority !== 0) return byPriority;
  return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
}

function toolStatusPriority(status: string) {
  if (status === "waiting_approval") return 0;
  if (status === "failed" || status === "blocked") return 1;
  if (status === "running") return 2;
  if (status === "success") return 3;
  return 4;
}

function toolStatusMeta(status: string): { label: string; color: "success" | "warning" | "processing" | "error" | "default"; border: string } {
  if (status === "success") {
    return { label: "已完成", color: "success", border: "var(--dy-border-subtle)" };
  }
  if (status === "waiting_approval") {
    return { label: "待人工审批", color: "warning", border: "rgba(166,106,0,0.34)" };
  }
  if (status === "running") {
    return { label: "执行中", color: "processing", border: "var(--dy-border-strong)" };
  }
  if (status === "failed") {
    return { label: "失败", color: "error", border: "rgba(196,61,75,0.36)" };
  }
  if (status === "blocked") {
    return { label: "已阻塞", color: "error", border: "rgba(196,61,75,0.36)" };
  }
  if (status === "planned") {
    return { label: "已计划", color: "default", border: "var(--dy-border-subtle)" };
  }
  if (status === "skipped") {
    return { label: "已跳过", color: "default", border: "var(--dy-border-subtle)" };
  }
  return { label: status, color: "default", border: "var(--dy-border-subtle)" };
}

function permissionLabel(mode: string) {
  if (mode === "auto") return "自动";
  if (mode === "confirm") return "需确认";
  if (mode === "manual") return "手动";
  return mode;
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const min = Math.floor(diff / 60000);
  if (min < 1) return "刚刚";
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  return `${Math.floor(hr / 24)} 天前`;
}

function CapabilityTab({ agent }: { agent: AgentProfile }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: 12 }}>
      <MiniSection title="一句话能力" items={[agent.one_liner]} />
      <MiniSection title="可调用工具" items={agent.tools} />
      <MiniSection title="典型任务" items={agent.typical_tasks} />
      <MiniSection title="标准输出" items={agent.standard_outputs} />
    </div>
  );
}

function HistoryTab({ agent }: { agent: AgentProfile }) {
  const history = [
    `${agent.name} · 最近交付物 v3 · 质量评价 A-`,
    "上次打回原因：表达太泛，缺少平台规则引用",
    "最近一次被运营大脑调用：2026-07-01 10:22",
  ];
  return <MiniSection title="最近记录" items={history} />;
}

function ConfigTab({ agent, isAdmin }: { agent: AgentProfile; isAdmin: boolean }) {
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <MiniSection
        title="模型与自动化"
        items={[
          `首选模型：${agent.model}`,
          `兜底模型：${agent.fallback_model ?? "未设置"}`,
          `自动化等级：${agent.automation_level}`,
        ]}
      />
      <div
        style={{
          border: "1px solid var(--dy-border-subtle)",
          borderRadius: 10,
          padding: 12,
          background: "var(--dy-elevated)",
          color: "var(--dy-muted)",
          fontSize: 12.5,
          lineHeight: 1.55,
        }}
      >
        <SettingOutlined /> {isAdmin ? "管理员可在后续版本调整模型、提示词和工具权限。" : "普通成员当前仅可查看配置。"}
      </div>
    </div>
  );
}

function DirectInvokeTab({
  agent,
  directGoal,
  invoking,
  onGoalChange,
  onInvoke,
}: {
  agent: AgentProfile;
  directGoal: string;
  invoking: boolean;
  onGoalChange: (value: string) => void;
  onInvoke: () => void;
}) {
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div style={{ fontSize: 12.5, color: "var(--dy-muted)", lineHeight: 1.55 }}>
        <ApiOutlined /> 直接调用会保留权限与审计边界，结果必须回流给运营大脑，不能形成孤立产出。
      </div>
      <Input.TextArea
        value={directGoal}
        onChange={(event) => onGoalChange(event.target.value)}
        rows={3}
        placeholder={`让${agent.name}处理一个明确目标...`}
      />
      <div>
        <Button type="primary" icon={<SendOutlined />} loading={invoking} onClick={onInvoke}>
          创建调用
        </Button>
      </div>
    </div>
  );
}

function MiniSection({ title, items }: { title: string; items: string[] }) {
  return (
    <div
      style={{
        border: "1px solid var(--dy-border-subtle)",
        borderRadius: 10,
        padding: 12,
        background: "var(--dy-elevated)",
      }}
    >
      <div style={{ fontSize: 12, color: "var(--dy-faint)", marginBottom: 8 }}>{title}</div>
      <div style={{ display: "grid", gap: 7 }}>
        {items.map((item) => (
          <div key={item} style={{ fontSize: 12.5, color: "var(--dy-text)", lineHeight: 1.5 }}>
            {item}
          </div>
        ))}
      </div>
    </div>
  );
}

function RiskTag({ risk }: { risk: "low" | "medium" | "high" }) {
  const label = { low: "低风险", medium: "中风险", high: "高风险" }[risk];
  const color = {
    low: "var(--dy-success)",
    medium: "var(--dy-warning)",
    high: "var(--dy-error)",
  }[risk];
  return (
    <Tag style={{ marginInlineEnd: 0, color, borderColor: "var(--dy-border)", background: "transparent" }}>
      {label}
    </Tag>
  );
}
