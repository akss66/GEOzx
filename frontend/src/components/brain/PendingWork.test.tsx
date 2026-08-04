// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App as AntApp } from "antd";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  completePendingShootTask,
  getAccountPendingWork,
  publishPendingScheduleEntry,
  type PendingWorkResponse,
} from "../../api/pendingWork";
import { PendingWork } from "./PendingWork";

vi.mock("../../api/pendingWork", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/pendingWork")>();
  return {
    ...actual,
    getAccountPendingWork: vi.fn(),
    completePendingShootTask: vi.fn(),
    publishPendingScheduleEntry: vi.fn(),
  };
});

const source = vi.fn();

function Location() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}

function renderPending(accountId: number | null = 3) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <MemoryRouter initialEntries={["/"]}>
      <QueryClientProvider client={queryClient}>
        <AntApp>
          <PendingWork accountId={accountId} onOpenSource={source} />
          <Location />
        </AntApp>
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return queryClient;
}

function response(groups: PendingWorkResponse["groups"]): PendingWorkResponse {
  return { account_id: 3, groups };
}

function group(
  kind: PendingWorkResponse["groups"][number]["kind"],
  label: string,
  items: PendingWorkResponse["groups"][number]["items"],
) {
  return { kind, label, count: items.length, items };
}

const shoot = {
  id: "shoot_task:21",
  kind: "shoot_task" as const,
  action_label: "查看拍摄要求",
  account_id: 3,
  thread_id: 81,
  turn_id: 501,
  due_at: "2026-08-06T09:00:00Z",
  reason: "拍摄门店改造前后对比",
  next_step_after_completion: "完成后进入剪辑。",
  target: { type: "conversation_turn" as const, thread_id: 81, turn_id: 501 },
};

describe("PendingWork", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getAccountPendingWork).mockResolvedValue(response([]));
  });
  afterEach(cleanup);

  it("renders compact groups and opens the exact source turn", async () => {
    vi.mocked(getAccountPendingWork).mockResolvedValue(response([
      group("shoot_task", "待拍摄", [shoot]),
    ]));

    renderPending();

    expect(await screen.findByRole("heading", { name: "待拍摄" })).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("拍摄门店改造前后对比")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看拍摄要求" }));
    expect(source).toHaveBeenCalledWith({ threadId: 81, turnId: 501 });
  });

  it("navigates data work to the selected account data page", async () => {
    vi.mocked(getAccountPendingWork).mockResolvedValue(response([
      group("account_data", "待补录数据", [{
        id: "account_data:3",
        kind: "account_data",
        action_label: "补录账号数据",
        account_id: 3,
        thread_id: null,
        turn_id: null,
        due_at: null,
        reason: "粉丝画像尚未导入",
        next_step_after_completion: "导入后继续分析。",
        target: { type: "account_data" },
      }]),
    ]));

    renderPending();
    fireEvent.click(await screen.findByRole("button", { name: "补录账号数据" }));

    expect(screen.getByTestId("location")).toHaveTextContent("/accounts/3/data");
  });

  it("removes completed work after refetch and announces the declared next step", async () => {
    vi.mocked(getAccountPendingWork)
      .mockResolvedValueOnce(response([group("shoot_task", "待拍摄", [shoot])]))
      .mockResolvedValue(response([]));
    vi.mocked(completePendingShootTask).mockResolvedValue({
      id: shoot.id,
      kind: "shoot_task",
      account_id: 3,
      completed: true,
      event_id: 91,
      next_step_after_completion: "拍摄任务已完成，可继续进入剪辑或发布准备。",
    });

    renderPending();
    fireEvent.click(await screen.findByRole("button", { name: "标记拍摄完成" }));

    await waitFor(() => expect(completePendingShootTask).toHaveBeenCalledWith(3, 21));
    await waitFor(() => expect(screen.queryByText(shoot.reason)).not.toBeInTheDocument());
    expect(await screen.findByText("拍摄任务已完成，可继续进入剪辑或发布准备。"))
      .toBeInTheDocument();
  });

  it("records a planned schedule as manually published", async () => {
    const schedule = {
      ...shoot,
      id: "schedule_entry:22",
      kind: "manual_publish" as const,
      action_label: "去完成发布",
      reason: "排期内容等待在抖音手动发布。",
    };
    vi.mocked(getAccountPendingWork)
      .mockResolvedValueOnce(response([group("manual_publish", "待手动发布", [schedule])]))
      .mockResolvedValue(response([]));
    vi.mocked(publishPendingScheduleEntry).mockResolvedValue({
      id: schedule.id,
      kind: "manual_publish",
      account_id: 3,
      completed: true,
      event_id: 92,
      next_step_after_completion: "发布记录已保存，可继续监测这条作品的数据。",
    });

    renderPending();
    fireEvent.click(await screen.findByRole("button", { name: "记录已发布" }));

    await waitFor(() => expect(publishPendingScheduleEntry).toHaveBeenCalledWith(3, 22));
    await waitFor(() => expect(screen.queryByText(schedule.reason)).not.toBeInTheDocument());
  });

  it("supports loading, retryable error, empty, disabled, and keyboard states", async () => {
    let reject!: (reason?: unknown) => void;
    vi.mocked(getAccountPendingWork).mockImplementationOnce(() => new Promise((_resolve, rejectFn) => {
      reject = rejectFn;
    }));
    const queryClient = renderPending();
    expect(screen.getByRole("status", { name: "正在读取待处理事项" })).toBeInTheDocument();

    await act(async () => reject(new Error("offline")));
    expect(await screen.findByRole("alert")).toHaveTextContent("待处理事项加载失败");
    vi.mocked(getAccountPendingWork).mockResolvedValue(response([]));
    fireEvent.click(screen.getByRole("button", { name: /重\s*试/ }));
    expect(await screen.findByText("当前没有需要你处理的事项")).toBeInTheDocument();

    queryClient.clear();
    renderPending(null);
    expect(screen.getByText("选择账号后查看待处理事项")).toBeInTheDocument();

    vi.mocked(getAccountPendingWork).mockResolvedValue(response([
      group("shoot_task", "待拍摄", [shoot]),
    ]));
    renderPending();
    const sourceButton = await screen.findByRole("button", { name: "查看拍摄要求" });
    sourceButton.focus();
    fireEvent.keyDown(sourceButton, { key: "Enter" });
    fireEvent.click(sourceButton);
    expect(sourceButton).toHaveFocus();
    expect(source).toHaveBeenCalledWith({ threadId: 81, turnId: 501 });
  });
});
