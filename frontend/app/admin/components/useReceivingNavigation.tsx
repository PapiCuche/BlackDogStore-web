"use client";

/**
 * INV-OPS-01 — the browser's own navigation, for the receiving wizard.
 *
 * 1. Each step is a history entry, so the browser's (or the phone's) «atrás»
 *    goes to the PREVIOUS STEP instead of leaving the flow and dropping the lines.
 * 2. While there is something to lose (lines typed, a confirmation in flight or
 *    without an answer) any way out of the app — a sidebar or breadcrumb link,
 *    the «atrás» of the first step — asks first, in an accessible dialog. With
 *    an attempt in flight or unanswered the warning is the stronger one: the
 *    reception may already be saved.
 *
 * It holds no data: only `rcv` (the step) and `pos` (its place in the flow's own entries) in `history.state`.
 */

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { registerLeaveGate } from "../lib/inventory-receipts";
import { useModalBehaviour } from "./useModalBehaviour";
import { internalButtonClass, internalPrimaryButtonClass } from "./internal-ui";

type Step = 1 | 2 | 3 | 4;
/** `href`: a link; neither `href` nor `proceed`: the browser's «atrás» at the start of the flow; `proceed`: another way out (company switch). */
export type LeavePrompt = { href: string | null; strong: boolean; proceed?: () => void };

type Options = {
  step: Step;
  /** The browser asks for this step (back/forward). */
  onStep: (step: Step) => void;
  /** There is something to lose by leaving. */
  guard: boolean;
  /** A confirmation is in flight or unanswered: stronger warning, and no stepping back. */
  strong: boolean;
  /** The result is on screen: going back must not reopen the flow. */
  finished: boolean;
};

const stateOf = () => (typeof window === "undefined" ? {} : ((window.history.state ?? {}) as Record<string, unknown>));

export function useReceivingNavigation({ step, onStep, guard, strong, finished }: Options) {
  const router = useRouter();
  const [prompt, setPrompt] = useState<LeavePrompt | null>(null);
  const latest = useRef({ step, onStep, guard, strong, finished });
  // The listeners below are installed once; they read the newest values from here.
  useEffect(() => { latest.current = { step, onStep, guard, strong, finished }; });
  const leaving = useRef(false);

  const restarting = useRef(false);
  const pushStep = useCallback((next: Step) => {
    const pos = typeof stateOf().pos === "number" ? (stateOf().pos as number) : 0;
    window.history.pushState({ ...stateOf(), rcv: next, pos: pos + 1 }, "");
  }, []);
  const replaceStep = useCallback((next: Step) => {
    window.history.replaceState({ ...stateOf(), rcv: next }, "");
  }, []);
  /** «Atrás» of the page itself: pops the entry that led here so the stack stays honest. */
  const popStep = useCallback(() => {
    if (stateOf().rcv === latest.current.step && latest.current.step > 1) window.history.back();
  }, []);

  /**
   * «Registrar otra recepción»: the new flow starts from the entries the old one began with, so the stack
   * is [start] [step 1] [step 2] again instead of growing a ghost for every reception.
   */
  const restart = useCallback(() => {
    const pos = stateOf().pos;
    leaving.current = false;
    if (typeof pos === "number" && pos > 1) {
      restarting.current = true;
      window.history.go(1 - pos); // the popstate that follows pushes the new step 2
    } else {
      window.history.replaceState({ ...stateOf(), rcv: 1 }, "");
      pushStep(2);
    }
  }, [pushStep]);

  useEffect(() => {
    const st = stateOf();
    if (st.rcv === undefined) {
      // [anything before] · [start of the flow] · [the step we are on]
      window.history.replaceState({ ...st, rcv: 0, pos: 0 }, "");
      // One entry per step up to where we start, so «atrás» from step 2 lands on step 1.
      for (let at = 1; at <= latest.current.step; at += 1) window.history.pushState({ ...stateOf(), rcv: at, pos: at }, "");
    } else {
      window.history.replaceState({ ...st, rcv: latest.current.step }, "");
    }
    registerLeaveGate((proceed) => {
      const now = latest.current;
      if (!now.guard) return false;
      setPrompt({ href: null, strong: now.strong, proceed });
      return true;
    });

    const onPop = (event: PopStateEvent) => {
      const here = event.state as { rcv?: number; pos?: number } | null;
      const rcv = here?.rcv;
      if (rcv === undefined) return; // not one of ours: the router handles it
      if (restarting.current) {
        restarting.current = false;
        pushStep(2);
        return;
      }
      if (leaving.current) return;
      const now = latest.current;
      if (now.finished) {
        // Done: «atrás» leaves the whole flow in one jump (never reopens a step, never leaves a ghost behind).
        leaving.current = true;
        if (typeof here?.pos === "number") window.history.go(-(here.pos + 1));
        else window.history.back();
        return;
      }
      if (rcv === 0) {
        if (now.guard) setPrompt({ href: null, strong: now.strong });
        else { leaving.current = true; window.history.back(); }
        return;
      }
      if (now.strong && rcv < now.step) {
        // Back/forward is not allowed here: put the current step back on top.
        window.history.pushState({ ...stateOf(), rcv: now.step }, "");
        return;
      }
      if (rcv >= 1 && rcv <= 4 && rcv !== now.step) now.onStep(rcv as Step);
    };

    const onClick = (event: MouseEvent) => {
      const now = latest.current;
      if (!now.guard || leaving.current || event.defaultPrevented) return;
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const anchor = (event.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!anchor || (anchor.target && anchor.target !== "_self") || anchor.hasAttribute("download")) return;
      if (anchor.dataset.receivingLeave === "ok") return; // it keeps the draft itself (crear artículo)
      const url = new URL(anchor.href, window.location.href);
      if (url.origin !== window.location.origin) return; // the browser's own «¿salir?» covers it
      if (url.pathname === window.location.pathname && url.search === window.location.search) return;
      event.preventDefault();
      event.stopPropagation();
      setPrompt({ href: `${url.pathname}${url.search}${url.hash}`, strong: now.strong });
    };

    window.addEventListener("popstate", onPop);
    document.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("popstate", onPop);
      document.removeEventListener("click", onClick, true);
      registerLeaveGate(null);
    };
  }, [pushStep]);

  // A reload or a closed tab: the browser's own question.
  useEffect(() => {
    if (!guard) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [guard]);

  const stay = useCallback(() => {
    // Came by the browser's «atrás» to the start of the flow: put the step back.
    if (prompt && prompt.href === null && !prompt.proceed) pushStep(latest.current.step);
    setPrompt(null);
  }, [prompt, pushStep]);

  const leave = useCallback(() => {
    if (prompt?.proceed) {
      prompt.proceed();
    } else {
      leaving.current = true;
      if (prompt?.href) router.push(prompt.href);
      else window.history.back();
    }
    setPrompt(null);
  }, [prompt, router]);

  return { prompt, stay, leave, pushStep, replaceStep, popStep, restart };
}

export function ReceivingLeaveDialog({ prompt, onStay, onLeave }: { prompt: LeavePrompt; onStay: () => void; onLeave: () => void }) {
  const panel = useRef<HTMLDivElement>(null);
  useModalBehaviour(panel, onStay);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div
        ref={panel} role="alertdialog" aria-modal="true" aria-labelledby="leave-title" aria-describedby="leave-text"
        className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-2xl border border-bd-border bg-background p-5 shadow-xl"
      >
        <h2 id="leave-title" className="text-base font-bold text-foreground">
          {prompt.strong ? "Hay una confirmación sin respuesta" : "Tienes una recepción sin confirmar"}
        </h2>
        <p id="leave-text" className="mt-2 text-sm text-muted">
          {prompt.strong
            ? "Puede que la recepción ya se haya registrado. Si sales ahora no sabrás si se guardó: quédate y reintenta con el mismo botón (no se duplicará), o revisa el historial de movimientos."
            : "Si sales se pierde lo que escribiste."}
        </p>
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <button type="button" autoFocus className={`${internalPrimaryButtonClass} min-h-11`} onClick={onStay}>Seguir aquí</button>
          <button type="button" className={`${internalButtonClass} min-h-11`} onClick={onLeave}>Salir</button>
        </div>
      </div>
    </div>
  );
}
