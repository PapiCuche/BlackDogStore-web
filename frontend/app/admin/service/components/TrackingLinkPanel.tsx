"use client";

/**
 * TRACKING — the link that is handed to the customer.
 *
 * WHOEVER HOLDS THE LINK CAN ANSWER THE QUOTE AS THE CUSTOMER. So opening the
 * order shows only that a link exists and how often it was opened. The link
 * itself is asked for with a button; the server gives it only to somebody who
 * may record the customer's decision, and writes down who asked.
 *
 * Replacing or turning it off changes who can see the order and needs
 * `service.orders.manage`. The server checks all of it; this only decides what
 * to draw.
 */

import { useCallback, useEffect, useState } from "react";

import {
  fetchTrackingLink, revealTrackingLink, revokeTrackingLink, rotateTrackingLink,
  type ServiceTrackingLink,
} from "@/app/lib/service-console";

import { Button, Confirm, dateTime, ErrorNote } from "./ServiceUi";

function opened(link: ServiceTrackingLink): string {
  if (link.view_count === 0) return "El cliente todavía no lo abrió.";
  const times = link.view_count === 1 ? "1 vez" : `${link.view_count} veces`;
  return `Abierto ${times} · última vez ${dateTime(link.last_viewed_at)}`;
}

export function TrackingLinkPanel({
  slug, orderId, mayManage,
}: { slug: string; orderId: number; mayManage: boolean }) {
  const [link, setLink] = useState<ServiceTrackingLink | null>(null);
  const [path, setPath] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [working, setWorking] = useState(false);
  const [copied, setCopied] = useState(false);

  /** Any change to the link forgets the one on screen: it may no longer be valid. */
  const change = useCallback(async (action: () => Promise<ServiceTrackingLink>) => {
    setWorking(true);
    setError(null);
    setCopied(false);
    setPath(null);
    try {
      setLink(await action());
    } catch (err) {
      setError(err);
    } finally {
      setWorking(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchTrackingLink(slug, orderId).then(
      (loaded) => { if (!cancelled) setLink(loaded); },
      (err) => { if (!cancelled) setError(err); },
    );
    return () => { cancelled = true; };
  }, [slug, orderId]);

  async function reveal() {
    setWorking(true);
    setError(null);
    try {
      setPath((await revealTrackingLink(slug, orderId)).path);
    } catch (err) {
      setError(err);
    } finally {
      setWorking(false);
    }
  }

  if (!link) {
    return error ? <ErrorNote error={error} /> : <p className="text-sm text-muted">Cargando enlace…</p>;
  }

  if (!link.active) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-muted">
          Esta orden no tiene un enlace activo: el anterior dejó de funcionar.
        </p>
        {mayManage ? (
          <Button tone="primary" disabled={working} onClick={() => void change(() => rotateTrackingLink(slug, orderId))}>
            Crear enlace nuevo
          </Button>
        ) : null}
        <ErrorNote error={error} />
      </div>
    );
  }

  const url = path ? `${window.location.origin}${path}` : "";

  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      setError(new Error("No se pudo copiar. Selecciona el enlace y cópialo a mano."));
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted">{opened(link)}</p>

      {path ? (
        <label className="block text-xs text-foreground/50">
          Enlace de seguimiento
          <input
            readOnly
            value={url}
            onFocus={(e) => e.currentTarget.select()}
            className="mt-1.5 w-full rounded-xl border border-bd-border bg-background px-3 py-2.5 font-mono text-xs text-foreground outline-none transition-colors focus:border-foreground/25"
          />
        </label>
      ) : link.can_reveal ? (
        <p className="text-xs text-muted">
          Quien tiene el enlace puede responder la cotización en nombre del cliente. Pedirlo queda
          registrado con tu usuario.
        </p>
      ) : (
        <p className="text-xs text-muted">
          Tu rol no entrega este enlace: quien lo tiene puede responder la cotización en nombre del
          cliente. Pídelo a quien registra las decisiones del cliente.
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {path ? (
          <>
            <Button tone="primary" onClick={() => void copy()} disabled={working}>Copiar enlace</Button>
            <a
              href={path}
              target="_blank"
              rel="noreferrer"
              className="rounded-xl border border-bd-border px-3 py-2 text-xs font-semibold text-muted transition-colors hover:border-foreground/25 hover:text-foreground"
            >
              Ver como el cliente
            </a>
          </>
        ) : link.can_reveal ? (
          <Button tone="primary" onClick={() => void reveal()} disabled={working}>Mostrar enlace</Button>
        ) : null}
        {mayManage ? (
          <>
            <Confirm
              label="Reemplazar enlace"
              question="¿Reemplazarlo? El enlace anterior dejará de funcionar."
              disabled={working}
              onConfirm={() => void change(() => rotateTrackingLink(slug, orderId))}
            />
            <Confirm
              label="Desactivar enlace"
              question="¿Desactivarlo? El cliente dejará de ver la orden."
              tone="danger"
              disabled={working}
              onConfirm={() => void change(() => revokeTrackingLink(slug, orderId))}
            />
          </>
        ) : null}
        {copied ? <span role="status" className="text-xs text-success">Copiado</span> : null}
      </div>
      <ErrorNote error={error} />
    </div>
  );
}
