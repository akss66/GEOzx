// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { listRiskQueue } from "../api/risks";
import Risks from "./Risks";

vi.mock("../api/risks", () => ({ listRiskQueue: vi.fn(async () => []) }));

describe("Risks", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("does not present a failed risk query as an empty queue", async () => {
    vi.mocked(listRiskQueue).mockRejectedValueOnce({ response: { status: 503 } });

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("风险队列加载失败");
    expect(screen.queryByText("当前筛选下暂无风险")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(await screen.findByText("当前筛选下暂无风险")).toBeInTheDocument();
  });
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Risks />
    </QueryClientProvider>,
  );
}
