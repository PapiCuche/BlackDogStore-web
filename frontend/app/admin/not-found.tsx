import Link from "next/link";

export default function AdminNotFound() {
  return (
    <main
      id="admin-main-content"
      tabIndex={-1}
      className="internal-ui-fonts min-h-screen bg-background px-5 py-12 text-foreground outline-none sm:px-8"
    >
      <div className="mx-auto max-w-3xl border-b border-bd-border pb-8">
        <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-muted">404 · Control interno</p>
        <h1 className="mt-2 font-display text-3xl font-extrabold tracking-[-0.03em]">
          Módulo no encontrado
        </h1>
        <p className="mt-3 max-w-xl text-sm leading-6 text-muted">
          La ruta interna no existe o dejó de estar disponible. La navegación sólo muestra módulos implementados y accesibles.
        </p>
        <Link
          href="/admin"
          className="mt-6 inline-flex rounded-lg bg-primary px-4 py-2.5 text-sm font-bold text-background transition hover:opacity-90"
        >
          Volver al dashboard
        </Link>
      </div>
    </main>
  );
}
