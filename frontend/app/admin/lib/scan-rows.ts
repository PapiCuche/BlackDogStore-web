/**
 * SUPPLIER-DEVICE-FLOW-02 — filas de equipos que se llenan con un lector.
 *
 * Un lector de códigos es un teclado que escribe el código y pulsa Enter. En
 * una lista de equipos (una fila por equipo) Enter no envía el formulario:
 * lleva al campo siguiente de la cadena de la fila y, al terminarla, al primero
 * de la fila siguiente. Pegar una lista (un código por línea) en un campo llena
 * ese mismo campo en las filas siguientes.
 *
 * Esto es sólo comodidad: lo que vale un código lo decide el servidor.
 */

import { useCallback, useRef } from "react";

/** Los códigos de un texto pegado, uno por línea, sin vacíos. Un solo código: `[]` (pegado normal). */
export function pastedCodes(text: string): string[] {
  if (!/[\r\n]/.test(text)) return [];
  return text.split(/\r?\n|\r/).map((line) => line.trim()).filter(Boolean);
}

export type ScanTarget = { row: number; field: number } | "end";

/** Adónde lleva Enter desde el campo `field` de la cadena de la fila `row`. */
export function nextScanTarget(row: number, field: number, chainLength: number, rowCount: number): ScanTarget {
  if (field + 1 < chainLength) return { row, field: field + 1 };
  if (row + 1 < rowCount) return { row: row + 1, field: 0 };
  return "end";
}

/** Los atributos que un campo de código lleva en todas las pantallas (móvil incluido). */
export const scanInputProps = {
  autoComplete: "off",
  autoCorrect: "off",
  autoCapitalize: "off",
  spellCheck: false,
  enterKeyHint: "next",
} as const;

/**
 * Los campos de la cadena, registrados por (fila, posición en la cadena).
 * `bind(row, field)` da el `ref` y el `onKeyDown` de ese campo; `onEnd` decide
 * qué pasa con Enter en el último campo de la última fila.
 */
export function useScanRows(chainLength: number, rowCount: number, onEnd?: () => void) {
  const inputs = useRef(new Map<string, HTMLInputElement>());

  const focus = useCallback((row: number, field: number) => {
    const input = inputs.current.get(`${row}:${field}`);
    if (input) {
      input.focus();
      input.select();
    }
    return Boolean(input);
  }, []);

  const bind = useCallback(
    (row: number, field: number) => ({
      ref: (element: HTMLInputElement | null) => {
        if (element) inputs.current.set(`${row}:${field}`, element);
        else inputs.current.delete(`${row}:${field}`);
      },
      onKeyDown: (event: React.KeyboardEvent<HTMLInputElement>) => {
        if (event.key !== "Enter" || event.nativeEvent.isComposing) return;
        event.preventDefault();
        const target = nextScanTarget(row, field, chainLength, rowCount);
        if (target === "end") onEnd?.();
        else focus(target.row, target.field);
      },
    }),
    [chainLength, rowCount, focus, onEnd],
  );

  return { bind, focus };
}
