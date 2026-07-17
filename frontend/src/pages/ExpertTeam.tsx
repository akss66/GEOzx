import { ArrowUpOutlined, HistoryOutlined, LoadingOutlined } from "@ant-design/icons";
import { App, Button, Input, Skeleton } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  handoffAgentRun,
  invokeAgent,
  listAgentRuns,
  listAgents,
  suggestAgentRunKnowledge,
} from "../api/agents";
import { approveDeliverableAcceptance } from "../api/brain";
import { presentApiError } from "../api/errors";
import { getWorkspaceContext } from "../api/shell";
import { ExpertArtifact, artifactToText } from "../components/experts/ExpertArtifact";
import { ExpertDirectory, expertMonogram } from "../components/experts/ExpertDirectory";
import { OperationalState } from "../components/ui";
import {
  resolveWorkspaceAccount,
  useCurrentWorkspace,
} from "../stores/currentWorkspace";
import type { AgentCode, AgentDirectRun, AgentProfile } from "../types";

export default function ExpertTeam() {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { clientId, projectId, platform, accountId } = useCurrentWorkspace();
  const [selectedCode, setSelectedCode] = useState<AgentCode | null>(null);
  const [goal, setGoal] = useState("");
  const [sourceTaskId, setSourceTaskId] = useState<number | undefined>();
  const [localRun, setLocalRun] = useState<AgentDirectRun | null>(null);

  const agentsQuery = useQuery({ queryKey: ["agents"], queryFn: listAgents });
  const contextQuery = useQuery({
    queryKey: ["workspace-context", clientId, projectId],
    queryFn: () => getWorkspaceContext(clientId, projectId),
  });
  const experts = useMemo(
    () => (agentsQuery.data ?? []).filter((agent) => agent.code !== "00-decision"),
    [agentsQuery.data],
  );
  const selectedExpert = experts.find((agent) => agent.code === selectedCode) ?? null;
  const project = contextQuery.data?.selected_project
    ?? contextQuery.data?.projects.find((item) => item.id === projectId)
    ?? null;
  const account = resolveWorkspaceAccount(
    contextQuery.data?.accounts ?? [],
    platform,
    accountId,
  );
  const scopeReady = Boolean(project && account);
  const scopeDescription = describeExpertScope(project?.name ?? null, account?.nickname ?? null);

  useEffect(() => {
    if (selectedCode == null && experts[0]) setSelectedCode(experts[0].code);
  }, [experts, selectedCode]);

  const runsQuery = useQuery({
    queryKey: ["agent-runs", selectedCode, project?.id, account?.id],
    queryFn: () => listAgentRuns(selectedCode!, project!.id, account!.id),
    enabled: Boolean(selectedCode && project && account),
  });
  const currentRun = localRun?.invocation.agent_code === selectedCode
    ? localRun
    : runsQuery.data?.[0] ?? null;

  const invokeMutation = useMutation({
    mutationFn: () => invokeAgent(selectedCode!, {
      prompt: goal.trim(),
      projectId: project!.id,
      accountId: account!.id,
      sourceTaskId,
    }),
    onSuccess: (run) => {
      setLocalRun(run);
      setGoal("");
      setSourceTaskId(undefined);
      queryClient.invalidateQueries({ queryKey: ["agent-runs", selectedCode] });
      message.success("专家已完成分析，成果等待你确认");
    },
    onError: (error) => message.error(presentApiError(error).message),
  });
  const adoptMutation = useMutation({
    mutationFn: () => approveDeliverableAcceptance(currentRun!.acceptance),
    onSuccess: (acceptance) => {
      if (currentRun) setLocalRun({ ...currentRun, acceptance });
      queryClient.invalidateQueries({ queryKey: ["agent-runs", selectedCode] });
      message.success("成果已采用并写入当前工作流");
    },
    onError: (error) => message.error(presentApiError(error).message),
  });
  const handoffMutation = useMutation({
    mutationFn: () => handoffAgentRun(currentRun!.task.id),
    onSuccess: (handoff) => navigate("/brain", {
      state: { agentDraft: handoff.prompt, agentMode: "task" },
    }),
    onError: (error) => message.error(presentApiError(error).message),
  });
  const knowledgeSuggestionMutation = useMutation({
    mutationFn: () => suggestAgentRunKnowledge(currentRun!.task.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["knowledge-suggestions"] });
      message.success("已送入知识库待确认建议");
    },
    onError: (error) => message.error(presentApiError(error).message),
  });

  const selectExpert = (code: AgentCode) => {
    setSelectedCode(code);
    setGoal("");
    setSourceTaskId(undefined);
    setLocalRun(null);
  };
  const submit = () => {
    if (!scopeReady) return message.warning("请先在顶部选择项目和抖音账号");
    if (!goal.trim()) return message.warning("请写下希望专家处理的具体任务");
    invokeMutation.mutate();
  };
  const revise = () => {
    if (!currentRun) return;
    setSourceTaskId(currentRun.task.id);
    setGoal("请根据以下修改意见重新处理：");
  };
  const copyResult = async () => {
    if (!currentRun) return;
    await navigator.clipboard.writeText(artifactToText(currentRun));
    message.success("成果全文已复制");
  };

  if (agentsQuery.isError) {
    const failure = presentApiError(agentsQuery.error, "专家目录暂时不可用。");
    return (
      <main className="expert-studio">
        <OperationalState
          kind="error"
          title="专家目录加载失败"
          description={failure.message}
          diagnostic={failure.diagnostic}
          actionLabel="重新加载"
          onAction={() => void agentsQuery.refetch()}
        />
      </main>
    );
  }

  return (
    <main className="expert-studio">
      <ExpertDirectory experts={experts} selectedCode={selectedCode} onSelect={selectExpert} />
      <section className="expert-workspace">
        {contextQuery.isError ? (
          <OperationalState
            kind="error"
            title="专家工作区加载失败"
            description={`${presentApiError(contextQuery.error, "专家工作区上下文暂时不可用。").message} 顶部选择不会被修改。`}
            diagnostic={presentApiError(contextQuery.error).diagnostic}
            actionLabel="重新加载"
            actionLoading={contextQuery.isFetching}
            onAction={() => void contextQuery.refetch()}
          />
        ) : agentsQuery.isLoading || !selectedExpert ? (
          <Skeleton active paragraph={{ rows: 10 }} />
        ) : (
          <>
            <ExpertHeader expert={selectedExpert} context={scopeDescription.context} />
            <div className="expert-workspace__body">
              <div className="expert-workspace__main">
                {!scopeReady ? (
                  <ScopeGate instruction={scopeDescription.instruction} />
                ) : runsQuery.isLoading ? (
                  <div className="expert-loading"><LoadingOutlined spin /><span>正在读取专家工作记录</span></div>
                ) : runsQuery.isError ? (
                  <OperationalState
                    kind="error"
                    title="专家记录加载失败"
                    description={`${presentApiError(runsQuery.error, "该专家的工作记录暂时不可用。").message} 当前专家和账号不会被修改。`}
                    diagnostic={presentApiError(runsQuery.error).diagnostic}
                    actionLabel="重试"
                    actionLoading={runsQuery.isFetching}
                    onAction={() => void runsQuery.refetch()}
                  />
                ) : currentRun ? (
                  <>
                    <div className="expert-user-request"><span>你的任务</span><p>{currentRun.task.brief.goal}</p></div>
                    <ExpertArtifact
                      run={currentRun}
                      adopting={adoptMutation.isPending}
                      handingOff={handoffMutation.isPending}
                      suggesting={knowledgeSuggestionMutation.isPending}
                      onAdopt={() => adoptMutation.mutate()}
                      onRevise={revise}
                      onHandoff={() => handoffMutation.mutate()}
                      onSuggest={() => knowledgeSuggestionMutation.mutate()}
                      onCopy={copyResult}
                    />
                  </>
                ) : (
                  <ExpertEmpty expert={selectedExpert} onPick={setGoal} />
                )}
              </div>
              {!runsQuery.isError ? <RunHistory runs={runsQuery.data ?? []} activeId={currentRun?.task.id} onSelect={setLocalRun} /> : null}
            </div>
            <ExpertComposer
              expert={selectedExpert}
              value={goal}
              revising={sourceTaskId != null}
              disabled={!scopeReady}
              loading={invokeMutation.isPending}
              onChange={setGoal}
              onCancelRevision={() => { setSourceTaskId(undefined); setGoal(""); }}
              onSubmit={submit}
            />
          </>
        )}
      </section>
    </main>
  );
}

function ExpertHeader({ expert, context }: { expert: AgentProfile; context: string }) {
  return (
    <header className="expert-workspace__header">
      <span className="expert-identity">{expertMonogram(expert.code)}</span>
      <div><span>独立调用</span><h2>{expert.name}</h2><p>{expert.one_liner}</p></div>
      <aside><strong>当前工作范围</strong><span>{context}</span><small>抖音 · 独立调用</small></aside>
    </header>
  );
}

function ScopeGate({ instruction }: { instruction: string }) {
  return (
    <div className="expert-scope-gate">
      <span>01</span><h3>先明确专家的工作对象</h3>
      <p>{instruction} 专家不会默认使用第一个账号，也不会跨项目读取成果。</p>
    </div>
  );
}

export function describeExpertScope(projectName: string | null, accountName: string | null) {
  const context = `${projectName ?? "尚未选择项目"} · ${accountName ?? "尚未选择账号"}`;
  if (!projectName && !accountName) {
    return { context, instruction: "请从顶部选择项目和抖音账号。" };
  }
  if (!projectName) {
    return { context, instruction: "当前账号已选定，请再选择项目。" };
  }
  if (!accountName) {
    return { context, instruction: "当前项目已选定，请再选择抖音账号。" };
  }
  return { context, instruction: "当前工作对象已明确。" };
}

function ExpertEmpty({ expert, onPick }: { expert: AgentProfile; onPick: (value: string) => void }) {
  return (
    <div className="expert-empty">
      <span>{expertMonogram(expert.code)}</span><h3>把一个明确问题交给{expert.name}</h3>
      <p>专家会独立完成分析并生成正式成果；采用前不会改写现有业务数据。</p>
      <div>{expert.typical_tasks.slice(0, 3).map((task) => <button key={task} type="button" onClick={() => onPick(task)}>{task}</button>)}</div>
    </div>
  );
}

function RunHistory({ runs, activeId, onSelect }: { runs: AgentDirectRun[]; activeId?: number; onSelect: (run: AgentDirectRun) => void }) {
  return (
    <aside className="expert-history"><header><HistoryOutlined /><strong>工作记录</strong><span>{runs.length}</span></header>
      {runs.length === 0 ? <p>本账号还没有该专家的工作记录。</p> : runs.slice(0, 8).map((run) => (
        <button key={run.task.id} type="button" className={run.task.id === activeId ? "is-active" : ""} onClick={() => onSelect(run)}>
          <strong>{run.acceptance.title}</strong><span>{run.acceptance.status === "approved" ? "已采用" : "待确认"}</span><small>{run.task.brief.goal}</small>
        </button>
      ))}
    </aside>
  );
}

function ExpertComposer({ expert, value, revising, disabled, loading, onChange, onCancelRevision, onSubmit }: { expert: AgentProfile; value: string; revising: boolean; disabled: boolean; loading: boolean; onChange: (value: string) => void; onCancelRevision: () => void; onSubmit: () => void }) {
  return (
    <footer className="expert-composer">
      {revising && <div><strong>正在基于上一版修改</strong><button type="button" onClick={onCancelRevision}>取消</button></div>}
      <Input.TextArea aria-label="专家任务" value={value} disabled={disabled} autoSize={{ minRows: 2, maxRows: 6 }} maxLength={4000} placeholder={`把一项明确任务直接交给${expert.name}...`} onChange={(event) => onChange(event.target.value)} />
      <Button
        type="primary"
        icon={<ArrowUpOutlined />}
        aria-label="开始分析"
        disabled={disabled || !value.trim()}
        loading={loading}
        onClick={onSubmit}
      >
        开始分析
      </Button>
    </footer>
  );
}
