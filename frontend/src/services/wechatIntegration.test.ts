import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { api } from "../api/client";
import {
  bindWechatKnowledgeBase,
  createWechatAuthorizationSession,
  getWechatAccountCapabilities,
  getWechatKnowledgeBinding,
  isOfficialWechatAuthorizationUrl,
  listWechatKnowledgeBases,
  unbindWechatKnowledgeBase,
} from "./wechatIntegration";

vi.mock("../api/client", () => ({
  api: { delete: vi.fn(), get: vi.fn(), post: vi.fn(), put: vi.fn() },
}));

const del = api.delete as unknown as Mock;
const get = api.get as unknown as Mock;
const post = api.post as unknown as Mock;
const put = api.put as unknown as Mock;

describe("WeChat integration service", () => {
  beforeEach(() => vi.resetAllMocks());

  it("creates a WeChat authorization session without exposing secrets", async () => {
    post.mockResolvedValueOnce({
      data: {
        authorization_url:
          "https://mp.weixin.qq.com/cgi-bin/componentloginpage?pre_auth_code=official-code",
        expires_at: "2026-08-12T12:00:00Z",
        state_id: "state-reference",
        access_token: "must-not-survive",
        raw_state: "must-not-survive",
      },
    });

    const result = await createWechatAuthorizationSession({ knowledgeBaseId: 12 });

    expect(post).toHaveBeenCalledWith("/platform-integrations/wechat/authorization-sessions", {
      knowledge_base_id: 12,
    });
    expect(result.authorizationUrl).toContain("pre_auth_code=");
    expect(result).toEqual({
      authorizationUrl:
        "https://mp.weixin.qq.com/cgi-bin/componentloginpage?pre_auth_code=official-code",
      expiresAt: "2026-08-12T12:00:00Z",
      stateId: "state-reference",
    });
    expect(JSON.stringify(result)).not.toContain("access_token");
    expect(JSON.stringify(result)).not.toContain("raw_state");
  });

  it("accepts only the official HTTPS WeChat authorization host", () => {
    expect(isOfficialWechatAuthorizationUrl("https://mp.weixin.qq.com/cgi-bin/componentloginpage?pre_auth_code=x")).toBe(true);
    expect(isOfficialWechatAuthorizationUrl("http://mp.weixin.qq.com/cgi-bin/componentloginpage")).toBe(false);
    expect(isOfficialWechatAuthorizationUrl("https://mp.weixin.qq.com.evil.test/cgi-bin/componentloginpage")).toBe(false);
  });

  it("parses capability states through a strict allowlist", async () => {
    const state = { can_use: true, reason: null, permission_ids: [1], token: "drop" };
    get.mockResolvedValueOnce({
      data: {
        account_id: 8,
        upload_article_image: state,
        add_permanent_material: state,
        draft_add: state,
        draft_get: state,
        draft_update: state,
        analytics: { can_use: false, reason: "live_probe_failed", permission_ids: [] },
        freepublish: { can_use: true, reason: null, permission_ids: [99] },
        checked_at: "2026-08-12T12:00:00Z",
        component_access_token: "drop",
      },
    });

    const result = await getWechatAccountCapabilities(8);

    expect(get).toHaveBeenCalledWith("/accounts/8/platform-capabilities");
    expect(result.analytics.reason).toBe("live_probe_failed");
    expect(result.freepublish.canUse).toBe(true);
    expect(JSON.stringify(result)).not.toContain("token");
  });

  it("lists only allowlisted knowledge-base fields and manages binding endpoints", async () => {
    get
      .mockResolvedValueOnce({
        data: {
          data: [
            {
              id: 12,
              client_id: 3,
              kind: "brand",
              name: "North Star",
              description: null,
              status: "active",
              version: 2,
              access_token: "drop",
            },
          ],
          pagination: { limit: 50, offset: 0, total: 1 },
        },
      })
      .mockResolvedValueOnce({
        data: {
          id: 6,
          account_id: 8,
          knowledge_base_id: 12,
          knowledge_base_kind: "brand",
          client_id: 3,
          binding_type: "primary_brand",
          status: "active",
          bound_at: "2026-08-12T12:00:00Z",
          refresh_token: "drop",
        },
      });
    put.mockResolvedValueOnce({ data: { id: 6, account_id: 8, knowledge_base_id: 12 } });
    del.mockResolvedValueOnce({ data: undefined });

    const bases = await listWechatKnowledgeBases();
    const binding = await getWechatKnowledgeBinding(8);
    await bindWechatKnowledgeBase(8, 12);
    await unbindWechatKnowledgeBase(8);

    expect(get).toHaveBeenNthCalledWith(1, "/knowledge-bases", {
      params: { limit: 100, offset: 0 },
    });
    expect(bases.data[0]).toEqual({
      id: 12,
      clientId: 3,
      kind: "brand",
      name: "North Star",
      description: null,
      status: "active",
      version: 2,
    });
    expect(binding?.knowledgeBaseId).toBe(12);
    expect(put).toHaveBeenCalledWith("/accounts/8/knowledge-binding", {
      knowledge_base_id: 12,
    });
    expect(del).toHaveBeenCalledWith("/accounts/8/knowledge-binding");
    expect(JSON.stringify({ bases, binding })).not.toContain("token");
  });

  it("loads later knowledge-base pages so an eligible brand is available", async () => {
    get
      .mockResolvedValueOnce({
        data: {
          data: [{
            id: 21,
            client_id: null,
            kind: "organization_shared",
            name: "Shared library",
            description: null,
            status: "active",
            version: 1,
          }],
          pagination: { limit: 1, offset: 0, total: 2 },
        },
      })
      .mockResolvedValueOnce({
        data: {
          data: [{
            id: 22,
            client_id: 3,
            kind: "brand",
            name: "Page two brand",
            description: null,
            status: "active",
            version: 1,
          }],
          pagination: { limit: 1, offset: 1, total: 2 },
        },
      });

    const result = await listWechatKnowledgeBases({ limit: 1 });

    expect(get).toHaveBeenNthCalledWith(2, "/knowledge-bases", {
      params: { limit: 1, offset: 1 },
    });
    expect(result.data.map((base) => base.name)).toEqual([
      "Shared library",
      "Page two brand",
    ]);
  });

  it("fails safely when a knowledge-base page makes no pagination progress", async () => {
    get.mockResolvedValueOnce({
      data: {
        data: [],
        pagination: { limit: 100, offset: 0, total: 2 },
      },
    });

    await expect(listWechatKnowledgeBases()).rejects.toThrow(
      "Knowledge-base pagination made no progress",
    );

    expect(get).toHaveBeenCalledTimes(1);
  });

  it("fails safely when knowledge-base pagination exceeds its page ceiling", async () => {
    get.mockImplementation((_url: string, config: { params: { offset: number } }) =>
      Promise.resolve({
        data: {
          data: [{
            id: config.params.offset + 1,
            client_id: 3,
            kind: "brand",
            name: `Brand ${config.params.offset + 1}`,
            description: null,
            status: "active",
            version: 1,
          }],
          pagination: { limit: 1, offset: config.params.offset, total: 101 },
        },
      }));

    await expect(listWechatKnowledgeBases({ limit: 1 })).rejects.toThrow(
      "Knowledge-base pagination exceeded the safe page limit",
    );
    expect(get).toHaveBeenCalledTimes(100);
  });
});
