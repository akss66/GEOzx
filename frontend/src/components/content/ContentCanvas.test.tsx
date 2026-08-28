// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ContentWorkspace } from "../../types";
import { ContentCanvas } from "./ContentCanvas";

describe("ContentCanvas", () => {
  afterEach(cleanup);

  it("does not offer legacy publish preparation for a WeChat account", () => {
    render(
      <ContentCanvas
        workspace={makeWechatWorkspace()}
        loading={false}
        starting={false}
        canOperate
        inspectorMode={null}
        onStart={vi.fn()}
        onOpenInspector={vi.fn()}
        onEdit={vi.fn()}
      />,
    );

    expect(screen.queryByText("发布准备")).not.toBeInTheDocument();
  });
});

function makeWechatWorkspace(): ContentWorkspace {
  return {
    content_item: {
      id: 7,
      project_id: 22,
      account_id: 3,
      title: "公众号内容",
      current_stage: "operation",
      status: "in_progress",
      created_at: "2026-08-12T00:00:00Z",
    },
    project_name: "品牌项目",
    account: {
      id: 3,
      nickname: "品牌公众号",
      platform: "wechat_official_account",
      auth_status: "authorized",
    },
    tasks: [],
    deliverables: [],
    gates: [],
    compliance: [],
    materials: [],
    publish_tool_calls: [],
  };
}
