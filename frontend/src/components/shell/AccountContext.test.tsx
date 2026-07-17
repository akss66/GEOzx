// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Account } from "../../types";
import { AccountContext } from "./AccountContext";

const accounts: Account[] = [
  {
    id: 1,
    nickname: "账号一",
    platform: "douyin",
    group_id: null,
    project_id: null,
    status: "active",
    external_account_id: "douyin-1",
    integration_status: "connected",
    auth_status: "authorized",
    data_sync_status: "pending",
    created_at: "2026-07-17T00:00:00Z",
  },
  {
    id: 2,
    nickname: "账号二",
    platform: "douyin",
    group_id: null,
    project_id: null,
    status: "active",
    external_account_id: "douyin-2",
    integration_status: "connected",
    auth_status: "authorized",
    data_sync_status: "pending",
    created_at: "2026-07-17T00:00:00Z",
  },
];

afterEach(cleanup);

describe("AccountContext", () => {
  it("does not display the first account until the user explicitly selects one", () => {
    render(
      <AccountContext
        accounts={accounts}
        platform="douyin"
        accountId={null}
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "当前账号" })).toHaveTextContent("选择抖音账号");
    expect(screen.getByRole("button", { name: "当前账号" })).not.toHaveTextContent("账号一");
  });

  it("returns only the account explicitly clicked by the user", () => {
    const onChange = vi.fn();
    render(
      <AccountContext
        accounts={accounts}
        platform="douyin"
        accountId={null}
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "当前账号" }));
    fireEvent.click(screen.getByRole("button", { name: /账号二/ }));

    expect(onChange).toHaveBeenCalledOnce();
    expect(onChange).toHaveBeenCalledWith(2);
  });

  it("treats a stale stored account id as unselected", () => {
    render(
      <AccountContext
        accounts={accounts}
        platform="douyin"
        accountId={999}
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "当前账号" })).toHaveTextContent("选择抖音账号");
  });
});
