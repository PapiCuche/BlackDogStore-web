"use client";

/**
 * ÁREAS — la organización de la empresa.
 *
 * ÁREA NO ES ROL. El área dice DÓNDE trabaja alguien; el rol, QUÉ puede hacer.
 * Alguien puede estar en Servicio Técnico con un rol que no le permita reparar
 * nada, y eso es correcto: el área organiza, no autoriza.
 *
 * DESACTIVAR NO REVOCA NADA. Un área desactivada deja de ofrecerse para
 * asignaciones nuevas, pero no quita roles, no quita permisos y no mueve a
 * nadie. Quien ya estaba, sigue — y el historial se conserva.
 *
 * Y NO SE BORRA. Un área con historia es la explicación de asignaciones
 * pasadas; borrarla dejaría un hueco que nadie puede reconstruir.
 */

import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "../components/AdminShell";
import {
  InternalControlGuard,
  type InternalContext,
} from "../components/InternalControlGuard";
import {
  createArea,
  fetchAreas,
  updateArea,
  type CompanyAreaRow,
} from "../../lib/staff";

export default function AreasPage() {
  return (
    <InternalControlGuard>{(ctx) => <AreasScreen ctx={ctx} />}</InternalControlGuard>
  );
}

function AreasScreen({ ctx }: { ctx: InternalContext }) {
  // Ver la nota en la pantalla de Personal: `selectedCompanyId` es el selector
  // del master y vale `null` para quien pertenece a una sola empresa.
  const companyId = ctx.dashboard?.company?.id ?? null;
  const [areas, setAreas] = useState<CompanyAreaRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [confirming, setConfirming] = useState<CompanyAreaRow | null>(null);

  const load = useCallback(async () => {
    if (!companyId) {
      setAreas([]);
      setError('Selecciona una empresa para ver sus áreas.');
      return;
    }
    try {
      setAreas(await fetchAreas(companyId));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudieron cargar las áreas.");
      setAreas([]);
    }
  }, [companyId]);

  useEffect(() => {
    let cancelled = false;
    void (async () => { if (!cancelled) await load(); })();
    return () => { cancelled = true; };
  }, [load]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!companyId || !name.trim() || busy) return;
    setBusy("create");
    setError(null);
    try {
      await createArea(companyId, {
        name: name.trim(),
        description: description.trim(),
      });
      setName(""); setDescription("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo crear el área.");
    } finally {
      setBusy(null);
    }
  }

  async function toggle(area: CompanyAreaRow) {
    // ANÁLISIS DE IMPACTO ANTES DE DESACTIVAR. Si hay gente asignada, se
    // pregunta: desactivar un área con cinco personas dentro no debería ser un
    // clic sin consecuencia visible.
    if (area.is_active && (area.member_count ?? 0) > 0 && confirming?.id !== area.id) {
      setConfirming(area);
      return;
    }
    setConfirming(null);
    setBusy(`toggle-${area.id}`);
    setError(null);
    try {
      await updateArea(area.id, { is_active: !area.is_active });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo actualizar el área.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
      <div className="space-y-6">
        <header>
          <h1 className="font-display text-2xl text-foreground">Áreas</h1>
          <p className="mt-1 text-sm text-muted">
            Organizan a las personas. Los permisos los da el rol, no el área.
          </p>
        </header>

        {error ? (
          <div role="alert" className="rounded-lg border border-danger-border bg-danger-surface px-4 py-3">
            <p className="text-sm text-danger">{error}</p>
          </div>
        ) : null}

        <form
          onSubmit={submit}
          className="rounded-xl border border-bd-border bg-surface p-4"
        >
          <h2 className="mb-3 text-sm font-semibold text-foreground">Nueva área</h2>
          <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
            <div className="min-w-0">
              <label htmlFor="area-name" className="mb-1 block text-[11px] font-semibold uppercase tracking-widest text-muted">
                Nombre
              </label>
              <input
                id="area-name" value={name} required
                onChange={(e) => setName(e.target.value)}
                placeholder="Taller, Postventa, Laboratorio…"
                className="min-h-11 w-full rounded-lg border border-bd-border bg-background px-3 text-sm text-foreground"
              />
            </div>
            <div className="min-w-0">
              <label htmlFor="area-desc" className="mb-1 block text-[11px] font-semibold uppercase tracking-widest text-muted">
                Descripción
              </label>
              <input
                id="area-desc" value={description}
                onChange={(e) => setDescription(e.target.value)}
                className="min-h-11 w-full rounded-lg border border-bd-border bg-background px-3 text-sm text-foreground"
              />
            </div>
            <button
              type="submit"
              disabled={!name.trim() || busy !== null}
              className="min-h-11 rounded-lg bg-foreground px-4 text-sm font-semibold text-background transition hover:bg-foreground/90 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {busy === "create" ? "Creando…" : "Crear área"}
            </button>
          </div>
        </form>

        {areas === null ? (
          <div className="flex items-center gap-3 text-sm text-muted">
            <div className="h-4 w-4 animate-spin rounded-full border-2 border-bd-border border-t-transparent" />
            Cargando áreas…
          </div>
        ) : !areas.length ? (
          <section className="rounded-xl border border-bd-border bg-surface p-8 text-center">
            <p className="text-sm text-muted">Esta empresa todavía no tiene áreas.</p>
          </section>
        ) : (
          <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {areas.map((area) => (
              <li
                key={area.id}
                className="min-w-0 rounded-xl border border-bd-border bg-surface p-4"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-semibold text-foreground">{area.name}</p>
                    {area.description ? (
                      <p className="mt-0.5 break-words text-xs text-muted">
                        {area.description}
                      </p>
                    ) : null}
                  </div>
                  <span
                    className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${
                      area.is_active
                        ? "bg-success-surface text-success"
                        : "bg-surface-2 text-muted"
                    }`}
                  >
                    {area.is_active ? "● Activa" : "○ Inactiva"}
                  </span>
                </div>

                <p className="mt-2 text-xs text-muted">
                  {area.member_count ?? 0} persona
                  {(area.member_count ?? 0) === 1 ? "" : "s"} asignada
                  {(area.member_count ?? 0) === 1 ? "" : "s"}
                </p>

                {confirming?.id === area.id ? (
                  <div className="mt-3 rounded-lg border border-warning-border bg-warning-surface px-3 py-2.5">
                    <p className="text-xs text-warning">
                      Esta área tiene {area.member_count} asignación
                      {area.member_count === 1 ? "" : "es"} activa
                      {area.member_count === 1 ? "" : "s"}. Desactivarla no quita
                      roles ni permisos: sólo deja de ofrecerse para asignaciones
                      nuevas.
                    </p>
                    <div className="mt-2 flex gap-2">
                      <button
                        type="button"
                        onClick={() => void toggle(area)}
                        className="min-h-11 rounded-lg bg-foreground px-3 text-xs font-semibold text-background"
                      >
                        Desactivar de todas formas
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirming(null)}
                        className="min-h-11 rounded-lg border border-bd-border px-3 text-xs text-foreground"
                      >
                        Cancelar
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => void toggle(area)}
                    disabled={busy !== null}
                    className="mt-3 min-h-11 w-full rounded-lg border border-bd-border px-3 text-sm text-foreground transition hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {busy === `toggle-${area.id}`
                      ? "Guardando…"
                      : area.is_active
                        ? "Desactivar"
                        : "Reactivar"}
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </AdminShell>
  );
}
