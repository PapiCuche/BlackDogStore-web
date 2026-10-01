export default function AdminLoading() {
  return (
    <main id="admin-main-content" tabIndex={-1} className="outline-none min-h-[70vh] bg-background px-5 py-8 text-foreground sm:px-8" aria-busy="true">
      <div className="mx-auto max-w-7xl" role="status" aria-label="Cargando control interno">
        <span className="sr-only">Cargando control interno…</span>
        <div className="border-b border-bd-border pb-6">
          <div className="h-3 w-24 animate-pulse rounded-full bg-foreground/[0.08]" />
          <div className="mt-3 h-8 w-64 max-w-full animate-pulse rounded-lg bg-foreground/[0.06]" />
          <div className="mt-3 h-4 w-96 max-w-full animate-pulse rounded-full bg-foreground/[0.04]" />
        </div>
        <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2, 3, 4, 5].map((item) => (
            <div key={item} className="h-28 animate-pulse rounded-xl border border-bd-border bg-surface" />
          ))}
        </div>
      </div>
    </main>
  );
}
