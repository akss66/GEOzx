import { useEffect, useRef, useState } from "react";

export interface DyEvent {
  id?: number;
  type: string;
  payload?: unknown;
  content_item_id?: number | null;
  project_id?: number | null;
}

/** 订阅后端 /ws/events 实时事件流（经 nginx 代理）。 */
export function useEventStream(onEvent?: (e: DyEvent) => void) {
  const [connected, setConnected] = useState(false);
  const [last, setLast] = useState<DyEvent | null>(null);
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  useEffect(() => {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/ws/events`);
    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data) as DyEvent;
        setLast(data);
        handlerRef.current?.(data);
      } catch {
        // 忽略非 JSON 消息
      }
    };
    return () => ws.close();
  }, []);

  return { connected, last };
}
