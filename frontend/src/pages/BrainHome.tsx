import {
  CheckCircleFilled,
  ClockCircleFilled,
  ExclamationCircleFilled,
  FileTextOutlined,
  HistoryOutlined,
  PlusOutlined,
  RedoOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  App as AntApp,
  Button,
  Drawer,
  Input,
  Tag,
  Typography,
} from "antd";
import { useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import {
  approveDeliverableAcceptance,
  approveToolCall,
  getBrainTaskRuntime,
  listBrainTasks,
  rejectDeliverableAcceptance,
  regenerateBrainMessage,
  reviseBrainDecision,
  selectBrainDecision,
  sendBrainMessage,
  stopBrainGeneration,
} from "../api/brain";
import { presentApiError } from "../api/errors";
import { getWorkspaceContext } from "../api/shell";
import { AgentAvatar } from "../components/agents/AgentAvatar";
import { OperationalState } from "../components/ui";
import { BrainComposer } from "../components/brain/BrainComposer";
import { DecisionRequest } from "../components/brain/DecisionRequest";
import {
  useEventStream,
  type DyEvent,
  type EventStreamConnectionState,
} from "../hooks/useEventStream";
import {
  clearActiveBrainTaskId,
  getActiveBrainTaskId,
  setActiveBrainTaskId,
} from "../stores/brainConversation";
import {
  resolveWorkspaceAccount,
  useCurrentWorkspace,
} from "../stores/currentWorkspace";
import type {
  Account,
  AgentInvocation,
  AgentToolCall,
  BrainRuntime,
  BrainTask,
  DeliverableAcceptance,
  OrchestrationPlanStep,
} from "../types";

interface LiveRuntimeMessage {
  id: string;
  taskId: number;
  agentCode: string;
  agentName: string;
  model?: string;
  content: string;
  status: "streaming" | "done" | "error" | "stopped";
}

interface PendingTurn {
  clientMessageId: string;
  content: string;
  taskId: number | null;
  showUser: boolean;
}

type ConversationItem =
  | { kind: "user"; id: string; content: string }
  | { kind: "agent"; id: string; message: LiveRuntimeMessage }
  | {
      kind: "expert";
      id: string;
      invocation: AgentInvocation;
      lifecycleMessage: string | null;
    }
  | { kind: "status"; id: string; content: string };

const PROMPT_CHIPS = ["账号定位诊断", "冷启动内容", "脚本生成", "发布前检查"];

export default function BrainHome() {
  const { message } = AntApp.useApp();
  const qc = useQueryClient();
  const [goal, setGoal] = useState("");
  const [localTasks, setLocalTasks] = useState<BrainTask[]>([]);
  const [activeRuntimeTaskId, setActiveRuntimeTaskId] = useState<number | null>(null);
  const [liveMessages, setLiveMessages] = useState<LiveRuntimeMessage[]>([]);
  const [pendingTurn, setPendingTurn] = useState<PendingTurn | null>(null);
  const [approvalComment, setApprovalComment] = useState("");
  const [detailsOpen, setDetailsOpen] = useState(false);
  const pendingClientMessageId = useRef<string | null>(null);
  const { clientId, projectId, platform, accountId } = useCurrentWorkspace();
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    const state = location.state as { agentDraft?: string; agentMode?: "discuss" | "task" } | null;
    if (!state?.agentDraft) return;
    setGoal(state.agentDraft);
    navigate(location.pathname, { replace: true, state: null });
  }, [location.pathname, location.state, navigate]);

  const contextQuery = useQuery({
    queryKey: ["workspace-context", clientId, projectId],
    queryFn: () => getWorkspaceContext(clientId, projectId),
  });
  const activeAccount = useMemo(
    () => resolveWorkspaceAccount(contextQuery.data?.accounts ?? [], platform, accountId),
    [accountId, contextQuery.data?.accounts, platform],
  );
  const tasksQuery = useQuery({
    queryKey: ["brain-tasks"],
    queryFn: listBrainTasks,
    enabled: Boolean(activeAccount),
  });

  const { connectionState } = useEventStream((event) => {
    if (!event.type.startsWith("brain.runtime.")) return;
    const payload = asRuntimePayload(event.payload);
    const eventClientMessageId = typeof payload?.client_message_id === "string"
      ? payload.client_message_id
      : null;
    const eventTaskId = payload?.task_id == null ? null : Number(payload.task_id);
    if (
      eventTaskId != null
      && Number.isFinite(eventTaskId)
      && eventClientMessageId != null
      && eventClientMessageId === pendingClientMessageId.current
    ) {
      setActiveRuntimeTaskId(eventTaskId);
      setPendingTurn((current) => current?.clientMessageId === eventClientMessageId
        ? { ...current, taskId: eventTaskId }
        : current);
      if (activeAccount) setActiveBrainTaskId(activeAccount.id, eventTaskId);
    }
    ingestRuntimeEvent(event, setLiveMessages);
    if (!["brain.runtime.message_start", "brain.runtime.message_delta"].includes(event.type)) {
      qc.invalidateQueries({ queryKey: ["brain-tasks"] });
      qc.invalidateQueries({ queryKey: ["brain-runtime"] });
    }
  }, {
    onReconnect: () => {
      void qc.invalidateQueries({ queryKey: ["brain-tasks"] });
      void qc.invalidateQueries({ queryKey: ["brain-runtime"] });
    },
  });

  const effectiveAccount = activeAccount;
  const accountReady = Boolean(
    effectiveAccount &&
    (effectiveAccount.auth_status === "authorized" || effectiveAccount.auth_status === "manual"),
  );

  const messageMutation = useMutation({
    mutationFn: sendBrainMessage,
    onSuccess: (nextRuntime) => {
      const completedClientMessageId = pendingClientMessageId.current;
      const task = nextRuntime.task;
      const taskAccountId = task.brief.account_ids[0] ?? effectiveAccount?.id;
      if (taskAccountId != null) setActiveBrainTaskId(taskAccountId, task.id);
      setLocalTasks((prev) => [task, ...prev.filter((item) => item.id !== task.id)]);
      setActiveRuntimeTaskId(task.id);
      qc.setQueryData(["brain-runtime", task.id], nextRuntime);
      qc.invalidateQueries({ queryKey: ["brain-tasks"] });
      if (nextRuntime.status === "stopped" && completedClientMessageId) {
        setLiveMessages((prev) => prev.map((item) =>
          item.id.startsWith(completedClientMessageId)
            ? { ...item, status: "stopped" }
            : item
        ));
      }
      setPendingTurn(null);
      pendingClientMessageId.current = null;
    },
    onError: (error) => {
      setPendingTurn((current) => {
        if (current) setGoal((value) => value || current.content);
        return null;
      });
      pendingClientMessageId.current = null;
      message.error(presentApiError(error, "任务启动失败，请稍后重试。").message);
    },
  });

  const regenerateMutation = useMutation({
    mutationFn: regenerateBrainMessage,
    onSuccess: (nextRuntime) => {
      const completedClientMessageId = pendingClientMessageId.current;
      const task = nextRuntime.task;
      const taskAccountId = task.brief.account_ids[0] ?? effectiveAccount?.id;
      if (taskAccountId != null) setActiveBrainTaskId(taskAccountId, task.id);
      setLocalTasks((prev) => [task, ...prev.filter((item) => item.id !== task.id)]);
      setActiveRuntimeTaskId(task.id);
      qc.setQueryData(["brain-runtime", task.id], nextRuntime);
      void qc.invalidateQueries({ queryKey: ["brain-tasks"] });
      if (nextRuntime.status === "stopped" && completedClientMessageId) {
        setLiveMessages((prev) => prev.map((item) =>
          item.id.startsWith(completedClientMessageId)
            ? { ...item, status: "stopped" }
            : item
        ));
      }
      setPendingTurn(null);
      pendingClientMessageId.current = null;
    },
    onError: (error) => {
      setPendingTurn(null);
      pendingClientMessageId.current = null;
      message.error(presentApiError(error, "重新生成失败，请稍后重试。").message);
    },
  });

  const stopMutation = useMutation({
    mutationFn: stopBrainGeneration,
    onSuccess: () => message.info("正在停止本轮生成"),
    onError: (error) => message.error(
      presentApiError(error, "停止生成失败，请稍后重试。").message,
    ),
  });

  const selectDecisionMutation = useMutation({
    mutationFn: selectBrainDecision,
    onSuccess: (nextRuntime) => {
      qc.setQueryData(["brain-runtime", nextRuntime.task.id], nextRuntime);
      qc.invalidateQueries({ queryKey: ["brain-tasks"] });
    },
    onError: (error) => message.error(
      presentApiError(error, "方案提交失败，请稍后重试。").message,
    ),
  });

  const reviseDecisionMutation = useMutation({
    mutationFn: reviseBrainDecision,
    onSuccess: (nextRuntime) => {
      qc.setQueryData(["brain-runtime", nextRuntime.task.id], nextRuntime);
      qc.invalidateQueries({ queryKey: ["brain-tasks"] });
    },
    onError: (error) => message.error(
      presentApiError(error, "方案调整失败，请稍后重试。").message,
    ),
  });

  const approveMutation = useMutation({
    mutationFn: approveToolCall,
    onSuccess: (toolCall) => {
      setApprovalComment("");
      qc.invalidateQueries({ queryKey: ["brain-tasks"] });
      qc.invalidateQueries({ queryKey: ["brain-runtime", toolCall.task_id] });
      message.success("工具权限已处理，Runtime 正在继续");
    },
    onError: (error) => message.error(
      presentApiError(error, "工具权限处理失败，请稍后重试。").message,
    ),
  });

  const acceptArtifactMutation = useMutation({
    mutationFn: (acceptance: DeliverableAcceptance) =>
      approveDeliverableAcceptance(acceptance),
    onSuccess: (acceptance) => {
      qc.invalidateQueries({ queryKey: ["brain-runtime", acceptance.task_id] });
      message.success("成果已采用");
    },
    onError: (error) => message.error(
      presentApiError(error, "成果验收失败，请稍后重试。").message,
    ),
  });

  const rerunArtifactMutation = useMutation({
    mutationFn: ({ acceptance, reason }: { acceptance: DeliverableAcceptance; reason: string }) =>
      rejectDeliverableAcceptance({
        acceptance,
        reason,
        rerun_scope: "current_agent",
        ask_brain_rejudge: true,
      }),
    onSuccess: (acceptance) => {
      qc.invalidateQueries({ queryKey: ["brain-runtime", acceptance.task_id] });
      message.success("修改意见已提交，专家将按要求重做");
    },
    onError: (error) => message.error(
      presentApiError(error, "修改意见提交失败，请稍后重试。").message,
    ),
  });

  const tasks = useMemo(
    () => mergeTasks(localTasks, tasksQuery.data ?? []),
    [localTasks, tasksQuery.data],
  );
  const accountScopedTasks = useMemo(
    () =>
      effectiveAccount
        ? tasks.filter((task) => task.brief.account_ids.includes(effectiveAccount.id))
        : [],
    [effectiveAccount, tasks],
  );
  const workflowTasks = accountScopedTasks;

  useEffect(() => {
    if (!effectiveAccount) {
      setActiveRuntimeTaskId(null);
      return;
    }
    if (tasksQuery.isLoading || tasksQuery.isError) return;

    const savedTaskId = getActiveBrainTaskId(effectiveAccount.id);
    const savedTask = workflowTasks.find(
      (task) => task.id === savedTaskId && task.context_closed_at == null,
    );
    if (savedTask) {
      setActiveRuntimeTaskId(savedTask.id);
      return;
    }
    if (savedTaskId != null) clearActiveBrainTaskId(effectiveAccount.id);
    setActiveRuntimeTaskId(null);
  }, [effectiveAccount, tasksQuery.isError, tasksQuery.isLoading, workflowTasks]);

  const activeTask =
    activeRuntimeTaskId == null
      ? null
      : workflowTasks.find((task) => task.id === activeRuntimeTaskId) ?? null;
  const runtimeQuery = useQuery({
    queryKey: ["brain-runtime", activeRuntimeTaskId],
    queryFn: () => getBrainTaskRuntime(activeRuntimeTaskId!),
    enabled: activeRuntimeTaskId != null,
  });
  const runtime =
    activeRuntimeTaskId != null && runtimeQuery.data?.task.id === activeRuntimeTaskId
      ? runtimeQuery.data
      : null;
  const visibleRuntime = runtime;
  const visibleTask = runtime?.task ?? activeTask;
  const pendingPermission = visibleRuntime?.pending_permissions[0] ?? null;
  const contextError = contextQuery.isError
    ? presentApiError(contextQuery.error, "运营上下文暂时不可用。")
    : null;
  const tasksError = tasksQuery.isError
    ? presentApiError(tasksQuery.error, "任务记录暂时不可用。")
    : null;
  const runtimeError = runtimeQuery.isError
    ? presentApiError(runtimeQuery.error, "当前任务运行时暂时不可用。")
    : null;
  const isGenerating = messageMutation.isPending || regenerateMutation.isPending;

  const startWorkflow = () => {
    if (isGenerating) return;
    const trimmed = goal.trim();
    if (!trimmed) {
      message.warning("先写下要交给主 Agent 的运营目标");
      return;
    }
    if (!effectiveAccount) {
      message.warning("先在账号矩阵创建一个抖音账号或本地开发账号");
      return;
    }
    if (!accountReady) {
      message.warning("当前账号尚未完成授权，请先到账号矩阵完成抖音授权");
      return;
    }

    const clientMessageId = createClientMessageId();
    pendingClientMessageId.current = clientMessageId;
    setPendingTurn({
      clientMessageId,
      content: trimmed,
      taskId: activeTask?.id ?? null,
      showUser: true,
    });
    setGoal("");
    messageMutation.mutate({
      message: trimmed,
      client_message_id: clientMessageId,
      task_id: activeTask?.id,
      project_id: projectId,
      account_id: effectiveAccount.id,
      platform: "douyin",
    });
  };

  const stopGeneration = () => {
    if (!pendingTurn || stopMutation.isPending) return;
    stopMutation.mutate({
      clientMessageId: pendingTurn.clientMessageId,
      taskId: pendingTurn.taskId,
    });
  };

  const regenerateLastTurn = () => {
    if (!visibleRuntime || isGenerating) return;
    const sourceMessage = latestUserMessage(visibleRuntime);
    if (!sourceMessage) {
      message.warning("当前对话没有可重新生成的用户消息");
      return;
    }
    const clientMessageId = createClientMessageId();
    pendingClientMessageId.current = clientMessageId;
    setPendingTurn({
      clientMessageId,
      content: sourceMessage,
      taskId: visibleRuntime.task.id,
      showUser: false,
    });
    regenerateMutation.mutate({
      taskId: visibleRuntime.task.id,
      clientMessageId,
    });
  };

  const resetConversation = () => {
    if (effectiveAccount) clearActiveBrainTaskId(effectiveAccount.id);
    setActiveRuntimeTaskId(null);
    setLiveMessages([]);
    setPendingTurn(null);
    setApprovalComment("");
    setGoal("");
    setDetailsOpen(false);
  };

  const hasConversation = Boolean(activeTask || pendingTurn);

  return (
    <div className={`tz-brain-page${hasConversation ? " has-conversation" : " is-empty"}`}>
      {hasConversation ? <header className="tz-brain-toolbar">
        <div className="tz-brain-identity">
          <AgentAvatar code="00-decision" className="tz-brain-wordmark" />
          <div>
            <strong>运营大脑</strong>
            <span>主 Agent · 目标理解与专家编排</span>
          </div>
        </div>
        <div className="tz-brain-toolbar-actions">
          <Button icon={<PlusOutlined />} onClick={resetConversation}>
            新对话
          </Button>
          <Button icon={<HistoryOutlined />} onClick={() => setDetailsOpen(true)}>
            执行详情
          </Button>
        </div>
      </header> : null}

      <main className="tz-brain-stage">
        {contextError ? (
          <OperationalState
            kind="error"
            title="运营上下文加载失败"
            description="当前账号选择不会被替换。重新加载后，主 Agent 会继续使用顶部明确选择的账号。"
            diagnostic={contextError.diagnostic}
            actionLabel="重新加载"
            onAction={() => void contextQuery.refetch()}
          />
        ) : <div className="tz-brain-thread">
          <ContextStrip
            account={effectiveAccount}
            loading={contextQuery.isLoading}
          />

          <section className="dy-brain-conversation" aria-label="运营大脑对话流">
            {tasksError ? (
              <OperationalState
                kind="error"
                title="任务记录加载失败"
                description={`${tasksError.message} 当前账号选择和已保存会话不会被修改。`}
                diagnostic={tasksError.diagnostic}
                actionLabel="重试"
                onAction={() => void tasksQuery.refetch()}
              />
            ) : activeTask && runtimeError ? (
              <OperationalState
                kind="error"
                title="任务运行时加载失败"
                description={`${runtimeError.message} 当前任务仍保持选中，不会显示成新对话。`}
                diagnostic={runtimeError.diagnostic}
                actionLabel="重试"
                onAction={() => void runtimeQuery.refetch()}
              />
            ) : visibleRuntime ? (
              <ConversationStream
                runtime={visibleRuntime}
                liveMessages={liveMessages.filter((item) => item.taskId === visibleRuntime.task.id)}
                pendingTurn={
                  pendingTurn?.taskId == null || pendingTurn.taskId === visibleRuntime.task.id
                    ? pendingTurn
                    : null
                }
                loading={runtimeQuery.isLoading || isGenerating}
                connectionState={connectionState}
                regenerating={regenerateMutation.isPending}
                selectingDecisionId={
                  selectDecisionMutation.isPending
                    ? selectDecisionMutation.variables?.decisionId ?? null
                    : null
                }
                revisingDecisionId={
                  reviseDecisionMutation.isPending
                    ? reviseDecisionMutation.variables?.decisionId ?? null
                    : null
                }
                acceptingArtifactId={
                  acceptArtifactMutation.isPending
                    ? acceptArtifactMutation.variables?.id ?? null
                    : null
                }
                rerunningArtifactId={
                  rerunArtifactMutation.isPending
                    ? rerunArtifactMutation.variables?.acceptance.id ?? null
                    : null
                }
                onAcceptArtifact={(acceptance) => acceptArtifactMutation.mutate(acceptance)}
                onRerunArtifact={(acceptance, reason) =>
                  rerunArtifactMutation.mutate({ acceptance, reason })
                }
                onSelectDecision={(decisionId, choiceId) =>
                  selectDecisionMutation.mutate({
                    taskId: visibleRuntime.task.id,
                    decisionId,
                    choiceId,
                  })
                }
                onReviseDecision={(decisionId, comment, requestNewOptions) =>
                  reviseDecisionMutation.mutate({
                    taskId: visibleRuntime.task.id,
                    decisionId,
                    comment,
                    requestNewOptions,
                  })
                }
                onRegenerate={regenerateLastTurn}
              />
            ) : pendingTurn ? (
              <PendingConversation turn={pendingTurn} />
            ) : (
              <ConversationEmpty account={effectiveAccount} loading={contextQuery.isLoading} />
            )}
          </section>

          <BrainComposer
            value={goal}
            disabled={
              !accountReady
              || isGenerating
              || tasksQuery.isError
              || runtimeQuery.isError
            }
            loading={isGenerating}
            pendingPermission={pendingPermission}
            approvalComment={approvalComment}
            approving={approveMutation.isPending}
            promptChips={PROMPT_CHIPS}
            onChange={setGoal}
            onApprovalCommentChange={setApprovalComment}
            onApprovePermission={(toolCallId, approved, comment) =>
              approveMutation.mutate({ toolCallId, approved, comment })
            }
            onSubmit={startWorkflow}
            onStop={stopGeneration}
          />
        </div>}
      </main>

      <Drawer
        title="执行详情"
        width={440}
        open={detailsOpen}
        onClose={() => setDetailsOpen(false)}
        className="tz-brain-details-drawer"
      >
        <ExecutionDetails task={visibleTask} runtime={visibleRuntime} />
      </Drawer>
    </div>
  );
}

function ContextStrip({
  account,
  loading,
}: {
  account: Account | null;
  loading: boolean;
}) {
  return (
    <section className="dy-brain-context-strip">
      <div>
        <span className="dy-brain-kicker">当前上下文</span>
        {account ? (
          <strong>{account.nickname}</strong>
        ) : (
          <strong>{loading ? "正在读取账号矩阵" : "尚未选择抖音账号"}</strong>
        )}
      </div>
      <div className="dy-brain-context-actions">
        <Tag style={{ marginInlineEnd: 0 }}>抖音</Tag>
        {account && <Tag style={{ marginInlineEnd: 0 }}>{syncLabel(account.data_sync_status)}</Tag>}
      </div>
    </section>
  );
}

function ConversationEmpty({
  account,
  loading,
}: {
  account: Account | null;
  loading: boolean;
}) {
  return (
    <div className="tz-brain-welcome">
      <div className="tz-brain-welcome__agent">
        <img src="/logo.png" alt="" />
        <strong>主 Agent</strong>
      </div>
      <h1>{account ? "今天，想推进什么？" : "先选择一个抖音账号"}</h1>
      <p>
        {account
          ? account.auth_status === "authorized" || account.auth_status === "manual"
            ? "先告诉我目标。我会判断是否需要专家、工具或人工确认。"
            : "当前账号尚未完成授权，请先到账号矩阵完成抖音授权。"
          : loading
            ? "正在读取你有权使用的账号矩阵。"
            : "运营任务必须绑定真实账号，系统不会替你默认选择。"}
      </p>
    </div>
  );
}

function ConversationStream({
  runtime,
  liveMessages,
  pendingTurn,
  loading,
  connectionState,
  regenerating,
  selectingDecisionId,
  revisingDecisionId,
  acceptingArtifactId,
  rerunningArtifactId,
  onAcceptArtifact,
  onRerunArtifact,
  onSelectDecision,
  onReviseDecision,
  onRegenerate,
}: {
  runtime: BrainRuntime;
  liveMessages: LiveRuntimeMessage[];
  pendingTurn: PendingTurn | null;
  loading: boolean;
  connectionState: EventStreamConnectionState;
  regenerating: boolean;
  selectingDecisionId: string | null;
  revisingDecisionId: string | null;
  acceptingArtifactId: number | null;
  rerunningArtifactId: number | null;
  onAcceptArtifact: (acceptance: DeliverableAcceptance) => void;
  onRerunArtifact: (acceptance: DeliverableAcceptance, reason: string) => void;
  onSelectDecision: (decisionId: string, choiceId: string) => void;
  onReviseDecision: (
    decisionId: string,
    comment: string,
    requestNewOptions: boolean,
  ) => void;
  onRegenerate: () => void;
}) {
  const items = conversationItems(runtime, liveMessages, pendingTurn);
  const showRegenerate = ["completed", "stopped", "failed"].includes(runtime.status)
    && runtime.pending_permissions.length === 0;

  return (
    <div className="dy-brain-message-stack">
      <div className="dy-runtime-header" data-status={runtime.status}>
        <span>{runtimeProgressCopy(runtime)}</span>
        {connectionState === "reconnecting" && (
          <Tag style={{ marginInlineEnd: 0 }}>正在恢复连接</Tag>
        )}
        {loading && <Tag style={{ marginInlineEnd: 0 }}>生成中</Tag>}
      </div>

      {items.map((item) => {
        if (item.kind === "user") return <UserMessage key={item.id} content={item.content} />;
        if (item.kind === "agent") return <AgentMessage key={item.id} message={item.message} />;
        if (item.kind === "status") {
          return <RuntimeStatusMessage key={item.id} content={item.content} />;
        }
        return (
          <ExpertMessage
            key={item.id}
            invocation={item.invocation}
            lifecycleMessage={item.lifecycleMessage}
          />
        );
      })}

      {(runtime.pending_decisions ?? []).map((decision) => (
        <DecisionRequest
          key={decision.id}
          decision={decision}
          selecting={selectingDecisionId === decision.id}
          revising={revisingDecisionId === decision.id}
          onSelect={(choiceId) => onSelectDecision(decision.id, choiceId)}
          onRevise={(comment, requestNewOptions) =>
            onReviseDecision(decision.id, comment, requestNewOptions)
          }
        />
      ))}

      {runtime.acceptances.map((acceptance) => (
        <ArtifactMessage
          key={acceptance.id}
          acceptance={acceptance}
          accepting={acceptingArtifactId === acceptance.id}
          rerunning={rerunningArtifactId === acceptance.id}
          onAccept={() => onAcceptArtifact(acceptance)}
          onRerun={(reason) => onRerunArtifact(acceptance, reason)}
        />
      ))}

      {showRegenerate && (
        <div className="tz-brain-regenerate-action">
          <Button
            type="text"
            aria-label="重新生成"
            icon={<RedoOutlined />}
            loading={regenerating}
            disabled={loading && !regenerating}
            onClick={onRegenerate}
          >
            重新生成
          </Button>
        </div>
      )}
    </div>
  );
}

function PendingConversation({ turn }: { turn: PendingTurn }) {
  return (
    <div className="dy-brain-message-stack" aria-live="polite">
      {turn.showUser && <UserMessage content={turn.content} />}
      <AgentMessage message={pendingAgentMessage(turn)} />
    </div>
  );
}

function UserMessage({ content }: { content: string }) {
  return (
    <article className="dy-chat-message dy-chat-message-user" aria-label="你的消息">
      <div className="dy-chat-bubble">
        <Typography.Paragraph style={{ color: "inherit", margin: 0 }}>
          {cleanBrainCopy(content)}
        </Typography.Paragraph>
      </div>
    </article>
  );
}

function AgentMessage({ message }: { message: LiveRuntimeMessage }) {
  const hasSystemError = message.status === "error" || isConfigurationError(message.content);
  const isThinking = message.status === "streaming" && !message.content;
  const isStopped = message.status === "stopped";

  return (
    <article
      className="dy-chat-message dy-chat-message-agent"
      data-error={hasSystemError || undefined}
      data-thinking={isThinking || undefined}
    >
      <AgentAvatar code="00-decision" className="dy-chat-avatar" />
      <div className="dy-chat-bubble">
        <div className="dy-chat-title-line">
          <span>{hasSystemError ? "系统提示" : message.agentName}</span>
          <Tag style={{ marginInlineEnd: 0 }}>
            {hasSystemError
              ? "需配置"
              : message.status === "streaming"
                ? isThinking ? "思考中" : "正在输出"
                : statusCopy(message.status)}
          </Tag>
        </div>
        <Typography.Paragraph style={{ color: "inherit", margin: 0, whiteSpace: "pre-wrap" }}>
          {hasSystemError
            ? "模型服务暂时不可用。请检查供应商凭证或模型路由配置后重试。"
            : formatAgentContent(message.content || (isStopped ? "已停止生成。" : "正在思考..."))}
        </Typography.Paragraph>
      </div>
    </article>
  );
}

function RuntimeStatusMessage({ content }: { content: string }) {
  return (
    <div className="tz-brain-handoff" role="status">
      <span aria-hidden="true" />
      <p>{cleanBrainCopy(content)}</p>
    </div>
  );
}

function ExpertMessage({
  invocation,
  lifecycleMessage,
}: {
  invocation: AgentInvocation;
  lifecycleMessage: string | null;
}) {
  const summary = formatAgentContent(
    invocation.output_summary ||
      invocation.failure_reason ||
      `${invocation.agent_name} 正在处理当前任务。`,
  );
  const isDone = invocation.status === "done";
  const isFailed = invocation.status === "failed" || invocation.status === "blocked";

  return (
    <article
      className="dy-chat-expert-message"
      data-status={invocation.status}
      aria-label={`专家：${invocation.agent_name}`}
    >
      <AgentAvatar
        code={invocation.agent_code}
        className="dy-chat-expert-mark"
        label={invocation.agent_name}
      />
      <div className="dy-chat-expert-body">
        <div className="dy-chat-expert-head">
          <div>
            <strong>{invocation.agent_name}</strong>
            <span>{expertRoleCopy(invocation.agent_code)}</span>
          </div>
          <Tag
            color={isFailed ? "error" : isDone ? "success" : "default"}
            style={{ marginInlineEnd: 0 }}
          >
            {expertStatusCopy(invocation.status)}
          </Tag>
        </div>
        <Typography.Paragraph className="dy-chat-expert-output">
          {summary}
        </Typography.Paragraph>
        {lifecycleMessage ? (
          <p className="dy-chat-expert-lifecycle" role="status">
            {cleanBrainCopy(lifecycleMessage)}
          </p>
        ) : null}
        <details className="dy-chat-expert-details">
          <summary>查看分析上下文</summary>
          <p>{cleanBrainCopy(invocation.input_summary || "暂无输入摘要")}</p>
        </details>
      </div>
    </article>
  );
}

function ArtifactMessage({
  acceptance,
  accepting,
  rerunning,
  onAccept,
  onRerun,
}: {
  acceptance: DeliverableAcceptance;
  accepting: boolean;
  rerunning: boolean;
  onAccept: () => void;
  onRerun: (reason: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [feedback, setFeedback] = useState("");
  const isApproved = acceptance.status === "approved";

  return (
    <article className="tz-brain-artifact" aria-label={`正式成果：${acceptance.title}`}>
      <div className="tz-brain-artifact-mark" aria-hidden="true">
        <FileTextOutlined />
      </div>
      <div className="tz-brain-artifact-body">
        <div className="tz-brain-artifact-head">
          <div>
            <span>正式成果 · V{acceptance.version}</span>
            <strong>{acceptance.title}</strong>
          </div>
          <Tag style={{ marginInlineEnd: 0 }}>{acceptanceStatusCopy(acceptance.status)}</Tag>
        </div>
        <p>{cleanBrainCopy(acceptance.summary)}</p>
        {acceptance.acceptance_items.length > 0 && (
          <details className="tz-brain-artifact-details">
            <summary>查看验收要点</summary>
            <ul>
              {acceptance.acceptance_items.map((item) => (
                <li key={`${item.label}-${item.note}`} data-status={item.status}>
                  <CheckCircleFilled />
                  <div>
                    <strong>{item.label}</strong>
                    <span>{item.note}</span>
                  </div>
                </li>
              ))}
            </ul>
          </details>
        )}
        <div className="tz-brain-artifact-actions">
          {!isApproved && (
            <>
              <Button loading={accepting} onClick={onAccept}>
                采用成果
              </Button>
              <Button onClick={() => setEditing((current) => !current)}>
                修改并重做
              </Button>
            </>
          )}
        </div>
        {editing && !isApproved && (
          <div className="tz-brain-artifact-revision">
            <Input.TextArea
              value={feedback}
              rows={2}
              maxLength={500}
              placeholder="写下需要调整的具体内容"
              onChange={(event) => setFeedback(event.target.value)}
            />
            <div>
              <Button onClick={() => setEditing(false)}>取消</Button>
              <Button
                type="primary"
                loading={rerunning}
                disabled={!feedback.trim()}
                onClick={() => onRerun(feedback.trim())}
              >
                提交重做
              </Button>
            </div>
          </div>
        )}
      </div>
    </article>
  );
}

function ExecutionDetails({
  task,
  runtime,
}: {
  task: BrainTask | null;
  runtime: BrainRuntime | null;
}) {
  if (!task) {
    return (
      <div className="tz-execution-empty">
        <HistoryOutlined />
        <strong>还没有正在执行的任务</strong>
        <p>输入明确的运营目标后，这里会记录专家编排、工具调用和人工确认。</p>
      </div>
    );
  }

  return (
    <div className="tz-execution-details">
      <section>
        <h3>运行状态</h3>
        <div className="dy-brain-risk-list">
          <div>
            <ClockCircleFilled />
            <span>{runtimeStatusLabel(runtime?.status ?? task.status)}</span>
          </div>
          <div>
            <ExclamationCircleFilled />
            <span>{cleanBrainCopy(task.current_focus)}</span>
          </div>
          {task.brief.risk_constraints.map((risk) => (
            <div key={risk}>
              <ExclamationCircleFilled />
              <span>{cleanBrainCopy(risk)}</span>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h3>专家接力</h3>
        <div className="dy-brain-dispatch">
          {task.plan.steps.slice(0, 6).map((step) => (
            <div key={step.id}>
              <DispatchIcon status={step.status} />
              <div>
                <strong>{step.agent_name}</strong>
                <p>{cleanBrainCopy(step.intent)}</p>
                <span>{dispatchStatusCopy(step.status)}</span>
              </div>
            </div>
          ))}
        </div>
      </section>

      {runtime && runtime.tool_calls.length > 0 && (
        <section>
          <h3>工具调用</h3>
          <div className="tz-execution-tools">
            {runtime.tool_calls.map((toolCall) => (
              <div key={toolCall.id}>
                <strong>{toolCallHumanName(toolCall)}</strong>
                <span>{toolCallStatusCopy(toolCall.status)}</span>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function DispatchIcon({ status }: { status: OrchestrationPlanStep["status"] }) {
  if (status === "done") return <CheckCircleFilled style={{ color: "var(--dy-success)" }} />;
  if (status === "blocked") return <ClockCircleFilled style={{ color: "var(--dy-warning)" }} />;
  return <span className="dy-brain-dispatch-dot" />;
}

function conversationItems(
  runtime: BrainRuntime,
  liveMessages: LiveRuntimeMessage[],
  pendingTurn: PendingTurn | null = null,
): ConversationItem[] {
  const items: ConversationItem[] = [];
  const invocationsById = new Map(runtime.invocations.map((row) => [row.id, row]));
  const invocationsByCode = new Map(
    runtime.invocations.map((row) => [String(row.agent_code), row]),
  );
  const representedInvocations = new Set<number>();
  const representedMessages = new Set<string>();
  const lifecycleByInvocation = expertLifecycleMessages(runtime);
  let hasUserMessage = false;

  runtime.timeline.forEach((event) => {
    const payload = asRuntimePayload(event.payload) ?? {};

    if (event.type === "brain.runtime.user_message") {
      const content = String(payload.message ?? payload.content ?? "").trim();
      if (content) {
        hasUserMessage = true;
        items.push({ kind: "user", id: `user-event-${event.id}`, content });
      }
      return;
    }

    if (["brain.runtime.message_done", "brain.runtime.message_error"].includes(event.type)) {
      const message = runtimeMessageFromEvent(runtime, event);
      representedMessages.add(message.id);
      if (message.agentCode && message.agentCode !== "00-decision") return;
      items.push({
        kind: "agent",
        id: `agent-event-${event.id}`,
        message,
      });
      return;
    }

    if (["brain.runtime.subagent_started", "brain.runtime.subagent_completed"].includes(event.type)) {
      const invocationId = Number(payload.invocation_id);
      const invocation = Number.isFinite(invocationId)
        ? invocationsById.get(invocationId)
        : invocationsByCode.get(String(payload.agent_code ?? ""));
      if (!invocation || representedInvocations.has(invocation.id)) return;
      representedInvocations.add(invocation.id);
      items.push({
        kind: "expert",
        id: `expert-${invocation.id}`,
        invocation,
        lifecycleMessage: lifecycleByInvocation.get(invocation.id) ?? null,
      });
      return;
    }

    if (event.type === "brain.runtime.handoff") {
      const content = String(payload.message ?? "").trim();
      if (content) items.push({ kind: "status", id: `handoff-${event.id}`, content });
    }
  });

  if (!hasUserMessage) {
    const userGoal = cleanBrainCopy(runtime.task.brief.goal).trim();
    if (userGoal) items.unshift({ kind: "user", id: `user-${runtime.task.id}`, content: userGoal });
  }

  runtime.invocations.forEach((invocation) => {
    if (representedInvocations.has(invocation.id)) return;
    items.push({
      kind: "expert",
      id: `expert-${invocation.id}`,
      invocation,
      lifecycleMessage: lifecycleByInvocation.get(invocation.id) ?? null,
    });
  });

  let hasPendingAgent = false;
  if (pendingTurn) {
    const hasPendingUser = runtime.timeline.some((event) => {
      if (event.type !== "brain.runtime.user_message") return false;
      const payload = asRuntimePayload(event.payload);
      return payload?.client_message_id === pendingTurn.clientMessageId;
    });
    hasPendingAgent = liveMessages.some((message) =>
      message.id.startsWith(pendingTurn.clientMessageId));
    if (pendingTurn.showUser && !hasPendingUser) {
      items.push({
        kind: "user",
        id: `user-pending-${pendingTurn.clientMessageId}`,
        content: pendingTurn.content,
      });
    }
  }

  liveMessages.forEach((message) => {
    if (representedMessages.has(message.id)) return;
    items.push({
      kind: "agent",
      id: `agent-live-${message.taskId}-${message.id}`,
      message,
    });
  });

  if (pendingTurn) {
    if (!hasPendingAgent) {
      items.push({
        kind: "agent",
        id: `agent-pending-${pendingTurn.clientMessageId}`,
        message: pendingAgentMessage(pendingTurn),
      });
    }
  }

  return items;
}

function pendingAgentMessage(turn: PendingTurn): LiveRuntimeMessage {
  return {
    id: `${turn.clientMessageId}:pending`,
    taskId: turn.taskId ?? -1,
    agentCode: "00-decision",
    agentName: "主 Agent",
    content: "",
    status: "streaming",
  };
}

function expertLifecycleMessages(runtime: BrainRuntime) {
  const byInvocation = new Map<number, string>();
  const invocationIdsByCode = new Map(
    runtime.invocations.map((row) => [String(row.agent_code), row.id]),
  );

  runtime.timeline.forEach((event) => {
    if (event.type !== "brain.runtime.subagent_completed") return;
    const payload = asRuntimePayload(event.payload) ?? {};
    const explicitId = Number(payload.invocation_id);
    const invocationId = Number.isFinite(explicitId)
      ? explicitId
      : invocationIdsByCode.get(String(payload.agent_code ?? ""));
    const message = String(payload.message ?? "").trim();
    if (invocationId != null && message) byInvocation.set(invocationId, message);
  });

  return byInvocation;
}

function runtimeMessageFromEvent(runtime: BrainRuntime, event: BrainRuntime["timeline"][number]) {
  const payload = asRuntimePayload(event.payload) ?? {};
  return {
    id: String(payload.message_id ?? event.id),
    taskId: runtime.task.id,
    agentCode: String(payload.agent_code ?? ""),
    agentName: String(payload.agent_name ?? "Agent"),
    model: typeof payload.model === "string" ? payload.model : undefined,
    content: String(payload.content ?? payload.message ?? payload.error ?? ""),
    status: event.type.endsWith("error") ? "error" : "done",
  } satisfies LiveRuntimeMessage;
}

function ingestRuntimeEvent(
  event: DyEvent,
  setLiveMessages: Dispatch<SetStateAction<LiveRuntimeMessage[]>>,
) {
  const payload = asRuntimePayload(event.payload);
  if (!payload || payload.task_id == null) return;

  if (event.type === "brain.runtime.generation_stopped") {
    const taskId = Number(payload.task_id);
    const clientMessageId = String(payload.client_message_id ?? "");
    if (!clientMessageId) return;
    setLiveMessages((prev) => prev.map((item) =>
      item.taskId === taskId && item.id.startsWith(clientMessageId)
        ? { ...item, status: "stopped" }
        : item
    ));
    return;
  }

  if (!payload.message_id) return;

  const taskId = Number(payload.task_id);
  const id = String(payload.message_id);
  const agentCode = String(payload.agent_code ?? "");
  const agentName = String(payload.agent_name ?? (agentCode || "Agent"));
  const model = typeof payload.model === "string" ? payload.model : undefined;

  if (event.type === "brain.runtime.message_start") {
    setLiveMessages((prev) =>
      upsertMessage(prev, { id, taskId, agentCode, agentName, model, content: "", status: "streaming" }),
    );
  }

  if (event.type === "brain.runtime.message_delta") {
    const delta = String(payload.delta ?? "");
    setLiveMessages((prev) =>
      upsertMessage(
        prev,
        { id, taskId, agentCode, agentName, model, content: delta, status: "streaming" },
        true,
      ),
    );
  }

  if (event.type === "brain.runtime.message_done") {
    const content = String(payload.content ?? payload.message ?? "");
    setLiveMessages((prev) =>
      upsertMessage(prev, { id, taskId, agentCode, agentName, model, content, status: "done" }),
    );
  }

  if (event.type === "brain.runtime.message_error") {
    const content = String(payload.error ?? payload.message ?? "模型调用失败");
    setLiveMessages((prev) =>
      upsertMessage(prev, { id, taskId, agentCode, agentName, model, content, status: "error" }),
    );
  }
}

function upsertMessage(
  messages: LiveRuntimeMessage[],
  next: LiveRuntimeMessage,
  append = false,
) {
  const index = messages.findIndex((item) => item.id === next.id && item.taskId === next.taskId);
  if (index < 0) return [...messages, next];

  return messages.map((item, itemIndex) =>
    itemIndex === index
      ? { ...item, ...next, content: append ? item.content + next.content : next.content }
      : item,
  );
}

function asRuntimePayload(payload: unknown): Record<string, unknown> | null {
  return typeof payload === "object" && payload != null
    ? (payload as Record<string, unknown>)
    : null;
}

function latestUserMessage(runtime: BrainRuntime) {
  for (let index = runtime.timeline.length - 1; index >= 0; index -= 1) {
    const event = runtime.timeline[index];
    if (event.type !== "brain.runtime.user_message") continue;
    const payload = asRuntimePayload(event.payload) ?? {};
    const content = String(payload.content ?? payload.message ?? "").trim();
    if (content) return content;
  }
  return cleanBrainCopy(runtime.task.brief.goal).trim();
}

function createClientMessageId() {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `brain-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function mergeTasks(localTasks: BrainTask[], serverTasks: BrainTask[]) {
  const byId = new Map<number, BrainTask>();
  [...localTasks, ...serverTasks].forEach((task) => byId.set(task.id, task));
  return Array.from(byId.values()).sort((a, b) => b.id - a.id);
}

function cleanBrainCopy(value: string) {
  return value.replace(/\bBrief\b/g, "工作流目标");
}

function formatAgentContent(value: string) {
  const cleaned = cleanBrainCopy(value).trim();
  const parsed = parseJsonObject(cleaned);
  if (!parsed) return cleaned;
  return objectToReadableSummary(parsed);
}

function parseJsonObject(value: string): Record<string, unknown> | null {
  if (!value.startsWith("{") || !value.endsWith("}")) return null;
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

function objectToReadableSummary(value: Record<string, unknown>) {
  return Object.entries(value)
    .map(([key, entry]) => readableEntry(readableKey(key), entry))
    .filter(Boolean)
    .join("\n\n");
}

function readableEntry(label: string, value: unknown): string {
  if (Array.isArray(value)) {
    const items = value
      .map((item) => `- ${readableValue(item)}`)
      .filter((item) => item.trim() !== "-");
    return items.length ? `${label}：\n${items.join("\n")}` : "";
  }
  if (value && typeof value === "object") {
    return `${label}：\n${objectToReadableSummary(value as Record<string, unknown>)}`;
  }
  const text = readableValue(value);
  return text ? `${label}：${text}` : "";
}

function readableValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return cleanBrainCopy(value).trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(readableValue).filter(Boolean).join("、");
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, entry]) => `${readableKey(key)}：${readableValue(entry)}`)
      .join("；");
  }
  return String(value);
}

function readableKey(key: string) {
  const labels: Record<string, string> = {
    account_persona: "账号定位",
    target_audience: "目标人群",
    differentiation: "差异化方向",
    content_pillars: "内容支柱",
    title: "标题",
    body: "正文",
    topics: "话题",
    conclusion: "核心结论",
    next_steps: "下一步",
    risks: "风险",
  };
  return labels[key] ?? key.replace(/_/g, " ");
}

function runtimeProgressCopy(runtime: BrainRuntime) {
  if (runtime.pending_permissions.length > 0) {
    return `等待你确认：${toolCallHumanName(runtime.pending_permissions[0])}`;
  }
  if (runtime.status === "waiting_permission") return "等待人工确认";
  if (runtime.status === "stopped") return "本轮生成已停止";
  if (runtime.status === "completed") {
    return runtime.invocations.length > 0
      ? "本轮专家协作已完成"
      : "主 Agent 已完成本轮回复";
  }
  if (runtime.status === "failed") return "运行失败，需要处理";
  const runningExpert = runtime.invocations.find((item) => item.status === "running");
  if (runningExpert) return `${runningExpert.agent_name} 正在处理`;
  const lastExpert = [...runtime.invocations].reverse().find((item) => item.status === "done");
  if (lastExpert) return `${lastExpert.agent_name} 已完成，主 Agent 正在推进下一步`;
  return "主 Agent 正在理解目标并组织专家";
}

function expertRoleCopy(code: AgentInvocation["agent_code"]) {
  const roles: Record<AgentInvocation["agent_code"], string> = {
    "00-decision": "理解目标、拆解任务、调度专家。",
    "01-positioning": "判断账号定位、目标人群与平台匹配度。",
    "02-content-director": "生成内容策略、脚本方向和表达结构。",
    "03-art-director": "整理视觉风格、画面要求和提示词。",
    "04-video-creator": "规划视频素材、镜头和生成路径。",
    "05-editor": "处理剪辑节奏、字幕和成片要求。",
    "06-operator": "负责发布准备、复盘路径和运营建议。",
    "07-advertiser": "评估投放策略和增长动作。",
    "08-customer-service": "处理反馈、评论和服务线索。",
  };
  return roles[code] ?? "专业 Agent";
}

function expertStatusCopy(status: AgentInvocation["status"]) {
  const labels: Record<AgentInvocation["status"], string> = {
    queued: "等待中",
    running: "分析中",
    done: "已完成",
    failed: "失败",
    blocked: "阻塞",
  };
  return labels[status] ?? status;
}

function toolCallHumanName(toolCall: AgentToolCall) {
  const names: Record<string, string> = {
    publish_package_prepare: "生成发布前检查清单",
    compliance_precheck: "执行合规预检查",
    material_validator: "校验素材与封面",
    brief_builder: "整理任务目标",
  };
  return names[toolCall.tool_code] ?? toolCall.tool_name;
}

function isConfigurationError(value: string) {
  return /API_KEY is not configured|not configured|LLMError|all candidate models failed/i.test(value);
}

function syncLabel(status: Account["data_sync_status"]) {
  const labels: Record<Account["data_sync_status"], string> = {
    not_configured: "未配置",
    pending: "待同步",
    syncing: "同步中",
    healthy: "正常",
    failed: "失败",
    manual: "手动",
  };
  return labels[status] ?? status;
}

function runtimeStatusLabel(status: string) {
  const labels: Record<string, string> = {
    legacy: "旧任务",
    running: "运行中",
    waiting_permission: "等待人工确认",
    completed: "已完成",
    stopped: "已停止",
    failed: "失败",
    pending_acceptance: "待验收",
  };
  return labels[status] ?? status;
}

function dispatchStatusCopy(status: OrchestrationPlanStep["status"]) {
  const labels: Record<OrchestrationPlanStep["status"], string> = {
    planned: "等待中",
    running: "执行中",
    done: "已完成",
    blocked: "等待确认",
    failed: "执行失败",
    skipped: "已跳过",
  };
  return labels[status] ?? status;
}

function toolCallStatusCopy(status: AgentToolCall["status"]) {
  const labels: Record<AgentToolCall["status"], string> = {
    planned: "等待中",
    running: "执行中",
    waiting_approval: "等待确认",
    success: "已完成",
    failed: "执行失败",
    blocked: "已阻塞",
    skipped: "已跳过",
  };
  return labels[status] ?? status;
}

function acceptanceStatusCopy(status: DeliverableAcceptance["status"]) {
  const labels: Record<DeliverableAcceptance["status"], string> = {
    pending: "待验收",
    approved: "已采用",
    rejected: "已驳回",
    rerun_requested: "重做中",
  };
  return labels[status] ?? status;
}

function statusCopy(status: LiveRuntimeMessage["status"]) {
  if (status === "done") return "完成";
  if (status === "error") return "失败";
  if (status === "stopped") return "已停止";
  return "进行中";
}
