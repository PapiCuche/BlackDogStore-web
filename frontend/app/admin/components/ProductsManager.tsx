"use client";

/**
 * La lista de productos del panel: filtros, tabla, páginas y —con permiso para
 * gestionar el catálogo— la selección para publicar u ocultar en bloque
 * (INVENTORY-UNIFIED-01).
 *
 * LA SELECCIÓN ES DE LO QUE SE VE. Son las filas marcadas de esta página, o
 * «todo lo que coincide» con los filtros puestos. Cambiar un filtro o de página
 * la quita: nadie confirma una acción sobre filas que ya no tiene delante.
 * «Todo lo que coincide» no viaja como una lista de ids: viaja el filtro y el
 * total que el servidor dio, y el servidor lo vuelve a contar antes de tocar nada.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  AdminCategory,
  AdminProduct,
  BulkVisibilityAction,
  BulkVisibilityResult,
  PaginatedResponse,
  ProductListFilter,
  SelectionChangedError,
  bulkSetProductVisibility,
  fetchAdminCategories,
  fetchAdminProducts,
} from "../../lib/admin";
import {
  BulkVisibilityBar,
  BulkVisibilityConfirm,
  BulkVisibilityResultPanel,
} from "./ProductBulkVisibility";
import { ProductsTable } from "./ProductsTable";
import {
  FilterBar,
  PageHeader,
  internalButtonClass,
  internalInputClass,
  internalPrimaryButtonClass,
} from "./internal-ui";

type Filters = {
  search: string;
  category: string;
  is_active: string;
  is_published_online: string;
  is_serialized: string;
  stock: string;
};

const NO_FILTERS: Filters = {
  search: "", category: "", is_active: "", is_published_online: "", is_serialized: "", stock: "",
};

/** Los filtros de la pantalla como los pide el servidor. Una definición: la lista y la acción en bloque. */
function listFilter(filters: Filters): ProductListFilter {
  return {
    search: filters.search || undefined,
    category: filters.category || undefined,
    is_active: (filters.is_active || undefined) as ProductListFilter["is_active"],
    is_published_online: (filters.is_published_online || undefined) as ProductListFilter["is_published_online"],
    is_serialized: (filters.is_serialized || undefined) as ProductListFilter["is_serialized"],
    stock: (filters.stock || undefined) as ProductListFilter["stock"],
  };
}

type Selection = { ids: ReadonlySet<number>; allMatching: boolean };
const NOTHING: Selection = { ids: new Set(), allMatching: false };

const VISIBILITY_CHIPS = [
  { value: "", label: "Todos" },
  { value: "true", label: "Publicados" },
  { value: "false", label: "Internos" },
];

const CHIP =
  "rounded-full border px-3.5 py-1.5 text-sm font-semibold transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground/40";

type Props = {
  canManage: boolean;
  /** INV-UX-A1: `inventory.adjust` — «Recibir mercadería» on each row. */
  canReceive?: boolean;
  /**
   * La empresa para la que se abrió la pantalla (la que el servidor resolvió al
   * decidir el acceso). La lista y la acción en bloque la mandan explícita: lo
   * que se confirma se aplica donde se contó, aunque otra pestaña haya cambiado
   * de empresa entretanto. `null`: operador sin empresa resuelta.
   */
  companyId?: number | null;
};

export function ProductsManager({ canManage, canReceive = false, companyId = null }: Props) {
  const [data, setData] = useState<PaginatedResponse<AdminProduct> | null>(null);
  const [categories, setCategories] = useState<AdminCategory[]>([]);
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [searchInput, setSearchInput] = useState("");
  const [categoryError, setCategoryError] = useState(false);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selection, setSelection] = useState<Selection>(NOTHING);
  const [pending, setPending] = useState<BulkVisibilityAction | null>(null);
  const [applying, setApplying] = useState(false);
  const [result, setResult] = useState<BulkVisibilityResult | null>(null);
  const [bulkError, setBulkError] = useState<string | null>(null);

  // Cuál fue la ÚLTIMA lista pedida. Una respuesta que llega tarde —la de un
  // filtro que ya no está puesto— no pisa a la que la pantalla espera.
  const latest = useRef(0);

  const load = useCallback(async () => {
    const mine = ++latest.current;
    setLoading(true);
    setError(null);
    try {
      const found = await fetchAdminProducts({ ...listFilter(filters), page, page_size: 20, company: companyId ?? undefined });
      if (mine !== latest.current) return;
      // Una acción puede vaciar la última página (ocultar, con el filtro
      // «Publicados»): se vuelve a la última que todavía tiene algo.
      const lastPage = Math.max(1, Math.ceil(found.count / found.page_size));
      if (found.results.length === 0 && page > lastPage) {
        setPage(lastPage);
        return;
      }
      setData(found);
    } catch (err) {
      if (mine === latest.current) setError(err instanceof Error ? err.message : "Error al cargar productos.");
    } finally {
      if (mine === latest.current) setLoading(false);
    }
  }, [filters, page, companyId]);

  /**
   * Quitar la selección es también cerrar la confirmación que estuviera
   * abierta: no se confirma «0 productos», ni algo que ya no se ve.
   */
  const clearSelection = useCallback(() => {
    setSelection(NOTHING);
    setPending(null);
  }, []);

  useEffect(() => {
    if (searchInput === filters.search) return;
    const timer = window.setTimeout(() => {
      setFilters((current) => ({ ...current, search: searchInput }));
      setPage(1);
      clearSelection();
    }, 300);
    return () => window.clearTimeout(timer);
  }, [searchInput, filters.search, clearSelection]);

  useEffect(() => {
    fetchAdminCategories()
      .then((list) => {
        setCategories(list);
        setCategoryError(false);
      })
      .catch(() => {
        setCategories([]);
        setCategoryError(true);
      });
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function setFilter(key: keyof Filters, value: string) {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setPage(1);
    clearSelection();
  }

  function goToPage(next: number) {
    setPage(next);
    clearSelection();
  }

  const rows = data?.results ?? [];
  const total = data?.count ?? 0;
  const totalPages = data ? Math.max(1, Math.ceil(data.count / data.page_size)) : 1;

  const selectedCount = selection.allMatching ? total : selection.ids.size;
  const wholePage = rows.length > 0 && rows.every((row) => selection.allMatching || selection.ids.has(row.id));

  const tableSelection = canManage
    ? {
        isSelected: (id: number) => selection.allMatching || selection.ids.has(id),
        onToggle: (id: number, checked: boolean) => {
          setSelection((current) => {
            // Desmarcar una fila de «todo lo que coincide» deja la página menos esa fila.
            const ids = new Set(current.allMatching ? rows.map((row) => row.id) : current.ids);
            if (checked) ids.add(id); else ids.delete(id);
            return { ids, allMatching: false };
          });
        },
        onTogglePage: (checked: boolean) => {
          setSelection(checked ? { ids: new Set(rows.map((row) => row.id)), allMatching: false } : NOTHING);
        },
      }
    : undefined;

  function ask(action: BulkVisibilityAction) {
    setBulkError(null);
    setResult(null);
    setPending(action);
  }

  const cancel = useCallback(() => setPending(null), []);

  async function apply() {
    if (!pending || applying) return;
    setApplying(true);
    try {
      const done = await bulkSetProductVisibility(
        pending,
        selection.allMatching
          ? { filter: listFilter(filters), expectedCount: total }
          : { ids: [...selection.ids].sort((a, b) => a - b) },
        companyId,
      );
      setResult(done);
      setSelection(NOTHING);
      await load();
    } catch (err) {
      if (err instanceof SelectionChangedError) {
        // Lo contado ya no es lo que hay: se dice, se vuelve a leer y se pide marcar otra vez.
        setBulkError(`${err.message} Revisa la lista y vuelve a seleccionar.`);
        setSelection(NOTHING);
        await load();
      } else {
        setBulkError(err instanceof Error ? err.message : "No se pudo cambiar la visibilidad de los productos.");
      }
    } finally {
      setApplying(false);
      setPending(null);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Catálogo"
        title="Productos"
        description={data ? `${data.count} productos en total. Filtra por categoría, publicación y disponibilidad.` : "Gestiona el catálogo de esta empresa."}
        actions={
          canManage ? (
            <>
              {/* INVENTORY-UNIFIED-01: muchas imágenes a la vez, sin recrear productos.
                  Esa pantalla decide por capacidades DE UNA EMPRESA: sin empresa
                  resuelta respondería «sin permisos», así que no se ofrece. */}
              {companyId !== null ? (
                <Link href="/admin/inventory/bulk#imagenes" className={internalButtonClass}>
                  Añadir imágenes en lote
                </Link>
              ) : null}
              <Link href="/admin/products/new" className={internalPrimaryButtonClass}>
                Nuevo producto
              </Link>
            </>
          ) : null
        }
      />

      <FilterBar>
        <div role="group" aria-label="Visibilidad en e-commerce" className="mb-3 flex flex-wrap gap-2">
          {VISIBILITY_CHIPS.map((chip) => {
            const active = filters.is_published_online === chip.value;
            return (
              <button
                key={chip.label}
                type="button"
                aria-pressed={active}
                onClick={() => setFilter("is_published_online", chip.value)}
                className={`${CHIP} ${
                  active
                    ? "border-foreground bg-foreground text-background"
                    : "border-bd-border bg-background text-foreground hover:border-foreground/25"
                }`}
              >
                {chip.label}
              </button>
            );
          })}
        </div>
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_repeat(4,auto)]">
          <label>
            <span className="sr-only">Buscar productos</span>
            <input
              type="search"
              placeholder="Buscar por nombre o slug…"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              className={internalInputClass}
            />
          </label>
          <label>
            <span className="sr-only">Categoría</span>
            <select
              value={filters.category}
              onChange={(e) => setFilter("category", e.target.value)}
              className={internalInputClass}
            >
              <option value="">Todas las categorías</option>
              {categories.map((category) => (
                <option key={category.id} value={category.id}>{category.name}</option>
              ))}
            </select>
          </label>
          <label>
            <span className="sr-only">Tipo de producto</span>
            <select
              value={filters.is_serialized}
              onChange={(e) => setFilter("is_serialized", e.target.value)}
              className={internalInputClass}
            >
              <option value="">Todos los tipos</option>
              <option value="true">Por serie</option>
              <option value="false">Por cantidad</option>
            </select>
          </label>
          <label>
            <span className="sr-only">Estado del producto</span>
            <select
              value={filters.is_active}
              onChange={(e) => setFilter("is_active", e.target.value)}
              className={internalInputClass}
            >
              <option value="">Todos los estados</option>
              <option value="true">Activos</option>
              <option value="false">Inactivos</option>
            </select>
          </label>
          <label>
            <span className="sr-only">Estado de stock</span>
            <select
              value={filters.stock}
              onChange={(e) => setFilter("stock", e.target.value)}
              className={internalInputClass}
            >
              <option value="">Cualquier stock</option>
              <option value="in_stock">En stock</option>
              <option value="out_of_stock">Sin stock</option>
              <option value="low_stock">Stock bajo (≤5)</option>
            </select>
          </label>
        </div>
        {categoryError ? (
          <p className="mt-3 text-xs text-warning">
            No se pudieron cargar las categorías. Los demás filtros siguen disponibles.
          </p>
        ) : null}
      </FilterBar>

      <section className="rounded-xl border border-bd-border bg-surface p-5 sm:p-6">
        {error ? <p className="mb-4 text-sm text-danger" role="alert">{error}</p> : null}
        {bulkError ? <p className="mb-4 text-sm text-danger" role="alert">{bulkError}</p> : null}
        {result ? <BulkVisibilityResultPanel result={result} onClose={() => setResult(null)} /> : null}

        {canManage && selectedCount > 0 ? (
          <BulkVisibilityBar count={selectedCount} onAction={ask} onClear={clearSelection} />
        ) : null}
        {canManage && selection.allMatching ? (
          <p className="mb-4 text-sm text-muted">Los {total} productos que coinciden están seleccionados.</p>
        ) : null}
        {canManage && !selection.allMatching && wholePage && total > rows.length ? (
          <p className="mb-4 text-sm text-muted">
            Están seleccionados los {rows.length} productos de esta página.{" "}
            <button
              type="button"
              onClick={() => setSelection({ ids: new Set(), allMatching: true })}
              className="font-semibold text-foreground underline underline-offset-2"
            >
              Seleccionar los {total} productos que coinciden
            </button>
          </p>
        ) : null}

        {loading ? (
          <p className="py-8 text-center text-sm text-muted">Cargando productos…</p>
        ) : (
          <ProductsTable
            products={rows}
            canManage={canManage}
            canReceive={canReceive}
            // Activar o desactivar una fila puede sacarla de la lista filtrada:
            // la selección se quita, para no dejar marcado algo que no se ve.
            onChanged={() => { clearSelection(); void load(); }}
            selection={tableSelection}
          />
        )}
      </section>

      {totalPages > 1 ? (
        <div className="flex flex-col gap-3 border-t border-bd-border pt-4 text-sm text-muted sm:flex-row sm:items-center sm:justify-between">
          <button
            type="button"
            onClick={() => goToPage(Math.max(1, page - 1))}
            disabled={page <= 1}
            className={internalButtonClass}
          >
            Anterior
          </button>
          <span>Página {page} de {totalPages}</span>
          <button
            type="button"
            onClick={() => goToPage(Math.min(totalPages, page + 1))}
            disabled={page >= totalPages}
            className={internalButtonClass}
          >
            Siguiente
          </button>
        </div>
      ) : null}

      {pending ? (
        <BulkVisibilityConfirm
          action={pending}
          count={selectedCount}
          busy={applying}
          onConfirm={apply}
          onCancel={cancel}
        />
      ) : null}
    </div>
  );
}
