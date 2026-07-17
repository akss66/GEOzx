import { describe, expect, it } from "vitest";

import { buildTheme, DESIGN_TOKENS } from "./tokens";

describe("theme tokens", () => {
  it("uses the approved V2 light palette", () => {
    const theme = buildTheme();

    expect(DESIGN_TOKENS.brandRed).toBe("#C9161D");
    expect(DESIGN_TOKENS.brandFrame).toBe("#EEE8DF");
    expect(DESIGN_TOKENS.workCanvas).toBe("#F7F7F4");
    expect(theme.token?.colorPrimary).toBe("#C9161D");
    expect(theme.token?.colorText).toBe("#171614");
    expect(theme.token?.borderRadius).toBe(8);
    expect(theme.token?.fontFamily).toContain('"Geist Variable"');
    expect(theme.token?.fontFamily).toContain('"Noto Sans SC Variable"');
    expect(theme.components?.Button?.colorPrimary).toBe("#171614");
    expect(theme.components?.Menu?.itemBorderRadius).toBe(8);
  });

  it("does not expose a dark-mode variant", () => {
    expect(buildTheme.length).toBe(0);
    expect(buildTheme().algorithm).toBeUndefined();
  });
});
