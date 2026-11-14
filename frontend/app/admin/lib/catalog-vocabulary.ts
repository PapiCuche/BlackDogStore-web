/**
 * CATALOG-COMMERCIAL-01 · A2 — the supplier vocabulary and the resolver, in the operator's words.
 *
 * Only WORDS live here: what a scope, an error code, a way of reading, a doubt or a possible action is called
 * on screen. Nothing here folds, matches or decides: the server does (`catalog_aliases`, `configuration_resolver`).
 * Closed technical vocabularies of the platform; the values (labels, names, aliases) are each company's.
 */

import {
  CatalogApiError, type AliasScope, type AttributeRole, type DoubtAction, type DoubtReason, type ResolveModelStatus,
} from "./internal-api";
import { ROLE_LABELS } from "./palette";

export const ALIAS_SCOPE_LABELS: Record<AliasScope, string> = {
  model_word: "Palabras de modelo",
  model: "Nombres completos de modelo",
  option: "Opciones",
};

/** What each scope does, in one sentence (shown above its list). */
export const ALIAS_SCOPE_HELP: Record<AliasScope, string> = {
  model_word: "Una palabra del proveedor que se lee como otra palabra de los nombres de tus modelos (por ejemplo, una abreviatura). Una sola palabra en cada lado.",
  model: "Un nombre de modelo completo, tal como lo escribe el proveedor, que se lee como uno de tus modelos. Sólo el nombre: las opciones van en sus columnas.",
  option: "Un texto del proveedor que se lee como una opción de la empresa (capacidad, color, SIM, región u otra).",
};

/**
 * What each refusal code of the alias endpoints means. The server's own sentence (`detail`) is always shown
 * next to the field; this names the kind of refusal so it reads the same everywhere.
 */
export const ALIAS_ERROR_LABELS: Record<string, string> = {
  invalid: "Texto no válido",
  duplicate: "Ya existe",
  conflict_with_label: "Choca con un nombre real",
  conflict_with_alias: "Choca con otro alias",
  chain: "Encadena con otra palabra",
  contains_options: "Lleva opciones dentro",
  too_many: "Límite de alias alcanzado",
  not_found: "Ya no existe",
};

/** The inputs of the alias forms the server may name in `field`. */
export type AliasField = "alias" | "replacement" | "model_id" | "option_id" | "scope";
const ALIAS_FIELDS: AliasField[] = ["alias", "replacement", "model_id", "option_id", "scope"];

export const NETWORK_MESSAGE = "No se pudo conectar con el servidor. Revisa tu conexión y vuelve a intentarlo.";

/**
 * A refused or failed alias write, ready to show: the server's sentence (never rewritten), the kind of refusal
 * and the field it belongs to. 403, 404, 429 and a network failure each have their own sentence (the alias endpoints
 * answer 400/404 only; any other status shows the server's text).
 */
export function aliasWriteError(error: unknown, fallback: string): { message: string; kind: string | null; field: AliasField | null } {
  if (error instanceof CatalogApiError) {
    const field = ALIAS_FIELDS.includes(error.field as AliasField) ? (error.field as AliasField) : null;
    if (error.status === 403) {
      return { message: "No tienes permiso para cambiar el vocabulario (hace falta gestionar productos).", kind: null, field: null };
    }
    if (error.status === 404) {
      return {
        message: error.code === "not_found" && field && field !== "alias"
          ? `${error.message} Puede que lo hayan borrado o que no sea de esta empresa.`
          : "Ese alias ya no existe en esta empresa. La lista se volvió a cargar.",
        kind: ALIAS_ERROR_LABELS.not_found, field,
      };
    }
    if (error.status === 429) {
      return { message: "Demasiados cambios seguidos. Espera un momento y vuelve a intentarlo.", kind: null, field: null };
    }
    return { message: error.message || fallback, kind: error.code ? ALIAS_ERROR_LABELS[error.code] ?? null : null, field };
  }
  if (error instanceof TypeError) return { message: NETWORK_MESSAGE, kind: null, field: null };
  return { message: fallback, kind: null, field: null };
}

/** A read that failed (list, vocabulary, resolver), as one sentence. */
export function readError(error: unknown, fallback: string): string {
  if (error instanceof CatalogApiError) {
    if (error.status === 403) return "No tienes permiso para ver el vocabulario del catálogo.";
    if (error.status === 429) return "Demasiadas consultas seguidas. Espera un momento y vuelve a intentarlo.";
    return error.message || fallback;
  }
  if (error instanceof TypeError) return NETWORK_MESSAGE;
  return fallback;
}

/** How the resolver found the model. */
export const MODEL_READ_LABELS: Record<ResolveModelStatus, string> = {
  exact: "Por su nombre",
  alias: "Por un alias de nombre completo",
  model_word: "Por palabras de modelo",
  name_with_options: "Quitando opciones escritas en el nombre",
  ambiguous: "Ambiguo: más de un modelo",
  unknown: "No se encontró",
};

/** How an option (or a model) was read. A way the panel does not know is shown as the server wrote it. */
export const VIA_LABELS: Record<string, string> = {
  label: "por su etiqueta",
  capacity: "por capacidad (sin espacio)",
  number: "por número en la columna de capacidad",
  alias: "por un alias de la empresa",
  spacing: "por su nombre (con otro espaciado)",
  exact: "por su nombre",
  model_word: "por palabras de modelo",
};

export const viaLabel = (via: string | null | undefined) => (via ? VIA_LABELS[via] ?? via : "");

export const DOUBT_REASON_LABELS: Record<DoubtReason, string> = {
  unknown: "Desconocido",
  ambiguous: "Ambiguo",
  outside: "Fuera de la paleta",
  name_with_options: "Opciones dentro del nombre",
};

export const DOUBT_REASON_HELP: Record<DoubtReason, string> = {
  unknown: "No es ninguna opción, modelo ni alias de la empresa.",
  ambiguous: "Se puede leer de más de una forma; ninguna se elige sola.",
  outside: "Es una opción real de la empresa, pero la paleta de este modelo no la admite.",
  name_with_options: "El nombre del modelo traía opciones escritas al final; se separaron sólo para mostrarlas.",
};

/** What a PERSON may do with a doubt. The panel only names them: nothing is executed from here. */
export const DOUBT_ACTION_LABELS: Record<DoubtAction, string> = {
  add_to_palette: "Agregarla a la paleta del modelo",
  alias_of: "Registrarla como alias de una opción o modelo existente",
  keep_pending: "Dejarla pendiente",
};

/** What a role is called in a doubt («model» is the model's name). */
export function doubtRoleLabel(role: AttributeRole | "model"): string {
  return role === "model" ? "Modelo" : ROLE_LABELS[role] ?? role;
}

/** A missing role: «capacity» → «Capacidad»; «other:Grado» → «Grado». */
export function missingLabel(name: string): string {
  if (name.startsWith("other:")) return name.slice("other:".length) || ROLE_LABELS.other;
  return ROLE_LABELS[name as AttributeRole] ?? name;
}
