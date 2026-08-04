// @vitest-environment jsdom

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useConversationTurnEvents } from "./useConversationTurnEvents";
import { listConversationEvents } from "../api/brain";
import { TOKEN_KEY } from "../api/client";

vi.mock("../api/brain", () => ({
  listConversationEvents: vi.fn(),
}));

const event = (id: number, sequence = id, turnId = 1) => ({
  id,
  sequence,
  type: "brain.runtime.message_delta",
  payload: { delta: `event-${id}` },
  thread_id: 12,
  turn_id: turnId,
  run_id: 3,
  skill_run_id: null,
  created_at: "2026-08-04T00:00:00Z",
});

function streamResponse(...chunks: string[]) {
  const encoder = new TextEncoder();
  return new Response(new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  }), { status: 200, headers: { "content-type": "text/event-stream" } });
}

function pendingStreamResponse() {
  const encoder = new TextEncoder();
  let streamController: ReadableStreamDefaultController<Uint8Array>;
  return {
    response: new Response(new ReadableStream({
      start(controller) {
        streamController = controller;
      },
    }), { status: 200, headers: { "content-type": "text/event-stream" } }),
    close() {
      streamController.close();
    },
    emit(chunk: string) {
      streamController.enqueue(encoder.encode(chunk));
    },
  };
}

describe("useConversationTurnEvents", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    localStorage.setItem(TOKEN_KEY, "bearer-token");
    vi.mocked(listConversationEvents).mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.useRealTimers();
    localStorage.clear();
  });

  it("recovers ascending HTTP events before opening an authenticated stream cursor", async () => {
    vi.useRealTimers();
    const onEvent = vi.fn();
    vi.mocked(listConversationEvents).mockResolvedValueOnce([event(2), event(1)]);
    const fetchMock = vi.fn().mockResolvedValue(
      streamResponse(`id: 3\nevent: brain.runtime.message_delta\ndata: ${JSON.stringify(event(3))}\n\n`),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useConversationTurnEvents({
      accountId: 7,
      threadId: 12,
      onEvent,
    }));

    await waitFor(() => expect(onEvent).toHaveBeenCalledTimes(3));

    expect(onEvent).toHaveBeenNthCalledWith(1, event(1));
    expect(onEvent).toHaveBeenNthCalledWith(2, event(2));
    expect(onEvent).toHaveBeenNthCalledWith(3, event(3));
    expect(listConversationEvents).toHaveBeenCalledWith(12, 0, expect.any(AbortSignal));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/conversation-threads/12/event-stream?after_id=2",
      expect.objectContaining({
        credentials: "omit",
        headers: { Accept: "text/event-stream", Authorization: "Bearer bearer-token" },
      }),
    );
    expect(result.current.lastEventId).toBe(3);
  });

  it("deduplicates overlapping recovery and parses CRLF, multiline, and EOF frames", async () => {
    vi.useRealTimers();
    const onEvent = vi.fn();
    vi.mocked(listConversationEvents).mockResolvedValueOnce([event(1)]);
    const multiline = JSON.stringify(event(2)).replace(",\"payload\":", ",\n\"payload\":");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamResponse(
      `: heartbeat\r\nid: 1\r\nevent: brain.runtime.message_delta\r\ndata: ${JSON.stringify(event(1))}\r\n\r\n`,
      `id: 2\r\nevent: brain.runtime.message_delta\r\ndata: ${multiline.split("\n").join("\r\ndata: ")}`,
    )));

    renderHook(() => useConversationTurnEvents({ accountId: 7, threadId: 12, onEvent }));

    await waitFor(() => expect(onEvent).toHaveBeenCalledTimes(2));
    expect(onEvent).toHaveBeenNthCalledWith(1, event(1));
    expect(onEvent).toHaveBeenNthCalledWith(2, event(2));
  });

  it("ignores a malformed stream event from another conversation thread", async () => {
    vi.useRealTimers();
    const onEvent = vi.fn();
    vi.mocked(listConversationEvents).mockResolvedValueOnce([]);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamResponse(
      `id: 1\ndata: ${JSON.stringify({ ...event(1), thread_id: 99 })}\n\n`,
      `id: 2\ndata: ${JSON.stringify(event(2))}\n\n`,
    )));

    renderHook(() => useConversationTurnEvents({ accountId: 7, threadId: 12, onEvent }));

    await waitFor(() => expect(onEvent).toHaveBeenCalledTimes(1));
    expect(onEvent).toHaveBeenCalledWith(event(2));
  });

  it("uses the latest durable ID for HTTP recovery before reopening a closed stream", async () => {
    vi.useRealTimers();
    const firstStream = pendingStreamResponse();
    const secondStream = pendingStreamResponse();
    vi.mocked(listConversationEvents)
      .mockResolvedValueOnce([event(10), event(11)])
      .mockResolvedValueOnce([]);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(firstStream.response)
      .mockResolvedValueOnce(secondStream.response);
    vi.stubGlobal("fetch", fetchMock);

    renderHook(() => useConversationTurnEvents({ accountId: 7, threadId: 12 }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    vi.useFakeTimers();
    act(() => firstStream.close());
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });

    expect(listConversationEvents).toHaveBeenNthCalledWith(2, 12, 11, expect.any(AbortSignal));
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/conversation-threads/12/event-stream?after_id=11",
      expect.any(Object),
    );
  });

  it("backs off repeated short EOF streams from 500ms through the 8s cap", async () => {
    vi.useRealTimers();
    const streams = Array.from({ length: 6 }, () => pendingStreamResponse());
    vi.mocked(listConversationEvents).mockResolvedValue([]);
    const fetchMock = vi.fn();
    for (const stream of streams) fetchMock.mockResolvedValueOnce(stream.response);
    vi.stubGlobal("fetch", fetchMock);
    renderHook(() => useConversationTurnEvents({ accountId: 7, threadId: 12 }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    vi.useFakeTimers();

    for (const [index, delay] of [500, 1_000, 2_000, 4_000, 8_000].entries()) {
      act(() => streams[index].close());
      await act(async () => { await vi.advanceTimersByTimeAsync(delay - 1); });
      expect(fetchMock).toHaveBeenCalledTimes(index + 1);
      await act(async () => { await vi.advanceTimersByTimeAsync(1); });
      expect(fetchMock).toHaveBeenCalledTimes(index + 2);
    }
  });

  it("aborts a connected stream and starts HTTP recovery immediately on online and visible", async () => {
    vi.useRealTimers();
    const streams = [pendingStreamResponse(), pendingStreamResponse(), pendingStreamResponse()];
    const signals: AbortSignal[] = [];
    vi.mocked(listConversationEvents).mockImplementation((_threadId, _afterId, signal) => {
      signals.push(signal!);
      return Promise.resolve([]);
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(streams[0].response)
      .mockResolvedValueOnce(streams[1].response)
      .mockResolvedValueOnce(streams[2].response);
    vi.stubGlobal("fetch", fetchMock);
    renderHook(() => useConversationTurnEvents({ accountId: 7, threadId: 12 }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    act(() => window.dispatchEvent(new Event("online")));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(signals[0].aborted).toBe(true);

    act(() => document.dispatchEvent(new Event("visibilitychange")));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(signals[1].aborted).toBe(true);
  });

  it("aborts the old recovery and resets its cursor when the conversation scope changes", async () => {
    vi.useRealTimers();
    const signals: AbortSignal[] = [];
    vi.mocked(listConversationEvents).mockImplementation((_threadId, _afterId, signal) => {
      signals.push(signal!);
      return new Promise(() => undefined);
    });
    vi.stubGlobal("fetch", vi.fn());

    const { rerender } = renderHook(
      ({ accountId, threadId }) => useConversationTurnEvents({ accountId, threadId }),
      { initialProps: { accountId: 7, threadId: 12 } },
    );
    await waitFor(() => expect(signals).toHaveLength(1));
    rerender({ accountId: 8, threadId: 13 });
    await waitFor(() => expect(signals).toHaveLength(2));

    expect(signals[0].aborted).toBe(true);
    expect(listConversationEvents).toHaveBeenNthCalledWith(2, 13, 0, signals[1]);
  });

  it("does not project a stale recovery response after the conversation scope changes", async () => {
    vi.useRealTimers();
    let resolveFirst: ((events: ReturnType<typeof event>[]) => void) | undefined;
    vi.mocked(listConversationEvents)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockImplementation(() => new Promise(() => undefined));
    const onEvent = vi.fn();
    const { rerender } = renderHook(
      ({ threadId }) => useConversationTurnEvents({ accountId: 7, threadId, onEvent }),
      { initialProps: { threadId: 12 } },
    );
    await waitFor(() => expect(listConversationEvents).toHaveBeenCalledTimes(1));
    rerender({ threadId: 13 });
    await waitFor(() => expect(listConversationEvents).toHaveBeenCalledTimes(2));
    act(() => resolveFirst?.([event(1)]));
    await act(async () => { await Promise.resolve(); });

    expect(onEvent).not.toHaveBeenCalled();
  });

  it("pages exact 500-event recoveries before opening the stream", async () => {
    vi.useRealTimers();
    const firstPage = Array.from({ length: 500 }, (_item, index) => event(index + 1));
    const stream = pendingStreamResponse();
    vi.mocked(listConversationEvents)
      .mockResolvedValueOnce(firstPage)
      .mockResolvedValueOnce([event(501)]);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(stream.response));

    renderHook(() => useConversationTurnEvents({ accountId: 7, threadId: 12 }));

    await waitFor(() => expect(listConversationEvents).toHaveBeenCalledTimes(2));
    expect(listConversationEvents).toHaveBeenNthCalledWith(2, 12, 500, expect.any(AbortSignal));
  });

  it("reports a sequence gap only within its own turn", async () => {
    vi.useRealTimers();
    const onRecover = vi.fn();
    vi.mocked(listConversationEvents).mockResolvedValueOnce([
      event(1, 1, 10),
      event(2, 1, 11),
      event(3, 3, 10),
    ]);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(pendingStreamResponse().response));

    renderHook(() => useConversationTurnEvents({ accountId: 7, threadId: 12, onRecover }));

    await waitFor(() => expect(onRecover).toHaveBeenCalledTimes(1));
    expect(onRecover).toHaveBeenCalledWith({
      threadId: 12,
      turnId: 10,
      previousSequence: 1,
      receivedSequence: 3,
      previousEventId: 1,
      receivedEventId: 3,
    });
  });

  it("reports a first observed turn sequence above one as a missing initial range", async () => {
    vi.useRealTimers();
    const onRecover = vi.fn();
    vi.mocked(listConversationEvents).mockResolvedValueOnce([
      event(1, 3, 10),
      event(2, 1, 11),
    ]);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(pendingStreamResponse().response));

    renderHook(() => useConversationTurnEvents({ accountId: 7, threadId: 12, onRecover }));

    await waitFor(() => expect(onRecover).toHaveBeenCalledTimes(1));
    expect(onRecover).toHaveBeenCalledWith({
      threadId: 12,
      turnId: 10,
      previousSequence: 0,
      receivedSequence: 3,
      previousEventId: 0,
      receivedEventId: 1,
    });
  });

  it("drops an SSE frame when its event field disagrees with the durable payload type", async () => {
    vi.useRealTimers();
    const onEvent = vi.fn();
    vi.mocked(listConversationEvents).mockResolvedValueOnce([]);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamResponse(
      `id: 1\nevent: brain.runtime.message_done\ndata: ${JSON.stringify(event(1))}\n\n`,
    )));

    renderHook(() => useConversationTurnEvents({ accountId: 7, threadId: 12, onEvent }));

    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(onEvent).not.toHaveBeenCalled();
  });

  it("ignores an aborted Axios recovery from an older connection attempt", async () => {
    vi.useRealTimers();
    let rejectOldRecovery: ((reason?: unknown) => void) | undefined;
    const currentStream = pendingStreamResponse();
    vi.mocked(listConversationEvents)
      .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectOldRecovery = reject; }))
      .mockResolvedValueOnce([]);
    const fetchMock = vi.fn().mockResolvedValue(currentStream.response);
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useConversationTurnEvents({ accountId: 7, threadId: 12 }));
    await waitFor(() => expect(listConversationEvents).toHaveBeenCalledTimes(1));

    act(() => window.dispatchEvent(new Event("online")));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    rejectOldRecovery?.({ name: "CanceledError", code: "ERR_CANCELED" });
    await act(async () => { await Promise.resolve(); });
    vi.useFakeTimers();
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });

    expect(listConversationEvents).toHaveBeenCalledTimes(2);
    expect(result.current.connectionState).toBe("connected");
  });

  it("does not let a late aborted fetch response claim the active stream reader", async () => {
    vi.useRealTimers();
    let resolveOldFetch: ((response: Response) => void) | undefined;
    const oldStream = pendingStreamResponse();
    const currentStream = pendingStreamResponse();
    vi.mocked(listConversationEvents).mockResolvedValue([]);
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOldFetch = resolve; }))
      .mockResolvedValueOnce(currentStream.response);
    vi.stubGlobal("fetch", fetchMock);
    renderHook(() => useConversationTurnEvents({ accountId: 7, threadId: 12 }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    act(() => window.dispatchEvent(new Event("online")));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    act(() => resolveOldFetch?.(oldStream.response));
    await act(async () => { await Promise.resolve(); });

    expect(oldStream.response.body?.locked).toBe(false);
  });

  it("does not start a request without a complete conversation scope or token", async () => {
    vi.useRealTimers();
    localStorage.removeItem(TOKEN_KEY);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderHook(() => useConversationTurnEvents({ accountId: null, threadId: 12 }));
    renderHook(() => useConversationTurnEvents({ accountId: 7, threadId: null }));
    renderHook(() => useConversationTurnEvents({ accountId: 7, threadId: 12 }));

    await act(async () => { await Promise.resolve(); });
    expect(listConversationEvents).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("marks ordinary authentication failure unauthorized without scheduling a retry", async () => {
    vi.useRealTimers();
    vi.mocked(listConversationEvents).mockRejectedValueOnce({ response: { status: 401 } });
    vi.stubGlobal("fetch", vi.fn());
    const { result } = renderHook(() => useConversationTurnEvents({ accountId: 7, threadId: 12 }));

    await waitFor(() => expect(result.current.connectionState).toBe("unauthorized"));
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });
});
