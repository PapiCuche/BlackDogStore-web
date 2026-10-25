"use client";

/**
 * AUTH-C — the 25-minute warning, «Continuar sesión», and what happens at zero.
 *
 * The server decides when the session ends (AUTH-B); this component only shows
 * it in time. It reads what the server said (`session-state`), warns
 * `warn_before_s` before the idle limit (300 s of 1800), and at zero HIDES the
 * screen before asking the server, so nothing internal stays on display while
 * the answer arrives.
 *
 *   «Continuar sesión»  → POST keepalive. The only thing in this dialog that
 *                          counts as activity, and only because a person
 *                          pressed it.
 *   «Cerrar sesión»     → the usual logout.
 *   at zero, internal   → full navigation to the login page with the reason
 *                          (`window.location.replace`: no React state with the
 *                          screen's data survives, and Back does not return).
 *   at zero, storefront → the visitor carries on as a guest; the cart lives in
 *                          the server under the browser's key and is not lost.
 *
 * A page restored from the browser's back-forward cache is hidden and asked
 * again before it shows anything (`pageshow` with `persisted`).
 *
 * The heartbeat: somebody typing a long form sends no request for minutes. A
 * TRUSTED key or pointer press, at most once a minute, sends a keepalive. No
 * timer ever does.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { API_BASE } from "../lib/api";
import { fetchWithAuth, logout } from "../lib/auth";
import {
  HEARTBEAT_EVERY_MS,
  type SessionEndReason,
  absoluteRemainingS,
  expiryReason,
  getSession,
  idleRemainingS,
  joinTabs,
  markEnded,
  noteRemaining,
  onTrustedGesture,
  subscribe,
} from "../lib/session-state";

type Mode = "internal" | "storefront";
type Phase = "ok" | "warning" | "checking" | "expired";

type Status = {
  idle_remaining_s: number;
  absolute_remaining_s: number;
  idle_timeout_s: number;
  warn_before_s: number;
};

const STATUS_URL = `${API_BASE}/auth/session/`;
const KEEPALIVE_URL = `${API_BASE}/auth/session/keepalive/`;

/** Where the watcher sends the page. An object so tests can see where it went. */
export const sessionNavigation = {
  replace(url: string): void {
    window.location.replace(url);
  },
};

export function loginPathFor(reason: SessionEndReason, location: Pick<Location, "pathname" | "search">): string {
  const next = `${location.pathname}${location.search}`;
  return `/auth?reason=${reason}&next=${encodeURIComponent(next)}`;
}

function clock(seconds: number): string {
  const whole = Math.max(0, Math.ceil(seconds));
  const minutes = Math.floor(whole / 60);
  return `${minutes}:${String(whole % 60).padStart(2, "0")}`;
}

/**
 * Ask the server how long is left. Never activity.
 *   Status  — alive, and how long is left;
 *   "over"  — the server said there is no session (401);
 *   null    — no answer (network, 5xx): nothing is concluded from silence.
 */
async function askServer(): Promise<Status | "over" | null> {
  try {
    const res = await fetchWithAuth(STATUS_URL, { activity: false, cache: "no-store" });
    if (res.status === 401) return "over";
    if (!res.ok) return null;
    const body = (await res.json()) as Status;
    noteRemaining(body.idle_remaining_s, body.absolute_remaining_s, body.warn_before_s);
    return body;
  } catch {
    return null;
  }
}

async function askStatus(): Promise<Status | null> {
  const answer = await askServer();
  return answer === "over" ? null : answer;
}

export function SessionWatcher({ mode, children }: { mode: Mode; children?: React.ReactNode }) {
  const [phase, setPhase] = useState<Phase>("ok");
  const [left, setLeft] = useState<number | null>(null);
  const [absolute, setAbsolute] = useState(false);
  const [idleTimeoutS, setIdleTimeoutS] = useState(1800);
  const known = useRef(false);
  const phaseRef = useRef<Phase>("ok");
  const lastBeat = useRef(0);
  const continueRef = useRef<HTMLButtonElement | null>(null);
  const titleId = useId();
  const bodyId = useId();

  const go = useCallback((next: Phase) => {
    phaseRef.current = next;
    setPhase(next);
  }, []);

  const expire = useCallback((reason: SessionEndReason) => {
    markEnded(reason);
    if (mode === "internal") {
      go("checking"); // stays covered until the navigation replaces the page
      sessionNavigation.replace(loginPathFor(reason === "logout" ? "idle" : reason, window.location));
      return;
    }
    go("expired");
  }, [go, mode]);

  /** Hide first, then ask. Shown again only if the server says it is alive. */
  const verify = useCallback(async () => {
    go("checking");
    // Without an answer the screen stays covered and the server is asked again:
    // silence is neither «alive» nor «over».
    for (let attempt = 0; ; attempt++) {
      const answer = await askServer();
      if (answer && answer !== "over") {
        if (answer.idle_remaining_s > 0 && answer.absolute_remaining_s > 0) {
          go(Math.min(answer.idle_remaining_s, answer.absolute_remaining_s) <= answer.warn_before_s ? "warning" : "ok");
          return;
        }
      }
      if (answer !== null) break;
      if (getSession().ended) break;
      await new Promise((resolve) => window.setTimeout(resolve, Math.min(10_000, 1_000 * 2 ** attempt)));
    }
    if (getSession().ended || known.current) expire(expiryReason());
    else go("ok");
  }, [expire, go]);

  // First reading, and the other tabs.
  useEffect(() => {
    const leave = joinTabs();
    void askStatus().then((status) => {
      if (status) {
        known.current = true;
        setIdleTimeoutS(status.idle_timeout_s);
      }
    });
    const unsubscribe = subscribe((snapshot) => {
      if (snapshot.idleDeadline !== null) known.current = true;
      // Only a session this page KNEW can end here: a guest has none to lose.
      if (snapshot.ended && known.current && phaseRef.current !== "expired" && phaseRef.current !== "checking") {
        if (snapshot.ended === "logout") {
          if (mode === "internal") sessionNavigation.replace("/auth");
          else go("ok");
        } else {
          expire(snapshot.ended);
        }
      } else if (!snapshot.ended && phaseRef.current === "expired") {
        go("ok"); // somebody signed in again, here or in another tab
        void askStatus();
      } else if (phaseRef.current === "warning") {
        const idle = idleRemainingS();
        if (idle !== null && idle > snapshot.warnBeforeS) go("ok"); // activity elsewhere
      }
    });
    return () => {
      unsubscribe();
      leave();
    };
  }, [expire, go, mode]);

  // The countdown. A display timer: it never sends activity, and asks the
  // server only at the two moments that matter (the warning, and zero).
  useEffect(() => {
    const tick = window.setInterval(() => {
      if (!known.current || getSession().ended) return;
      const idle = idleRemainingS();
      const abs = absoluteRemainingS();
      if (idle === null) return;
      const remaining = abs === null ? idle : Math.min(idle, abs);
      setLeft(remaining);
      setAbsolute(abs !== null && abs <= idle);
      const current = phaseRef.current;
      if (remaining <= 0 && (current === "ok" || current === "warning")) {
        void verify();
      } else if (current === "ok" && remaining <= getSession().warnBeforeS) {
        void (async () => {
          const status = await askStatus();
          if (!status) return;
          if (Math.min(status.idle_remaining_s, status.absolute_remaining_s) <= status.warn_before_s) go("warning");
        })();
      }
    }, 1000);
    return () => window.clearInterval(tick);
  }, [go, verify]);

  // Back-forward cache: a restored page shows nothing until the server answers.
  useEffect(() => {
    const onShow = (event: PageTransitionEvent) => {
      if (event.persisted) void verify();
    };
    window.addEventListener("pageshow", onShow);
    return () => window.removeEventListener("pageshow", onShow);
  }, [verify]);

  // The heartbeat of somebody working without saving: trusted gestures only.
  useEffect(() => onTrustedGesture(() => {
    const at = Date.now();
    if (!known.current || getSession().ended || phaseRef.current !== "ok") return;
    if (at - lastBeat.current < HEARTBEAT_EVERY_MS) return;
    lastBeat.current = at;
    void fetchWithAuth(KEEPALIVE_URL, { method: "POST", activity: true }).catch(() => {});
  }), []);

  useEffect(() => {
    if (phase === "warning") continueRef.current?.focus();
  }, [phase]);

  async function keepGoing() {
    lastBeat.current = Date.now();
    try {
      const res = await fetchWithAuth(KEEPALIVE_URL, { method: "POST", activity: true });
      if (res.ok) {
        const body = (await res.json()) as Status;
        noteRemaining(body.idle_remaining_s, body.absolute_remaining_s, body.warn_before_s);
        go("ok");
        return;
      }
    } catch {
      /* fall through: ask the server what is true */
    }
    await verify();
  }

  async function signOut() {
    await logout();
    sessionNavigation.replace("/auth");
  }

  const covered = phase === "checking" || (phase === "expired" && mode === "internal");
  const minutes = Math.round(idleTimeoutS / 60);

  return (
    <>
      {children !== undefined && (
        <div inert={covered || phase === "warning" || undefined} aria-hidden={covered || undefined}>
          {children}
        </div>
      )}

      {covered && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-background p-4" role="status">
          <p className="text-sm text-muted">Comprobando tu sesión…</p>
        </div>
      )}

      {phase === "warning" && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4">
          <div
            role="alertdialog"
            aria-modal="true"
            aria-labelledby={titleId}
            aria-describedby={bodyId}
            onKeyDown={(event) => {
              if (event.key !== "Tab") return;
              // Two buttons: Tab moves between them and never leaves the dialog.
              const buttons = Array.from(event.currentTarget.querySelectorAll("button"));
              const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
              event.preventDefault();
              buttons[(index + (event.shiftKey ? buttons.length - 1 : 1)) % buttons.length]?.focus();
            }}
            className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-xl border border-bd-border bg-surface p-5 shadow-xl sm:p-6"
          >
            <h2 id={titleId} className="text-lg font-bold text-foreground">
              {absolute ? "Tu sesión llega a su límite" : "Tu sesión está por cerrarse"}
            </h2>
            <p id={bodyId} className="mt-3 text-sm text-foreground">
              {absolute
                ? "Por seguridad, una sesión dura como máximo 8 horas desde que entraste. Guarda lo que estés haciendo: tendrás que volver a entrar."
                : `Por seguridad, la sesión se cierra tras ${minutes} minutos sin actividad.`}
            </p>
            <p className="mt-2 text-2xl font-bold tabular-nums text-foreground" aria-hidden="true">
              {clock(left ?? 0)}
            </p>
            <p className="sr-only" aria-live="polite">
              {`Quedan ${Math.max(1, Math.ceil((left ?? 0) / 60))} minuto(s).`}
            </p>
            <div className="mt-5 flex flex-wrap justify-end gap-2">
              <button
                type="button"
                onClick={signOut}
                className="rounded-lg border border-bd-border bg-background px-3.5 py-2.5 text-sm font-semibold text-foreground transition hover:border-foreground/25 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
              >
                Cerrar sesión
              </button>
              {!absolute && (
                <button
                  ref={continueRef}
                  type="button"
                  onClick={keepGoing}
                  className="rounded-lg bg-primary px-4 py-2.5 text-sm font-bold text-background transition hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
                >
                  Continuar sesión
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {phase === "expired" && mode === "storefront" && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4">
          <div
            role="alertdialog"
            aria-modal="true"
            aria-labelledby={titleId}
            className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-xl border border-bd-border bg-surface p-5 shadow-xl sm:p-6"
          >
            <h2 id={titleId} className="text-lg font-bold text-foreground">Tu sesión se cerró</h2>
            <p className="mt-3 text-sm text-foreground">
              Se cerró por seguridad. Tu carrito sigue aquí: puedes iniciar sesión para que el pedido quede en tu
              cuenta, o seguir como invitado.
            </p>
            <div className="mt-5 flex flex-wrap justify-end gap-2">
              <button
                type="button"
                onClick={() => go("ok")}
                className="rounded-lg border border-bd-border bg-background px-3.5 py-2.5 text-sm font-semibold text-foreground"
              >
                Seguir como invitado
              </button>
              <a
                href={loginPathFor(expiryReason(), typeof window === "undefined" ? { pathname: "/", search: "" } : window.location)}
                className="rounded-lg bg-primary px-4 py-2.5 text-sm font-bold text-background"
              >
                Iniciar sesión
              </a>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
