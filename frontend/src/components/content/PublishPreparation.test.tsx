// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { checkPublishReadiness } from "../../api/orchestrator";
import {
  createPublishJob,
  listPublishJobs,
} from "../../services/publishing";
import { useCurrentWorkspace } from "../../stores/currentWorkspace";
import type {
  AgentToolCall,
  ContentWorkspace,
  PublishPackage,
  PublishReadiness,
} from "../../types";
import type { PublishJob } from "../../types/publishing";
import { PublishPreparation } from "./PublishPreparation";

vi.mock("../../api/orchestrator", () => ({
  checkPublishReadiness: vi.fn(),
}));

vi.mock("../../services/publishing", () => ({
  cancelPublishJob: vi.fn(),
  createPublishJob: vi.fn(),
  listPublishJobs: vi.fn(),
  markPublishJobLaunched: vi.fn(),
  preparePublishHandoff: vi.fn(),
  retryPublishJob: vi.fn(),
}));

vi.mock("./PublishJobPanel", () => ({
  PublishJobPanel: ({ job }: { job: PublishJob }) => (
    <div data-testid="publish-job-panel">发布任务 #{job.id}</div>
  ),
}));

describe("PublishPreparation", () => {
  beforeAll(() => {
    vi.stubGlobal("matchMedia", vi.fn().mockImplementation(() => ({
      matches: false,
      media: "",
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })));
  });

  beforeEach(() => {
    useCurrentWorkspace.setState({
      clientId: 11,
      projectId: 22,
      platform: "douyin",
      accountId: 3,
    });
    vi.mocked(listPublishJobs).mockResolvedValue([]);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("creates a durable publish job after the package passes readiness checks", async () => {
    const readiness = makeReadiness();
    const job = makeJob();
    vi.mocked(checkPublishReadiness).mockResolvedValue(readiness);
    vi.mocked(createPublishJob).mockResolvedValue(job);

    renderPreparation();

    fireEvent.change(screen.getByLabelText("正文"), {
      target: { value: "这是一条可发布的内容。" },
    });
    fireEvent.click(await screen.findByRole("checkbox", { name: "视频 #9" }));
    fireEvent.click(screen.getByRole("button", { name: "检查并生成发布包" }));

    await waitFor(() => {
      expect(createPublishJob).toHaveBeenCalledWith({
        account_id: 3,
        active_client_id: 11,
        active_project_id: 22,
        tool_call_id: 41,
        idempotency_key: "content:7:tool:41",
        publish_package: readiness.package,
      });
    });
    expect(await screen.findByTestId("publish-job-panel")).toHaveTextContent("发布任务 #61");
  });

  it("restores the latest matching publish job without creating a duplicate", async () => {
    vi.mocked(listPublishJobs).mockResolvedValue([
      makeJob({ id: 72, tool_call_id: 40, updated_at: "2026-07-27T08:00:00Z" }),
      makeJob({ id: 73, tool_call_id: 41, updated_at: "2026-07-27T09:00:00Z" }),
    ]);

    renderPreparation();

    expect(await screen.findByTestId("publish-job-panel")).toHaveTextContent("发布任务 #73");
    expect(createPublishJob).not.toHaveBeenCalled();
  });

  it("does not call legacy publish APIs for a WeChat account", () => {
    const workspace = makeWorkspace();
    workspace.account = {
      ...workspace.account!,
      nickname: "品牌公众号",
      platform: "wechat_official_account",
    };

    renderPreparation(workspace);

    expect(screen.getByText("微信公众号暂不支持旧版发布准备")).toBeInTheDocument();
    expect(checkPublishReadiness).not.toHaveBeenCalled();
    expect(listPublishJobs).not.toHaveBeenCalled();
  });
});

function renderPreparation(workspace = makeWorkspace()) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <PublishPreparation workspace={workspace} canOperate />
      </AntApp>
    </QueryClientProvider>,
  );
}

function makeWorkspace(): ContentWorkspace {
  return {
    content_item: {
      id: 7,
      project_id: 22,
      account_id: 3,
      title: "新品发布",
      current_stage: "operation",
      status: "in_progress",
      created_at: "2026-07-27T00:00:00Z",
    },
    project_name: "增长项目",
    account: {
      id: 3,
      nickname: "抖音测试账号",
      platform: "douyin",
      auth_status: "authorized",
    },
    tasks: [],
    deliverables: [],
    gates: [],
    compliance: [],
    materials: [{
      id: 9,
      content_item_id: 7,
      deliverable_id: null,
      kind: "video",
      provider: "manual",
      status: "ready",
      size_bytes: 1024,
      file_url: "/files/video.mp4",
      error: null,
      created_at: "2026-07-27T00:00:00Z",
    }],
    publish_tool_calls: [makeToolCall()],
  };
}

function makeToolCall(): AgentToolCall {
  return {
    id: 41,
    org_id: 1,
    task_id: 8,
    invocation_id: 12,
    module: "publishing",
    agent_code: "06-operation",
    tool_code: "publish_package_prepare",
    tool_name: "发布包准备",
    status: "waiting_approval",
    permission_mode: "confirm",
    requires_human_confirmation: true,
    input_summary: "准备抖音发布包",
    output_summary: "发布包已准备",
    error: null,
    latency_ms: 100,
    cost: 0,
    meta: {},
    started_at: "2026-07-27T00:00:00Z",
    finished_at: "2026-07-27T00:00:01Z",
    created_at: "2026-07-27T00:00:00Z",
    updated_at: "2026-07-27T00:00:01Z",
  };
}

function makePackage(): PublishPackage {
  return {
    platform: "douyin",
    account_id: 3,
    content_type: "video",
    title: "新品发布",
    body: "这是一条可发布的内容。",
    topics: [],
    scheduled_at: null,
    material_ids: [9],
    cover_material_id: null,
    visibility: "public",
    allow_comment: true,
    execution_mode: "official_api",
    manual_steps: [],
  };
}

function makeReadiness(): PublishReadiness {
  return {
    content_item_id: 7,
    platform: "douyin",
    ready: true,
    risk: "pass",
    package: makePackage(),
    findings: [{ level: "pass", code: "material.ok", message: "素材可以用于发布。" }],
    tool_call: makeToolCall(),
  };
}

function makeJob(overrides: Partial<PublishJob> = {}): PublishJob {
  return {
    id: 61,
    org_id: 1,
    account_id: 3,
    active_client_id: 11,
    active_project_id: 22,
    created_by_id: 1,
    brain_task_id: 8,
    tool_call_id: 41,
    platform_content_record_id: null,
    platform: "douyin",
    status: "pending_approval",
    idempotency_key: "content:7:tool:41",
    publish_package: makePackage(),
    capabilities_snapshot: {},
    approval_snapshot: {},
    share_id: null,
    posting_task_id: null,
    external_video_id: null,
    external_item_id: null,
    expires_at: null,
    handoff_started_at: null,
    bound_at: null,
    retry_count: 0,
    next_retry_at: null,
    last_error_code: null,
    last_error_message: null,
    last_platform_log_id: null,
    created_at: "2026-07-27T00:00:00Z",
    updated_at: "2026-07-27T00:00:00Z",
    ...overrides,
  };
}
