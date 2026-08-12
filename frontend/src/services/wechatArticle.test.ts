import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { api } from "../api/client";
import {
  WechatArticleVersionConflictError,
  createWechatDraftSync,
  generateAllWechatArticleImages,
  getWechatArticleDraftSyncContext,
  getWechatArticleWorkingCopy,
  saveWechatArticleWorkingCopy,
  selectWechatArticleImageMaterial,
  uploadWechatArticleImage,
} from "./wechatArticle";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), patch: vi.fn(), post: vi.fn(), put: vi.fn() },
}));

const get = api.get as unknown as Mock;
const patch = api.patch as unknown as Mock;
const post = api.post as unknown as Mock;
const put = api.put as unknown as Mock;

describe("wechatArticle service", () => {
  beforeEach(() => vi.resetAllMocks());

  it("parses the working copy through an allowlist and keeps account projection", async () => {
    get.mockResolvedValueOnce({
      data: {
        articleId: 9,
        document: {
          title: "Summer window insulation guide",
          digest: "Practical tips for a cooler home.",
          author: "Editorial team",
          blocks: [{ type: "paragraph", block_id: "intro", text: "Keep rooms cool." }],
          claims: [],
        },
        lockVersion: 4,
        basedOnDeliverableId: 12,
        accountId: 31,
        accountName: "品牌公众号",
        imageSlots: [
          {
            id: 7,
            stableKey: "cover",
            purpose: "封面图",
            aspectRatio: "2.35:1",
            visualBrief: "暖纸感产品陈列",
            status: "selected",
            selectedMaterialId: 101,
            lockVersion: 2,
            hasPrompt: true,
            prompt_internal: "must-not-leak",
          },
        ],
        raw: "must-not-leak",
      },
    });

    const result = await getWechatArticleWorkingCopy(9);

    expect(get).toHaveBeenCalledWith("/wechat-articles/9/working-copy");
    expect(result).toEqual({
      articleId: 9,
      accountId: 31,
      accountName: "品牌公众号",
      lockVersion: 4,
      basedOnDeliverableId: 12,
      document: {
        title: "Summer window insulation guide",
        digest: "Practical tips for a cooler home.",
        author: "Editorial team",
        blocks: [{ type: "paragraph", blockId: "intro", text: "Keep rooms cool." }],
        claims: [],
      },
      imageSlots: [
        {
          id: 7,
          stableKey: "cover",
          purpose: "封面图",
          aspectRatio: "2.35:1",
          visualBrief: "暖纸感产品陈列",
          status: "selected",
          selectedMaterialId: 101,
          lockVersion: 2,
          hasPrompt: true,
        },
      ],
    });
    expect(JSON.stringify(result)).not.toContain("prompt_internal");
    expect(JSON.stringify(result)).not.toContain("raw");
  });

  it("raises a structured version conflict instead of a generic overwrite retry", async () => {
    patch.mockRejectedValueOnce({
      response: {
        status: 409,
        data: {
          error: {
            code: "ARTICLE_VERSION_CONFLICT",
            details: { currentLockVersion: 8 },
          },
        },
      },
    });

    let thrown: unknown;
    try {
      await saveWechatArticleWorkingCopy(9, {
        expectedLockVersion: 7,
        document: {
          title: "T",
          digest: "D",
          author: "A",
          blocks: [{ type: "paragraph", blockId: "intro", text: "changed" }],
          claims: [],
        },
      });
    } catch (error) {
      thrown = error;
    }

    expect(thrown).toBeInstanceOf(WechatArticleVersionConflictError);
    expect(thrown).toMatchObject({ currentLockVersion: 8 });
    expect(patch).toHaveBeenCalledWith("/wechat-articles/9/working-copy", {
      expected_lock_version: 7,
      document: {
        title: "T",
        digest: "D",
        author: "A",
        blocks: [{ type: "paragraph", block_id: "intro", text: "changed" }],
        claims: [],
      },
    });
  });

  it("loads the draft sync confirmation context through a strict allowlist", async () => {
    get.mockResolvedValueOnce({
      data: {
        targetAccount: { id: 31, name: "品牌公众号" },
        articleTitle: "Summer window insulation guide",
        articleVersionId: 12,
        imageCount: 3,
        readiness: {
          canSync: false,
          blockers: [{ code: "UNRESOLVED_PRODUCT_CLAIM", message: "need source", claimId: "c-1" }],
          warnings: [{ code: "QUALITY_REVIEW_UNAVAILABLE", message: "review offline", claimId: null }],
          unresolvedClaimCount: 1,
          citationCount: 99,
        },
        remote: {
          status: "wechat_synced",
          remoteHash: "known-hash",
          updatedAt: "2026-08-12T09:00:00Z",
          errorCode: null,
          operationType: "draft_sync",
          publish_package: "must-not-leak",
        },
      },
    });

    const result = await getWechatArticleDraftSyncContext(9, 12);

    expect(get).toHaveBeenCalledWith("/wechat-articles/9/draft-sync-context", {
      params: { article_version_id: 12 },
    });
    expect(result).toEqual({
      targetAccount: { id: 31, name: "品牌公众号" },
      articleTitle: "Summer window insulation guide",
      articleVersionId: 12,
      imageCount: 3,
      readiness: {
        canSync: false,
        blockers: [{ code: "UNRESOLVED_PRODUCT_CLAIM", message: "need source", claimId: "c-1" }],
        warnings: [{ code: "QUALITY_REVIEW_UNAVAILABLE", message: "review offline", claimId: null }],
        unresolvedClaimCount: 1,
      },
      remote: {
        status: "wechat_synced",
        remoteHash: "known-hash",
        updatedAt: "2026-08-12T09:00:00Z",
        errorCode: null,
        operationType: "draft_sync",
      },
    });
    expect(JSON.stringify(result)).not.toContain("publish_package");
    expect(JSON.stringify(result)).not.toContain("citationCount");
  });

  it("sends fresh idempotency keys for generate-all and keeps selection lock version explicit", async () => {
    post.mockResolvedValueOnce({
      data: { requestedSlotIds: [7, 8], materialIds: [101, 102], failedSlotIds: [] },
    });
    put.mockResolvedValueOnce({
      data: {
        id: 7,
        stableKey: "cover",
        purpose: "封面图",
        aspectRatio: "2.35:1",
        visualBrief: "暖纸感产品陈列",
        status: "selected",
        selectedMaterialId: 101,
        lockVersion: 3,
        hasPrompt: true,
      },
    });

    const generation = await generateAllWechatArticleImages(9, "wechat-image-all-1");
    const selection = await selectWechatArticleImageMaterial(9, 7, {
      materialId: 101,
      expectedLockVersion: 2,
    });

    expect(post).toHaveBeenCalledWith("/wechat-articles/9/image-generations", {
      idempotency_key: "wechat-image-all-1",
      reference_material_ids: [],
    });
    expect(put).toHaveBeenCalledWith("/wechat-articles/9/image-slots/7/selection", {
      material_id: 101,
      expected_lock_version: 2,
    });
    expect(generation.materialIds).toEqual([101, 102]);
    expect(selection.selectedMaterialId).toBe(101);
    expect(selection.lockVersion).toBe(3);
  });

  it("uploads a local image as multipart form data and returns the safe material id", async () => {
    post.mockResolvedValueOnce({ data: { materialId: 555, status: "ready", secret: "drop" } });

    const file = new File(["png-data"], "cover.png", { type: "image/png" });
    const result = await uploadWechatArticleImage(9, 7, file);

    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][0]).toBe("/wechat-articles/9/image-slots/7/uploads");
    const formData = post.mock.calls[0][1] as FormData;
    expect(formData.get("file")).toBe(file);
    expect(result).toEqual({ materialId: 555, status: "ready" });
    expect(JSON.stringify(result)).not.toContain("secret");
  });

  it("creates a draft sync with the exact Task 13 payload shape", async () => {
    post.mockResolvedValueOnce({
      data: {
        id: 88,
        account_id: 31,
        article_id: 9,
        article_version_id: 12,
        status: "wechat_synced",
        conflict_strategy: "fail",
        external_media_id: "remote-7",
        expected_remote_hash: "hash-1",
        observed_remote_hash: "hash-1",
        retryable: false,
        error_code: null,
        created_at: "2026-08-12T09:10:00Z",
        updated_at: "2026-08-12T09:10:10Z",
        publish_package: "drop",
      },
    });

    const result = await createWechatDraftSync(9, {
      articleVersionId: 12,
      idempotencyKey: "wechat-sync-9-v12",
      expectedRemoteHash: "hash-1",
      conflictStrategy: "fail",
    });

    expect(post).toHaveBeenCalledWith("/wechat-articles/9/draft-syncs", {
      article_version_id: 12,
      idempotency_key: "wechat-sync-9-v12",
      expected_remote_hash: "hash-1",
      conflict_strategy: "fail",
    });
    expect(result.status).toBe("wechat_synced");
    expect(JSON.stringify(result)).not.toContain("publish_package");
  });
});
