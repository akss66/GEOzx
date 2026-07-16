import { describe, expect, it } from "vitest";

import { buildTheme, DESIGN_TOKENS } from "./tokens";

describe("theme tokens", () => {
  it("uses the approved V2 light palette", () => {
    const theme = buildTheme();

    expect(DESIGN_TOKENS.brandRed).toBe("#C9161D");
    expect(DESIGN_TOKENS.brandFrame).toBe("#EEE8DF");
    expect(DESIGN_TOKENS.workCanvas).toBe("#F6F6F3");
    expect(theme.token?.colorPrimary).toBe("#C9161D");
    expect(theme.token?.colorText).toBe("#171614");
    expect(theme.token?.borderRadius).toBe(10);
    expect(theme.components?.Button?.colorPrimary).toBe("#171614");
    expect(theme.components?.Menu?.itemBorderRadius).toBe(10);
  });

  it("does not expose a dark-mode variant", () => {
    expect(buildTheme.length).toBe(0);
    expect(buildTheme().algorithm).toBeUndefined();
  });
});
