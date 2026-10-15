import { useEffect, useRef } from "react";

const focusable = 'button:not(:disabled), a[href], input:not(:disabled), textarea:not(:disabled), select:not(:disabled), summary, [tabindex="0"]';

/** Focus containment and restoration for drawers and confirmation dialogs. */
export function useModalLayer<T extends HTMLElement>(open: boolean, onClose: () => void, fallbackSelector?: string) {
  const ref = useRef<T>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const layer = ref.current;
    if (!open || !layer) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const elements = () => [...layer.querySelectorAll<HTMLElement>(focusable)]
      .filter((element) => element.getClientRects().length && !element.closest('[hidden], [inert]'));
    (elements()[0] ?? layer).focus();
    function keydown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        closeRef.current();
      }
      if (event.key !== "Tab") return;
      const items = elements();
      const first = items[0], last = items.at(-1);
      if (!first || !last) { event.preventDefault(); layer?.focus(); return; }
      if (event.shiftKey && (document.activeElement === first || !layer?.contains(document.activeElement))) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !layer?.contains(document.activeElement))) {
        event.preventDefault(); first.focus();
      }
    }
    layer.addEventListener("keydown", keydown);
    return () => {
      layer.removeEventListener("keydown", keydown);
      if (previous?.isConnected && previous.getClientRects().length && !previous.closest('[hidden], [inert]')) {
        previous.focus();
      } else if (fallbackSelector) {
        document.querySelector<HTMLElement>(fallbackSelector)?.focus();
      }
    };
  }, [open, fallbackSelector]);
  return ref;
}
