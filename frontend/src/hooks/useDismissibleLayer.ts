import { useEffect, type RefObject } from "react";

interface DismissibleLayerOptions {
  open: boolean;
  onDismiss: () => void;
  panelRef: RefObject<HTMLElement | null>;
  triggerRef: RefObject<HTMLElement | null>;
}

export function useDismissibleLayer({
  open,
  onDismiss,
  panelRef,
  triggerRef,
}: DismissibleLayerOptions) {
  useEffect(() => {
    if (!open) return;
    const returnFocusTo = triggerRef.current;

    const dismiss = () => {
      onDismiss();
      queueMicrotask(() => returnFocusTo?.focus());
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      dismiss();
    };
    const onMouseDown = (event: MouseEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (panelRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      dismiss();
    };

    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("mousedown", onMouseDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("mousedown", onMouseDown);
    };
  }, [onDismiss, open, panelRef, triggerRef]);
}
