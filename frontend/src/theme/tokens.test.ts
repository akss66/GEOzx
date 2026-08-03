import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";

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

  it("keeps one accessible light-theme token source", () => {
    const foundation = readFileSync(
      new URL("../styles/foundation.css", import.meta.url),
      "utf8",
    );
    const legacyStyles = readFileSync(new URL("../index.css", import.meta.url), "utf8");
    const fidelityStyles = readFileSync(
      new URL("../styles/high-fidelity-system.css", import.meta.url),
      "utf8",
    );

    expect(DESIGN_TOKENS.faint).toBe("#6F695F");
    expect(foundation).toContain("--dy-faint: var(--tz-faint)");
    expect(legacyStyles).not.toContain(':root[data-theme="dark"]');
    expect(fidelityStyles).not.toMatch(/:root\s*\{/);
  });
});
