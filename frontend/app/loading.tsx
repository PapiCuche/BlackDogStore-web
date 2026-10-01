export default function Loading() {
  return (
    <main className="min-h-[60vh] bg-background px-6 py-12 text-foreground" aria-busy="true">
      <div className="mx-auto max-w-7xl" role="status" aria-label="Cargando contenido">
        <span className="sr-only">Cargando contenido…</span>
        <div className="h-3 w-24 animate-pulse rounded-full bg-foreground/[0.08]" />
        <div className="mt-5 h-10 w-full max-w-xl animate-pulse rounded-xl bg-foreground/[0.06]" />
        <div className="mt-3 h-4 w-full max-w-md animate-pulse rounded-full bg-foreground/[0.04]" />
        <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2, 3, 4, 5].map((item) => (
            <div
              key={item}
              className="h-44 animate-pulse rounded-2xl border border-bd-border bg-surface"
            />
          ))}
        </div>
      </div>
    </main>
  );
}
