import { useEffect, useRef, useState } from "react";

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

const RECONNECT_DELAYS = [500, 1_000, 2_000, 4_000, 8_000] as const;
const MAX_SEEN_DURABLE_EVENTS = 2_000;

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
          if (typeof data.id === "number" && Number.isFinite(data.id)) {
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
