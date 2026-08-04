import { useEffect, useRef, useState } from "react";

import { TOKEN_KEY } from "../api/client";

export interface DyEvent {
  id?: number;
  type: string;
  payload?: unknown;
  content_item_id?: number | null;
  project_id?: number | null;
}

export type EventStreamConnectionState =
  | "connecting"
  | "connected"
  | "reconnecting"
  | "disconnected";

interface EventStreamOptions {
  onReconnect?: () => void;
}

export interface ConversationRuntimeStreamOptions {
  accountId: number | null | undefined;
  threadId: number | null | undefined;
  onEvent?: (event: DyEvent) => void;
}

const RECONNECT_DELAYS = [500, 1_000, 2_000, 4_000, 8_000] as const;
const MAX_SEEN_DURABLE_EVENTS = 2_000;

const THREAD_RUNTIME_EVENT_TYPES = new Set([
  "brain.runtime.message_start",
  "brain.runtime.message_delta",
  "brain.runtime.message_done",
  "brain.runtime.message_error",
]);

function isDurableTransportEvent(type: string) {
  return ![
    "brain.runtime.message_start",
    "brain.runtime.message_delta",
  ].includes(type);
}

/** Subscribe to the proxied event stream and recover durable state after reconnecting. */
export function useEventStream(
  onEvent?: (event: DyEvent) => void,
  options?: EventStreamOptions,
) {
  const [connectionState, setConnectionState] =
    useState<EventStreamConnectionState>("connecting");
  const lastRef = useRef<DyEvent | null>(null);
  const handlerRef = useRef(onEvent);
  const reconnectHandlerRef = useRef(options?.onReconnect);
  const seenDurableEventsRef = useRef<{
    ids: Set<number>;
    order: number[];
  }>({ ids: new Set(), order: [] });
  handlerRef.current = onEvent;
  reconnectHandlerRef.current = options?.onReconnect;

  useEffect(() => {
    let disposed = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let reconnectAttempt = 0;
    let hasConnected = false;

    const clearReconnectTimer = () => {
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
    };

    const connect = (recovering: boolean) => {
      if (disposed) return;
      clearReconnectTimer();
      setConnectionState(recovering ? "reconnecting" : "connecting");

      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      const nextSocket = new WebSocket(`${proto}://${window.location.host}/ws/events`);
      socket = nextSocket;

      nextSocket.onopen = () => {
        if (disposed || socket !== nextSocket) return;
        const recovered = hasConnected || recovering;
        hasConnected = true;
        reconnectAttempt = 0;
        setConnectionState("connected");
        if (recovered) reconnectHandlerRef.current?.();
      };

      nextSocket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as DyEvent;
          if (
            isDurableTransportEvent(data.type)
            && typeof data.id === "number"
            && Number.isFinite(data.id)
          ) {
            const seen = seenDurableEventsRef.current;
            if (seen.ids.has(data.id)) return;
            seen.ids.add(data.id);
            seen.order.push(data.id);
            if (seen.order.length > MAX_SEEN_DURABLE_EVENTS) {
              const expiredId = seen.order.shift();
              if (expiredId !== undefined) seen.ids.delete(expiredId);
            }
          }
          lastRef.current = data;
          handlerRef.current?.(data);
        } catch {
          // Ignore non-JSON transport frames.
        }
      };

      nextSocket.onclose = () => {
        if (disposed || socket !== nextSocket) return;
        socket = null;
        setConnectionState("reconnecting");
        const delay = RECONNECT_DELAYS[
          Math.min(reconnectAttempt, RECONNECT_DELAYS.length - 1)
        ];
        reconnectAttempt += 1;
        reconnectTimer = setTimeout(() => connect(true), delay);
      };

      nextSocket.onerror = () => {
        if (nextSocket.readyState !== WebSocket.CLOSED) nextSocket.close();
      };
    };

    const reconnectNow = () => {
      if (disposed) return;
      if (
        socket?.readyState === WebSocket.OPEN ||
        socket?.readyState === WebSocket.CONNECTING
      ) {
        return;
      }
      connect(hasConnected);
    };

    const handleVisibility = () => {
      if (document.visibilityState === "visible") reconnectNow();
    };

    window.addEventListener("online", reconnectNow);
    document.addEventListener("visibilitychange", handleVisibility);
    connect(false);

    return () => {
      disposed = true;
      clearReconnectTimer();
      window.removeEventListener("online", reconnectNow);
      document.removeEventListener("visibilitychange", handleVisibility);
      if (socket) {
        socket.onclose = null;
        socket.close();
      }
      socket = null;
    };
  }, []);

  return {
    connected: connectionState === "connected",
    connectionState,
    last: lastRef.current,
  };
}

/** Subscribe to one authorized Thread's short-lived text stream. */
export function useConversationRuntimeStream({
  accountId,
  threadId,
  onEvent,
}: ConversationRuntimeStreamOptions) {
  const [connectionState, setConnectionState] =
    useState<EventStreamConnectionState>("disconnected");
  const handlerRef = useRef(onEvent);
  const scopeEpochRef = useRef(0);
  handlerRef.current = onEvent;

  useEffect(() => {
    const token = localStorage.getItem(TOKEN_KEY);
    const epoch = scopeEpochRef.current + 1;
    scopeEpochRef.current = epoch;
    if (accountId == null || threadId == null || !token) {
      setConnectionState("disconnected");
      return;
    }

    let disposed = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let reconnectAttempt = 0;
    const clearReconnectTimer = () => {
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
    };
    const owns = (candidate: WebSocket) => (
      !disposed && scopeEpochRef.current === epoch && socket === candidate
    );
    const connect = () => {
      if (disposed || scopeEpochRef.current !== epoch) return;
      clearReconnectTimer();
      setConnectionState(reconnectAttempt === 0 ? "connecting" : "reconnecting");
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      const nextSocket = new WebSocket(`${proto}://${window.location.host}/ws/conversation-runtime`);
      socket = nextSocket;
      nextSocket.onopen = () => {
        if (!owns(nextSocket)) return;
        nextSocket.send(JSON.stringify({
          type: "authenticate",
          token,
          thread_id: threadId,
        }));
      };
      nextSocket.onmessage = (message) => {
        if (!owns(nextSocket)) return;
        try {
          const event = JSON.parse(message.data) as DyEvent;
          if (isRuntimeAuthenticationAcknowledgement(event, threadId)) {
            reconnectAttempt = 0;
            setConnectionState("connected");
            return;
          }
          if (!isThreadRuntimeEvent(event, threadId)) return;
          handlerRef.current?.(event);
        } catch {
          // Ignore malformed transient frames; durable recovery remains SSE-backed.
        }
      };
      nextSocket.onclose = (event) => {
        if (!owns(nextSocket)) return;
        socket = null;
        if (event.code === 4401) {
          clearReconnectTimer();
          setConnectionState("disconnected");
          return;
        }
        setConnectionState("reconnecting");
        const delay = RECONNECT_DELAYS[
          Math.min(reconnectAttempt, RECONNECT_DELAYS.length - 1)
        ];
        reconnectAttempt += 1;
        reconnectTimer = setTimeout(connect, delay);
      };
      nextSocket.onerror = () => {
        if (nextSocket.readyState !== WebSocket.CLOSED) nextSocket.close();
      };
    };
    connect();

    return () => {
      disposed = true;
      clearReconnectTimer();
      if (socket) {
        socket.onclose = null;
        socket.close();
      }
      socket = null;
    };
  }, [accountId, threadId]);

  return {
    connected: connectionState === "connected",
    connectionState,
  };
}

function isThreadRuntimeEvent(event: DyEvent, threadId: number) {
  if (!THREAD_RUNTIME_EVENT_TYPES.has(event.type)) return false;
  if (typeof event.payload !== "object" || event.payload == null) return false;
  return (event.payload as Record<string, unknown>).thread_id === threadId;
}

function isRuntimeAuthenticationAcknowledgement(event: DyEvent, threadId: number) {
  return event.type === "authenticated" && (event as { thread_id?: unknown }).thread_id === threadId;
}
