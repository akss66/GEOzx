// @vitest-environment jsdom

import { describe, expect, it } from "vitest";

import {
  buildMatrixSections,
  normalizePlatformIntegrationPatch,
  platformIntegrationByKey,
  platformSummaryByKey,
} from "./Accounts";
import type {
  Account,
  AccountGroup,
  PlatformIntegration,
  PlatformMatrixSummary,
  Project,
} from "../types";

const baseAccount: Account = {
  id: 1,
  nickname: "抖音主号",
  platform: "douyin",
  group_id: 10,
  project_id: 100,
  status: "active",
  external_account_id: "dy_1",
  integration_status: "connected",
  auth_status: "authorized",
  data_sync_status: "healthy",
  created_at: "2026-07-01T00:00:00Z",
};

describe("Accounts matrix helpers", () => {
  it("groups accounts by project, group, and platform", () => {
    const projects: Project[] = [
      { id: 100, name: "新品项目", description: null, status: "active", created_at: "" },
    ];
    const groups: AccountGroup[] = [
      { id: 10, name: "测评赛道", dimension: "track", created_at: "" },
    ];
    const accounts: Account[] = [
      baseAccount,
      {
        ...baseAccount,
        id: 2,
        nickname: "小红书种草号",
        platform: "xiaohongshu",
        external_account_id: "xhs_1",
      },
      {
        ...baseAccount,
        id: 3,
        nickname: "视频号",
        platform: "shipinhao",
        group_id: null,
        project_id: null,
      },
    ];

    const sections = buildMatrixSections(accounts, projects, groups);

    expect(sections).toHaveLength(2);
    expect(sections[0]).toMatchObject({
      id: 100,
      name: "新品项目",
      groups: [
        {
          id: 10,
          name: "测评赛道",
          dimension: "track",
        },
      ],
    });
    expect(sections[0].groups[0].platforms.map((node) => node.platform)).toEqual([
      "douyin",
      "xiaohongshu",
    ]);
    expect(sections[1]).toMatchObject({
      id: null,
      name: "未绑定项目",
      groups: [{ id: null, name: "未分组账号", dimension: "ungrouped" }],
    });
  });

  it("indexes backend platform summaries by platform", () => {
    const rows: PlatformMatrixSummary[] = [
      {
        platform: "douyin",
        total: 2,
        active: 1,
        integration_status: "connected",
        auth_status: "authorized",
        data_sync_status: "healthy",
      },
    ];

    expect(platformSummaryByKey(rows).get("douyin")).toEqual(rows[0]);
  });

  it("indexes platform integration config by platform", () => {
    const integrations: PlatformIntegration[] = [
      {
        id: 1,
        platform: "douyin",
        status: "configured",
        client_key: "client-key",
        client_secret_configured: true,
        redirect_uri: "https://example.com/callback",
        js_sdk_domain: "https://example.com",
        auth_status: "not_configured",
        data_sync_status: "not_configured",
        scopes: ["user_info"],
        capabilities: {},
        official_docs: [],
        note: null,
        created_at: null,
        updated_at: null,
      },
    ];

    expect(platformIntegrationByKey(integrations).get("douyin")).toEqual(integrations[0]);
  });

  it("promotes a completed platform config to configured on save", () => {
    const patch = normalizePlatformIntegrationPatch(
      {
        status: "not_configured",
        client_key: "client-key",
        redirect_uri: "https://example.com/callback",
      },
      {
        id: 1,
        platform: "douyin",
        status: "not_configured",
        client_key: "client-key",
        client_secret_configured: true,
        redirect_uri: "https://example.com/callback",
        js_sdk_domain: null,
        auth_status: "not_configured",
        data_sync_status: "not_configured",
        scopes: [],
        capabilities: {},
        official_docs: [],
        note: null,
        created_at: null,
        updated_at: null,
      },
    );

    expect(patch.status).toBe("configured");
  });
});
