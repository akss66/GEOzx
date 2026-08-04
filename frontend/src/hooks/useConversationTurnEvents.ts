import { useEffect, useRef, useState } from "react";

import { listConversationEvents } from "../api/brain";
import { API_BASE, TOKEN_KEY } from "../api/client";
import type { ConversationTurnEvent } from "../types";

const RECONNECT_DELAYS = [500, 1_000, 2_000, 4_000, 8_000] as const;
const EVENT_PAGE_SIZE = 500;

export type ConversationTurnEventConnectionState =
  | "idle"
  | "recovering"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "unauthorized"
  | "unavailable";

export interface ConversationSequenceGap {
  threadId: number;
  turnId: number;
  previousSequence: number;
  receivedSequence: number;
  previousEventId: number;
  receivedEventId: number;
}

export interface UseConversationTurnEventsOptions {
  threadId: number | null | undefined;
  accountId: number | null | undefined;
  onEvent?: (event: ConversationTurnEvent) => void;
  onRecover?: (gap: ConversationSequenceGap) => void;
}

interface ParsedSseFrame {
  id: string | null;
  event: string | null;
  data: string[];
}

function createFrame(): ParsedSseFrame {
  return { id: null, event: null, data: [] };
}

function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError"
    || typeof error === "object"
      && error !== null
      && (("name" in error
        && ["AbortError", "CanceledError"].includes(
          String((error as { name?: unknown }).name),
        ))
        || ("code" in error
          && (error as { code?: unknown }).code === "ERR_CANCELED"));
}

function statusFrom(error: unknown) {
  if (error instanceof Response) return error.status;
  if (typeof error === "object" && error !== null && "response" in error) {
    const response = (error as { response?: { status?: unknown } }).response;
    return typeof response?.status === "number" ? response.status : undefined;
  }
  return undefined;
}

function isConversationTurnEvent(value: unknown): value is ConversationTurnEvent {
  if (typeof value !== "object" || value === null) return false;
  const event = value as Partial<ConversationTurnEvent>;
  return Number.isFinite(event.id)
    && Number.isFinite(event.sequence)
    && typeof event.type === "string"
    && typeof event.payload === "object"
    && event.payload !== null
    && Number.isFinite(event.thread_id)
    && Number.isFinite(event.turn_id)
    && typeof event.created_at === "string";
}

function parseSseLine(frame: ParsedSseFrame, line: string) {
  if (line.startsWith(":")) return;
  const separator = line.indexOf(":");
  const field = separator === -1 ? line : line.slice(0, separator);
  const value = separator === -1
    ? ""
    : line.slice(separator + 1).replace(/^ /, "");
  if (field === "id") frame.id = value;
  if (field === "event") frame.event = value;
  if (field === "data") frame.data.push(value);
}

export function useConversationTurnEvents({
  threadId,
  accountId,
  onEvent,
  onRecover,
}: UseConversationTurnEventsOptions) {
  const [connectionState, setConnectionState] =
    useState<ConversationTurnEventConnectionState>("idle");
  const [lastEventId, setLastEventId] = useState<number | null>(null);
  const [error, setError] = useState<unknown>(null);
  const onEventRef = useRef(onEvent);
  const onRecoverRef = useRef(onRecover);
  onEventRef.current = onEvent;
  onRecoverRef.current = onRecover;

  useEffect(() => {
    const token = localStorage.getItem(TOKEN_KEY);
    if (threadId == null || accountId == null || !token) {
      setConnectionState("idle");
      setLastEventId(null);
      setError(null);
      return;
    }

    let disposed = false;
    let controller: AbortController | null = null;
    let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let activeAttempt = 0;
    let reconnectAttempt = 0;
    let lastId = 0;
    const seenIds = new Set<number>();
    const turnSequences = new Map<number, { sequence: number; eventId: number }>();
    const reportedGaps = new Set<string>();

    const clearRetryTimer = () => {
      if (retryTimer !== null) {
        clearTimeout(retryTimer);
        retryTimer = null;
      }
    };

    const deliver = (candidate: ConversationTurnEvent) => {
      if (
        candidate.thread_id !== threadId
        || candidate.id <= lastId
        || seenIds.has(candidate.id)
      ) return false;
      seenIds.add(candidate.id);
      lastId = candidate.id;
      setLastEventId(candidate.id);

      const previous = turnSequences.get(candidate.turn_id);
      const previousSequence = previous?.sequence ?? 0;
      const previousEventId = previous?.eventId ?? 0;
      if (candidate.sequence > previousSequence + 1) {
        const key = `${candidate.turn_id}:${previousSequence}:${candidate.sequence}`;
        if (!reportedGaps.has(key)) {
          reportedGaps.add(key);
          onRecoverRef.current?.({
            threadId,
            turnId: candidate.turn_id,
            previousSequence,
            receivedSequence: candidate.sequence,
            previousEventId,
            receivedEventId: candidate.id,
          });
        }
      }
      if (!previous || candidate.sequence >= previous.sequence) {
        turnSequences.set(candidate.turn_id, {
          sequence: candidate.sequence,
          eventId: candidate.id,
        });
      }
      onEventRef.current?.(candidate);
      return true;
    };

    const dispatchFrame = (frame: ParsedSseFrame) => {
      if (frame.data.length === 0) return false;
      try {
        const parsed = JSON.parse(frame.data.join("\n")) as unknown;
        if (!isConversationTurnEvent(parsed)) return false;
        if (frame.id !== null && Number(frame.id) !== parsed.id) return false;
        if (frame.event !== null && frame.event !== parsed.type) return false;
        return deliver(parsed);
      } catch {
        // Malformed transport frames are not durable events.
        return false;
      }
    };

    const ownsAttempt = (attempt: number, signal: AbortSignal) => (
      !disposed
      && activeAttempt === attempt
      && controller?.signal === signal
      && !signal.aborted
    );

    const recover = async (attempt: number, signal: AbortSignal) => {
      if (!ownsAttempt(attempt, signal)) return;
      setConnectionState("recovering");
      while (ownsAttempt(attempt, signal)) {
        const page = await listConversationEvents(threadId, lastId, signal);
        if (!ownsAttempt(attempt, signal)) return;
        const before = lastId;
        for (const item of [...page].sort((left, right) => left.id - right.id)) {
          if (!ownsAttempt(attempt, signal)) return;
          if (isConversationTurnEvent(item)) deliver(item);
        }
        if (page.length < EVENT_PAGE_SIZE || lastId === before) return;
      }
    };

    const scheduleReconnect = (attempt: number, signal: AbortSignal) => {
      if (!ownsAttempt(attempt, signal)) return;
      setConnectionState("reconnecting");
      if (navigator.onLine === false) {
        return;
      }
      const delay = RECONNECT_DELAYS[Math.min(reconnectAttempt, RECONNECT_DELAYS.length - 1)];
      reconnectAttempt += 1;
      retryTimer = setTimeout(() => {
        retryTimer = null;
        if (ownsAttempt(attempt, signal)) void connect();
      }, delay);
    };

    const handleFailure = (
      failure: unknown,
      attempt: number,
      signal: AbortSignal,
    ) => {
      if (!ownsAttempt(attempt, signal) || isAbortError(failure)) return;
      const status = statusFrom(failure);
      if (status === 401) {
        localStorage.removeItem(TOKEN_KEY);
        setConnectionState("unauthorized");
        setError(null);
        return;
      }
      if (status === 403 || status === 404) {
        setConnectionState("unavailable");
        setError(failure);
        return;
      }
      setError(failure);
      scheduleReconnect(attempt, signal);
    };

    const connect = async () => {
      if (disposed) return;
      const attempt = activeAttempt + 1;
      activeAttempt = attempt;
      clearRetryTimer();
      controller?.abort();
      reader?.cancel().catch(() => undefined);
      const currentController = new AbortController();
      controller = currentController;
      try {
        await recover(attempt, currentController.signal);
        if (!ownsAttempt(attempt, currentController.signal)) return;
        setConnectionState("connecting");
        const response = await fetch(
          `${API_BASE}/conversation-threads/${threadId}/event-stream?after_id=${lastId}`,
          {
            signal: currentController.signal,
            credentials: "omit",
            headers: {
              Accept: "text/event-stream",
              Authorization: `Bearer ${token}`,
            },
          },
        );
        if (!ownsAttempt(attempt, currentController.signal)) return;
        if (!response.ok) throw response;
        if (!response.body) throw new Error("SSE response has no readable body");
        setError(null);
        setConnectionState("connected");
        reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let frame = createFrame();
        let hasHealthyProgress = false;
        const consumeLine = (line: string) => {
          const normalized = line.endsWith("\r") ? line.slice(0, -1) : line;
          if (normalized === "") {
            hasHealthyProgress = dispatchFrame(frame) || hasHealthyProgress;
            frame = createFrame();
          } else {
            parseSseLine(frame, normalized);
          }
        };
        while (ownsAttempt(attempt, currentController.signal)) {
          const next = await reader.read();
          if (next.done) break;
          if (!ownsAttempt(attempt, currentController.signal)) break;
          buffer += decoder.decode(next.value, { stream: true });
          let newline = buffer.indexOf("\n");
          while (newline !== -1) {
            consumeLine(buffer.slice(0, newline));
            buffer = buffer.slice(newline + 1);
            newline = buffer.indexOf("\n");
          }
        }
        if (!ownsAttempt(attempt, currentController.signal)) return;
        buffer += decoder.decode();
        if (buffer.length > 0) consumeLine(buffer);
        hasHealthyProgress = dispatchFrame(frame) || hasHealthyProgress;
        if (hasHealthyProgress) reconnectAttempt = 0;
        scheduleReconnect(attempt, currentController.signal);
      } catch (failure) {
        handleFailure(failure, attempt, currentController.signal);
      }
    };

    const reconnectNow = () => {
      if (disposed) return;
      clearRetryTimer();
      void connect();
    };
    const handleVisibility = () => {
      if (document.visibilityState === "visible") reconnectNow();
    };

    window.addEventListener("online", reconnectNow);
    document.addEventListener("visibilitychange", handleVisibility);
    void connect();

    return () => {
      disposed = true;
      clearRetryTimer();
      controller?.abort();
      reader?.cancel().catch(() => undefined);
      window.removeEventListener("online", reconnectNow);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [accountId, threadId]);

  return { connectionState, lastEventId, error };
}
