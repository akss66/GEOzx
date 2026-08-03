// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { listNotifications } from "../../api/shell";
import { NotificationCenter } from "./NotificationCenter";

vi.mock("../../api/shell", () => ({
  getUnreadNotificationCount: vi.fn(async () => 0),
  listNotifications: vi.fn(async () => []),
  markNotificationRead: vi.fn(),
}));

describe("NotificationCenter", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows a retry state instead of claiming there are no notifications", async () => {
    vi.mocked(listNotifications).mockRejectedValueOnce({ response: { status: 503 } });

    renderCenter();
    fireEvent.click(screen.getByRole("button", { name: "通知" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("通知加载失败");
    expect(screen.queryByText("没有新通知")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(await screen.findByText("没有新通知")).toBeInTheDocument();
  });

  it("dismisses with Escape or an outside click and restores trigger focus", async () => {
    renderCenter();

    const trigger = screen.getByRole("button", { name: "通知" });
    fireEvent.click(trigger);
    expect(screen.getByLabelText("通知中心")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByLabelText("通知中心")).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());

    fireEvent.click(trigger);
    fireEvent.mouseDown(document.body);
    expect(screen.queryByLabelText("通知中心")).not.toBeInTheDocument();
  });
});

function renderCenter() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <NotificationCenter />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}
