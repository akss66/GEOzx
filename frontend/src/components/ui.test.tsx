// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { OperationalState } from "./ui";

describe("OperationalState", () => {
  it("explains the failure and gives the user a recovery action", () => {
    const onRetry = vi.fn();

    render(
      <OperationalState
        kind="error"
        title="账号矩阵加载失败"
        description="当前账号选择仍然保留，可以重新读取，不会切换到其他账号。"
        actionLabel="重新加载"
        onAction={onRetry}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("账号矩阵加载失败");
    expect(screen.getByText(/不会切换到其他账号/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("keeps diagnostics collapsed and separate from the user-facing message", () => {
    render(
      <OperationalState
        kind="error"
        title="数据暂时不可用"
        description="请稍后重试。"
        diagnostic="HTTP 503 · request req-42"
      />,
    );

    const details = screen.getByText("查看诊断信息").closest("details");
    expect(details).not.toHaveAttribute("open");
    expect(details).toHaveTextContent("HTTP 503 · request req-42");
  });
});
