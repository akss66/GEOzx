// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const orbMock = vi.hoisted(() => ({ shouldThrow: false }));

vi.mock("thinking-orbs", () => ({
  ThinkingOrb: (props: Record<string, unknown>) => {
    if (orbMock.shouldThrow) throw new Error("canvas unavailable");
    return <canvas data-testid="thinking-orb" data-state={props.state} data-theme={props.theme} aria-label={typeof props["aria-label"] === "string" ? props["aria-label"] : undefined} />;
  },
}));

import { MainAgentStatusAvatar } from "./MainAgentStatusAvatar";

describe("MainAgentStatusAvatar", () => {
  afterEach(() => {
    orbMock.shouldThrow = false;
    cleanup();
  });

  it("renders the phase orb only when selected as active", () => {
    const view = render(<MainAgentStatusAvatar showThinkingOrb phase="reading_data" identity="杩愯惀澶ц剳" activityLabel="姝ｅ湪鏍稿宸插鍏ョ殑鏁版嵁鑼冨洿" className="tz-work-turn__avatar" />);
    expect(screen.getByTestId("thinking-orb")).toHaveAttribute("data-state", "searching");
    expect(screen.getByTestId("thinking-orb")).toHaveAttribute("data-theme", "light");
    expect(screen.getByLabelText("姝ｅ湪鏍稿宸插鍏ョ殑鏁版嵁鑼冨洿")).toBeVisible();

    view.rerender(<MainAgentStatusAvatar showThinkingOrb={false} phase="completed" identity="杩愯惀澶ц剳" activityLabel={null} className="tz-work-turn__avatar" />);
    expect(screen.queryByTestId("thinking-orb")).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: "杩愯惀澶ц剳" })).toBeVisible();
  });

  it("falls back to the static avatar when orb rendering fails", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    orbMock.shouldThrow = true;
    render(<MainAgentStatusAvatar showThinkingOrb phase="quality_review" identity="杩愯惀澶ц剳" activityLabel="姝ｅ湪鏍搁獙缁撹涓庢暟鎹緷鎹?" />);
    expect(screen.getByRole("img", { name: "杩愯惀澶ц剳" })).toBeVisible();
  });

  it("does not synthesize an orb label when activityLabel is missing", () => {
    render(<MainAgentStatusAvatar showThinkingOrb phase="understanding" identity="main agent" />);

    expect(screen.getByTestId("thinking-orb")).not.toHaveAttribute("aria-label");
  });
});
