"use client";

/**
 * ECOM-READY-D1 — what a modal panel owes the page behind it, in one place:
 * the page does not scroll while it is open (and gets back exactly what it had),
 * Escape closes it, Tab stays inside it, and focus returns to what opened it.
 */
import { useEffect, type RefObject } from "react";

const FOCUSABLE = "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])";

// Two modals can overlap; the page is released only when the last one goes.
let locks = 0;
let savedOverflow = "";

export function useModalBehaviour(
  panel: RefObject<HTMLElement | null>, onClose: () => void,
  /** What opened it. Some browsers do not focus a button on click, so the opener is named, not guessed. */
  returnFocusTo?: RefObject<HTMLElement | null>,
) {

  useEffect(() => {
    if (locks === 0) savedOverflow = document.body.style.overflow;
    locks += 1;
    document.body.style.overflow = "hidden";
    return () => {
      locks -= 1;
      if (locks === 0) document.body.style.overflow = savedOverflow;
    };
  }, []);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const opener = returnFocusTo?.current ?? null; // set by the click, before this mounts
    return () => {
      const target = opener?.isConnected ? opener : previous;
      if (target?.isConnected) target.focus();
    };
  }, [returnFocusTo]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { onClose(); return; }
      if (event.key !== "Tab" || !panel.current) return;
      const stops = [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (stops.length === 0) return;
      const at = stops.indexOf(document.activeElement as HTMLElement);
      const next = event.shiftKey ? (at <= 0 ? stops.length - 1 : at - 1) : (at + 1) % stops.length;
      event.preventDefault();
      stops[next].focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, panel]);
}
