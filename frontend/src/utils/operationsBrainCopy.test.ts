import { describe, expect, it } from "vitest";
import { presentOperationsBrainSystemCopy } from "./operationsBrainCopy";

describe("presentOperationsBrainSystemCopy", () => {
  it("normalizes legacy system identity copy", () => {
    expect(presentOperationsBrainSystemCopy("主 Agent 正在推进"))
      .toBe("运营大脑正在推进");
    expect(presentOperationsBrainSystemCopy("主Agent已完成"))
      .toBe("运营大脑已完成");
  });

  it("leaves unrelated copy unchanged", () => {
    expect(presentOperationsBrainSystemCopy("账号定位专家已完成"))
      .toBe("账号定位专家已完成");
  });
});
