"use client";

/**
 * DOC-PREVIEW-ESCPOS — el rollo de la impresora térmica, simulado.
 *
 * NO ES OTRO DISEÑO. El servidor imprime la nota de muestra en bytes ESC/POS con la misma función que
 * imprime las notas reales y los lee renglón por renglón: esta pantalla sólo los dibuja. Cada renglón
 * conserva su alineación, la negrita y la letra doble (doble de ancho y de alto: en una letra de ancho
 * fijo, `2em` ocupa exactamente dos columnas). El logo es el mapa de puntos que recibe la impresora, a su
 * escala: `dots / columns` puntos por columna.
 *
 * EL PAPEL ES BLANCO Y LA TINTA NEGRA en los dos temas: así sale de la térmica.
 */

import { useId } from "react";
import type { ThermalAlign, ThermalLine, ThermalPreview } from "../../lib/document-preview-api";

const ALIGN: Record<ThermalAlign, string> = { left: "text-left", center: "text-center", right: "text-right" };

function millimetres(dots: number, perMm: number): string {
  return (dots / perMm).toLocaleString("es-PE", { maximumFractionDigits: 1 });
}

function Line({ line, roll }: { line: ThermalLine; roll: ThermalPreview }) {
  switch (line.type) {
    case "text":
      return (
        <span
          data-testid="thermal-line"
          data-align={line.align}
          className={`block ${ALIGN[line.align]}${line.bold ? " font-bold" : ""}${line.large ? " text-[2em] leading-[1.15]" : ""}`}
        >
          {line.text || " "}
        </span>
      );
    case "image": {
      const perColumn = roll.dots / roll.columns;
      return (
        <span className={`block ${ALIGN[line.align]}`} data-testid="thermal-image" data-align={line.align}>
          {/* eslint-disable-next-line @next/next/no-img-element -- el mapa de puntos llega como data: PNG */}
          <img
            src={`data:image/png;base64,${line.png}`}
            alt={`Logotipo de ${millimetres(line.width, roll.dots_per_mm)} × ${millimetres(line.height, roll.dots_per_mm)} mm `
              + `(${line.width} × ${line.height} puntos)`}
            width={line.width}
            height={line.height}
            className="inline-block h-auto max-w-full align-top [image-rendering:pixelated]"
            style={{ width: `${line.width / perColumn}ch` }}
          />
        </span>
      );
    }
    case "qr":
      return (
        <span className={`block ${ALIGN[line.align]}`} data-testid="thermal-qr">
          [Código QR]
        </span>
      );
    case "feed":
      return (
        <>
          {Array.from({ length: line.lines }, (_v, n) => <span key={n} className="block">{" "}</span>)}
        </>
      );
    case "cut":
      return (
        <span className="block border-t border-dashed border-neutral-400 text-center text-[0.8em] text-neutral-500">
          corte del papel
        </span>
      );
    default:
      return null;
  }
}

export function ThermalRoll({ roll }: { roll: ThermalPreview }) {
  const captionId = useId();
  return (
    <figure aria-labelledby={captionId} data-testid="thermal-roll" className="min-w-0 space-y-3">
      <figcaption id={captionId} className="text-sm text-muted">
        Simulación de la impresora térmica: rollo de {roll.paper_width_mm} mm, {roll.columns} columnas. Es el
        texto que recibe la impresora, con su alineación, negrita y letra doble; el logo es el mapa de puntos que
        imprime. La letra real depende del modelo de impresora.
      </figcaption>
      <div className="overflow-x-auto rounded-lg border border-bd-border bg-neutral-200 p-2 sm:p-4">
        {/* Sin nombre propio: la figura ya lo tiene en su leyenda. */}
        <pre
          className="mx-auto box-content bg-white px-[1ch] py-4 font-mono text-[10px] leading-[1.35] text-black shadow-sm sm:text-xs"
          style={{ width: `${roll.columns}ch` }}
        >
          {roll.lines.map((line, index) => <Line key={index} line={line} roll={roll} />)}
        </pre>
      </div>
    </figure>
  );
}
