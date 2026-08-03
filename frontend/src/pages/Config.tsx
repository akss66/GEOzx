import {
  CheckOutlined,
  ClockCircleOutlined,
  SaveOutlined,
  SafetyCertificateOutlined,
  ToolOutlined,
} from "@ant-design/icons";
import { App as AntApp, Button, Input, Modal, Skeleton, Switch } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { listAgentManagement, updateAgentManagement } from "../api/agents";
import { presentApiError } from "../api/errors";
import { OperationalState } from "../components/ui";
import type {
  AgentCode,
  AgentManagement,
  ToolPermissionMode,
  UpdateAgentManagementInput,
} from "../types";

const GROUP_LABEL: Record<AgentManagement["group"], string> = {
  control: "主控",
  strategy: "策略",
  creative: "创作",
  operation: "运营",
  growth: "增长",
  feedback: "反馈",
};

const PERMISSION_OPTIONS: Array<{ value: ToolPermissionMode; label: string }> = [
  { value: "auto", label: "自动执行" },
  { value: "confirm", label: "执行前确认" },
  { value: "manual", label: "仅人工执行" },
  { value: "disabled", label: "停用工具" },
];

type Draft = UpdateAgentManagementInput;

export default function Config() {
  const { message } = AntApp.useApp();
  const queryClient = useQueryClient();
  const [selectedCode, setSelectedCode] = useState<AgentCode | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [pendingCode, setPendingCode] = useState<AgentCode | null>(null);

  const expertsQuery = useQuery({
    queryKey: ["agent-management"],
    queryFn: listAgentManagement,
  });
  const experts = useMemo(() => expertsQuery.data ?? [], [expertsQuery.data]);
  const selected = useMemo(
    () => experts.find((expert) => expert.code === selectedCode) ?? experts[0] ?? null,
    [experts, selectedCode],
  );

  useEffect(() => {
    if (!selectedCode && experts[0]) setSelectedCode(experts[0].code);
  }, [experts, selectedCode]);

  useEffect(() => {
    if (selected) setDraft(toDraft(selected));
  }, [selected]);

  const updateMutation = useMutation({
    mutationFn: ({ code, input }: { code: AgentCode; input: Draft }) =>
      updateAgentManagement(code, input),
    onSuccess: (saved) => {
      queryClient.setQueryData<AgentManagement[]>(["agent-management"], (current = []) =>
        current.map((item) => (item.code === saved.code ? saved : item)),
      );
      setDraft(toDraft(saved));
      message.success("专家配置已保存并应用到运行时");
    },
    onError: () => message.error("保存失败，请检查配置后重试"),
  });

  const enabledCount = experts.filter((expert) => expert.enabled).length;
  const confirmToolCount = experts.reduce(
    (total, expert) => total + Object.values(expert.tool_permissions).filter(
      (mode) => mode === "confirm" || mode === "manual",
    ).length,
    0,
  );
  const dirty = Boolean(selected && draft && JSON.stringify(draft) !== JSON.stringify(toDraft(selected)));

  useEffect(() => {
    if (!dirty) return;
    const preventUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", preventUnload);
    return () => window.removeEventListener("beforeunload", preventUnload);
  }, [dirty]);

  const requestSelectedCode = (code: AgentCode) => {
    if (code === selected.code) return;
    if (dirty) {
      setPendingCode(code);
      return;
    }
    setSelectedCode(code);
  };

  if (expertsQuery.isError) {
    const failure = presentApiError(expertsQuery.error, "专家配置暂时不可用。");
    return (
      <main className="expert-admin">
        <OperationalState
          kind="error"
          title="专家配置加载失败"
          description={failure.message}
          diagnostic={failure.diagnostic}
          actionLabel="重新加载"
          onAction={() => void expertsQuery.refetch()}
        />
      </main>
    );
  }

  if (expertsQuery.isLoading || !selected || !draft) {
    return <div className="expert-admin-loading"><Skeleton active paragraph={{ rows: 12 }} /></div>;
  }

  const save = () => {
    if (!draft.responsibility.trim()) {
      message.warning("请填写专家职责");
      return;
    }
    updateMutation.mutate({ code: selected.code, input: draft });
  };

  return (
    <main className="expert-admin">
      <header className="expert-admin__masthead">
        <div>
          <span>专家治理</span>
          <h1>专家管理</h1>
          <p>统一定义专家职责、执行边界、工具权限与质量门。</p>
        </div>
        <dl aria-label="专家管理概览">
          <div><dt>专家</dt><dd>{experts.length}</dd></div>
          <div><dt>已启用</dt><dd>{enabledCount}</dd></div>
          <div><dt>人工权限点</dt><dd>{confirmToolCount}</dd></div>
        </dl>
      </header>

      <div className="expert-admin__workspace">
        <aside className="expert-admin__directory">
          <header><strong>专家目录</strong><span>{experts.length}</span></header>
          <nav aria-label="专家目录">
            {experts.map((expert, index) => (
              <button
                key={expert.code}
                type="button"
                className={expert.code === selected.code ? "is-active" : ""}
                aria-label={`${expert.name}，${expert.enabled ? "已启用" : "已停用"}`}
                onClick={() => requestSelectedCode(expert.code)}
              >
                <span className="expert-admin__index">{String(index).padStart(2, "0")}</span>
                <span className="expert-admin__avatar">{monogram(expert)}</span>
                <span className="expert-admin__directory-copy">
                  <strong>{expert.name}</strong>
                  <small>{GROUP_LABEL[expert.group]} · {expert.available_tools.length} 个工具</small>
                </span>
                <i className={expert.enabled ? "is-online" : ""} aria-hidden="true" />
              </button>
            ))}
          </nav>
        </aside>

        <section className="expert-admin__editor">
          <header className="expert-admin__identity">
            <span className="expert-admin__hero-avatar">{monogram(selected)}</span>
            <div>
              <span>{GROUP_LABEL[selected.group]} EXPERT</span>
              <h2>{selected.name}</h2>
              <p>{selected.code}</p>
            </div>
            <label className="expert-admin__availability">
              <span><strong>{draft.enabled ? "参与工作流" : "暂停调度"}</strong><small>{draft.enabled ? "可被运营大脑和独立入口调用" : "保存后停止新任务调用"}</small></span>
              <Switch
                aria-label={`启用${selected.name}`}
                checked={draft.enabled}
                onChange={(enabled) => setDraft({ ...draft, enabled })}
              />
            </label>
          </header>

          <div className="expert-admin__editor-body">
            <section className="expert-admin__section expert-admin__section--copy">
              <header><span>01</span><div><h3>职责与指令</h3><p>职责用于界定业务边界，补充指令会进入该专家的真实 system prompt。</p></div></header>
              <label>
                <span>专家职责</span>
                <Input.TextArea
                  aria-label="专家职责"
                  value={draft.responsibility}
                  maxLength={500}
                  autoSize={{ minRows: 2, maxRows: 4 }}
                  onChange={(event) => setDraft({ ...draft, responsibility: event.target.value })}
                />
              </label>
              <label>
                <span>运行时补充指令</span>
                <Input.TextArea
                  aria-label="运行时补充指令"
                  value={draft.system_prompt}
                  maxLength={8000}
                  autoSize={{ minRows: 4, maxRows: 9 }}
                  placeholder="例如：不得编造账号数据；结论必须引用当前项目知识库。"
                  onChange={(event) => setDraft({ ...draft, system_prompt: event.target.value })}
                />
              </label>
            </section>

            <section className="expert-admin__section">
              <header><span>02</span><div><h3>工具权限</h3><p>权限会直接决定工具是否执行，以及运行时是否暂停等待人工确认。</p></div></header>
              <div className="expert-admin__policy-list">
                {selected.available_tools.map((tool) => (
                  <div key={tool.code} className="expert-admin__policy-row">
                    <ToolOutlined />
                    <div><strong>{tool.name}</strong><p>{tool.description}</p><small>{tool.code}</small></div>
                    <select
                      aria-label={`${tool.name}权限`}
                      value={draft.tool_permissions[tool.code] ?? "auto"}
                      onChange={(event) => setDraft({
                        ...draft,
                        tool_permissions: {
                          ...draft.tool_permissions,
                          [tool.code]: event.target.value as ToolPermissionMode,
                        },
                      })}
                    >
                      {PERMISSION_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </div>
                ))}
              </div>
            </section>

            <section className="expert-admin__section">
              <header><span>03</span><div><h3>质量门</h3><p>命中的质量门会随专家计划进入任务账本，并在必要时进入人工审批。</p></div></header>
              {selected.available_quality_gates.length === 0 ? (
                <div className="expert-admin__quiet-state">
                  <CheckOutlined /><span>该专家当前没有独立质量门，由上游或运营大脑统一验收。</span>
                </div>
              ) : (
                <div className="expert-admin__gate-grid">
                  {selected.available_quality_gates.map((gate) => {
                    const checked = draft.quality_gates.includes(gate.code);
                    return (
                      <label key={gate.code} className={checked ? "is-checked" : ""}>
                        <input
                          type="checkbox"
                          aria-label={gate.name}
                          checked={checked}
                          disabled={gate.forced}
                          onChange={(event) => setDraft({
                            ...draft,
                            quality_gates: event.target.checked
                              ? [...draft.quality_gates, gate.code]
                              : draft.quality_gates.filter((code) => code !== gate.code),
                          })}
                        />
                        <SafetyCertificateOutlined />
                        <span><strong>{gate.name}</strong><small>{gate.description}</small></span>
                        {gate.forced && <em>强制</em>}
                      </label>
                    );
                  })}
                </div>
              )}
            </section>
          </div>

          <footer className="expert-admin__actions">
            <span><ClockCircleOutlined /> {selected.updated_at ? "已有组织配置" : "使用系统默认策略"}</span>
            <Button disabled={!dirty || updateMutation.isPending} onClick={() => setDraft(toDraft(selected))}>撤销更改</Button>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              loading={updateMutation.isPending}
              disabled={!dirty}
              onClick={save}
            >
              保存专家配置
            </Button>
          </footer>
        </section>
      </div>
      <Modal
        title="放弃未保存的专家配置？"
        open={pendingCode != null}
        okText="放弃并切换"
        cancelText="继续编辑"
        onCancel={() => setPendingCode(null)}
        onOk={() => {
          if (pendingCode) setSelectedCode(pendingCode);
          setPendingCode(null);
        }}
      >
        <p>当前专家配置尚未保存。切换后，本次修改将被放弃。</p>
      </Modal>
    </main>
  );
}

function toDraft(expert: AgentManagement): Draft {
  const toolPermissions = Object.fromEntries(
    expert.available_tools.map((tool) => [
      tool.code,
      expert.tool_permissions[tool.code] ?? "auto",
    ]),
  ) as Record<string, ToolPermissionMode>;
  return {
    enabled: expert.enabled,
    responsibility: expert.responsibility,
    system_prompt: expert.system_prompt,
    tool_permissions: toolPermissions,
    quality_gates: [...expert.quality_gates],
  };
}

function monogram(expert: AgentManagement): string {
  if (expert.code === "00-decision") return "主";
  return expert.name.replace("专家", "").slice(0, 2);
}
