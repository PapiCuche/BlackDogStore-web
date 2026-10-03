"use client";

/**
 * Un hueco de imagen: lo que hay colocado, y cómo cambiarlo.
 *
 * La tienda sube el archivo y el servidor devuelve su dirección; el formulario
 * que contiene este hueco es quien la guarda. Subir no publica nada.
 *
 * LA VISTA PREVIA VA SOBRE UNA CUADRÍCULA. Un recorte en PNG no tiene fondo, y
 * sobre un color liso no se distingue un recorte de una foto con fondo blanco.
 * La cuadrícula es la forma de ver, antes de guardar, que la transparencia
 * llegó intacta.
 */

import { useId, useState } from "react";
import { STOREFRONT_IMAGE_ACCEPT, uploadStorefrontImage } from "../lib/internal-api";

type Props = {
  label: string;
  /** Nombre del campo que este hueco llena en el formulario que lo contiene. */
  name: string;
  value: string;
  companyId: number | null;
  onChange: (name: string, value: string) => void;
  errors?: Record<string, string[]>;
  hint?: string;
  readOnly?: boolean;
};

const GRID_STYLE = {
  backgroundColor: "#f4f4f5",
  backgroundImage:
    "linear-gradient(45deg, #d4d4d8 25%, transparent 25%), linear-gradient(-45deg, #d4d4d8 25%, transparent 25%), " +
    "linear-gradient(45deg, transparent 75%, #d4d4d8 75%), linear-gradient(-45deg, transparent 75%, #d4d4d8 75%)",
  backgroundSize: "16px 16px",
  backgroundPosition: "0 0, 0 8px, 8px -8px, -8px 0",
} as const;

export function ImageUploadField({
  label, name, value, companyId, onChange, errors, hint, readOnly = false,
}: Props) {
  const inputId = useId();
  const [uploading, setUploading] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const problem = failure ?? errors?.[name]?.join(" ") ?? null;

  async function handleFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    // Se vacía para que elegir el mismo archivo otra vez vuelva a disparar el cambio.
    event.target.value = "";
    if (!file) return;
    setUploading(true);
    setFailure(null);
    try {
      const image = await uploadStorefrontImage(file, companyId);
      onChange(name, image.url);
    } catch (err) {
      setFailure(err instanceof Error ? err.message : "No se pudo subir la imagen.");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wider text-muted">{label}</p>
      <div className="mt-1.5 flex flex-wrap items-start gap-4">
        <div
          data-transparency-grid
          style={GRID_STYLE}
          className="flex h-28 w-40 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-bd-border"
        >
          {value ? (
            // Imagen del propio tenant, de tamaño desconocido: no pasa por el optimizador.
            // eslint-disable-next-line @next/next/no-img-element
            <img src={value} alt={label} className="max-h-full max-w-full object-contain" />
          ) : (
            <span className="rounded bg-background/80 px-2 py-1 text-xs text-muted">Sin imagen</span>
          )}
        </div>

        {!readOnly ? (
          <div className="flex min-w-0 flex-col items-start gap-2">
            <label
              htmlFor={inputId}
              className="inline-flex min-h-11 cursor-pointer items-center rounded-full border border-bd-border px-5 text-sm font-semibold text-foreground transition-colors hover:bg-surface-2 focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-primary"
            >
              {uploading ? "Subiendo…" : value ? "Cambiar imagen" : "Subir imagen"}
              <input
                id={inputId}
                type="file"
                accept={STOREFRONT_IMAGE_ACCEPT}
                aria-label={`Subir ${label}`}
                disabled={uploading}
                onChange={handleFile}
                className="sr-only"
              />
            </label>
            {value ? (
              <button
                type="button"
                aria-label={`Quitar ${label}`}
                onClick={() => { setFailure(null); onChange(name, ""); }}
                className="min-h-11 px-1 text-sm text-muted underline-offset-4 transition-colors hover:text-danger hover:underline"
              >
                Quitar
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      {problem ? (
        <p role="alert" className="mt-1.5 text-xs text-danger">{problem}</p>
      ) : (
        <p className="mt-1.5 text-xs text-muted">
          {hint ?? "PNG, JPEG o WebP, hasta 8 MB. Un PNG sin fondo se muestra sin fondo."}
        </p>
      )}
    </div>
  );
}
