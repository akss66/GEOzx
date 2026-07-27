export const OPERATIONS_BRAIN_DISPLAY_NAME = "运营大脑" as const;

export function presentOperationsBrainSystemCopy(value: string) {
  return value.replace(
    /主\s*Agent(?:\s+(?=\p{Script=Han}))?/gu,
    OPERATIONS_BRAIN_DISPLAY_NAME,
  );
}
