"use client";

import Link from "next/link";

export default function AdminError({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main id="admin-main-content" tabIndex={-1} className="outline-none min-h-[70vh] bg-background px-5 py-10 text-foreground sm:px-8">
      <div className="mx-auto max-w-3xl rounded-xl border border-red-500/20 bg-surface p-6">
        <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-muted">
          Control interno
        </p>
        <h1 className="mt-2 font-display text-2xl font-extrabold tracking-[-0.03em]">
          No pudimos cargar este módulo
        </h1>
        <p className="mt-3 text-sm leading-6 text-muted">
          Reintenta la vista antes de repetir una operación. Las acciones sensibles deben confirmarse sólo cuando el sistema muestre su resultado.
        </p>
        <div className="mt-6 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={reset}
            className="rounded-lg bg-primary px-4 py-2.5 text-sm font-bold text-background transition hover:opacity-90"
          >
            Reintentar
          </button>
          <Link
            href="/admin"
            className="rounded-lg border border-bd-border bg-background px-4 py-2.5 text-sm font-semibold text-foreground transition hover:border-foreground/25"
          >
            Volver al dashboard
          </Link>
        </div>
      </div>
    </main>
  );
}
