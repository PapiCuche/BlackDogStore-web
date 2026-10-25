/**
 * AUTH-C — what the browser knows about its web session, and nothing more.
 *
 * THE SERVER DECIDES. This module only MIRRORS what the server last said: how
 * many seconds of inactivity the session has left (`X-Session-Idle-Remaining`,
 * or `GET /api/auth/session/`) and how many until its absolute limit. Seconds,
 * never dates: the deadline is «the instant we received it + what was left»,
 * on this tab's monotonic clock, so a computer with the wrong time moves
 * nothing. If this module is wrong, the next request is corrected by the server.
 *
 * ACTIVITY IS A PERSON. The `X-Session-Activity` mark goes out only within a
 * few seconds of a TRUSTED gesture (a real key or pointer, `event.isTrusted`,
 * or the browser's own `navigator.userActivation`). A timer, a poll, a focus
 * event, an effect on mount or a `router.replace` that syncs the address bar
 * can never produce one, whatever it calls: there is no gesture behind it. The
 * server additionally ignores the mark without CSRF, outside 2xx/3xx and on
 * renewal, logout and status (AUTH-B).
 *
 * NO RESURRECTION. Once the server says `session_expired`, nothing here asks
 * for a renewal again until somebody signs in (`sessionStarted`).
 *
 * Tabs share one session (same cookies): they share this state through a
 * BroadcastChannel — activity in one postpones the warning in all, and logging
 * out or expiring in one ends it in all.
 */

export type SessionEndReason = "idle" | "absolute" | "logout";

export type SessionSnapshot = {
  /** Monotonic deadline of the idle limit, in ms (`performance.now()` scale), or null if unknown. */
  idleDeadline: number | null;
  /** Monotonic deadline of the absolute limit, or null if unknown. */
  absoluteDeadline: number | null;
  /** Seconds before the idle deadline at which the warning shows. */
  warnBeforeS: number;
  /** Set once the session is over; cleared only by a new sign-in. */
  ended: SessionEndReason | null;
};

export const ACTIVITY_HEADER = "X-Session-Activity";
export const REMAINING_HEADER = "X-Session-Idle-Remaining";
export const SESSION_EXPIRED_CODE = "session_expired";
/** On every answer to a request that presented a session that is over (AUTH-B). */
export const EXPIRED_HEADER = "X-Session-Expired";
/** How long after a trusted gesture a request still counts as the person's. */
export const GESTURE_WINDOW_MS = 5_000;
/** At most one activity heartbeat (keepalive) per this, while somebody works without saving. */
export const HEARTBEAT_EVERY_MS = 60_000;
const CHANNEL = "bd-session";

type Listener = (snapshot: SessionSnapshot) => void;

let state: SessionSnapshot = { idleDeadline: null, absoluteDeadline: null, warnBeforeS: 300, ended: null };
const listeners = new Set<Listener>();
let lastGestureAt = Number.NEGATIVE_INFINITY;
let channel: BroadcastChannel | null = null;

function now(): number {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

function emit(): void {
  for (const listener of listeners) listener(state);
}

function post(message: Record<string, unknown>): void {
  try {
    channel?.postMessage({ ...message, sentAt: Date.now() });
  } catch {
    // A closed channel only means the other tabs learn it from the server.
  }
}

export function getSession(): SessionSnapshot {
  return state;
}

export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Seconds of inactivity left, as of now, or null if the server has not said. */
export function idleRemainingS(): number | null {
  return state.idleDeadline === null ? null : Math.max(0, (state.idleDeadline - now()) / 1000);
}

export function absoluteRemainingS(): number | null {
  return state.absoluteDeadline === null ? null : Math.max(0, (state.absoluteDeadline - now()) / 1000);
}

/** The server said how long is left. `relayed` = it came from another tab, not from our server call. */
export function noteRemaining(
  idleS: number | null,
  absoluteS: number | null = null,
  warnBeforeS: number | null = null,
  { relayed = false }: { relayed?: boolean } = {},
): void {
  if (state.ended) return;
  const at = now();
  state = {
    ...state,
    idleDeadline: idleS === null || !Number.isFinite(idleS) ? state.idleDeadline : at + idleS * 1000,
    absoluteDeadline:
      absoluteS === null || !Number.isFinite(absoluteS) ? state.absoluteDeadline : at + absoluteS * 1000,
    warnBeforeS: warnBeforeS ?? state.warnBeforeS,
  };
  if (!relayed) post({ type: "remaining", idleS, absoluteS, warnBeforeS });
  emit();
}

/** The session is over. Idempotent; tells the other tabs unless they told us. */
export function markEnded(reason: SessionEndReason, { relayed = false }: { relayed?: boolean } = {}): void {
  if (state.ended) return;
  state = { ...state, ended: reason };
  if (!relayed) post({ type: "ended", reason });
  emit();
}

/** A new sign-in: forget the previous session's end. */
export function sessionStarted(): void {
  state = { idleDeadline: null, absoluteDeadline: null, warnBeforeS: state.warnBeforeS, ended: null };
  emit();
}

export function isEnded(): boolean {
  return state.ended !== null;
}

/** Which end an expiry is: the absolute limit if that was what ran out first. */
export function expiryReason(): SessionEndReason {
  const absolute = absoluteRemainingS();
  const idle = idleRemainingS();
  if (absolute !== null && (idle === null || absolute <= idle)) return "absolute";
  return "idle";
}

// -- gestures ------------------------------------------------------------------

export function noteTrustedGesture(event: Event): boolean {
  if (!event.isTrusted) return false;
  lastGestureAt = now();
  return true;
}

/** Whether a request made NOW is a person's doing. */
export function hasRecentGesture(): boolean {
  if (now() - lastGestureAt < GESTURE_WINDOW_MS) return true;
  const activation = typeof navigator !== "undefined"
    ? (navigator as Navigator & { userActivation?: { isActive?: boolean } }).userActivation
    : undefined;
  return Boolean(activation?.isActive);
}

/** Tests only. */
export function __resetSessionStateForTests(): void {
  state = { idleDeadline: null, absoluteDeadline: null, warnBeforeS: 300, ended: null };
  lastGestureAt = Number.NEGATIVE_INFINITY;
  listeners.clear();
  try {
    channel?.close();
  } catch {
    /* ignore */
  }
  channel = null;
}

// -- the channel between tabs --------------------------------------------------

/** Join the other tabs of this browser. Safe to call more than once. */
export function joinTabs(): () => void {
  if (channel || typeof BroadcastChannel === "undefined") return () => {};
  const opened = new BroadcastChannel(CHANNEL);
  channel = opened;
  opened.onmessage = (event: MessageEvent) => {
    const data = event.data as {
      type?: string; idleS?: number | null; absoluteS?: number | null; warnBeforeS?: number | null;
      reason?: SessionEndReason; sentAt?: number;
    };
    if (!data || typeof data !== "object") return;
    if (data.type === "remaining") {
      // Same machine, same wall clock: subtract how long the message travelled.
      const late = typeof data.sentAt === "number" ? Math.max(0, (Date.now() - data.sentAt) / 1000) : 0;
      const sub = (value: number | null | undefined) =>
        typeof value === "number" ? Math.max(0, value - late) : null;
      noteRemaining(sub(data.idleS), sub(data.absoluteS), data.warnBeforeS ?? null, { relayed: true });
    } else if (data.type === "ended" && data.reason) {
      markEnded(data.reason, { relayed: true });
    }
  };
  return () => {
    opened.close();
    if (channel === opened) channel = null;
  };
}

let tracking = false;
type GestureListener = () => void;
const gestureListeners = new Set<GestureListener>();

/** Called after every trusted gesture (the heartbeat of `SessionWatcher` listens here). */
export function onTrustedGesture(listener: GestureListener): () => void {
  gestureListeners.add(listener);
  return () => {
    gestureListeners.delete(listener);
  };
}

/** Watch for real key and pointer presses, page-wide. Idempotent. */
export function installGestureTracking(): void {
  if (tracking || typeof document === "undefined") return;
  tracking = true;
  const handler = (event: Event) => {
    if (!noteTrustedGesture(event)) return;
    for (const listener of gestureListeners) listener();
  };
  for (const type of ["pointerdown", "keydown"]) {
    document.addEventListener(type, handler, { capture: true, passive: true });
  }
}

/** What the login screen says when a session closed by itself (`/auth?reason=`). */
export function sessionEndNotice(reason: string | null): string | null {
  if (reason === "idle") return "Tu sesión se cerró por inactividad. Vuelve a entrar para continuar.";
  if (reason === "absolute") return "Tu sesión llegó a su límite de 8 horas. Vuelve a entrar para continuar.";
  return null;
}

/**
 * Read what any answer of our API says about the session. A guest page, too:
 * the cookies a dead session loses are HttpOnly, so `X-Session-Expired` is the
 * only readable sign that this browser is no longer signed in.
 */
export function noteSessionHeaders(response: Pick<Response, "headers"> | null | undefined): void {
  // In the browser only: a server render has no session of anybody's.
  if (typeof window === "undefined") return;
  const headers = response?.headers;
  if (!headers || typeof headers.get !== "function") return;
  if (headers.get(EXPIRED_HEADER) === "1") {
    markEnded(expiryReason());
    return;
  }
  const raw = headers.get(REMAINING_HEADER);
  if (raw === null || raw === "") return;
  const seconds = Number(raw);
  if (Number.isFinite(seconds)) noteRemaining(seconds);
}
