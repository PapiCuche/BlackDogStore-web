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
 * VARIAS FOTOS, UNA SOLA VEZ. El técnico elige la etapa, toma o elige varias
 * fotos, anota lo que muestran y las sube juntas. Cada una viaja en su propia
 * petición, con su propia clave: la que falla se queda en la cola con su motivo
 * y se reintenta con la MISMA clave, así un reintento nunca duplica.
 *
 * SE ANULA, NO SE BORRA. El botón dice "Anular" porque eso es lo que hace: la
 * fila y el archivo se conservan. Llamarlo "Eliminar" prometería algo que no
 * ocurre, y la primera persona que lo pulsara creyendo que borra sería la que
 * descubriera la diferencia.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE } from "../../../lib/api";
import { fetchWithAuth } from "../../../lib/auth";
import { ImageDropzone } from "../../components/ImageDropzone";

export type Evidence = {
  id: number;
  stage: string;
  /** Lo que la foto muestra, dicho por quien la tomó. */
  caption: string;
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

type Stage = { value: string; label: string; capability: string };

/**
 * Las etapas, en el orden del ciclo, y la capacidad que el backend pedirá para
 * cada una. El servidor envía esta misma lista con la galería; ésta es la que
 * se usa mientras carga, y la que una prueba compara con la del servidor.
 */
export const EVIDENCE_STAGES: Stage[] = [
  { value: "intake", label: "Ingreso", capability: "service.orders.create" },
  { value: "diagnosis", label: "Diagnóstico", capability: "service.diagnostic.manage" },
  { value: "repair_before", label: "Antes de reparar", capability: "service.repair.manage" },
  { value: "repair_during", label: "Durante la reparación", capability: "service.repair.manage" },
  { value: "repair_after", label: "Después de reparar", capability: "service.repair.manage" },
  { value: "parts", label: "Repuestos", capability: "service.repair.manage" },
  { value: "quality", label: "Control de calidad", capability: "service.quality.manage" },
  { value: "ready", label: "Listo para entrega", capability: "service.delivery.manage" },
  { value: "delivery", label: "Entrega", capability: "service.delivery.manage" },
  { value: "warranty", label: "Garantía / reingreso", capability: "service.orders.create" },
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

function photos(n: number) {
  return `${n} foto${n === 1 ? "" : "s"}`;
}

/** Lo que el servidor acepta recibir. Aquí sólo ahorra un viaje. */
const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
const PHOTO_ACCEPT = "image/jpeg,image/png,image/webp,image/heic,image/heif";
const PHOTO_NAME = /\.(jpe?g|png|webp|heic|heif)$/i;

function rejectReason(file: File): string | null {
  // Algunos teléfonos entregan un HEIC sin tipo: entonces manda la extensión.
  const looksLikePhoto = file.type ? PHOTO_ACCEPT.split(",").includes(file.type) : PHOTO_NAME.test(file.name);
  if (!looksLikePhoto) return "no es una foto (JPEG, PNG, WebP o HEIC)";
  if (file.size > MAX_UPLOAD_BYTES) return "pesa más de 25 MB";
  return null;
}

function newKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
}

type Queued = {
  /** También es la clave de idempotencia: un reintento no crea otra evidencia. */
  key: string;
  file: File;
  preview: string;
  caption: string;
  status: "queued" | "uploading" | "failed";
  error?: string;
};

type Props = {
  slug: string;
  orderId: number;
  may: (capability: string) => boolean;
};

export function EvidenceGallery({ slug, orderId, may }: Props) {
  const base = `${API_BASE}/v1/internal/${slug}/service/orders/${orderId}/evidence`;

  const [rows, setRows] = useState<Evidence[] | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [stages, setStages] = useState<Stage[]>(EVIDENCE_STAGES);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("intake");
  const [queue, setQueue] = useState<Queued[]>([]);
  const [rejected, setRejected] = useState<string[]>([]);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [voiding, setVoiding] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const [sharing, setSharing] = useState<number | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [viewing, setViewing] = useState<number | null>(null);

  const label = (value: string) => stages.find((s) => s.value === value)?.label ?? value;
  const capability = (value: string) => stages.find((s) => s.value === value)?.capability ?? "";

  const load = useCallback(async () => {
    try {
      const res = await fetchWithAuth(`${base}/`);
      if (!res.ok) throw new Error("No se pudo cargar la galería.");
      const body = await res.json();
      setRows(body.results);
      if (Array.isArray(body.stages) && body.stages.length) setStages(body.stages);
      if (body.stage_counts) {
        setCounts(body.stage_counts);
      } else {
        const computed: Record<string, number> = {};
        for (const item of body.results as Evidence[]) {
          if (!item.voided_at) computed[item.stage] = (computed[item.stage] ?? 0) + 1;
        }
        setCounts(computed);
      }
      setError(null);
    } catch (err) {
      setRows([]);
      setError(err instanceof Error ? err.message : "No se pudo cargar.");
    }
  }, [base]);

  useEffect(() => {
    void load();
  }, [load]);

  // Cada vista previa es un object URL: se libera al quitar la foto, al subirla
  // y al desmontar. Sin esto una sesión larga retiene cada imagen en memoria.
  const previews = useRef(new Set<string>());
  useEffect(() => () => {
    for (const url of previews.current) URL.revokeObjectURL(url);
  }, []);

  function release(item: Queued) {
    URL.revokeObjectURL(item.preview);
    previews.current.delete(item.preview);
  }

  function addFiles(files: File[]) {
    const problems: string[] = [];
    const added: Queued[] = [];
    for (const file of files) {
      const why = rejectReason(file);
      if (why) {
        problems.push(`${file.name}: ${why}.`);
        continue;
      }
      const preview = URL.createObjectURL(file);
      previews.current.add(preview);
      added.push({ key: newKey(), file, preview, caption: "", status: "queued" });
    }
    setRejected(problems);
    if (added.length) setQueue((prev) => [...prev, ...added]);
  }

  function removeQueued(key: string) {
    setQueue((prev) => {
      const item = prev.find((entry) => entry.key === key);
      if (item) release(item);
      return prev.filter((entry) => entry.key !== key);
    });
  }

  async function uploadAll() {
    const pending = queue;
    if (!pending.length) return;
    setBusy(true);
    setError(null);
    setProgress({ done: 0, total: pending.length });
    let done = 0;
    for (const item of pending) {
      setQueue((prev) => prev.map((entry) => (entry.key === item.key ? { ...entry, status: "uploading", error: undefined } : entry)));
      try {
        const form = new FormData();
        form.append("stage", stage);
        form.append("image", item.file);
        form.append("caption", item.caption.trim());
        const res = await fetchWithAuth(`${base}/`, {
          method: "POST",
          body: form,
          // Un reintento del mismo envío no debe crear dos evidencias.
          headers: { "Idempotency-Key": item.key },
        });
        if (!res.ok) {
          const body = await res.json().catch(() => null);
          throw new Error(
            body?.detail || (res.status === 413 ? "La imagen es demasiado grande." : "No se pudo subir la imagen."),
          );
        }
        release(item);
        setQueue((prev) => prev.filter((entry) => entry.key !== item.key));
      } catch (err) {
        const message = err instanceof Error ? err.message : "No se pudo subir.";
        setQueue((prev) => prev.map((entry) => (entry.key === item.key ? { ...entry, status: "failed", error: message } : entry)));
      }
      done += 1;
      setProgress({ done, total: pending.length });
    }
    await load();
    setProgress(null);
    setBusy(false);
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
      setSharing(null);
      setReason("");
    }
  }

  async function saveCaption(id: number) {
    setBusy(true);
    setError(null);
    try {
      const res = await fetchWithAuth(`${base}/${id}/`, {
        method: "PATCH",
        body: JSON.stringify({ caption: draft.trim() }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || "No se pudo guardar la nota.");
      }
      setEditing(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar la nota.");
    } finally {
      setBusy(false);
    }
  }

  const canUploadHere = may(capability(stage));
  const groups = stages
    .map((entry) => ({ stage: entry, items: (rows ?? []).filter((r) => r.stage === entry.value) }))
    .filter((group) => group.items.length > 0);
  // El orden en que se ven es el orden en que se recorren en el visor.
  const ordered = groups.flatMap((group) => group.items);
  const inForce = (value: string) => (rows ?? []).filter((r) => r.stage === value && !r.voided_at);
  const before = inForce("repair_before").at(-1);
  const after = inForce("repair_after").at(-1);
  const describe = (e: Evidence) => e.caption || `${label(e.stage)} · ${new Date(e.created_at).toLocaleDateString("es-PE")}`;

  return (
    <div className="space-y-5">
      <div className="space-y-3 rounded-xl border border-bd-border bg-surface p-4">
        <label className="flex flex-wrap items-center gap-2 text-xs text-muted">
          Etapa
          <select
            aria-label="Etapa de las fotos"
            value={stage}
            disabled={busy}
            onChange={(e) => setStage(e.target.value)}
            className="min-h-11 rounded-lg border border-bd-border bg-background px-3 text-sm text-foreground"
          >
            {stages.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>

        {!canUploadHere ? (
          <p className="text-xs text-warning">
            No tienes autoridad sobre la etapa «{label(stage)}».
          </p>
        ) : null}

        <ImageDropzone
          label="Elegir fotos"
          accept={PHOTO_ACCEPT}
          onFiles={addFiles}
          disabled={busy}
          camera
          hint="Puedes tomar varias seguidas o elegirlas de la galería del teléfono. Hasta 25 MB cada una."
        />

        {rejected.length ? (
          <div role="alert" className="rounded-lg border border-warning-border bg-warning-surface px-3 py-2 text-xs text-warning">
            <p className="font-semibold">No se añadieron:</p>
            <ul className="mt-1 list-disc pl-4">
              {rejected.map((line) => <li key={line}>{line}</li>)}
            </ul>
          </div>
        ) : null}

        {queue.length ? (
          <>
            <ul aria-label="Fotos por subir" className="grid gap-3 sm:grid-cols-2">
              {queue.map((item) => (
                <li key={item.key} className="flex gap-3 rounded-lg border border-bd-border bg-background p-2">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={item.preview}
                    alt={`Vista previa de ${item.file.name}`}
                    className="h-20 w-20 shrink-0 rounded-md object-cover"
                  />
                  <div className="min-w-0 flex-1 space-y-1.5">
                    <p className="break-all text-xs text-foreground">{item.file.name}</p>
                    <input
                      type="text"
                      aria-label={`Nota de ${item.file.name}`}
                      value={item.caption}
                      maxLength={300}
                      disabled={busy}
                      placeholder="Qué muestra la foto (opcional)"
                      onChange={(e) => setQueue((prev) => prev.map((entry) => (
                        entry.key === item.key ? { ...entry, caption: e.target.value } : entry
                      )))}
                      className="w-full rounded-md border border-bd-border bg-background px-2 py-1.5 text-xs text-foreground"
                    />
                    <div className="flex flex-wrap items-center justify-between gap-2 text-[11px]">
                      {item.status === "failed" ? (
                        <span className="text-danger">{item.error}</span>
                      ) : (
                        <span className="text-muted">
                          {item.status === "uploading" ? "Subiendo…" : humanBytes(item.file.size)}
                        </span>
                      )}
                      <button
                        type="button"
                        aria-label={`Quitar ${item.file.name}`}
                        disabled={busy}
                        onClick={() => removeQueued(item.key)}
                        className="text-muted underline underline-offset-4 hover:text-danger disabled:opacity-40"
                      >
                        Quitar
                      </button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                disabled={busy || !canUploadHere}
                onClick={() => void uploadAll()}
                className="min-h-11 rounded-full bg-foreground px-5 text-sm font-semibold text-background transition-opacity hover:opacity-90 disabled:opacity-40"
              >
                Subir {photos(queue.length)}
              </button>
              <p className="text-xs text-muted" aria-live="polite">
                {progress ? `Subiendo ${Math.min(progress.done + 1, progress.total)} de ${progress.total}…` : `Etapa: ${label(stage)}`}
              </p>
            </div>
          </>
        ) : null}

        <p className="text-[11px] leading-5 text-muted">
          La plataforma optimiza la imagen automáticamente: se reorienta, se le
          quita la metadata —incluida la ubicación— y se guarda comprimida. No se
          conserva el archivo original de la cámara.
        </p>
      </div>

      {error ? (
        <p role="alert" className="rounded-lg border border-danger-border bg-danger-surface px-3 py-2 text-xs text-danger">
          {error}
        </p>
      ) : null}

      {rows === null ? (
        <p className="text-xs text-muted">Cargando…</p>
      ) : rows.length === 0 ? (
        <p className="text-xs text-muted">Todavía no hay evidencias.</p>
      ) : (
        <>
          <ul aria-label="Evidencias por etapa" className="flex flex-wrap gap-2">
            {stages.filter((s) => counts[s.value]).map((s) => (
              <li key={s.value} className="rounded-full border border-bd-border bg-surface px-3 py-1 text-xs text-foreground">
                {s.label} · {photos(counts[s.value])}
              </li>
            ))}
          </ul>

          {before && after ? (
            <section aria-label="Antes y después de la reparación" className="rounded-xl border border-bd-border bg-surface p-3">
              <h4 className="text-[11px] font-medium uppercase tracking-wide text-muted">Antes y después</h4>
              <div className="mt-2 grid grid-cols-2 gap-3">
                {[["Antes", before], ["Después", after]].map(([title, item]) => {
                  const evidence = item as Evidence;
                  return (
                    <figure key={evidence.id}>
                      <button
                        type="button"
                        aria-label={`Ver en grande: ${title as string}`}
                        onClick={() => setViewing(evidence.id)}
                        className="block w-full overflow-hidden rounded-lg border border-bd-border"
                      >
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={`${base}/${evidence.id}/content/`}
                          alt={`${title as string}: ${describe(evidence)}`}
                          loading="lazy"
                          className="aspect-[4/3] w-full bg-background object-cover"
                        />
                      </button>
                      <figcaption className="mt-1 text-xs text-muted">{title as string}</figcaption>
                    </figure>
                  );
                })}
              </div>
            </section>
          ) : null}

          {groups.map(({ stage: s, items }) => (
            <section key={s.value} className="space-y-2">
              <h4 className="text-[11px] font-medium uppercase tracking-wide text-muted">
                {s.label} · {items.length}
              </h4>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
                {items.map((e) => (
                  <figure
                    key={e.id}
                    className={`overflow-hidden rounded-xl border border-bd-border ${e.voided_at ? "opacity-50" : ""}`}
                  >
                    <button
                      type="button"
                      aria-label={`Ver en grande: ${describe(e)}`}
                      onClick={() => setViewing(e.id)}
                      className="block w-full"
                    >
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={`${base}/${e.id}/content/`}
                        alt={describe(e)}
                        className="h-32 w-full bg-background/40 object-cover"
                        loading="lazy"
                      />
                    </button>
                    <figcaption className="space-y-1.5 px-2 py-2">
                      <div className="flex flex-wrap items-center gap-1">
                        {e.voided_at ? (
                          <span className="rounded bg-danger-surface px-1.5 py-px text-[10px] text-danger">
                            Anulada
                          </span>
                        ) : e.visibility === "customer" ? (
                          <span className="rounded bg-success-surface px-1.5 py-px text-[10px] text-success">
                            Visible al cliente
                          </span>
                        ) : (
                          <span className="rounded bg-surface px-1.5 py-px text-[10px] text-muted">
                            Interna
                          </span>
                        )}
                      </div>

                      {editing === e.id ? (
                        <div className="space-y-1">
                          <input
                            aria-label="Nota de la evidencia"
                            value={draft}
                            maxLength={300}
                            onChange={(ev) => setDraft(ev.target.value)}
                            className="w-full rounded border border-bd-border bg-background px-1.5 py-1 text-xs text-foreground"
                          />
                          <div className="flex gap-2 text-[11px]">
                            <button type="button" disabled={busy} onClick={() => void saveCaption(e.id)} className="font-semibold text-foreground underline underline-offset-4 disabled:opacity-40">
                              Guardar nota
                            </button>
                            <button type="button" onClick={() => setEditing(null)} className="text-muted">
                              Cancelar
                            </button>
                          </div>
                        </div>
                      ) : e.caption ? (
                        <p className="text-xs leading-5 text-foreground [overflow-wrap:anywhere]">{e.caption}</p>
                      ) : null}

                      <p className="text-[10px] text-muted">
                        {new Date(e.created_at).toLocaleString("es-PE")} · {e.uploaded_by || "sin autor"}
                      </p>
                      {e.void_reason ? (
                        <p className="text-[10px] text-muted">{e.void_reason}</p>
                      ) : null}

                      {!e.voided_at && may(s.capability) && editing !== e.id ? (
                        <div className="flex flex-wrap gap-1">
                          {e.visibility === "internal" ? (
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => setSharing(e.id)}
                              className="rounded border border-bd-border px-1.5 py-1 text-[10px] text-foreground/85 disabled:opacity-40"
                            >
                              Compartir con cliente
                            </button>
                          ) : (
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => void act(e.id, "hide-from-customer")}
                              className="rounded border border-bd-border px-1.5 py-1 text-[10px] text-foreground/85 disabled:opacity-40"
                            >
                              Ocultar al cliente
                            </button>
                          )}
                          <button
                            type="button"
                            aria-label={`Editar nota: ${e.caption || "sin nota"}`}
                            disabled={busy}
                            onClick={() => { setEditing(e.id); setDraft(e.caption); }}
                            className="rounded border border-bd-border px-1.5 py-1 text-[10px] text-foreground/85 disabled:opacity-40"
                          >
                            Editar nota
                          </button>
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => setVoiding(e.id)}
                            className="rounded border border-bd-border px-1.5 py-1 text-[10px] text-muted disabled:opacity-40"
                          >
                            Anular
                          </button>
                        </div>
                      ) : null}

                      {sharing === e.id ? (
                        <div className="space-y-1">
                          <p className="text-[10px] text-foreground">
                            El cliente verá la foto y su nota{e.caption ? `: «${e.caption}»` : ""}.
                          </p>
                          <div className="flex gap-2 text-[11px]">
                            <button type="button" disabled={busy} onClick={() => void act(e.id, "publish-to-customer")} className="font-semibold text-foreground underline underline-offset-4 disabled:opacity-40">
                              Sí, compartir
                            </button>
                            <button type="button" onClick={() => setSharing(null)} className="text-muted">
                              Cancelar
                            </button>
                          </div>
                        </div>
                      ) : null}

                      {voiding === e.id ? (
                        <div className="space-y-1">
                          <input
                            value={reason}
                            aria-label="Motivo de la anulación"
                            onChange={(ev) => setReason(ev.target.value)}
                            placeholder="Motivo de la anulación"
                            className="w-full rounded border border-bd-border bg-transparent px-1.5 py-1 text-[10px] text-foreground"
                          />
                          <div className="flex gap-1">
                            <button
                              type="button"
                              disabled={busy || !reason.trim()}
                              onClick={() => void act(e.id, "void", { reason })}
                              className="rounded bg-danger-solid px-1.5 py-px text-[10px] text-on-status disabled:opacity-40"
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
          ))}
        </>
      )}

      {viewing !== null ? (
        <EvidenceViewer
          rows={ordered}
          current={viewing}
          src={(id) => `${base}/${id}/content/`}
          label={label}
          onMove={setViewing}
          onClose={() => setViewing(null)}
        />
      ) : null}
    </div>
  );
}

/**
 * La foto en grande, y las demás a un paso.
 *
 * Un diálogo de verdad: se cierra con Escape, se recorre con las flechas, toma
 * el foco al abrirse y lo devuelve al cerrarse. La imagen se pide al mismo
 * endpoint que la miniatura, que vuelve a autorizar.
 */
function EvidenceViewer({
  rows, current, src, label, onMove, onClose,
}: {
  rows: Evidence[];
  current: number;
  src: (id: number) => string;
  label: (stage: string) => string;
  onMove: (id: number) => void;
  onClose: () => void;
}) {
  const index = Math.max(0, rows.findIndex((row) => row.id === current));
  const evidence = rows[index];
  const close = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    close.current?.focus();
    return () => { if (previous?.isConnected) previous.focus(); };
  }, []);

  useEffect(() => {
    const step = (delta: number) => {
      const next = rows[(index + delta + rows.length) % rows.length];
      if (next) onMove(next.id);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      else if (event.key === "ArrowRight") step(1);
      else if (event.key === "ArrowLeft") step(-1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, rows, onMove, onClose]);

  if (!evidence) return null;
  const move = (delta: number) => onMove(rows[(index + delta + rows.length) % rows.length].id);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Evidencia"
      className="fixed inset-0 z-50 flex flex-col bg-black/90 p-3 sm:p-6"
    >
      <div className="flex items-center justify-between gap-3 text-sm text-white">
        <p className="tabular-nums">{index + 1} de {rows.length}</p>
        <button ref={close} type="button" onClick={onClose} className="min-h-11 rounded-full border border-white/30 px-4 font-semibold">
          Cerrar
        </button>
      </div>
      <div className="flex min-h-0 flex-1 items-center justify-center gap-2 py-3">
        <button type="button" aria-label="Foto anterior" disabled={rows.length < 2} onClick={() => move(-1)} className="grid h-11 w-11 shrink-0 place-items-center rounded-full border border-white/30 text-white disabled:opacity-30">
          <span aria-hidden="true">←</span>
        </button>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          key={evidence.id}
          src={src(evidence.id)}
          alt={evidence.caption || label(evidence.stage)}
          className="max-h-full min-w-0 max-w-full flex-1 object-contain"
        />
        <button type="button" aria-label="Foto siguiente" disabled={rows.length < 2} onClick={() => move(1)} className="grid h-11 w-11 shrink-0 place-items-center rounded-full border border-white/30 text-white disabled:opacity-30">
          <span aria-hidden="true">→</span>
        </button>
      </div>
      <div className="space-y-1 text-center text-white">
        {evidence.caption ? <p className="text-sm [overflow-wrap:anywhere]">{evidence.caption}</p> : null}
        <p className="text-xs text-white/70">
          {label(evidence.stage)} · {new Date(evidence.created_at).toLocaleString("es-PE")} · {evidence.uploaded_by || "sin autor"}
          {evidence.voided_at ? " · Anulada" : evidence.visibility === "customer" ? " · Visible al cliente" : " · Interna"}
        </p>
      </div>
    </div>
  );
}
