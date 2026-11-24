/**
 * CATALOG-ADMIN-UX-B — the search and the filters of Productos › Modelos, kept in the address so a reload, a
 * shared link or «atrás» shows the same list:
 *
 *   ?buscar=<texto>&categoria=<id|sin>&estado=activos|inactivos&filtro=incompletas,sin-variante,sin-paleta&pagina=2
 *
 * Only these keys are read and written; every other parameter of the address (`seccion`, `editar`…) is kept as it
 * was. The server does the searching (`catalogModelsPath`): nothing here filters rows.
 */
import type { CatalogModelFlag, CatalogModelQuery } from "./internal-api";

export const MODEL_LIST_PAGE_SIZE = 25;

export type ModelListState = {
  search: string;
  /** A category id, "none" for the models without one, or "" for every category. */
  category: string;
  active: "" | "true" | "false";
  flags: CatalogModelFlag[];
  page: number;
};

export const EMPTY_MODEL_LIST_STATE: ModelListState = { search: "", category: "", active: "", flags: [], page: 1 };

/** The order the chips are drawn in, and the word each one takes in the address. */
export const MODEL_FLAGS: CatalogModelFlag[] = ["incomplete", "missing", "no_palette"];
const FLAG_WORDS: Record<CatalogModelFlag, string> = {
  incomplete: "incompletas", missing: "sin-variante", no_palette: "sin-paleta",
};
const KEYS = ["buscar", "categoria", "estado", "filtro", "pagina"];

const params = (query: string) => new URLSearchParams(query.startsWith("?") ? query.slice(1) : query);

export function readModelListState(query: string): ModelListState {
  const read = params(query);
  const category = (read.get("categoria") ?? "").trim();
  const state = read.get("estado");
  const words = (read.get("filtro") ?? "").split(",").map((word) => word.trim());
  const page = Number.parseInt(read.get("pagina") ?? "", 10);
  return {
    search: (read.get("buscar") ?? "").slice(0, 100),
    category: category === "sin" ? "none" : /^\d+$/.test(category) ? category : "",
    active: state === "activos" ? "true" : state === "inactivos" ? "false" : "",
    flags: MODEL_FLAGS.filter((flag) => words.includes(FLAG_WORDS[flag])),
    page: Number.isFinite(page) && page > 0 ? page : 1,
  };
}

/** The address query for `state`, keeping every parameter that is not the list's own. Leading «?» when not empty. */
export function writeModelListState(query: string, state: ModelListState): string {
  const next = params(query);
  for (const key of KEYS) next.delete(key);
  if (state.search.trim()) next.set("buscar", state.search.trim());
  if (state.category) next.set("categoria", state.category === "none" ? "sin" : state.category);
  if (state.active) next.set("estado", state.active === "true" ? "activos" : "inactivos");
  if (state.flags.length) next.set("filtro", MODEL_FLAGS.filter((f) => state.flags.includes(f)).map((f) => FLAG_WORDS[f]).join(","));
  if (state.page > 1) next.set("pagina", String(state.page));
  const text = next.toString();
  return text ? `?${text}` : "";
}

export function modelListQuery(state: ModelListState): CatalogModelQuery {
  return {
    search: state.search, category: state.category || undefined, active: state.active || undefined,
    flags: state.flags, page: state.page, pageSize: MODEL_LIST_PAGE_SIZE,
  };
}

export const isFiltered = (state: ModelListState) =>
  Boolean(state.search.trim() || state.category || state.active || state.flags.length);
