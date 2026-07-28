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
import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import {
  acceptArtifact,
  approveDeliverableAcceptance,
  approveToolCall,
  createConversation,
  getConversation,
  getBrainTaskRuntime,
  listBrainTasks,
  listComposerSkills,
  rejectDeliverableAcceptance,
  reviseArtifact,
  regenerateBrainMessage,
  refreshBrainObservation,
  reviseBrainDecision,
  selectBrainDecision,
  sendBrainMessage,
  sendConversationTurn,
  stopBrainGeneration,
  verifyBrainExperienceCandidate,
} from "../api/brain";
import { presentApiError } from "../api/errors";
import { getWorkspaceContext } from "../api/shell";
import { AgentAvatar } from "../components/agents/AgentAvatar";
import { OperationalState } from "../components/ui";
import { BrainComposer } from "../components/brain/BrainComposer";
import { DecisionRequest } from "../components/brain/DecisionRequest";
import { TurnStream } from "../components/brain/TurnStream";
import { ArtifactCard, businessArtifactTitle, type ArtifactAction } from "../components/brain/ArtifactCard";
import { ArtifactCenter } from "../components/brain/ArtifactCenter";
import {
  useEventStream,
  type DyEvent,
  type EventStreamConnectionState,
} from "../hooks/useEventStream";
import {
  clearActiveBrainTaskId,
  clearActiveConversationThreadId,
  getActiveConversationThreadId,
  getActiveBrainTaskId,
  setActiveConversationThreadId as persistActiveConversationThreadId,
  setActiveBrainTaskId,
} from "../stores/brainConversation";
import { useAuth } from "../stores/auth";
import {
  resolveWorkspaceAccount,
  useCurrentWorkspace,
} from "../stores/currentWorkspace";
import {
  OPERATIONS_BRAIN_DISPLAY_NAME,
  presentOperationsBrainSystemCopy,
} from "../utils/operationsBrainCopy";
import type {
  Account,
  AgentInvocation,
  AgentToolCall,
  Artifact,
  BrainRuntime,
  BrainTask,
  ConversationThread,
  DeliverableAcceptance,
  TurnSubmission,
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

interface SourceReturnTarget {
  accountId: number;
  threadId: number;
  turnId: number;
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
  | { kind: "failure"; id: string; content: string; recoveryAction: string }
  | { kind: "status"; id: string; content: string };

export default function BrainHome() {
  const { message } = AntApp.useApp();
  const qc = useQueryClient();
  const [goal, setGoal] = useState("");
  const [localTasks, setLocalTasks] = useState<BrainTask[]>([]);
  const [activeRuntimeTaskId, setActiveRuntimeTaskId] = useState<number | null>(null);
  const [activeConversationThreadId, setActiveConversationThreadId] = useState<number | null>(null);
  const [liveMessages, setLiveMessages] = useState<LiveRuntimeMessage[]>([]);
  const [pendingTurn, setPendingTurn] = useState<PendingTurn | null>(null);
  const [launcherPending, setLauncherPending] = useState(false);
  const [approvalComment, setApprovalComment] = useState("");
  const [artifactRefreshKey, setArtifactRefreshKey] = useState(0);
  const [artifactRevisionChains, setArtifactRevisionChains] = useState<Record<number, Artifact[]>>({});
  const [artifactSourceOverrides, setArtifactSourceOverrides] = useState<Record<number, Artifact>>({});
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [workspaceMode, setWorkspaceMode] = useState<"conversation" | "results">("conversation");
  const [selectedArtifact, setSelectedArtifact] = useState<Artifact | null>(null);
  const [sourceReturnTarget, setSourceReturnTarget] = useState<SourceReturnTarget | null>(null);
  const [sourceReturnError, setSourceReturnError] = useState<string | null>(null);
  const isAdmin = useAuth((state) => state.user?.role === "admin");
  const pendingClientMessageId = useRef<string | null>(null);
  const launcherRequestInFlight = useRef(false);
  const effectiveAccountIdRef = useRef<number | null>(null);
  const conversationRef = useRef<HTMLElement | null>(null);
  const followLatestMessage = useRef(true);
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
  effectiveAccountIdRef.current = activeAccount?.id ?? null;
  const tasksQuery = useQuery({
    queryKey: ["brain-tasks"],
    queryFn: listBrainTasks,
    enabled: Boolean(activeAccount),
  });
  const composerSkillsQuery = useQuery({
    queryKey: ["composer-skills", "douyin"],
    queryFn: () => listComposerSkills("douyin"),
  });

  const { connectionState } = useEventStream((event) => {
    if (!event.type.startsWith("brain.runtime.")) return;
    const payload = asRuntimePayload(event.payload);
    const eventClientMessageId = typeof payload?.client_message_id === "string"
      ? payload.client_message_id
      : null;
    const eventTaskId = payload?.task_id == null ? null : Number(payload.task_id);
    const eventThreadId = payload?.thread_id == null ? null : Number(payload.thread_id);
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
    if (
      activeConversationThreadId != null
      && eventThreadId === activeConversationThreadId
      && eventClientMessageId === pendingClientMessageId.current
      && isTerminalConversationRuntimeEvent(event.type)
    ) {
      setPendingTurn((current) => current?.clientMessageId === eventClientMessageId ? null : current);
      pendingClientMessageId.current = null;
    }
    if (!["brain.runtime.message_start", "brain.runtime.message_delta"].includes(event.type)) {
      qc.invalidateQueries({ queryKey: ["brain-tasks"] });
      qc.invalidateQueries({ queryKey: ["brain-runtime"] });
      if (eventThreadId === activeConversationThreadId) {
        qc.invalidateQueries({ queryKey: ["brain-conversation", activeConversationThreadId] });
      }
    }
  }, {
    onReconnect: () => {
      void qc.invalidateQueries({ queryKey: ["brain-tasks"] });
      void qc.invalidateQueries({ queryKey: ["brain-runtime"] });
      if (activeConversationThreadId != null) {
        void qc.invalidateQueries({ queryKey: ["brain-conversation", activeConversationThreadId] });
      }
    },
  });

  const effectiveAccount = activeAccount;
  const accountReady = Boolean(
    effectiveAccount &&
    (effectiveAccount.auth_status === "authorized" || effectiveAccount.auth_status === "manual"),
  );
  const selectCenterArtifact = useCallback((artifact: Artifact | null) => {
    setSourceReturnError(null);
    setSelectedArtifact(artifact && artifact.account_id === effectiveAccount?.id ? artifact : null);
  }, [effectiveAccount?.id]);

  useEffect(() => {
    setActiveConversationThreadId(
      effectiveAccount ? getActiveConversationThreadId(effectiveAccount.id) : null,
    );
    setLiveMessages([]);
    setPendingTurn(null);
    setSelectedArtifact(null);
    setSourceReturnTarget(null);
    setSourceReturnError(null);
  }, [effectiveAccount?.id]);

  const conversationTurnMutation = useMutation({
    mutationFn: ({
      threadId,
      message,
      clientMessageId,
      requestedSkillCode = null,
    }: {
      threadId: number;
      message: string;
      clientMessageId: string;
      requestedSkillCode?: string | null;
      accountId?: number;
    }) => sendConversationTurn(threadId, requestedSkillCode == null ? {
      client_message_id: clientMessageId,
      message,
    } : {
      client_message_id: clientMessageId,
      message,
      requested_skill_code: requestedSkillCode,
      execution_preference: "AUTO",
      attachment_ids: [],
    }),
    onSuccess: (submission, variables) => {
      qc.setQueryData<ConversationThread>(["brain-conversation", variables.threadId], (current) =>
        mergeConversationTurn(current, submission),
      );
      void qc.invalidateQueries({ queryKey: ["brain-conversation", variables.threadId] });
      if (
        variables.accountId != null
        && effectiveAccountIdRef.current !== variables.accountId
      ) return;
      if (isTerminalConversationRunStatus(submission.run.status)) {
        setPendingTurn(null);
        pendingClientMessageId.current = null;
        return;
      }
      const clientMessageId = submission.run.client_message_id || variables.clientMessageId;
      const taskId = submission.task_id ?? submission.run.task_id;
      setPendingTurn((current) => current?.clientMessageId === variables.clientMessageId
        ? { ...current, clientMessageId, taskId: taskId ?? current.taskId }
        : current);
      pendingClientMessageId.current = clientMessageId;
    },
    onError: (error, variables) => {
      if (
        variables.accountId != null
        && effectiveAccountIdRef.current !== variables.accountId
      ) return;
      setPendingTurn((current) => {
        if (current) setGoal((value) => value || current.content);
        return null;
      });
      pendingClientMessageId.current = null;
      message.error(presentApiError(error, "Conversation turn failed.").message);
    },
  });

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
    onSettled: () => {
      if (activeConversationThreadId != null) {
        void qc.invalidateQueries({ queryKey: ["brain-conversation", activeConversationThreadId] });
      }
    },
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
      if (activeConversationThreadId != null) {
        void qc.invalidateQueries({ queryKey: ["brain-conversation", activeConversationThreadId] });
      }
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

  const formalArtifactAcceptMutation = useMutation({
    mutationFn: (input: { sourceArtifact: Artifact; createNextStep: boolean }) => acceptArtifact(input.sourceArtifact.id),
    onSuccess: (accepted, input) => {
      if (!matchesArtifactResponse(accepted, input.sourceArtifact, "accept")) {
        message.error("成果返回校验失败，请重试。");
        return;
      }
      setArtifactRevisionChains((current) => updateExistingArtifactChain(current, accepted));
      setSelectedArtifact((current) => current?.id === input.sourceArtifact.id
        && current.account_id === input.sourceArtifact.account_id
        ? accepted
        : current);
      setArtifactRefreshKey((value) => value + 1);
      void qc.invalidateQueries({ queryKey: ["account-artifacts", input.sourceArtifact.account_id] });
      if (activeConversationThreadId != null) {
        void qc.invalidateQueries({ queryKey: ["brain-conversation", activeConversationThreadId] });
      }
      if (input.createNextStep) {
        setGoal(nextStepGoal(accepted));
      }
      message.success("报告已采用");
    },
    onError: (error) => message.error(
      presentApiError(error, "报告采用失败，请稍后重试。").message,
    ),
  });

  const formalArtifactRevisionMutation = useMutation({
    mutationFn: (input: { sourceArtifact: Artifact; payload: Record<string, unknown>; note: string }) =>
      reviseArtifact({
        artifactId: input.sourceArtifact.id,
        payload: input.payload,
        note: input.note,
      }),
    onSuccess: (revision, input) => {
      if (!matchesArtifactResponse(revision, input.sourceArtifact, "revision")) {
        message.error("修订成果校验失败，请重试。");
        return;
      }
      setArtifactRevisionChains((current) => appendArtifactRevision(current, input.sourceArtifact, revision));
      setArtifactSourceOverrides((current) => supersedeRootArtifact(current, input.sourceArtifact));
      setSelectedArtifact((current) => current?.id === input.sourceArtifact.id
        && current.account_id === input.sourceArtifact.account_id
        ? revision
        : current);
      setArtifactRefreshKey((value) => value + 1);
      void qc.invalidateQueries({ queryKey: ["account-artifacts", input.sourceArtifact.account_id] });
      if (activeConversationThreadId != null) {
        void qc.invalidateQueries({ queryKey: ["brain-conversation", activeConversationThreadId] });
      }
      message.success("修改请求已提交，正在生成新版本");
    },
    onError: (error) => message.error(
      presentApiError(error, "修改请求提交失败，请稍后重试。").message,
    ),
  });

  const refreshObservationMutation = useMutation({
    mutationFn: (taskId: number) => refreshBrainObservation(taskId),
    onSuccess: (reflection, taskId) => {
      qc.setQueryData<BrainRuntime>(["brain-runtime", taskId], (current) =>
        current ? { ...current, reflection } : current
      );
      void qc.invalidateQueries({
        queryKey: ["brain-runtime", taskId],
        refetchType: "none",
      });
      if (reflection.status === "observed") {
        message.success("已回收最新真实效果数据");
      } else {
        message.info("当前观察窗口尚未满足，系统会保留待回查状态");
      }
    },
    onError: (error) => message.error(
      presentApiError(error, "真实效果回查失败，请稍后重试。").message,
    ),
  });

  const verifyExperienceMutation = useMutation({
    mutationFn: (input: {
      taskId: number;
      candidateKey: string;
      verificationNote: string;
    }) => verifyBrainExperienceCandidate(input),
    onSuccess: (memory, input) => {
      qc.setQueryData<BrainRuntime>(["brain-runtime", input.taskId], (current) => {
        if (!current) return current;
        const memories = current.experience_memories ?? [];
        return {
          ...current,
          experience_memories: [
            ...memories.filter((item) => item.id !== memory.id),
            memory,
          ],
        };
      });
      message.success("该经验已通过人工核验并沉淀");
    },
    onError: (error) => message.error(
      presentApiError(error, "经验核验失败，请检查依据后重试。").message,
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
  const conversationQuery = useQuery({
    queryKey: ["brain-conversation", activeConversationThreadId],
    queryFn: () => getConversation(activeConversationThreadId!),
    enabled: activeConversationThreadId != null,
  });
  const activeConversation =
    activeConversationThreadId != null
    && effectiveAccount != null
    && conversationQuery.data?.id === activeConversationThreadId
    && conversationQuery.data.account_id === effectiveAccount.id
      ? conversationQuery.data
      : null;
  const runtime =
    activeRuntimeTaskId != null
    && effectiveAccount != null
    && runtimeQuery.data?.task.id === activeRuntimeTaskId
    && runtimeQuery.data.task.brief.account_ids.includes(effectiveAccount.id)
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
  const visibleTasksError = activeConversationThreadId == null ? tasksError : null;
  const runtimeError = runtimeQuery.isError
    ? presentApiError(runtimeQuery.error, "当前任务运行时暂时不可用。")
    : null;
  const conversationError = conversationQuery.isError
    ? presentApiError(conversationQuery.error, "Conversation history is temporarily unavailable.")
    : null;

  useEffect(() => {
    if (!sourceReturnTarget) return;
    if (sourceReturnTarget.accountId !== effectiveAccount?.id) {
      setSourceReturnTarget(null);
      return;
    }
    if (conversationQuery.isError) {
      setSourceReturnError("来源对话暂时无法加载，请在成果中心重试。");
      setSourceReturnTarget(null);
      return;
    }
    const source = conversationQuery.data;
    if (!source) return;
    if (source.id !== sourceReturnTarget.threadId || source.account_id !== sourceReturnTarget.accountId) {
      setSourceReturnError("来源对话与当前成果不匹配，请在成果中心重试。");
      setSourceReturnTarget(null);
      return;
    }
    if (!source.turns.some((turn) => turn.id === sourceReturnTarget.turnId)) {
      setSourceReturnError("来源对话未包含该成果所在轮次，请在成果中心重试。");
      setSourceReturnTarget(null);
      return;
    }
    setWorkspaceMode("conversation");
  }, [conversationQuery.data, conversationQuery.isError, effectiveAccount?.id, sourceReturnTarget]);

  useEffect(() => {
    if (
      workspaceMode !== "conversation"
      || !sourceReturnTarget
      || activeConversation?.id !== sourceReturnTarget.threadId
    ) return;
    const node = document.querySelector<HTMLElement>(`[data-turn-id="${sourceReturnTarget.turnId}"]`);
    if (!node) return;
    node.setAttribute("tabindex", "-1");
    node.scrollIntoView({ block: "center" });
    node.focus({ preventScroll: true });
    setSourceReturnTarget(null);
  }, [activeConversation, sourceReturnTarget, workspaceMode]);
  const isGenerating =
    messageMutation.isPending
    || regenerateMutation.isPending
    || conversationTurnMutation.isPending
    || launcherPending
    || (activeConversationThreadId != null && pendingTurn != null);

  const startWorkflow = () => {
    if (isGenerating) return;
    const trimmed = goal.trim();
    if (!trimmed) {
      message.warning("先写下要交给运营大脑的运营目标");
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
    followLatestMessage.current = true;
    pendingClientMessageId.current = clientMessageId;
    setPendingTurn({
      clientMessageId,
      content: trimmed,
      taskId: activeConversationThreadId == null ? activeTask?.id ?? null : null,
      showUser: true,
    });
    setGoal("");
    if (activeConversationThreadId != null) {
      conversationTurnMutation.mutate({
        threadId: activeConversationThreadId,
        message: trimmed,
        clientMessageId,
      });
      return;
    }
    messageMutation.mutate({
      message: trimmed,
      client_message_id: clientMessageId,
      task_id: activeTask?.id,
      project_id: projectId,
      account_id: effectiveAccount.id,
      platform: "douyin",
    });
  };

  const requestAccountSelection = useCallback(() => {
    const selector = document.querySelector<HTMLButtonElement>('[aria-label="当前账号"]');
    if (selector) {
      selector.focus();
      selector.click();
      return;
    }
    message.info("请在顶部选择抖音账号");
  }, [message]);

  const launchComposerSkill = useCallback(async (skillCode: string) => {
    if (launcherRequestInFlight.current || isGenerating) return;
    const skill = composerSkillsQuery.data?.find((item) => item.code === skillCode);
    if (!skill || !skill.is_available) return;
    const account = effectiveAccount;
    if (!account) {
      requestAccountSelection();
      return;
    }
    if (!accountReady) {
      message.warning("当前账号尚未完成授权，请先完成账号授权后再执行");
      return;
    }

    launcherRequestInFlight.current = true;
    setLauncherPending(true);
    const clientMessageId = createClientMessageId();
    try {
      const savedThreadId = getActiveConversationThreadId(account.id);
      const thread = savedThreadId != null
        ? { id: savedThreadId, account_id: account.id }
        : await createConversation({ account_id: account.id });
      if (
        thread.account_id !== account.id
        || effectiveAccountIdRef.current !== account.id
      ) return;

      if (savedThreadId == null) {
        persistActiveConversationThreadId(account.id, thread.id);
        setActiveConversationThreadId(thread.id);
      }
      followLatestMessage.current = true;
      pendingClientMessageId.current = clientMessageId;
      setPendingTurn({
        clientMessageId,
        content: skill.name,
        taskId: null,
        showUser: true,
      });
      await conversationTurnMutation.mutateAsync({
        threadId: thread.id,
        message: skill.name,
        clientMessageId,
        requestedSkillCode: skill.code,
        accountId: account.id,
      });
    } catch (error) {
      setPendingTurn((current) => current?.clientMessageId === clientMessageId ? null : current);
      pendingClientMessageId.current = null;
      message.error(presentApiError(error, "启动能力失败，请稍后重试。").message);
    } finally {
      launcherRequestInFlight.current = false;
      setLauncherPending(false);
    }
  }, [
    accountReady,
    composerSkillsQuery.data,
    conversationTurnMutation,
    effectiveAccount,
    isGenerating,
    message,
    requestAccountSelection,
  ]);

  const stopGeneration = () => {
    if (!pendingTurn || stopMutation.isPending) return;
    stopMutation.mutate({
      clientMessageId: pendingTurn.clientMessageId,
      taskId: pendingTurn.taskId,
    });
  };

  const regenerateLastTurn = () => {
    if (activeConversation && !isGenerating) {
      const sourceMessage = activeConversation.turns.at(-1)?.user_input.trim();
      if (!sourceMessage) return;
      const clientMessageId = createClientMessageId();
      pendingClientMessageId.current = clientMessageId;
      setPendingTurn({ clientMessageId, content: sourceMessage, taskId: null, showUser: false });
      conversationTurnMutation.mutate({
        threadId: activeConversation.id,
        message: sourceMessage,
        clientMessageId,
      });
      return;
    }
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
    if (effectiveAccount) {
      clearActiveBrainTaskId(effectiveAccount.id);
      clearActiveConversationThreadId(effectiveAccount.id);
    }
    setActiveRuntimeTaskId(null);
    setActiveConversationThreadId(null);
    setLiveMessages([]);
    setPendingTurn(null);
    setApprovalComment("");
    setGoal("");
    setDetailsOpen(false);
    setSourceReturnTarget(null);
    setSourceReturnError(null);
  };

  const returnToArtifactSource = (retry = false) => {
    if (
      !selectedArtifact
      || !effectiveAccount
      || selectedArtifact.account_id !== effectiveAccount.id
      || selectedArtifact.thread_id == null
      || selectedArtifact.turn_id == null
    ) return;
    const target: SourceReturnTarget = {
      accountId: effectiveAccount.id,
      threadId: selectedArtifact.thread_id,
      turnId: selectedArtifact.turn_id,
    };
    setSourceReturnError(null);
    persistActiveConversationThreadId(effectiveAccount.id, selectedArtifact.thread_id);
    setActiveConversationThreadId(selectedArtifact.thread_id);
    if (retry) {
      void conversationQuery.refetch().then((result) => {
        if (result.data) setSourceReturnTarget(target);
        else setSourceReturnError("来源对话暂时无法加载，请在成果中心重试。");
      });
      return;
    }
    setSourceReturnTarget(target);
  };

  const hasConversation = Boolean(activeTask || activeConversationThreadId || pendingTurn);

  useEffect(() => {
    followLatestMessage.current = true;
  }, [activeRuntimeTaskId]);

  useEffect(() => {
    if (!hasConversation || !followLatestMessage.current) return;
    const frame = window.requestAnimationFrame(() => {
      const conversation = conversationRef.current;
      if (!conversation) return;
      if (typeof conversation.scrollTo === "function") {
        conversation.scrollTo({ top: conversation.scrollHeight, behavior: "auto" });
      } else {
        conversation.scrollTop = conversation.scrollHeight;
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [
    activeRuntimeTaskId,
    activeConversationThreadId,
    activeConversation,
    conversationQuery.dataUpdatedAt,
    hasConversation,
    isGenerating,
    liveMessages,
    pendingTurn,
    runtimeError,
    tasksError,
    visibleRuntime,
  ]);

  const handleConversationScroll = () => {
    const conversation = conversationRef.current;
    if (!conversation) return;
    const distanceFromBottom =
      conversation.scrollHeight - conversation.scrollTop - conversation.clientHeight;
    followLatestMessage.current = distanceFromBottom <= 96;
  };

  return (
    <div className={`tz-brain-page${hasConversation ? " has-conversation" : " is-empty"}${workspaceMode === "results" ? " is-results" : ""}`}>
      {hasConversation ? <header className="tz-brain-toolbar">
        <div className="tz-brain-identity">
          <AgentAvatar code="00-decision" className="tz-brain-wordmark" />
          <div>
            <strong>运营大脑</strong>
            <span>运营大脑 · 目标理解与专家编排</span>
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
            description="当前账号选择不会被替换。重新加载后，运营大脑会继续使用顶部明确选择的账号。"
            diagnostic={contextError.diagnostic}
            actionLabel="重新加载"
            onAction={() => void contextQuery.refetch()}
          />
        ) : <div className="tz-brain-thread">
          <ContextStrip
            account={effectiveAccount}
            loading={contextQuery.isLoading}
            workspaceMode={workspaceMode}
            onWorkspaceModeChange={setWorkspaceMode}
          />

          <section
            ref={conversationRef}
            className="dy-brain-conversation"
            aria-label="运营大脑对话流"
            onScroll={handleConversationScroll}
          >
            {workspaceMode === "results" ? (
              <div className="tz-artifact-center-panel">
                <ArtifactCenter
                  key={effectiveAccount?.id ?? "unavailable-account"}
                  accountId={effectiveAccount?.id ?? null}
                  onSelect={selectCenterArtifact}
                />
                {sourceReturnError ? (
                  <div className="tz-artifact-center__error" role="alert">
                    <p>{sourceReturnError}</p>
                    <Button onClick={() => returnToArtifactSource(true)}>重试返回来源对话</Button>
                  </div>
                ) : null}
                {selectedArtifact && selectedArtifact.account_id === effectiveAccount?.id ? (
                  <section className="tz-artifact-center__detail" aria-label="Artifact detail">
                    <ArtifactCard
                      artifact={selectedArtifact}
                      revisionPending={
                        formalArtifactRevisionMutation.isPending
                        && formalArtifactRevisionMutation.variables?.sourceArtifact.id === selectedArtifact.id
                      }
                      onAction={(action) => handleArtifactAction(
                        action,
                        formalArtifactAcceptMutation.mutate,
                        formalArtifactRevisionMutation.mutate,
                      )}
                    />
                    {selectedArtifact.thread_id != null && selectedArtifact.turn_id != null ? (
                      <Button onClick={() => returnToArtifactSource()} aria-label="返回来源对话">
                        返回来源对话
                      </Button>
                    ) : null}
                  </section>
                ) : null}
              </div>
            ) : visibleTasksError ? (
              <OperationalState
                kind="error"
                title="任务记录加载失败"
                description={`${visibleTasksError.message} 当前账号选择和已保存会话不会被修改。`}
                diagnostic={visibleTasksError.diagnostic}
                actionLabel="重试"
                onAction={() => void tasksQuery.refetch()}
              />
            ) : activeConversationThreadId != null && conversationError ? (
              <OperationalState
                kind="error"
                title="对话记录加载失败"
                description={`${conversationError.message} 当前账号不会显示其他账号的对话记录。`}
                diagnostic={conversationError.diagnostic}
                actionLabel="重试"
                onAction={() => void conversationQuery.refetch()}
              />
            ) : activeConversation ? (
              <>
                <TurnStream
                  thread={activeConversation}
                  artifactRefreshKey={artifactRefreshKey}
                  revisionArtifacts={artifactRevisionChains}
                  sourceArtifactOverrides={artifactSourceOverrides}
                  revisingArtifactId={
                    formalArtifactRevisionMutation.isPending
                      ? formalArtifactRevisionMutation.variables?.sourceArtifact.id ?? null
                      : null
                  }
                  approvingToolCallId={
                    approveMutation.isPending ? approveMutation.variables?.toolCallId ?? null : null
                  }
                  approvalComment={approvalComment}
                  onApprovalCommentChange={setApprovalComment}
                  onApprove={(approval, approved, comment) => approveMutation.mutate({
                    toolCallId: approval.id,
                    approved,
                    comment,
                  })}
                  onArtifactAction={(action) => handleArtifactAction(
                    action,
                    formalArtifactAcceptMutation.mutate,
                    formalArtifactRevisionMutation.mutate,
                  )}
                />
                {pendingTurn ? <PendingConversation turn={pendingTurn} /> : null}
                {!pendingTurn ? (
                  <Button aria-label="Regenerate V2 turn" type="text" onClick={regenerateLastTurn}>
                    Regenerate
                  </Button>
                ) : null}
              </>
            ) : activeConversationThreadId != null ? (
              <div className="tz-turn-stream" aria-live="polite">Loading conversation…</div>
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

          {workspaceMode === "conversation" ? <BrainComposer
            value={goal}
            disabled={
              isGenerating
              || (activeConversationThreadId == null && (tasksQuery.isError || runtimeQuery.isError))
            }
            loading={isGenerating}
            skills={composerSkillsQuery.data ?? []}
            onSelectSkill={launchComposerSkill}
            onAddFilesAndMaterials={() => message.info("尚未接入文件或素材附件")}
            onAddAccountDataPackage={() => message.info("尚未接入账号数据包附件")}
            onAddHistoricalArtifacts={() => setWorkspaceMode("results")}
            onSelectAccount={requestAccountSelection}
            pendingPermission={pendingPermission}
            approvalComment={approvalComment}
            approving={approveMutation.isPending}
            onChange={setGoal}
            onApprovalCommentChange={setApprovalComment}
            onApprovePermission={(toolCallId, approved, comment) =>
              approveMutation.mutate({ toolCallId, approved, comment })
            }
            onSubmit={startWorkflow}
            onStop={stopGeneration}
          /> : null}
        </div>}
      </main>

      <Drawer
        title="执行详情"
        width={440}
        open={detailsOpen}
        onClose={() => setDetailsOpen(false)}
        className="tz-brain-details-drawer"
      >
        <ExecutionDetails
          task={visibleTask}
          runtime={visibleRuntime}
          isAdmin={isAdmin}
          refreshingObservation={refreshObservationMutation.isPending}
          verifyingCandidateKey={
            verifyExperienceMutation.isPending
              ? verifyExperienceMutation.variables?.candidateKey ?? null
              : null
          }
          onRefreshObservation={(taskId) => refreshObservationMutation.mutate(taskId)}
          onVerifyCandidate={(input) => verifyExperienceMutation.mutate(input)}
        />
      </Drawer>
    </div>
  );
}

function ContextStrip({
  account,
  loading,
  workspaceMode,
  onWorkspaceModeChange,
}: {
  account: Account | null;
  loading: boolean;
  workspaceMode: "conversation" | "results";
  onWorkspaceModeChange: (mode: "conversation" | "results") => void;
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
        <div className="tz-brain-mode-switch" aria-label="Brain workspace mode">
          <Button
            type={workspaceMode === "conversation" ? "primary" : "text"}
            size="small"
            onClick={() => onWorkspaceModeChange("conversation")}
            aria-label="对话视图"
          >
            对话
          </Button>
          <Button
            type={workspaceMode === "results" ? "primary" : "text"}
            size="small"
            onClick={() => onWorkspaceModeChange("results")}
            aria-label="成果视图"
          >
            成果
          </Button>
        </div>
        <Tag style={{ marginInlineEnd: 0 }}>抖音</Tag>
        {account && <Tag style={{ marginInlineEnd: 0 }}>{syncLabel(account.data_sync_status)}</Tag>}
      </div>
    </section>
  );
}

function handleArtifactAction(
  action: ArtifactAction,
  accept: (input: { sourceArtifact: Artifact; createNextStep: boolean }) => void,
  revise: (input: { sourceArtifact: Artifact; payload: Record<string, unknown>; note: string }) => void,
) {
  if (action.type === "accept") {
    accept({ sourceArtifact: action.artifact, createNextStep: false });
    return;
  }
  if (action.type === "accept_and_continue") {
    accept({ sourceArtifact: action.artifact, createNextStep: true });
    return;
  }
  if (action.type === "request_revision") {
    revise({
      sourceArtifact: action.artifact,
      payload: buildArtifactRevisionPayload(action.artifact),
      note: action.note,
    });
  }
}

function matchesArtifactResponse(
  returned: Artifact,
  source: Artifact,
  operation: "accept" | "revision",
) {
  const sameScope = returned.account_id === source.account_id
    && returned.thread_id === source.thread_id
    && returned.turn_id === source.turn_id
    && returned.artifact_type === source.artifact_type;

  return sameScope && (operation === "accept"
    ? returned.id === source.id && returned.version === source.version
    : returned.id !== source.id && returned.version === source.version + 1);
}

function updateExistingArtifactChain(
  chains: Record<number, Artifact[]>,
  accepted: Artifact,
) {
  for (const [sourceId, chain] of Object.entries(chains)) {
    const versionIndex = chain.findIndex((artifact) => artifact.id === accepted.id);
    if (versionIndex >= 0) {
      const nextChain = [...chain];
      nextChain[versionIndex] = accepted;
      return { ...chains, [sourceId]: nextChain };
    }
  }
  return chains;
}

function appendArtifactRevision(
  chains: Record<number, Artifact[]>,
  sourceArtifact: Artifact,
  revision: Artifact,
) {
  const revisedArtifactId = sourceArtifact.id;
  const sourceId = Object.entries(chains).find(([rootId, chain]) =>
    Number(rootId) === revisedArtifactId || chain.some((artifact) => artifact.id === revisedArtifactId),
  )?.[0] ?? String(revisedArtifactId);
  const currentChain = chains[Number(sourceId)] ?? [];
  const existingIndex = currentChain.findIndex((artifact) => artifact.id === revision.id);
  const supersededChain = currentChain.map((artifact) => artifact.id === revisedArtifactId
    ? { ...artifact, status: "superseded" }
    : artifact);
  const nextChain = existingIndex >= 0
    ? supersededChain.map((artifact, index) => index === existingIndex ? revision : artifact)
    : [...supersededChain, revision];

  return {
    ...chains,
    [sourceId]: [...nextChain].sort((left, right) => left.version - right.version),
  };
}

function supersedeRootArtifact(overrides: Record<number, Artifact>, sourceArtifact: Artifact) {
  return sourceArtifact.version === 1
    ? { ...overrides, [sourceArtifact.id]: { ...sourceArtifact, status: "superseded" as const } }
    : overrides;
}

function buildArtifactRevisionPayload(artifact: Artifact): Record<string, unknown> {
  return {
    title: artifact.title,
    summary: artifact.summary,
    ...Object.fromEntries(artifact.sections.map((section) => [section.key, section.content])),
  };
}

function nextStepGoal(artifact: Artifact) {
  return `已采用《${businessArtifactTitle(artifact)}》（成果 #${artifact.id}）。请基于该报告提出下一步执行建议。`;
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
        <AgentAvatar code="00-decision" className="tz-brain-welcome__avatar" />
        <strong>运营大脑</strong>
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
        if (item.kind === "failure") {
          return (
            <RuntimeFailureMessage
              key={item.id}
              content={item.content}
              recoveryAction={item.recoveryAction}
            />
          );
        }
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

      <AICOOConversationRecord runtime={runtime} />

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

function AICOOConversationRecord({ runtime }: { runtime: BrainRuntime }) {
  const strategy = runtime.strategy;
  const qualityScores = runtime.quality_scores ?? [];
  const quality = qualityScores[qualityScores.length - 1] ?? null;
  const decisions = runtime.decisions ?? [];
  const decision = decisions[decisions.length - 1] ?? null;
  const reflection = runtime.reflection;

  if (!strategy && !quality && !reflection) return null;

  const periodDays = recordNumber(strategy?.strategy, "period_days") ?? 30;
  const primaryAction =
    recordText(decision?.selected_option, "title")
    || recordText(strategy?.strategy, "primary_action");
  const nextStrategy = nextStrategyCopy(reflection?.next_strategy);

  return (
    <article className="tz-coo-record" aria-label="AI COO 运营决策">
      <AgentAvatar code="00-decision" className="tz-coo-record-avatar" />
      <div className="tz-coo-record-body">
        <header className="tz-coo-record-head">
          <div>
            <span>运营大脑 · 运营决策</span>
            {strategy ? <strong>{periodDays} 天运营策略</strong> : <strong>运营复盘</strong>}
          </div>
          {quality ? (
            <Tag color={quality.passed ? "success" : "warning"} style={{ marginInlineEnd: 0 }}>
              质量审核 {Math.round(quality.score)} 分
            </Tag>
          ) : null}
        </header>

        {strategy ? (
          <>
            <p className="tz-coo-record-goal">{cleanBrainCopy(strategy.goal)}</p>
            {strategy.rationale_summary ? (
              <p className="tz-coo-record-summary">
                {cleanBrainCopy(strategy.rationale_summary)}
              </p>
            ) : null}
          </>
        ) : null}

        {primaryAction ? (
          <section className="tz-coo-decision">
            <span>关键决定</span>
            <strong>{cleanBrainCopy(primaryAction)}</strong>
            {decision?.decision_reason ? (
              <p>{cleanBrainCopy(decision.decision_reason)}</p>
            ) : null}
            {decision?.action_summary ? (
              <p>{cleanBrainCopy(decision.action_summary)}</p>
            ) : null}
          </section>
        ) : null}

        {strategy && (
          strategy.kpis.length > 0
          || strategy.risks.length > 0
          || strategy.evidence_refs.length > 0
          || (quality?.issues.length ?? 0) > 0
          || (quality?.suggestions.length ?? 0) > 0
        ) ? (
          <details className="tz-coo-record-details">
            <summary>查看策略依据与审核意见</summary>
            <div>
              {strategy.kpis.length > 0 ? (
                <section>
                  <strong>关键指标</strong>
                  <ul>
                    {strategy.kpis.map((item, index) => (
                      <li key={`kpi-${index}`}>{kpiCopy(item)}</li>
                    ))}
                  </ul>
                </section>
              ) : null}
              {strategy.risks.length > 0 ? (
                <section>
                  <strong>主要风险</strong>
                  <ul>
                    {strategy.risks.map((risk) => (
                      <li key={risk}>{cleanBrainCopy(risk)}</li>
                    ))}
                  </ul>
                </section>
              ) : null}
              {quality && (quality.issues.length > 0 || quality.suggestions.length > 0) ? (
                <section>
                  <strong>质量审核</strong>
                  <ul>
                    {[...quality.issues, ...quality.suggestions].map((item) => (
                      <li key={item}>{cleanBrainCopy(item)}</li>
                    ))}
                  </ul>
                </section>
              ) : null}
              {strategy.evidence_refs.length > 0 ? (
                <p className="tz-coo-evidence-count">
                  已引用 {strategy.evidence_refs.length} 条真实数据依据
                </p>
              ) : null}
            </div>
          </details>
        ) : null}

        {reflection ? (
          <section className="tz-coo-reflection">
            <span>复盘结论</span>
            <p>{cleanBrainCopy(reflection.conclusion)}</p>
            {nextStrategy ? (
              <>
                <span>下一轮建议</span>
                <p>{nextStrategy}</p>
              </>
            ) : null}
          </section>
        ) : null}
      </div>
    </article>
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
          <span>
            {hasSystemError
              ? "系统提示"
              : presentOperationsBrainSystemCopy(message.agentName)}
          </span>
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
      <p>{presentOperationsBrainSystemCopy(cleanBrainCopy(content))}</p>
    </div>
  );
}

function RuntimeFailureMessage({
  content,
  recoveryAction,
}: {
  content: string;
  recoveryAction: string;
}) {
  return (
    <article className="dy-chat-event-card" data-tone="warning" role="alert">
      <ExclamationCircleFilled aria-hidden="true" />
      <div>
        <strong>{presentOperationsBrainSystemCopy(cleanBrainCopy(content))}</strong>
        {recoveryAction ? (
          <p>{presentOperationsBrainSystemCopy(cleanBrainCopy(recoveryAction))}</p>
        ) : null}
      </div>
    </article>
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
            {presentOperationsBrainSystemCopy(cleanBrainCopy(lifecycleMessage))}
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
  isAdmin,
  refreshingObservation,
  verifyingCandidateKey,
  onRefreshObservation,
  onVerifyCandidate,
}: {
  task: BrainTask | null;
  runtime: BrainRuntime | null;
  isAdmin: boolean;
  refreshingObservation: boolean;
  verifyingCandidateKey: string | null;
  onRefreshObservation: (taskId: number) => void;
  onVerifyCandidate: (input: {
    taskId: number;
    candidateKey: string;
    verificationNote: string;
  }) => void;
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
            <span>
              {presentOperationsBrainSystemCopy(cleanBrainCopy(task.current_focus))}
            </span>
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

      {runtime?.operation_intelligence ? (
        <OperationIntelligenceDetails value={runtime.operation_intelligence} />
      ) : null}

      {runtime && isAdmin ? <ModelCallAudit runtime={runtime} /> : null}

      {runtime ? (
        <section className="tz-observation-control">
          <div className="tz-observation-control__head">
            <div>
              <h3>效果回查</h3>
              <p>{observationStatusCopy(runtime.reflection)}</p>
            </div>
            <Button
              aria-label="检查最新效果"
              icon={<RedoOutlined />}
              loading={refreshingObservation}
              onClick={() => onRefreshObservation(runtime.task.id)}
            >
              检查最新效果
            </Button>
          </div>

          {runtime.reflection?.status === "observed" ? (
            <ExperienceCandidateList
              taskId={runtime.task.id}
              candidates={runtime.reflection.experience_candidates}
              verifiedMemories={runtime.experience_memories ?? []}
              verifyingCandidateKey={verifyingCandidateKey}
              onVerify={onVerifyCandidate}
            />
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

function ModelCallAudit({ runtime }: { runtime: BrainRuntime }) {
  const isSuccessfulCall = (status: string) =>
    ["ok", "success"].includes(status.trim().toLowerCase());
  const promptContracts = [
    runtime.strategy?.prompt_id
      ? {
          key: "strategy",
          label: "策略规划",
          id: runtime.strategy.prompt_id,
          version: runtime.strategy.prompt_version,
        }
      : null,
    ...(runtime.quality_scores ?? [])
      .filter((item) => item.critic_prompt_id)
      .map((item) => ({
        key: `critic-${item.id}`,
        label: `质量审核 · 第 ${item.iteration} 轮`,
        id: item.critic_prompt_id,
        version: item.critic_prompt_version,
      })),
  ].filter((item): item is {
    key: string;
    label: string;
    id: string;
    version: string | null | undefined;
  } => item != null);

  return (
    <section className="tz-model-audit">
      <div className="tz-model-audit__head">
        <div>
          <h3>模型调用审计</h3>
          <p>仅管理员可见的安全摘要，不包含密钥与原始上下文。</p>
        </div>
        <Tag bordered={false}>管理员</Tag>
      </div>

      {promptContracts.length > 0 ? (
        <div className="tz-model-audit__contracts">
          {promptContracts.map((item) => (
            <div key={item.key}>
              <span>{item.label}</span>
              <strong>{item.id} · {item.version ?? "未标记版本"}</strong>
            </div>
          ))}
        </div>
      ) : null}

      {(runtime.llm_calls ?? []).length > 0 ? (
        <div className="tz-model-audit__calls">
          {(runtime.llm_calls ?? []).map((call) => {
            const isSuccessful = isSuccessfulCall(call.status);
            return (
              <article key={call.id}>
                <div className="tz-model-audit__call-head">
                  <div>
                    <strong>{call.agent_code ?? "runtime"} · {call.model}</strong>
                    <span>{call.provider}</span>
                  </div>
                  <Tag color={isSuccessful ? "success" : "error"}>
                    {isSuccessful ? "成功" : "异常"}
                  </Tag>
                </div>
                <p>{call.prompt_id ?? "未记录 Prompt"} · {call.prompt_version ?? "未标记版本"}</p>
                <div className="tz-model-audit__metrics">
                  <span>{call.total_tokens} Token</span>
                  <span>${Number(call.cost_usd).toFixed(4)}</span>
                  <span>{call.latency_ms} ms</span>
                </div>
                {call.error ? <div className="tz-model-audit__error">{call.error}</div> : null}
              </article>
            );
          })}
        </div>
      ) : (
        <p className="tz-model-audit__empty">本任务暂未记录模型调用。</p>
      )}
    </section>
  );
}

function OperationIntelligenceDetails({
  value,
}: {
  value: NonNullable<BrainRuntime["operation_intelligence"]>;
}) {
  const componentLabels: Record<string, string> = {
    strategy_quality: "策略质量",
    evidence_quality: "数据依据",
    execution_effect: "执行效果",
    learning_quality: "学习能力",
  };

  return (
    <section className="tz-operation-score">
      <h3>运营智能评分</h3>
      <div className="tz-operation-score__headline">
        <strong>{Math.round(value.score)}</strong>
        <div>
          <span>本轮综合评分</span>
          <p>{dataSufficiencyCopy(value.data_sufficiency)}</p>
        </div>
      </div>
      <div className="tz-operation-score__components">
        {Object.entries(value.components).map(([key, score]) => (
          <div key={key}>
            <span>{componentLabels[key] ?? "运营能力"}</span>
            <strong>{Math.round(score)}</strong>
          </div>
        ))}
      </div>
      {value.basis.length > 0 ? (
        <details>
          <summary>查看评分依据</summary>
          <ul>
            {value.basis.map((item) => (
              <li key={item}>{cleanBrainCopy(item)}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}

function ExperienceCandidateList({
  taskId,
  candidates,
  verifiedMemories,
  verifyingCandidateKey,
  onVerify,
}: {
  taskId: number;
  candidates: Record<string, unknown>[];
  verifiedMemories: NonNullable<BrainRuntime["experience_memories"]>;
  verifyingCandidateKey: string | null;
  onVerify: (input: {
    taskId: number;
    candidateKey: string;
    verificationNote: string;
  }) => void;
}) {
  const pendingCandidates = candidates.filter(
    (candidate) => !verifiedMemories.some(
      (memory) =>
        memory.action === recordText(candidate, "action")
        && memory.condition === recordText(candidate, "condition"),
    ),
  );

  if (pendingCandidates.length === 0) {
    return (
      <p className="tz-observation-control__empty">
        本轮没有待核验的运营经验。只有真实数据支持的候选才会出现在这里。
      </p>
    );
  }

  return (
    <div className="tz-experience-candidates">
      <strong>待人工核验的经验候选</strong>
      {pendingCandidates.map((candidate, index) => {
        const key = recordText(candidate, "key") || `candidate-${index}`;
        return (
          <ExperienceCandidate
            key={key}
            taskId={taskId}
            candidateKey={key}
            candidate={candidate}
            verifying={verifyingCandidateKey === key}
            onVerify={onVerify}
          />
        );
      })}
    </div>
  );
}

function ExperienceCandidate({
  taskId,
  candidateKey,
  candidate,
  verifying,
  onVerify,
}: {
  taskId: number;
  candidateKey: string;
  candidate: Record<string, unknown>;
  verifying: boolean;
  onVerify: (input: {
    taskId: number;
    candidateKey: string;
    verificationNote: string;
  }) => void;
}) {
  const [note, setNote] = useState("");
  const action = recordText(candidate, "action");
  const condition = recordText(candidate, "condition");
  const result = recordText(candidate, "result");
  const confidence = recordNumber(candidate, "confidence");

  return (
    <article className="tz-experience-candidate">
      <div>
        <span>{condition || "已满足真实数据观察条件"}</span>
        <strong>{action || "待核验运营动作"}</strong>
        {result ? <p>{result}</p> : null}
        {confidence != null ? (
          <small>数据置信度 {Math.round(confidence * 100)}%</small>
        ) : null}
      </div>
      <Input.TextArea
        value={note}
        rows={2}
        maxLength={500}
        placeholder="写下人工核验依据"
        onChange={(event) => setNote(event.target.value)}
      />
      <Button
        loading={verifying}
        disabled={!note.trim()}
        onClick={() => onVerify({
          taskId,
          candidateKey,
          verificationNote: note.trim(),
        })}
      >
        确认沉淀经验
      </Button>
    </article>
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
  const representedInvocations = new Set<number>();
  const representedFailures = new Set<number>();
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

    if (event.type === "brain.runtime.failed") {
      if (representedFailures.has(event.id)) return;
      representedFailures.add(event.id);
      const userMessage = String(payload.user_message ?? "").trim();
      const content = userMessage || String(payload.message ?? "").trim();
      const recoveryAction = String(payload.recovery_action ?? "").trim();
      if (content) {
        items.push({
          kind: "failure",
          id: `failure-${event.id}`,
          content,
          recoveryAction,
        });
      }
      return;
    }

    if (
      [
        "brain.runtime.subagent_started",
        "brain.runtime.subagent_completed",
        "brain.runtime.subagent_failed",
      ].includes(event.type)
    ) {
      const invocationId = Number(payload.invocation_id);
      const invocation = Number.isInteger(invocationId) && invocationId > 0
        ? invocationsById.get(invocationId)
        : undefined;
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
    agentName: OPERATIONS_BRAIN_DISPLAY_NAME,
    content: "",
    status: "streaming",
  };
}

function expertLifecycleMessages(runtime: BrainRuntime) {
  const byInvocation = new Map<number, string>();
  const invocationIds = new Set(runtime.invocations.map((row) => row.id));

  runtime.timeline.forEach((event) => {
    if (
      ![
        "brain.runtime.subagent_started",
        "brain.runtime.subagent_completed",
        "brain.runtime.subagent_failed",
      ].includes(event.type)
    ) return;
    const payload = asRuntimePayload(event.payload) ?? {};
    const invocationId = Number(payload.invocation_id);
    const message = String(payload.message ?? "").trim();
    if (
      Number.isInteger(invocationId)
      && invocationId > 0
      && invocationIds.has(invocationId)
      && message
    ) {
      byInvocation.set(invocationId, message);
    }
  });

  return byInvocation;
}

function runtimeMessageFromEvent(runtime: BrainRuntime, event: BrainRuntime["timeline"][number]) {
  const payload = asRuntimePayload(event.payload) ?? {};
  return {
    id: String(payload.message_id ?? event.id),
    taskId: runtime.task.id,
    agentCode: String(payload.agent_code ?? ""),
    agentName: presentOperationsBrainSystemCopy(String(payload.agent_name ?? "Agent")),
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
  const agentName = presentOperationsBrainSystemCopy(
    String(payload.agent_name ?? (agentCode || "Agent")),
  );
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

function mergeConversationTurn(
  current: ConversationThread | undefined,
  submission: TurnSubmission,
) {
  if (!current || current.id !== submission.turn.thread_id) return current;
  const turns = current.turns.some((turn) => turn.id === submission.turn.id)
    ? current.turns.map((turn) => turn.id === submission.turn.id ? submission.turn : turn)
    : [...current.turns, submission.turn];
  return { ...current, turns };
}

function isTerminalConversationRunStatus(status: string) {
  return [
    "blocked",
    "cancelled",
    "completed",
    "dead_letter",
    "failed",
    "stopped",
    "waiting_decision",
    "waiting_permission",
    "waiting_user",
  ].includes(status);
}

function isTerminalConversationRuntimeEvent(type: string) {
  return [
    "brain.runtime.blocked",
    "brain.runtime.completed",
    "brain.runtime.failed",
    "brain.runtime.generation_stopped",
    "brain.runtime.turn_paused",
  ].includes(type);
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
  const cleaned = presentOperationsBrainSystemCopy(cleanBrainCopy(value)).trim();
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

function recordText(value: Record<string, unknown> | null | undefined, key: string) {
  const entry = value?.[key];
  return typeof entry === "string" ? entry.trim() : "";
}

function recordNumber(value: Record<string, unknown> | null | undefined, key: string) {
  const entry = value?.[key];
  if (typeof entry === "number" && Number.isFinite(entry)) return entry;
  if (typeof entry === "string") {
    const parsed = Number(entry);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function kpiCopy(value: Record<string, unknown>) {
  const metricLabels: Record<string, string> = {
    qualified_leads: "有效咨询",
    views: "播放量",
    completion_rate: "完播率",
    engagement_rate: "互动率",
    follower_growth: "粉丝增长",
  };
  const metric = recordText(value, "metric");
  const target = value.target;
  const targetText =
    typeof target === "number" || typeof target === "string"
      ? String(target)
      : "待确认";
  return `${metricLabels[metric] ?? cleanBrainCopy(metric || "运营指标")}：${targetText}`;
}

function nextStrategyCopy(value: Record<string, unknown> | null | undefined) {
  const action = recordText(value, "action");
  const actions: Record<string, string> = {
    continue_and_expand_observation: "延续当前有效策略，并扩大真实数据观察窗口。",
    adjust_strategy: "根据本轮偏差调整策略，再进入下一轮执行。",
    wait_for_measurement: "等待结果数据满足观察窗口后再做策略判断。",
    stop_and_review: "暂停扩展执行，先完成风险与目标复核。",
  };
  if (actions[action]) return actions[action];
  const recommendation =
    recordText(value, "recommendation")
    || recordText(value, "summary")
    || recordText(value, "next_action");
  return recommendation ? cleanBrainCopy(recommendation) : "";
}

function dataSufficiencyCopy(value: "insufficient" | "partial" | "sufficient") {
  const labels = {
    insufficient: "真实数据不足，评分仅供风险判断",
    partial: "已有部分真实数据，建议继续观察",
    sufficient: "真实数据满足本轮评估条件",
  };
  return labels[value];
}

function observationStatusCopy(reflection: BrainRuntime["reflection"]) {
  if (!reflection) {
    return "任务完成后可检查真实效果；系统不会用模型猜测结果。";
  }
  const labels: Record<string, string> = {
    pending_observation: "正在等待真实数据满足观察窗口。",
    observed: reflection.conclusion || "已回收本轮真实效果。",
    insufficient_data: "当前真实数据不足，暂不形成运营结论。",
    failed: "最近一次效果回查失败，请检查数据源后重试。",
  };
  return cleanBrainCopy(labels[reflection.status] ?? reflection.conclusion);
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
      : "运营大脑已完成本轮回复";
  }
  if (runtime.status === "failed") return "运行失败，需要处理";
  const runningExpert = runtime.invocations.find((item) => item.status === "running");
  if (runningExpert) return `${runningExpert.agent_name} 正在处理`;
  const lastExpert = [...runtime.invocations].reverse().find((item) => item.status === "done");
  if (lastExpert) return `${lastExpert.agent_name} 已完成，运营大脑正在推进下一步`;
  return "运营大脑正在理解目标并组织专家";
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
