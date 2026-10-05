"use client";

/**
 * Una zona para elegir o soltar imágenes.
 *
 * NO SUBE NADA. Entrega los archivos a quien la usa —la galería de producto,
 * la carga masiva, las evidencias de servicio— y cada uno decide qué hacer.
 *
 * FUNCIONA SIN ARRASTRAR. Soltar archivos es un atajo de ratón; el control de
 * verdad es un `<input type="file">` con su etiqueta, que se alcanza con el
 * teclado y abre el selector del sistema (en un teléfono, también la galería).
 * `camera` añade un segundo botón que abre directamente la cámara trasera, sin
 * quitar el primero: forzar la cámara impediría elegir una foto ya tomada.
 */

import { useId, useState } from "react";

type Props = {
  /** Texto del botón principal; es también el nombre accesible del control. */
  label: string;
  onFiles: (files: File[]) => void;
  accept: string;
  multiple?: boolean;
  disabled?: boolean;
  hint?: string;
  /** Añade «Tomar foto»: abre la cámara en un teléfono. */
  camera?: boolean;
};

export function ImageDropzone({
  label, onFiles, accept, multiple = true, disabled = false, hint, camera = false,
}: Props) {
  const pickerId = useId();
  const cameraId = useId();
  const [over, setOver] = useState(false);

  function deliver(list: FileList | File[] | null | undefined) {
    const files = Array.from(list ?? []);
    if (files.length) onFiles(multiple ? files : files.slice(0, 1));
  }

  function handleChange(event: React.ChangeEvent<HTMLInputElement>) {
    deliver(event.target.files);
    // Se vacía para que elegir los mismos archivos otra vez vuelva a avisar.
    event.target.value = "";
  }

  return (
    <div
      data-testid="image-dropzone"
      data-over={over || undefined}
      onDragOver={(event) => { if (!disabled) { event.preventDefault(); setOver(true); } }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => {
        event.preventDefault();
        setOver(false);
        if (!disabled) deliver(event.dataTransfer?.files);
      }}
      className={`rounded-xl border border-dashed px-4 py-6 text-center transition-colors ${
        over ? "border-foreground/50 bg-foreground/[0.04]" : "border-bd-border bg-background"
      } ${disabled ? "opacity-60" : ""}`}
    >
      <div className="flex flex-wrap items-center justify-center gap-2">
        <label
          htmlFor={pickerId}
          className={`inline-flex min-h-11 items-center rounded-full border border-bd-border bg-background px-5 text-sm font-semibold text-foreground transition-colors focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-primary ${
            disabled ? "cursor-not-allowed" : "cursor-pointer hover:bg-surface"
          }`}
        >
          {label}
          <input
            id={pickerId} type="file" accept={accept} multiple={multiple}
            disabled={disabled} onChange={handleChange} className="sr-only"
          />
        </label>
        {camera ? (
          <label
            htmlFor={cameraId}
            className={`inline-flex min-h-11 items-center rounded-full bg-foreground px-5 text-sm font-semibold text-background transition-opacity focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-primary ${
              disabled ? "cursor-not-allowed" : "cursor-pointer hover:opacity-90"
            }`}
          >
            Tomar foto
            <input
              id={cameraId} type="file" accept="image/*" capture="environment"
              disabled={disabled} onChange={handleChange} className="sr-only"
            />
          </label>
        ) : null}
      </div>
      <p className="mt-3 text-xs text-muted">
        {hint ?? "También puedes arrastrar los archivos hasta aquí."}
      </p>
    </div>
  );
}
