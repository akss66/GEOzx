// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WorkspaceSwitcher } from "./WorkspaceSwitcher";

const context = {
  clients: [
    { id: 1, name: "云帆科技", status: "active" as const, created_at: "2026-07-01" },
    { id: 2, name: "山海餐饮", status: "active" as const, created_at: "2026-07-01" },
  ],
  selected_client: null,
  projects: [],
  selected_project: null,
  accounts: [],
};

describe("WorkspaceSwitcher", () => {
  afterEach(cleanup);
  it("asks for confirmation before clearing an active account", () => {
    const onClientChange = vi.fn();
    render(
      <WorkspaceSwitcher
        context={context}
        clientId={1}
        projectId={null}
        accountId={3}
        onClientChange={onClientChange}
        onProjectChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /客户与项目/ }));
    fireEvent.click(screen.getByRole("button", { name: "山海餐饮" }));

    expect(screen.getByText("切换后将清除当前账号上下文")).toBeInTheDocument();
    expect(onClientChange).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "确认切换" }));
    expect(onClientChange).toHaveBeenCalledWith(2);
  });

  it("dismisses with Escape or an outside click and restores trigger focus", async () => {
    render(
      <WorkspaceSwitcher
        context={context}
        clientId={1}
        projectId={null}
        accountId={null}
        onClientChange={vi.fn()}
        onProjectChange={vi.fn()}
      />,
    );

    const trigger = screen.getByRole("button", { name: /客户与项目/ });
    fireEvent.click(trigger);
    expect(screen.getByRole("dialog", { name: "切换客户与项目" })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "切换客户与项目" })).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());

    fireEvent.click(trigger);
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("dialog", { name: "切换客户与项目" })).not.toBeInTheDocument();
  });
});
