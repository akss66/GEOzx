// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AttachmentTray, type DraftAttachment } from "./AttachmentTray";

afterEach(cleanup);

function attachment(
  status: DraftAttachment["status"],
  overrides: Partial<DraftAttachment> = {},
): DraftAttachment {
  return {
    key: `attachment-${status}`,
    filename: `${status}.txt`,
    file: new File([status], `${status}.txt`, { type: "text/plain" }),
    threadId: 81,
    id: status === "ready" ? 7 : null,
    status,
    ...overrides,
  };
}

describe("AttachmentTray", () => {
  it("renders upload progress and prevents removing an in-flight file", () => {
    render(
      <AttachmentTray
        attachments={[attachment("uploading")]}
        onRemove={vi.fn()}
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("uploading.txt处理中")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "移除 uploading.txt" })).toBeDisabled();
  });

  it("keeps a failed file visible and exposes retry and remove actions", () => {
    const failed = attachment("error", { error: "文件格式无法解析" });
    const onRetry = vi.fn();
    const onRemove = vi.fn();
    render(
      <AttachmentTray
        attachments={[failed]}
        onRemove={onRemove}
        onRetry={onRetry}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("文件格式无法解析");
    fireEvent.click(screen.getByRole("button", { name: "重试 error.txt" }));
    fireEvent.click(screen.getByRole("button", { name: "移除 error.txt" }));
    expect(onRetry).toHaveBeenCalledWith(failed);
    expect(onRemove).toHaveBeenCalledWith(failed);
  });
});
