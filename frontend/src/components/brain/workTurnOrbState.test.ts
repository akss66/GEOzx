import { describe, expect, it } from "vitest";

import { workTurnOrbState } from "./workTurnOrbState";

describe("workTurnOrbState", () => {
  it.each([
    ["understanding", "listening"],
    ["reading_data", "searching"],
    ["consulting_experts", "weaving"],
    ["quality_review", "solving"],
    ["composing_artifact", "composing"],
  ] as const)("maps %s to %s", (phase, state) => {
    expect(workTurnOrbState(phase)).toBe(state);
  });

  it("uses working for phases without an animated business meaning", () => {
    expect(workTurnOrbState()).toBe("working");
    expect(workTurnOrbState("waiting_approval")).toBe("working");
    expect(workTurnOrbState("completed")).toBe("working");
    expect(workTurnOrbState("failed")).toBe("working");
  });
});
