"use client";

/**
 * INVENTORY-UNIFIED-01 — el panel «Exportar».
 *
 * Cinco descargas del inventario, con sus filtros y con una elección que la
 * pantalla explica antes de bajar nada:
 *
 *   · «Para volver a cargar»: el archivo que las cargas masivas leen tal cual.
 *     Subido a la MISMA tienda no cambia nada.
 *   · «Reporte»: lo mismo con columnas de sólo lectura. No se vuelve a cargar.
 *
 * `InventoryExportPanel` sólo pinta y pide; `InventoryExports` le trae las
 * sucursales y las categorías de la empresa. Los dos se exportan: hoy el panel
 * vive en «Carga de stock», y una pantalla propia lo montará igual.
 *
 * Ofrece sólo lo que la persona puede descargar, pero la autoridad es del
 * servidor: qué empresa, qué sucursales y si la columna «Costo» lleva datos.
 */
import { useEffect, useState } from "react";
import { fetchAdminCategories } from "../../lib/admin";
import {
  ExportFilters,
  ExportKind,
  ExportMode,
  downloadInventoryExport,
  fetchExportBranches,
} from "../../lib/inventory-exports";
import { internalButtonClass, internalInputClass } from "./internal-ui";

type Option = { id: number; name: string };

type PanelProps = {
  companyId: number | null;
  /** `products.view`: el catálogo. */
  canCatalogue: boolean;
  /** `inventory.view`: stock y equipos, de sus sucursales. */
  canInventory: boolean;
  /** `costs.view` (DC1): el servidor sólo escribe los costos para quien lo tiene. */
  canSeeCost?: boolean;
  branches: Option[];
  categories: Option[];
  /** Aviso de algo que no se pudo leer (sucursales, categorías). */
  notice?: string | null;
};

type Download = {
  id: string;
  title: string;
  kind: ExportKind;
  description: string;
  needs: "catalogue" | "inventory" | "both";
  /** Filtros que esta descarga fija, diga lo que diga el formulario. */
  fixed?: Partial<ExportFilters>;
};

const DOWNLOADS: Download[] = [
  {
    id: "catalogue", title: "Catálogo general", kind: "products", needs: "catalogue",
    description: "Una fila por producto: códigos, nombre, descripción, precio, categoría y destino. Lleva activos e inactivos, salvo que filtres por estado. Para volver a cargar trae además la columna «Estado»: cargarlo no activa ni desactiva un producto que ya existe. El «Stock total» del reporte suma toda la empresa.",
  },
  {
    id: "accessories", title: "Productos por cantidad", kind: "products", needs: "catalogue",
    fixed: { type: "quantity" },
    description: "El catálogo sin los equipos con número de serie: accesorios, repuestos y todo lo que se cuenta por cantidad.",
  },
  {
    id: "stock", title: "Stock por sucursal", kind: "stock", needs: "inventory",
    description: "Las cantidades de cada producto, con una columna por sucursal, de tus sucursales activas. Los equipos con serie sólo aparecen en el reporte. Si el stock se movió desde la descarga, volver a cargarlo lo deja como dice el archivo.",
  },
  {
    id: "units", title: "Equipos con serie", kind: "units", needs: "inventory",
    description: "Una fila por unidad física: serie, IMEI, condición y sucursal, de tus sucursales activas.",
  },
  {
    id: "full", title: "Inventario completo (ZIP)", kind: "full", needs: "both",
    description: "Productos.xlsx, Stock.xlsx y Equipos.xlsx con una hoja cada uno, y una nota con el orden para cargarlos en una tienda vacía. Productos activos e inactivos; siempre para volver a cargar.",
  },
  {
    // INV-EXP-02 — para consultar: lo propio y lo de proveedores, cada uno en su hoja.
    id: "integral", title: "Reporte integral", kind: "integral", needs: "both",
    description: "Un solo Excel para consultar: stock propio por sucursal, equipos con serie y la disponibilidad de proveedores en su propia hoja. Las cantidades de proveedores nunca se suman al stock; si el proveedor no dio cantidad, dice «Desconocida». Siempre es un reporte.",
  },
];

const NO_FILTERS = { category: "", type: "", stock: "", condition: "", visibility: "", is_active: "", line: "" };

const LABEL = "mb-1 block text-xs font-semibold text-muted";

export function InventoryExportPanel({
  companyId, canCatalogue, canInventory, canSeeCost, branches, categories, notice,
}: PanelProps) {
  const [mode, setMode] = useState<ExportMode>("reimport");
  const [filters, setFilters] = useState(NO_FILTERS);
  const [chosen, setChosen] = useState<number[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const offered = DOWNLOADS.filter((item) =>
    item.needs === "catalogue" ? canCatalogue : item.needs === "inventory" ? canInventory : canCatalogue && canInventory,
  );
  if (offered.length === 0) return null;

  // Con algo puesto arriba, el ZIP ya no es «todo»: se dice en su tarjeta.
  const narrowed = chosen.length > 0 || Object.values(filters).some(Boolean);

  const set = (key: keyof typeof NO_FILTERS) => (event: React.ChangeEvent<HTMLSelectElement>) =>
    setFilters((current) => ({ ...current, [key]: event.target.value }));

  async function run(item: Download) {
    if (busy) return;
    setBusy(item.id);
    setError(null);
    try {
      await downloadInventoryExport(
        item.kind,
        item.kind === "full" ? "reimport" : item.kind === "integral" ? "report" : mode,
        {
          ...(filters as ExportFilters),
          ...item.fixed,
          // Sólo las suyas, y en un orden estable. Ninguna marcada = todas las suyas.
          branches: branches.filter((branch) => chosen.includes(branch.id)).map((branch) => branch.id),
        },
        companyId,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No se pudo generar la descarga.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-5 rounded-xl border border-bd-border bg-surface p-4 sm:p-5">
      <fieldset role="radiogroup" aria-label="Tipo de archivo" className="grid gap-3 sm:grid-cols-2">
        <label className="flex cursor-pointer gap-3 rounded-lg border border-bd-border bg-background p-3 text-sm">
          <input
            type="radio" name="export-mode" className="mt-1" checked={mode === "reimport"}
            onChange={() => setMode("reimport")}
          />
          <span>
            <span className="block font-semibold text-foreground">Para volver a cargar</span>
            <span className="mt-0.5 block text-xs text-muted">
              Trae las mismas columnas que piden las cargas masivas; subido a esta misma tienda no cambia nada.
            </span>
          </span>
        </label>
        <label className="flex cursor-pointer gap-3 rounded-lg border border-bd-border bg-background p-3 text-sm">
          <input
            type="radio" name="export-mode" className="mt-1" checked={mode === "report"}
            onChange={() => setMode("report")}
          />
          <span>
            <span className="block font-semibold text-foreground">Reporte</span>
            <span className="mt-0.5 block text-xs text-muted">
              Añade columnas de sólo lectura (estado, totales, fechas); sirve para revisar y no se puede volver a cargar.
            </span>
          </span>
        </label>
      </fieldset>

      {canInventory ? (
        <fieldset role="group" aria-label="Sucursales">
          <legend className={LABEL}>Sucursales (sin marcar ninguna: todas las tuyas)</legend>
          {branches.length === 0 ? (
            <p className="text-xs text-muted">No tienes sucursales asignadas.</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {branches.map((branch) => (
                <label key={branch.id} className="flex items-center gap-2 rounded-lg border border-bd-border px-3 py-1.5 text-xs text-foreground">
                  <input
                    type="checkbox"
                    checked={chosen.includes(branch.id)}
                    onChange={(event) =>
                      setChosen((current) =>
                        event.target.checked ? [...current, branch.id] : current.filter((id) => id !== branch.id),
                      )
                    }
                  />
                  {branch.name}
                </label>
              ))}
            </div>
          )}
        </fieldset>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <label>
          <span className={LABEL}>Categoría</span>
          <select value={filters.category} onChange={set("category")} className={internalInputClass}>
            <option value="">Todas las categorías</option>
            {categories.map((category) => (
              <option key={category.id} value={category.id}>{category.name}</option>
            ))}
          </select>
        </label>
        <label>
          <span className={LABEL}>Tipo de producto</span>
          <select value={filters.type} onChange={set("type")} className={internalInputClass}>
            <option value="">Todos los tipos</option>
            <option value="serialized">Por serie</option>
            <option value="quantity">Por cantidad</option>
          </select>
        </label>
        {/* INV-EXP-01: un equipo pertenece a un modelo o va por serie; lo demás nuevo es accesorio. */}
        <label>
          <span className={LABEL}>Línea</span>
          <select value={filters.line} onChange={set("line")} className={internalInputClass}>
            <option value="">Todo el inventario</option>
            <option value="new">Solo nuevos</option>
            <option value="used">Solo seminuevos</option>
            <option value="new,used">Nuevos y seminuevos</option>
            <option value="accessory">Solo accesorios</option>
          </select>
        </label>
        <label>
          <span className={LABEL}>Existencia</span>
          <select value={filters.stock} onChange={set("stock")} className={internalInputClass}>
            <option value="">Con y sin existencia</option>
            <option value="in_stock">Con existencia</option>
            <option value="out_of_stock">Sin existencia</option>
          </select>
        </label>
        {canInventory ? (
          <label>
            <span className={LABEL}>Condición del equipo</span>
            <select value={filters.condition} onChange={set("condition")} className={internalInputClass}>
              <option value="">Cualquier condición</option>
              <option value="new">Nuevo</option>
              <option value="open_box">Caja abierta</option>
              <option value="refurbished">Reacondicionado</option>
              <option value="used">Usado</option>
            </select>
          </label>
        ) : null}
        <label>
          <span className={LABEL}>Visibilidad en e-commerce</span>
          <select value={filters.visibility} onChange={set("visibility")} className={internalInputClass}>
            <option value="">Publicados e internos</option>
            <option value="published">Publicados</option>
            <option value="internal">Solo stock interno</option>
          </select>
        </label>
        <label>
          <span className={LABEL}>Estado del producto</span>
          <select value={filters.is_active} onChange={set("is_active")} className={internalInputClass}>
            <option value="">Activos e inactivos</option>
            <option value="true">Activos</option>
            <option value="false">Inactivos</option>
          </select>
        </label>
      </div>

      {notice ? <p className="text-xs text-warning">{notice}</p> : null}
      {error ? <p className="text-sm text-danger" role="alert">{error}</p> : null}

      <ul className="grid gap-3 md:grid-cols-2">
        {offered.map((item) => (
          <li key={item.id} className="flex flex-col justify-between gap-3 rounded-lg border border-bd-border bg-background p-4">
            <div>
              <h3 className="text-sm font-semibold text-foreground">{item.title}</h3>
              <p className="mt-1 text-xs text-muted">{item.description}</p>
              {item.kind === "full" && narrowed ? (
                <p role="alert" className="mt-1 text-xs font-semibold text-warning">
                  Con los filtros o las sucursales de arriba NO es el inventario completo: lleva sólo lo que
                  coincide. Para llevarlo todo, quítalos.
                </p>
              ) : null}
              {item.kind === "units" ? (
                <p className="mt-1 text-xs text-muted">
                  {canSeeCost
                    ? "Incluye el costo de cada equipo."
                    : "La columna «Costo» va vacía: sólo se entrega a quien puede ver costos."}
                </p>
              ) : null}
            </div>
            <button
              type="button"
              onClick={() => void run(item)}
              disabled={busy !== null}
              className={`${internalButtonClass} self-start`}
              aria-label={busy === item.id ? undefined : `Descargar ${item.title}`}
            >
              {busy === item.id ? "Generando…" : "Descargar"}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** El panel con las sucursales y las categorías de la empresa ya leídas. */
export function InventoryExports({
  companyId, canCatalogue, canInventory, canSeeCost,
}: Pick<PanelProps, "companyId" | "canCatalogue" | "canInventory" | "canSeeCost">) {
  const [branches, setBranches] = useState<Option[]>([]);
  const [categories, setCategories] = useState<Option[]>([]);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (canInventory) {
      fetchExportBranches(companyId)
        .then((list) => { if (!cancelled) setBranches(list); })
        .catch(() => { if (!cancelled) setNotice("No se pudieron cargar las sucursales. Se descargan todas las tuyas."); });
    }
    if (canCatalogue || canInventory) {
      // Sin categorías sólo falta ese filtro: las descargas siguen disponibles.
      fetchAdminCategories(companyId)
        .then((list) => { if (!cancelled) setCategories(list.map((category) => ({ id: category.id, name: category.name }))); })
        .catch(() => {});
    }
    return () => { cancelled = true; };
  }, [companyId, canCatalogue, canInventory]);

  return (
    <InventoryExportPanel
      companyId={companyId}
      canCatalogue={canCatalogue}
      canInventory={canInventory}
      canSeeCost={canSeeCost}
      branches={branches}
      categories={categories}
      notice={notice}
    />
  );
}
