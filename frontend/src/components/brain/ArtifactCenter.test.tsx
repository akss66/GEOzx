// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { listArtifacts } from "../../api/brain";
import type { Artifact, ArtifactPage } from "../../types";
import { ArtifactCenter } from "./ArtifactCenter";

vi.mock("../../api/brain", () => ({ listArtifacts: vi.fn() }));

const artifact = (id: number, accountId = 3, createdAt = "2026-07-28T08:00:00Z"): Artifact => ({
  id,
  account_id: accountId,
  thread_id: 81,
  turn_id: 101,
  run_id: 1,
  skill_run_id: 2,
  task_id: 3,
  artifact_type: id === 2 ? "weekly_review" : "account_inspection_report",
  title: "脚本生成中",
  version: 1,
  status: id === 2 ? "accepted" : "ready_for_review",
  summary: "Account result",
  sections: [],
  evidence_refs: [],
  quality: null,
  created_at: createdAt,
});

const page = (data: Artifact[], current = 1, pages = 1): ArtifactPage => ({
  data,
  pagination: { page: current, page_size: 20, total: data.length, pages },
});

function renderCenter(accountId: number | null, onSelect = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    onSelect,
    ...render(
      <QueryClientProvider client={client}>
        <ArtifactCenter accountId={accountId} onSelect={onSelect} />
      </QueryClientProvider>,
    ),
  };
}

describe("ArtifactCenter", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("opens the exact account Artifact and fails closed for an account mismatch", async () => {
    vi.mocked(listArtifacts).mockResolvedValue(page([artifact(1), artifact(99, 4)]));
    const { onSelect } = renderCenter(3);

    expect(await screen.findByRole("button", { name: "查看方案与内容：账号诊断" })).toBeInTheDocument();
    expect(screen.queryByText("脚本生成中")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看方案与内容：账号诊断" }));
    expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ id: 1, account_id: 3 }));
  });

  it("sends date filters before pagination so a matching later-page artifact appears on page one", async () => {
    vi.mocked(listArtifacts).mockImplementation(async (input) => {
      if (input.createdFrom || input.createdTo) {
        return page([{ ...artifact(3, 3, "2026-07-28T23:59:59Z"), status: "accepted" }], 1, 1);
      }
      if (input.page === 2) return page([artifact(3)], 2, 2);
      return page([artifact(1, 3, "2026-07-27T08:00:00Z"), { ...artifact(2), artifact_type: "review_report" }], 1, 2);
    });
    renderCenter(3);
    await screen.findByText("账号诊断");

    const typeSelect = screen.getByLabelText("业务类型");
    expect(Array.from((typeSelect as HTMLSelectElement).options).map((option) => option.text))
      .toEqual(["全部业务", "诊断与复盘", "对标分析", "选题", "拍摄稿", "发布安排"]);
    fireEvent.change(typeSelect, { target: { value: "topics" } });
    expect(screen.queryByText("账号诊断")).not.toBeInTheDocument();
    fireEvent.change(typeSelect, { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("状态"), { target: { value: "accepted" } });
    await waitFor(() => expect(listArtifacts).toHaveBeenLastCalledWith(expect.objectContaining({
      accountId: 3, status: "accepted", page: 1,
    })));
    fireEvent.click(await screen.findByRole("button", { name: "下一页" }));
    await waitFor(() => expect(listArtifacts).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 })));

    fireEvent.change(screen.getByLabelText("创建时间（起）"), { target: { value: "2026-07-28" } });
    fireEvent.change(screen.getByLabelText("创建时间（止）"), { target: { value: "2026-07-28" } });
    await waitFor(() => expect(listArtifacts).toHaveBeenLastCalledWith(expect.objectContaining({
      accountId: 3,
      status: "accepted",
      createdFrom: "2026-07-28",
      createdTo: "2026-07-28",
      page: 1,
    })));
    expect(await screen.findByText("账号诊断")).toBeInTheDocument();
    expect(screen.queryByText("当前账号下没有符合筛选条件的方案与内容。")).not.toBeInTheDocument();
    expect(screen.queryByText("仅筛当前页")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "下一页" })).not.toBeInTheDocument();
  });

  it("shows the server range-validation message instead of a retryable load failure", async () => {
    vi.mocked(listArtifacts).mockImplementation(async (input) => {
      if (input.createdFrom && input.createdTo && input.createdFrom > input.createdTo) {
        throw {
          response: {
            status: 422,
            data: { detail: "created_from must be on or before created_to" },
          },
        };
      }
      return page([]);
    });
    renderCenter(3);
    await screen.findByText("当前账号下没有符合筛选条件的方案与内容。");

    fireEvent.change(screen.getByLabelText("创建时间（起）"), { target: { value: "2026-07-29" } });
    fireEvent.change(screen.getByLabelText("创建时间（止）"), { target: { value: "2026-07-28" } });

    expect(await screen.findByRole("alert")).toHaveTextContent("开始日期不能晚于结束日期");
    expect(screen.queryByText("方案与内容暂时无法加载，请重试。")).not.toBeInTheDocument();
  });

  it("shows a retryable error and clears list selection before another account loads", async () => {
    vi.mocked(listArtifacts)
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce(page([artifact(1)]))
      .mockResolvedValueOnce(page([artifact(4, 4)]));
    const { onSelect, rerender } = renderCenter(3);

    expect(await screen.findByText("方案与内容暂时无法加载，请重试。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新加载方案与内容" }));
    expect(await screen.findByText("账号诊断")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看方案与内容：账号诊断" }));

    rerender(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <ArtifactCenter accountId={4} onSelect={onSelect} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(onSelect).toHaveBeenLastCalledWith(null));
    expect(screen.queryByText("账号诊断")).not.toBeInTheDocument();
    expect(await screen.findByText("账号诊断")).toBeInTheDocument();
  });

  it("starts a new account with clean filters and ignores a late response from the prior account", async () => {
    let resolveAccountA: ((value: ArtifactPage) => void) | undefined;
    vi.mocked(listArtifacts).mockImplementation((input) => {
      if (input.accountId === 3) {
        return new Promise<ArtifactPage>((resolve) => { resolveAccountA = resolve; });
      }
      return Promise.resolve(page([artifact(4, 4)]));
    });
    const onSelect = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { rerender } = render(
      <QueryClientProvider client={client}>
        <ArtifactCenter key="account-3" accountId={3} onSelect={onSelect} />
      </QueryClientProvider>,
    );
    fireEvent.change(screen.getByLabelText("业务类型"), { target: { value: "topics" } });

    rerender(
      <QueryClientProvider client={client}>
        <ArtifactCenter key="account-4" accountId={4} onSelect={onSelect} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(listArtifacts).toHaveBeenCalledWith({
      accountId: 4,
      status: undefined,
      page: 1,
      pageSize: 20,
    }));
    expect(await screen.findByText("账号诊断")).toBeInTheDocument();

    await act(async () => resolveAccountA?.(page([artifact(1)])));
    fireEvent.click(screen.getByRole("button", { name: "查看方案与内容：账号诊断" }));
    expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ id: 4, account_id: 4 }));
    expect(screen.getByLabelText("业务类型")).toHaveValue("");
  });

  it("organizes plans and content into fixed business groups with reliable context", async () => {
    vi.mocked(listArtifacts).mockResolvedValue(page([
      {
        ...artifact(1),
        artifact_type: "account_inspection_report",
        sections: [
          { key: "data_period", title: "数据周期", content: "2026-07-01 至 2026-07-21" },
          { key: "next_step", title: "下一步", content: "确认两个内容支柱后排期" },
        ],
      },
      { ...artifact(2), artifact_type: "positioning_strategy" },
      { ...artifact(3), artifact_type: "topic_plan" },
      { ...artifact(4), artifact_type: "video_script" },
      { ...artifact(5), artifact_type: "publish_calendar" },
    ]));

    renderCenter(3);

    expect(await screen.findByText("下一步：确认两个内容支柱后排期")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "方案与内容" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "诊断与复盘" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "对标分析" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "选题" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "拍摄稿" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "发布安排" })).toBeInTheDocument();
    expect(screen.getAllByText((_, element) => element?.tagName === "SPAN" && element.textContent?.includes("V1") === true)).not.toHaveLength(0);
    expect(screen.getAllByText(/创建于/)).not.toHaveLength(0);
    expect(screen.queryByText(/更新于/)).not.toBeInTheDocument();
    expect(screen.getByText("数据周期：2026-07-01 至 2026-07-21")).toBeInTheDocument();
    expect(screen.getByText("下一步：确认两个内容支柱后排期")).toBeInTheDocument();
    expect(screen.getByLabelText("业务类型")).toBeInTheDocument();
  });

  it("requests every business type before pagination and does not invent a next action", async () => {
    vi.mocked(listArtifacts).mockImplementation(async (input) => (input as { artifactTypes?: string[] }).artifactTypes?.includes("topic_plan")
      ? page([{ ...artifact(3), artifact_type: "topic_plan" }])
      : page([]));
    renderCenter(3);

    fireEvent.change(await screen.findByLabelText("业务类型"), { target: { value: "topics" } });

    await waitFor(() => expect(listArtifacts).toHaveBeenLastCalledWith({
      accountId: 3,
      artifactTypes: ["topic_plan"],
      status: undefined,
      page: 1,
      pageSize: 20,
    }));
    expect(await screen.findByText("选题清单")).toBeInTheDocument();
    expect(screen.getByText("下一步：未提供")).toBeInTheDocument();
  });

  it("keeps unknown artifact types out of the five business groups", async () => {
    vi.mocked(listArtifacts).mockResolvedValue(page([
      { ...artifact(9), artifact_type: "future_business_type" },
    ]));
    renderCenter(3);

    expect(await screen.findByText("有 1 项新类型内容待归类")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "诊断与复盘" })).not.toHaveTextContent("账号运营分析");
  });
});
