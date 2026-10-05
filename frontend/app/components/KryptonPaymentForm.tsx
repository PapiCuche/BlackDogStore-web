"use client";

import { useEffect, useRef } from "react";

import { mountKryptonForm, type MiCuentaWebSession } from "../lib/payments";

/**
 * The payment form of «Mi Cuenta Web».
 *
 * This component draws a box and a sentence. The fields where a card is typed
 * are drawn by the gateway's own script inside `.kr-smart-form`: no card
 * number, expiry or security code is ever an input of this application.
 */
export default function KryptonPaymentForm({
  session,
  onSettled,
  onError,
}: {
  session: MiCuentaWebSession;
  onSettled: () => void;
  onError: (message: string) => void;
}) {
  const region = useRef<HTMLElement>(null);
  const settled = useRef(onSettled);
  const failed = useRef(onError);
  useEffect(() => {
    settled.current = onSettled;
    failed.current = onError;
  });

  useEffect(() => {
    let live = true;
    region.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
    mountKryptonForm(session, () => {
      if (live) settled.current();
    }).catch((error: unknown) => {
      if (live) failed.current(error instanceof Error ? error.message : "No se pudo abrir el pago.");
    });
    return () => {
      live = false;
      window.KR?.removeForms?.();
    };
    // One form per payment: the token is the identity of what is drawn.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.form_token]);

  return (
    <section
      ref={region}
      aria-label="Pago con tarjeta"
      className="mb-8 rounded-2xl border border-bd-border bg-surface p-5 sm:p-6"
    >
      <h2 className="text-sm font-semibold text-foreground">Pago con tarjeta</h2>
      <p className="mt-1 text-xs leading-5 text-muted">
        Los datos de la tarjeta se escriben en el formulario de la pasarela de pago. Esta tienda no
        los ve ni los guarda. Tu pedido queda confirmado cuando la pasarela nos avisa del pago.
      </p>
      <div className="mt-4 flex justify-center">
        <div className="kr-smart-form" kr-form-token={session.form_token} />
      </div>
    </section>
  );
}
