"use client";

import Link from "next/link";

export default function GlobalError({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main className="min-h-[65vh] bg-background px-6 py-16 text-foreground">
      <div className="mx-auto max-w-3xl rounded-2xl border border-danger-border bg-surface p-6 sm:p-8">
        <span className="section-label">Algo salió mal</span>
        <h1 className="mt-3 font-display text-3xl font-black italic uppercase tracking-[-0.035em] sm:text-5xl">
          No pudimos cargar esta vista
        </h1>
        <p className="mt-4 max-w-xl text-sm leading-7 text-muted">
          Puedes intentarlo de nuevo. Si el problema continúa, vuelve al inicio y conserva tu operación actual antes de repetir una acción sensible.
        </p>
        <div className="mt-7 flex flex-col gap-3 sm:flex-row">
          <button
            type="button"
            onClick={reset}
            className="min-h-12 rounded-xl bg-primary px-6 py-3 text-sm font-bold uppercase tracking-[0.06em] text-background transition hover:opacity-90"
          >
            Reintentar
          </button>
          <Link
            href="/"
            className="inline-flex min-h-12 items-center justify-center rounded-xl border border-bd-border px-6 py-3 text-sm font-bold text-foreground transition hover:border-foreground/25"
          >
            Ir al inicio
          </Link>
        </div>
      </div>
    </main>
  );
}
