// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useEventStream } from "./useEventStream";

class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  static instances: FakeWebSocket[] = [];

  readonly url: string;
  readyState = FakeWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  serverClose() {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED;
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
});
