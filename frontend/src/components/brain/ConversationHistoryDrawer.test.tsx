// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "antd";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { deleteConversation, listConversations } from "../../api/brain";
import { ConversationHistoryDrawer } from "./ConversationHistoryDrawer";

vi.mock("../../api/brain", () => ({
  deleteConversation: vi.fn(),
  listConversations: vi.fn(),
}));

function renderHistory() {
  const onSelect = vi.fn();
  const onDeleted = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <App>
        <ConversationHistoryDrawer
          accountId={3}
          activeThreadId={21}
          open
          onClose={vi.fn()}
          onSelect={onSelect}
          onDeleted={onDeleted}
        />
      </App>
    </QueryClientProvider>,
  );
  return { onSelect, onDeleted };
}

describe("ConversationHistoryDrawer", () => {
  beforeEach(() => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockImplementation(() => ({
        matches: false,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("opens an owned account conversation and permanently deletes it after confirmation", async () => {
    vi.mocked(listConversations).mockResolvedValue([{
      id: 21,
      account_id: 3,
      title: "账号体检",
      turn_count: 2,
      last_message: "看看这个账号的问题",
      created_at: "2026-07-29T00:00:00Z",
      updated_at: "2026-07-29T00:01:00Z",
    }]);
    vi.mocked(deleteConversation).mockResolvedValue();
    const { onSelect, onDeleted } = renderHistory();

    fireEvent.click(await screen.findByRole("button", { name: "账号体检" }));
    expect(onSelect).toHaveBeenCalledWith(21);

    fireEvent.click(screen.getByRole("button", { name: "删除会话 账号体检" }));
    expect(await screen.findByText("永久删除这条历史会话？")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "永久删除" }));

    await waitFor(() => expect(deleteConversation).toHaveBeenCalledWith(21));
    await waitFor(() => expect(onDeleted).toHaveBeenCalledWith(21));
  });
});
