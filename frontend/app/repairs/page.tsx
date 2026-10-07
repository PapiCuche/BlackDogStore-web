"use client";

/**
 * «Mis reparaciones» — las órdenes de servicio de quien inició sesión.
 *
 * La lista es del servidor: las órdenes del cliente que esta cuenta ES. Una
 * orden que se dejó en el mostrador sin cuenta se suma presentando su enlace
 * de seguimiento; nada se vincula por correo, teléfono o nombre.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { getCurrentUser } from "../lib/auth";
import { claimRepair, fetchMyRepairs, tokenFromInput, type AccountRepair } from "../lib/tracking";

function day(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString("es-PE", { dateStyle: "medium" });
}

export default function RepairsPage() {
  const router = useRouter();
  const [repairs, setRepairs] = useState<AccountRepair[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [link, setLink] = useState("");
  const [documentNumber, setDocumentNumber] = useState("");
  const [claiming, setClaiming] = useState(false);
  const [claimError, setClaimError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRepairs(await fetchMyRepairs());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudieron cargar tus reparaciones.");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const user = await getCurrentUser();
      if (cancelled) return;
      if (!user) {
        router.push("/auth");
        return;
      }
      await load();
    })();
    return () => { cancelled = true; };
  }, [router, load]);

  async function claim(event: React.FormEvent) {
    event.preventDefault();
    setClaiming(true);
    setClaimError(null);
    try {
      await claimRepair(tokenFromInput(link), documentNumber.trim());
      setLink("");
      setDocumentNumber("");
      await load();
    } catch (err) {
      setClaimError(err instanceof Error ? err.message : "No se pudo agregar la orden a tu cuenta.");
    } finally {
      setClaiming(false);
    }
  }

  return (
    <main className="mx-auto w-full max-w-3xl space-y-6 px-4 py-10 sm:py-14">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">Mis reparaciones</h1>
        <p className="mt-2 text-sm text-muted">
          El avance de los equipos que dejaste en servicio técnico.{" "}
          <Link href="/orders" className="font-semibold text-foreground underline underline-offset-4">
            Ver mis pedidos
          </Link>
        </p>
      </header>

      {error ? (
        <p role="alert" className="rounded-xl border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger">
          {error}
        </p>
      ) : null}

      {repairs === null && !error ? <p className="text-sm text-muted" role="status">Cargando…</p> : null}

      {repairs !== null && repairs.length === 0 ? (
        <p className="rounded-2xl border border-bd-border bg-surface p-6 text-sm text-muted">
          Todavía no hay reparaciones en tu cuenta. Si dejaste un equipo en la tienda, agrégalo
          abajo con el enlace de seguimiento que te entregaron y tu documento.
        </p>
      ) : null}

      {repairs && repairs.length > 0 ? (
        <ul className="space-y-3">
          {repairs.map((repair) => (
            <li key={repair.number} className="rounded-2xl border border-bd-border bg-surface p-5">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  {repair.tracking_path ? (
                    // UN ENLACE DE VERDAD, no `<Link>`: la dirección de seguimiento
                    // lleva un token. Con una navegación completa la página se
                    // abre en un documento nuevo, donde no hay cargado ningún
                    // script de medición que pueda leer esa dirección.
                    <a
                      href={repair.tracking_path}
                      className="text-base font-semibold text-foreground underline-offset-4 hover:underline"
                    >
                      {repair.number}
                    </a>
                  ) : (
                    <span className="text-base font-semibold text-foreground">{repair.number}</span>
                  )}
                  <p className="mt-1 text-sm text-muted">{repair.device_summary}</p>
                  <p className="mt-1 text-xs text-muted">Recibido el {day(repair.received_at)}</p>
                </div>
                <span className="rounded-full border border-bd-border px-3 py-1 text-xs font-semibold text-foreground">
                  {repair.status_label}
                </span>
              </div>
            </li>
          ))}
        </ul>
      ) : null}

      <form onSubmit={claim} className="rounded-2xl border border-bd-border bg-surface p-5">
        <h2 className="text-sm font-semibold text-foreground">Agregar una orden</h2>
        <p className="mt-1 text-xs text-muted">
          Pega el enlace de seguimiento que te dio la tienda y escribe el documento con el que
          dejaste el equipo. Desde entonces verás aquí todas tus reparaciones en esta tienda.
        </p>
        <label className="mt-3 block text-xs text-muted">
          Enlace de seguimiento
          <input
            value={link}
            onChange={(e) => setLink(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            className="mt-1.5 w-full rounded-xl border border-bd-border bg-background px-3 py-2.5 text-sm text-foreground outline-none transition-colors focus:border-foreground/25"
          />
        </label>
        <label className="mt-3 block text-xs text-muted">
          Número de documento
          <input
            value={documentNumber}
            onChange={(e) => setDocumentNumber(e.target.value)}
            autoComplete="off"
            inputMode="text"
            className="mt-1.5 w-full rounded-xl border border-bd-border bg-background px-3 py-2.5 text-sm text-foreground outline-none transition-colors focus:border-foreground/25"
          />
        </label>
        {claimError ? <p role="alert" className="mt-2 text-xs text-danger">{claimError}</p> : null}
        <button
          type="submit"
          disabled={claiming || !link.trim() || !documentNumber.trim()}
          className="mt-3 rounded-xl border border-primary bg-primary px-4 py-2.5 text-sm font-semibold text-background transition-opacity hover:opacity-90 disabled:opacity-40"
        >
          Agregar a mi cuenta
        </button>
      </form>
    </main>
  );
}
