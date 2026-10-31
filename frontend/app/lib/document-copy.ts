/**
 * SALES-EMAIL-03 — la dirección de una copia, comprobada antes de enviarla.
 *
 * El servidor vuelve a comprobar todo (`order_document_copy.normalise_recipient`)
 * y es quien decide. Esto sólo evita un viaje para lo evidente y dice en
 * palabras qué está mal: una sola dirección, sin nombre, sin comas ni punto y
 * coma, sin espacios ni tildes, 254 caracteres como mucho, escrita dos veces.
 */

export const MAX_COPY_ADDRESS_LENGTH = 254;
const MAX_LOCAL_LENGTH = 64;
const FORBIDDEN = /[,;<>"():[\]\\]/;
const PRINTABLE_ASCII = /^[\x21-\x7e]+$/;
// Una forma razonable de «algo@dominio.tld»; el servidor aplica la regla completa.
const SHAPE = /^[^@]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}$/;

/** Lo que está mal en la dirección, o `null` si se puede revisar el envío. */
export function copyAddressProblem(to: string, confirmation: string): string | null {
  if (/[\r\n\0]/.test(to)) return "El correo no puede tener saltos de línea.";
  const value = to.trim();
  if (!value) return "Escribe el correo de destino.";
  if (value.length > MAX_COPY_ADDRESS_LENGTH) {
    return `El correo no puede pasar de ${MAX_COPY_ADDRESS_LENGTH} caracteres.`;
  }
  if (FORBIDDEN.test(value) || value.split("@").length !== 2) {
    return "Escribe una sola dirección de correo, sin nombre, comas ni punto y coma.";
  }
  if (!PRINTABLE_ASCII.test(value)) return "El correo no puede tener espacios, tildes ni caracteres especiales.";
  if (value.split("@")[0].length > MAX_LOCAL_LENGTH) {
    return `La parte antes de la @ no puede pasar de ${MAX_LOCAL_LENGTH} caracteres.`;
  }
  if (!SHAPE.test(value)) return "Escribe un correo válido.";
  if (confirmation.trim().toLowerCase() !== value.toLowerCase()) return "Los dos correos no coinciden.";
  return null;
}
