export type ShellPresentation = {
  showGlobalAgent: boolean;
  raiseGlobalAgent: boolean;
};

const BRAIN_PATHS = new Set(["/", "/brain"]);
const ACTION_BAR_PATHS = new Set(["/agents", "/approvals", "/config"]);

export function shellPresentationForPath(pathname: string): ShellPresentation {
  return {
    showGlobalAgent: !BRAIN_PATHS.has(pathname),
    raiseGlobalAgent: ACTION_BAR_PATHS.has(pathname),
  };
}
