import { describe, expect, it } from "vitest";

import type { Account } from "../../types";
import { getAccountActionMode } from "./accountActionMode";

function account(patch: Partial<Account>): Account {
  return {
    id: 1,
    client_id: 1,
    nickname: "测试账号",
    platform: "douyin",
    group_id: null,
    project_id: null,
    project_ids: [],
    status: "active",
    external_account_id: null,
    integration_status: "oauth_ready",
    auth_status: "unauthorized",
    data_sync_status: "not_configured",
    avatar_url: null,
    positioning_summary: null,
    current_task: null,
    risk_count: 0,
    last_sync_at: null,
    publish_capability: "unavailable",
    created_at: "2026-07-17T00:00:00Z",
    ...patch,
  };
}

describe("getAccountActionMode", () => {
  it("only offers official authorization for an unauthorized Douyin account", () => {
    expect(getAccountActionMode(account({ auth_status: "unauthorized" }))).toBe(
      "official_authorize",
    );
  });

  it("offers real metric sync after Douyin OAuth authorization", () => {
    expect(getAccountActionMode(account({ auth_status: "authorized" }))).toBe(
      "sync_metrics",
    );
  });

  it("keeps placeholder platforms read-only", () => {
    expect(
      getAccountActionMode(
        account({ platform: "xiaohongshu", auth_status: "unauthorized" }),
      ),
    ).toBe("coming_soon");
  });
});
