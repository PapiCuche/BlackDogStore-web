"use client";

/**
 * M12D — la galería de evidencias de una orden.
 *
 * EL BACKEND ES LA AUTORIDAD. Aquí se ocultan botones que el servidor
 * rechazaría, y eso es cortesía: la comprobación de verdad ocurre en cada
 * petición, por etapa. Si alguien fuerza el formulario recibe un 403.
 *
 * LAS IMÁGENES NO LLEGAN POR URL. El servidor manda `id`, nunca la clave del
 * objeto ni un enlace firmado que se pudiera reenviar. Cada `<img>` apunta al
 * endpoint de contenido, que vuelve a comprobar empresa, sucursal, autoridad,
 * visibilidad y anulación antes de servir un byte.
 *
 * SE ANULA, NO SE BORRA. El botón dice "Anular" porque eso es lo que hace: la
 * fila y el archivo se conservan. Llamarlo "Eliminar" prometería algo que no
 * ocurre, y la primera persona que lo pulsara creyendo que borra sería la que
 * descubriera la diferencia.
 */

import { useCallback, useEffect, useState } from "react";
import { API_BASE } from "../../../lib/api";
import { fetchWithAuth } from "../../../lib/auth";

export type Evidence = {
  id: number;
  stage: string;
  visibility: "internal" | "customer";
  mime_type: string;
  byte_size: number;
  width: number;
  height: number;
  created_at: string;
  uploaded_by: string;
  voided_at: string | null;
  void_reason: string | null;
};

/** Las etapas, y la capacidad que el backend pedirá para cada una. */
export const EVIDENCE_STAGES: { value: string; label: string; capability: string }[] = [
  { value: "intake", label: "Ingreso", capability: "service.orders.create" },
  { value: "diagnosis", label: "Diagnóstico", capability: "service.diagnostic.manage" },
  { value: "repair_before", label: "Antes de reparar", capability: "service.repair.manage" },
  { value: "repair_during", label: "Durante la reparación", capability: "service.repair.manage" },
  { value: "repair_after", label: "Después de reparar", capability: "service.repair.manage" },
  { value: "quality", label: "Control de calidad", capability: "service.quality.manage" },
  { value: "delivery", label: "Entrega", capability: "service.delivery.manage" },
  { value: "other", label: "Otras", capability: "service.orders.manage" },
];

export function stageLabel(value: string) {
  return EVIDENCE_STAGES.find((s) => s.value === value)?.label ?? value;
}

export function stageCapability(value: string) {
  return EVIDENCE_STAGES.find((s) => s.value === value)?.capability ?? null;
}

/** Agrupa por etapa respetando el orden del ciclo, no el alfabético. */
export function groupByStage(rows: Evidence[]) {
  return EVIDENCE_STAGES.map((stage) => ({
    stage,
    items: rows.filter((r) => r.stage === stage.value),
  })).filter((g) => g.items.length > 0);
}

function humanBytes(n: number) {
  return n >= 1024 * 1024
    ? `${(n / 1024 / 1024).toFixed(1)} MB`
    : `${Math.round(n / 1024)} KB`;
}

type Props = {
  slug: string;
  orderId: number;
  may: (capability: string) => boolean;
};

export function EvidenceGallery({ slug, orderId, may }: Props) {
  const base = `${API_BASE}/v1/internal/${slug}/service/orders/${orderId}/evidence`;

  const [rows, setRows] = useState<Evidence[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("intake");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [voiding, setVoiding] = useState<number | null>(null);
  const [reason, setReason] = useState("");

  const load = useCallback(async () => {
    try {
      const res = await fetchWithAuth(`${base}/`);
      if (!res.ok) throw new Error("No se pudo cargar la galería.");
      const body = await res.json();
      setRows(body.results);
      setError(null);
    } catch (err) {
      setRows([]);
      setError(err instanceof Error ? err.message : "No se pudo cargar.");
    }
  }, [base]);

  useEffect(() => {
    void load();
  }, [load]);

  // El objeto URL se libera al cambiar de archivo y al desmontar. Sin esto una
  // sesión larga de subidas va reteniendo cada imagen previsualizada.
  useEffect(() => {
    if (!file) {
      setPreview(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  async function upload() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("stage", stage);
      form.append("image", file);
      const res = await fetchWithAuth(`${base}/`, {
        method: "POST",
        body: form,
        // Un reintento del mismo envío no debe crear dos evidencias.
        headers: { "Idempotency-Key": `${orderId}-${stage}-${file.name}-${file.size}` },
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || "No se pudo subir la imagen.");
      }
      setFile(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo subir.");
    } finally {
      setBusy(false);
    }
  }

  async function act(id: number, action: string, payload?: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      const res = await fetchWithAuth(`${base}/${id}/${action}/`, {
        method: "POST",
        headers: payload ? { "Content-Type": "application/json" } : undefined,
        body: payload ? JSON.stringify(payload) : undefined,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || "No se pudo completar la acción.");
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo completar.");
    } finally {
      setBusy(false);
      setVoiding(null);
      setReason("");
    }
  }

  const canUploadHere = may(stageCapability(stage) ?? "");
  const groups = rows ? groupByStage(rows) : [];

  return (
    <div className="space-y-5">
      <div className="space-y-3 rounded-xl border border-bd-border bg-surface p-4">
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={stage}
            onChange={(e) => setStage(e.target.value)}
            className="rounded-lg border border-bd-border bg-transparent px-2 py-1.5 text-xs text-foreground"
          >
            {EVIDENCE_STAGES.map((s) => (
              <option key={s.value} value={s.value} className="bg-surface">
                {s.label}
              </option>
            ))}
          </select>
          <input
            type="file"
            accept="image/jpeg,image/png,image/webp,image/heic,image/heif"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="text-xs text-muted file:mr-2 file:rounded-lg file:border file:border-bd-border file:bg-surface file:px-2 file:py-1 file:text-xs file:text-foreground"
          />
          <button
            type="button"
            disabled={busy || !file || !canUploadHere}
            onClick={() => void upload()}
            className="rounded-lg bg-sky-500/90 px-3 py-1.5 text-xs font-medium text-on-status disabled:opacity-40"
          >
            Subir
          </button>
        </div>

        {!canUploadHere ? (
          <p className="text-[11px] text-amber-300/80">
            No tienes autoridad sobre la etapa «{stageLabel(stage)}».
          </p>
        ) : null}

        {preview ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={preview}
            alt="Vista previa"
            className="max-h-40 rounded-lg border border-bd-border"
          />
        ) : null}

        <p className="text-[11px] leading-5 text-muted">
          La plataforma optimiza la imagen automáticamente: se reorienta, se le
          quita la metadata —incluida la ubicación— y se guarda comprimida. No se
          conserva el archivo original de la cámara.
        </p>
      </div>

      {error ? (
        <p className="rounded-lg border border-red-500/30 bg-red-500/5 px-3 py-2 text-xs text-red-300">
          {error}
        </p>
      ) : null}

      {rows === null ? (
        <p className="text-xs text-muted">Cargando…</p>
      ) : rows.length === 0 ? (
        <p className="text-xs text-muted">Todavía no hay evidencias.</p>
      ) : (
        groups.map(({ stage: s, items }) => (
          <section key={s.value} className="space-y-2">
            <h4 className="text-[11px] font-medium uppercase tracking-wide text-muted">
              {s.label} · {items.length}
            </h4>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
              {items.map((e) => (
                <figure
                  key={e.id}
                  className={`overflow-hidden rounded-xl border ${
                    e.voided_at
                      ? "border-bd-border opacity-50"
                      : "border-bd-border"
                  }`}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={`${base}/${e.id}/content/`}
                    alt={`${s.label} · ${new Date(e.created_at).toLocaleDateString("es-PE")}`}
                    className="h-32 w-full bg-background/40 object-cover"
                    loading="lazy"
                  />
                  <figcaption className="space-y-1.5 px-2 py-2">
                    <div className="flex flex-wrap items-center gap-1">
                      {e.voided_at ? (
                        <span className="rounded bg-red-500/15 px-1.5 py-px text-[10px] text-red-300">
                          Anulada
                        </span>
                      ) : e.visibility === "customer" ? (
                        <span className="rounded bg-emerald-500/15 px-1.5 py-px text-[10px] text-emerald-300">
                          Visible al cliente
                        </span>
                      ) : (
                        <span className="rounded bg-surface px-1.5 py-px text-[10px] text-muted">
                          Interna
                        </span>
                      )}
                    </div>
                    <p className="text-[10px] text-muted">
                      {new Date(e.created_at).toLocaleString("es-PE")} ·{" "}
                      {e.width}×{e.height} · {humanBytes(e.byte_size)}
                    </p>
                    {e.void_reason ? (
                      <p className="text-[10px] text-muted">{e.void_reason}</p>
                    ) : null}

                    {!e.voided_at && may(s.capability) ? (
                      <div className="flex flex-wrap gap-1">
                        {e.visibility === "internal" ? (
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => void act(e.id, "publish-to-customer")}
                            className="rounded border border-bd-border px-1.5 py-px text-[10px] text-foreground/85 disabled:opacity-40"
                          >
                            Compartir con cliente
                          </button>
                        ) : (
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => void act(e.id, "hide-from-customer")}
                            className="rounded border border-bd-border px-1.5 py-px text-[10px] text-foreground/85 disabled:opacity-40"
                          >
                            Ocultar al cliente
                          </button>
                        )}
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => setVoiding(e.id)}
                          className="rounded border border-bd-border px-1.5 py-px text-[10px] text-muted disabled:opacity-40"
                        >
                          Anular
                        </button>
                      </div>
                    ) : null}

                    {voiding === e.id ? (
                      <div className="space-y-1">
                        <input
                          value={reason}
                          onChange={(ev) => setReason(ev.target.value)}
                          placeholder="Motivo de la anulación"
                          className="w-full rounded border border-bd-border bg-transparent px-1.5 py-1 text-[10px] text-foreground"
                        />
                        <div className="flex gap-1">
                          <button
                            type="button"
                            disabled={busy || !reason.trim()}
                            onClick={() => void act(e.id, "void", { reason })}
                            className="rounded bg-red-500/80 px-1.5 py-px text-[10px] text-on-status disabled:opacity-40"
                          >
                            Confirmar
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setVoiding(null);
                              setReason("");
                            }}
                            className="rounded px-1.5 py-px text-[10px] text-muted"
                          >
                            Cancelar
                          </button>
                        </div>
                        <p className="text-[10px] text-muted">
                          La evidencia se conserva; deja de estar disponible.
                        </p>
                      </div>
                    ) : null}
                  </figcaption>
                </figure>
              ))}
            </div>
          </section>
        ))
      )}
    </div>
  );
}
