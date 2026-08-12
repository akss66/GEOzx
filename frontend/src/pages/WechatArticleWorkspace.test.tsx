// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  WechatArticleVersionConflictError,
  createWechatArticleVersion,
  createWechatDraftSync,
  generateAllWechatArticleImages,
  generateWechatArticleImage,
  getWechatArticleDraftSyncContext,
  getWechatArticleImagePrompt,
  getWechatArticlePreview,
  getWechatArticleWorkingCopy,
  saveWechatArticleWorkingCopy,
  selectWechatArticleImageMaterial,
  uploadWechatArticleImage,
} from "../services/wechatArticle";
import WechatArticleWorkspace from "./WechatArticleWorkspace";

const COPY = {
  title: "标题",
  prompt: "画面提示词",
  generateAll: "一键生成全部配图",
  getPrompt: "获取提示词",
  copyPrompt: "复制提示词",
  uploadImage: "上传自己的图片",
  copiedPrompt: "已复制提示词",
  selectedImage: "已选图",
  conflict: "存在新版本",
  viewDiff: "查看差异",
  reloadLatest: "基于新版本继续修改",
  discardLocal: "放弃本地修改",
  syncTrigger: "创建版本并同步到公众号草稿箱",
  syncDialog: "同步确认",
  blockedFact: "仍有事实待核对",
  syncUnavailable: "同步确认上下文暂时不可用。",
  articleUnavailable: "当前文章不可访问",
  imagePlan: "配图计划",
};

const routerMocks = vi.hoisted(() => ({
  articleId: "9",
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useParams: () => ({ articleId: routerMocks.articleId }),
  };
});

vi.mock("../services/wechatArticle", () => ({
  WechatArticleVersionConflictError: class WechatArticleVersionConflictError extends Error {
    currentLockVersion: number;

    constructor(currentLockVersion: number) {
      super("ARTICLE_VERSION_CONFLICT");
      this.name = "WechatArticleVersionConflictError";
      this.currentLockVersion = currentLockVersion;
    }
  },
  getWechatArticleWorkingCopy: vi.fn(),
  saveWechatArticleWorkingCopy: vi.fn(),
  getWechatArticlePreview: vi.fn(),
  listWechatArticleVersions: vi.fn(async () => []),
  getWechatArticleVersionDiff: vi.fn(async () => ({
    baseVersion: 0,
    targetVersion: 0,
    added: [],
    removed: [],
    moved: [],
    changed: [],
    textSemanticChangeRatio: 0,
  })),
  getWechatArticleImagePrompt: vi.fn(),
  generateAllWechatArticleImages: vi.fn(),
  generateWechatArticleImage: vi.fn(),
  uploadWechatArticleImage: vi.fn(),
  selectWechatArticleImageMaterial: vi.fn(),
  createWechatArticleVersion: vi.fn(),
  getWechatArticleDraftSyncContext: vi.fn(),
  createWechatDraftSync: vi.fn(),
  getWechatDraftSync: vi.fn(),
}));

const getWorkingCopyMock = vi.mocked(getWechatArticleWorkingCopy);
const saveWorkingCopyMock = vi.mocked(saveWechatArticleWorkingCopy);
const getPreviewMock = vi.mocked(getWechatArticlePreview);
const getPromptMock = vi.mocked(getWechatArticleImagePrompt);
const generateAllMock = vi.mocked(generateAllWechatArticleImages);
const generateSingleMock = vi.mocked(generateWechatArticleImage);
const uploadMock = vi.mocked(uploadWechatArticleImage);
const selectMock = vi.mocked(selectWechatArticleImageMaterial);
const createVersionMock = vi.mocked(createWechatArticleVersion);
const getSyncContextMock = vi.mocked(getWechatArticleDraftSyncContext);
const createSyncMock = vi.mocked(createWechatDraftSync);

describe("WechatArticleWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
    routerMocks.articleId = "9";
    getWorkingCopyMock.mockResolvedValue(baseWorkingCopy());
    saveWorkingCopyMock.mockImplementation(async (_articleId, input) => ({
      ...baseWorkingCopy(),
      lockVersion: input.expectedLockVersion + 1,
      document: input.document,
    }));
    getPreviewMock.mockResolvedValue({
      articleId: 9,
      document: baseWorkingCopy().document,
      renderedHtml: "<h1>Summer window insulation guide</h1>",
    });
    getPromptMock.mockResolvedValue({ prompt: COPY.prompt });
    generateAllMock.mockResolvedValue({
      requestedSlotIds: [7],
      materialIds: [101],
      failedSlotIds: [],
    });
    generateSingleMock.mockResolvedValue({
      requestedSlotIds: [7],
      materialIds: [101],
      failedSlotIds: [],
    });
    uploadMock.mockResolvedValue({ materialId: 101, status: "ready" });
    selectMock.mockResolvedValue({
      ...baseWorkingCopy().imageSlots[0],
      selectedMaterialId: 101,
      lockVersion: 3,
    });
    createVersionMock.mockResolvedValue({
      id: 12,
      articleId: 9,
      version: 4,
      trigger: "manual",
      document: baseWorkingCopy().document,
    });
    getSyncContextMock.mockResolvedValue(blockedSyncContext());
    createSyncMock.mockResolvedValue({
      id: 88,
      accountId: 31,
      articleId: 9,
      articleVersionId: 12,
      status: "queued",
      conflictStrategy: "fail",
      externalMediaId: null,
      expectedRemoteHash: null,
      observedRemoteHash: null,
      retryable: false,
      errorCode: null,
      createdAt: "2026-08-12T09:10:00Z",
      updatedAt: "2026-08-12T09:10:10Z",
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("hides prompts until requested and can generate all pending images", async () => {
    renderPage();

    expect(screen.queryByText(COPY.prompt)).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: COPY.generateAll }));
    expect(generateAllMock).toHaveBeenCalledWith(9, expect.any(String));
    fireEvent.click(screen.getByRole("button", { name: COPY.getPrompt }));
    expect(await screen.findAllByText(COPY.prompt)).toHaveLength(2);
  });

  it("copies a revealed prompt and announces the clipboard action", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: COPY.getPrompt }));
    fireEvent.click(await screen.findByRole("button", { name: COPY.copyPrompt }));

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(COPY.prompt);
    expect(screen.getByText(COPY.copiedPrompt)).toBeInTheDocument();
  });

  it("uploads a local image, selects it, and exposes retry generation on the same stable slot", async () => {
    renderPage();

    const uploader = (await screen.findByLabelText(COPY.uploadImage)) as HTMLInputElement;
    const file = new File(["png"], "cover.png", { type: "image/png" });
    fireEvent.change(uploader, { target: { files: [file] } });

    await waitFor(() => expect(uploadMock).toHaveBeenCalledWith(9, 7, file));
    expect(selectMock).toHaveBeenCalledWith(9, 7, { materialId: 101, expectedLockVersion: 2 });
    expect(await screen.findByText(COPY.selectedImage)).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: /重新生成/ }));
    expect(generateSingleMock).toHaveBeenCalledWith(9, 7, expect.any(String));
  });

  it("preserves the selected material on refresh when the stable slot returns without a new selection", async () => {
    getWorkingCopyMock
      .mockResolvedValueOnce(baseWorkingCopy())
      .mockResolvedValueOnce(baseWorkingCopy());

    renderPage();

    const uploader = (await screen.findByLabelText(COPY.uploadImage)) as HTMLInputElement;
    const file = new File(["png"], "cover.png", { type: "image/png" });
    fireEvent.change(uploader, { target: { files: [file] } });

    await waitFor(() => expect(screen.getByText(COPY.selectedImage)).toBeVisible());
    fireEvent.click(screen.getByRole("button", { name: COPY.generateAll }));

    await waitFor(() => expect(generateAllMock).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(COPY.selectedImage)).toBeVisible();
  });

  it("debounces autosave by two seconds and surfaces a structured conflict without retrying", async () => {
    renderPage();
    await screen.findByLabelText(COPY.title);

    saveWorkingCopyMock.mockRejectedValueOnce(new WechatArticleVersionConflictError(8));
    vi.useFakeTimers();
    try {
      fireEvent.change(screen.getByLabelText(COPY.title), { target: { value: "Conflict title" } });

      expect(saveWorkingCopyMock).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(1900);
      expect(saveWorkingCopyMock).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(100);
    } finally {
      vi.useRealTimers();
    }

    await waitFor(() => expect(saveWorkingCopyMock).toHaveBeenCalledTimes(1));
    expect(await screen.findAllByText(COPY.conflict)).toHaveLength(3);
    expect(screen.getByRole("button", { name: COPY.viewDiff })).toBeVisible();
    expect(screen.getByRole("button", { name: COPY.reloadLatest })).toBeVisible();
    expect(screen.getByRole("button", { name: COPY.discardLocal })).toBeVisible();
  });

  it("switches to the versions surface when the operator chooses to inspect a conflict diff", async () => {
    getWorkingCopyMock
      .mockResolvedValueOnce(baseWorkingCopy())
      .mockResolvedValue({
        ...baseWorkingCopy(),
        lockVersion: 8,
        document: {
          ...baseWorkingCopy().document,
          title: "Server latest title",
        },
      });

    renderPage();

    await triggerConflict();

    fireEvent.click(screen.getByRole("button", { name: COPY.viewDiff }));

    await waitFor(() => expect(getWorkingCopyMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("本地待保存")).toBeVisible();
    expect(screen.getByText("服务器最新")).toBeVisible();
    expect(screen.getAllByText("Conflict title")).toHaveLength(2);
    expect(screen.getByText("Server latest title")).toBeVisible();
  });

  it("keeps the local document, updates the expected lock, and waits for a fresh edit before saving over a conflict", async () => {
    const latestServerCopy = {
      ...baseWorkingCopy(),
      lockVersion: 8,
      document: {
        ...baseWorkingCopy().document,
        title: "Server latest title",
      },
    };

    getWorkingCopyMock
      .mockResolvedValueOnce(baseWorkingCopy())
      .mockResolvedValue(latestServerCopy);

    renderPage();

    await triggerConflict("Local draft title");
    await waitFor(() => expect(getWorkingCopyMock).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: COPY.reloadLatest }));

    expect(await screen.findByDisplayValue("Local draft title")).toBeVisible();
    expect(screen.queryByText(COPY.conflict)).not.toBeInTheDocument();
    expect(saveWorkingCopyMock).toHaveBeenCalledTimes(1);

    vi.useFakeTimers();
    try {
      fireEvent.change(screen.getByDisplayValue("Local draft title"), {
        target: { value: "Local draft title v2" },
      });
      await vi.advanceTimersByTimeAsync(2000);
    } finally {
      vi.useRealTimers();
    }

    await waitFor(() => expect(saveWorkingCopyMock).toHaveBeenCalledTimes(2));
    expect(saveWorkingCopyMock).toHaveBeenLastCalledWith(9, expect.objectContaining({
      expectedLockVersion: 8,
    }));
  });

  it("refetches the server copy and restores it when the operator discards local edits after a conflict", async () => {
    const latestServerCopy = {
      ...baseWorkingCopy(),
      lockVersion: 8,
      document: {
        ...baseWorkingCopy().document,
        title: "Server latest title",
      },
    };

    getWorkingCopyMock
      .mockResolvedValueOnce(baseWorkingCopy())
      .mockResolvedValue(latestServerCopy);

    renderPage();

    await triggerConflict("Local draft title");
    fireEvent.click(screen.getByRole("button", { name: COPY.discardLocal }));

    await waitFor(() => expect(getWorkingCopyMock).toHaveBeenCalledTimes(3));
    expect(await screen.findByDisplayValue("Server latest title")).toBeVisible();
    expect(screen.queryByText(COPY.conflict)).not.toBeInTheDocument();
  });

  it("keeps newer local text when an older save resolves late", async () => {
    let resolveFirst: ((value: ReturnType<typeof baseWorkingCopy>) => void) | null = null;
    saveWorkingCopyMock
      .mockImplementationOnce(() => new Promise((resolve) => {
        resolveFirst = resolve as (value: ReturnType<typeof baseWorkingCopy>) => void;
      }))
      .mockResolvedValueOnce({
        ...baseWorkingCopy(),
        lockVersion: 9,
        document: { ...baseWorkingCopy().document, title: "Second title" },
      });

    renderPage();
    await screen.findByLabelText(COPY.title);

    vi.useFakeTimers();
    try {
      fireEvent.change(screen.getByLabelText(COPY.title), { target: { value: "First title" } });
      await vi.advanceTimersByTimeAsync(2000);
      fireEvent.change(screen.getByLabelText(COPY.title), { target: { value: "Second title" } });
      await vi.advanceTimersByTimeAsync(2000);
    } finally {
      vi.useRealTimers();
    }

    const firstResolver = resolveFirst;
    expect(firstResolver).not.toBeNull();
    if (firstResolver) {
      (firstResolver as (value: ReturnType<typeof baseWorkingCopy>) => void)({
        ...baseWorkingCopy(),
        lockVersion: 8,
        document: { ...baseWorkingCopy().document, title: "First title" },
      });
    }

    await waitFor(() => expect(saveWorkingCopyMock).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("heading", { name: "Second title" })).toBeVisible();
  });

  it("opens sync confirmation, shows immutable context, and cancel does not sync", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: COPY.syncTrigger }));

    await waitFor(() => expect(createVersionMock).toHaveBeenCalledWith(9));
    await waitFor(() => expect(getSyncContextMock).toHaveBeenCalledWith(9, 12));
    const dialog = await screen.findByRole("dialog", { name: COPY.syncDialog });
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByText("品牌公众号")).toBeInTheDocument();
    expect(within(dialog).getByText(COPY.blockedFact)).toBeInTheDocument();
    expect(createSyncMock).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getAllByRole("button")[0]);
    expect(createSyncMock).not.toHaveBeenCalled();
  });

  it("submits the exact draft-sync payload when readiness allows confirmation", async () => {
    getSyncContextMock.mockResolvedValueOnce({
      targetAccount: { id: 31, name: "品牌公众号" },
      articleTitle: "Summer window insulation guide",
      articleVersionId: 12,
      imageCount: 1,
      readiness: {
        canSync: true,
        blockers: [],
        warnings: [],
        unresolvedClaimCount: 0,
      },
      remote: {
        status: "wechat_synced",
        remoteHash: "hash-1",
        updatedAt: "2026-08-12T09:00:00Z",
        errorCode: null,
        operationType: "draft_sync",
      },
    });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: COPY.syncTrigger }));

    const dialog = await screen.findByRole("dialog", { name: COPY.syncDialog });
    fireEvent.click(within(dialog).getByRole("button", { name: "确认同步到公众号「品牌公众号」草稿箱" }));

    await waitFor(() => expect(createSyncMock).toHaveBeenCalledWith(9, {
      articleVersionId: 12,
      idempotencyKey: expect.any(String),
      expectedRemoteHash: "hash-1",
      conflictStrategy: "fail",
    }));
  });

  it("shows reconciliation details and returns focus to the sync trigger when the dialog closes", async () => {
    getSyncContextMock.mockResolvedValueOnce({
      targetAccount: { id: 31, name: "品牌公众号" },
      articleTitle: "Summer window insulation guide",
      articleVersionId: 12,
      imageCount: 1,
      readiness: {
        canSync: false,
        blockers: [{ code: "REMOTE_RECONCILIATION_REQUIRED", message: "需要人工核对本地与远端差异", claimId: null }],
        warnings: [{ code: "REMOTE_OUTDATED", message: "远端草稿更新时间晚于当前版本", claimId: null }],
        unresolvedClaimCount: 0,
      },
      remote: {
        status: "conflicted",
        remoteHash: "hash-conflict",
        updatedAt: "2026-08-12T09:00:00Z",
        errorCode: "REMOTE_HASH_CONFLICT",
        operationType: "draft_sync",
      },
    });

    const view = renderPage();
    const syncTrigger = await screen.findByRole("button", { name: COPY.syncTrigger });
    syncTrigger.focus();

    fireEvent.click(syncTrigger);

    const dialog = await screen.findByRole("dialog", { name: COPY.syncDialog });
    const dialogButtons = within(dialog).getAllByRole("button");
    const cancelButton = dialogButtons[0];
    const confirmButton = dialogButtons[1];

    expect(cancelButton).toBeEnabled();
    expect(within(dialog).getByText(/REMOTE_HASH_CONFLICT/)).toHaveTextContent("REMOTE_HASH_CONFLICT");
    expect(within(dialog).getByText(/conflicted \/ draft_sync/i)).toHaveTextContent("conflicted / draft_sync");
    expect(confirmButton).toBeDisabled();

    fireEvent.click(cancelButton);

    await waitFor(() => expect(view.container.querySelector(".ant-modal")).toBeNull());
    expect(syncTrigger).toHaveFocus();
  });

  it("shows a concrete recovery error when sync confirmation context is unavailable", async () => {
    getSyncContextMock.mockRejectedValueOnce({ response: { status: 503 } });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: COPY.syncTrigger }));

    expect(await screen.findByText(COPY.syncUnavailable)).toBeVisible();
    expect(createSyncMock).not.toHaveBeenCalled();
  });

  it("renders a scoped error state when the article is unavailable", async () => {
    getWorkingCopyMock.mockRejectedValueOnce({ response: { status: 404 } });

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent(COPY.articleUnavailable);
    expect(screen.queryByText("Summer window insulation guide")).not.toBeInTheDocument();
  });

  it("keeps the workspace readable on narrow screens and exposes live regions", async () => {
    renderPage();

    const page = await screen.findByTestId("wechat-article-workspace");
    expect(page).toHaveAttribute("data-layout", "workspace");
    expect(screen.getByRole("heading", { name: "Summer window insulation guide" })).toBeVisible();
    expect(screen.getByRole("region", { name: COPY.imagePlan })).toBeVisible();
    expect(screen.getByText("", { selector: ".wechat-article-live" })).toHaveAttribute("aria-live", "polite");
  });
});

function renderPage() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <AntApp>
        <WechatArticleWorkspace />
      </AntApp>
    </QueryClientProvider>,
  );
}

async function triggerConflict(nextTitle = "Conflict title") {
  saveWorkingCopyMock.mockRejectedValueOnce(new WechatArticleVersionConflictError(8));

  await screen.findByDisplayValue("Summer window insulation guide");
  vi.useFakeTimers();
  try {
    fireEvent.change(screen.getByDisplayValue("Summer window insulation guide"), {
      target: { value: nextTitle },
    });
    await vi.advanceTimersByTimeAsync(2000);
  } finally {
    vi.useRealTimers();
  }

  await waitFor(() => expect(screen.getAllByText(COPY.conflict)).toHaveLength(3));
}

function blockedSyncContext() {
  return {
    targetAccount: { id: 31, name: "品牌公众号" },
    articleTitle: "Summer window insulation guide",
    articleVersionId: 12,
    imageCount: 1,
    readiness: {
      canSync: false,
      blockers: [{ code: "UNRESOLVED_PRODUCT_CLAIM", message: COPY.blockedFact, claimId: "c-1" }],
      warnings: [],
      unresolvedClaimCount: 1,
    },
    remote: null,
  };
}

function baseWorkingCopy() {
  return {
    articleId: 9,
    accountId: 31,
    accountName: "品牌公众号",
    lockVersion: 7,
    basedOnDeliverableId: 11,
    document: {
      title: "Summer window insulation guide",
      digest: "Practical tips for a cooler home.",
      author: "Editorial team",
      blocks: [
        { type: "paragraph", blockId: "intro", text: "Keep rooms cool with layered insulation." },
      ],
      claims: [
        { claimId: "c-1", blockId: "intro", kind: "product", text: "Layering helps.", citationIds: [] },
      ],
    },
    imageSlots: [
      {
        id: 7,
        stableKey: "cover",
        purpose: "封面图",
        aspectRatio: "2.35:1",
        visualBrief: "暖纸感产品陈列",
        status: "pending",
        selectedMaterialId: null,
        lockVersion: 2,
        hasPrompt: true,
      },
    ],
  };
}
