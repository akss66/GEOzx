import { describe, expect, it } from "vitest";

import { shellPresentationForPath } from "./shellPresentation";

describe("shellPresentationForPath", () => {
  it("hides the duplicate global agent entry on the operations brain", () => {
    expect(shellPresentationForPath("/")).toEqual({
      showGlobalAgent: false,
      raiseGlobalAgent: false,
    });
    expect(shellPresentationForPath("/brain")).toEqual({
      showGlobalAgent: false,
      raiseGlobalAgent: false,
    });
  });

  it("raises the global agent above page-level action bars", () => {
    for (const path of ["/agents", "/approvals", "/config"]) {
      expect(shellPresentationForPath(path)).toEqual({
        showGlobalAgent: true,
        raiseGlobalAgent: true,
      });
    }
  });

  it("keeps the default launcher position on ordinary workspaces", () => {
    expect(shellPresentationForPath("/accounts")).toEqual({
      showGlobalAgent: true,
      raiseGlobalAgent: false,
    });
  });
});
