import {
  DownOutlined,
  HistoryOutlined,
  PlusOutlined,
  RedoOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App as AntApp, Button, Tag } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import {
  acceptArtifact,
  approveToolCall,
  createConversation,
  getConversation,
  listComposerSkills,
  reviseArtifact,
  sendConversationTurn,
  stopBrainGeneration,
} from "../api/brain";
import { presentApiError } from "../api/errors";
import { getWorkspaceContext } from "../api/shell";
import { AgentAvatar } from "../components/agents/AgentAvatar";
import { ArtifactCard, businessArtifactTitle, type ArtifactAction } from "../components/brain/ArtifactCard";
import { ArtifactCenter } from "../components/brain/ArtifactCenter";
import { BrainComposer } from "../components/brain/BrainComposer";
import { ConversationHistoryDrawer } from "../components/brain/ConversationHistoryDrawer";
import { TurnStream } from "../components/brain/TurnStream";
import {
  applyConversationEvent,
  appendOptimisticTurn,
  isActiveConversationTurnStatus,
  mergeConversationTurn,
} from "../components/brain/conversationTurnProjection";
import { OperationalState } from "../components/ui";
import { useEventStream, type DyEvent } from "../hooks/useEventStream";
import {
  clearActiveBrainTaskId,
  clearActiveConversationThreadId,
  getActiveConversationThreadId,
  setActiveConversationThreadId as persistActiveConversationThreadId,
} from "../stores/brainConversation";
import {
  resolveWorkspaceAccount,
  useCurrentWorkspace,
} from "../stores/currentWorkspace";
import type {
  Account,
  Artifact,
  ConversationApproval,
  ConversationThread,
  ConversationTurn,
} from "../types";

interface PendingTurn {
  clientMessageId: string;
  content: string;
  taskId: number | null;
}

interface SourceReturnTarget {
  accountId: number;
  threadId: number;
  turnId: number;
}

export default function BrainHome() {
  const { message } = AntApp.useApp();
  const qc = useQueryClient();
  const [goal, setGoal] = useState("");
  const [activeConversationThreadId, setActiveConversationThreadId] = useState<number | null>(null);
  const [pendingTurn, setPendingTurn] = useState<PendingTurn | null>(null);
  const [launcherPending, setLauncherPending] = useState(false);
  const [approvalComment, setApprovalComment] = useState("");
  const [artifactRefreshKey, setArtifactRefreshKey] = useState(0);
  const [artifactRevisionChains, setArtifactRevisionChains] = useState<Record<number, Artifact[]>>({});
  const [artifactSourceOverrides, setArtifactSourceOverrides] = useState<Record<number, Artifact>>({});
  const [historyOpen, setHistoryOpen] = useState(false);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const [workspaceMode, setWorkspaceMode] = useState<"conversation" | "results">("conversation");
  const [selectedArtifact, setSelectedArtifact] = useState<Artifact | null>(null);
  const [sourceReturnTarget, setSourceReturnTarget] = useState<SourceReturnTarget | null>(null);
  const [sourceReturnError, setSourceReturnError] = useState<string | null>(null);
  const pendingClientMessageId = useRef<string | null>(null);
  const activeConversationThreadIdRef = useRef<number | null>(null);
  const launcherRequestInFlight = useRef(false);
  const effectiveAccountIdRef = useRef<number | null>(null);
  const conversationRef = useRef<HTMLElement | null>(null);
  const followLatestMessage = useRef(true);
  const { clientId, projectId, platform, accountId } = useCurrentWorkspace();
  const location = useLocation();
  const navigate = useNavigate();
  activeConversationThreadIdRef.current = activeConversationThreadId;

  useEffect(() => {
    const state = location.state as { agentDraft?: string } | null;
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

  const composerSkillsQuery = useQuery({
    queryKey: ["composer-skills", "douyin"],
    queryFn: () => listComposerSkills("douyin"),
  });

  useEventStream((event) => {
    if (!event.type.startsWith("brain.runtime.")) return;
    const payload = asRuntimePayload(event.payload);
    const eventThreadId = payload?.thread_id == null ? null : Number(payload.thread_id);
    const eventClientMessageId = typeof payload?.client_message_id === "string"
      ? payload.client_message_id
      : null;
    const currentThreadId = activeConversationThreadIdRef.current;
    if (
      currentThreadId != null
      && eventThreadId === currentThreadId
    ) {
      qc.setQueryData<ConversationThread>(
        ["brain-conversation", currentThreadId],
        (current) => current ? applyConversationEvent(current, event) : current,
      );
    }
    if (
      eventThreadId === currentThreadId
      && eventClientMessageId === pendingClientMessageId.current
      && isTerminalConversationRuntimeEvent(event.type)
    ) {
      setPendingTurn((current) =>
        current?.clientMessageId === eventClientMessageId ? null : current
      );
      pendingClientMessageId.current = null;
    }
    if (
      eventThreadId === currentThreadId
      && currentThreadId != null
      && !isEphemeralConversationEvent(event.type)
    ) {
      void qc.invalidateQueries({
        queryKey: ["brain-conversation", currentThreadId],
      });
    }
  }, {
    onReconnect: () => {
      const currentThreadId = activeConversationThreadIdRef.current;
      if (currentThreadId != null) {
        void qc.invalidateQueries({
          queryKey: ["brain-conversation", currentThreadId],
        });
      }
    },
  });

  const effectiveAccount = activeAccount;
  const effectiveAccountId = effectiveAccount?.id ?? null;
  const accountReady = Boolean(
    effectiveAccount
    && (
      effectiveAccount.auth_status === "authorized"
      || effectiveAccount.auth_status === "manual"
    ),
  );

  useEffect(() => {
    setActiveConversationThreadId(
      effectiveAccountId != null ? getActiveConversationThreadId(effectiveAccountId) : null,
    );
    setPendingTurn(null);
    pendingClientMessageId.current = null;
    setSelectedArtifact(null);
    setSourceReturnTarget(null);
    setSourceReturnError(null);
  }, [effectiveAccountId]);

  const conversationQuery = useQuery({
    queryKey: ["brain-conversation", activeConversationThreadId],
    queryFn: () => getConversation(activeConversationThreadId!),
    enabled: activeConversationThreadId != null,
    // createConversation seeds this exact cache before activating the Thread.
    // Refetching on that first mount can replace a just-added optimistic Turn
    // with an older empty server snapshot.
    refetchOnMount: false,
    staleTime: 10_000,
  });
  useEffect(() => {
    const loaded = conversationQuery.data;
    if (
      loaded == null
      || activeConversationThreadId == null
      || effectiveAccount == null
      || (
        loaded.id === activeConversationThreadId
        && loaded.account_id === effectiveAccount.id
      )
    ) {
      return;
    }
    clearActiveConversationThreadId(effectiveAccount.id);
    setActiveConversationThreadId(null);
    setPendingTurn(null);
    pendingClientMessageId.current = null;
  }, [
    activeConversationThreadId,
    conversationQuery.data,
    effectiveAccount,
  ]);
  const activeConversation =
    activeConversationThreadId != null
    && effectiveAccount != null
    && conversationQuery.data?.id === activeConversationThreadId
    && conversationQuery.data.account_id === effectiveAccount.id
      ? conversationQuery.data
      : null;
  const activeTurn = useMemo(
    () => findLatestActiveTurn(activeConversation),
    [activeConversation],
  );
  const pendingPermission = useMemo(
    () => findLatestPendingApproval(activeConversation),
    [activeConversation],
  );

  const conversationTurnMutation = useMutation({
    mutationFn: ({
      threadId,
      content,
      clientMessageId,
      requestedSkillCode,
    }: {
      threadId: number;
      content: string;
      clientMessageId: string;
      requestedSkillCode: string | null;
      accountId: number;
    }) => sendConversationTurn(
      threadId,
      requestedSkillCode == null
        ? {
            client_message_id: clientMessageId,
            message: content,
          }
        : {
            client_message_id: clientMessageId,
            message: content,
            requested_skill_code: requestedSkillCode,
            execution_preference: "AUTO",
            attachment_ids: [],
          },
    ),
    onSuccess: (submission, variables) => {
      qc.setQueryData<ConversationThread>(
        ["brain-conversation", variables.threadId],
        (current) => current ? mergeConversationTurn(current, submission.turn) : current,
      );
      void qc.invalidateQueries({
        queryKey: ["brain-conversation", variables.threadId],
      });
      if (effectiveAccountIdRef.current !== variables.accountId) return;
      if (isTerminalConversationRunStatus(submission.run.status)) {
        setPendingTurn(null);
        pendingClientMessageId.current = null;
        return;
      }
      const clientMessageId = submission.run.client_message_id || variables.clientMessageId;
      const taskId = submission.task_id ?? submission.run.task_id;
      setPendingTurn((current) =>
        current?.clientMessageId === variables.clientMessageId
          ? { ...current, clientMessageId, taskId: taskId ?? current.taskId }
          : current
      );
      pendingClientMessageId.current = clientMessageId;
    },
    onError: (error, variables) => {
      if (effectiveAccountIdRef.current !== variables.accountId) return;
      setPendingTurn((current) => {
        if (current?.clientMessageId === variables.clientMessageId) {
          setGoal((value) => value || current.content);
          return null;
        }
        return current;
      });
      pendingClientMessageId.current = null;
      void qc.invalidateQueries({
        queryKey: ["brain-conversation", variables.threadId],
      });
      message.error(presentApiError(error, "消息发送失败，请稍后重试。").message);
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
        void qc.invalidateQueries({
          queryKey: ["brain-conversation", activeConversationThreadId],
        });
      }
    },
  });

  const approveMutation = useMutation({
    mutationFn: approveToolCall,
    onSuccess: () => {
      setApprovalComment("");
      if (activeConversationThreadId != null) {
        void qc.invalidateQueries({
          queryKey: ["brain-conversation", activeConversationThreadId],
        });
      }
      message.success("工具权限已处理，Runtime 正在继续");
    },
    onError: (error) => message.error(
      presentApiError(error, "工具权限处理失败，请稍后重试。").message,
    ),
  });

  const formalArtifactAcceptMutation = useMutation({
    mutationFn: (input: { sourceArtifact: Artifact; createNextStep: boolean }) =>
      acceptArtifact(input.sourceArtifact.id),
    onSuccess: (accepted, input) => {
      if (!matchesArtifactResponse(accepted, input.sourceArtifact, "accept")) {
        message.error("成果返回校验失败，请重试。");
        return;
      }
      setArtifactRevisionChains((current) => updateExistingArtifactChain(current, accepted));
      setSelectedArtifact((current) =>
        current?.id === input.sourceArtifact.id
        && current.account_id === input.sourceArtifact.account_id
          ? accepted
          : current
      );
      setArtifactRefreshKey((value) => value + 1);
      void qc.invalidateQueries({
        queryKey: ["account-artifacts", input.sourceArtifact.account_id],
      });
      if (activeConversationThreadId != null) {
        void qc.invalidateQueries({
          queryKey: ["brain-conversation", activeConversationThreadId],
        });
      }
      if (input.createNextStep) setGoal(nextStepGoal(accepted));
      message.success("报告已采用");
    },
    onError: (error) => message.error(
      presentApiError(error, "报告采用失败，请稍后重试。").message,
    ),
  });

  const formalArtifactRevisionMutation = useMutation({
    mutationFn: (input: {
      sourceArtifact: Artifact;
      payload: Record<string, unknown>;
      note: string;
    }) => reviseArtifact({
      artifactId: input.sourceArtifact.id,
      payload: input.payload,
      note: input.note,
    }),
    onSuccess: (revision, input) => {
      if (!matchesArtifactResponse(revision, input.sourceArtifact, "revision")) {
        message.error("修订成果校验失败，请重试。");
        return;
      }
      setArtifactRevisionChains((current) =>
        appendArtifactRevision(current, input.sourceArtifact, revision)
      );
      setArtifactSourceOverrides((current) =>
        supersedeRootArtifact(current, input.sourceArtifact)
      );
      setSelectedArtifact((current) =>
        current?.id === input.sourceArtifact.id
        && current.account_id === input.sourceArtifact.account_id
          ? revision
          : current
      );
      setArtifactRefreshKey((value) => value + 1);
      void qc.invalidateQueries({
        queryKey: ["account-artifacts", input.sourceArtifact.account_id],
      });
      if (activeConversationThreadId != null) {
        void qc.invalidateQueries({
          queryKey: ["brain-conversation", activeConversationThreadId],
        });
      }
      message.success("修改请求已提交，正在生成新版本");
    },
    onError: (error) => message.error(
      presentApiError(error, "修改请求提交失败，请稍后重试。").message,
    ),
  });

  const contextError = contextQuery.isError
    ? presentApiError(contextQuery.error, "运营上下文暂时不可用。")
    : null;
  const conversationError = conversationQuery.isError
    ? presentApiError(
        conversationQuery.error,
        "Conversation history is temporarily unavailable.",
      )
    : null;
  const isGenerating =
    conversationTurnMutation.isPending
    || launcherPending
    || activeTurn != null;

  const ensureAccountThread = useCallback(async (account: Account) => {
    const savedThreadId =
      activeConversationThreadId ?? getActiveConversationThreadId(account.id);
    const thread = savedThreadId == null
      ? await createConversation({ account_id: account.id })
      : await qc.ensureQueryData({
          queryKey: ["brain-conversation", savedThreadId],
          queryFn: () => getConversation(savedThreadId),
        });
    if (
      thread.account_id !== account.id
      || effectiveAccountIdRef.current !== account.id
    ) {
      return null;
    }
    qc.setQueryData(["brain-conversation", thread.id], thread);
    persistActiveConversationThreadId(account.id, thread.id);
    setActiveConversationThreadId(thread.id);
    return thread;
  }, [activeConversationThreadId, qc]);

  const submitTurn = useCallback(async ({
    content,
    requestedSkillCode,
  }: {
    content: string;
    requestedSkillCode: string | null;
  }) => {
    const account = effectiveAccount;
    if (!account) return;
    const clientMessageId = createClientMessageId();
    const thread = await ensureAccountThread(account);
    if (!thread) return;
    followLatestMessage.current = true;
    setShowJumpToLatest(false);
    pendingClientMessageId.current = clientMessageId;
    qc.setQueryData<ConversationThread>(
      ["brain-conversation", thread.id],
      (current) => current
        ? appendOptimisticTurn(current, clientMessageId, content)
        : current,
    );
    setPendingTurn({ clientMessageId, content, taskId: null });
    await conversationTurnMutation.mutateAsync({
      threadId: thread.id,
      content,
      clientMessageId,
      requestedSkillCode,
      accountId: account.id,
    });
  }, [conversationTurnMutation, effectiveAccount, ensureAccountThread, qc]);

  const startWorkflow = async () => {
    if (launcherRequestInFlight.current || isGenerating) return;
    const content = goal.trim();
    if (!content) {
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
    launcherRequestInFlight.current = true;
    setLauncherPending(true);
    setGoal("");
    try {
      await submitTurn({ content, requestedSkillCode: null });
    } catch {
      setGoal((current) => current || content);
    } finally {
      launcherRequestInFlight.current = false;
      setLauncherPending(false);
    }
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
    if (!effectiveAccount) {
      requestAccountSelection();
      return;
    }
    if (!accountReady) {
      message.warning("当前账号尚未完成授权，请先完成账号授权后再执行");
      return;
    }
    launcherRequestInFlight.current = true;
    setLauncherPending(true);
    try {
      await submitTurn({
        content: skill.name,
        requestedSkillCode: skill.code,
      });
    } catch {
      // The mutation owns the user-facing error and restores the composer.
    } finally {
      launcherRequestInFlight.current = false;
      setLauncherPending(false);
    }
  }, [
    accountReady,
    composerSkillsQuery.data,
    effectiveAccount,
    isGenerating,
    message,
    requestAccountSelection,
    submitTurn,
  ]);

  const regenerateLastTurn = () => {
    const sourceMessage = activeConversation?.turns.at(-1)?.user_input.trim();
    if (!sourceMessage || isGenerating) return;
    void submitTurn({ content: sourceMessage, requestedSkillCode: null });
  };

  const resetConversation = () => {
    if (effectiveAccount) {
      clearActiveBrainTaskId(effectiveAccount.id);
      clearActiveConversationThreadId(effectiveAccount.id);
    }
    setActiveConversationThreadId(null);
    setPendingTurn(null);
    pendingClientMessageId.current = null;
    setApprovalComment("");
    setGoal("");
    setSourceReturnTarget(null);
    setSourceReturnError(null);
    followLatestMessage.current = true;
    setShowJumpToLatest(false);
  };

  const selectConversation = (threadId: number) => {
    if (!effectiveAccount) return;
    clearActiveBrainTaskId(effectiveAccount.id);
    persistActiveConversationThreadId(effectiveAccount.id, threadId);
    setActiveConversationThreadId(threadId);
    setPendingTurn(null);
    pendingClientMessageId.current = null;
    setWorkspaceMode("conversation");
    followLatestMessage.current = true;
    setShowJumpToLatest(false);
  };

  const handleConversationDeleted = (threadId: number) => {
    if (threadId !== activeConversationThreadId || !effectiveAccount) return;
    clearActiveConversationThreadId(effectiveAccount.id);
    setActiveConversationThreadId(null);
    setPendingTurn(null);
    pendingClientMessageId.current = null;
    setGoal("");
    followLatestMessage.current = true;
    setShowJumpToLatest(false);
  };

  const selectCenterArtifact = useCallback((artifact: Artifact | null) => {
    setSourceReturnError(null);
    setSelectedArtifact(
      artifact && artifact.account_id === effectiveAccount?.id ? artifact : null,
    );
  }, [effectiveAccount?.id]);

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
    persistActiveConversationThreadId(effectiveAccount.id, target.threadId);
    setActiveConversationThreadId(target.threadId);
    if (retry) {
      void qc.fetchQuery({
        queryKey: ["brain-conversation", target.threadId],
        queryFn: () => getConversation(target.threadId),
      }).then(() => setSourceReturnTarget(target)).catch(() => {
        setSourceReturnError("来源对话暂时无法加载，请在成果中心重试。");
      });
      return;
    }
    setSourceReturnTarget(target);
  };

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
    if (
      source.id !== sourceReturnTarget.threadId
      || source.account_id !== sourceReturnTarget.accountId
    ) {
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
  }, [
    conversationQuery.data,
    conversationQuery.isError,
    effectiveAccount?.id,
    sourceReturnTarget,
  ]);

  useEffect(() => {
    if (
      workspaceMode !== "conversation"
      || !sourceReturnTarget
      || activeConversation?.id !== sourceReturnTarget.threadId
    ) return;
    const node = document.querySelector<HTMLElement>(
      `[data-turn-id="${sourceReturnTarget.turnId}"]`,
    );
    if (!node) return;
    node.setAttribute("tabindex", "-1");
    node.scrollIntoView({ block: "center" });
    node.focus({ preventScroll: true });
    setSourceReturnTarget(null);
  }, [activeConversation, sourceReturnTarget, workspaceMode]);

  const hasConversation = Boolean(activeConversationThreadId || pendingTurn);

  useEffect(() => {
    if (!hasConversation || !followLatestMessage.current) return;
    const frame = window.requestAnimationFrame(() => {
      const conversation = conversationRef.current;
      if (!conversation) return;
      if (typeof conversation.scrollTo === "function") {
        conversation.scrollTo({
          top: conversation.scrollHeight,
          behavior: "auto",
        });
      } else {
        conversation.scrollTop = conversation.scrollHeight;
      }
      setShowJumpToLatest(false);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [
    activeConversation,
    activeConversationThreadId,
    conversationQuery.dataUpdatedAt,
    hasConversation,
    isGenerating,
    pendingTurn,
  ]);

  const handleConversationScroll = () => {
    const conversation = conversationRef.current;
    if (!conversation) return;
    const distanceFromBottom =
      conversation.scrollHeight - conversation.scrollTop - conversation.clientHeight;
    const isNearLatest = distanceFromBottom <= 96;
    followLatestMessage.current = isNearLatest;
    setShowJumpToLatest(!isNearLatest);
  };

  const jumpToLatestMessage = () => {
    const conversation = conversationRef.current;
    if (!conversation) return;
    followLatestMessage.current = true;
    setShowJumpToLatest(false);
    if (typeof conversation.scrollTo === "function") {
      conversation.scrollTo({
        top: conversation.scrollHeight,
        behavior: "smooth",
      });
      return;
    }
    conversation.scrollTop = conversation.scrollHeight;
  };

  return (
    <div className={`tz-brain-page${hasConversation ? " has-conversation" : " is-empty"}${workspaceMode === "results" ? " is-results" : ""}`}>
      {hasConversation ? (
        <header className="tz-brain-toolbar">
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
            <Button icon={<HistoryOutlined />} onClick={() => setHistoryOpen(true)}>
              历史会话
            </Button>
          </div>
        </header>
      ) : (
        <div className="tz-brain-empty-actions">
          <Button
            icon={<HistoryOutlined />}
            disabled={!effectiveAccount}
            onClick={() => setHistoryOpen(true)}
          >
            历史会话
          </Button>
        </div>
      )}

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
        ) : (
          <div className="tz-brain-thread">
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
                      <Button onClick={() => returnToArtifactSource(true)}>
                        重试返回来源对话
                      </Button>
                    </div>
                  ) : null}
                  {selectedArtifact
                  && selectedArtifact.account_id === effectiveAccount?.id ? (
                    <section
                      className="tz-artifact-center__detail"
                      aria-label="Artifact detail"
                    >
                      <ArtifactCard
                        artifact={selectedArtifact}
                        revisionPending={
                          formalArtifactRevisionMutation.isPending
                          && formalArtifactRevisionMutation.variables?.sourceArtifact.id
                            === selectedArtifact.id
                        }
                        onAction={(action) => handleArtifactAction(
                          action,
                          formalArtifactAcceptMutation.mutate,
                          formalArtifactRevisionMutation.mutate,
                        )}
                      />
                      {selectedArtifact.thread_id != null
                      && selectedArtifact.turn_id != null ? (
                        <Button
                          onClick={() => returnToArtifactSource()}
                          aria-label="返回来源对话"
                        >
                          返回来源对话
                        </Button>
                      ) : null}
                    </section>
                  ) : null}
                </div>
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
                      approveMutation.isPending
                        ? approveMutation.variables?.toolCallId ?? null
                        : null
                    }
                    approvalComment={approvalComment}
                    onApprovalCommentChange={setApprovalComment}
                    onApprove={(approval, approved, comment) =>
                      approveMutation.mutate({
                        toolCallId: approval.id,
                        approved,
                        comment,
                      })
                    }
                    onArtifactAction={(action) => handleArtifactAction(
                      action,
                      formalArtifactAcceptMutation.mutate,
                      formalArtifactRevisionMutation.mutate,
                    )}
                  />
                  {!isGenerating && !pendingPermission ? (
                    <Button
                      aria-label="重新生成"
                      icon={<RedoOutlined />}
                      type="text"
                      onClick={regenerateLastTurn}
                    >
                      重新生成
                    </Button>
                  ) : null}
                </>
              ) : activeConversationThreadId != null ? (
                <div className="tz-turn-stream" aria-live="polite">
                  Loading conversation…
                </div>
              ) : (
                <ConversationEmpty
                  account={effectiveAccount}
                  loading={contextQuery.isLoading}
                />
              )}
            </section>

            {workspaceMode === "conversation" && showJumpToLatest ? (
              <Button
                className="tz-brain-jump-latest"
                icon={<DownOutlined />}
                size="small"
                aria-label="回到最新消息"
                onClick={jumpToLatestMessage}
              >
                最新消息
              </Button>
            ) : null}

            {workspaceMode === "conversation" ? (
              <BrainComposer
                value={goal}
                disabled={isGenerating}
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
                onStop={() => {
                  if (
                    !activeTurn?.client_message_id
                    || stopMutation.isPending
                  ) return;
                  stopMutation.mutate({
                    clientMessageId: activeTurn.client_message_id,
                    taskId:
                      pendingTurn?.clientMessageId === activeTurn.client_message_id
                        ? pendingTurn.taskId
                        : null,
                  });
                }}
              />
            ) : null}
          </div>
        )}
      </main>

      <ConversationHistoryDrawer
        accountId={effectiveAccount?.id ?? null}
        activeThreadId={activeConversationThreadId}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        onSelect={selectConversation}
        onDeleted={handleConversationDeleted}
      />
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
        {account ? (
          <Tag style={{ marginInlineEnd: 0 }}>
            {syncLabel(account.data_sync_status)}
          </Tag>
        ) : null}
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

function handleArtifactAction(
  action: ArtifactAction,
  accept: (input: { sourceArtifact: Artifact; createNextStep: boolean }) => void,
  revise: (input: {
    sourceArtifact: Artifact;
    payload: Record<string, unknown>;
    note: string;
  }) => void,
) {
  if (action.type === "accept") {
    accept({ sourceArtifact: action.artifact, createNextStep: false });
  } else if (action.type === "accept_and_continue") {
    accept({ sourceArtifact: action.artifact, createNextStep: true });
  } else if (action.type === "request_revision") {
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
  const sameScope =
    returned.account_id === source.account_id
    && returned.thread_id === source.thread_id
    && returned.turn_id === source.turn_id
    && returned.artifact_type === source.artifact_type;
  return sameScope && (
    operation === "accept"
      ? returned.id === source.id && returned.version === source.version
      : returned.id !== source.id && returned.version === source.version + 1
  );
}

function updateExistingArtifactChain(
  chains: Record<number, Artifact[]>,
  accepted: Artifact,
) {
  for (const [sourceId, chain] of Object.entries(chains)) {
    const versionIndex = chain.findIndex((artifact) => artifact.id === accepted.id);
    if (versionIndex < 0) continue;
    const nextChain = [...chain];
    nextChain[versionIndex] = accepted;
    return { ...chains, [sourceId]: nextChain };
  }
  return chains;
}

function appendArtifactRevision(
  chains: Record<number, Artifact[]>,
  sourceArtifact: Artifact,
  revision: Artifact,
) {
  const sourceId = Object.entries(chains).find(([rootId, chain]) =>
    Number(rootId) === sourceArtifact.id
    || chain.some((artifact) => artifact.id === sourceArtifact.id)
  )?.[0] ?? String(sourceArtifact.id);
  const currentChain = chains[Number(sourceId)] ?? [];
  const nextChain = [
    ...currentChain.map((artifact) =>
      artifact.id === sourceArtifact.id
        ? { ...artifact, status: "superseded" as const }
        : artifact
    ).filter((artifact) => artifact.id !== revision.id),
    revision,
  ].sort((left, right) => left.version - right.version);
  return { ...chains, [sourceId]: nextChain };
}

function supersedeRootArtifact(
  overrides: Record<number, Artifact>,
  sourceArtifact: Artifact,
) {
  return sourceArtifact.version === 1
    ? {
        ...overrides,
        [sourceArtifact.id]: {
          ...sourceArtifact,
          status: "superseded" as const,
        },
      }
    : overrides;
}

function buildArtifactRevisionPayload(artifact: Artifact): Record<string, unknown> {
  return {
    title: artifact.title,
    summary: artifact.summary,
    ...Object.fromEntries(
      artifact.sections.map((section) => [section.key, section.content]),
    ),
  };
}

function nextStepGoal(artifact: Artifact) {
  return `已采用《${businessArtifactTitle(artifact)}》（成果 #${artifact.id}）。请基于该报告提出下一步执行建议。`;
}

function asRuntimePayload(payload: DyEvent["payload"]) {
  return typeof payload === "object" && payload != null
    ? payload as Record<string, unknown>
    : null;
}

function isEphemeralConversationEvent(type: string) {
  return [
    "brain.runtime.started",
    "brain.runtime.intent_classified",
    "brain.runtime.tool_completed",
    "brain.runtime.message_start",
    "brain.runtime.message_delta",
  ].includes(type);
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

function createClientMessageId() {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `brain-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function findLatestActiveTurn(
  conversation: ConversationThread | null,
): ConversationTurn | null {
  if (!conversation) return null;
  for (let index = conversation.turns.length - 1; index >= 0; index -= 1) {
    const turn = conversation.turns[index];
    if (isActiveConversationTurnStatus(turn.status)) return turn;
  }
  return null;
}

function findLatestPendingApproval(
  conversation: ConversationThread | null,
): ConversationApproval | null {
  if (!conversation) return null;
  for (let turnIndex = conversation.turns.length - 1; turnIndex >= 0; turnIndex -= 1) {
    const projections = conversation.turns[turnIndex].projections;
    for (
      let projectionIndex = projections.length - 1;
      projectionIndex >= 0;
      projectionIndex -= 1
    ) {
      const projection = projections[projectionIndex];
      if (
        projection.type === "approval"
        && projection.approval.status === "waiting_approval"
      ) {
        return projection.approval;
      }
    }
  }
  return null;
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
