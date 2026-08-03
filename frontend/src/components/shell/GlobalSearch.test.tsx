// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { GlobalSearch } from "./GlobalSearch";

vi.mock("../../api/shell", () => ({
  searchWorkspace: vi.fn(async () => []),
}));

describe("GlobalSearch", () => {
  it("closes with Escape and restores focus to the search trigger", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <GlobalSearch />
        </QueryClientProvider>
      </MemoryRouter>,
    );

    const trigger = screen.getByRole("button", { name: "全局搜索" });
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "全局搜索" });
    expect(dialog).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveFocus());

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "全局搜索" })).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
  });
});
