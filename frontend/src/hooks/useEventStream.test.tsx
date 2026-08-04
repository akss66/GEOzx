// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  useAccountEventStream,
  useConversationRuntimeStream,
  useEventStream,
} from "./useEventStream";
import { TOKEN_KEY } from "../api/client";

class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  static instances: FakeWebSocket[] = [];

  readonly url: string;
  readyState = FakeWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;
  sent: string[] = [];

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  serverClose(code = 1006) {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({ code } as CloseEvent);
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED;
  }

  send(data: string) {
    this.sent.push(data);
  }

  emit(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent<string>);
  }
}

describe("useEventStream", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
    localStorage.clear();
  });

  it("reconnects after an unexpected close and announces recovery", () => {
    const onReconnect = vi.fn();
    const { result } = renderHook(() => useEventStream(undefined, { onReconnect }));

    expect(result.current.connectionState).toBe("connecting");
    act(() => FakeWebSocket.instances[0].open());
    expect(result.current.connectionState).toBe("connected");
    expect(onReconnect).not.toHaveBeenCalled();

    act(() => FakeWebSocket.instances[0].serverClose());
    expect(result.current.connectionState).toBe("reconnecting");

    act(() => vi.advanceTimersByTime(500));
    expect(FakeWebSocket.instances).toHaveLength(2);
    act(() => FakeWebSocket.instances[1].open());

    expect(result.current.connectionState).toBe("connected");
    expect(onReconnect).toHaveBeenCalledOnce();
  });

  it("keeps delivering events after the socket has reconnected", () => {
    const onEvent = vi.fn();
    renderHook(() => useEventStream(onEvent));
    act(() => FakeWebSocket.instances[0].open());
    act(() => FakeWebSocket.instances[0].serverClose());
    act(() => vi.advanceTimersByTime(500));
    act(() => FakeWebSocket.instances[1].open());
    act(() => FakeWebSocket.instances[1].emit({ type: "brain.runtime.message_done" }));

    expect(onEvent).toHaveBeenCalledWith({ type: "brain.runtime.message_done" });
  });

  it("delivers a persisted event id only once across reconnects", () => {
    const onEvent = vi.fn();
    renderHook(() => useEventStream(onEvent));
    act(() => FakeWebSocket.instances[0].open());
    act(() =>
      FakeWebSocket.instances[0].emit({
        id: 41,
        type: "brain.runtime.message_done",
      }),
    );
    act(() => FakeWebSocket.instances[0].serverClose());
    act(() => vi.advanceTimersByTime(500));
    act(() => FakeWebSocket.instances[1].open());
    act(() =>
      FakeWebSocket.instances[1].emit({
        id: 41,
        type: "brain.runtime.message_done",
      }),
    );

    expect(onEvent).toHaveBeenCalledTimes(1);
  });

  it("does not deduplicate ephemeral token deltas without an event id", () => {
    const onEvent = vi.fn();
    renderHook(() => useEventStream(onEvent));
    act(() => FakeWebSocket.instances[0].open());
    act(() =>
      FakeWebSocket.instances[0].emit({
        type: "brain.runtime.message_delta",
        payload: { delta: "你" },
      }),
    );
    act(() =>
      FakeWebSocket.instances[0].emit({
        type: "brain.runtime.message_delta",
        payload: { delta: "你" },
      }),
    );

    expect(onEvent).toHaveBeenCalledTimes(2);
  });

  it("does not drop start and delta frames when they reuse one durable event id", () => {
    const onEvent = vi.fn();
    renderHook(() => useEventStream(onEvent));
    act(() => FakeWebSocket.instances[0].open());
    act(() =>
      FakeWebSocket.instances[0].emit({
        id: 71,
        type: "brain.runtime.message_start",
        payload: { message_id: "m-1", stream_seq: 0 },
      }),
    );
    act(() =>
      FakeWebSocket.instances[0].emit({
        id: 71,
        type: "brain.runtime.message_delta",
        payload: { message_id: "m-1", delta: "a", stream_seq: 1 },
      }),
    );
    act(() =>
      FakeWebSocket.instances[0].emit({
        id: 71,
        type: "brain.runtime.message_done",
        payload: { message_id: "m-1", content: "ab", stream_seq: 2 },
      }),
    );

    expect(onEvent).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ id: 71, type: "brain.runtime.message_start" }),
    );
    expect(onEvent).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ id: 71, type: "brain.runtime.message_delta" }),
    );
    expect(onEvent).toHaveBeenNthCalledWith(
      3,
      expect.objectContaining({ id: 71, type: "brain.runtime.message_done" }),
    );
  });

  it("authenticates an account event socket without putting the token in its URL", () => {
    localStorage.setItem(TOKEN_KEY, "account-token");
    const onEvent = vi.fn();
    const { result } = renderHook(() => useAccountEventStream({
      accountId: 81,
      onEvent,
    }));

    const socket = FakeWebSocket.instances[0];
    act(() => socket.open());
    expect(socket.url).toContain("/ws/account-events");
    expect(socket.url).not.toContain("account-token");
    expect(JSON.parse(socket.sent[0])).toEqual({
      type: "authenticate",
      token: "account-token",
      account_id: 81,
    });
    expect(result.current.connectionState).toBe("connecting");

    act(() => socket.emit({ type: "authenticated", account_id: 81 }));
    expect(result.current.connectionState).toBe("connected");
    act(() => socket.emit({
      type: "pending_work.updated",
      payload: { account_id: 82 },
    }));
    act(() => socket.emit({
      type: "pending_work.updated",
      payload: { account_id: 81 },
    }));

    expect(onEvent).toHaveBeenCalledOnce();
    expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({
      payload: { account_id: 81 },
    }));
  });

  it("reauthenticates after reconnect and announces recovery after acknowledgement", () => {
    localStorage.setItem(TOKEN_KEY, "account-token");
    const onReconnect = vi.fn();
    renderHook(() => useAccountEventStream({ accountId: 81, onReconnect }));

    const first = FakeWebSocket.instances[0];
    act(() => first.open());
    act(() => first.emit({ type: "authenticated", account_id: 81 }));
    expect(onReconnect).not.toHaveBeenCalled();
    act(() => first.serverClose());
    act(() => vi.advanceTimersByTime(500));

    const second = FakeWebSocket.instances[1];
    act(() => second.open());
    expect(JSON.parse(second.sent[0])).toEqual({
      type: "authenticate",
      token: "account-token",
      account_id: 81,
    });
    expect(onReconnect).not.toHaveBeenCalled();
    act(() => second.emit({ type: "authenticated", account_id: 81 }));

    expect(onReconnect).toHaveBeenCalledOnce();
  });

  it("authenticates a thread-scoped transient socket and ignores another Thread", () => {
    localStorage.setItem(TOKEN_KEY, "runtime-token");
    const onEvent = vi.fn();
    renderHook(() => useConversationRuntimeStream({ accountId: 7, threadId: 12, onEvent }));

    const socket = FakeWebSocket.instances[0];
    act(() => socket.open());
    expect(socket.url).toContain("/ws/conversation-runtime");
    expect(socket.url).not.toContain("runtime-token");
    expect(JSON.parse(socket.sent[0])).toEqual({
      type: "authenticate",
      token: "runtime-token",
      thread_id: 12,
    });
    expect(onEvent).not.toHaveBeenCalled();
    act(() => socket.emit({ type: "authenticated", thread_id: 12 }));
    act(() => socket.emit({
      type: "brain.runtime.message_delta",
      payload: { thread_id: 13, delta: "wrong scope" },
    }));
    act(() => socket.emit({
      type: "brain.runtime.message_delta",
      payload: { thread_id: 12, delta: "current scope" },
    }));

    expect(onEvent).toHaveBeenCalledOnce();
    expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({
      payload: { thread_id: 12, delta: "current scope" },
    }));
  });

  it("waits for the authenticated acknowledgement before reporting connected", () => {
    localStorage.setItem(TOKEN_KEY, "runtime-token");
    const { result } = renderHook(() => useConversationRuntimeStream({
      accountId: 7,
      threadId: 12,
    }));

    act(() => FakeWebSocket.instances[0].open());
    expect(result.current.connectionState).toBe("connecting");
    act(() => FakeWebSocket.instances[0].emit({ type: "authenticated", thread_id: 12 }));
    expect(result.current.connectionState).toBe("connected");
  });

  it("does not retry an unauthorized runtime socket until its scope changes", () => {
    localStorage.setItem(TOKEN_KEY, "runtime-token");
    const { result, rerender } = renderHook(
      ({ accountId, threadId }) => useConversationRuntimeStream({ accountId, threadId }),
      { initialProps: { accountId: 7, threadId: 12 } },
    );

    act(() => FakeWebSocket.instances[0].open());
    act(() => FakeWebSocket.instances[0].serverClose(4401));
    expect(result.current.connectionState).toBe("disconnected");
    act(() => vi.advanceTimersByTime(30_000));
    expect(FakeWebSocket.instances).toHaveLength(1);

    rerender({ accountId: 7, threadId: 13 });
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it("closes the old transient socket when the account or Thread scope changes", () => {
    localStorage.setItem(TOKEN_KEY, "runtime-token");
    const onEvent = vi.fn();
    const { rerender } = renderHook(
      ({ accountId, threadId }) => useConversationRuntimeStream({ accountId, threadId, onEvent }),
      { initialProps: { accountId: 7, threadId: 12 } },
    );
    const oldSocket = FakeWebSocket.instances[0];
    rerender({ accountId: 8, threadId: 13 });

    expect(oldSocket.readyState).toBe(FakeWebSocket.CLOSED);
    act(() => oldSocket.emit({
      type: "brain.runtime.message_delta",
      payload: { thread_id: 12, delta: "late" },
    }));
    expect(onEvent).not.toHaveBeenCalled();
  });
});
