import Link from "next/link";

export default function NotFound() {
  return (
    <main className="min-h-[65vh] bg-background px-6 py-16 text-foreground">
      <div className="mx-auto max-w-3xl border-b border-bd-border pb-10">
        <span className="section-label">404</span>
        <h1 className="mt-3 font-display text-4xl font-semibold leading-[1.08] tracking-tight sm:text-6xl">
          Página no encontrada
        </h1>
        <p className="mt-5 max-w-xl text-sm leading-7 text-muted sm:text-base">
          La dirección puede haber cambiado o el contenido ya no está disponible.
        </p>
        <div className="mt-8 flex flex-col gap-3 sm:flex-row">
          <Link
            href="/"
            className="inline-flex min-h-12 items-center justify-center rounded-full bg-foreground px-6 py-3 text-sm font-bold uppercase tracking-[0.06em] text-background transition hover:opacity-90"
          >
            Volver al inicio
          </Link>
          <Link
            href="/product"
            className="inline-flex min-h-12 items-center justify-center rounded-xl border border-bd-border px-6 py-3 text-sm font-bold text-foreground transition hover:border-foreground/25"
          >
            Ver catálogo
          </Link>
        </div>
      </div>
    </main>
  );
}
