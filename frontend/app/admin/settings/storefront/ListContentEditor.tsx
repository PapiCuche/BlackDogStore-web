"use client";

/**
 * M12F.1 — servicios, preguntas y métricas, editables por el taller.
 *
 * UN EDITOR Y NO TRES. Los tres contenidos tienen la misma vida —crear, editar,
 * activar, ordenar, borrar— y sólo se diferencian en qué campos muestran. Tres
 * copias del mismo formulario son tres sitios donde arreglar el mismo fallo,
 * con dos de ellos olvidados.
 *
 * DESACTIVAR ANTES QUE BORRAR. El botón principal para dejar de mostrar algo es
 * el interruptor; borrar está, pero es la acción secundaria y pide confirmación.
 */

import { useCallback, useEffect, useState } from "react";
import {
  ContentValidationError,
  deleteStorefrontListRow,
  fetchStorefrontList,
  saveStorefrontListRow,
  type StorefrontListKind,
  type StorefrontListRow,
} from "../../lib/internal-api";

type FieldSpec = {
  name: keyof StorefrontListRow;
  label: string;
  as?: "input" | "textarea";
  hint?: string;
  maxLength?: number;
  full?: boolean;
};

const SHAPES: Record<StorefrontListKind, { singular: string; fields: FieldSpec[] }> = {
  services: {
    singular: "servicio",
    fields: [
      { name: "title", label: "Nombre", maxLength: 80 },
      { name: "highlight", label: "Etiqueta", maxLength: 40, hint: "Corta. Vacía no se pinta." },
      {
        name: "description", label: "Descripción", as: "textarea", maxLength: 400, full: true,
      },
      { name: "devices_text", label: "Equipos", maxLength: 120, hint: "Ej.: iPhone · iPad" },
      {
        name: "estimated_time_text", label: "Tiempo estimado", maxLength: 40,
        // El rótulo dice «estimado» y la página también. El manual pide
        // informar que el tiempo puede variar según equipo, falla y repuesto.
        hint: "Se publica como estimación, no como compromiso.",
      },
    ],
  },
  faqs: {
    singular: "pregunta",
    fields: [
      { name: "question", label: "Pregunta", maxLength: 200, full: true },
      { name: "answer", label: "Respuesta", as: "textarea", maxLength: 1200, full: true },
    ],
  },
  metrics: {
    singular: "métrica",
    fields: [
      { name: "value", label: "Cifra", maxLength: 24, hint: "Lo grande: «+1.200», «24 h»." },
      { name: "label", label: "Qué significa", maxLength: 60 },
    ],
  },
};

const FIELD_CLASS =
  "mt-1 w-full rounded-lg border border-bd-border bg-background px-3 py-2 text-sm text-foreground outline-none transition-colors focus:border-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary";

function rowTitle(kind: StorefrontListKind, row: StorefrontListRow): string {
  if (kind === "faqs") return row.question ?? "";
  if (kind === "metrics") return `${row.value ?? ""} · ${row.label ?? ""}`;
  return row.title ?? "";
}

export function ListContentEditor({
  kind, companyId, canManage, onNotice,
}: {
  kind: StorefrontListKind;
  companyId: number | null;
  canManage: boolean;
  onNotice: (message: string) => void;
}) {
  const shape = SHAPES[kind];
  const [rows, setRows] = useState<StorefrontListRow[]>([]);
  const [editing, setEditing] = useState<StorefrontListRow | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchStorefrontList(kind, companyId);
      setRows(data.results);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cargar.");
    } finally {
      setLoading(false);
    }
  }, [kind, companyId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function toggleActive(row: StorefrontListRow) {
    try {
      await saveStorefrontListRow(
        kind, { id: row.id, is_active: !row.is_active }, companyId,
      );
      onNotice(row.is_active ? "Dejó de mostrarse." : "Vuelve a mostrarse.");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar.");
    }
  }

  async function remove(row: StorefrontListRow) {
    // Borrar es irreversible y el interruptor cubre el caso normal, así que
    // esto pregunta antes.
    if (!window.confirm(`¿Borrar esta ${shape.singular}? No se puede deshacer.`)) return;
    try {
      await deleteStorefrontListRow(kind, row.id, companyId);
      onNotice("Borrado.");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo borrar.");
    }
  }

  if (loading) return <p className="text-sm text-muted">Cargando…</p>;

  return (
    <div>
      {error ? (
        <p role="alert" className="mb-4 rounded-xl border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger">
          {error}
        </p>
      ) : null}

      {canManage ? (
        <button
          type="button"
          onClick={() => setEditing({ id: 0, is_active: true, sort_order: rows.length * 10 })}
          className="mb-4 min-h-11 rounded-full border border-bd-border px-5 text-sm font-semibold text-foreground transition-colors hover:bg-surface-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        >
          Añadir {shape.singular}
        </button>
      ) : null}

      {editing ? (
        <RowForm
          kind={kind}
          row={editing}
          companyId={companyId}
          onCancel={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            onNotice("Guardado.");
            await load();
          }}
        />
      ) : null}

      {rows.length === 0 ? (
        <p className="text-sm text-muted">
          Ninguna. Mientras esté vacío, este bloque no aparece en la web.
        </p>
      ) : (
        <ul className="space-y-2">
          {rows.map((row) => (
            <li
              key={row.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-bd-border bg-surface p-4"
            >
              <div className="min-w-0 flex-1">
                <p className={`truncate text-sm font-semibold ${row.is_active ? "text-foreground" : "text-muted line-through"}`}>
                  {rowTitle(kind, row)}
                </p>
                {!row.is_active ? (
                  <p className="mt-0.5 text-xs text-muted">No se muestra en la web.</p>
                ) : null}
              </div>
              {canManage ? (
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => setEditing(row)}
                    className="min-h-11 rounded-full border border-bd-border px-4 text-xs font-semibold text-foreground transition-colors hover:bg-surface-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                  >
                    Editar
                  </button>
                  <button
                    type="button"
                    onClick={() => toggleActive(row)}
                    aria-pressed={row.is_active}
                    className="min-h-11 rounded-full border border-bd-border px-4 text-xs font-semibold text-muted transition-colors hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                  >
                    {row.is_active ? "Ocultar" : "Mostrar"}
                  </button>
                  <button
                    type="button"
                    onClick={() => remove(row)}
                    className="min-h-11 rounded-full border border-bd-border px-4 text-xs font-semibold text-muted transition-colors hover:text-danger focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                  >
                    Borrar
                  </button>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function RowForm({
  kind, row, companyId, onCancel, onSaved,
}: {
  kind: StorefrontListKind;
  row: StorefrontListRow;
  companyId: number | null;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const shape = SHAPES[kind];
  const [draft, setDraft] = useState<Record<string, string | number | boolean>>(() => {
    const base: Record<string, string | number | boolean> = {
      is_active: row.is_active,
      sort_order: row.sort_order,
    };
    for (const f of shape.fields) base[f.name as string] = (row[f.name] as string) ?? "";
    return base;
  });
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [saving, setSaving] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setErrors({});
    try {
      await saveStorefrontListRow(
        kind,
        { ...(draft as Partial<StorefrontListRow>), ...(row.id ? { id: row.id } : {}) },
        companyId,
      );
      onSaved();
    } catch (err) {
      if (err instanceof ContentValidationError) setErrors(err.errors);
      else setErrors({ __all__: [err instanceof Error ? err.message : "Error"] });
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="mb-4 grid gap-4 rounded-xl border border-bd-border bg-surface p-4 sm:grid-cols-2"
    >
      {shape.fields.map((f) => {
        const id = `lc-${kind}-${String(f.name)}`;
        const problem = errors[f.name as string];
        const Tag = f.as ?? "input";
        return (
          <label key={String(f.name)} htmlFor={id} className={f.full ? "block sm:col-span-2" : "block"}>
            <span className="text-xs font-semibold uppercase tracking-wider text-muted">
              {f.label}
            </span>
            <Tag
              id={id}
              value={String(draft[f.name as string] ?? "")}
              maxLength={f.maxLength}
              {...(f.as === "textarea" ? { rows: 3 } : { type: "text" })}
              onChange={(e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
                setDraft((d) => ({ ...d, [f.name as string]: e.target.value }))
              }
              aria-invalid={problem ? true : undefined}
              aria-describedby={problem ? `${id}-error` : f.hint ? `${id}-hint` : undefined}
              className={FIELD_CLASS}
            />
            {f.hint && !problem ? (
              <span id={`${id}-hint`} className="mt-1 block text-xs text-muted">{f.hint}</span>
            ) : null}
            {problem ? (
              <span id={`${id}-error`} className="mt-1 block text-xs text-danger">
                {problem.join(" ")}
              </span>
            ) : null}
          </label>
        );
      })}

      <label htmlFor={`lc-${kind}-order`} className="block">
        <span className="text-xs font-semibold uppercase tracking-wider text-muted">Orden</span>
        <input
          id={`lc-${kind}-order`}
          type="number"
          value={String(draft.sort_order ?? 0)}
          onChange={(e) => setDraft((d) => ({ ...d, sort_order: Number(e.target.value) || 0 }))}
          className={FIELD_CLASS}
        />
      </label>

      {errors.__all__ ? (
        <p role="alert" className="text-sm text-danger sm:col-span-2">{errors.__all__.join(" ")}</p>
      ) : null}

      <div className="flex flex-wrap gap-3 sm:col-span-2">
        <button
          type="submit"
          disabled={saving}
          className="min-h-11 rounded-full bg-foreground px-6 text-sm font-semibold text-background transition-colors hover:bg-foreground/85 disabled:opacity-60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        >
          {saving ? "Guardando…" : "Guardar"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="min-h-11 rounded-full border border-bd-border px-6 text-sm font-semibold text-muted transition-colors hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        >
          Cancelar
        </button>
      </div>
    </form>
  );
}
