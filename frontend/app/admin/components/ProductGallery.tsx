"use client";

/**
 * PRODUCT-MEDIA — la galería de un producto.
 *
 * CADA CAMBIO SE GUARDA AL HACERLO. Subir, elegir la principal, reordenar,
 * escribir el texto alternativo o quitar una imagen son peticiones propias: no
 * esperan al botón «Guardar» del formulario del producto, que es otra cosa.
 *
 * EL SERVIDOR ES LA AUTORIDAD. Aquí se descarta lo que seguro va a rechazar
 * (otro tipo de archivo, más de 8 MB) para ahorrar el viaje; qué es una imagen
 * de verdad, cuántas caben y cuál queda como principal lo decide él.
 *
 * UN ARCHIVO QUE FALLA NO DETIENE A LOS DEMÁS. Se suben de uno en uno y cada
 * uno muestra su estado; el que falla dice por qué y se puede reintentar.
 */

import { type ReactNode, useEffect, useRef, useState } from "react";
import {
  AdminProductImage, PRODUCT_IMAGE_ACCEPT, PRODUCT_IMAGE_MAX_BYTES,
  deleteProductImage, patchProductImage, reorderProductImages, uploadProductImage,
} from "../../lib/admin";
import { storefrontMediaStyle } from "../../lib/storefront-media";
import { ImageDropzone } from "./ImageDropzone";
import { TRANSPARENCY_GRID_STYLE } from "./ImageUploadField";
import { internalButtonClass, internalInputClass } from "./internal-ui";

type Props = {
  productId: number;
  productName: string;
  images: AdminProductImage[];
  onChange: (images: AdminProductImage[]) => void;
  readOnly?: boolean;
  /**
   * CATALOG-VARIANT-IMAGES: lo elegido NO se sube al soltarlo. Se ve antes (sobre cuadros, con nombre y tamaño),
   * se puede quitar, y «Subir N imágenes» lo envía todo. Sin esta opción, se sube al elegirlo, como siempre.
   */
  stageBeforeUpload?: boolean;
  /** La empresa de la pantalla (un MASTER que cambió de empresa). Sin ella, la que añade `fetchWithAuth`. */
  companyId?: number | null;
  /** Acciones propias por imagen (p. ej. «Usar como portada del modelo»). Sólo para quien edita. */
  renderImageActions?: (image: AdminProductImage, position: number) => ReactNode;
  /** What is waiting or on its way (staged mode): whoever hosts the gallery must not drop it silently. */
  onWorkChange?: (work: GalleryWork) => void;
  /** A change finished (a whole staged batch, the upload queue, a primary, an order, a removal). */
  onSettled?: () => void;
};

export type GalleryWork = { staged: number; sending: boolean };

type Staged = { key: string; file: File; preview: string; status: "ready" | "uploading" | "failed"; error?: string };

type Pending = { key: string; file: File; status: "queued" | "uploading" | "failed"; error?: string };

const ACCEPTED = new Set(PRODUCT_IMAGE_ACCEPT.split(","));

/** Lo que no vale la pena enviar, con el motivo que leerá quien lo eligió. */
export function rejectReason(file: File): string | null {
  if (!ACCEPTED.has(file.type)) return "no es PNG, JPEG o WebP";
  if (file.size > PRODUCT_IMAGE_MAX_BYTES) return "pesa más de 8 MB";
  return null;
}

/** «2 KB», «3.4 MB»: lo que pesa un archivo, para quien lo eligió. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function ProductGallery({
  productId, productName, images, onChange, readOnly = false, stageBeforeUpload = false, companyId,
  renderImageActions, onWorkChange, onSettled,
}: Props) {
  const [pending, setPending] = useState<Pending[]>([]);
  const [rejected, setRejected] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [drafts, setDrafts] = useState<Record<number, string>>({});

  // La galería más reciente, para las subidas en cadena: cada una añade a lo
  // que dejó la anterior, no a lo que había cuando empezó la tanda.
  const current = useRef(images);
  useEffect(() => { current.current = images; }, [images]);
  const counter = useRef(0);
  const running = useRef(false);
  const queue = useRef<Pending[]>([]);
  const [staged, setStaged] = useState<Staged[]>([]);
  const stagedRef = useRef<Staged[]>([]);
  const [sending, setSending] = useState(false);

  // Las vistas previas son objetos del navegador: se liberan al quitar, al subir y al cerrar.
  useEffect(() => () => { stagedRef.current.forEach((item) => URL.revokeObjectURL(item.preview)); }, []);

  // The host's callbacks, current without re-running the effects below on every render of the host.
  const hostWork = useRef(onWorkChange);
  const hostSettled = useRef(onSettled);
  useEffect(() => { hostWork.current = onWorkChange; hostSettled.current = onSettled; });
  const stagedCount = staged.length;
  useEffect(() => { hostWork.current?.({ staged: stagedCount, sending }); }, [stagedCount, sending]);
  useEffect(() => () => { hostWork.current?.({ staged: 0, sending: false }); }, []);

  // Con la empresa de la pantalla sólo cuando la hay: sin ella, las peticiones son las de siempre.
  const scoped = companyId !== undefined && companyId !== null;
  const api = {
    upload: (file: File) => (scoped ? uploadProductImage(productId, file, "", companyId) : uploadProductImage(productId, file)),
    patch: (imageId: number, data: { alt_text?: string; is_primary?: true }) => (
      scoped ? patchProductImage(productId, imageId, data, companyId) : patchProductImage(productId, imageId, data)),
    remove: (imageId: number) => (
      scoped ? deleteProductImage(productId, imageId, companyId) : deleteProductImage(productId, imageId)),
    reorder: (order: number[]) => (
      scoped ? reorderProductImages(productId, order, companyId) : reorderProductImages(productId, order)),
  };

  function publish(next: AdminProductImage[]) {
    current.current = next;
    onChange(next);
  }

  /** Lo que el servidor acaba de crear, al final. Si la marcó principal, las demás dejan de serlo. */
  function append(created: AdminProductImage) {
    const rest = created.is_primary
      ? current.current.map((image) => ({ ...image, is_primary: false }))
      : current.current;
    publish([...rest, created]);
  }

  function setStagedList(next: Staged[]) {
    stagedRef.current = next;
    setStaged(next);
  }

  function updateStaged(key: string, change: Partial<Staged>) {
    setStagedList(stagedRef.current.map((item) => (item.key === key ? { ...item, ...change } : item)));
  }

  function unstage(key: string) {
    const item = stagedRef.current.find((entry) => entry.key === key);
    if (item) URL.revokeObjectURL(item.preview);
    setStagedList(stagedRef.current.filter((entry) => entry.key !== key));
  }

  /** Sube lo que espera, de uno en uno: el que falla dice por qué y se queda para volver a intentarlo. */
  async function sendStaged() {
    if (sending) return;
    setSending(true);
    setError(null);
    try {
      for (const item of stagedRef.current.filter((entry) => entry.status !== "uploading")) {
        updateStaged(item.key, { status: "uploading", error: undefined });
        try {
          const created = await api.upload(item.file);
          unstage(item.key);
          append(created);
        } catch (err) {
          updateStaged(item.key, {
            status: "failed", error: err instanceof Error ? err.message : "No se pudo subir la imagen.",
          });
        }
      }
    } finally {
      setSending(false);
      hostSettled.current?.();
    }
  }

  async function drain() {
    if (running.current) return;
    running.current = true;
    try {
      for (;;) {
        const item = queue.current.find((entry) => entry.status === "queued");
        if (!item) break;
        item.status = "uploading";
        setPending([...queue.current]);
        try {
          const created = await api.upload(item.file);
          queue.current = queue.current.filter((entry) => entry.key !== item.key);
          append(created);
        } catch (err) {
          item.status = "failed";
          item.error = err instanceof Error ? err.message : "No se pudo subir la imagen.";
        }
        setPending([...queue.current]);
      }
    } finally {
      running.current = false;
      hostSettled.current?.();
    }
  }

  function addFiles(files: File[]) {
    const problems: string[] = [];
    const accepted: File[] = [];
    for (const file of files) {
      const reason = rejectReason(file);
      if (reason) {
        problems.push(`${file.name}: ${reason}.`);
        continue;
      }
      accepted.push(file);
    }
    setRejected(problems);
    if (stageBeforeUpload) {
      setStagedList([...stagedRef.current, ...accepted.map((file) => {
        counter.current += 1;
        return { key: `${counter.current}-${file.name}`, file, preview: URL.createObjectURL(file), status: "ready" as const };
      })]);
      return;
    }
    for (const file of accepted) {
      counter.current += 1;
      queue.current.push({ key: `${counter.current}-${file.name}`, file, status: "queued" });
    }
    setPending([...queue.current]);
    void drain();
  }

  function retry(key: string) {
    const item = queue.current.find((entry) => entry.key === key);
    if (!item) return;
    item.status = "queued";
    item.error = undefined;
    setPending([...queue.current]);
    void drain();
  }

  function discard(key: string) {
    queue.current = queue.current.filter((entry) => entry.key !== key);
    setPending([...queue.current]);
  }

  async function act(imageId: number, work: () => Promise<AdminProductImage[]>) {
    setBusy(imageId);
    setError(null);
    try {
      publish(await work());
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar el cambio.");
    } finally {
      setBusy(null);
      hostSettled.current?.();
    }
  }

  function makePrimary(image: AdminProductImage) {
    return act(image.id, async () => {
      const updated = await api.patch(image.id, { is_primary: true });
      return current.current.map((row) => (
        row.id === updated.id ? updated : { ...row, is_primary: false }
      ));
    });
  }

  /**
   * Guarda el texto alternativo al salir del campo.
   *
   * NO pasa por `act` y NO deshabilita nada. El campo pierde el foco justo
   * cuando se pulsa un botón: si este guardado apagara los botones, el clic
   * que lo provocó se perdería. Y de la respuesta sólo se toma el texto: llega
   * con el estado de cuando salió, y lo demás pudo cambiar entretanto.
   */
  async function saveAlt(image: AdminProductImage) {
    const draft = drafts[image.id];
    if (draft === undefined || draft.trim() === image.alt_text) return;
    setError(null);
    try {
      const updated = await api.patch(image.id, { alt_text: draft.trim() });
      setDrafts((prev) => {
        const next = { ...prev };
        delete next[image.id];
        return next;
      });
      publish(current.current.map((row) => (
        row.id === updated.id ? { ...row, alt_text: updated.alt_text } : row
      )));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar el texto alternativo.");
    }
  }

  function remove(image: AdminProductImage) {
    setConfirming(null);
    return act(image.id, () => api.remove(image.id));
  }

  function move(index: number, step: -1 | 1) {
    const order = current.current.map((row) => row.id);
    const target = index + step;
    if (target < 0 || target >= order.length) return;
    [order[index], order[target]] = [order[target], order[index]];
    return act(current.current[index].id, () => api.reorder(order));
  }

  const uploading = pending.filter((item) => item.status !== "failed").length;
  const waiting = staged.filter((item) => item.status !== "uploading").length;

  return (
    <div className="space-y-4">
      {!readOnly ? (
        <ImageDropzone
          label="Elegir imágenes"
          accept={PRODUCT_IMAGE_ACCEPT}
          onFiles={addFiles}
          hint={stageBeforeUpload
            ? "PNG (con transparencia), JPEG o WebP, hasta 8 MB cada una. Las verás antes de subirlas."
            : "PNG, JPEG o WebP, hasta 8 MB cada una. También puedes arrastrarlas hasta aquí."}
        />
      ) : null}

      {rejected.length ? (
        <div role="alert" className="rounded-lg border border-warning-border bg-warning-surface px-4 py-3 text-xs text-warning">
          <p className="font-semibold">No se subieron:</p>
          <ul className="mt-1 list-disc pl-4">
            {rejected.map((line) => <li key={line}>{line}</li>)}
          </ul>
        </div>
      ) : null}

      {error ? (
        <p role="alert" className="rounded-lg border border-danger-border bg-danger-surface px-4 py-3 text-xs text-danger">
          {error}
        </p>
      ) : null}

      {!readOnly && staged.length ? (
        <div className="rounded-lg border border-bd-border bg-background p-3">
          <p className="text-xs font-semibold text-foreground" aria-live="polite">
            {sending ? "Subiendo…" : "Revisa antes de subir. Los cuadros del fondo son la transparencia de la imagen."}
          </p>
          <ul aria-label="Imágenes por subir" className="mt-2 grid gap-2 sm:grid-cols-2">
            {staged.map((item) => (
              <li key={item.key} className="flex min-w-0 items-center gap-3 rounded-lg border border-bd-border p-2 text-xs">
                <span
                  data-transparency-grid="true"
                  style={TRANSPARENCY_GRID_STYLE}
                  className="flex h-16 w-16 shrink-0 items-center justify-center overflow-hidden rounded-md border border-bd-border"
                >
                  {/* Vista previa local (blob:), aún no está en el servidor. */}
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={item.preview} alt={`Vista previa de ${item.file.name}`} className="max-h-full max-w-full object-contain" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block break-all font-semibold text-foreground">{item.file.name}</span>
                  <span className="block text-muted">{formatBytes(item.file.size)}</span>
                  {item.status === "uploading" ? <span className="block text-muted">Subiendo…</span> : null}
                  {item.status === "failed" ? <span role="alert" className="block text-danger">{item.error}</span> : null}
                </span>
                <button
                  type="button"
                  aria-label={`Quitar ${item.file.name} de la lista`}
                  disabled={item.status === "uploading"}
                  onClick={() => unstage(item.key)}
                  className="min-h-9 shrink-0 px-1 text-xs text-muted underline-offset-4 hover:text-danger hover:underline disabled:opacity-50"
                >
                  Quitar
                </button>
              </li>
            ))}
          </ul>
          <div className="mt-3 flex justify-end">
            <button
              type="button"
              disabled={sending || waiting === 0}
              onClick={() => void sendStaged()}
              className={`${internalButtonClass} min-h-10 px-4 text-sm font-semibold`}
            >
              {sending ? "Subiendo…" : (waiting === 1 ? "Subir 1 imagen" : `Subir ${waiting} imágenes`)}
            </button>
          </div>
        </div>
      ) : null}

      {pending.length ? (
        <div className="rounded-lg border border-bd-border bg-background p-3">
          <p className="text-xs font-semibold text-foreground" aria-live="polite">
            {uploading ? `Subiendo ${uploading} imagen${uploading === 1 ? "" : "es"}…` : "Subidas con error"}
          </p>
          <ul className="mt-2 space-y-2">
            {pending.map((item) => (
              <li key={item.key} className="flex flex-wrap items-center justify-between gap-2 text-xs">
                <span className="min-w-0 break-all text-foreground">{item.file.name}</span>
                {item.status === "failed" ? (
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="text-danger">{item.error}</span>
                    <button type="button" aria-label={`Reintentar ${item.file.name}`} onClick={() => retry(item.key)} className="font-semibold underline underline-offset-4">
                      Reintentar
                    </button>
                    <button type="button" aria-label={`Descartar ${item.file.name}`} onClick={() => discard(item.key)} className="text-muted underline underline-offset-4">
                      Descartar
                    </button>
                  </span>
                ) : (
                  <span className="text-muted">{item.status === "uploading" ? "Subiendo…" : "En cola"}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {images.length ? (
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {images.map((image, index) => {
            const position = index + 1;
            const working = busy === image.id;
            return (
              <li key={image.id} className="flex flex-col rounded-xl border border-bd-border bg-background p-3">
                <div
                  data-transparency-grid="true"
                  style={TRANSPARENCY_GRID_STYLE}
                  className="relative flex aspect-[4/3] items-center justify-center overflow-hidden rounded-lg"
                >
                  {/* Imagen del propio tenant, de tamaño desconocido: no pasa por el optimizador. */}
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={image.url}
                    alt={image.alt_text || productName}
                    loading="lazy"
                    style={storefrontMediaStyle(image.url)}
                    className="max-h-full max-w-full object-contain"
                  />
                  {image.is_primary ? (
                    <span className="absolute left-2 top-2 rounded-full bg-foreground px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-background shadow-sm">
                      Principal
                    </span>
                  ) : null}
                </div>

                {readOnly ? (
                  image.alt_text ? <p className="mt-2 text-xs text-muted">{image.alt_text}</p> : null
                ) : (
                  <>
                    <label className="mt-3 block text-xs text-muted">
                      Texto alternativo
                      <input
                        type="text"
                        aria-label={`Texto alternativo de la imagen ${position}`}
                        value={drafts[image.id] ?? image.alt_text}
                        maxLength={160}
                        placeholder={productName}
                        onChange={(event) => setDrafts((prev) => ({ ...prev, [image.id]: event.target.value }))}
                        onBlur={() => void saveAlt(image)}
                        className={`${internalInputClass} mt-1`}
                      />
                    </label>

                    {confirming === image.id ? (
                      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
                        <span className="text-foreground">¿Quitar esta imagen?</span>
                        <button type="button" onClick={() => void remove(image)} className="min-h-9 rounded-lg border border-danger-border bg-danger-surface px-3 font-semibold text-danger">
                          Sí, quitar
                        </button>
                        <button type="button" onClick={() => setConfirming(null)} className="min-h-9 px-2 text-muted underline underline-offset-4">
                          Cancelar
                        </button>
                      </div>
                    ) : (
                      <div className="mt-3 flex flex-wrap items-center gap-2">
                        <button
                          type="button"
                          aria-label={`Mover imagen ${position} antes`}
                          disabled={working || index === 0}
                          onClick={() => void move(index, -1)}
                          className={`${internalButtonClass} min-h-9 px-2.5 py-1.5`}
                        >
                          <span aria-hidden="true">←</span>
                        </button>
                        <button
                          type="button"
                          aria-label={`Mover imagen ${position} después`}
                          disabled={working || index === images.length - 1}
                          onClick={() => void move(index, 1)}
                          className={`${internalButtonClass} min-h-9 px-2.5 py-1.5`}
                        >
                          <span aria-hidden="true">→</span>
                        </button>
                        {!image.is_primary ? (
                          <button
                            type="button"
                            aria-label={`Marcar como principal: imagen ${position}`}
                            disabled={working}
                            onClick={() => void makePrimary(image)}
                            className={`${internalButtonClass} min-h-9 px-3 py-1.5 text-xs`}
                          >
                            Marcar como principal
                          </button>
                        ) : null}
                        <button
                          type="button"
                          aria-label={`Quitar imagen ${position}`}
                          disabled={working}
                          onClick={() => setConfirming(image.id)}
                          className="min-h-9 px-1 text-xs text-muted underline-offset-4 transition-colors hover:text-danger hover:underline"
                        >
                          Quitar
                        </button>
                        {renderImageActions ? renderImageActions(image, position) : null}
                      </div>
                    )}
                  </>
                )}
              </li>
            );
          })}
        </ul>
      ) : pending.length === 0 && staged.length === 0 ? (
        <p className="rounded-lg border border-bd-border bg-background px-4 py-6 text-center text-sm text-muted">
          Este producto todavía no tiene imágenes.
        </p>
      ) : null}
    </div>
  );
}
