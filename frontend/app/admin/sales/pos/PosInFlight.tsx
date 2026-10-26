"use client";

/**
 * AUTH-C (owner, points 4 and 5) — a counter sale that was sent and has no
 * definitive answer, after signing in again.
 *
 * Until it is RESOLVED the till charges nothing. Resolved means one of:
 *   · the server has it     → «sí se registró»: the cashier must not charge again;
 *   · the server says no, and enough time has passed for it to have arrived
 *                           → «no se registró»: the basket is entered again,
 *                             under a NEW key;
 *   · the cashier decides «no se cobró», and the server RECORDS that decision
 *     (and refuses it if a sale under that key exists).
 * Anything else is UNCERTAIN: a lookup that failed, or «not found» while the
 * sale might still be arriving. «Volver a comprobar» only ASKS (GET by key): it
 * never re-sends the charge and never creates an order.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  lookupPosSaleByKey,
  recordPosSaleNotCharged,
} from "../../lib/internal-api";
import { forgetSaleInFlight, readSaleInFlight, type SaleInFlight } from "../../lib/pos-in-flight";
import { internalButtonClass, internalPrimaryButtonClass } from "../../components/internal-ui";

/** How long a sent sale may still be arriving: «not found» before this is not an answer. */
export const IN_FLIGHT_WINDOW_MS = 120_000;

export type InFlightState =
  | { kind: "checking" }
  | { kind: "created"; orderId: number; total: string; branch: string }
  | { kind: "not_created"; recorded: boolean }
  | { kind: "uncertain"; why: "error" | "too_soon" };

export function usePosInFlight(companyId: number | null, userId: number) {
  const [mark, setMark] = useState<SaleInFlight | null>(null);
  const [state, setState] = useState<InFlightState | null>(null);
  // Set before the first answer arrives: nothing is charged while asking.
  const pending = useRef(false);

  const ask = useCallback(async (current: SaleInFlight) => {
    try {
      const answer = await lookupPosSaleByKey(companyId, current.key);
      if (answer.found) {
        return { kind: "created", orderId: answer.order_id, total: answer.total, branch: answer.branch } as const;
      }
      return Date.now() - current.at < IN_FLIGHT_WINDOW_MS
        ? ({ kind: "uncertain", why: "too_soon" } as const)
        : ({ kind: "not_created", recorded: false } as const);
    } catch {
      return { kind: "uncertain", why: "error" } as const;
    }
  }, [companyId]);

  useEffect(() => {
    const found = readSaleInFlight(userId, companyId);
    if (!found) return;
    pending.current = true;
    let cancelled = false;
    void (async () => {
      const next = await ask(found);
      pending.current = false;
      if (cancelled) return;
      setMark(found);
      setState(next);
    })();
    return () => { cancelled = true; };
  }, [ask, companyId, userId]);

  /** Only a read. */
  const recheck = useCallback(async () => {
    if (!mark) return;
    setState({ kind: "checking" });
    setState(await ask(mark));
  }, [ask, mark]);

  const decideNotCharged = useCallback(async () => {
    if (!mark) return;
    setState({ kind: "checking" });
    try {
      const answer = await recordPosSaleNotCharged(companyId, mark.key);
      setState("recorded" in answer
        ? { kind: "not_created", recorded: true }
        : { kind: "created", orderId: answer.order_id, total: answer.total, branch: "" });
    } catch {
      setState({ kind: "uncertain", why: "error" });
    }
  }, [companyId, mark]);

  const acknowledge = useCallback(() => {
    forgetSaleInFlight();
    setMark(null);
    setState(null);
  }, []);

  return {
    mark,
    state,
    /** For drawing: an unresolved sale is on screen. */
    blocking: mark !== null,
    /** For charging: unresolved, or still being asked about. */
    isBlocking: () => pending.current || mark !== null,
    recheck,
    decideNotCharged,
    acknowledge,
  };
}

export function PosInFlightNotice({
  mark, state, onRecheck, onDecideNotCharged, onAcknowledge,
}: {
  mark: SaleInFlight;
  state: InFlightState;
  onRecheck: () => void;
  onDecideNotCharged: () => void;
  onAcknowledge: () => void;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    // A display clock for «disponible en N s». It asks nothing of anybody.
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(tick);
  }, []);
  const waitS = Math.max(0, Math.ceil((mark.at + IN_FLIGHT_WINDOW_MS - now) / 1000));

  return (
    <section role="alert" className="rounded-xl border border-bd-border bg-surface p-4 text-sm text-foreground">
      <h2 className="font-bold">Venta anterior sin confirmar</h2>
      {state.kind === "checking" && <p className="mt-1 text-muted">Comprobando con el servidor si se registró…</p>}
      {state.kind === "created" && (
        <p className="mt-1">
          La venta anterior <strong>sí se registró</strong>: pedido #{state.orderId} por S/ {state.total}
          {state.branch ? ` en ${state.branch}` : ""}. No la cobres de nuevo.
        </p>
      )}
      {state.kind === "not_created" && (
        <p className="mt-1">
          {state.recorded ? "Quedó registrado que no se cobró. " : "La venta anterior no se registró. "}
          Vuelve a ingresar sus productos y confírmala.
        </p>
      )}
      {state.kind === "uncertain" && (
        <p className="mt-1">
          {state.why === "too_soon"
            ? "El servidor todavía no tiene esa venta, pero puede estar llegando. Si pasó menos de dos minutos, vuelve a comprobar antes de cobrar."
            : "No se pudo comprobar si la venta anterior se registró. No la cobres de nuevo sin comprobarlo."}
        </p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        {state.kind === "uncertain" && (
          <>
            <button type="button" onClick={onRecheck} className={internalButtonClass}>Volver a comprobar</button>
            <button
              type="button"
              onClick={onDecideNotCharged}
              disabled={waitS > 0}
              className={internalButtonClass}
            >
              {waitS > 0 ? `Confirmo que no se cobró (en ${waitS} s)` : "Confirmo que no se cobró"}
            </button>
          </>
        )}
        {(state.kind === "created" || state.kind === "not_created") && (
          <button type="button" onClick={onAcknowledge} className={internalPrimaryButtonClass}>Entendido</button>
        )}
      </div>
    </section>
  );
}
