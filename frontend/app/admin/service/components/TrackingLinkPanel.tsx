"use client";

/**
 * TRACKING — the link reception hands to the customer.
 *
 * Whoever can open the order can copy it: that is how it reaches a message.
 * Replacing or turning it off changes who can see the order and needs
 * `service.orders.manage`; the server checks, this only decides what to draw.
 */

import { useCallback, useEffect, useState } from "react";

import {
  fetchTrackingLink, revokeTrackingLink, rotateTrackingLink, type ServiceTrackingLink,
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
  const [error, setError] = useState<unknown>(null);
  const [working, setWorking] = useState(false);
  const [copied, setCopied] = useState(false);

  const act = useCallback(async (action: () => Promise<ServiceTrackingLink>) => {
    setWorking(true);
    setError(null);
    setCopied(false);
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

  if (!link) {
    return error ? <ErrorNote error={error} /> : <p className="text-sm text-muted">Cargando enlace…</p>;
  }

  if (!link.active || !link.path) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-muted">
          Esta orden no tiene un enlace activo: el anterior dejó de funcionar.
        </p>
        {mayManage ? (
          <Button tone="primary" disabled={working} onClick={() => void act(() => rotateTrackingLink(slug, orderId))}>
            Crear enlace nuevo
          </Button>
        ) : null}
        <ErrorNote error={error} />
      </div>
    );
  }

  const url = `${window.location.origin}${link.path}`;

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
      <label className="block text-xs text-foreground/50">
        Enlace de seguimiento
        <input
          readOnly
          value={url}
          onFocus={(e) => e.currentTarget.select()}
          className="mt-1.5 w-full rounded-xl border border-bd-border bg-background px-3 py-2.5 font-mono text-xs text-foreground outline-none transition-colors focus:border-foreground/25"
        />
      </label>
      <p className="text-xs text-muted">{opened(link)}</p>
      <div className="flex flex-wrap items-center gap-2">
        <Button tone="primary" onClick={() => void copy()} disabled={working}>Copiar enlace</Button>
        <a
          href={link.path}
          target="_blank"
          rel="noreferrer"
          className="rounded-xl border border-bd-border px-3 py-2 text-xs font-semibold text-muted transition-colors hover:border-foreground/25 hover:text-foreground"
        >
          Ver como el cliente
        </a>
        {mayManage ? (
          <>
            <Confirm
              label="Reemplazar enlace"
              question="¿Reemplazarlo? El enlace anterior dejará de funcionar."
              disabled={working}
              onConfirm={() => void act(() => rotateTrackingLink(slug, orderId))}
            />
            <Confirm
              label="Desactivar enlace"
              question="¿Desactivarlo? El cliente dejará de ver la orden."
              tone="danger"
              disabled={working}
              onConfirm={() => void act(() => revokeTrackingLink(slug, orderId))}
            />
          </>
        ) : null}
        {copied ? <span role="status" className="text-xs text-success">Copiado</span> : null}
      </div>
      <ErrorNote error={error} />
    </div>
  );
}
