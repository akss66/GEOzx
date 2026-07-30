import type { DyEvent } from "../../hooks/useEventStream";
import type { ConversationThread, ConversationTurn } from "../../types";

export type TurnIdentity = {
  threadId: number;
  turnId: number | null;
  clientMessageId: string;
};

export function turnDomainKey(identity: TurnIdentity) {
  return `${identity.threadId}:${identity.turnId ?? "pending"}:${identity.clientMessageId}`;
}

export function turnReactKey(identity: TurnIdentity) {
  return `${identity.threadId}:${identity.clientMessageId}`;
}

export function isActiveConversationTurnStatus(status: string) {
  return [
    "claimed",
    "waiting_predecessor",
    "queued",
    "running",
    "retry_wait",
  ].includes(status);
}

export function appendOptimisticTurn(
  thread: ConversationThread,
  clientMessageId: string,
  userInput: string,
): ConversationThread {
  if (thread.turns.some((turn) => turn.client_message_id === clientMessageId)) {
    return thread;
  }
  const timestamp = new Date().toISOString();
  return {
    ...thread,
    turns: [
      ...thread.turns,
      {
        id: null,
        thread_id: thread.id,
        org_id: thread.org_id,
        created_by_id: thread.created_by_id,
        client_message_id: clientMessageId,
        user_input: userInput,
        assistant_response: null,
        intent: null,
        status: "queued",
        projections: [],
        created_at: timestamp,
        updated_at: timestamp,
      },
    ],
  };
}

export function mergeConversationTurn(
  thread: ConversationThread,
  incoming: ConversationTurn,
): ConversationThread {
  if (incoming.thread_id !== thread.id) return thread;
  const index = thread.turns.findIndex((turn) =>
    (incoming.id != null && turn.id === incoming.id)
    || (
      incoming.client_message_id != null
      && turn.client_message_id === incoming.client_message_id
    )
  );
  if (index < 0) return { ...thread, turns: [...thread.turns, incoming] };

  const current = thread.turns[index];
  const preserveRuntimeOverlay = current.stream_state != null
    || (
      current.id != null
      && current.status !== "queued"
      && ["claimed", "waiting_predecessor", "queued"].includes(incoming.status)
    );
  const replacement = preserveRuntimeOverlay
    ? {
        ...incoming,
        assistant_response: current.assistant_response,
        status: current.status,
        stream_state: current.stream_state,
      }
    : incoming;
  return {
    ...thread,
    turns: thread.turns.map((turn, turnIndex) =>
      turnIndex === index ? replacement : turn
    ),
  };
}

export function applyConversationEvent(
  thread: ConversationThread,
  event: DyEvent,
): ConversationThread {
  const payload = asRecord(event.payload);
  if (!payload) return thread;
  const threadId = positiveInteger(payload.thread_id);
  const eventTurnId = positiveInteger(payload.turn_id);
  const clientMessageId = stringValue(payload.client_message_id);
  if (threadId !== thread.id || !clientMessageId) return thread;

  const turnIndex = thread.turns.findIndex((turn) => {
    if (turn.client_message_id !== clientMessageId) return false;
    return turn.id == null || eventTurnId == null || turn.id === eventTurnId;
  });
  if (turnIndex < 0) return thread;

  const turn = thread.turns[turnIndex];
  const next = reduceTurnEvent(turn, event, payload, eventTurnId);
  if (next === turn) return thread;
  return {
    ...thread,
    turns: thread.turns.map((candidate, index) => index === turnIndex ? next : candidate),
  };
}

function reduceTurnEvent(
  turn: ConversationTurn,
  event: DyEvent,
  payload: Record<string, unknown>,
  eventTurnId: number | null,
): ConversationTurn {
  const agentCode = stringValue(payload.agent_code);
  if (agentCode && agentCode !== "00-decision") return turn;

  if (event.type === "brain.runtime.message_start") {
    return reduceStreamFrame(turn, payload, eventTurnId, "start");
  }
  if (event.type === "brain.runtime.message_delta") {
    return reduceStreamFrame(turn, payload, eventTurnId, "delta");
  }
  if (event.type === "brain.runtime.message_done") {
    return reduceStreamFrame(turn, payload, eventTurnId, "done");
  }
  if (event.type === "brain.runtime.message_error") {
    return reduceStreamFrame(turn, payload, eventTurnId, "error");
  }

  const status = eventStatus(event.type, payload);
  if (!status) return turn;
  return {
    ...turn,
    ...(turn.id == null && eventTurnId != null ? { id: eventTurnId } : {}),
    status,
  };
}

function reduceStreamFrame(
  turn: ConversationTurn,
  payload: Record<string, unknown>,
  eventTurnId: number | null,
  phase: "start" | "delta" | "done" | "error",
): ConversationTurn {
  const sequence = nonNegativeInteger(payload.stream_seq);
  if (sequence == null) return turn;
  const messageId = stringValue(payload.message_id) ?? "";
  const previous = turn.stream_state;
  if (previous?.terminal) return turn;
  if (previous && previous.messageId === messageId && sequence <= previous.lastSequence) {
    return turn;
  }

  const content = phase === "delta"
    ? `${turn.assistant_response ?? ""}${String(payload.delta ?? "")}`
    : phase === "done"
      ? String(payload.content ?? payload.message ?? "")
      : phase === "error"
        ? String(payload.error ?? payload.message ?? "")
        : turn.assistant_response;
  const terminal = phase === "done" || phase === "error";
  return {
    ...turn,
    ...(turn.id == null && eventTurnId != null ? { id: eventTurnId } : {}),
    assistant_response: content,
    status: phase === "error"
      ? "failed"
      : phase === "done"
        ? stringValue(payload.status) ?? "completed"
        : "running",
    stream_state: {
      messageId,
      lastSequence: sequence,
      terminal,
    },
  };
}

function eventStatus(type: string, payload: Record<string, unknown>) {
  const explicit = stringValue(payload.status);
  if (explicit) return explicit;
  if (type === "brain.runtime.completed") return "completed";
  if (type === "brain.runtime.failed") return "failed";
  if (type === "brain.runtime.generation_stopped") return "stopped";
  return null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value != null
    ? value as Record<string, unknown>
    : null;
}

function stringValue(value: unknown) {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function positiveInteger(value: unknown) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : null;
}

function nonNegativeInteger(value: unknown) {
  const number = Number(value);
  return Number.isInteger(number) && number >= 0 ? number : null;
}
