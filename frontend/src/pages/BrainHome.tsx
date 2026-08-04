import {
  DownOutlined,
  HistoryOutlined,
  PlusOutlined,
  RedoOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App as AntApp, Button, Tag } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import {
  approveToolCall,
  createConversation,
  executeDeliverableAction,
  getArtifact,
  getConversation,
  listComposerSkills,
  resolveTurnInterrupt,
  sendConversationTurn,
  stopBrainGeneration,
  stopConversationTurn,
} from "../api/brain";
import {
  deleteConversationAttachment,
  uploadConversationAttachments,
} from "../api/attachments";
import { presentApiError } from "../api/errors";
import { getWorkspaceContext } from "../api/shell";
import { AgentAvatar } from "../components/agents/AgentAvatar";
import { ArtifactCard, type ArtifactAction } from "../components/brain/ArtifactCard";
import { ArtifactCenter } from "../components/brain/ArtifactCenter";
import { BrainComposer } from "../components/brain/BrainComposer";
import type { DraftAttachment } from "../components/brain/AttachmentTray";
import { ConversationHistoryDrawer } from "../components/brain/ConversationHistoryDrawer";
import { TurnStream } from "../components/brain/TurnStream";
import { presentDeliverable } from "../components/brain/deliverablePresentation";
import {
  applyConversationEvent,
  applyConversationTurnEvent,
  appendOptimisticTurn,
  isActiveConversationTurnStatus,
  mergeConversationTurn,
  reconcileConversationThread,
  removeOptimisticTurn,
} from "../components/brain/conversationTurnProjection";
import { OperationalState } from "../components/ui";
import { useConversationTurnEvents } from "../hooks/useConversationTurnEvents";
import { useConversationRuntimeStream, type DyEvent } from "../hooks/useEventStream";
import {
  clearActiveBrainTaskId,
  clearActiveConversationThreadId,
  getActiveConversationThreadId,
  isCurrentConversationRequest,
  setActiveConversationThreadId as persistActiveConversationThreadId,
  upsertTurnByClientMessageId,
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
  ConversationTurnEvent,
  TurnInterrupt,
} from "../types";

interface PendingTurn {
  clientMessageId: string;
  content: string;
  taskId: number | null;
}

interface ConversationRequest {
  accountId: number;
  initialThreadId: number | null;
  scopeEpoch: number;
  threadId: number | null;
  clientMessageId: string;
  content: string;
}

interface ConversationScopeToken {
  accountId: number;
  initialThreadId: number | null;
  epoch: number;
}

interface SourceReturnTarget {
  accountId: number;
  threadId: number;
  turnId: number;
}

interface PendingDurableTurnEvent {
  accountId: number;
  threadId: number;
  epoch: number;
  event: ConversationTurnEvent;
}

interface ProjectionRecoveryRequirements {
  events: ConversationTurnEvent[];
  followUpUsed: boolean;
}

function isQueuedDurableEventForScope(
  pending: PendingDurableTurnEvent,
  accountId: number,
  threadId: number,
  epoch: number,
) {
  return pending.accountId === accountId
    && pending.threadId === threadId
    && pending.epoch === epoch;
}

function replayPendingDurableTurnEvents(
  snapshot: ConversationThread,
  pending: PendingDurableTurnEvent[],
) {
  let projected = snapshot;
  const replayedEventIds = new Set<number>();
  const replayedEvents: ConversationTurnEvent[] = [];
  const recoveryEvents: ConversationTurnEvent[] = [];
  for (const item of pending) {
    if (!projected.turns.some((turn) => turn.id === item.event.turn_id)) continue;
    const next = applyConversationTurnEvent(projected, item.event);
    if (next === projected) continue;
    if (needsConversationProjectionRecovery(projected, item.event)) {
      recoveryEvents.push(item.event);
    }
    projected = next;
    replayedEventIds.add(item.event.id);
    replayedEvents.push(item.event);
  }
  return { projected, replayedEventIds, replayedEvents, recoveryEvents };
}

export default function BrainHome() {
  const { message } = AntApp.useApp();
  const qc = useQueryClient();
  const [goal, setGoal] = useState("");
  const [activeConversationThreadId, setActiveConversationThreadId] = useState<number | null>(null);
  const [pendingTurn, setPendingTurn] = useState<PendingTurn | null>(null);
  const [launcherPending, setLauncherPending] = useState(false);
  const [approvalComment, setApprovalComment] = useState("");
  const [draftAttachments, setDraftAttachments] = useState<DraftAttachment[]>([]);
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
  const conversationRequestsRef = useRef(new Map<string, ConversationRequest>());
  const failedOptimisticTurnIdsRef = useRef(new Map<string, Set<string>>());
  const pendingDurableTurnEventsRef = useRef<PendingDurableTurnEvent[]>([]);
  const activeConversationThreadIdRef = useRef<number | null>(null);
  const conversationScopeEpochRef = useRef(0);
  const previousAccountIdRef = useRef<number | null>(null);
  const recoveryInFlightRef = useRef<{ key: string; promise: Promise<void> } | null>(null);
  const recoverConversationProjectionRef = useRef<() => void>(() => undefined);
  const requestConversationProjectionRecoveryRef = useRef<(
    accountId: number,
    threadId: number,
    epoch: number,
    events: ConversationTurnEvent[],
  ) => void>(() => undefined);
  const projectionRecoveryRequirementsRef = useRef(new Map<string, ProjectionRecoveryRequirements>());
  const launcherRequestInFlight = useRef(false);
  const effectiveAccountIdRef = useRef<number | null>(null);
  const conversationRef = useRef<HTMLElement | null>(null);
  const followLatestMessage = useRef(true);
  const { clientId, projectId, platform, accountId } = useCurrentWorkspace();
  const location = useLocation();
  const navigate = useNavigate();
  activeConversationThreadIdRef.current = activeConversationThreadId;

  const matchesConversationScope = useCallback((
    token: ConversationScopeToken,
    threadId = token.initialThreadId,
  ) => (
    effectiveAccountIdRef.current === token.accountId
    && activeConversationThreadIdRef.current === threadId
    && conversationScopeEpochRef.current === token.epoch
  ), []);

  const failedOptimisticScopeKey = useCallback((accountId: number, threadId: number) =>
    `${accountId}:${threadId}`, []);

  const applyDurableTurnEventEffects = useCallback((
    events: ConversationTurnEvent[],
    snapshot: ConversationThread,
  ) => {
    if (events.some((event) => event.type === "deliverable.updated")) {
      setArtifactRefreshKey((value) => value + 1);
    }
    const pendingId = pendingClientMessageId.current;
    const resolvesPendingTurn = events.some((event) =>
      isTerminalConversationTurnEvent(event.type)
      && snapshot.turns.some((turn) =>
        turn.id === event.turn_id && (pendingId == null || turn.client_message_id === pendingId)
      )
    );
    if (resolvesPendingTurn) {
      setPendingTurn(null);
      pendingClientMessageId.current = null;
    }
  }, []);

  const replayQueuedDurableTurnEvents = useCallback((
    snapshot: ConversationThread,
    accountId: number,
    threadId: number,
    epoch: number,
  ) => {
    const pending = pendingDurableTurnEventsRef.current.filter((item) =>
      isQueuedDurableEventForScope(item, accountId, threadId, epoch)
    );
    if (pending.length === 0) return snapshot;
    const {
      projected,
      replayedEventIds,
      replayedEvents,
      recoveryEvents,
    } = replayPendingDurableTurnEvents(snapshot, pending);
    if (replayedEventIds.size === 0) return snapshot;
    pendingDurableTurnEventsRef.current = pendingDurableTurnEventsRef.current.filter((item) =>
      !(
        isQueuedDurableEventForScope(item, accountId, threadId, epoch)
        && replayedEventIds.has(item.event.id)
      )
    );
    applyDurableTurnEventEffects(replayedEvents, projected);
    if (recoveryEvents.length > 0) {
      requestConversationProjectionRecoveryRef.current(accountId, threadId, epoch, recoveryEvents);
    }
    return projected;
  }, [applyDurableTurnEventEffects]);

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
    queryKey: ["composer-skills", activeAccount?.platform ?? platform, activeAccount?.id ?? null],
    queryFn: () => listComposerSkills(activeAccount?.platform ?? platform, activeAccount?.id),
  });

  const effectiveAccount = activeAccount;
  const effectiveAccountId = effectiveAccount?.id ?? null;

  const projectTransientRuntimeEvent = useCallback((event: DyEvent) => {
    const payload = asRuntimePayload(event.payload);
    const currentThreadId = activeConversationThreadIdRef.current;
    if (
      currentThreadId == null
      || payload?.thread_id == null
      || Number(payload.thread_id) !== currentThreadId
    ) return;
    qc.setQueryData<ConversationThread>(
      ["brain-conversation", currentThreadId],
      (current) => current ? applyConversationEvent(current, event) : current,
    );
  }, [qc]);

  const projectDurableTurnEvent = useCallback((event: ConversationTurnEvent) => {
    const currentThreadId = activeConversationThreadIdRef.current;
    const accountId = effectiveAccountIdRef.current;
    if (currentThreadId == null || accountId == null || event.thread_id !== currentThreadId) return;
    const epoch = conversationScopeEpochRef.current;
    const current = qc.getQueryData<ConversationThread>(["brain-conversation", currentThreadId]);
    if (
      !current
      || current.account_id !== accountId
      || !current.turns.some((turn) => turn.id === event.turn_id)
    ) {
      const alreadyQueued = pendingDurableTurnEventsRef.current.some((pending) =>
        isQueuedDurableEventForScope(pending, accountId, currentThreadId, epoch)
        && pending.event.id === event.id
      );
      if (!alreadyQueued) {
        pendingDurableTurnEventsRef.current.push({
          accountId,
          threadId: currentThreadId,
          epoch,
          event,
        });
        if (isProjectionRecoveryEvent(event)) {
          requestConversationProjectionRecoveryRef.current(accountId, currentThreadId, epoch, [event]);
        }
      }
      return;
    }
    const shouldRecoverProjection = needsConversationProjectionRecovery(current, event);
    const projected = applyConversationTurnEvent(current, event);
    qc.setQueryData<ConversationThread>(["brain-conversation", currentThreadId], projected);
    applyDurableTurnEventEffects([event], projected);
    if (projected !== current && shouldRecoverProjection) {
      requestConversationProjectionRecoveryRef.current(accountId, currentThreadId, epoch, [event]);
    }
  }, [applyDurableTurnEventEffects, qc]);

  const recoverConversationProjection = useCallback(() => {
    const accountId = effectiveAccountIdRef.current;
    const threadId = activeConversationThreadIdRef.current;
    const epoch = conversationScopeEpochRef.current;
    if (accountId == null || threadId == null) return;
    const key = `${accountId}:${threadId}:${epoch}`;
    if (recoveryInFlightRef.current?.key === key) return;
    let recoveredSnapshot: ConversationThread | null = null;
    const promise = getConversation(threadId).then((incoming) => {
      if (
        effectiveAccountIdRef.current !== accountId
        || activeConversationThreadIdRef.current !== threadId
        || conversationScopeEpochRef.current !== epoch
        || incoming.account_id !== accountId
      ) return;
      qc.setQueryData<ConversationThread>(["brain-conversation", threadId], (current) => {
        const reconciled = reconcileConversationThread(current, incoming);
        const projected = replayQueuedDurableTurnEvents(reconciled, accountId, threadId, epoch);
        recoveredSnapshot = projected;
        return projected;
      });
    }).catch(() => undefined).finally(() => {
      if (recoveryInFlightRef.current?.key === key) recoveryInFlightRef.current = null;
      const requirements = projectionRecoveryRequirementsRef.current.get(key);
      if (!requirements || recoveredSnapshot == null) return;
      const unmetRequirements = requirements.events.filter((event) =>
        !isConversationProjectionRequirementSatisfied(recoveredSnapshot!, event)
      );
      if (unmetRequirements.length === 0) {
        projectionRecoveryRequirementsRef.current.delete(key);
      } else if (!requirements.followUpUsed) {
        requirements.events = unmetRequirements;
        requirements.followUpUsed = true;
        if (
          effectiveAccountIdRef.current === accountId
          && activeConversationThreadIdRef.current === threadId
          && conversationScopeEpochRef.current === epoch
        ) {
          recoverConversationProjectionRef.current();
        }
      }
    });
    recoveryInFlightRef.current = { key, promise };
  }, [qc, replayQueuedDurableTurnEvents]);
  recoverConversationProjectionRef.current = recoverConversationProjection;
  requestConversationProjectionRecoveryRef.current = (accountId, threadId, epoch, events) => {
    if (
      effectiveAccountIdRef.current !== accountId
      || activeConversationThreadIdRef.current !== threadId
      || conversationScopeEpochRef.current !== epoch
    ) return;
    const key = `${accountId}:${threadId}:${epoch}`;
    const requirements = projectionRecoveryRequirementsRef.current.get(key) ?? {
      events: [],
      followUpUsed: false,
    };
    for (const event of events) {
      if (
        isProjectionRecoveryEvent(event)
        && !requirements.events.some((candidate) => candidate.id === event.id)
      ) {
        requirements.events.push(event);
      }
    }
    if (requirements.events.length === 0) return;
    projectionRecoveryRequirementsRef.current.set(key, requirements);
    if (recoveryInFlightRef.current?.key === key) return;
    recoverConversationProjectionRef.current();
  };

  useConversationRuntimeStream({
    accountId: effectiveAccountId,
    threadId: activeConversationThreadId,
    onEvent: projectTransientRuntimeEvent,
  });
  useConversationTurnEvents({
    accountId: effectiveAccountId,
    threadId: activeConversationThreadId,
    onEvent: projectDurableTurnEvent,
    onRecover: recoverConversationProjection,
  });
  const accountReady = Boolean(
    effectiveAccount
    && (
      effectiveAccount.auth_status === "authorized"
      || effectiveAccount.auth_status === "manual"
    ),
  );

  useEffect(() => {
    const nextAccountId = effectiveAccountId;
    const previousAccountId = previousAccountIdRef.current;
    const accountChanged = previousAccountId != null && previousAccountId !== nextAccountId;
    previousAccountIdRef.current = nextAccountId;
    const nextThreadId = effectiveAccountId != null
      ? getActiveConversationThreadId(effectiveAccountId)
      : null;
    conversationScopeEpochRef.current += 1;
    activeConversationThreadIdRef.current = nextThreadId;
    setActiveConversationThreadId(nextThreadId);
    setPendingTurn(null);
    pendingClientMessageId.current = null;
    if (accountChanged) setGoal("");
    if (accountChanged) qc.removeQueries({ queryKey: ["account-artifacts", previousAccountId] });
    setDraftAttachments([]);
    setSelectedArtifact(null);
    setSourceReturnTarget(null);
    setSourceReturnError(null);
  }, [effectiveAccountId, qc]);

  const conversationQuery = useQuery({
    queryKey: ["brain-conversation", activeConversationThreadId],
    queryFn: async () => {
      const incoming = await getConversation(activeConversationThreadId!);
      const reconciled = reconcileConversationThread(
        qc.getQueryData<ConversationThread>(["brain-conversation", activeConversationThreadId!]),
        incoming,
      );
      const accountId = effectiveAccountIdRef.current;
      const epoch = conversationScopeEpochRef.current;
      if (
        accountId == null
        || incoming.account_id !== accountId
        || activeConversationThreadIdRef.current !== activeConversationThreadId
      ) return reconciled;
      return replayQueuedDurableTurnEvents(reconciled, accountId, activeConversationThreadId!, epoch);
    },
    enabled: activeConversationThreadId != null,
    // createConversation seeds this exact cache before activating the Thread.
    // Refetching on that first mount can replace a just-added optimistic Turn
    // with an older empty server snapshot.
    refetchOnMount: false,
    staleTime: 10_000,
  });
  useEffect(() => {
    const accountId = effectiveAccountId;
    const threadId = activeConversationThreadId;
    const epoch = conversationScopeEpochRef.current;
    pendingDurableTurnEventsRef.current = pendingDurableTurnEventsRef.current.filter((item) =>
      accountId != null
      && threadId != null
      && isQueuedDurableEventForScope(item, accountId, threadId, epoch)
    );
    const activeScopeKey = accountId != null && threadId != null
      ? `${accountId}:${threadId}:${epoch}`
      : null;
    for (const key of projectionRecoveryRequirementsRef.current.keys()) {
      if (key !== activeScopeKey) projectionRecoveryRequirementsRef.current.delete(key);
    }
  }, [activeConversationThreadId, effectiveAccountId]);
  useEffect(() => {
    const accountId = effectiveAccountId;
    const threadId = activeConversationThreadId;
    const epoch = conversationScopeEpochRef.current;
    if (accountId == null || threadId == null) return;

    const current = qc.getQueryData<ConversationThread>(["brain-conversation", threadId]);
    if (!current || current.account_id !== accountId) return;
    const projected = replayQueuedDurableTurnEvents(current, accountId, threadId, epoch);
    if (projected !== current) qc.setQueryData<ConversationThread>(["brain-conversation", threadId], projected);
  }, [activeConversationThreadId, conversationQuery.data, effectiveAccountId, qc, replayQueuedDurableTurnEvents]);
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
    conversationScopeEpochRef.current += 1;
    activeConversationThreadIdRef.current = null;
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
    () => findLatestPendingInterrupt(activeConversation) == null
      ? findLatestPendingApproval(activeConversation)
      : null,
    [activeConversation],
  );

  const conversationTurnMutation = useMutation({
    mutationFn: ({
      threadId,
      content,
      clientMessageId,
      requestedSkillCode,
      attachmentIds,
    }: {
      threadId: number;
      content: string;
      clientMessageId: string;
      requestedSkillCode: string | null;
      attachmentIds: number[];
      accountId: number;
    }) => sendConversationTurn(
      threadId,
      requestedSkillCode == null
        ? {
            client_message_id: clientMessageId,
            message: content,
            attachment_ids: attachmentIds,
          }
        : {
            client_message_id: clientMessageId,
            message: content,
            requested_skill_code: requestedSkillCode,
            execution_preference: "AUTO",
            attachment_ids: attachmentIds,
          },
    ),
    onSuccess: (submission, variables) => {
      if (!isCurrentConversationRequest({
        activeAccountId: effectiveAccountIdRef.current,
        activeThreadId: activeConversationThreadIdRef.current,
        accountId: variables.accountId,
        threadId: variables.threadId,
      })) {
        conversationRequestsRef.current.delete(variables.clientMessageId);
        return;
      }
      qc.setQueryData<ConversationThread>(
        ["brain-conversation", variables.threadId],
        (current) => {
          if (!current) return current;
          const clientMessageId = submission.turn.client_message_id || variables.clientMessageId;
          const merged = mergeConversationTurn(current, {
            ...submission.turn,
            client_message_id: clientMessageId,
          });
          const mergedTurn = merged.turns.find(
            (turn) => turn.client_message_id === clientMessageId,
          ) ?? submission.turn;
          return upsertTurnByClientMessageId(
            current,
            variables.threadId,
            clientMessageId,
            () => mergedTurn,
          );
        },
      );
      conversationRequestsRef.current.delete(variables.clientMessageId);
      setDraftAttachments([]);
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
      const request = conversationRequestsRef.current.get(variables.clientMessageId);
      if (!isCurrentConversationRequest({
        activeAccountId: effectiveAccountIdRef.current,
        activeThreadId: activeConversationThreadIdRef.current,
        accountId: variables.accountId,
        threadId: variables.threadId,
      })) {
        const scopeKey = failedOptimisticScopeKey(variables.accountId, variables.threadId);
        const failed = failedOptimisticTurnIdsRef.current.get(scopeKey) ?? new Set<string>();
        failed.add(variables.clientMessageId);
        failedOptimisticTurnIdsRef.current.set(scopeKey, failed);
        conversationRequestsRef.current.delete(variables.clientMessageId);
        return;
      }
      qc.setQueryData<ConversationThread>(
        ["brain-conversation", variables.threadId],
        (current) => current ? removeOptimisticTurn(current, variables.clientMessageId) : current,
      );
      conversationRequestsRef.current.delete(variables.clientMessageId);
      setPendingTurn((current) => {
        if (current?.clientMessageId === variables.clientMessageId) {
          setGoal((value) => value || request?.content || current.content);
          return null;
        }
        return current;
      });
      pendingClientMessageId.current = null;
      message.error(presentApiError(error, "消息发送失败，请稍后重试。").message);
    },
  });

  const stopMutation = useMutation({
    mutationFn: (input:
      | {
          mode: "canonical";
          threadId: number;
          turnId: number;
          idempotencyKey: string;
        }
      | {
          mode: "legacy_optimistic";
          clientMessageId: string;
          taskId: number | null;
        }
    ) => input.mode === "canonical"
      ? stopConversationTurn({
          threadId: input.threadId,
          turnId: input.turnId,
          idempotencyKey: input.idempotencyKey,
        }).then(() => undefined)
      : stopBrainGeneration({
          clientMessageId: input.clientMessageId,
          taskId: input.taskId,
        }).then(() => undefined),
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

  const interruptMutation = useMutation({
    mutationFn: (input: {
      interrupt: TurnInterrupt;
      resolution: Record<string, unknown>;
      idempotencyKey: string;
    }) => resolveTurnInterrupt({
      interruptId: input.interrupt.id,
      expectedVersion: input.interrupt.version,
      resolution: input.resolution,
      idempotencyKey: input.idempotencyKey,
    }),
    onSuccess: (result) => {
      if (activeConversationThreadId != null) {
        qc.setQueryData<ConversationThread>(
          ["brain-conversation", activeConversationThreadId],
          (current) => current == null ? current : {
            ...current,
            turns: current.turns.map((turn) =>
              turn.pending_interrupt?.id === result.interrupt.id
                ? { ...turn, pending_interrupt: null }
                : turn
            ),
          },
        );
        void qc.invalidateQueries({
          queryKey: ["brain-conversation", activeConversationThreadId],
        });
      }
      if (result.dispatch_deferred) {
        message.info(result.dispatch_message ?? "你的回复已保存，任务会自动继续。");
      }
    },
    onError: (error) => message.error(
      presentApiError(error, "处理失败，请重试。").message,
    ),
  });

  const deliverableActionMutation = useMutation({
    mutationFn: async (input: {
      sourceArtifact: Artifact;
      action: Extract<ArtifactAction, { type: "execute" }>["action"];
      actionInput: Record<string, unknown>;
      accountId: number;
      idempotencyKey: string;
    }) => {
      const execution = await executeDeliverableAction({
        artifactId: input.sourceArtifact.id,
        actionCode: input.action.code,
        idempotencyKey: input.idempotencyKey,
        input: input.actionInput,
      });
      const revisedArtifact = execution.resource?.type === "artifact"
        ? await getArtifact(execution.resource.id)
        : null;
      return { execution, revisedArtifact };
    },
    onSuccess: ({ execution, revisedArtifact }, input) => {
      if (effectiveAccountIdRef.current !== input.accountId) return;
      if (revisedArtifact != null) {
        if (!matchesArtifactResponse(revisedArtifact, input.sourceArtifact, "revision")) {
          message.error("修改版本返回校验失败，请刷新后重试。");
          return;
        }
        setArtifactRevisionChains((current) =>
          appendArtifactRevision(current, input.sourceArtifact, revisedArtifact)
        );
        setArtifactSourceOverrides((current) =>
          supersedeRootArtifact(current, input.sourceArtifact)
        );
        setSelectedArtifact((current) =>
          current?.id === input.sourceArtifact.id
          && current.account_id === input.sourceArtifact.account_id
            ? revisedArtifact
            : current
        );
      }
      setArtifactRefreshKey((value) => value + 1);
      void qc.invalidateQueries({
        queryKey: ["account-artifacts", input.sourceArtifact.account_id],
      });
      if (activeConversationThreadId != null) {
        void qc.invalidateQueries({
          queryKey: ["brain-conversation", activeConversationThreadId],
        });
      }
      message.success(actionSuccessCopy(input.action.label, execution.status));
    },
    onError: (error, input) => {
      if (effectiveAccountIdRef.current !== input.accountId) return;
      message.error(presentApiError(error, `${input.action.label}失败，请稍后重试。`).message);
    },
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

  const ensureAccountThread = useCallback(async (
    account: Account,
    scopeToken?: ConversationScopeToken,
  ) => {
    const matchesScope = (threadId = scopeToken?.initialThreadId) =>
      scopeToken == null
        ? effectiveAccountIdRef.current === account.id
        : matchesConversationScope(scopeToken, threadId);
    if (!matchesScope()) return null;
    const savedThreadId =
      activeConversationThreadId ?? getActiveConversationThreadId(account.id);
    const thread = savedThreadId == null
      ? await createConversation({ account_id: account.id })
      : await getConversation(savedThreadId);
    if (
      thread.account_id !== account.id
      || !matchesScope()
    ) {
      return null;
    }
    if (!matchesScope()) return null;
    qc.setQueryData<ConversationThread>(["brain-conversation", thread.id], (current) =>
      reconcileConversationThread(current, thread)
    );
    if (!matchesScope()) return null;
    persistActiveConversationThreadId(account.id, thread.id);
    if (!matchesScope()) return null;
    activeConversationThreadIdRef.current = thread.id;
    setActiveConversationThreadId(thread.id);
    return thread;
  }, [activeConversationThreadId, matchesConversationScope, qc]);

  const submitTurn = useCallback(async ({
    content,
    requestedSkillCode,
    attachmentIds,
  }: {
    content: string;
    requestedSkillCode: string | null;
    attachmentIds?: number[];
  }) => {
    const account = effectiveAccount;
    if (!account) return;
    const request: ConversationRequest = {
      accountId: account.id,
      initialThreadId: activeConversationThreadIdRef.current,
      scopeEpoch: conversationScopeEpochRef.current,
      threadId: null,
      clientMessageId: createClientMessageId(),
      content,
    };
    conversationRequestsRef.current.set(request.clientMessageId, request);
    let thread: ConversationThread | null = null;
    try {
      thread = await ensureAccountThread(account, {
        accountId: request.accountId,
        initialThreadId: request.initialThreadId,
        epoch: request.scopeEpoch,
      });
    } catch (error) {
      conversationRequestsRef.current.delete(request.clientMessageId);
      if (
        matchesConversationScope({
          accountId: request.accountId,
          initialThreadId: request.initialThreadId,
          epoch: request.scopeEpoch,
        })
      ) {
        setGoal((current) => current || request.content);
      }
      throw error;
    }
    if (!thread) {
      conversationRequestsRef.current.delete(request.clientMessageId);
      if (
        matchesConversationScope({
          accountId: request.accountId,
          initialThreadId: request.initialThreadId,
          epoch: request.scopeEpoch,
        })
      ) {
        setGoal((current) => current || request.content);
      }
      return;
    }
    request.threadId = thread.id;
    if (!matchesConversationScope({
      accountId: request.accountId,
      initialThreadId: request.initialThreadId,
      epoch: request.scopeEpoch,
    }, thread.id)) {
      conversationRequestsRef.current.delete(request.clientMessageId);
      return;
    }
    followLatestMessage.current = true;
    setShowJumpToLatest(false);
    pendingClientMessageId.current = request.clientMessageId;
    qc.setQueryData<ConversationThread>(
      ["brain-conversation", thread.id],
      (current) => current
        ? appendOptimisticTurn(current, request.clientMessageId, content)
        : current,
    );
    setPendingTurn({ clientMessageId: request.clientMessageId, content, taskId: null });
    await conversationTurnMutation.mutateAsync({
      threadId: thread.id,
      content,
      clientMessageId: request.clientMessageId,
      requestedSkillCode,
      attachmentIds: attachmentIds ?? draftAttachments
        .filter((item) => item.status === "ready" && item.id != null)
        .map((item) => item.id as number),
      accountId: account.id,
    });
  }, [conversationTurnMutation, draftAttachments, effectiveAccount, ensureAccountThread, matchesConversationScope, qc]);

  const requestAccountSelection = useCallback(() => {
    const selector = document.querySelector<HTMLButtonElement>('[aria-label="当前账号"]');
    if (selector) {
      selector.focus();
      selector.click();
      return;
    }
    message.info("请在顶部选择抖音账号");
  }, [message]);

  const uploadDraftFiles = useCallback(async (files: File[]) => {
    const account = effectiveAccount;
    if (!account) {
      requestAccountSelection();
      return;
    }
    const available = Math.max(0, 5 - draftAttachments.length);
    const accepted = files.slice(0, available);
    if (accepted.length < files.length) message.warning("每轮最多添加 5 个附件");
    if (accepted.length === 0) return;
    const thread = await ensureAccountThread(account);
    if (!thread) return;
    const items: DraftAttachment[] = accepted.map((file) => ({
      key: `${Date.now()}-${file.name}-${Math.random().toString(36).slice(2)}`,
      filename: file.name,
      file,
      threadId: thread.id,
      id: null,
      status: "uploading",
    }));
    setDraftAttachments((current) => [...current, ...items]);
    await Promise.all(items.map(async (item) => {
      try {
        const [uploaded] = await uploadConversationAttachments(thread.id, [item.file]);
        if (!uploaded) throw new Error("附件上传未返回结果");
        setDraftAttachments((current) => current.map((candidate) =>
          candidate.key === item.key
            ? { ...candidate, id: uploaded.id, status: "ready", error: undefined }
            : candidate
        ));
      } catch (error) {
        const detail = presentApiError(error, "上传失败，请重试").message;
        setDraftAttachments((current) => current.map((candidate) =>
          candidate.key === item.key
            ? { ...candidate, status: "error", error: detail }
            : candidate
        ));
      }
    }));
  }, [draftAttachments.length, effectiveAccount, ensureAccountThread, message, requestAccountSelection]);

  const retryDraftAttachment = useCallback(async (attachment: DraftAttachment) => {
    setDraftAttachments((current) => current.map((item) =>
      item.key === attachment.key
        ? { ...item, status: "uploading", error: undefined }
        : item
    ));
    try {
      const [uploaded] = await uploadConversationAttachments(
        attachment.threadId,
        [attachment.file],
      );
      if (!uploaded) throw new Error("附件上传未返回结果");
      setDraftAttachments((current) => current.map((item) =>
        item.key === attachment.key
          ? { ...item, id: uploaded.id, status: "ready", error: undefined }
          : item
      ));
    } catch (error) {
      setDraftAttachments((current) => current.map((item) =>
        item.key === attachment.key
          ? { ...item, status: "error", error: presentApiError(error, "上传失败，请重试").message }
          : item
      ));
    }
  }, []);

  const removeDraftAttachment = useCallback(async (attachment: DraftAttachment) => {
    if (attachment.id == null) {
      setDraftAttachments((current) => current.filter((item) => item.key !== attachment.key));
      return;
    }
    setDraftAttachments((current) => current.map((item) =>
      item.key === attachment.key ? { ...item, status: "removing" } : item
    ));
    try {
      await deleteConversationAttachment(attachment.threadId, attachment.id);
      setDraftAttachments((current) => current.filter((item) => item.key !== attachment.key));
    } catch (error) {
      setDraftAttachments((current) => current.map((item) =>
        item.key === attachment.key
          ? { ...item, status: "error", error: presentApiError(error, "移除失败").message }
          : item
      ));
    }
  }, []);

  const startWorkflow = async () => {
    if (launcherRequestInFlight.current || isGenerating) return;
    const content = goal.trim();
    if (!content) {
      message.warning("先写下要交给运营大脑的运营目标");
      return;
    }
    if (draftAttachments.some((item) => item.status === "error")) {
      message.warning("请先重试或移除上传失败的附件");
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
      // submitTurn restores the captured goal only if its original scope remains active.
    } finally {
      launcherRequestInFlight.current = false;
      setLauncherPending(false);
    }
  };

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
    conversationScopeEpochRef.current += 1;
    activeConversationThreadIdRef.current = null;
    setActiveConversationThreadId(null);
    setPendingTurn(null);
    pendingClientMessageId.current = null;
    setApprovalComment("");
    setGoal("");
    setDraftAttachments([]);
    setSourceReturnTarget(null);
    setSourceReturnError(null);
    followLatestMessage.current = true;
    setShowJumpToLatest(false);
  };

  const selectConversation = (threadId: number) => {
    if (!effectiveAccount) return;
    const failedScopeKey = failedOptimisticScopeKey(effectiveAccount.id, threadId);
    const failedOptimisticIds = failedOptimisticTurnIdsRef.current.get(failedScopeKey);
    if (failedOptimisticIds?.size) {
      qc.setQueryData<ConversationThread>(["brain-conversation", threadId], (current) =>
        current
          ? [...failedOptimisticIds].reduce(
              (thread, clientMessageId) => removeOptimisticTurn(thread, clientMessageId),
              current,
            )
          : current
      );
      failedOptimisticTurnIdsRef.current.delete(failedScopeKey);
    }
    clearActiveBrainTaskId(effectiveAccount.id);
    persistActiveConversationThreadId(effectiveAccount.id, threadId);
    conversationScopeEpochRef.current += 1;
    activeConversationThreadIdRef.current = threadId;
    setActiveConversationThreadId(threadId);
    setDraftAttachments([]);
    setPendingTurn(null);
    pendingClientMessageId.current = null;
    setWorkspaceMode("conversation");
    followLatestMessage.current = true;
    setShowJumpToLatest(false);
  };

  const handleConversationDeleted = (threadId: number) => {
    if (threadId !== activeConversationThreadId || !effectiveAccount) return;
    clearActiveConversationThreadId(effectiveAccount.id);
    conversationScopeEpochRef.current += 1;
    activeConversationThreadIdRef.current = null;
    setActiveConversationThreadId(null);
    setDraftAttachments([]);
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
    conversationScopeEpochRef.current += 1;
    activeConversationThreadIdRef.current = target.threadId;
    setActiveConversationThreadId(target.threadId);
    if (retry) {
      void qc.fetchQuery({
        queryKey: ["brain-conversation", target.threadId],
        queryFn: async () => {
          const incoming = await getConversation(target.threadId);
          return reconcileConversationThread(
            qc.getQueryData<ConversationThread>(["brain-conversation", target.threadId]),
            incoming,
          );
        },
      }).then(() => setSourceReturnTarget(target)).catch(() => {
        setSourceReturnError("来源对话暂时无法加载，请在运营内容中心重试。");
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
      setSourceReturnError("来源对话暂时无法加载，请在运营内容中心重试。");
      setSourceReturnTarget(null);
      return;
    }
    const source = conversationQuery.data;
    if (!source) return;
    if (
      source.id !== sourceReturnTarget.threadId
      || source.account_id !== sourceReturnTarget.accountId
    ) {
      setSourceReturnError("来源对话与当前运营内容不匹配，请在运营内容中心重试。");
      setSourceReturnTarget(null);
      return;
    }
    if (!source.turns.some((turn) => turn.id === sourceReturnTarget.turnId)) {
      setSourceReturnError("来源对话未包含该运营内容所在轮次，请在运营内容中心重试。");
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
              id={workspaceMode === "conversation" ? "brain-conversation-panel" : "brain-plans-panel"}
              role="tabpanel"
              aria-labelledby={workspaceMode === "conversation" ? "brain-conversation-tab" : "brain-plans-tab"}
              aria-label={workspaceMode === "conversation" ? "对话" : "方案与内容"}
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
                      aria-label="方案与内容详情"
                    >
                      <ArtifactCard
                        artifact={selectedArtifact}
                        actionPending={
                          deliverableActionMutation.isPending
                          && deliverableActionMutation.variables?.sourceArtifact.id
                            === selectedArtifact.id
                        }
                        revisionPending={
                          deliverableActionMutation.isPending
                          && deliverableActionMutation.variables?.action.code === "request_revision"
                          && deliverableActionMutation.variables?.sourceArtifact.id
                            === selectedArtifact.id
                        }
                        onAction={(action) => handleArtifactAction(action, deliverableActionMutation.mutate)}
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
                      deliverableActionMutation.isPending
                      && deliverableActionMutation.variables?.action.code === "request_revision"
                        ? deliverableActionMutation.variables?.sourceArtifact.id ?? null
                        : null
                    }
                    actionPendingArtifactId={
                      deliverableActionMutation.isPending
                        ? deliverableActionMutation.variables?.sourceArtifact.id ?? null
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
                    resolvingInterruptId={
                      interruptMutation.isPending
                        ? interruptMutation.variables?.interrupt.id ?? null
                        : null
                    }
                    onResolveInterrupt={(interrupt, resolution) =>
                      interruptMutation.mutate({
                        interrupt,
                        resolution,
                        idempotencyKey: createInterruptIdempotencyKey(interrupt),
                      })
                    }
                    onArtifactAction={(action) => handleArtifactAction(action, deliverableActionMutation.mutate)}
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
                attachments={draftAttachments}
                attachmentBusy={draftAttachments.some((item) =>
                  item.status === "uploading" || item.status === "removing"
                )}
                onFilesSelected={(files) => void uploadDraftFiles(files)}
                onRemoveAttachment={(attachment) => void removeDraftAttachment(attachment)}
                onRetryAttachment={retryDraftAttachment}
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
                  if (!activeTurn || stopMutation.isPending) return;
                  if (activeTurn.id != null) {
                    stopMutation.mutate({
                      mode: "canonical",
                      threadId: activeTurn.thread_id,
                      turnId: activeTurn.id,
                      idempotencyKey: createStopIdempotencyKey(activeTurn),
                    });
                    return;
                  }
                  if (!activeTurn.client_message_id) return;
                  stopMutation.mutate({
                    mode: "legacy_optimistic",
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
  const navigate = useNavigate();
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
        <div className="tz-brain-mode-switch" role="tablist" aria-label="运营工作区">
          <Button
            type={workspaceMode === "conversation" ? "primary" : "text"}
            size="small"
            role="tab"
            id="brain-conversation-tab"
            aria-label="对话"
            aria-selected={workspaceMode === "conversation"}
            aria-controls="brain-conversation-panel"
            onKeyDown={(event) => handleWorkspaceTabKey(event, workspaceMode, onWorkspaceModeChange)}
            onClick={() => onWorkspaceModeChange("conversation")}
          >
            对话
          </Button>
          <Button
            type={workspaceMode === "results" ? "primary" : "text"}
            size="small"
            role="tab"
            id="brain-plans-tab"
            aria-label="方案与内容"
            aria-selected={workspaceMode === "results"}
            aria-controls="brain-plans-panel"
            onKeyDown={(event) => handleWorkspaceTabKey(event, workspaceMode, onWorkspaceModeChange)}
            onClick={() => onWorkspaceModeChange("results")}
          >
            方案与内容
          </Button>
        </div>
        <nav aria-label="运营相关导航">
          <Button
            size="small"
            disabled={!account}
            onClick={() => account && navigate(`/accounts/${account.id}/data`)}
          >
            抖音数据
          </Button>
          <Button size="small" onClick={() => navigate("/approvals")}>待处理</Button>
        </nav>
        {!account ? <small>请先选择账号后查看抖音数据</small> : null}
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

function handleWorkspaceTabKey(
  event: KeyboardEvent<HTMLElement>,
  mode: "conversation" | "results",
  onChange: (mode: "conversation" | "results") => void,
) {
  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
  event.preventDefault();
  const nextMode = mode === "conversation" ? "results" : "conversation";
  onChange(nextMode);
  window.requestAnimationFrame(() => document.getElementById(
    nextMode === "conversation" ? "brain-conversation-tab" : "brain-plans-tab",
  )?.focus());
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
  execute: (input: {
    sourceArtifact: Artifact;
    action: Extract<ArtifactAction, { type: "execute" }>["action"];
    actionInput: Record<string, unknown>;
    accountId: number;
    idempotencyKey: string;
  }) => void,
) {
  if (action.type === "execute") {
    execute({
      sourceArtifact: action.artifact,
      action: action.action,
      actionInput: action.input,
      accountId: action.artifact.account_id,
      idempotencyKey: action.idempotencyKey,
    });
  } else if (action.type === "export") {
    downloadArtifact(action.artifact);
  }
}

function matchesArtifactResponse(
  returned: Artifact,
  source: Artifact,
    operation: "revision",
) {
  const sameScope =
    returned.account_id === source.account_id
    && returned.thread_id === source.thread_id
    && returned.turn_id === source.turn_id
    && returned.artifact_type === source.artifact_type;
  return sameScope
    && operation === "revision"
    && returned.id !== source.id
    && returned.version === source.version + 1;
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

function actionSuccessCopy(label: string, status: string) {
  return status === "queued" ? `${label}已进入执行队列` : `${label}已完成`;
}

function downloadArtifact(artifact: Artifact) {
  const payload = JSON.stringify({
    title: artifact.title,
    version: artifact.version,
    summary: artifact.summary,
    sections: artifact.sections,
  }, null, 2);
  const url = URL.createObjectURL(new Blob([payload], { type: "application/json;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${presentDeliverable(artifact).typeLabel}-V${artifact.version}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}

function asRuntimePayload(payload: DyEvent["payload"]) {
  return typeof payload === "object" && payload != null
    ? payload as Record<string, unknown>
    : null;
}

function isTerminalConversationTurnEvent(type: string) {
  return [
    "turn.blocked",
    "turn.completed",
    "turn.failed",
    "turn.cancelled",
    "turn.stopped",
  ].includes(type);
}

function durableDeliverableId(event: ConversationTurnEvent) {
  const deliverableId = Number(event.payload.deliverable_id);
  return Number.isInteger(deliverableId) && deliverableId > 0 ? deliverableId : null;
}

function isProjectionRecoveryEvent(event: ConversationTurnEvent) {
  return event.type === "turn.paused"
    || event.type === "turn.interrupt_requested"
    || event.type === "turn.interrupt_resolved"
    || event.type === "turn.interrupt_cancelled"
    || (event.type === "deliverable.updated" && durableDeliverableId(event) != null);
}

function needsConversationProjectionRecovery(
  thread: ConversationThread,
  event: ConversationTurnEvent,
) {
  if (
    event.type === "turn.paused"
    || event.type === "turn.interrupt_requested"
    || event.type === "turn.interrupt_resolved"
    || event.type === "turn.interrupt_cancelled"
  ) return true;
  const deliverableId = durableDeliverableId(event);
  if (event.type !== "deliverable.updated" || deliverableId == null) return false;
  const turn = thread.turns.find((candidate) => candidate.id === event.turn_id);
  return turn != null && !hasArtifactProjection(turn, deliverableId);
}

function isConversationProjectionRequirementSatisfied(
  thread: ConversationThread,
  event: ConversationTurnEvent,
) {
  const turn = thread.turns.find((candidate) => candidate.id === event.turn_id);
  if (!turn) return false;
  if (event.type === "turn.interrupt_requested") {
    return turn.pending_interrupt?.id === Number(event.payload.interrupt_id);
  }
  if (event.type === "turn.interrupt_resolved" || event.type === "turn.interrupt_cancelled") {
    return turn.pending_interrupt == null;
  }
  if (event.type === "turn.paused") {
    const pausedStatus = typeof event.payload.status === "string"
      ? event.payload.status
      : turn.status;
    if (turn.status !== pausedStatus) return false;
    if (pausedStatus !== "waiting_permission") return true;
    return turn.projections.some((projection) =>
      projection.type === "approval" && projection.approval.status === "waiting_approval"
    );
  }
  const deliverableId = durableDeliverableId(event);
  return event.type !== "deliverable.updated"
    || deliverableId == null
    || hasArtifactProjection(turn, deliverableId);
}

function hasArtifactProjection(turn: ConversationTurn, deliverableId: number) {
  return turn.projections.some((projection) =>
    projection.type === "artifact" && projection.artifact_id === deliverableId
  );
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

function findLatestPendingInterrupt(
  conversation: ConversationThread | null,
): TurnInterrupt | null {
  if (!conversation) return null;
  for (let index = conversation.turns.length - 1; index >= 0; index -= 1) {
    const interrupt = conversation.turns[index].pending_interrupt;
    if (interrupt?.status === "pending") return interrupt;
  }
  return null;
}

function createInterruptIdempotencyKey(interrupt: TurnInterrupt) {
  const nonce = typeof globalThis.crypto?.randomUUID === "function"
    ? globalThis.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  return `interrupt-${interrupt.id}-v${interrupt.version}-${nonce}`;
}

function createStopIdempotencyKey(turn: ConversationTurn) {
  const nonce = typeof globalThis.crypto?.randomUUID === "function"
    ? globalThis.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  return `stop-${turn.thread_id}-${turn.id}-${nonce}`;
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
